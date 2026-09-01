"""On-disk cache for registry lookups.

Scanning a manifest costs one request per dependency, every time. A 200-line
requirements.txt in CI is 200 requests on every run, which is slow for the user
and rude to the registry.

Time-to-live depends on what was cached, and the important case is the negative
one. A name that does not exist today can be registered tomorrow -- that is the
entire attack this tool exists to catch -- so "does not exist" is held only
briefly. Established packages change slowly and are held far longer.

Cache failures are never fatal. An unwritable or corrupt cache degrades to no
cache at all rather than breaking a build.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from .registries import PackageFacts

SCHEMA = 1

TTL_MISSING = 60 * 60             # 1 hour  -- a free name can be taken any time
TTL_YOUNG = 6 * 60 * 60           # 6 hours -- facts still moving
TTL_ESTABLISHED = 24 * 60 * 60    # 1 day   -- changes slowly

ESTABLISHED_DAYS = 365


def default_dir() -> Path:
    """Per-platform cache directory, worked out by hand.

    A dependency for this would contradict the zero-dependency rule for the
    sake of three lines.
    """
    override = os.environ.get("GHOSTPKG_CACHE_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "ghostpkg"
    if sys.platform == "darwin":
        return Path(os.path.expanduser("~/Library/Caches")) / "ghostpkg"
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "ghostpkg"


def ttl_for(facts: PackageFacts) -> int:
    if not facts.exists:
        return TTL_MISSING
    if facts.age_days is not None and facts.age_days >= ESTABLISHED_DAYS:
        return TTL_ESTABLISHED
    return TTL_YOUNG


class Cache:
    """Read once, write once. Threads only read, so no locking is needed."""

    def __init__(self, directory: Path | None = None, enabled: bool = True) -> None:
        self.enabled = enabled
        self.directory = directory or default_dir()
        self._entries: dict[str, dict] = {}
        self._dirty = False
        self.hits = 0
        self.misses = 0
        if self.enabled:
            self._load()

    @property
    def path(self) -> Path:
        return self.directory / "registry.json"

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
            return
        entries = raw.get("entries")
        if isinstance(entries, dict):
            self._entries = entries

    @staticmethod
    def _key(ecosystem: str, name: str) -> str:
        return f"{ecosystem}:{name.lower()}"

    def get(self, ecosystem: str, name: str) -> PackageFacts | None:
        if not self.enabled:
            return None
        entry = self._entries.get(self._key(ecosystem, name))
        if not isinstance(entry, dict):
            self.misses += 1
            return None
        stored_at = entry.get("at")
        ttl = entry.get("ttl")
        facts = entry.get("facts")
        if not isinstance(stored_at, (int, float)) or not isinstance(ttl, (int, float)):
            self.misses += 1
            return None
        if time.time() - stored_at > ttl:
            self.misses += 1
            return None
        try:
            revived = PackageFacts(**facts)
        except (TypeError, ValueError):
            self.misses += 1
            return None
        self.hits += 1
        # keep the caller's spelling of the name rather than the cached one
        if revived.name != name:
            revived = PackageFacts(**{**asdict(revived), "name": name})
        return revived

    def put(self, facts: PackageFacts) -> None:
        if not self.enabled:
            return
        self._entries[self._key(facts.ecosystem, facts.name)] = {
            "at": time.time(),
            "ttl": ttl_for(facts),
            "facts": asdict(facts),
        }
        self._dirty = True

    def _prune(self) -> None:
        now = time.time()
        self._entries = {
            key: entry
            for key, entry in self._entries.items()
            if isinstance(entry, dict)
            and isinstance(entry.get("at"), (int, float))
            and now - entry["at"] <= entry.get("ttl", 0)
        }

    def save(self) -> None:
        """Write atomically. Any failure is silent -- a cache is a convenience."""
        if not self.enabled or not self._dirty:
            return
        self._prune()
        payload = {"schema": SCHEMA, "entries": self._entries}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=str(self.directory), suffix=".tmp")
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(payload, file)
            os.replace(temporary, self.path)
        except OSError:
            try:
                os.unlink(temporary)  # noqa: F821
            except (OSError, NameError):
                pass

    def clear(self) -> bool:
        try:
            self.path.unlink()
            return True
        except OSError:
            return False
