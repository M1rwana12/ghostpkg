"""Registry clients. Standard library only -- ghostpkg has no dependencies.

A supply-chain security tool that pulls in a dependency tree is a poor joke,
so this module deliberately uses urllib rather than requests.
"""

from __future__ import annotations

import gzip
import json
import time
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

# Both registries rate-limit, and a scan opens several connections at once.
# Giving up immediately turns a busy moment into a failed run; retrying without
# a pause makes the rate limiting worse. I did exactly that in a data-collection
# script and turned one 429 into a self-amplifying storm.
RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.5


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
    #: Versions the maintainer withdrew, mapped to the reason they gave.
    #: PyPI only: npm's nearest equivalent is `deprecated`, which is used
    #: routinely for superseded branches -- 160 of glob's 168 versions carry
    #: it -- so it says nothing useful about safety.
    yanked: "tuple[tuple[str, str], ...]" = ()
    #: npm took this name away from whoever published malware under it and
    #: replaced the package with a placeholder. The name therefore *exists*,
    #: which is why it used to come back "ok".
    security_hold: bool = False

    def yanked_reason(self, version: str) -> str | None:
        return dict(self.yanked).get(version)

    def has_version(self, version: str) -> bool | None:
        """True/False if we know the version list, None if we do not."""
        if not self.versions:
            return None
        return version in self.versions


def _get_json(url: str, timeout: int | None = None) -> dict | None:
    """Fetch and decode JSON. Returns None on a clean 404.

    Everything that is not a clean 404 becomes a RegistryError. That matters
    more than it looks: urlopen only wraps failures that happen while
    *connecting*, so a stalled or reset connection during `read()` used to
    escape as a bare TimeoutError or ConnectionResetError, leave a traceback,
    and exit 1 -- the code that means "a package does not exist". The most
    ordinary network flakiness read as a confirmed detection.
    """
    # Read at call time, not at import. Binding the module global as a
    # default froze it at 15 seconds, so `--timeout` never took effect.
    timeout = TIMEOUT if timeout is None else timeout
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    )
    last: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise RegistryError(
                        f"{url} returned more than {MAX_RESPONSE_BYTES} bytes"
                    )
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in RETRY_STATUSES and attempt < MAX_ATTEMPTS - 1:
                time.sleep(_retry_after(exc, attempt))
                last = exc
                continue
            raise RegistryError(f"{url} returned HTTP {exc.code}") from exc
        except RegistryError:
            raise
        except Exception as exc:  # noqa: BLE001 - anything else is "lookup failed"
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(BACKOFF_SECONDS * (attempt + 1))
                last = exc
                continue
            raise RegistryError(f"could not read {url}: {exc}") from exc

    raise RegistryError(f"could not read {url} after {MAX_ATTEMPTS} attempts: {last}")


def _retry_after(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Honour Retry-After when the registry sends one, else back off."""
    header = exc.headers.get("Retry-After") if exc.headers else None
    if header:
        try:
            return min(float(header), 30.0)
        except ValueError:
            pass
    return BACKOFF_SECONDS * (2**attempt)


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
        yanked=tuple(
            (version, next((f.get("yanked_reason") or "") for f in files), )
            for version, files in releases.items()
            if files and all(f.get("yanked") for f in files)
        ),
    )


#: When npm removes a package for malware it does not delete the name and does
#: not answer 451 -- it republishes a placeholder owned by npm, pointing at this
#: repository. `crossenv` and `ffmepg`, both real typosquat incidents, look
#: exactly like this. Matching the repository rather than the description
#: because a description is free text anyone could copy.
SECURITY_HOLDER = "github.com/npm/security-holder"


def fetch_npm(name: str) -> PackageFacts:
    quoted = urllib.parse.quote(name, safe="@/")
    payload = _get_json(f"https://registry.npmjs.org/{quoted}")
    if payload is None:
        return PackageFacts(name=name, ecosystem="npm", exists=False)
    return parse_npm(name, payload)


def parse_npm(name: str, payload: dict) -> PackageFacts:
    """Facts from an npm registry document.

    Kept separate from the request so the shapes npm actually returns can be
    tested without a network round trip -- the registry does not enforce one
    shape per field, and a scan wide enough to touch a thousand real packages
    is how that gets discovered.
    """
    time_map = payload.get("time") or {}
    created = time_map.get("created")
    age = _age_in_days(created) if created else None

    versions = payload.get("versions") or {}
    # npm allows both `{"url": ...}` and the string shorthand `"github:a/b"`.
    # Assuming the object crashed a whole scan the first time a real lockfile
    # was wide enough to contain one.
    raw_repository = payload.get("repository")
    if isinstance(raw_repository, str):
        repository_url = raw_repository
    elif isinstance(raw_repository, dict):
        repository_url = str(raw_repository.get("url") or "")
    else:
        repository_url = ""
    homepage = payload.get("homepage") or ""

    latest = (payload.get("dist-tags") or {}).get("latest")
    dist = ((versions.get(latest) or {}).get("dist") or {}) if latest else {}

    return PackageFacts(
        name=name,
        ecosystem="npm",
        exists=True,
        age_days=age,
        release_count=len(versions),
        has_repo_url=bool(repository_url) or bool(homepage),
        latest_version=latest,
        summary=payload.get("description") or None,
        archive_url=dist.get("tarball"),
        archive_size=dist.get("unpackedSize"),
        versions=tuple(versions),
        security_hold=SECURITY_HOLDER in repository_url,
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
