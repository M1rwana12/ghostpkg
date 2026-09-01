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
from dataclasses import dataclass
from pathlib import Path

# PEP 508 name: letter/digit at each end, dots/hyphens/underscores inside.
NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")

# Everything after the name and optional extras, up to an environment marker.
SPECIFIER = re.compile(r"^\s*(\[[^\]]*\])?\s*([^;#]*)")


@dataclass(frozen=True)
class Requirement:
    """One dependency as the manifest states it.

    `specifier` is kept because a hallucinated *version* is the same class of
    mistake as a hallucinated name -- a model will happily write
    `requests==99.99.99` -- and the registry response already lists every real
    version, so checking costs nothing extra.

    `line` is the 1-based line it came from, for pointing at it later.
    """

    name: str
    specifier: str | None = None
    line: int | None = None
    source: str | None = None


# `name @ https://...` is a direct reference: the source is stated explicitly
# and is not the public registry, so there is nothing for us to check.
DIRECT_REFERENCE = re.compile(r"^\s*[A-Za-z0-9][A-Za-z0-9._-]*\s*(\[[^\]]*\])?\s*@\s")

REQUIREMENTS_NAMES = ("requirements", "constraints", "dev-requirements", "test-requirements")

SUPPORTED = (
    "requirements*.txt, *.in, pyproject.toml, package.json, "
    "package-lock.json, poetry.lock, uv.lock"
)

MAX_INCLUDE_DEPTH = 10


class UnsupportedManifest(ValueError):
    """The file is not a manifest ghostpkg knows how to read."""


def _is_url(text: str) -> bool:
    return "://" in text


def parse_requirements(
    text: str,
    *,
    base: Path | None = None,
    source: str | None = None,
    _seen: set[Path] | None = None,
    _depth: int = 0,
) -> list[Requirement]:
    """Package names from a requirements file.

    Follows `-r` / `--requirement` includes when `base` is given, because a
    requirements file that pulls in `base.txt` was otherwise only half checked.
    """
    seen = _seen if _seen is not None else set()
    found: list[Requirement] = []

    for number, raw in enumerate(text.splitlines(), 1):
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
                found.extend(_read_include(include, base, seen, _depth))
            continue

        # A bare URL or local path, not a name we can look up.
        if _is_url(line) or line.startswith((".", "/", "~")):
            continue
        # `name @ url` states its own source; the registry has no say.
        if DIRECT_REFERENCE.match(line):
            continue

        match = NAME.match(line)
        if match:
            found.append(
                Requirement(
                    name=match.group(1),
                    specifier=_specifier(line[match.end() :]),
                    line=number,
                    source=source,
                )
            )

    return found


def _specifier(rest: str) -> str | None:
    """The version constraint following a name, without extras or markers."""
    match = SPECIFIER.match(rest)
    if not match:
        return None
    text = (match.group(2) or "").strip()
    return text or None


def _include_target(line: str) -> str | None:
    """The path from a `-r file` / `--requirement=file` / `-c file` line."""
    for flag in ("--requirement", "--constraint", "-r", "-c"):
        if line == flag or line.startswith(flag + "="):
            return line[len(flag) + 1 :].strip() or None
        if line.startswith(flag + " "):
            return line[len(flag) :].strip() or None
    return None


def _read_include(
    target: str, base: Path, seen: set[Path], depth: int
) -> list[Requirement]:
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
    return parse_requirements(
        text, base=path.parent, source=str(path), _seen=seen, _depth=depth + 1
    )


def parse_package_json(text: str, source: str | None = None) -> list[Requirement]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("package.json does not contain an object")
    found: list[Requirement] = []
    seen: set[str] = set()
    for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = data.get(key)
        if not isinstance(section, dict):
            continue
        for name, spec in section.items():
            name = str(name)
            if name in seen:
                continue
            seen.add(name)
            found.append(
                Requirement(name=name, specifier=str(spec) if spec else None, source=source)
            )
    return found


def parse_package_lock(text: str, source: str | None = None) -> list[Requirement]:
    """Dependencies from an npm `package-lock.json`, both layouts.

    Lockfiles matter more than manifests here: CI installs from the lockfile,
    so it is the list of names that actually get fetched -- including
    transitive ones a manifest never mentions.

    v2/v3 key `packages` by install path (`node_modules/foo`,
    `node_modules/a/node_modules/b`); v1 nests `dependencies`. The root entry
    has an empty key and is the project itself, not a dependency.
    """
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("package-lock.json does not contain an object")

    found: list[Requirement] = []
    seen: set[tuple[str, str | None]] = set()

    def add(name: str, version: str | None) -> None:
        key = (name, version)
        if not name or key in seen:
            return
        seen.add(key)
        found.append(Requirement(name=name, specifier=version, source=source))

    packages = data.get("packages")
    if isinstance(packages, dict):
        for path, entry in packages.items():
            if not path or not isinstance(entry, dict):
                continue  # "" is the project itself
            if entry.get("link"):
                continue  # a workspace symlink, not a registry package
            # The name is the path after the last node_modules segment, which
            # keeps scopes intact: node_modules/@types/node -> @types/node.
            marker = "node_modules/"
            name = path[path.rfind(marker) + len(marker) :] if marker in path else path
            add(str(entry.get("name") or name), entry.get("version"))

    def walk(section: dict) -> None:
        for name, entry in section.items():
            if not isinstance(entry, dict):
                continue
            add(str(name), entry.get("version"))
            nested = entry.get("dependencies")
            if isinstance(nested, dict):
                walk(nested)

    legacy = data.get("dependencies")
    if isinstance(legacy, dict):
        walk(legacy)

    return found


