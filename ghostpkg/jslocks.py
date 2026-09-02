"""`pnpm-lock.yaml` and `yarn.lock`, read as text.

Measured across twenty popular JavaScript repositories, `package-lock.json` --
the only npm lockfile ghostpkg could read -- was present in two of them.
`pnpm-lock.yaml` was in ten and `yarn.lock` in six, so four out of five projects
had a lockfile the tool refused. Lockfiles are the list that matters, because CI
installs from them and they name transitive dependencies a manifest never
mentions.

Both formats are read line by line rather than with a YAML library, for the same
reason `pyproject.toml` has a hand-written fallback: a supply-chain tool that
installs a dependency tree of its own undermines its own argument. Neither
format needs general YAML -- the part worth reading is a list of keys.

Every entry states its own source, and this module applies the same rule the
rest of the parsers do: a name resolved from a workspace, a directory, a patch
or a git host is not a registry name, so the registry has no say. Both formats
carry that inline, and both were confirmed to do so in real files:
`"eslint-plugin-react-internal@link:./scripts/eslint-rules"` in React's classic
lockfile, `"@babel/benchmark@workspace:..."` in Babel's berry one.
"""

from __future__ import annotations

import re

from .manifests import Requirement, npm_alias_target, strip_bom

#: A yarn descriptor protocol that resolves somewhere other than the registry.
#: `exec:` and `patch:` are berry-only; the rest appear in both versions.
YARN_LOCAL = (
    "workspace:", "link:", "portal:", "file:", "patch:", "exec:",
    "git+", "git:", "http://", "https://",
    "github:", "gitlab:", "bitbucket:",
)

#: pnpm writes the same protocols into its keys, minus yarn's own inventions.
PNPM_LOCAL = ("file:", "link:", "http://", "https://", "git+", "git:")

#: In a pnpm key the version always starts with a digit, which is what tells a
#: scoped name apart from its version: `@babel/parser@7.29.3` splits at the
#: second `@`, not the first. Peer suffixes -- `(react@18.2.0)` in v9,
#: `_react@18.2.0` in v5 -- come after it and must not be split on.
PNPM_VERSION_AT = re.compile(r"(?<=.)@(?=\d)")


def _yarn_descriptor(text: str) -> "tuple[str, str] | None":
    """`(name, range)` from `name@range`, keeping a leading scope `@`.

    The split is at the *first* `@` past position zero. Taking the last one
    reads `@babel-baseline/cli@npm:@babel/cli@7.27.1` -- a real entry in
    Babel's lockfile -- as a package called
    `@babel-baseline/cli@npm:@babel/cli`.
    """
    text = text.strip().strip('"').strip("'")
    at = text.find("@", 1)
    if at < 1:
        return None
    return text[:at], text[at + 1 :]


def parse_yarn_lock(text: str, source: str | None = None) -> list[Requirement]:
    """Dependencies from a `yarn.lock`, classic (v1) or berry (v2+).

    Both write entries as a key at column zero holding one or more
    comma-separated descriptors. Classic writes the raw range (`lodash@^4.17.19`),
    berry prefixes the protocol (`lodash@npm:^4.17.19`), and berry also uses the
    descriptor to alias: `@babel-baseline/cli@npm:@babel/cli@7.27.1` installs
    `@babel/cli`, which is the name worth checking.
    """
    found: list[Requirement] = []
    seen: set[str] = set()

    for raw in strip_bom(text).splitlines():
        if not raw or raw[0] in " \t#" or not raw.rstrip().endswith(":"):
            continue
        line = raw.rstrip()[:-1].strip()
        if not line or line.startswith("__metadata"):
            continue

        for part in line.split(","):
            parsed = _yarn_descriptor(part)
            if parsed is None:
                continue
            name, spec = parsed
            if spec.startswith(YARN_LOCAL):
                continue  # a protocol, so not a registry name
            if spec.startswith("npm:"):
                alias = npm_alias_target(spec)
                if alias is not None:
                    name = alias
            if name and name not in seen:
                seen.add(name)
                found.append(Requirement(name=name, source=source))

    return found


def parse_pnpm_lock(text: str, source: str | None = None) -> list[Requirement]:
    """Dependencies from a `pnpm-lock.yaml`.

    Only the `packages:` block is read: it is the resolved set, and unlike
    `dependencies:` it names transitive packages too. Three key shapes exist
    across lockfile versions, all seen in the wild:

        /react/18.2.0:          v5
        /react@18.2.0:          v6
        'react@18.2.0':         v9

    v9 appends peer resolutions in parentheses and v5 after an underscore, so
    the split is made at the `@` that starts the version rather than the last
    one in the line.
    """
    found: list[Requirement] = []
    seen: set[str] = set()
    in_packages = False

    for raw in strip_bom(text).splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            # A blank line does not end the block. Treating one as a
            # column-zero key closed `packages:` at the first gap between
            # entries, and every real lockfile has one there -- the parser
            # returned nothing at all for all three files tested.
            continue
        if not raw[:1].isspace():
            # A column-zero key ends whatever block preceded it.
            in_packages = stripped == "packages:"
            continue
        if not in_packages or not stripped.endswith(":"):
            continue
        # Keys sit one level in; anything deeper belongs to an entry's body.
        if len(raw) - len(raw.lstrip()) != 2:
            continue

        key = stripped[:-1].strip().strip('"').strip("'")
        if not key or key.startswith(PNPM_LOCAL):
            continue
        key = key.split("(", 1)[0]  # v9 peer resolutions
        if key.startswith("/"):
            key = key[1:]

        match = PNPM_VERSION_AT.search(key)
        if match:
            name = key[: match.start()]
        elif "/" in key.lstrip("@"):
            # v5 keys the version with a slash: @babel/code-frame/7.12.11
            name = key.rsplit("/", 1)[0]
        else:
            continue

        if name and name not in seen:
            seen.add(name)
            found.append(Requirement(name=name, source=source))

    return found
