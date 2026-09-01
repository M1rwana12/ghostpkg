"""Registry clients. Standard library only -- ghostpkg has no dependencies.

A supply-chain security tool that pulls in a dependency tree is a poor joke,
so this module deliberately uses urllib rather than requests.
"""

from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

USER_AGENT = "ghostpkg/0.1 (+https://github.com/m1rwana12/ghostpkg)"
TIMEOUT = 15

# npm serves whole packuments: @types/node is 11 MB uncompressed and 1.4 MB
# gzipped, and parsing the uncompressed form peaks around 60 MB of objects.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class RegistryError(RuntimeError):
    """The registry could not be reached or returned something unusable."""


@dataclass(frozen=True)
class PackageFacts:
    """What a registry tells us about a name. `exists=False` means 404."""

    name: str
    ecosystem: str
    exists: bool
    age_days: int | None = None
    release_count: int = 0
    has_repo_url: bool = False
    latest_version: str | None = None
    summary: str | None = None
    # Where the source archive lives, for --deep install-script inspection.
    archive_url: str | None = None
    archive_size: int | None = None
    #: Every version the registry lists. Already in the response we fetch, so
    #: checking a pinned version costs nothing extra -- and a model inventing
    #: `requests==99.99.99` is the same mistake as inventing the name.
    versions: tuple[str, ...] = ()

    def has_version(self, version: str) -> bool | None:
        """True/False if we know the version list, None if we do not."""
        if not self.versions:
            return None
        return version in self.versions


def _get_json(url: str) -> dict | None:
    """Fetch and decode JSON. Returns None on a clean 404.

    Everything that is not a clean 404 becomes a RegistryError. That matters
    more than it looks: urlopen only wraps failures that happen while
    *connecting*, so a stalled or reset connection during `read()` used to
    escape as a bare TimeoutError or ConnectionResetError, leave a traceback,
    and exit 1 -- the code that means "a package does not exist". The most
    ordinary network flakiness read as a confirmed detection.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise RegistryError(f"{url} returned more than {MAX_RESPONSE_BYTES} bytes")
            if response.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RegistryError(f"{url} returned HTTP {exc.code}") from exc
    except RegistryError:
        raise
    except Exception as exc:  # noqa: BLE001 - anything else is "lookup failed"
        raise RegistryError(f"could not read {url}: {exc}") from exc


def _age_in_days(iso_timestamp: str) -> int | None:
    try:
        moment = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - moment).days


def fetch_pypi(name: str) -> PackageFacts:
    quoted = urllib.parse.quote(name, safe="")
    payload = _get_json(f"https://pypi.org/pypi/{quoted}/json")
    if payload is None:
        return PackageFacts(name=name, ecosystem="pypi", exists=False)

    info = payload.get("info") or {}
    releases = payload.get("releases") or {}

    uploads = sorted(
        file["upload_time_iso_8601"]
        for files in releases.values()
        for file in files
        if file.get("upload_time_iso_8601")
    )
    age = _age_in_days(uploads[0]) if uploads else None

    project_urls = info.get("project_urls") or {}
    home_page = info.get("home_page") or ""

    # Prefer the sdist: it carries setup.py, which a wheel does not.
    archive_url = archive_size = None
    for entry in payload.get("urls") or []:
        if entry.get("packagetype") == "sdist":
            archive_url = entry.get("url")
            archive_size = entry.get("size")
            break

    return PackageFacts(
        name=name,
        ecosystem="pypi",
        exists=True,
        age_days=age,
        release_count=sum(1 for files in releases.values() if files),
        has_repo_url=bool(project_urls) or bool(home_page),
        latest_version=info.get("version"),
        summary=info.get("summary") or None,
        archive_url=archive_url,
        archive_size=archive_size,
        versions=tuple(releases),
    )


def fetch_npm(name: str) -> PackageFacts:
    quoted = urllib.parse.quote(name, safe="@/")
    payload = _get_json(f"https://registry.npmjs.org/{quoted}")
    if payload is None:
        return PackageFacts(name=name, ecosystem="npm", exists=False)

    time_map = payload.get("time") or {}
    created = time_map.get("created")
    age = _age_in_days(created) if created else None

    versions = payload.get("versions") or {}
    repository = payload.get("repository") or {}
    homepage = payload.get("homepage") or ""

    latest = (payload.get("dist-tags") or {}).get("latest")
    dist = ((versions.get(latest) or {}).get("dist") or {}) if latest else {}

    return PackageFacts(
        name=name,
        ecosystem="npm",
        exists=True,
        age_days=age,
        release_count=len(versions),
        has_repo_url=bool(repository.get("url")) or bool(homepage),
        latest_version=latest,
        summary=payload.get("description") or None,
        archive_url=dist.get("tarball"),
        archive_size=dist.get("unpackedSize"),
        versions=tuple(versions),
    )


FETCHERS = {"pypi": fetch_pypi, "npm": fetch_npm}


def fetch(name: str, ecosystem: str) -> PackageFacts:
    try:
        fetcher = FETCHERS[ecosystem]
    except KeyError:
        raise ValueError(
            f"unknown ecosystem {ecosystem!r}; expected one of {sorted(FETCHERS)}"
        ) from None
    return fetcher(name)
