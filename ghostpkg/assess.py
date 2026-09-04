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
    GP_SECURITY_HOLD,
    GP_YANKED,
    Reason,
)
from .registries import PackageFacts

YOUNG_DAYS = 90
NEW_DAYS = 365

# An exact pin, and only an exact pin. `>=`, `^`, `~` and wildcards describe a
# range that the registry may satisfy with some other version, so there is
# nothing definite to check. `==1.2.3` either exists or it does not.
#: `===` is PEP 440 arbitrary equality -- a pin like any other, and the only
#: way to name a version that is not PEP 440 normalisable. Missing it meant
#: such a pin was never checked at all.
PYPI_PIN = re.compile(r"^\s*={2,3}\s*([A-Za-z0-9][A-Za-z0-9.\-+!]*)\s*$")
NPM_PIN = re.compile(r"^\s*v?(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.\-]+)?)\s*$")


def exact_pin(specifier: str | None, ecosystem: str) -> str | None:
    """The single version a specifier demands, if it demands exactly one."""
    if not specifier:
        return None
    specifier = specifier.split(";", 1)[0]
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
    #: Where the name was written. Once a scan can search a whole directory,
    #: "does not exist" without saying which of six files it came from is a
    #: finding the reader has to go and look for by hand. It is also what a CI
    #: annotation needs in order to point at a line.
    source: str | None = None
    line: int | None = None

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


def _normalise(name: str, ecosystem: str) -> str:
    """The name as its own registry understands it.

    PyPI treats `-`, `_` and `.` as one separator and ignores case (PEP 503),
    so `typing_extensions` and `typing-extensions` are the same project. Without
    this the popular-name list was missed on the underscore spelling and the
    same package came back as a one-edit typo of itself. It is unreachable
    today -- PyPI resolves both spellings, so such a name always exists and
    never reaches the comparison -- but the function is public and the next
    reader should not have to work that out.

    npm does no such folding: `JSONStream` and `jsonstream` are two real and
    different packages, so its names are compared as written.
    """
    if ecosystem == "pypi":
        return re.sub(r"[-_.]+", "-", name).lower()
    return name.lower()


def _comparable(name: str, ecosystem: str) -> str:
    """The part of a name worth comparing.

    An npm squat on a scoped package targets the part after the slash, since
    the scope is usually owned by whoever it names.
    """
    lowered = _normalise(name, ecosystem)
    if ecosystem == "npm" and lowered.startswith("@") and "/" in lowered:
        return lowered.rsplit("/", 1)[1]
    return lowered


def nearest_popular(name: str, ecosystem: str = "pypi") -> tuple[str, int] | None:
    """Closest popular package name within the typo budget, if any."""
    popular = POPULAR.get(ecosystem)
    if not popular:
        return None

    lowered = _normalise(name, ecosystem)
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


#: How many packages a scope needs in one scan before its young members stop
#: being warned about. Measured across four repositories: two silences 41% of
#: all warnings, three silences 40%, six silences 39%. The effect saturates
#: immediately, so the threshold sits where it is defensible rather than where
#: it is largest.
TRUSTED_SCOPE_MEMBERS = 3


def trusted_scopes(names: "list[str]") -> "set[str]":
    """npm scopes this scan depends on several times over.

    The justification is structural rather than statistical: **an npm scope is
    owned**. To publish `@oxfmt/binding-darwin-arm64` you must control
    `@oxfmt`. So a young package in a scope the project already uses three
    times is the same publisher shipping another build -- usually one of the
    dozen platform binaries a compiled tool releases at once -- and not a
    stranger who guessed a name.

    A scope is itself squattable: `@types-node` is not `@types/node`. That is
    the risk this has to survive, and the threshold is what does it -- a
    squatted scope appears once in a scan, not three times. An attacker who
    genuinely owned a scope a project leaned on that heavily would have far
    better attacks available than guessing one more name inside it.

    Measured: 106 of 268 warnings across four popular repositories, every one
    of them a platform binary, and none of them something a person would want
    to be told about.
    """
    counts: dict[str, int] = {}
    for name in names:
        if name.startswith("@") and "/" in name:
            scope = name.split("/", 1)[0]
            counts[scope] = counts.get(scope, 0) + 1
    return {scope for scope, n in counts.items() if n >= TRUSTED_SCOPE_MEMBERS}


def assess(
    facts: PackageFacts,
    strict: bool = False,
    signals: "list | None" = None,
    specifier: str | None = None,
    known_scope: bool = False,
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
        reasons = [Reason(GP_MISSING, f"does not exist on {facts.ecosystem}")]
        # The age gate that guards this comparison elsewhere exists to keep a
        # legitimate published package from being called a typo. A name that
        # does not exist has no legitimacy to protect and is already blocked,
        # so a suggestion can only help someone fix the line. Measured on
        # eleven plausible typos it named the right package every time, and on
        # six invented names -- the shape a hallucination usually takes -- it
        # stayed quiet.
        neighbour = nearest_popular(facts.name, facts.ecosystem)
        if neighbour is not None:
            reasons.append(
                Reason(GP_LOOKALIKE, f"did you mean {neighbour[0]}?")
            )
        return Finding(
            name=facts.name,
            ecosystem=facts.ecosystem,
            verdict=Verdict.BLOCK,
            reasons=reasons,
            facts=facts,
            blocking=(GP_MISSING,),
        )

    # A name the registry took away over malware is not a heuristic: somebody
    # published something bad under it and npm replaced it with a placeholder.
    # Because the placeholder exists, this used to come back "ok" -- the tool
    # said a confirmed-malicious name was fine.
    if facts.security_hold:
        return Finding(
            name=facts.name,
            ecosystem=facts.ecosystem,
            verdict=Verdict.BLOCK,
            reasons=[
                Reason(
                    GP_SECURITY_HOLD,
                    "the registry removed this name over malware and replaced "
                    "it with a placeholder",
                )
            ],
            facts=facts,
            blocking=(GP_SECURITY_HOLD,),
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

    # `known_scope` says the scan already depends on this scope several times,
    # so "new and thin" describes a known publisher rather than a stranger.
    # It quiets the soft signals only. Nothing that blocks is affected, and
    # neither is the lookalike check -- a typo inside a trusted scope is still
    # worth saying.
    soft = not known_scope

    # A withdrawn version exists, so it is not a block -- and pip installs one
    # anyway when it is pinned explicitly, so blocking would be stricter than
    # the package manager itself. But the maintainer said not to use it, and
    # usually said why, which is worth passing on. Rare enough to be signal:
    # 0.38% of versions across a dozen popular projects.
    if pin is not None:
        withdrawn = facts.yanked_reason(pin)
        if withdrawn is not None:
            reasons.append(
                Reason(
                    GP_YANKED,
                    f"version {pin} was withdrawn by its maintainer"
                    + (f": {withdrawn}" if withdrawn else ""),
                )
            )

    if soft and facts.age_days is not None:
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

    if soft and is_young and facts.release_count <= 1:
        reasons.append(Reason(GP_ONE_RELEASE, "only one release"))

    if soft and is_young and not facts.has_repo_url:
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
