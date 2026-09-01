"""Manifest parsing.

Each format is recognised explicitly. Guessing is what made an earlier version
read `pyproject.toml` with the requirements.txt parser and report TOML keys as
package names -- `build-backend` came back "does not exist", `version` came back
"ok" because a package of that name exists. A security tool that confidently
reports nonsense is worse than one that refuses to run.

Two later bugs came from the same root cause, filtering by prefix instead of by
structure:

* Skipping lines that start with `http` was meant to drop bare URLs. It also
  dropped `httpx`, `httpcore` and `httplib2` -- silently, so the tool reported
  "all packages look fine" having never checked them.
* Treating every `.txt` file as a requirements file turned `README.txt` into
  the package list `['Install', 'Run', 'numpy']`, two of which do not exist.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# PEP 508 name: letter/digit at each end, dots/hyphens/underscores inside.
NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")

# `name @ https://...` is a direct reference: the source is stated explicitly
# and is not the public registry, so there is nothing for us to check.
DIRECT_REFERENCE = re.compile(r"^\s*[A-Za-z0-9][A-Za-z0-9._-]*\s*(\[[^\]]*\])?\s*@\s")

REQUIREMENTS_NAMES = ("requirements", "constraints", "dev-requirements", "test-requirements")

SUPPORTED = "requirements*.txt, *.in, pyproject.toml, package.json"

MAX_INCLUDE_DEPTH = 10


class UnsupportedManifest(ValueError):
    """The file is not a manifest ghostpkg knows how to read."""


def _is_url(text: str) -> bool:
    return "://" in text


def parse_requirements(
    text: str,
    *,
    base: Path | None = None,
    _seen: set[Path] | None = None,
    _depth: int = 0,
) -> list[str]:
    """Package names from a requirements file.

    Follows `-r` / `--requirement` includes when `base` is given, because a
    requirements file that pulls in `base.txt` was otherwise only half checked.
    """
    seen = _seen if _seen is not None else set()
    names: list[str] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        # Strip an inline comment, but only when it is preceded by whitespace --
        # a `#` can legitimately appear inside a URL fragment.
        if " #" in line:
            line = line.split(" #", 1)[0].strip()
        if not line:
            continue

        if line.startswith("-"):
            include = _include_target(line)
            if include and base is not None and _depth < MAX_INCLUDE_DEPTH:
                names.extend(_read_include(include, base, seen, _depth))
            continue

        # A bare URL or local path, not a name we can look up.
        if _is_url(line) or line.startswith((".", "/", "~")):
            continue
        # `name @ url` states its own source; the registry has no say.
        if DIRECT_REFERENCE.match(line):
            continue

        match = NAME.match(line)
        if match:
            names.append(match.group(1))

    return names


def _include_target(line: str) -> str | None:
    """The path from a `-r file` / `--requirement=file` / `-c file` line."""
    for flag in ("--requirement", "--constraint", "-r", "-c"):
        if line == flag or line.startswith(flag + "="):
            return line[len(flag) + 1 :].strip() or None
        if line.startswith(flag + " "):
            return line[len(flag) :].strip() or None
    return None


def _read_include(target: str, base: Path, seen: set[Path], depth: int) -> list[str]:
    if _is_url(target):
        return []
    try:
        path = (base / target).resolve()
    except (OSError, ValueError):
        return []
    if path in seen or not path.is_file():
        return []
    seen.add(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return parse_requirements(text, base=path.parent, _seen=seen, _depth=depth + 1)


def parse_package_json(text: str) -> list[str]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("package.json does not contain an object")
    names: list[str] = []
    for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            names.extend(str(name) for name in section)
    seen: set[str] = set()
    return [n for n in names if not (n in seen or seen.add(n))]


def _requirement_name(spec: str) -> str | None:
    spec = spec.strip()
    if not spec or spec.startswith(("#", "-")):
        return None
    if _is_url(spec) or DIRECT_REFERENCE.match(spec):
        return None
    match = NAME.match(spec)
    return match.group(1) if match else None


def _toml_arrays(text: str, table: str, key: str) -> list[str]:
    """Read a string array out of one TOML table without a TOML parser.

    Only the shapes dependency declarations use are handled. This exists
    because tomllib is 3.11+ and ghostpkg supports 3.9 with no dependencies.
    """
    current = ""
    collected: list[str] = []
    depth = 0
    buffer = ""

    for raw in text.splitlines():
        line = raw.strip()
        if depth == 0:
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip().strip('"')
                continue
            if current != table:
                continue
            stripped = line.split("#", 1)[0].strip()
            if not stripped.startswith(key):
                continue
            after = stripped[len(key) :].lstrip()
            if not after.startswith("="):
                continue
            buffer = after[1:].strip()
            if not buffer.startswith("["):
                continue
            depth = 1
            buffer = buffer[1:]
        else:
            buffer += " " + line.split("#", 1)[0].strip()

        if depth and "]" in buffer:
            buffer = buffer[: buffer.index("]")]
            depth = 0
            collected.extend(re.findall(r'["\']([^"\']+)["\']', buffer))
            buffer = ""
    return collected


def _toml_table_keys(text: str, table: str) -> list[str]:
    keys: list[str] = []
    current = ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().strip('"')
            continue
        if current != table or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip().strip('"').strip("'")
        if name:
            keys.append(name)
    return keys


def _poetry_names(section: dict) -> list[str]:
    # `python` is an interpreter constraint, not a package.
    return [name for name in section if name.lower() != "python"]


def parse_pyproject(text: str) -> list[str]:
    """Dependencies from PEP 621, PEP 735 and Poetry."""
    try:
        import tomllib  # noqa: PLC0415
    except ImportError:
        tomllib = None  # type: ignore[assignment]

    names: list[str] = []

    if tomllib is not None:
        data = tomllib.loads(text)
        project = data.get("project") or {}
        for spec in project.get("dependencies") or []:
            name = _requirement_name(str(spec))
            if name:
                names.append(name)
        for group in (project.get("optional-dependencies") or {}).values():
            for spec in group:
                name = _requirement_name(str(spec))
                if name:
                    names.append(name)
        # PEP 735, where uv and pip put dev dependencies.
        for group in (data.get("dependency-groups") or {}).values():
            if isinstance(group, list):
                for spec in group:
                    if isinstance(spec, str):
                        name = _requirement_name(spec)
                        if name:
                            names.append(name)
        poetry = (data.get("tool") or {}).get("poetry") or {}
        for section in ("dependencies", "dev-dependencies"):
            if isinstance(poetry.get(section), dict):
                names.extend(_poetry_names(poetry[section]))
        for group in (poetry.get("group") or {}).values():
            if isinstance(group, dict) and isinstance(group.get("dependencies"), dict):
                names.extend(_poetry_names(group["dependencies"]))
    else:
        for spec in _toml_arrays(text, "project", "dependencies"):
            name = _requirement_name(spec)
            if name:
                names.append(name)
        for table in ("project.optional-dependencies", "dependency-groups"):
            for key in _toml_table_keys(text, table):
                for spec in _toml_arrays(text, table, key):
                    name = _requirement_name(spec)
                    if name:
                        names.append(name)
        # Poetry groups live in their own tables; find them by scanning headers.
        poetry_tables = ["tool.poetry.dependencies", "tool.poetry.dev-dependencies"]
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if (
                line.startswith("[tool.poetry.group.")
                and line.endswith(".dependencies]")
            ):
                poetry_tables.append(line[1:-1])
        for table in poetry_tables:
            names.extend(
                name for name in _toml_table_keys(text, table) if name.lower() != "python"
            )

    seen: set[str] = set()
    return [n for n in names if not (n in seen or seen.add(n))]


def _looks_like_requirements(name: str) -> bool:
    stem = name.rsplit(".", 1)[0]
    if name.endswith(".in"):
        return True
    if not name.endswith(".txt"):
        return False
    return any(word in stem for word in REQUIREMENTS_NAMES)


def load_manifest(path: Path) -> tuple[list[str], str]:
    """Return (package names, ecosystem). Raises UnsupportedManifest."""
    name = path.name.lower()

    if name == "package.json":
        return parse_package_json(path.read_text(encoding="utf-8")), "npm"
    if name == "pyproject.toml":
        return parse_pyproject(path.read_text(encoding="utf-8")), "pypi"
    if _looks_like_requirements(name):
        text = path.read_text(encoding="utf-8")
        return parse_requirements(text, base=path.parent), "pypi"

    raise UnsupportedManifest(
        f"don't know how to read {path.name!r}. Supported: {SUPPORTED}"
    )
