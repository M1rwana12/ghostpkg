"""Tests for the assessment policy.

These construct PackageFacts directly, so the suite never touches the network
and cannot break when a real package changes.
"""

import pytest

from ghostpkg.assess import Verdict, assess, edit_distance, nearest_popular
from ghostpkg.data import TOP_NPM, TOP_PYPI
from ghostpkg.registries import PackageFacts
from ghostpkg.rules import GP_LOOKALIKE, GP_MISSING


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


class TestNpmTypoDetection:
    """npm used to be compared against the PyPI popular list, which meant
    `expresss` was never flagged because `express` was not in the list at all."""

    def test_npm_lookalike_is_flagged(self):
        assert nearest_popular("expresss", "npm") == ("express", 1)

    def test_ecosystems_do_not_cross_contaminate(self):
        assert nearest_popular("expresss", "pypi") is None
        assert nearest_popular("requestss", "npm") is None

    def test_unknown_ecosystem_is_silent(self):
        assert nearest_popular("whatever", "crates") is None

    def test_scoped_name_compares_the_package_part(self):
        assert nearest_popular("@evil/expresss", "npm") == ("express", 1)

    def test_legitimate_scoped_packages_pass(self):
        assert nearest_popular("@types/node", "npm") is None
        assert nearest_popular("@babel/core", "npm") is None

    def test_very_short_names_are_not_compared(self):
        """Below five characters the name space is too dense to discriminate:
        'core' sits one edit from 'cors'."""
        assert nearest_popular("chak", "npm") is None


class TestTranspositions:
    """Swapping two adjacent characters is the commonest typosquat shape, and
    plain Levenshtein scores it as two edits -- outside the budget for short
    names, which let every one of these through."""

    @pytest.mark.parametrize(
        "typo,real,ecosystem",
        [
            ("recat", "react", "npm"),
            ("lodahs", "lodash", "npm"),
            ("webpakc", "webpack", "npm"),
            ("reqeusts", "requests", "pypi"),
            ("nupmy", "numpy", "pypi"),
        ],
    )
    def test_transposed_names_are_caught(self, typo, real, ecosystem):
        assert nearest_popular(typo, ecosystem) == (real, 1)

    def test_transposition_costs_one_edit(self):
        assert edit_distance("abc", "acb") == 1


class TestNoFalsePositivesOnPopularNames:
    """The guard that matters most. A tool that flags real packages gets
    switched off, and then it protects nothing."""

    def test_no_popular_pypi_name_is_flagged(self):
        flagged = [n for n in TOP_PYPI if nearest_popular(n, "pypi") is not None]
        assert flagged == []

    def test_no_popular_npm_name_is_flagged(self):
        flagged = [n for n in TOP_NPM if nearest_popular(n, "npm") is not None]
        assert flagged == []


class TestAbandonedLookalikes:
    """Age was the wrong gate for typo detection.

    `expresss` has sat on npm since 2016 with one release, no repository link,
    and roughly 2,500 downloads a month arriving purely from other people's
    typos. It is ten years old, so an age gate let it straight through.
    """

    def old_squat(self, **overrides):
        base = dict(
            name="expresss",
            ecosystem="npm",
            exists=True,
            age_days=3400,
            release_count=1,
            has_repo_url=False,
        )
        base.update(overrides)
        return PackageFacts(**base)

    def test_old_parked_lookalike_is_flagged(self):
        finding = assess(self.old_squat())
        assert finding.verdict is Verdict.WARN
        assert any("express" in reason for reason in finding.reasons)

    def test_reason_says_why_rather_than_calling_it_new(self):
        finding = assess(self.old_squat())
        assert any("one release, no repository" in r for r in finding.reasons)
        assert not any("recently published" in r for r in finding.reasons)

    def test_a_maintained_lookalike_is_left_alone(self):
        """Sibling packages in a family sit close together -- dagster-k8s is two
        edits from dagster-aws -- and they are maintained."""
        finding = assess(self.old_squat(release_count=630, has_repo_url=True))
        assert finding.verdict is Verdict.OK

    def test_releases_alone_are_not_enough_to_fire(self):
        """Measured on 120 real lookalike-shaped packages, few-releases alone
        was wrong 10% of the time."""
        finding = assess(self.old_squat(release_count=1, has_repo_url=True))
        assert not any("away from" in r for r in finding.reasons)

    def test_missing_repo_alone_is_not_enough_to_fire(self):
        """And no-repository alone was wrong 5.8% of the time."""
        finding = assess(self.old_squat(release_count=40, has_repo_url=False))
        assert not any("away from" in r for r in finding.reasons)

    def test_a_defensively_held_name_passes(self):
        """npm parks some names itself, with a repository link on the holder."""
        finding = assess(
            PackageFacts(
                name="lodahs", ecosystem="npm", exists=True, age_days=2100,
                release_count=1, has_repo_url=True,
            )
        )
        assert finding.verdict is Verdict.OK


