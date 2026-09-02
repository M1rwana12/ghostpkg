"""Suppressing findings you have already decided about.

One false positive is enough for a team to remove a security check from CI, so
there has to be a way to say "we know about this one". Three things make that
safe rather than a hole:

**The file is never read from the project directory.** ghostpkg is meant to sit
in front of a coding agent, and an agent with shell access can edit files in the
repository it is working on. A `.ghostpkgignore` next to the code would be a
suppression list the thing being guarded can rewrite. So the file comes from an
explicit path, an environment variable, or the user's own config directory --
never from the tree being scanned.

**A reason is required.** An entry nobody can justify in one sentence is an
entry nobody will dare remove later.

**A malformed file is an error, not a shrug.** Degrading quietly to "no
suppressions" would be the safe direction; degrading quietly to "no protection"
would not, and from the outside the two are indistinguishable. Failing loudly
is the only honest option.
"""

from __future__ import annotations

import fnmatch
import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

CONFIG_ENV = "GHOSTPKG_CONFIG"
CONFIG_NAME = "ignore.json"


class PolicyError(ValueError):
    """The ignore file exists but could not be used."""


@dataclass(frozen=True)
class Rule:
    """One suppression. `package` and `rule` accept glob patterns."""

    package: str
    reason: str
    ecosystem: str = "*"
    rule: str = "*"
    expires: date | None = None

    def expired(self, today: date | None = None) -> bool:
        return self.expires is not None and self.expires < (today or date.today())

    def matches(self, name: str, ecosystem: str, rule_id: str | None) -> bool:
        if self.ecosystem != "*" and self.ecosystem != ecosystem:
            return False
        if self.rule != "*" and not fnmatch.fnmatch(rule_id or "", self.rule):
            return False
        return fnmatch.fnmatch(name.lower(), self.package.lower())


def config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "ghostpkg"
    if sys.platform == "darwin":
        return Path(os.path.expanduser("~/Library/Application Support")) / "ghostpkg"
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "ghostpkg"


def locate(explicit: str | Path | None = None) -> Path | None:
    """Where the ignore file lives, or None. Never the project directory."""
    if explicit:
        return Path(explicit)
    from_env = os.environ.get(CONFIG_ENV)
    if from_env:
        return Path(from_env)
    candidate = config_dir() / CONFIG_NAME
    return candidate if candidate.is_file() else None


def _parse_date(value, where: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise PolicyError(
            f"{where}: 'expires' must be a date like 2026-12-31, got {value!r}"
        ) from None


def parse(text: str, origin: str = "ignore file") -> list[Rule]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise PolicyError(f"{origin}: not valid JSON ({exc})") from None

    entries = data.get("ignore") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise PolicyError(f"{origin}: expected a list under 'ignore'")

    rules: list[Rule] = []
    for index, entry in enumerate(entries, 1):
        where = f"{origin}, entry {index}"
        if not isinstance(entry, dict):
            raise PolicyError(f"{where}: expected an object")
        package = entry.get("package")
        reason = entry.get("reason")
        if not package or not isinstance(package, str):
            raise PolicyError(f"{where}: 'package' is required")
        if not reason or not isinstance(reason, str):
            raise PolicyError(
                f"{where}: 'reason' is required -- a suppression nobody can "
                f"justify is one nobody will dare remove"
            )
        rules.append(
            Rule(
                package=package,
                reason=reason,
                ecosystem=str(entry.get("ecosystem") or "*"),
                rule=str(entry.get("rule") or "*"),
                expires=_parse_date(entry.get("expires"), where),
            )
        )
    return rules


def load(explicit: str | Path | None = None) -> tuple[list[Rule], Path | None]:
    """Read the ignore file. Missing is fine; malformed is not."""
    path = locate(explicit)
    if path is None:
        return [], None
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise PolicyError(f"could not read {path}: {exc}") from None
    return parse(text, str(path)), path


def apply(finding, rules: list[Rule], today: date | None = None):
    """Drop suppressed reasons and re-derive the verdict.

    Returns the finding and the rules that fired, so the caller can say what was
    suppressed rather than silently showing a clean result.
    """
    from .assess import Verdict  # noqa: PLC0415 - avoids a circular import

    if not rules:
        return finding, []

    kept, used = [], []
    for reason in finding.reasons:
        rule_id = getattr(reason, "rule", None)
        match = next(
            (
                r
                for r in rules
                if not r.expired(today)
                and r.matches(finding.name, finding.ecosystem, rule_id)
            ),
            None,
        )
        if match is None:
            kept.append(reason)
        else:
            used.append(match)

    if not used:
        return finding, []

    finding.reasons = kept
    if not kept:
        finding.verdict = Verdict.OK
    elif finding.verdict is Verdict.BLOCK and not any(
        getattr(r, "rule", None) in finding.blocking for r in kept
    ):
        # The reason that blocked is gone; what remains is only advisory.
        finding.verdict = Verdict.WARN
    return finding, used
