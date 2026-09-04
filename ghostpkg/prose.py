"""Install commands written in prose.

The hallucination arrives before the manifest does. A model writes
`pip install foo-bar` into a README, an `AGENTS.md`, or a Cursor rule; a person
copies the line and runs it; the install has already happened by the time that
name reaches `requirements.txt`. Scanning only manifests means arriving after
the fact.

Extraction is deliberately narrow. A prose file is full of words that look like
package names, so this only ever reads the arguments of a recognised install
command -- never bare identifiers, never imports, never code fences in general.
Everything ambiguous is dropped rather than guessed at, because a false positive
on a README is exactly the noise that gets a tool switched off.
"""

from __future__ import annotations

import re

from .manifests import Requirement, strip_bom

#: Each installer, with the ecosystem it installs into and the sub-command
#: that means "install these names".
INSTALLERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("pip", "pypi", ("install",)),
    ("pip3", "pypi", ("install",)),
    ("uv", "pypi", ("add",)),
    ("poetry", "pypi", ("add",)),
    ("pipx", "pypi", ("install",)),
    ("conda", "pypi", ("install",)),
    ("npm", "npm", ("install", "i", "add")),
    ("pnpm", "npm", ("install", "i", "add")),
    ("yarn", "npm", ("add",)),
    ("bun", "npm", ("add", "install")),
)

#: `npx pkg` and `uvx pkg` run a package without installing it first, which
#: fetches it just the same.
RUNNERS = {"npx": "npm", "bunx": "npm", "uvx": "pypi"}

#: Flags that take a value, so the value must not be read as a package name.
FLAGS_WITH_VALUES = {
    "-r", "--requirement", "-c", "--constraint", "-i", "--index-url",
    "--extra-index-url", "--find-links", "-f", "--target", "-t",
    "--prefix", "--root", "--python", "--registry", "--save-exact",
}

PROSE_SUFFIXES = (".md", ".mdx", ".markdown", ".mdc", ".rst", ".txt")
#: Kept in step with `discover.AGENT_FILES` -- a file the search offers up
#: and the parser then refuses is dropped in silence, which is how
#: `.windsurfrules` was found and never scanned.
PROSE_NAMES = (
    "agents.md", "claude.md", "readme", "contributing.md",
    ".cursorrules", ".windsurfrules",
)

# A shell prompt, a markdown bullet or a blockquote in front of the command.
LEAD = re.compile(r"^[\s>*\-+]*(?:\$|#|»|❯|PS[^>]*>)?\s*")

NAME = re.compile(r"^([A-Za-z0-9@][A-Za-z0-9._/@-]*)")


def looks_like_prose(filename: str) -> bool:
    lowered = filename.lower()
    if lowered in PROSE_NAMES or lowered.startswith("readme"):
        return True
    return lowered.endswith(PROSE_SUFFIXES[:-1])  # .txt handled by the caller


#: Ends a sentence, which in prose also ends the command: a README writes
#: "pip install httpx. The command line client is optional", and everything
#: after the full stop is English, not arguments.
SENTENCE_END = ".,;:!?)"


def _clean(token: str) -> "tuple[str | None, bool]":
    """(package name, whether the command ended here).

    Returning the second value matters more than it looks. Measured against ten
    real READMEs, letting a command run past the end of its sentence produced a
    **25% false-positive rate** -- `The`, `command`, `line`, `client`, `is` all
    came back as package names from one httpx line.
    """
    token = token.strip().strip("`\"'")
    stop = False
    while token and token[-1] in SENTENCE_END:
        token = token[:-1]
        stop = True
    if not token or token.startswith("-"):
        return None, stop
    # A path, a URL, an archive or a direct reference names its own source.
    if "://" in token or token.startswith((".", "/", "~")) or "@" in token[1:]:
        return None, stop
    if token.endswith((".whl", ".tar.gz", ".tgz", ".zip", ".txt", ".toml", ".json")):
        return None, stop
    # `fastapi[standard]` names fastapi; the extras are not part of it.
    token = re.sub(r"\[[^\]]*\]$", "", token)
    if not token:
        return None, stop
    match = NAME.match(token)
    if not match or match.group(1) != token:
        return None, stop
    name = match.group(1)
    # `npm install` with no argument, or a lone dot, installs the local project.
    return (name if name not in (".", "..") else None), stop


def _split_commands(line: str) -> list[str]:
    """One shell line can hold several commands."""
    return [part for part in re.split(r"&&|\|\||[;|]", line) if part.strip()]


#: A backtick code span. Prose puts commands inside one rather than on a line
#: of their own -- "Install using `pip install -U pydantic`" in pydantic's
#: README, "can be installed by running `pip install black`" in Black's. Three
#: of fourteen popular project READMEs write it only this way, and reading
#: whole lines found nothing at all in them.
CODE_SPAN = re.compile(r"`([^`\n]+)`")


def _candidates(line: str) -> list[str]:
    """The line itself, plus whatever sits inside its code spans.

    A span is explicit markup saying "this is a command", so reading one adds
    no guesswork: the same installer rules still have to match, and a span that
    is not a command yields nothing.
    """
    spans = CODE_SPAN.findall(line)
    return [line] + spans if spans else [line]


def extract(text: str, source: str | None = None) -> list[Requirement]:
    """Package names from install commands written anywhere in `text`."""
    found: list[Requirement] = []
    seen: set[tuple[str, str]] = set()

    for number, raw in enumerate(strip_bom(text).splitlines(), 1):
        commands = [
            part
            for candidate in _candidates(raw)
            for part in _split_commands(candidate)
        ]
        for command in commands:
            stripped = LEAD.sub("", command).strip().strip("`")
            if not stripped:
                continue
            # Strip leading VAR=value assignments.
            words = stripped.split()
            while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
                words.pop(0)
            if not words:
                continue

            tool = words[0].rsplit("/", 1)[-1]
            rest = words[1:]

            only_first = False
            if tool in RUNNERS:
                ecosystem = RUNNERS[tool]
                # `npx pkg arg arg` runs one package; the rest are its
                # arguments, and `my-app` is not something to look up.
                only_first = True
            else:
                # `python -m pip install x` and `py -3 -m pip install x`.
                if tool.startswith(("python", "py")) and "-m" in rest:
                    index = rest.index("-m")
                    if index + 1 < len(rest) and rest[index + 1] in ("pip", "uv"):
                        tool = rest[index + 1]
                        rest = rest[index + 2 :]
                entry = next((e for e in INSTALLERS if e[0] == tool), None)
                if entry is None:
                    continue
                _, ecosystem, subcommands = entry
                # `uv pip install x` -- step over the inner tool.
                if rest and rest[0] == "pip" and tool == "uv":
                    rest = rest[1:]
                    subcommands = ("install",)
                if not rest or rest[0] not in subcommands:
                    continue
                rest = rest[1:]

            skip_next = False
            for token in rest:
                if skip_next:
                    skip_next = False
                    continue
                if token in FLAGS_WITH_VALUES:
                    skip_next = True
                    continue
                if token.startswith("-"):
                    # An ordinary flag like --upgrade. Not a name, but not the
                    # end of the command either.
                    continue
                name, ends_here = _clean(token)
                if name is None:
                    # Prose has begun. Anything further on this line is English.
                    break
                if (name, ecosystem) not in seen:
                    seen.add((name, ecosystem))
                    found.append(
                        Requirement(
                            name=name,
                            line=number,
                            source=source,
                            ecosystem=ecosystem,
                        )
                    )
                if only_first or ends_here:
                    break
    return found
