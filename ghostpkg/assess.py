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

import re
from dataclasses import dataclass, field
from enum import Enum

from .data import TOP_NPM, TOP_PYPI
from .rules import (
    GP_BAD_VERSION,
    GP_INSTALL_CODE,
    GP_LOOKALIKE,
    GP_MISSING,
    GP_NO_REPO,
    GP_ONE_RELEASE,
    GP_RECENT,
    Reason,
)
from .registries import PackageFacts

YOUNG_DAYS = 90
NEW_DAYS = 365

# An exact pin, and only an exact pin. `>=`, `^`, `~` and wildcards describe a
# range that the registry may satisfy with some other version, so there is
# nothing definite to check. `==1.2.3` either exists or it does not.
PYPI_PIN = re.compile(r"^\s*==\s*([A-Za-z0-9][A-Za-z0-9.\-+!]*)\s*$")
NPM_PIN = re.compile(r"^\s*v?(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.\-]+)?)\s*$")


def exact_pin(specifier: str | None, ecosystem: str) -> str | None:
    """The single version a specifier demands, if it demands exactly one."""
    if not specifier:
        return None
    pattern = NPM_PIN if ecosystem == "npm" else PYPI_PIN
    match = pattern.match(specifier)
    if not match:
        return None
    version = match.group(1)
    # `==1.2.*` is a range wearing a pin's clothes.
    return None if "*" in version else version


class Verdict(str, Enum):
    OK = "OK"
    WARN = "WARN"
    BLOCK = "BLOCK"
    #: The registry could not be reached for this name. Never a pass: a
    #: security check that silently succeeds when it could not run is worse
    #: than no check at all.
    ERROR = "ERROR"


@dataclass
class Finding:
    name: str
    ecosystem: str
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    facts: PackageFacts | None = None
    #: Rule ids that caused a BLOCK. Kept so that suppressing the blocking
    #: reason downgrades the verdict instead of leaving a block with no
    #: explanation attached to it.
    blocking: tuple[str, ...] = ()

    @property
    def is_blocked(self) -> bool:
        return self.verdict is Verdict.BLOCK

    @property
    def is_error(self) -> bool:
        return self.verdict is Verdict.ERROR


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

# Iterating a frozenset has no defined order, so with several candidates at the
# same distance the reported neighbour changed between runs -- `cjson` came back
# as `ujson` or `ijson` depending on PYTHONHASHSEED. The verdict was stable and
# only the explanation moved, but unreproducible output is a poor look on a
# security tool. Membership tests still use the sets above.
POPULAR_ORDERED: dict[str, tuple[str, ...]] = {
    ecosystem: tuple(sorted(names)) for ecosystem, names in POPULAR.items()
}

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
    for candidate in POPULAR_ORDERED[ecosystem]:
        if abs(len(candidate) - len(target)) > budget:
            continue
        distance = edit_distance(target, candidate, cutoff=budget)
        if 0 < distance <= budget and (best is None or distance < best[1]):
            best = (candidate, distance)
            if distance == 1:
                break
    return best


def assess(
    facts: PackageFacts,
    strict: bool = False,
    signals: "list | None" = None,
    specifier: str | None = None,
) -> Finding:
    """Turn registry facts, and optionally --deep install-script signals, into
    a verdict.

    Install-time signals are treated differently from every other soft signal,
    and the difference is measured rather than assumed. Age flags 100% of
    legitimate same-day publications, so it can only ever warn. Install-time
    behaviour flagged 0 of 27 established and 0 of 32 brand-new real packages
    while catching all six known malicious shapes, so a *young* package that
    reaches for the network, a subprocess or a decoded payload during install
    is specific enough to block.

    An established package doing the same is only warned about: legitimate
    old packages do sometimes build things at install time, and the sample
    behind that judgement is small.
    """
    if not facts.exists:
        return Finding(
            name=facts.name,
            ecosystem=facts.ecosystem,
            verdict=Verdict.BLOCK,
            reasons=[Reason(GP_MISSING, f"does not exist on {facts.ecosystem}")],
            facts=facts,
            blocking=(GP_MISSING,),
        )

    # A pinned version that does not exist is the same class of mistake as a
    # name that does not exist, and just as precise: the registry lists every
    # real version, so this is a lookup, not a heuristic. It blocks for the
    # same reason non-existence does.
    pin = exact_pin(specifier, facts.ecosystem)
    if pin is not None and facts.has_version(pin) is False:
        return Finding(
            name=facts.name,
            ecosystem=facts.ecosystem,
            verdict=Verdict.BLOCK,
            reasons=[
                Reason(
                    GP_BAD_VERSION,
                    f"version {pin} does not exist (latest is {facts.latest_version})"
                    if facts.latest_version
                    else f"version {pin} does not exist",
                )
            ],
            facts=facts,
            blocking=(GP_BAD_VERSION,),
        )

    reasons: list[Reason] = []

    if facts.age_days is not None:
        if facts.age_days < YOUNG_DAYS:
            reasons.append(
                Reason(GP_RECENT, f"first published {facts.age_days} days ago")
            )
        elif facts.age_days < NEW_DAYS:
            reasons.append(
                Reason(
                    GP_RECENT,
                    f"first published {facts.age_days} days ago (under a year)",
                )
            )

    is_young = facts.age_days is not None and facts.age_days < NEW_DAYS

    # A parked lookalike is not necessarily new. `expresss` has sat on npm since
    # 2016 with one release, no repository link, and roughly 2,500 downloads a
    # month arriving purely from other people's typos. Age was the wrong gate.
    #
    # Abandonment is the right one, but only as a pair. Measured against 120 real
    # packages that sit within the typo budget of a popular name, "few releases"
    # alone was wrong 10% of the time and "no repository link" alone 5.8%, while
    # requiring both was wrong 0% of the time. That matters because sibling
    # packages in a family are naturally close together -- dagster-k8s is two
    # edits from dagster-aws, pulumi-tls from pulumi-aws -- and they are
    # maintained, so they carry releases and a repository.
    looks_abandoned = facts.release_count <= 2 and not facts.has_repo_url

    if is_young or looks_abandoned:
        neighbour = nearest_popular(facts.name, facts.ecosystem)
        if neighbour is not None:
            popular_name, distance = neighbour
            character = "character" if distance == 1 else "characters"
            context = "recently published" if is_young else "one release, no repository"
            reasons.append(
                Reason(
                    GP_LOOKALIKE,
                    f"{distance} {character} away from '{popular_name}', and {context}",
                )
            )

    if is_young and facts.release_count <= 1:
        reasons.append(Reason(GP_ONE_RELEASE, "only one release"))

    if is_young and not facts.has_repo_url:
        reasons.append(Reason(GP_NO_REPO, "no repository or homepage link"))

    install_reasons = [
        Reason(GP_INSTALL_CODE, str(signal)) for signal in (signals or [])
    ]
    reasons.extend(install_reasons)

    blocking: tuple[str, ...] = ()
    if install_reasons and is_young:
        verdict = Verdict.BLOCK
        blocking = (GP_INSTALL_CODE,)
    elif not reasons:
        verdict = Verdict.OK
    elif strict:
        verdict = Verdict.BLOCK
        blocking = tuple({r.rule for r in reasons if hasattr(r, "rule")})
    else:
        verdict = Verdict.WARN

    return Finding(
        name=facts.name,
        ecosystem=facts.ecosystem,
        verdict=verdict,
        reasons=reasons,
        facts=facts,
        blocking=blocking,
    )
