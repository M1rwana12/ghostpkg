"""Withdrawn versions, and names the registry took away.

Both cases share a shape the tool used to get wrong: the thing *exists*, so an
existence check says "ok" and stops. A version the maintainer withdrew, and a
name npm confiscated after somebody published malware under it, are both real
entries in the registry.
"""

from __future__ import annotations

import pytest

from ghostpkg.assess import Verdict, assess
from ghostpkg.registries import PackageFacts


def facts(**overrides) -> PackageFacts:
    base = dict(
        name="requests",
        ecosystem="pypi",
        exists=True,
        age_days=4000,
        release_count=160,
        has_repo_url=True,
        latest_version="2.34.2",
        versions=("2.31.0", "2.32.0", "2.34.2"),
    )
    base.update(overrides)
    return PackageFacts(**base)


class TestWithdrawnVersions:
    """PyPI's `yanked` covers 0.38% of versions across a dozen popular
    projects -- rare enough to be signal. npm's nearest equivalent,
    `deprecated`, covers 5.78% and reaches 160 of glob's 168 versions, because
    it is used routinely for superseded branches. So this is PyPI only."""

    withdrawn = (("2.32.0", "Yanked due to conflicts with CVE-2024-35195"),)

    def test_a_withdrawn_pin_is_reported(self):
        finding = assess(facts(yanked=self.withdrawn), specifier="==2.32.0")
        assert finding.verdict is Verdict.WARN
        assert any("withdrawn" in reason for reason in finding.reasons)

    def test_the_maintainers_reason_is_passed_on(self):
        finding = assess(facts(yanked=self.withdrawn), specifier="==2.32.0")
        assert any("CVE-2024-35195" in reason for reason in finding.reasons)

    def test_it_warns_rather_than_blocks(self):
        """The version exists, and pip installs a withdrawn one when it is
        pinned explicitly -- blocking would be stricter than the package
        manager itself."""
        assert assess(facts(yanked=self.withdrawn), specifier="==2.32.0").verdict is Verdict.WARN

    def test_a_healthy_pin_is_untouched(self):
        assert assess(facts(yanked=self.withdrawn), specifier="==2.31.0").verdict is Verdict.OK

    def test_a_range_is_not_checked_against_withdrawals(self):
        """A range resolves to whatever the installer picks, which will not be
        the withdrawn one."""
        assert assess(facts(yanked=self.withdrawn), specifier=">=2.0").verdict is Verdict.OK

    def test_no_pin_means_nothing_to_say(self):
        assert assess(facts(yanked=self.withdrawn)).verdict is Verdict.OK


class TestNamesTheRegistryConfiscated:
    """npm does not delete a name it removes for malware, and does not answer
    451. It republishes a placeholder it owns, pointing at
    github.com/npm/security-holder -- `crossenv` and `ffmepg`, both real
    typosquat incidents, look exactly like that.

    Because the placeholder exists, these came back **ok**: the tool told you a
    confirmed-malicious name was fine.
    """

    def held(self, name="lodahs"):
        return PackageFacts(
            name=name, ecosystem="npm", exists=True, age_days=2100,
            release_count=1, has_repo_url=True, security_hold=True,
        )

    def test_a_confiscated_name_is_blocked(self):
        finding = assess(self.held())
        assert finding.verdict is Verdict.BLOCK
        assert finding.blocking == ("GP011",)

    def test_the_reason_says_what_happened(self):
        finding = assess(self.held())
        assert any("malware" in reason for reason in finding.reasons)

    def test_it_outranks_every_softer_signal(self):
        """A confiscated name is a confirmed verdict, not one input among
        several."""
        finding = assess(self.held())
        assert len(finding.reasons) == 1

    def test_an_ordinary_package_is_untouched(self):
        finding = assess(
            PackageFacts(
                name="express", ecosystem="npm", exists=True, age_days=5700,
                release_count=288, has_repo_url=True,
            )
        )
        assert finding.verdict is Verdict.OK

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("git+https://github.com/npm/security-holder.git", True),
            ("https://github.com/npm/security-holder", True),
            ("git+https://github.com/expressjs/express.git", False),
            ("", False),
        ],
    )
    def test_detection_matches_the_repository_not_the_description(self, url, expected):
        """A description is free text anyone could copy; the repository is not."""
        from ghostpkg.registries import SECURITY_HOLDER

        assert (SECURITY_HOLDER in url) is expected
