"""On-disk cache for registry lookups.

Scanning a manifest costs one request per dependency, every time. A 200-line
requirements.txt in CI is 200 requests on every run, which is slow for the user
and rude to the registry.

**"Does not exist" is never cached.** That answer is the only one that blocks,
so it must always be fresh. Caching it briefly seemed safe until a real case
turned up: PyPI's RSS feed announces a package a moment before its JSON API
serves it, so a lookup lands on a 404, and a legitimately published package
was then reported as non-existent for the rest of the hour. A stale block is
the worst failure this tool has, and a missing name is rare enough in a real
manifest that re-checking costs almost nothing.

The reverse direction argues the same way: a name that is free right now can be
registered at any moment, and that is the entire attack. Either way, the
blocking signal is the one that must not come from a cache.

Positive answers are cached, since an established package changes slowly.

Cache failures are never fatal. An unwritable or corrupt cache degrades to no
cache at all rather than breaking a build.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .registries import PackageFacts

SCHEMA = 1

TTL_MISSING = 0                   # never cached -- see the module docstring
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
    """Loaded once, written once, read and updated from worker threads between.

    `put` is called from the scan's thread pool, so this is not lock-free by
    virtue of nobody mutating it -- an earlier version of this docstring
    claimed that and was wrong. It is safe because `dict.__setitem__` is
    atomic under the GIL and `save`/`_prune` only run after the pool has shut
    down. The lock exists so that stops being load-bearing on free-threaded
    builds, where that guarantee does not hold.
    """

    def __init__(self, directory: Path | None = None, enabled: bool = True) -> None:
        self.enabled = enabled
        self.directory = directory or default_dir()
        self._entries: dict[str, dict] = {}
        self._dirty = False
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()
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
        """Normalise per ecosystem, because they do not agree.

        PyPI names are case-insensitive and treat `-`, `_` and `.` as the same
        separator (PEP 503), so `zope.interface` and `Zope_Interface` are one
        package. npm names are case-sensitive, and `JSONStream` and `jsonstream`
        are two different real packages -- lowercasing both into one key served
        one package's facts for the other, including a wrong `exists`.
        """
        if ecosystem == "pypi":
            return f"pypi:{re.sub(r'[-_.]+', '-', name).lower()}"
        return f"{ecosystem}:{name}"

    def get(self, ecosystem: str, name: str) -> PackageFacts | None:
        if not self.enabled:
            return None
        with self._lock:
            entry = self._entries.get(self._key(ecosystem, name))
        if not isinstance(entry, dict):
            self._miss()
            return None
        stored_at = entry.get("at")
        ttl = entry.get("ttl")
        facts = entry.get("facts")
        if not isinstance(stored_at, (int, float)) or not isinstance(ttl, (int, float)):
            self._miss()
            return None
        if time.time() - stored_at > ttl:
            self._miss()
            return None
        try:
            revived = PackageFacts(**facts)
        except (TypeError, ValueError):
            self._miss()
            return None
        with self._lock:
            self.hits += 1
        # keep the caller's spelling of the name rather than the cached one
        if revived.name != name:
            revived = PackageFacts(**{**asdict(revived), "name": name})
        return revived

    def _miss(self) -> None:
        with self._lock:
            self.misses += 1

    def put(self, facts: PackageFacts) -> None:
        if not self.enabled:
            return
        # Never store a negative: it is the answer that blocks.
        if not facts.exists:
            return
        entry = {
            "at": time.time(),
            "ttl": ttl_for(facts),
            "facts": asdict(facts),
        }
        with self._lock:
            self._entries[self._key(facts.ecosystem, facts.name)] = entry
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
