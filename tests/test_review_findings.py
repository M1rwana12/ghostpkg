"""Defects found by reviewing the shipped 0.19.2, not the working tree.

Two of the four are false blocks, which this project treats as its worst
failure. Both come from the same cause: `jslocks.py` was written after
`test_stated_sources.py`, so the rule that file exists to defend -- a
dependency naming its own source is not the registry's business -- was never
asserted for `yarn.lock` or `pnpm-lock.yaml`.
"""

from __future__ import annotations

import inspect

import pytest

from ghostpkg import registries
from ghostpkg.discover import AGENT_FILES
from ghostpkg.jslocks import parse_pnpm_lock, parse_yarn_lock
from ghostpkg.manifests import SUPPORTED
from ghostpkg.prose import looks_like_prose


def names(requirements):
    return [r.name for r in requirements]


class TestTheTimeoutFlagTakesEffect:
    """`def _get_json(url, timeout=TIMEOUT)` bound the module global once, at
    import. The CLI set `registries.TIMEOUT`, nothing re-read it, and a
    documented flag did nothing at all."""

    def test_the_default_is_resolved_in_the_body(self):
        assert inspect.signature(registries._get_json).parameters["timeout"].default is None

    def test_a_changed_global_is_picked_up(self, monkeypatch):
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["timeout"] = timeout
            raise OSError("stop here")

        monkeypatch.setattr(registries, "TIMEOUT", 99)
        monkeypatch.setattr(registries.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(registries.RegistryError):
            registries._get_json("https://example.invalid/x")
        assert seen["timeout"] == 99

    def test_an_explicit_argument_still_wins(self, monkeypatch):
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["timeout"] = timeout
            raise OSError("stop here")

        monkeypatch.setattr(registries, "TIMEOUT", 99)
        monkeypatch.setattr(registries.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(registries.RegistryError):
            registries._get_json("https://example.invalid/x", timeout=5)
        assert seen["timeout"] == 5


class TestTheSearchAndTheParserAgree:
    """A file the directory search offers up and the parser then refuses is
    dropped in silence -- the CLI ignores an unreadable *discovered* file on
    purpose, so nothing is printed. `.windsurfrules` was in the search list and
    not in the prose list, so it was found and never scanned."""

    @pytest.mark.parametrize("filename", sorted(AGENT_FILES))
    def test_every_discovered_agent_file_can_be_read(self, filename):
        assert looks_like_prose(filename), f"{filename} is found but not parseable"

    def test_the_error_message_lists_what_is_actually_supported(self):
        for name in sorted(AGENT_FILES):
            assert name in SUPPORTED.lower(), f"{name} missing from the supported list"

    def test_an_agent_file_is_scanned_end_to_end(self, tmp_path):
        from ghostpkg.manifests import load_manifest

        path = tmp_path / ".windsurfrules"
        path.write_text("Run `pip install flask` first.\n", encoding="utf-8")
        assert names(load_manifest(path)[0]) == ["flask"]


class TestYarnStatedSources:
    """`GITHUB_SHORTHAND` was applied to `package.json` and missed here, so a
    private repository dependency was looked up on npmjs and blocked."""

    @pytest.mark.parametrize(
        "descriptor",
        [
            '"internal-lib@acme/internal-lib#v1.2.3"',
            '"thing@acme/thing"',
            '"thing@acme/thing#semver:^1.0.0"',
        ],
    )
    def test_github_shorthand_is_not_a_registry_name(self, descriptor):
        assert names(parse_yarn_lock(f"{descriptor}:\n  version \"1.0.0\"\n")) == []

    @pytest.mark.parametrize(
        "descriptor",
        ['"thing@workspace:packages/x"', '"thing@link:../x"', '"thing@patch:thing@npm%3A1.0.0#./p.diff"'],
    )
    def test_the_protocols_still_hold(self, descriptor):
        assert names(parse_yarn_lock(f"{descriptor}:\n  version: 1.0.0\n")) == []

    def test_ordinary_entries_are_untouched(self):
        text = 'lodash@^4:\n  version "4.17.21"\n\n"@babel/core@^7":\n  version "7.0.0"\n'
        assert names(parse_yarn_lock(text)) == ["lodash", "@babel/core"]


class TestPnpmStatedSources:
    """The protocol is only a prefix in lockfile v5. From v6 it follows
    `name@`, so testing the start of the key never fired: the parser fell
    through to its slash split and emitted `github.com/acme/forked` as a
    package name, which was then looked up and blocked."""

    def scan(self, key):
        return names(parse_pnpm_lock(f"packages:\n  {key}:\n    resolution: {{}}\n"))

    @pytest.mark.parametrize(
        "key",
        [
            "github.com/acme/forked/abc123",
            "codeload.github.com/acme/foo/tar.gz/abc",
            "foo@https://codeload.github.com/acme/foo/tar.gz",
            "foo@git+ssh://git@github.com/acme/foo.git",
            "file:packages/ui",
            "link:../shared",
        ],
    )
    def test_a_stated_source_yields_nothing(self, key):
        assert self.scan(key) == []

    @pytest.mark.parametrize(
        "key, expected",
        [
            ("big.js@6.2.1", "big.js"),
            ("array.prototype.concat@1.0.0", "array.prototype.concat"),
            ("react@18.2.0", "react"),
            ("'@babel/parser@7.29.3'", "@babel/parser"),
            ("/react/18.2.0", "react"),
            ("/@babel/code-frame/7.12.11", "@babel/code-frame"),
        ],
    )
    def test_real_names_survive(self, key, expected):
        """A dot in a name is legal and common. Rejecting every key with one
        threw away 289 of Svelte's 435 packages -- a silent miss introduced
        while fixing a false block, and caught only by re-measuring."""
        assert self.scan(key) == [expected]


class TestTheRealLockfilesAreUnchanged:
    """The counts from six popular projects, so a future tightening of the
    stated-source rule cannot quietly start dropping real packages."""

    @pytest.mark.parametrize(
        "shape, expected",
        [
            ("packages:\n  " + "\n  ".join(f"pkg{i}@1.0.0:\n    resolution: {{}}" for i in range(50)), 50),
        ],
    )
    def test_a_wide_lockfile_reads_every_entry(self, shape, expected):
        assert len(parse_pnpm_lock(shape)) == expected
