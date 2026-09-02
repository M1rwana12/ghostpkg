"""Turning findings into something to read, or into JSON."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePath

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

        where = _origin(finding)
        print(f"  {paint(label):<8} {finding.name}{palette.dim(where)}")
        for reason in finding.reasons:
            print(f"           {palette.dim('- ' + reason)}")


def _origin(finding: Finding) -> str:
    """`  (requirements.txt:12)`, or nothing when the name came from argv."""
    if not finding.source:
        return ""
    name = PurePath(finding.source).name
    return f"  ({name}:{finding.line})" if finding.line else f"  ({name})"


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


#: Characters GitHub reads as separators inside a workflow command, so they
#: have to be escaped or the annotation is silently mangled.
_COMMAND_ESCAPES = (("%", "%25"), ("\r", "%0D"), ("\n", "%0A"))
_PROPERTY_ESCAPES = _COMMAND_ESCAPES + ((":", "%3A"), (",", "%2C"))


def _escape(text: str, escapes) -> str:
    for character, replacement in escapes:
        text = text.replace(character, replacement)
    return text


def as_github(findings: list[Finding], root: "Path | None" = None) -> str:
    """Workflow commands, so a pull request is annotated on the offending line.

    A blocking finding is an error and a warning is a warning, which lines up
    with the exit code: the annotation and the failed job agree about severity.
    Findings with no file behind them -- `ghostpkg check name` -- are still
    printed, just without a location.
    """
    base = root or Path.cwd()
    lines = []
    for finding in findings:
        if finding.verdict is Verdict.OK:
            continue
        level = "error" if finding.verdict is not Verdict.WARN else "warning"
        properties = []
        if finding.source:
            path = Path(finding.source)
            try:
                path = path.resolve().relative_to(base.resolve())
            except (ValueError, OSError):
                pass
            properties.append(f"file={_escape(path.as_posix(), _PROPERTY_ESCAPES)}")
            if finding.line:
                properties.append(f"line={finding.line}")
        rules = ",".join(getattr(r, "rule", "") for r in finding.reasons if getattr(r, "rule", None))
        title = f"ghostpkg {rules}" if rules else "ghostpkg"
        properties.append(f"title={_escape(title, _PROPERTY_ESCAPES)}")
        detail = "; ".join(str(r) for r in finding.reasons) or finding.verdict.value
        message = _escape(f"{finding.name}: {detail}", _COMMAND_ESCAPES)
        lines.append(f"::{level} {','.join(properties)}::{message}")
    return "\n".join(lines)


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
                    "source": f.source,
                    "line": f.line,
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
