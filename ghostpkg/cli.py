"""Command line interface."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
from pathlib import Path

from . import __version__
from .assess import NEW_DAYS, Finding, Verdict, assess
from .cache import Cache
from .rules import GP_NOT_INSPECTED, GP_UNCHECKED, Reason
from .inspection import InspectionError, inspect_package
from .manifests import Requirement, UnsupportedManifest, load_manifest, parse_requirements
from .policy import PolicyError, apply as apply_policy, load as load_policy
from . import registries
from .registries import RegistryError, fetch

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_ERROR = 2
#: Nothing was found to check. Distinct from "checked, all clean", because a
#: manifest we failed to understand used to exit 0 and read as a pass in CI.
EXIT_NOTHING_SCANNED = 3

MAX_WORKERS = 8


def _use_colour(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(stream, "isatty") and stream.isatty()


class Palette:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def red(self, text: str) -> str:
        return self._wrap("31;1", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33;1", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)


MARKS = {
    Verdict.BLOCK: ("BLOCKED", "red"),
    Verdict.ERROR: ("ERROR", "red"),
    Verdict.WARN: ("WARNING", "yellow"),
    Verdict.OK: ("ok", "green"),
}


def render(findings: list[Finding], palette: Palette, quiet: bool) -> None:
    for finding in findings:
        label, colour = MARKS[finding.verdict]
        paint = getattr(palette, colour)
        if finding.verdict is Verdict.OK:
            if quiet:
                continue
            detail = ""
            if finding.facts and finding.facts.age_days is not None:
                years = finding.facts.age_days / 365.0
                detail = palette.dim(
                    f"  ({finding.facts.release_count} releases, {years:.1f}y old)"
                )
            print(f"  {paint(label):<8} {finding.name}{detail}")
            continue

        print(f"  {paint(label):<8} {finding.name}")
        for reason in finding.reasons:
            print(f"           {palette.dim('- ' + reason)}")


def summarise(findings: list[Finding], palette: Palette) -> None:
    blocked = [f for f in findings if f.verdict is Verdict.BLOCK]
    warned = [f for f in findings if f.verdict is Verdict.WARN]
    errored = [f for f in findings if f.verdict is Verdict.ERROR]

    print()
    if blocked:
        names = ", ".join(f.name for f in blocked)
        print(palette.red(f"  {len(blocked)} blocked: {names}"))
    if errored:
        names = ", ".join(f.name for f in errored)
        print(palette.red(f"  {len(errored)} could not be checked: {names}"))
    if warned:
        print(palette.yellow(f"  {len(warned)} to review by hand"))
    if not blocked and not warned and not errored:
        print(palette.green(f"  all {len(findings)} packages look fine"))


def evaluate(
    requirements: "list[Requirement] | list[str]",
    ecosystem: str,
    strict: bool,
    cache: Cache | None = None,
    deep: bool = False,
    workers: int = MAX_WORKERS,
) -> list[Finding]:
    items = [
        r if isinstance(r, Requirement) else Requirement(name=r) for r in requirements
    ]

    def one(requirement: Requirement) -> Finding:
        name = requirement.name
        # A failure on one name must not discard the whole scan. It used to:
        # pool.map re-raised the first exception while iterating, throwing away
        # every confirmed BLOCK alongside it and skipping the cache write, so
        # the inevitable retry re-issued every lookup and made a rate-limit
        # response self-amplifying.
        try:
            facts = cache.get(ecosystem, name) if cache else None
            if facts is None:
                facts = fetch(name, ecosystem)
                if cache:
                    cache.put(facts)
        except RegistryError as exc:
            return Finding(
                name=name,
                ecosystem=ecosystem,
                verdict=Verdict.ERROR,
                reasons=[Reason(GP_UNCHECKED, f"could not check: {exc}")],
            )

        signals = None
        not_inspected = None
        # Only young packages are worth the download: a registered slopsquat is
        # new by definition, and inspecting everything would make a scan slow
        # for no gain. A compromised established package is a different threat
        # and is out of scope -- SECURITY.md says so.
        wanted = (
            deep
            and facts.exists
            and facts.age_days is not None
            and facts.age_days < NEW_DAYS
        )
        if wanted and not facts.archive_url:
            not_inspected = "no source archive published"
        elif wanted:
            try:
                signals = inspect_package(facts.archive_url, ecosystem)
            except InspectionError as exc:
                not_inspected = str(exc)

        finding = assess(
            facts, strict=strict, signals=signals, specifier=requirement.specifier
        )
        # Saying nothing would let "could not inspect" read as "inspected and
        # clean" -- and padding an archive past the size limit would then be a
        # way to switch --deep off from the outside.
        if not_inspected and not finding.is_blocked:
            finding.reasons.append(
                Reason(GP_NOT_INSPECTED, f"install scripts not inspected: {not_inspected}")
            )
            if finding.verdict is Verdict.OK:
                finding.verdict = Verdict.WARN
        return finding

    # Look each distinct (name, pin) pair up once. A manifest can repeat a
    # name, and following -r includes makes that more likely.
    unique: dict[tuple[str, str | None], Requirement] = {}
    for item in items:
        unique.setdefault((item.name, item.specifier), item)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = dict(zip(unique, pool.map(one, unique.values())))
    if cache:
        cache.save()
    return [results[(item.name, item.specifier)] for item in items]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghostpkg",
        description="Catch package names that do not exist before you install them.",
    )
    parser.add_argument(
        "--version", action="version", version=f"ghostpkg {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="check one or more package names")
    check.add_argument(
        "names", nargs="+", help="names, optionally pinned: requests==2.31.0"
    )
    check.add_argument("-e", "--ecosystem", choices=("pypi", "npm"), default="pypi")

    scan = sub.add_parser("scan", help="check every dependency in a manifest")
    scan.add_argument(
        "paths",
        type=Path,
        nargs="+",
        help="one or more manifests or lockfiles; mixed ecosystems are fine",
    )

    for command in (check, scan):
        command.add_argument(
            "--strict",
            action="store_true",
            help="treat warnings as blocking (flags legitimate new packages too)",
        )
        command.add_argument("--json", action="store_true", help="machine-readable output")
        command.add_argument("-q", "--quiet", action="store_true", help="hide passing packages")
        command.add_argument(
            "--no-cache", action="store_true", help="ignore and do not write the cache"
        )
        command.add_argument(
            "--config",
            metavar="PATH",
            help="ignore file (never read from the project directory; see "
            "GHOSTPKG_CONFIG and the user config directory)",
        )
        command.add_argument(
            "--workers",
            type=int,
            default=MAX_WORKERS,
            metavar="N",
            help=f"parallel lookups (default {MAX_WORKERS})",
        )
        command.add_argument(
            "--timeout",
            type=int,
            default=None,
            metavar="SECONDS",
            help="per-request timeout",
        )
        command.add_argument(
            "--deep",
            action="store_true",
            help="download recently published packages and statically inspect "
            "their install scripts (never executes anything)",
        )

    sub.add_parser("clear-cache", help="delete the cached registry lookups")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "clear-cache":
        cache = Cache(enabled=True)
        removed = cache.clear()
        print(f"ghostpkg: {'removed ' + str(cache.path) if removed else 'nothing to remove'}")
        return EXIT_OK

    if args.timeout:
        registries.TIMEOUT = args.timeout

    # Group by ecosystem so several manifests are looked up together and a
    # dependency repeated across them costs one request, not one per file.
    by_ecosystem: "dict[str, list]" = {}

    if args.command == "scan":
        for path in args.paths:
            if not path.exists():
                print(f"ghostpkg: no such file: {path}", file=sys.stderr)
                return EXIT_ERROR
            if path.is_dir():
                print(
                    f"ghostpkg: {path} is a directory; pass a manifest file",
                    file=sys.stderr,
                )
                return EXIT_ERROR
            try:
                found, ecosystem = load_manifest(path)
            except UnsupportedManifest as exc:
                print(f"ghostpkg: {exc}", file=sys.stderr)
                return EXIT_ERROR
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError) as exc:
                print(f"ghostpkg: could not parse {path}: {exc}", file=sys.stderr)
                return EXIT_ERROR
            for requirement in found:
                by_ecosystem.setdefault(
                    requirement.ecosystem or ecosystem, []
                ).append(requirement)
        if not any(by_ecosystem.values()):
            where = ", ".join(str(p) for p in args.paths)
            print(f"ghostpkg: no dependencies found in {where}", file=sys.stderr)
            # Not the same as "checked, all clean": a manifest we failed to
            # understand used to exit 0 and read as a pass in CI.
            return EXIT_NOTHING_SCANNED
    else:
        # Accept a pin on the command line too: `ghostpkg check requests==2.31.0`.
        by_ecosystem[args.ecosystem] = parse_requirements("\n".join(args.names))

    try:
        policy, policy_path = load_policy(args.config)
    except PolicyError as exc:
        # Degrading quietly here would be indistinguishable from having no
        # protection at all, so it is an error.
        print(f"ghostpkg: {exc}", file=sys.stderr)
        return EXIT_ERROR

    cache = Cache(enabled=not args.no_cache)
    findings: list[Finding] = []
    for ecosystem, requirements in by_ecosystem.items():
        if requirements:
            findings.extend(
                evaluate(
                    requirements, ecosystem, args.strict, cache, args.deep, args.workers
                )
            )

    suppressed = 0
    for finding in findings:
        _, used = apply_policy(finding, policy)
        suppressed += bool(used)

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "name": f.name,
                        "ecosystem": f.ecosystem,
                        "verdict": f.verdict.value,
                        "reasons": [
                            {"rule": getattr(r, "rule", None), "text": str(r)}
                            for r in f.reasons
                        ],
                    }
                    for f in findings
                ],
                indent=2,
            )
        )
    else:
        palette = Palette(_use_colour(sys.stdout))
        render(findings, palette, args.quiet)
        summarise(findings, palette)
        if suppressed:
            print(
                palette.dim(
                    f"  {suppressed} suppressed by {policy_path}"
                )
            )

    if any(f.is_blocked for f in findings):
        return EXIT_BLOCKED
    # An unchecked name is not a pass.
    if any(f.is_error for f in findings):
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
