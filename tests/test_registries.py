"""Registry client behaviour, against a local HTTP server.

This is where the worst bug in the project lived and went untested: `urlopen`
only wraps failures that happen while *connecting*, so a connection that
stalled or reset while the body was being read escaped as a bare TimeoutError,
left a traceback, and exited 1 -- the code that means "this package does not
exist". Ordinary network flakiness read as a confirmed detection.

Everything here runs against 127.0.0.1, so the suite still needs no internet.
"""

from __future__ import annotations

import gzip
import json
import socket
import threading

import pytest

from ghostpkg import registries
from ghostpkg.inspection import InspectionError, _download
from ghostpkg.registries import RegistryError


class Server:
    """A one-shot HTTP server that replies however the test needs it to."""

    def __init__(self, handler):
        self.handler = handler
        self.requests = 0
        self.socket = socket.socket()
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(8)
        self.port = self.socket.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                connection, _ = self.socket.accept()
            except OSError:
                return
            try:
                connection.recv(65536)
                self.requests += 1
                self.handler(connection, self.requests)
            except OSError:
                pass
            finally:
                try:
                    connection.close()
                except OSError:
                    pass

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/x"

    def close(self):
        self.socket.close()


def respond(connection, status: str, body: bytes = b"", headers: str = ""):
    connection.sendall(
        f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n{headers}\r\n".encode()
        + body
    )


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    """Keep the retry tests fast without disabling the behaviour."""
    monkeypatch.setattr(registries, "BACKOFF_SECONDS", 0.01)


class TestFailuresDuringTheResponseBody:
    """The regression that exited 1 on a network hiccup."""

    def test_a_connection_reset_mid_body_is_a_registry_error(self):
        def handler(connection, _):
            connection.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: 5000\r\n\r\n{\"partial\":"
            )
            connection.close()

        server = Server(handler)
        try:
            with pytest.raises(RegistryError):
                registries._get_json(server.url)
        finally:
            server.close()

    def test_malformed_json_is_a_registry_error(self):
        server = Server(lambda c, _: respond(c, "200 OK", b"not json at all"))
        try:
            with pytest.raises(RegistryError):
                registries._get_json(server.url)
        finally:
            server.close()

    def test_an_oversized_response_is_refused(self, monkeypatch):
        monkeypatch.setattr(registries, "MAX_RESPONSE_BYTES", 64)
        server = Server(lambda c, _: respond(c, "200 OK", b"x" * 500))
        try:
            with pytest.raises(RegistryError, match="more than"):
                registries._get_json(server.url)
        finally:
            server.close()


class TestStatusMapping:
    def test_404_is_not_an_error_but_an_answer(self):
        """A missing package is the whole point, not a failure."""
        server = Server(lambda c, _: respond(c, "404 Not Found"))
        try:
            assert registries._get_json(server.url) is None
        finally:
            server.close()

    @pytest.mark.parametrize("status", ["400 Bad Request", "403 Forbidden"])
    def test_other_4xx_are_errors(self, status):
        server = Server(lambda c, _: respond(c, status))
        try:
            with pytest.raises(RegistryError, match="HTTP"):
                registries._get_json(server.url)
        finally:
            server.close()

    def test_a_good_response_is_decoded(self):
        body = json.dumps({"hello": "world"}).encode()
        server = Server(lambda c, _: respond(c, "200 OK", body))
        try:
            assert registries._get_json(server.url) == {"hello": "world"}
        finally:
            server.close()

    def test_gzip_is_decompressed(self):
        body = gzip.compress(json.dumps({"big": "payload"}).encode())
        server = Server(
            lambda c, _: respond(c, "200 OK", body, "Content-Encoding: gzip\r\n")
        )
        try:
            assert registries._get_json(server.url) == {"big": "payload"}
        finally:
            server.close()


class TestRetry:
    """Registries answer 429 and 503 under load. Giving up on the first one
    turned a busy moment into a failed run; retrying without a pause is what
    turned a single 429 into a self-amplifying storm elsewhere."""

    def test_a_429_is_retried_and_can_succeed(self):
        def handler(connection, attempt):
            if attempt < 3:
                respond(connection, "429 Too Many Requests", headers="Retry-After: 0\r\n")
            else:
                respond(connection, "200 OK", b'{"ok": true}')

        server = Server(handler)
        try:
            assert registries._get_json(server.url) == {"ok": True}
            assert server.requests == 3
        finally:
            server.close()

    def test_retries_are_bounded(self):
        server = Server(lambda c, _: respond(c, "503 Service Unavailable"))
        try:
            with pytest.raises(RegistryError):
                registries._get_json(server.url)
            assert server.requests == registries.MAX_ATTEMPTS
        finally:
            server.close()

    def test_a_404_is_not_retried(self):
        server = Server(lambda c, _: respond(c, "404 Not Found"))
        try:
            assert registries._get_json(server.url) is None
            assert server.requests == 1
        finally:
            server.close()

    def test_retry_after_is_capped(self):
        """A registry asking us to wait an hour must not hang the run."""

        class FakeError:
            headers = {"Retry-After": "3600"}

        assert registries._retry_after(FakeError(), 0) <= 30.0

    def test_a_nonsense_retry_after_falls_back_to_backoff(self):
        class FakeError:
            headers = {"Retry-After": "soon"}

        assert registries._retry_after(FakeError(), 0) > 0


class TestArchiveUrlScheme:
    """The archive URL is data the registry handed us, not a value we chose."""

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "http://169.254.169.254/latest/meta-data/",
            "ftp://example.com/x.tgz",
            "//example.com/x.tgz",
        ],
    )
    def test_only_https_is_fetched(self, url):
        with pytest.raises(InspectionError, match="refusing"):
            _download(url)


class TestNpmMetadataShapes:
    """npm's registry does not enforce a single shape for these fields, and a
    scan that touched a thousand real packages found it out."""

    def facts(self, payload):
        import json as _json

        from ghostpkg import registries

        return registries.parse_npm("thing", _json.loads(_json.dumps(payload)))

    def test_repository_as_a_string_does_not_crash(self):
        """`"repository": "github:user/repo"` is the documented shorthand.
        Assuming the object form raised AttributeError and took down a whole
        scan the first time a lockfile was wide enough to contain one."""
        facts = self.facts({
            "name": "thing",
            "repository": "github:user/repo",
            "versions": {"1.0.0": {}},
            "dist-tags": {"latest": "1.0.0"},
        })
        assert facts.has_repo_url is True

    def test_repository_as_an_object_still_works(self):
        facts = self.facts({
            "name": "thing",
            "repository": {"url": "git+https://github.com/user/repo.git"},
            "versions": {"1.0.0": {}},
            "dist-tags": {"latest": "1.0.0"},
        })
        assert facts.has_repo_url is True

    def test_a_missing_repository_is_not_a_repo_url(self):
        facts = self.facts({
            "name": "thing",
            "versions": {"1.0.0": {}},
            "dist-tags": {"latest": "1.0.0"},
        })
        assert facts.has_repo_url is False

    def test_a_security_hold_is_still_seen_through_the_string_form(self):
        facts = self.facts({
            "name": "thing",
            "repository": "https://github.com/npm/security-holder",
            "versions": {"1.0.0": {}},
            "dist-tags": {"latest": "1.0.0"},
        })
        assert facts.security_hold is True
