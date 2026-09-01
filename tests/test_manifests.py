"""Tests for manifest parsing.

The regression these guard against: an earlier version treated every file that
was not package.json as a requirements.txt, so `scan pyproject.toml` reported
TOML keys as package names -- `build-backend` came back "does not exist on
pypi", and `version` came back "ok" because a package of that name exists.
"""

import json

import pytest

from ghostpkg.manifests import (
    UnsupportedManifest,
    load_manifest,
    parse_package_json,
    parse_pyproject,
    parse_requirements,
)

PYPROJECT = """
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "example"
version = "1.0.0"
description = "not a dependency"
requires-python = ">=3.9"
dependencies = [
    "requests>=2.31",
    "click",
    # a comment inside the array
    "rich~=13.0",
]

[project.optional-dependencies]
dev = ["pytest>=7", "mypy"]
docs = ["sphinx"]

[tool.ruff]
line-length = 88
"""

POETRY = """
[tool.poetry]
name = "example"
version = "1.0.0"

[tool.poetry.dependencies]
python = "^3.11"
requests = "^2.31"
httpx = { version = "^0.27", optional = true }

[tool.poetry.dev-dependencies]
pytest = "^8.0"
"""


class TestPyproject:
    def test_finds_pep621_dependencies(self):
        names = parse_pyproject(PYPROJECT)
        assert "requests" in names
        assert "click" in names
        assert "rich" in names

    def test_finds_optional_dependencies(self):
        names = parse_pyproject(PYPROJECT)
        assert "pytest" in names
        assert "mypy" in names
        assert "sphinx" in names

    @pytest.mark.parametrize(
        "key", ["build-backend", "name", "version", "description", "requires-python"]
    )
    def test_toml_keys_are_not_treated_as_packages(self, key):
        """The exact regression: TOML keys reported as package names."""
        assert key not in parse_pyproject(PYPROJECT)

    def test_build_system_requires_is_not_a_dependency(self):
        """`requires` under [build-system] is a build dep, not a project one."""
        assert "hatchling" not in parse_pyproject(PYPROJECT)

    def test_poetry_dependencies(self):
        names = parse_pyproject(POETRY)
        assert "requests" in names
        assert "httpx" in names
        assert "pytest" in names

    def test_poetry_python_constraint_is_skipped(self):
        """`python` is an interpreter constraint, not a package."""
        assert "python" not in parse_pyproject(POETRY)

    def test_no_duplicates(self):
        names = parse_pyproject(PYPROJECT)
        assert len(names) == len(set(names))


class TestFallbackParser:
    """Python 3.9 and 3.10 have no tomllib, so a narrow parser stands in.

    These run the fallback explicitly rather than trusting the interpreter
    version the suite happens to be executed on.
    """

    @pytest.fixture
    def no_tomllib(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "tomllib":
                raise ImportError("no tomllib")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

    def test_fallback_finds_dependencies(self, no_tomllib):
        names = parse_pyproject(PYPROJECT)
        assert "requests" in names
        assert "click" in names
        assert "rich" in names

    def test_fallback_finds_optional_dependencies(self, no_tomllib):
        names = parse_pyproject(PYPROJECT)
        assert "pytest" in names
        assert "sphinx" in names

    def test_fallback_ignores_toml_keys(self, no_tomllib):
        names = parse_pyproject(PYPROJECT)
        for key in ("build-backend", "name", "version", "requires-python"):
            assert key not in names

    def test_fallback_reads_poetry(self, no_tomllib):
        names = parse_pyproject(POETRY)
        assert "requests" in names
        assert "python" not in names


class TestRequirements:
    def test_basic(self):
        text = "requests==2.31.0\nflask>=3.0\n\n# comment\nnumpy\n"
        assert parse_requirements(text) == ["requests", "flask", "numpy"]

    def test_skips_flags_and_urls(self):
        text = "-r base.txt\n--index-url https://example.com\ngit+https://x/y\nrequests\n"
        assert parse_requirements(text) == ["requests"]


class TestPackageJson:
    def test_all_dependency_groups(self):
        text = json.dumps(
            {
                "dependencies": {"express": "^4"},
                "devDependencies": {"jest": "^29"},
                "optionalDependencies": {"fsevents": "^2"},
            }
        )
        assert set(parse_package_json(text)) == {"express", "jest", "fsevents"}


class TestDetection:
    def test_pyproject_is_recognised(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text(PYPROJECT, encoding="utf-8")
        names, ecosystem = load_manifest(p)
        assert ecosystem == "pypi"
        assert "requests" in names

    def test_package_json_is_npm(self, tmp_path):
        p = tmp_path / "package.json"
        p.write_text('{"dependencies": {"express": "^4"}}', encoding="utf-8")
        assert load_manifest(p) == (["express"], "npm")

    def test_requirements_variants(self, tmp_path):
        p = tmp_path / "requirements-dev.txt"
        p.write_text("pytest\n", encoding="utf-8")
        assert load_manifest(p) == (["pytest"], "pypi")

    def test_unknown_file_is_refused_not_guessed(self, tmp_path):
        """Refusing beats guessing: guessing is what produced the TOML bug."""
        p = tmp_path / "LICENSE"
        p.write_text("MIT License\n", encoding="utf-8")
        with pytest.raises(UnsupportedManifest):
            load_manifest(p)
