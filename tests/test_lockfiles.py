"""Lockfile parsing.

Lockfiles matter more than manifests for this tool: CI installs from the
lockfile, so it holds the names that actually get fetched -- including
transitive ones a manifest never mentions.
"""

from __future__ import annotations

import json

import pytest

from ghostpkg.manifests import load_manifest, parse_package_lock, parse_toml_lock


def pairs(requirements):
    return [(r.name, r.specifier) for r in requirements]


V3 = json.dumps(
    {
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "myapp", "version": "1.0.0"},
            "node_modules/express": {"version": "4.18.2"},
            "node_modules/@types/node": {"version": "20.1.0"},
            "node_modules/a/node_modules/ms": {"version": "2.1.3"},
            "packages/ui": {"link": True},
        },
    }
)

V1 = json.dumps(
    {
        "lockfileVersion": 1,
        "dependencies": {
            "express": {
                "version": "4.18.2",
                "dependencies": {"ms": {"version": "2.1.3"}},
            }
        },
    }
)

TOML_LOCK = """
version = 1

[[package]]
name = "requests"
version = "2.31.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "urllib3"
version = "2.0.7"

[package.dependencies]
certifi = "*"

[metadata]
lock-version = "2.0"
"""


class TestPackageLock:
    def test_v3_packages_layout(self):
        assert pairs(parse_package_lock(V3)) == [
            ("express", "4.18.2"),
            ("@types/node", "20.1.0"),
            ("ms", "2.1.3"),
        ]

    def test_the_root_entry_is_not_a_dependency(self):
        """The `""` key is the project itself."""
        assert "myapp" not in [r.name for r in parse_package_lock(V3)]

    def test_scoped_names_survive_the_path(self):
        assert "@types/node" in [r.name for r in parse_package_lock(V3)]

    def test_workspace_links_are_skipped(self):
        """A `link` entry is a symlink to a local workspace, not a package."""
        assert "packages/ui" not in [r.name for r in parse_package_lock(V3)]

    def test_v1_nested_layout(self):
        assert pairs(parse_package_lock(V1)) == [("express", "4.18.2"), ("ms", "2.1.3")]

    def test_malformed_json_raises(self):
        with pytest.raises(ValueError):
            parse_package_lock("[]")


class TestTomlLock:
    """poetry.lock and uv.lock share the `[[package]]` shape, which is regular
    enough to read without a TOML parser -- tomllib is 3.11+ and 3.9 is
    supported."""

    def test_names_and_versions(self):
        assert pairs(parse_toml_lock(TOML_LOCK)) == [
            ("requests", "2.31.0"),
            ("urllib3", "2.0.7"),
        ]

    def test_a_nested_table_does_not_leak_names(self):
        """`[package.dependencies]` lists constraints, not packages to check."""
        assert "certifi" not in [r.name for r in parse_toml_lock(TOML_LOCK)]

    def test_metadata_is_not_a_package(self):
        assert "lock-version" not in [r.name for r in parse_toml_lock(TOML_LOCK)]

    def test_an_empty_lock_is_empty(self):
        assert parse_toml_lock("version = 1\n") == []


class TestDispatch:
    @pytest.mark.parametrize(
        "filename,content,ecosystem,expected",
        [
            ("package-lock.json", V3, "npm", "express"),
            ("poetry.lock", TOML_LOCK, "pypi", "requests"),
            ("uv.lock", TOML_LOCK, "pypi", "requests"),
        ],
    )
    def test_lockfiles_are_recognised(
        self, tmp_path, filename, content, ecosystem, expected
    ):
        path = tmp_path / filename
        path.write_text(content, encoding="utf-8")
        found, found_ecosystem = load_manifest(path)
        assert found_ecosystem == ecosystem
        assert expected in [r.name for r in found]

    def test_pinned_lockfile_versions_are_checked(self, tmp_path):
        """A lockfile pins exact versions, so every one of them is checkable."""
        path = tmp_path / "package-lock.json"
        path.write_text(V3, encoding="utf-8")
        found, _ = load_manifest(path)
        assert all(r.specifier for r in found)
