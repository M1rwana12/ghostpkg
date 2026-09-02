"""The shipped GitHub Action and pre-commit hook.

These are distributed artefacts. They are not Python, so nothing else in the
suite would notice them breaking, and the action in particular is a place where
a mistake is a vulnerability rather than a bug: `${{ }}` is substituted into the
*text* of a step before bash parses it, so an input carrying a quote or a
semicolon becomes part of the command. That is GitHub Actions template
injection, and it shipped in 0.18.0.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
ACTION = ROOT / "action.yml"
HOOKS = ROOT / ".pre-commit-hooks.yaml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def steps():
    """The action's steps, so a `run` body is read the way YAML sees it.

    The first version of this test matched `run: |` with a regular expression.
    It was greedy and swallowed the `env:` blocks of the following steps --
    which contain the interpolation on purpose -- and so reported the fixed
    file as still vulnerable.
    """
    return yaml.safe_load(read(ACTION))["runs"]["steps"]


def hook():
    return yaml.safe_load(read(HOOKS))[0]


class TestNoTemplateInjection:
    def test_no_interpolation_inside_any_run_block(self):
        """Every input must arrive through `env:` instead, where it is data the
        shell reads rather than script text it executes."""
        offenders = [
            step.get("name") for step in steps() if "${{" in (step.get("run") or "")
        ]
        assert offenders == []

    @pytest.mark.parametrize(
        "name, variable",
        [
            ("paths", "PATHS"),
            ("strict", "STRICT"),
            ("deep", "DEEP"),
            ("fail-on-error", "FAIL_ON_ERROR"),
            ("version", "SPEC"),
        ],
    )
    def test_each_input_reaches_the_shell_through_env(self, name, variable):
        environments = [step.get("env") or {} for step in steps()]
        assert any(
            env.get(variable) == "${{ inputs.%s }}" % name for env in environments
        ), f"{name} is not passed as {variable}"

    @pytest.mark.parametrize(
        "variable", ["PATHS", "STRICT", "DEEP", "SPEC", "FAIL_ON_ERROR"]
    )
    def test_every_expansion_is_quoted(self, variable):
        """An unquoted expansion is word-split and glob-expanded by the shell.
        Smaller than injection, but still not what was written."""
        pattern = re.compile(r"\$" + variable + r"\b")
        for step in steps():
            for line in (step.get("run") or "").splitlines():
                for match in pattern.finditer(line):
                    # Inside quotes, not necessarily as a bare "$VAR":
                    # "ghostpkg==$SPEC" is quoted too, and an earlier version
                    # of this assertion called that a failure.
                    assert line[: match.start()].count('"') % 2 == 1, line.strip()

    def test_a_hostile_paths_input_stays_an_argument(self):
        """The payload should end up as ordinary words handed to ghostpkg, not
        as a command. Run against the real splitting logic from the step."""
        bash = pytest.importorskip("subprocess")
        step = next(s for s in steps() if s.get("id") == "scan")
        splitting = "\n".join(
            line
            for line in step["run"].splitlines()
            if not line.strip().startswith(("ghostpkg", "code=", "set ", "echo "))
            and "GITHUB_OUTPUT" not in line
        )
        script = (
            'PATHS=$1; STRICT=false; DEEP=false\n'
            + splitting.split("case \"$code\"")[0]
            + '\nprintf "%s\\n" "${#paths[@]}"\n'
        )
        payload = 'requirements.txt"; touch /tmp/ghostpkg-pwned; echo "'
        result = bash.run(
            ["bash", "-c", script, "_", payload], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert not Path("/tmp/ghostpkg-pwned").exists()


class TestTheActionIsWellFormed:
    def test_it_parses(self):
        assert yaml.safe_load(read(ACTION))["runs"]["using"] == "composite"

    def test_every_declared_input_is_used(self):
        text = read(ACTION)
        for name in yaml.safe_load(text)["inputs"]:
            assert f"inputs.{name}" in text, f"{name} is declared and never read"

    def test_every_step_that_runs_a_script_names_a_shell(self):
        """A composite action fails to load without it, and the error does not
        say which step."""
        for step in steps():
            if "run" in step:
                assert step.get("shell"), step.get("name")


class TestTheHookIsWellFormed:
    def test_it_declares_one_hook(self):
        assert [h["id"] for h in yaml.safe_load(read(HOOKS))] == ["ghostpkg"]

    @pytest.mark.parametrize(
        "path",
        [
            "requirements.txt", "requirements-dev.txt", "reqs/requirements.in",
            "constraints.txt", "pyproject.toml", "package.json", "pnpm-lock.yaml",
            "yarn.lock", "uv.lock", "poetry.lock", "AGENTS.md", "CLAUDE.md",
            ".cursorrules", "packages/ui/package.json",
        ],
    )
    def test_dependency_files_are_matched(self, path):
        spec = hook()
        assert re.search(spec["files"], path)
        assert not re.search(spec["exclude"], path)

    @pytest.mark.parametrize(
        "path",
        [
            "node_modules/evil/package.json", ".venv/lib/requirements.txt",
            "vendor/x/package.json", "dist/package.json",
        ],
    )
    def test_vendored_trees_are_excluded(self, path):
        """node_modules alone would hand the hook a package.json per installed
        package on any commit that touches one."""
        assert re.search(hook()["exclude"], path)

    @pytest.mark.parametrize(
        "path", ["README.md", "CHANGELOG.md", "setup.cfg", "src/main.py", "notes.txt"]
    )
    def test_unrelated_files_are_not_matched(self, path):
        assert not re.search(hook()["files"], path)
