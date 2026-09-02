"""Install commands written in prose.

The hallucination arrives before the manifest does: a model writes
`pip install foo-bar` into a README or an AGENTS.md, a person copies the line
and runs it, and the install has already happened by the time that name reaches
requirements.txt.

A README is also full of words that look like package names, so the extractor
is deliberately narrow. The measurement that shaped it: letting a command run
past the end of its sentence produced a **25% false-positive rate** across ten
real READMEs -- `pip install httpx. The command line client is optional` gave
back `The`, `command`, `line`, `client` and `is`. Stopping where the prose
begins took that to **0% across thirteen**.
"""

from __future__ import annotations

import pytest

from ghostpkg.manifests import load_manifest
from ghostpkg.prose import extract, looks_like_prose


def names(text):
    return [r.name for r in extract(text)]


class TestInstallCommands:
    @pytest.mark.parametrize(
        "line,expected",
        [
            ("pip install requests", ["requests"]),
            ("pip install requests flask numpy", ["requests", "flask", "numpy"]),
            ("pip3 install --upgrade httpx", ["httpx"]),
            ("python -m pip install numpy", ["numpy"]),
            ("py -3 -m pip install numpy", ["numpy"]),
            ("uv add polars", ["polars"]),
            ("uv pip install polars", ["polars"]),
            ("pipx install ruff", ["ruff"]),
            ("poetry add httpx", ["httpx"]),
            ("$ pip install rich", ["rich"]),
            ("  - `pip install rich`", ["rich"]),
            ("FOO=bar pip install rich", ["rich"]),
        ],
    )
    def test_python_installers(self, line, expected):
        assert names(line) == expected

    @pytest.mark.parametrize(
        "line,expected",
        [
            ("npm install express", ["express"]),
            ("npm i express @types/node", ["express", "@types/node"]),
            ("yarn add lodash", ["lodash"]),
            ("pnpm add vite", ["vite"]),
            ("bun add hono", ["hono"]),
        ],
    )
    def test_node_installers(self, line, expected):
        assert names(line) == expected

    def test_the_ecosystem_comes_from_the_command(self):
        found = extract("pip install requests\nnpm install express\n")
        assert [(r.name, r.ecosystem) for r in found] == [
            ("requests", "pypi"),
            ("express", "npm"),
        ]

    def test_line_numbers_are_recorded(self):
        assert extract("# title\n\npip install rich\n")[0].line == 3


class TestRunners:
    """`npx pkg arg arg` fetches one package; the rest are its arguments."""

    def test_only_the_package_is_taken(self):
        assert names("npx create-react-app my-app") == ["create-react-app"]

    def test_flags_before_the_package_are_skipped(self):
        assert names("npx --yes cowsay hello world") == ["cowsay"]

    @pytest.mark.parametrize(
        "line,expected", [("bunx vite build", "vite"), ("uvx ruff check .", "ruff")]
    )
    def test_other_runners(self, line, expected):
        assert names(line) == [expected]


class TestProseIsNotAPackageList:
    """Everything here appears in real documentation."""

    @pytest.mark.parametrize(
        "line",
        [
            "install the package first",
            "npm run build",
            "yarn build",
            "pip is a package manager",
            "This will install requests for you",
            "see https://example.com/pip install fake",
            "pip install -r requirements.txt",
            "pip install -e .",
            "pip install .",
            "npm install",
            "conda activate myenv",
            "poetry run pytest",
            "pip install ./dist/pkg-1.0.whl",
            "Run pip install requests, then import it.",
        ],
    )
    def test_nothing_is_extracted(self, line):
        assert names(line) == []

    def test_a_command_stops_at_the_end_of_its_sentence(self):
        """The 25%-false-positive case, from httpx's README."""
        text = "pip install httpx. The command line client is an optional dependency."
        assert names(text) == ["httpx"]

    def test_a_trailing_comment_is_not_a_package(self):
        assert names("npm install express # the web framework") == ["express"]

    def test_an_unknown_argument_ends_the_command(self):
        assert names("pip install requests $(cat extra.txt)") == ["requests"]


class TestFileRecognition:
    @pytest.mark.parametrize(
        "filename", ["README.md", "readme.md", "AGENTS.md", "CLAUDE.md", "guide.mdx", ".cursorrules"]
    )
    def test_prose_files(self, filename):
        assert looks_like_prose(filename.lower())

    @pytest.mark.parametrize("filename", ["requirements.txt", "package.json", "pyproject.toml"])
    def test_manifests_are_not_prose(self, filename):
        assert not looks_like_prose(filename)

    def test_a_readme_is_scanned_through_load_manifest(self, tmp_path):
        path = tmp_path / "AGENTS.md"
        path.write_text(
            "Install with:\n\n```bash\npip install rich\nnpm install express\n```\n",
            encoding="utf-8",
        )
        found, _ = load_manifest(path)
        assert [(r.name, r.ecosystem) for r in found] == [
            ("rich", "pypi"),
            ("express", "npm"),
        ]


class TestACommandInsideASentence:
    """Three of fourteen popular READMEs write the install command only inside
    a backtick span, and reading whole lines found nothing at all in them.
    A span is explicit markup saying "this is a command", so reading one adds
    no guesswork -- the installer rules still have to match. Re-measured after
    the widening: 18 names across 22 READMEs, 0% that do not exist."""

    def names(self, text):
        return [r.name for r in extract(text)]

    def test_pydantic_readme_shape(self):
        text = "Install using `pip install -U pydantic` or `conda install pydantic -c conda-forge`."
        assert self.names(text) == ["pydantic"]

    def test_black_readme_shape(self):
        text = "_Black_ can be installed by running `pip install black`. It requires Python 3.10+."
        assert self.names(text) == ["black"]

    def test_fastapi_readme_shape(self):
        """Extras belong to the requirement, not to the name. Rejecting the
        whole token dropped the command with it."""
        text = 'When you install FastAPI with `uv add "fastapi[standard]"` it comes with extras.'
        assert self.names(text) == ["fastapi"]

    def test_a_span_that_is_not_a_command_yields_nothing(self):
        text = "Set `DEBUG=True` and call `app.run()` in `main.py` before you deploy."
        assert self.names(text) == []

    def test_prose_around_a_span_is_not_read_as_packages(self):
        """The failure this feature had at 25%: the sentence continuing past
        the command became package names."""
        text = "Run `pip install httpx` and the command line client is optional."
        assert self.names(text) == ["httpx"]

    def test_several_spans_on_one_line(self):
        text = "Use `pip install flask` or `npm install express` depending on the stack."
        assert set(self.names(text)) == {"flask", "express"}

    def test_a_fenced_block_still_works(self):
        text = "```bash\npip install requests\n```\n"
        assert self.names(text) == ["requests"]

    def test_the_line_number_is_the_line_of_the_span(self):
        text = "intro\n\nInstall with `pip install ghost-thing` today.\n"
        assert extract(text)[0].line == 3

    def test_extras_on_a_bare_line_too(self):
        assert self.names("pip install fastapi[standard]") == ["fastapi"]

    def test_a_span_with_a_backtick_pair_but_no_installer(self):
        assert self.names("The `requirements.txt` file lists them.") == []
