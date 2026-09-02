"""Searching a directory for the files worth scanning.

`ghostpkg scan .` used to answer "that is a directory, pass a manifest file" and
exit 2, so using the tool on a project meant knowing and naming every dependency
file by hand -- dozens of them in a monorepo.
"""

from __future__ import annotations

import pytest

from ghostpkg.cli import main
from ghostpkg.discover import discover


def names(paths, root):
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in paths)


def write(root, relative, text="{}"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestWhatIsFound:
    def test_the_common_manifests(self, tmp_path):
        for name in ("package.json", "pyproject.toml", "requirements.txt"):
            write(tmp_path, name)
        assert names(discover(tmp_path), tmp_path) == [
            "package.json",
            "pyproject.toml",
            "requirements.txt",
        ]

    @pytest.mark.parametrize(
        "name",
        ["requirements-dev.txt", "dev-requirements.txt", "constraints.txt", "reqs.in"],
    )
    def test_requirements_are_matched_by_shape(self, tmp_path, name):
        write(tmp_path, name)
        assert names(discover(tmp_path), tmp_path) == [name]

    @pytest.mark.parametrize("name", ["AGENTS.md", "CLAUDE.md", ".cursorrules"])
    def test_agent_instruction_files(self, tmp_path, name):
        """A model writes `pip install foo-bar` into one of these and a person
        runs it, so the install happens before the name reaches a manifest."""
        write(tmp_path, name, "pip install foo\n")
        assert names(discover(tmp_path), tmp_path) == [name]

    def test_a_readme_only_at_the_root(self, tmp_path):
        """One file at the top is worth reading; a large repository has many
        further down, and they are documentation rather than instructions."""
        write(tmp_path, "README.md", "pip install foo\n")
        write(tmp_path, "docs/README.md", "pip install bar\n")
        assert names(discover(tmp_path), tmp_path) == ["README.md"]

    def test_unrelated_files_are_left_alone(self, tmp_path):
        for name in ("NOTICE.txt", "CHANGELOG.md", "setup.cfg", "Makefile", "notes.md"):
            write(tmp_path, name, "text\n")
        assert discover(tmp_path) == []


class TestWhatIsSkipped:
    @pytest.mark.parametrize(
        "directory", ["node_modules", ".git", ".venv", "venv", "dist", "build", "vendor", "__pycache__"]
    )
    def test_vendored_and_generated_trees(self, tmp_path, directory):
        """node_modules alone holds a package.json per installed package, so
        walking it turns one scan into thousands of pointless lookups."""
        write(tmp_path, f"{directory}/thing/package.json")
        write(tmp_path, "package.json")
        assert names(discover(tmp_path), tmp_path) == ["package.json"]

    def test_the_walk_has_a_depth_limit(self, tmp_path):
        write(tmp_path, "a/b/c/d/e/f/g/h/package.json")
        assert discover(tmp_path) == []

    def test_an_unreadable_directory_does_not_stop_the_search(self, tmp_path, monkeypatch):
        write(tmp_path, "package.json")
        write(tmp_path, "sub/pyproject.toml")

        real = type(tmp_path).iterdir

        def sometimes_denied(self):
            if self.name == "sub":
                raise PermissionError("denied")
            return real(self)

        monkeypatch.setattr(type(tmp_path), "iterdir", sometimes_denied)
        assert names(discover(tmp_path), tmp_path) == ["package.json"]


class TestALockfileSupersedesItsManifest:
    """A lockfile is the manifest resolved: it names everything the manifest
    does plus the transitive dependencies. Reading both prints most packages
    twice and checks nothing extra."""

    @pytest.mark.parametrize(
        "lockfile", ["package-lock.json", "yarn.lock", "pnpm-lock.yaml"]
    )
    def test_npm_lockfiles_supersede_package_json(self, tmp_path, lockfile):
        write(tmp_path, "package.json")
        write(tmp_path, lockfile)
        assert names(discover(tmp_path), tmp_path) == [lockfile]

    @pytest.mark.parametrize("lockfile", ["poetry.lock", "uv.lock"])
    def test_python_lockfiles_supersede_pyproject(self, tmp_path, lockfile):
        write(tmp_path, "pyproject.toml")
        write(tmp_path, lockfile)
        assert names(discover(tmp_path), tmp_path) == [lockfile]

    def test_only_within_the_same_directory(self, tmp_path):
        """A lockfile at the root says nothing about a workspace member's own
        manifest, which may declare dependencies of its own."""
        write(tmp_path, "pnpm-lock.yaml")
        write(tmp_path, "package.json")
        write(tmp_path, "packages/ui/package.json")
        assert names(discover(tmp_path), tmp_path) == [
            "packages/ui/package.json",
            "pnpm-lock.yaml",
        ]

    def test_requirements_are_never_superseded(self, tmp_path):
        write(tmp_path, "uv.lock")
        write(tmp_path, "pyproject.toml")
        write(tmp_path, "requirements.txt")
        assert names(discover(tmp_path), tmp_path) == ["requirements.txt", "uv.lock"]


class TestTheCommand:
    def test_an_empty_directory_is_not_a_pass(self, tmp_path):
        """Exit 0 would read as "checked, all clean" in CI."""
        assert main(["scan", str(tmp_path)]) == 3

    def test_a_missing_path_is_still_an_error(self, tmp_path):
        assert main(["scan", str(tmp_path / "nope")]) == 2

    def test_a_named_file_that_cannot_be_read_is_an_error(self, tmp_path):
        path = write(tmp_path, "NOTICE.txt", "Install ghostpkg today.\n")
        assert main(["scan", str(path)]) == 2

    def test_a_discovered_file_that_cannot_be_read_is_not(self, tmp_path, monkeypatch):
        """Refusing to scan a whole project because one file in it is malformed
        would make the directory form unusable."""
        write(tmp_path, "package.json", "{ this is not json")
        write(tmp_path, "requirements.txt", "\n")
        assert main(["scan", str(tmp_path), "--no-cache"]) == 3
