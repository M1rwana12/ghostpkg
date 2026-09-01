"""Turning findings into something to read, or into JSON."""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import __version__
from .assess import Finding, Verdict

MARKS = {
    Verdict.BLOCK: ("BLOCKED", "red"),
    Verdict.ERROR: ("ERROR", "red"),
    Verdict.WARN: ("WARNING", "yellow"),
    Verdict.OK: ("ok", "green"),
}


def use_colour(stream) -> bool:
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


def render(findings: list[Finding], palette: Palette, quiet: bool = False) -> None:
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


def summarise(
    findings: list[Finding],
    palette: Palette,
    suppressed: int = 0,
    policy_path: "Path | None" = None,
) -> None:
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
    if suppressed:
        print(palette.dim(f"  {suppressed} suppressed by {policy_path}"))


def as_json(findings: list[Finding]) -> str:
    """A versioned envelope, so a consumer can tell what produced this."""
    return json.dumps(
        {
            "schema": 1,
            "tool": {"name": "ghostpkg", "version": __version__},
            "summary": {
                "checked": len(findings),
                "blocked": sum(1 for f in findings if f.verdict is Verdict.BLOCK),
                "warned": sum(1 for f in findings if f.verdict is Verdict.WARN),
                "errored": sum(1 for f in findings if f.verdict is Verdict.ERROR),
            },
            "findings": [
                {
                    "name": f.name,
                    "ecosystem": f.ecosystem,
                    "verdict": f.verdict.value,
                    "exists": f.facts.exists if f.facts else None,
                    "latest_version": f.facts.latest_version if f.facts else None,
                    "reasons": [
                        {"rule": getattr(r, "rule", None), "text": str(r)}
                        for r in f.reasons
                    ],
                }
                for f in findings
            ],
        },
        indent=2,
    )
