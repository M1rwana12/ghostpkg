"""Risk assessment for a package name.

The policy here is shaped by a measurement, not by taste. Validation against
the live PyPI feed showed that "young, one release, no repository link"
describes a malicious slopsquat and an honest new project equally well: a
detector that blocks on youth flags 100% of legitimate brand-new packages.

So the default profile blocks on exactly one thing -- the package does not
exist -- because that signal is precise and it is the one that actually
corresponds to a hallucination. Everything softer is reported as a warning
and left to a human. `--strict` promotes warnings to blocks for people who
want that trade, but it is not the default and it is not recommended for CI
that installs new packages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .data import TOP_NPM, TOP_PYPI
from .registries import PackageFacts

YOUNG_DAYS = 90
NEW_DAYS = 365


class Verdict(str, Enum):
    OK = "OK"
    WARN = "WARN"
    BLOCK = "BLOCK"


@dataclass
class Finding:
    name: str
    ecosystem: str
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    facts: PackageFacts | None = None

    @property
    def is_blocked(self) -> bool:
        return self.verdict is Verdict.BLOCK


def edit_distance(left: str, right: str, cutoff: int = 3) -> int:
    """Damerau-Levenshtein distance, abandoning early once it exceeds `cutoff`.

    Swapping two adjacent characters counts as one edit, not two. That matters
    here more than it looks: transposition is the commonest typosquat shape --
    `recat` for `react`, `lodahs` for `lodash`, `webpakc` for `webpack`. Plain
    Levenshtein scores all three as two edits, which put them outside the budget
    for names of that length and let every one of them through.
    """
    if left == right:
        return 0
    if abs(len(left) - len(right)) > cutoff:
        return cutoff + 1

    before_previous: list[int] = []
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            cost = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b))
            if (
                i > 1
                and j > 1
                and a == right[j - 2]
                and left[i - 2] == b
            ):
                cost = min(cost, before_previous[j - 2] + 1)
            current.append(cost)
        if min(current) > cutoff:
            return cutoff + 1
        before_previous, previous = previous, current
    return previous[-1]


POPULAR: dict[str, frozenset[str]] = {"pypi": TOP_PYPI, "npm": TOP_NPM}

MIN_COMPARABLE_LENGTH = 5


def _typo_budget(name: str) -> int:
    """How many edits still count as a plausible typo of a popular name.

    Short names are inherently close to each other -- 'flask', 'black' and
    'click' sit within two edits -- so a flat budget produces false positives
    on exactly the packages people use most.
    """
    return 2 if len(name) >= 10 else 1


def _comparable(name: str, ecosystem: str) -> str:
    """The part of a name worth comparing.

    An npm squat on a scoped package targets the part after the slash, since
    the scope is usually owned by whoever it names.
    """
    lowered = name.lower()
    if ecosystem == "npm" and lowered.startswith("@") and "/" in lowered:
        return lowered.rsplit("/", 1)[1]
    return lowered


def nearest_popular(name: str, ecosystem: str = "pypi") -> tuple[str, int] | None:
    """Closest popular package name within the typo budget, if any."""
    popular = POPULAR.get(ecosystem)
    if not popular:
        return None

    lowered = name.lower()
    target = _comparable(name, ecosystem)

    if lowered in popular or target in popular:
        return None
    # Below this length the name space is too dense for edit distance to mean
    # anything: 'core' sits one edit from 'cors'.
    if len(target) < MIN_COMPARABLE_LENGTH:
        return None

    budget = _typo_budget(target)
    best: tuple[str, int] | None = None
    for candidate in popular:
        if abs(len(candidate) - len(target)) > budget:
            continue
        distance = edit_distance(target, candidate, cutoff=budget)
        if 0 < distance <= budget and (best is None or distance < best[1]):
            best = (candidate, distance)
            if distance == 1:
                break
    return best


def assess(facts: PackageFacts, strict: bool = False) -> Finding:
    if not facts.exists:
        return Finding(
            name=facts.name,
            ecosystem=facts.ecosystem,
            verdict=Verdict.BLOCK,
            reasons=[f"does not exist on {facts.ecosystem}"],
            facts=facts,
        )

    reasons: list[str] = []

    if facts.age_days is not None:
        if facts.age_days < YOUNG_DAYS:
            reasons.append(f"first published {facts.age_days} days ago")
        elif facts.age_days < NEW_DAYS:
            reasons.append(f"first published {facts.age_days} days ago (under a year)")

    is_young = facts.age_days is not None and facts.age_days < NEW_DAYS

    if is_young:
        neighbour = nearest_popular(facts.name, facts.ecosystem)
        if neighbour is not None:
            popular_name, distance = neighbour
            reasons.append(
                f"{distance} character{'s' if distance > 1 else ''} away from "
                f"'{popular_name}', and recently published"
            )

    if is_young and facts.release_count <= 1:
        reasons.append("only one release")

    if is_young and not facts.has_repo_url:
        reasons.append("no repository or homepage link")

    if not reasons:
        verdict = Verdict.OK
    elif strict:
        verdict = Verdict.BLOCK
    else:
        verdict = Verdict.WARN

    return Finding(
        name=facts.name,
        ecosystem=facts.ecosystem,
        verdict=verdict,
        reasons=reasons,
        facts=facts,
    )
