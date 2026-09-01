"""Command line interface."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
from pathlib import Path

from . import __version__
from .assess import Finding, Verdict, assess
from .manifests import UnsupportedManifest, load_manifest
from .registries import RegistryError, fetch

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_ERROR = 2

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

    print()
    if blocked:
        names = ", ".join(f.name for f in blocked)
        print(palette.red(f"  {len(blocked)} blocked: {names}"))
    if warned:
        print(palette.yellow(f"  {len(warned)} to review by hand"))
    if not blocked and not warned:
        print(palette.green(f"  all {len(findings)} packages look fine"))


def evaluate(names: list[str], ecosystem: str, strict: bool) -> list[Finding]:
    def one(name: str) -> Finding:
        return assess(fetch(name, ecosystem), strict=strict)

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        return list(pool.map(one, names))


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
    check.add_argument("names", nargs="+")
    check.add_argument("-e", "--ecosystem", choices=("pypi", "npm"), default="pypi")

    scan = sub.add_parser("scan", help="check every dependency in a manifest")
    scan.add_argument(
        "path", type=Path, help="requirements*.txt, pyproject.toml or package.json"
    )

    for command in (check, scan):
        command.add_argument(
            "--strict",
            action="store_true",
            help="treat warnings as blocking (flags legitimate new packages too)",
        )
        command.add_argument("--json", action="store_true", help="machine-readable output")
        command.add_argument("-q", "--quiet", action="store_true", help="hide passing packages")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "scan":
        if not args.path.exists():
            print(f"ghostpkg: no such file: {args.path}", file=sys.stderr)
            return EXIT_ERROR
        try:
            names, ecosystem = load_manifest(args.path)
        except UnsupportedManifest as exc:
            print(f"ghostpkg: {exc}", file=sys.stderr)
            return EXIT_ERROR
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            print(f"ghostpkg: could not parse {args.path}: {exc}", file=sys.stderr)
            return EXIT_ERROR
        if not names:
            print(f"ghostpkg: no dependencies found in {args.path}", file=sys.stderr)
            return EXIT_OK
    else:
        names, ecosystem = args.names, args.ecosystem

    try:
        findings = evaluate(names, ecosystem, args.strict)
    except RegistryError as exc:
        print(f"ghostpkg: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "name": f.name,
                        "ecosystem": f.ecosystem,
                        "verdict": f.verdict.value,
                        "reasons": f.reasons,
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

    return EXIT_BLOCKED if any(f.is_blocked for f in findings) else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