class TestASuggestionOnANameThatDoesNotExist:
    """The age gate that guards the typo comparison elsewhere exists to keep a
    legitimate published package from being called a typo. A name that does not
    exist has no legitimacy to protect and is already blocked, so naming the
    likely intent can only help. Measured: right on 11 of 11 plausible typos,
    silent on 6 invented names."""

    def missing(self, name, ecosystem="pypi"):
        return assess(PackageFacts(name=name, ecosystem=ecosystem, exists=False))

    @pytest.mark.parametrize(
        "typo, meant",
        [
            ("reqeusts", "requests"),
            ("beautifulsoop", "beautifulsoup4"),
            ("djagno", "django"),
            ("numpyy", "numpy"),
            ("scikit-lean", "scikit-learn"),
        ],
    )
    def test_the_likely_intent_is_named(self, typo, meant):
        finding = self.missing(typo)
        assert finding.verdict is Verdict.BLOCK
        assert any(f"did you mean {meant}?" in r for r in finding.reasons)

    @pytest.mark.parametrize(
        "name",
        ["fastapi-auth-helper", "langchain-pinecone-utils", "acme-corp-widgets"],
    )
    def test_an_invented_name_gets_no_guess(self, name):
        """The shape a hallucination usually takes is not a typo of anything,
        and inventing a suggestion would be noise on top of a block."""
        finding = self.missing(name)
        assert not any("did you mean" in r for r in finding.reasons)

    def test_the_block_still_comes_from_the_missing_rule(self):
        """A suggestion is an explanation, not a second reason to block --
        suppressing it must not change the verdict."""
        finding = self.missing("reqeusts")
        assert finding.blocking == (GP_MISSING,)

    def test_the_suggestion_carries_the_lookalike_rule(self):
        finding = self.missing("reqeusts")
        suggestion = [r for r in finding.reasons if "did you mean" in r][0]
        assert suggestion.rule == GP_LOOKALIKE


class TestSeparatorSpellingIsNotATypo:
    """PyPI treats `-`, `_` and `.` as one separator and ignores case, so
    `typing_extensions` and `typing-extensions` are the same project. The
    popular-name list stores the normalised spelling, and comparing the raw one
    against it missed the match -- the package then came back as a one-edit typo
    of itself.

    It is unreachable in the product: PyPI resolves both spellings, so such a
    name always exists, and the lookalike check runs only on packages that are
    young or abandoned while the suggestion path runs only on names that do not
    exist. It is fixed anyway because `nearest_popular` is public, and because
    working out why it was harmless took longer than the fix.
    """

    @pytest.mark.parametrize(
        "name",
        ["typing_extensions", "pre_commit", "readme_renderer", "et_xmlfile",
         "zope.interface", "jaraco.classes", "TYPING-EXTENSIONS"],
    )
    def test_a_different_spelling_of_a_popular_name_is_silent(self, name):
        assert nearest_popular(name, "pypi") is None

    @pytest.mark.parametrize(
        "typo, meant",
        [("reqeusts", "requests"), ("djagno", "django"), ("numpyy", "numpy")],
    )
    def test_real_typos_are_still_found(self, typo, meant):
        assert nearest_popular(typo, "pypi")[0] == meant

    def test_npm_names_are_not_folded(self):
        """`JSONStream` and `jsonstream` are two different real packages there,
        so npm keeps case and separators as written."""
        assert nearest_popular("JSONStream", "npm") is None
        assert nearest_popular("jsonstream", "npm") is None
