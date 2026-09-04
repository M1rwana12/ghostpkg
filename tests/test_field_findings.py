"""Defects found by scanning three large real repositories.

10,109 packages across `vercel/next.js`, `home-assistant/core` and
`getsentry/sentry` produced 53 blocks, and every one of them was false. None of
these shapes appear in a small synthetic manifest, which is why a suite of 539
tests and a 35-check acceptance pass had missed all four.
"""

from __future__ import annotations

import json

import pytest

from ghostpkg.cli import main
from ghostpkg.manifests import declared_name, load_manifest, parse_npm_names
from ghostpkg.registries import PackageFacts, normalise_version


def names(requirements):
    return [r.name for r in requirements]


class TestScopedNamesOnTheCommandLine:
    """`ghostpkg check -e npm "@evil-corp/fake"` printed "all 0 packages look
    fine" and exited 0. The names went through the requirements parser, whose
    pattern demands an alphanumeric first character, so every scoped name was
    dropped in silence -- a security tool reporting success having checked
    nothing, on the command it is named for."""

    @pytest.mark.parametrize(
        "argument, name, specifier",
        [
            ("@scope/name", "@scope/name", None),
            ("@scope/name@1.2.3", "@scope/name", "1.2.3"),
            ("@babel/core", "@babel/core", None),
            ("lodash", "lodash", None),
            ("lodash@4.17.21", "lodash", "4.17.21"),
            ("lodash@^4", "lodash", "^4"),
        ],
    )
    def test_the_name_and_pin_are_read(self, argument, name, specifier):
        found = parse_npm_names([argument])
        assert (found[0].name, found[0].specifier) == (name, specifier)

    @pytest.mark.parametrize("argument", ["", "   ", "@"])
    def test_nothing_usable_yields_nothing(self, argument):
        assert parse_npm_names([argument]) == []

    def test_a_scoped_name_reaches_the_registry(self, monkeypatch):
        looked_up = []

        def fake_fetch(name, ecosystem):
            looked_up.append(name)
            return PackageFacts(name=name, ecosystem=ecosystem, exists=False)

        monkeypatch.setattr("ghostpkg.scanner.fetch", fake_fetch)
        assert main(["check", "-e", "npm", "@evil-corp/fake", "--no-cache"]) == 1
        assert looked_up == ["@evil-corp/fake"]

    def test_an_empty_name_list_is_not_a_pass(self, tmp_path):
        """Exit 0 here would be the original bug in a different disguise."""
        assert main(["check", "-e", "npm", "@", "--no-cache"]) == 3


class TestVersionsAreComparedAfterNormalisation:
    """`aiopurpleair==2025.08.1` was blocked as a version that does not exist,
    while `pip download` installed it. PyPI stores the canonical `2025.8.1`,
    and the comparison was made on the raw text. Straight out of unmodified
    Home Assistant requirements, and a false block breaks a build."""

    @pytest.mark.parametrize(
        "left, right",
        [
            ("2025.08.1", "2025.8.1"),
            ("2025.09.0", "2025.9.0"),
            ("2026.01.1", "2026.1.1"),
            ("1.0-RC1", "1.0rc1"),
            ("1.0.0-alpha1", "1.0.0a1"),
            ("v2.0.0", "2.0.0"),
            ("1.0.0", "1.0.0"),
        ],
    )
    def test_the_same_release_spelled_differently(self, left, right):
        assert normalise_version(left) == normalise_version(right)

    @pytest.mark.parametrize(
        "left, right",
        [("1.0.0", "1.0.1"), ("1.0.0", "1.0"), ("2.0.0", "20.0.0"), ("1.0a1", "1.0b1")],
    )
    def test_different_releases_stay_different(self, left, right):
        assert normalise_version(left) != normalise_version(right)

    def test_a_pinned_version_is_accepted_in_either_spelling(self):
        facts = PackageFacts(
            name="thing", ecosystem="pypi", exists=True, versions=("2025.8.1",)
        )
        assert facts.has_version("2025.08.1") is True
        assert facts.has_version("2025.8.1") is True

    def test_a_version_that_really_is_absent_is_still_refused(self):
        facts = PackageFacts(
            name="thing", ecosystem="pypi", exists=True, versions=("1.0.0",)
        )
        assert facts.has_version("99.99.99") is False

    def test_npm_is_compared_literally(self):
        """semver forbids leading zeros, so there is nothing to fold, and
        folding would risk accepting a version that does not exist."""
        facts = PackageFacts(
            name="thing", ecosystem="npm", exists=True, versions=("1.2.3",)
        )
        assert facts.has_version("01.2.3") is False


class TestManifestInIsNotRequirements:
    """`.in` is the pip-tools convention, and also the extension of
    `MANIFEST.in`. Read as requirements it reported `graft` as a package that
    exists -- there is a real project of that name -- and blocked
    `recursive-exclude`."""

    def test_manifest_in_is_refused(self, tmp_path):
        path = tmp_path / "MANIFEST.in"
        path.write_text("include README.md\ngraft docs\n", encoding="utf-8")
        with pytest.raises(Exception):
            load_manifest(path)

    @pytest.mark.parametrize("filename", ["requirements.in", "dev-requirements.in", "reqs.in"])
    def test_a_real_requirements_source_is_still_read(self, tmp_path, filename):
        path = tmp_path / filename
        path.write_text("flask\n", encoding="utf-8")
        assert names(load_manifest(path)[0]) == ["flask"]


class TestAPackageTheCheckoutProvides:
    """All three names blocked in a 6,335-package scan of `vercel/next.js` were
    the repository's own packages. A monorepo depends on itself, and not always
    through `workspace:*` -- an exact pin is just as common."""

    def test_declared_name_from_package_json(self, tmp_path):
        path = tmp_path / "package.json"
        path.write_text('{"name":"@acme/font","version":"1.0.0"}', encoding="utf-8")
        assert declared_name(path) == "@acme/font"

    def test_declared_name_from_pyproject(self, tmp_path):
        path = tmp_path / "pyproject.toml"
        path.write_text("[project]\nname = 'acme-lib'\n", encoding="utf-8")
        assert declared_name(path) == "acme-lib"

    def test_a_manifest_with_no_name_is_not_a_problem(self, tmp_path):
        path = tmp_path / "package.json"
        path.write_text('{"dependencies":{}}', encoding="utf-8")
        assert declared_name(path) is None

    def test_an_internal_package_is_not_looked_up(self, tmp_path, monkeypatch):
        looked_up = []

        def fake_fetch(name, ecosystem):
            looked_up.append(name)
            return PackageFacts(name=name, ecosystem=ecosystem, exists=False)

        monkeypatch.setattr("ghostpkg.scanner.fetch", fake_fetch)
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "root", "dependencies": {"@acme/font": "15.0.0"}}),
            encoding="utf-8",
        )
        member = tmp_path / "packages" / "font"
        member.mkdir(parents=True)
        (member / "package.json").write_text(
            json.dumps({"name": "@acme/font", "version": "15.0.0"}), encoding="utf-8"
        )
        assert main(["scan", str(tmp_path), "--no-cache"]) == 3
        assert looked_up == []

    def test_a_genuine_absence_is_still_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "ghostpkg.scanner.fetch",
            lambda name, ecosystem: PackageFacts(name=name, ecosystem=ecosystem, exists=False),
        )
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "root", "dependencies": {"ghost-991-nope": "^1"}}),
            encoding="utf-8",
        )
        assert main(["scan", str(tmp_path), "--no-cache"]) == 1