# `[[package]]` array-of-tables entries, as used by both poetry.lock and
# uv.lock. The shape is regular enough to read without a TOML parser, which
# matters because tomllib is 3.11+ and 3.9 is supported.
LOCK_ENTRY_KEY = re.compile(r'^(name|version)\s*=\s*["\']([^"\']+)["\']')


def parse_toml_lock(text: str, source: str | None = None) -> list[Requirement]:
    """Dependencies from a `poetry.lock` or `uv.lock`."""
    found: list[Requirement] = []
    seen: set[str] = set()
    in_package = False
    name: str | None = None
    version: str | None = None

    def flush() -> None:
        nonlocal name, version
        if name and name.lower() != "python" and name not in seen:
            seen.add(name)
            found.append(Requirement(name=name, specifier=version, source=source))
        name = version = None

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("[["):
            flush()
            in_package = line.startswith("[[package]]")
            continue
        if line.startswith("["):
            flush()
            in_package = False
            continue
        if not in_package:
            continue
        match = LOCK_ENTRY_KEY.match(line)
        if match:
            if match.group(1) == "name":
                # A second `name` means a new entry began without a header.
                if name is not None:
                    flush()
                name = match.group(2)
            else:
                version = match.group(2)
    flush()
    return found


def _requirement_name(spec: str) -> str | None:
    spec = spec.strip()
    if not spec or spec.startswith(("#", "-")):
        return None
    if _is_url(spec) or DIRECT_REFERENCE.match(spec):
        return None
    match = NAME.match(spec)
    return match.group(1) if match else None


def _as_requirement(spec: str, source: str | None = None) -> Requirement | None:
    name = _requirement_name(spec)
    if name is None:
        return None
    rest = spec.strip()[len(name) :]
    return Requirement(name=name, specifier=_specifier(rest), source=source)


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


def parse_pyproject(text: str, source: str | None = None) -> list[Requirement]:
    """Dependencies from PEP 621, PEP 735 and Poetry."""
    try:
        import tomllib  # noqa: PLC0415
    except ImportError:
        tomllib = None  # type: ignore[assignment]

    found: list[Requirement] = []

    def add(spec: str) -> None:
        requirement = _as_requirement(spec, source)
        if requirement is not None:
            found.append(requirement)

    def add_name(name: str) -> None:
        if name.lower() != "python":
            found.append(Requirement(name=name, source=source))

    if tomllib is not None:
        data = tomllib.loads(text)
        project = data.get("project") or {}
        for spec in project.get("dependencies") or []:
            add(str(spec))
        for group in (project.get("optional-dependencies") or {}).values():
            for spec in group:
                add(str(spec))
        # PEP 735, where uv and pip put dev dependencies.
        for group in (data.get("dependency-groups") or {}).values():
            if isinstance(group, list):
                for spec in group:
                    if isinstance(spec, str):
                        add(spec)
        poetry = (data.get("tool") or {}).get("poetry") or {}
        for section in ("dependencies", "dev-dependencies"):
            if isinstance(poetry.get(section), dict):
                for name in _poetry_names(poetry[section]):
                    add_name(name)
        for group in (poetry.get("group") or {}).values():
            if isinstance(group, dict) and isinstance(group.get("dependencies"), dict):
                for name in _poetry_names(group["dependencies"]):
                    add_name(name)
    else:
        for spec in _toml_arrays(text, "project", "dependencies"):
            add(spec)
        for table in ("project.optional-dependencies", "dependency-groups"):
            for key in _toml_table_keys(text, table):
                for spec in _toml_arrays(text, table, key):
                    add(spec)
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
            for name in _toml_table_keys(text, table):
                add_name(name)

    seen: set[str] = set()
    return [r for r in found if not (r.name in seen or seen.add(r.name))]


def _looks_like_requirements(name: str) -> bool:
    stem = name.rsplit(".", 1)[0]
    if name.endswith(".in"):
        return True
    if not name.endswith(".txt"):
        return False
    return any(word in stem for word in REQUIREMENTS_NAMES)


def load_manifest(path: Path) -> tuple[list[Requirement], str]:
    """Return (requirements, ecosystem). Raises UnsupportedManifest."""
    name = path.name.lower()
    source = str(path)

    if name == "package.json":
        return parse_package_json(path.read_text(encoding="utf-8"), source), "npm"
    if name == "package-lock.json":
        return parse_package_lock(path.read_text(encoding="utf-8"), source), "npm"
    if name in ("poetry.lock", "uv.lock"):
        return parse_toml_lock(path.read_text(encoding="utf-8"), source), "pypi"
    if name == "pyproject.toml":
        return parse_pyproject(path.read_text(encoding="utf-8"), source), "pypi"
    if _looks_like_requirements(name):
        text = path.read_text(encoding="utf-8")
        return parse_requirements(text, base=path.parent, source=source), "pypi"

    raise UnsupportedManifest(
        f"don't know how to read {path.name!r}. Supported: {SUPPORTED}"
    )
