"""Tests for the registry lookup cache.

The security-relevant part is the time-to-live on a negative result. A name that
does not exist today can be registered tomorrow, and that is precisely the attack
ghostpkg exists to catch, so "does not exist" must not be held for long.
"""

import json
import time

import pytest

from ghostpkg.cache import (
    SCHEMA,
    TTL_ESTABLISHED,
    TTL_MISSING,
    TTL_YOUNG,
    Cache,
    default_dir,
    ttl_for,
)
from ghostpkg.registries import PackageFacts


def facts(**overrides) -> PackageFacts:
    base = dict(
        name="example",
        ecosystem="pypi",
        exists=True,
        age_days=2000,
        release_count=25,
        has_repo_url=True,
    )
    base.update(overrides)
    return PackageFacts(**base)


@pytest.fixture
def cache(tmp_path):
    return Cache(directory=tmp_path, enabled=True)


class TestTimeToLive:
    def test_missing_package_is_held_briefly(self):
        """The attack is registering a name that is free right now."""
        assert ttl_for(facts(exists=False)) == TTL_MISSING
        assert TTL_MISSING <= 60 * 60

    def test_missing_is_shorter_than_anything_else(self):
        assert TTL_MISSING < TTL_YOUNG < TTL_ESTABLISHED

    def test_established_package_is_held_longest(self):
        assert ttl_for(facts(age_days=2000)) == TTL_ESTABLISHED

    def test_young_package_gets_the_middle_ttl(self):
        assert ttl_for(facts(age_days=10)) == TTL_YOUNG

    def test_unknown_age_is_treated_as_young(self):
        assert ttl_for(facts(age_days=None)) == TTL_YOUNG


class TestRoundTrip:
    def test_stores_and_returns_facts(self, cache):
        cache.put(facts(name="requests"))
        got = cache.get("pypi", "requests")
        assert got is not None
        assert got.name == "requests"
        assert got.release_count == 25

    def test_survives_save_and_reload(self, cache, tmp_path):
        cache.put(facts(name="requests"))
        cache.save()
        assert Cache(directory=tmp_path).get("pypi", "requests") is not None

    def test_lookup_is_case_insensitive(self, cache):
        cache.put(facts(name="Requests"))
        assert cache.get("pypi", "requests") is not None

    def test_returned_name_keeps_the_callers_spelling(self, cache):
        cache.put(facts(name="Requests"))
        assert cache.get("pypi", "requests").name == "requests"

    def test_ecosystems_are_separate(self, cache):
        cache.put(facts(name="express", ecosystem="npm"))
        assert cache.get("npm", "express") is not None
        assert cache.get("pypi", "express") is None

    def test_expired_entry_is_a_miss(self, cache, tmp_path):
        cache.put(facts(name="requests", exists=False))
        cache.save()
        raw = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
        for entry in raw["entries"].values():
            entry["at"] = time.time() - (TTL_MISSING + 60)
        (tmp_path / "registry.json").write_text(json.dumps(raw), encoding="utf-8")
        assert Cache(directory=tmp_path).get("pypi", "requests") is None

    def test_expired_entries_are_dropped_on_save(self, cache, tmp_path):
        cache.put(facts(name="stale"))
        for entry in cache._entries.values():
            entry["at"] = time.time() - (TTL_ESTABLISHED + 60)
        cache.put(facts(name="fresh"))
        cache.save()
        raw = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
        assert "pypi:fresh" in raw["entries"]
        assert "pypi:stale" not in raw["entries"]


class TestDegradation:
    """A cache is a convenience. It must never break a run."""

    def test_disabled_cache_stores_nothing(self, tmp_path):
        cache = Cache(directory=tmp_path, enabled=False)
        cache.put(facts())
        cache.save()
        assert cache.get("pypi", "example") is None
        assert not (tmp_path / "registry.json").exists()

    def test_corrupt_file_is_ignored(self, tmp_path):
        (tmp_path / "registry.json").write_text("not json at all", encoding="utf-8")
        assert Cache(directory=tmp_path).get("pypi", "requests") is None

    def test_wrong_schema_is_ignored(self, tmp_path):
        (tmp_path / "registry.json").write_text(
            json.dumps({"schema": SCHEMA + 99, "entries": {"pypi:x": {}}}),
            encoding="utf-8",
        )
        assert Cache(directory=tmp_path).get("pypi", "x") is None

    def test_malformed_entry_is_ignored(self, tmp_path):
        (tmp_path / "registry.json").write_text(
            json.dumps({"schema": SCHEMA, "entries": {"pypi:x": {"at": "soon"}}}),
            encoding="utf-8",
        )
        assert Cache(directory=tmp_path).get("pypi", "x") is None

    def test_unwritable_directory_does_not_raise(self, tmp_path):
        blocked = tmp_path / "afile"
        blocked.write_text("", encoding="utf-8")
        cache = Cache(directory=blocked / "under-a-file")
        cache.put(facts())
        cache.save()  # must not raise

    def test_counts_hits_and_misses(self, cache):
        cache.put(facts(name="requests"))
        cache.get("pypi", "requests")
        cache.get("pypi", "nothing-here")
        assert cache.hits == 1
        assert cache.misses == 1


class TestLocation:
    def test_environment_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GHOSTPKG_CACHE_DIR", str(tmp_path / "custom"))
        assert default_dir() == tmp_path / "custom"

    def test_default_is_platform_specific(self, monkeypatch):
        monkeypatch.delenv("GHOSTPKG_CACHE_DIR", raising=False)
        assert default_dir().name == "ghostpkg"
