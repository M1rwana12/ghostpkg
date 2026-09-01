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
from .cache import Cache
from .manifests import UnsupportedManifest, load_manifest, parse_requirements
from .policy import PolicyError, apply as apply_policy, load as load_policy
from .report import Palette, as_json, render, summarise, use_colour
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
        print(as_json(findings))
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
