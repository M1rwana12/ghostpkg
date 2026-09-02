"""A dependency that names its own source is not the registry's business.

The rule was already known: `manifests.py` has carried `DIRECT_REFERENCE` and a
regression suite for `name @ url` in a requirements file since the audit. It was
never applied to the other four parsers, and every one of these tests is a case
that was measured failing against real files:

* `pydantic`'s own `pyproject.toml` was blocked, because `pydantic-docs` is
  pointed at a git repository by `[tool.uv.sources]` and is not on PyPI.
* A monorepo `package.json` produced six false blocks out of nine, plus two
  silent wrong answers -- `link:../linked` was looked up as `linked`, which is
  an unrelated real package, and the alias `npm:lodash@^4` was checked under the
  key `aliased` rather than as `lodash`.
* In a `package-lock.json`, a git-resolved entry named `patched` matched an
  unrelated package and yielded a confident "version 1.0.0 does not exist".

False blocks are the failure mode this project treats as worse than a miss, so
these are the highest-value tests in the suite.
"""

from __future__ import annotations

import json

import pytest

from ghostpkg.manifests import (
    parse_package_json,
    parse_package_lock,
    parse_pyproject,
    parse_toml_lock,
)


def names(requirements):
    return [r.name for r in requirements]


class TestPackageJsonStatedSources:
    def scan(self, deps):
        return names(parse_package_json(json.dumps({"dependencies": deps})))

    @pytest.mark.parametrize(
        "spec",
        [
            "workspace:*",
            "workspace:^1.0.0",
            "catalog:default",
            "file:../internal-lib",
            "link:../linked",
            "portal:../thing",
            "git+https://github.com/acme/forked.git",
            "git://github.com/acme/forked.git",
            "https://example.com/x.tgz",
            "github:acme/private",
            "gitlab:acme/private",
            "acme/private-repo",
            "acme/private-repo#semver:^1",
            "../sibling",
            "./local",
        ],
    )
    def test_a_stated_source_is_not_looked_up(self, spec):
        assert self.scan({"internal": spec}) == []

    def test_ordinary_ranges_are_still_checked(self):
        deps = {"react": "^18.0.0", "left-pad": "*", "x": "latest", "y": ">=1 <2"}
        assert set(self.scan(deps)) == {"react", "left-pad", "x", "y"}

    def test_an_alias_is_checked_under_the_installed_name(self):
        """`npm:lodash@^4` installs lodash. Checking the key found an unrelated
        package called `aliased` and reported it fine."""
        assert self.scan({"aliased": "npm:lodash@^4"}) == ["lodash"]

    def test_a_scoped_alias_keeps_its_scope(self):
        assert self.scan({"ui": "npm:@scope/real@^2"}) == ["@scope/real"]

    def test_an_alias_drops_the_range(self):
        """The range belongs to the alias, not to the aliased name, so pinning
        the version check to it would be checking a claim nobody made."""
        found = parse_package_json(json.dumps({"dependencies": {"a": "npm:lodash@^4"}}))
        assert found[0].specifier is None

    def test_a_monorepo_is_not_a_wall_of_blocks(self):
        """The measured case: six of these nine were blocked."""
        found = self.scan({
            "react": "^18.0.0",
            "@acme/ui": "workspace:*",
            "@acme/utils": "workspace:^1.0.0",
            "internal-lib": "file:../internal-lib",
            "forked-thing": "git+https://github.com/acme/forked-thing.git",
            "tarball-dep": "https://example.com/x.tgz",
            "linked": "link:../linked",
            "aliased": "npm:lodash@^4",
            "gh-short": "acme/private-repo",
        })
        assert found == ["react", "lodash"]


class TestPackageLockStatedSources:
    def lock(self, packages):
        return names(parse_package_lock(json.dumps({"lockfileVersion": 3, "packages": packages})))

    def test_a_workspace_member_is_not_a_dependency(self):
        """Keyed by a plain path rather than under node_modules: it is part of
        this project, and looking it up blocked every package in a monorepo."""
        found = self.lock({
            "": {"name": "root"},
            "packages/ui": {"name": "@acme/ui", "version": "1.0.0"},
            "node_modules/react": {
                "version": "18.2.0",
                "resolved": "https://registry.npmjs.org/react/-/react-18.2.0.tgz",
            },
        })
        assert found == ["react"]

    def test_a_git_resolved_entry_is_skipped(self):
        """`patched` resolved from git matched an unrelated npm package and
        produced a confident, wrong statement about its versions."""
        found = self.lock({
            "node_modules/patched": {
                "version": "1.0.0",
                "resolved": "git+ssh://git@github.com/acme/patched.git#abc",
            },
        })
        assert found == []

    def test_a_public_mirror_is_still_checked(self):
        """Mirrors serve the public namespace, so those names are ours to ask
        about. Skipping them would be checking nothing and saying it was fine."""
        found = self.lock({
            "node_modules/react": {
                "version": "18.2.0",
                "resolved": "https://registry.yarnpkg.com/react/-/react-18.2.0.tgz",
            },
        })
        assert found == ["react"]

    def test_a_private_registry_is_left_alone(self):
        """A corporate registry proxies public names *and* hosts private ones
        under the same host, and nothing here says which is which."""
        found = self.lock({
            "node_modules/internal": {
                "version": "1.0.0",
                "resolved": "https://npm.corp.internal/internal/-/internal-1.0.0.tgz",
            },
        })
        assert found == []

    def test_a_link_entry_is_still_skipped(self):
        found = self.lock({"node_modules/@acme/ui": {"resolved": "packages/ui", "link": True}})
        assert found == []


