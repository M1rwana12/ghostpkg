"""Command line interface: argument parsing and process exit codes.

The work itself lives in `scanner` (lookups and verdicts) and `report`
(presentation), so that neither needs a terminal to be useful.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, registries
from .assess import Finding
from . import inspection
from .cache import Cache
from .discover import discover
from .manifests import (
    UnsupportedManifest,
    declared_name,
    load_manifest,
    parse_npm_names,
    parse_requirements,
)
from .policy import PolicyError, apply as apply_policy, load as load_policy
from .report import Palette, as_github, as_json, render, summarise, use_colour
from .scanner import DEFAULT_WORKERS, evaluate

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_ERROR = 2
#: Nothing was found to check. Distinct from "checked, all clean", because a
#: manifest we failed to understand used to exit 0 and read as a pass in CI.
EXIT_NOTHING_SCANNED = 3

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
        nargs="*",
        default=[Path(".")],
        help="manifests, lockfiles, or a directory to search "
        "(default: the current one); mixed ecosystems are fine",
    )

    for command in (check, scan):
        command.add_argument(
            "--strict",
            action="store_true",
            help="treat warnings as blocking (flags legitimate new packages too)",
        )
        command.add_argument(
            "--format",
            choices=("text", "json", "github"),
            default="text",
            help="text for a terminal, json for a program, github for "
            "annotations on a pull request diff",
        )
        command.add_argument(
            "--json", action="store_true", help="the same as --format json"
        )
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
            default=DEFAULT_WORKERS,
            metavar="N",
            help=f"parallel lookups (default {DEFAULT_WORKERS})",
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

    if args.timeout is not None:
        # `if args.timeout:` made `--timeout 0` a silent no-op. Nought is not a
        # sensible budget either, so it is refused rather than ignored.
        if args.timeout <= 0:
            print("ghostpkg: --timeout must be a positive number of seconds", file=sys.stderr)
            return EXIT_ERROR
        registries.TIMEOUT = args.timeout
        # `--deep` downloads an archive through its own client, which had a
        # fixed budget of its own -- so the documented per-request timeout
        # applied to every request except the slowest kind.
        inspection.TIMEOUT = max(args.timeout, inspection.TIMEOUT)

    # Group by ecosystem so several manifests are looked up together and a
    # dependency repeated across them costs one request, not one per file.
    by_ecosystem: "dict[str, list]" = {}

    if args.command == "scan":
        paths: list[Path] = []
        for path in args.paths or [Path(".")]:
            if not path.exists():
                print(f"ghostpkg: no such file: {path}", file=sys.stderr)
                return EXIT_ERROR
            paths.extend(discover(path) if path.is_dir() else [path])

        if not paths:
            where = ", ".join(str(p) for p in args.paths)
            print(f"ghostpkg: no manifests found in {where}", file=sys.stderr)
            return EXIT_NOTHING_SCANNED

        # Names the checkout provides itself. A monorepo depends on its own
            # packages, and not always through `workspace:*` -- an exact pin is
            # just as common, and looking those up on the public registry
            # blocked every one of them.
        local: set[str] = set()
        for path in paths:
            own = declared_name(path)
            if own:
                local.add(own.lower())

        for path in paths:
            try:
                found, ecosystem = load_manifest(path)
            except UnsupportedManifest as exc:
                if path in args.paths:
                    print(f"ghostpkg: {exc}", file=sys.stderr)
                    return EXIT_ERROR
                continue
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError) as exc:
                # A named file that will not parse is an error. A discovered one
                # is not: refusing to scan a project because some unrelated file
                # in it is malformed would make the directory form unusable.
                print(f"ghostpkg: could not parse {path}: {exc}", file=sys.stderr)
                if path in args.paths:
                    return EXIT_ERROR
                continue
            for requirement in found:
                if requirement.name.lower() in local:
                    continue  # provided by this checkout
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
        # npm names go through their own reader, because a scoped name is not
        # expressible in PEP 508 and the requirements parser dropped every one.
        if args.ecosystem == "npm":
            by_ecosystem[args.ecosystem] = parse_npm_names(args.names)
        else:
            by_ecosystem[args.ecosystem] = parse_requirements("\n".join(args.names))
        if not by_ecosystem[args.ecosystem]:
            print(f"ghostpkg: no package names in {' '.join(args.names)}", file=sys.stderr)
            return EXIT_NOTHING_SCANNED

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

    output = "json" if args.json else args.format
    if output == "json":
        print(as_json(findings))
    elif output == "github":
        annotations = as_github(findings)
        if annotations:
            print(annotations)
        palette = Palette(False)
        summarise(findings, palette, suppressed, policy_path)
    else:
        palette = Palette(use_colour(sys.stdout))
        render(findings, palette, args.quiet)
        summarise(findings, palette, suppressed, policy_path)

    if any(f.is_blocked for f in findings):
        return EXIT_BLOCKED
    # An unchecked name is not a pass.
    if any(f.is_error for f in findings):
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
