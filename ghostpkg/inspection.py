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

# A long unbroken base64-looking run is worth flagging on its own.
BLOB = re.compile(r"['\"][A-Za-z0-9+/]{220,}={0,2}['\"]")


class InspectionError(RuntimeError):
    """The archive could not be retrieved or read."""


def scan_text(text: str, where: str) -> list[Signal]:
    """Pattern-match one install script. Never executes anything."""
    found: list[Signal] = []
    seen: set[str] = set()
    for kind, detail, pattern in PATTERNS:
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
    name = path.rsplit("/", 1)[-1]
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


def inspect_archive(data: bytes, ecosystem: str) -> list[Signal]:
    """Walk an archive in memory and scan its install-time files."""
    signals: list[Signal] = []
    buffer = io.BytesIO(data)

    try:
        if data[:2] == b"PK":
            with zipfile.ZipFile(buffer) as archive:
                for info in archive.infolist()[:MAX_MEMBERS]:
                    if info.is_dir() or not _interesting(info.filename, ecosystem):
                        continue
                    text = _read_member(archive, info.filename, info.file_size)
                    if text is None:
                        continue
                    if ecosystem == "npm":
                        text = _npm_hook_source(text)
                    if text.strip():
                        signals += scan_text(text, info.filename.rsplit("/", 1)[-1])
        else:
            with tarfile.open(fileobj=buffer, mode="r:*") as archive:
                for member in archive.getmembers()[:MAX_MEMBERS]:
                    if not member.isfile() or not _interesting(member.name, ecosystem):
                        continue
                    text = _read_member(archive, member, member.size)
                    if text is None:
                        continue
                    if ecosystem == "npm":
                        text = _npm_hook_source(text)
                    if text.strip():
                        signals += scan_text(text, member.name.rsplit("/", 1)[-1])
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, OSError) as exc:
        raise InspectionError(f"could not read archive: {exc}") from exc

    # keep the first signal of each kind, most alarming first
    order = {kind: i for i, (kind, _, _) in enumerate(PATTERNS)}
    unique: dict[str, Signal] = {}
    for signal in signals:
        unique.setdefault(signal.kind, signal)
    return sorted(unique.values(), key=lambda s: order.get(s.kind, 99))


def inspect_package(archive_url: str, ecosystem: str) -> list[Signal]:
    """Download and statically inspect one package's install-time code."""
    return inspect_archive(_download(archive_url), ecosystem)
