"""Manifest parsing.

Each format is recognised explicitly. Guessing is what made an earlier version
read `pyproject.toml` with the requirements.txt parser and report TOML keys as
package names -- `build-backend` came back "does not exist", `version` came back
"ok" because a package of that name happens to exist. A security tool that
confidently reports nonsense is worse than one that refuses to run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REQUIREMENT_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

SUPPORTED = "requirements*.txt, pyproject.toml, package.json"


class UnsupportedManifest(ValueError):
    """The file is not a manifest ghostpkg knows how to read."""


def parse_requirements(text: str) -> list[str]:
    names = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-", "git+", "http")):
            continue
        match = REQUIREMENT_LINE.match(line)
        if match:
            names.append(match.group(1))
    return names


def parse_package_json(text: str) -> list[str]:
    data = json.loads(text)
    names: list[str] = []
    for key in ("dependencies", "devDependencies", "optionalDependencies"):
        names.extend((data.get(key) or {}).keys())
    return names


def _requirement_name(spec: str) -> str | None:
    """Take the project name off a PEP 508 requirement string."""
    spec = spec.strip()
    if not spec or spec.startswith(("#", "-")):
        return None
    match = REQUIREMENT_LINE.match(spec)
    return match.group(1) if match else None


def _toml_arrays(text: str, table: str, key: str) -> list[str]:
    """Read a string array out of one TOML table without a TOML parser.

    Only the shapes that appear in dependency declarations are handled:
    `key = ["a", "b"]` on one line, or spread across several. This exists
    because tomllib is 3.11+ and ghostpkg supports 3.9 with no dependencies.
    """
    lines = text.splitlines()
    current = ""
    collected: list[str] = []
    depth = 0
    buffer = ""

    for raw in lines:
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
            after = stripped[len(key):].lstrip()
            if not after.startswith("="):
                continue
            buffer = after[1:].strip()
            if not buffer.startswith("["):
                continue
            depth = 1
            buffer = buffer[1:]
        else:
            buffer += " " + line.split("#", 1)[0].strip()

        if depth:
            if "]" in buffer:
                buffer = buffer[: buffer.index("]")]
                depth = 0
                collected.extend(re.findall(r'["\']([^"\']+)["\']', buffer))
                buffer = ""
    return collected


def _toml_table_keys(text: str, table: str) -> list[str]:
    """Read the keys of one TOML table (`name = value` lines)."""
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


def parse_pyproject(text: str) -> list[str]:
    """Dependencies from PEP 621 and from Poetry.

    Uses tomllib where it exists (3.11+) and a narrow fallback parser below
    that, because adding a TOML dependency would contradict the whole point
    of this tool.
    """
    try:
        import tomllib  # noqa: PLC0415
    except ImportError:
        tomllib = None  # type: ignore[assignment]

    names: list[str] = []

    if tomllib is not None:
        data = tomllib.loads(text)
        project = data.get("project") or {}
        for spec in project.get("dependencies") or []:
            name = _requirement_name(spec)
            if name:
                names.append(name)
        for group in (project.get("optional-dependencies") or {}).values():
            for spec in group:
                name = _requirement_name(spec)
                if name:
                    names.append(name)
        poetry = ((data.get("tool") or {}).get("poetry") or {})
        for section in ("dependencies", "dev-dependencies"):
            for name in (poetry.get(section) or {}):
                if name.lower() != "python":
                    names.append(name)
        for group in ((poetry.get("group") or {}).values()):
            for name in (group.get("dependencies") or {}):
                if name.lower() != "python":
                    names.append(name)
    else:
        for spec in _toml_arrays(text, "project", "dependencies"):
            name = _requirement_name(spec)
            if name:
                names.append(name)
        # optional-dependencies is a table of arrays; scan every key in it
        for raw in text.splitlines():
            stripped = raw.split("#", 1)[0].strip()
            if stripped.startswith("[project.optional-dependencies]"):
                break
        for key in _toml_table_keys(text, "project.optional-dependencies"):
            for spec in _toml_arrays(text, "project.optional-dependencies", key):
                name = _requirement_name(spec)
                if name:
                    names.append(name)
        for table in ("tool.poetry.dependencies", "tool.poetry.dev-dependencies"):
            for name in _toml_table_keys(text, table):
                if name.lower() != "python":
                    names.append(name)

    seen: set[str] = set()
    unique = []
    for name in names:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def load_manifest(path: Path) -> tuple[list[str], str]:
    """Return (package names, ecosystem). Raises UnsupportedManifest."""
    name = path.name.lower()
    text = path.read_text(encoding="utf-8")

    if name == "package.json":
        return parse_package_json(text), "npm"
    if name == "pyproject.toml":
        return parse_pyproject(text), "pypi"
    if name.endswith(".txt"):
        return parse_requirements(text), "pypi"

    raise UnsupportedManifest(
        f"don't know how to read {path.name!r}. Supported: {SUPPORTED}"
    )
