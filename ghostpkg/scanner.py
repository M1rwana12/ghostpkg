"""Turning a list of requirements into findings.

Kept apart from the CLI because this is the part worth calling from something
that is not a terminal -- a hook, an editor, a server.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import replace

from .assess import NEW_DAYS, Finding, Verdict, assess
from .cache import Cache
from .inspection import InspectionError, inspect_package
from .manifests import Requirement
from .registries import RegistryError, fetch
from .rules import GP_NOT_INSPECTED, GP_UNCHECKED, Reason

DEFAULT_WORKERS = 8


def evaluate(
    requirements: "list[Requirement] | list[str]",
    ecosystem: str,
    strict: bool = False,
    cache: Cache | None = None,
    deep: bool = False,
    workers: int = DEFAULT_WORKERS,
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
                source=requirement.source,
                line=requirement.line,
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
        finding.source = requirement.source
        finding.line = requirement.line
        # Saying nothing would let "could not inspect" read as "inspected and
        # clean" -- and padding an archive past the size limit would then be a
        # way to switch --deep off from the outside.
        if not_inspected and not finding.is_blocked:
            finding.reasons.append(
                Reason(
                    GP_NOT_INSPECTED, f"install scripts not inspected: {not_inspected}"
                )
            )
            if finding.verdict is Verdict.OK:
                finding.verdict = Verdict.WARN
        return finding

    # Look each distinct (name, pin) pair up once. A manifest can repeat a
    # name, and following -r includes makes that more likely.
    unique: "dict[tuple[str, str | None], Requirement]" = {}
    for item in items:
        unique.setdefault((item.name, item.specifier), item)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = dict(zip(unique, pool.map(one, unique.values())))
    if cache:
        cache.save()

    # One lookup, but one finding per place the name was written -- otherwise
    # the same package listed in two files reports whichever was looked up.
    findings = []
    for item in items:
        shared = results[(item.name, item.specifier)]
        findings.append(replace(shared, source=item.source, line=item.line))
    return findings
