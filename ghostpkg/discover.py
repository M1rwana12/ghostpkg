"""Finding the files worth scanning in a directory.

`ghostpkg scan .` in a project root used to answer "that is a directory, pass a
manifest file" and exit 2, which meant knowing and listing every dependency file
by hand -- in a monorepo, dozens of them.

Two rules keep the result small enough to read:

* **Vendored trees are skipped.** `node_modules` alone holds a `package.json`
  for every installed package, so walking into it would turn one scan into
  thousands of lookups of things already on disk.
* **A lockfile supersedes the manifest beside it.** A lockfile is the manifest
  resolved, so it names everything the manifest does and the transitive
  dependencies too. Reading both prints most packages twice and checks nothing
  extra.
"""

from __future__ import annotations

from pathlib import Path

#: Directories that hold someone else's code, build output, or version-control
#: internals. Walking into these is slow and answers nothing about this project.
SKIP_DIRS = frozenset({
    ".bzr", ".git", ".hg", ".svn",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox", "__pycache__",
    ".venv", "venv", "env", "site-packages", ".eggs",
    "node_modules", "bower_components", "vendor",
    "dist", "build", "target", "out", ".next", ".nuxt", ".svelte-kit",
    ".terraform", ".gradle", ".idea", ".vscode",
})

#: Lockfiles, and the manifest each one makes redundant in the same directory.
LOCKS = {
    "package-lock.json": "package.json",
    "yarn.lock": "package.json",
    "pnpm-lock.yaml": "package.json",
    "poetry.lock": "pyproject.toml",
    "uv.lock": "pyproject.toml",
}

MANIFESTS = frozenset({"package.json", "pyproject.toml"})

#: Agent instruction files. A model writes `pip install foo-bar` into one of
#: these and a person runs it, so the install happens before the name ever
#: reaches a manifest. They are named exactly, unlike Markdown in general:
#: reading every `.md` in a repository would be mostly changelogs and docs.
AGENT_FILES = frozenset({"agents.md", "claude.md", ".cursorrules", ".windsurfrules"})

#: Requirements files are found by shape rather than by an exact name, since
#: projects spell them `requirements-dev.txt`, `dev-requirements.txt`, `reqs.in`.
REQUIREMENTS_WORDS = ("requirements", "constraints")

#: How far down to walk. Deep enough for `packages/*/src`-shaped monorepos,
#: shallow enough not to crawl a data directory that happens to sit in the tree.
MAX_DEPTH = 6


def _is_requirements(name: str) -> bool:
    if name.endswith(".in"):
        return True
    stem = name[:-4] if name.endswith(".txt") else ""
    return bool(stem) and any(word in stem for word in REQUIREMENTS_WORDS)


def discover(root: Path) -> list[Path]:
    """Every file under `root` worth scanning, in a stable order.

    The root's own `README` is included -- one file, and the place an install
    command is most likely to be copied from -- but READMEs further down are
    not, because a large repository has many and they are rarely instructions.
    """
    found: list[Path] = []

    def walk(directory: Path, depth: int) -> None:
        try:
            entries = sorted(directory.iterdir())
        except (OSError, PermissionError):
            return

        names = {entry.name for entry in entries if entry.is_file()}
        superseded = {
            LOCKS[name] for name in names if name in LOCKS
        }

        for entry in entries:
            if entry.is_dir():
                if depth < MAX_DEPTH and entry.name not in SKIP_DIRS and not entry.is_symlink():
                    walk(entry, depth + 1)
                continue
            if not entry.is_file():
                continue
            name = entry.name
            lowered = name.lower()
            if name in superseded:
                continue
            if name in LOCKS or name in MANIFESTS or lowered in AGENT_FILES:
                found.append(entry)
            elif _is_requirements(name):
                found.append(entry)
            elif depth == 0 and lowered.startswith("readme"):
                found.append(entry)

    walk(root, 0)
    return found