class TestPoetryLockStatedSources:
    def test_a_package_source_subtable_drops_the_entry(self):
        text = (
            '[[package]]\nname = "requests"\nversion = "2.31.0"\n\n'
            '[[package]]\nname = "internal-thing"\nversion = "0.1.0"\n\n'
            '[package.source]\ntype = "git"\nurl = "https://github.com/acme/x.git"\n\n'
            '[[package]]\nname = "flask"\nversion = "3.0.0"\n'
        )
        assert names(parse_toml_lock(text)) == ["requests", "flask"]

    def test_other_package_subtables_do_not_drop_the_entry(self):
        """`[package.dependencies]` and `[package.extras]` are ordinary parts of
        an entry; only `[package.source]` says where it came from."""
        text = (
            '[[package]]\nname = "requests"\nversion = "2.31.0"\n\n'
            '[package.dependencies]\nurllib3 = ">=1.21.1"\n\n'
            '[package.extras]\nsocks = ["pysocks"]\n'
        )
        assert names(parse_toml_lock(text)) == ["requests"]

    def test_a_following_top_level_table_still_ends_the_entry(self):
        text = (
            '[[package]]\nname = "requests"\nversion = "2.31.0"\n\n'
            '[metadata]\nlock-version = "2.0"\n'
        )
        assert names(parse_toml_lock(text)) == ["requests"]


class TestUvLockStatedSources:
    def test_only_registry_sources_are_checked(self):
        text = (
            '[[package]]\nname = "httpx"\nversion = "0.27.0"\n'
            'source = { registry = "https://pypi.org/simple" }\n\n'
            '[[package]]\nname = "my-project"\nversion = "0.1.0"\n'
            'source = { editable = "." }\n\n'
            '[[package]]\nname = "patched-dep"\nversion = "1.0.0"\n'
            'source = { git = "https://github.com/acme/patched-dep" }\n'
        )
        assert names(parse_toml_lock(text)) == ["httpx"]

    @pytest.mark.parametrize(
        "source",
        ['{ editable = "." }', '{ directory = "libs/x" }', '{ virtual = "." }',
         '{ git = "https://github.com/a/b" }', '{ url = "https://x/y.whl" }'],
    )
    def test_every_non_registry_source_is_dropped(self, source):
        text = f'[[package]]\nname = "thing"\nversion = "1.0"\nsource = {source}\n'
        assert names(parse_toml_lock(text)) == []


class TestPyprojectStatedSources:
    def test_uv_sources_redirects_away_from_the_index(self):
        """The pydantic case, reduced: declared as a dependency, resolved from
        git, absent from PyPI -- and blocked."""
        text = (
            "[project]\nname = 'app'\ndependencies = ['requests', 'pydantic-docs']\n\n"
            "[tool.uv.sources]\n"
            "pydantic-docs = { git = 'https://github.com/pydantic/pydantic-docs' }\n"
        )
        assert names(parse_pyproject(text)) == ["requests"]

    def test_a_workspace_source_is_dropped_too(self):
        text = (
            "[project]\nname = 'app'\ndependencies = ['pydantic-core', 'requests']\n\n"
            "[tool.uv.sources]\npydantic-core = { workspace = true }\n"
        )
        assert names(parse_pyproject(text)) == ["requests"]

    def test_a_dependency_group_entry_is_covered(self):
        """`pydantic-docs` is declared in a PEP 735 group, not in `project`."""
        text = (
            "[project]\nname = 'app'\ndependencies = []\n\n"
            "[dependency-groups]\ndocs = ['mkdocs', 'pydantic-docs']\n\n"
            "[tool.uv.sources]\npydantic-docs = { git = 'https://x/y' }\n"
        )
        assert names(parse_pyproject(text)) == ["mkdocs"]

    @pytest.mark.parametrize("key", ["git", "path", "url"])
    def test_poetry_dependencies_with_a_stated_source(self, key):
        text = (
            "[tool.poetry.dependencies]\npython = '^3.9'\nrequests = '^2.31'\n"
            f"internal = {{ {key} = 'https://github.com/acme/internal' }}\n"
        )
        assert names(parse_pyproject(text)) == ["requests"]

    def test_ordinary_poetry_dependencies_are_kept(self):
        text = (
            "[tool.poetry.dependencies]\npython = '^3.9'\nrequests = '^2.31'\n"
            "flask = { version = '^3.0', optional = true }\n"
        )
        assert set(names(parse_pyproject(text))) == {"requests", "flask"}
