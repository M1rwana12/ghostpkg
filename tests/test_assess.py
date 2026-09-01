"""Tests for the assessment policy.

These construct PackageFacts directly, so the suite never touches the network
and cannot break when a real package changes.
"""

import pytest

from ghostpkg.assess import Verdict, assess, edit_distance, nearest_popular
from ghostpkg.registries import PackageFacts


def facts(**overrides) -> PackageFacts:
    base = dict(
        name="example",
        ecosystem="pypi",
        exists=True,
        age_days=2000,
        release_count=25,
        has_repo_url=True,
    )
    base.update(overrides)
    return PackageFacts(**base)


class TestMissingPackages:
    def test_absent_package_is_blocked(self):
        finding = assess(facts(name="totally-invented", exists=False))
        assert finding.verdict is Verdict.BLOCK
        assert "does not exist on pypi" in finding.reasons

    def test_absence_blocks_even_without_strict(self):
        """Non-existence is the one signal precise enough to block by default."""
        assert assess(facts(exists=False), strict=False).is_blocked


class TestEstablishedPackages:
    def test_mature_package_passes(self):
        assert assess(facts()).verdict is Verdict.OK

    def test_mature_package_without_repo_link_still_passes(self):
        """Age is what earns trust; an old package with no link is not suspicious."""
        assert assess(facts(has_repo_url=False)).verdict is Verdict.OK

    def test_mature_single_release_package_passes(self):
        assert assess(facts(release_count=1)).verdict is Verdict.OK


class TestNewPackages:
    def test_new_package_warns_but_does_not_block(self):
        """The measured reason for this rule: blocking on youth flags every
        legitimate package published this week."""
        finding = assess(facts(age_days=3, release_count=1, has_repo_url=False))
        assert finding.verdict is Verdict.WARN
        assert not finding.is_blocked

    def test_strict_mode_promotes_warning_to_block(self):
        finding = assess(facts(age_days=3, release_count=1), strict=True)
        assert finding.verdict is Verdict.BLOCK

    def test_reasons_are_reported(self):
        finding = assess(facts(age_days=5, release_count=1, has_repo_url=False))
        assert any("5 days ago" in r for r in finding.reasons)
        assert any("one release" in r for r in finding.reasons)
        assert any("repository" in r for r in finding.reasons)


class TestTyposquatDetection:
    def test_young_lookalike_is_flagged(self):
        finding = assess(facts(name="requestss", age_days=10))
        assert finding.verdict is Verdict.WARN
        assert any("requests" in r for r in finding.reasons)

    def test_established_lookalike_is_not_flagged(self):
        """An old package that merely resembles a popular one is not a squat."""
        finding = assess(facts(name="requestss", age_days=3000))
        assert not any("away from" in r for r in finding.reasons)

    @pytest.mark.parametrize("name", ["flask", "click", "black", "attrs", "six"])
    def test_short_popular_names_are_never_squats_of_each_other(self, name):
        """Regression: a flat edit-distance budget flagged flask, click and
        black as typos of one another, because short names sit close together."""
        assert nearest_popular(name) is None

    def test_distance_budget_is_tighter_for_short_names(self):
        assert nearest_popular("blackk") is not None   # 1 edit from "black"
        assert nearest_popular("blvcx") is None        # 2 edits from "black",
        #                                                under the 10-char length
        #                                                where a 2-edit budget applies


class TestEditDistance:
    @pytest.mark.parametrize(
        "left,right,expected",
        [("abc", "abc", 0), ("abc", "abd", 1), ("abc", "axc", 1), ("", "ab", 2)],
    )
    def test_known_distances(self, left, right, expected):
        assert edit_distance(left, right) == expected

    def test_far_apart_strings_short_circuit_above_cutoff(self):
        assert edit_distance("a", "abcdefghij", cutoff=3) > 3
