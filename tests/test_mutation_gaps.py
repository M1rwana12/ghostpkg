"""Behaviour the suite was not defending.

Found by mutation testing: 186 single-line changes to the source, each run
against the whole suite. 53 left it green. These tests kill the survivors that
carry a real consequence — a wrong verdict, a dropped package, a flag that
silently does nothing.

The code was correct in every case. What was missing was anything that would
notice if it stopped being.
"""

from __future__ import annotations

import json

import pytest

from ghostpkg.assess import Verdict
from ghostpkg.cli import main
from ghostpkg.manifests import parse_requirements
from ghostpkg.prose import extract
from ghostpkg.registries import PackageFacts
from ghostpkg.scanner import evaluate


def facts(name, ecosystem="pypi", **kwargs):
    base = dict(
        name=name, ecosystem=ecosystem, exists=True,
        age_days=4000, release_count=30, has_repo_url=True,
    )
    base.update(kwargs)
    return PackageFacts(**base)


class TestEachFindingKeepsItsOwnOrigin:
    """The thread pool returns results in submission order and they are zipped
    back onto the names. Reversing that list left all 656 tests passing: the
    printed names still looked right, because a finding carries its own name —
    but the file and line stamped onto it came from a different package. A CI
    annotation would point at the wrong line and mark the wrong one clean."""

    def test_the_finding_named_x_carries_the_line_x_was_written_on(self, monkeypatch):
        from ghostpkg.manifests import Requirement

        monkeypatch.setattr(
            "ghostpkg.scanner.fetch",
            lambda name, ecosystem: facts(name, ecosystem, exists=name != "ghost"),
        )
        findings = evaluate(
            [
                Requirement(name="flask", source="requirements.txt", line=1),
                Requirement(name="ghost", source="requirements.txt", line=2),
                Requirement(name="requests", source="other.txt", line=7),
            ],
            "pypi",
            cache=None,
        )
        placed = {f.name: (f.source, f.line) for f in findings}
        assert placed["flask"] == ("requirements.txt", 1)
        assert placed["ghost"] == ("requirements.txt", 2)
        assert placed["requests"] == ("other.txt", 7)

    def test_the_verdict_belongs_to_the_name_it_is_printed_against(self, monkeypatch):
        monkeypatch.setattr(
            "ghostpkg.scanner.fetch",
            lambda name, ecosystem: facts(name, ecosystem, exists="ghost" not in name),
        )
        findings = evaluate(["a-real-one", "ghost-one", "b-real-one", "ghost-two"], "pypi", cache=None)
        for finding in findings:
            expected = Verdict.BLOCK if "ghost" in finding.name else Verdict.OK
            assert finding.verdict is expected, finding.name

    def test_order_is_preserved(self, monkeypatch):
        monkeypatch.setattr("ghostpkg.scanner.fetch", lambda name, ecosystem: facts(name, ecosystem))
        wanted = ["one", "two", "three", "four", "five"]
        assert [f.name for f in evaluate(wanted, "pypi", cache=None)] == wanted


class TestTheFlagsReachTheScan:
    """Every flag was tested at the function it configures and none end to end
    from `main()`. Setting `args.deep` or `args.strict` to False in the call
    left the suite green: the flag would silently do nothing."""

    def test_deep_reaches_the_scanner(self, tmp_path, monkeypatch):
        seen = {}

        def fake_evaluate(requirements, ecosystem, strict=False, cache=None, deep=False, workers=8):
            seen["deep"] = deep
            seen["strict"] = strict
            return []

        monkeypatch.setattr("ghostpkg.cli.evaluate", fake_evaluate)
        path = tmp_path / "requirements.txt"
        path.write_text("flask\n", encoding="utf-8")
        main(["scan", str(path), "--deep", "--no-cache"])
        assert seen["deep"] is True

    def test_strict_reaches_the_scanner(self, tmp_path, monkeypatch):
        seen = {}

        def fake_evaluate(requirements, ecosystem, strict=False, cache=None, deep=False, workers=8):
            seen["strict"] = strict
            return []

        monkeypatch.setattr("ghostpkg.cli.evaluate", fake_evaluate)
        path = tmp_path / "requirements.txt"
        path.write_text("flask\n", encoding="utf-8")
        main(["scan", str(path), "--strict", "--no-cache"])
        assert seen["strict"] is True

    def test_strict_changes_the_exit_code(self, tmp_path, monkeypatch):
        """The consequence, not just the wiring: a warning becomes a failure."""
        monkeypatch.setattr(
            "ghostpkg.scanner.fetch",
            lambda name, ecosystem: facts(name, ecosystem, age_days=10, release_count=1, has_repo_url=False),
        )
        path = tmp_path / "requirements.txt"
        path.write_text("something-new\n", encoding="utf-8")
        assert main(["scan", str(path), "--no-cache"]) == 0
        assert main(["scan", str(path), "--strict", "--no-cache"]) == 1

    def test_an_error_exit_beats_a_clean_one(self, tmp_path, monkeypatch):
        """Swapping the BLOCK/ERROR precedence survived. An unchecked name must
        never come out as a pass."""
        from ghostpkg.registries import RegistryError

        monkeypatch.setattr(
            "ghostpkg.scanner.fetch",
            lambda name, ecosystem: (_ for _ in ()).throw(RegistryError("offline")),
        )
        path = tmp_path / "requirements.txt"
        path.write_text("flask\n", encoding="utf-8")
        assert main(["scan", str(path), "--no-cache"]) == 2


class TestProseKeepsItsOwnEcosystem:
    """A README can hold `pip install x` and `npm install y` in adjacent lines,
    so each name carries the registry it belongs to. Dropping the per-item
    override made every npm name from prose be looked up on PyPI -- a false
    block on all of them, and the feature silently dead."""

    def test_an_npm_name_from_prose_is_looked_up_on_npm(self, tmp_path, monkeypatch):
        looked_up = []

        def fake_fetch(name, ecosystem):
            looked_up.append((name, ecosystem))
            return facts(name, ecosystem)

        monkeypatch.setattr("ghostpkg.scanner.fetch", fake_fetch)
        path = tmp_path / "AGENTS.md"
        path.write_text("Run `npm install left-pad` then `pip install flask`.\n", encoding="utf-8")
        main(["scan", str(path), "--no-cache"])
        assert ("left-pad", "npm") in looked_up
        assert ("flask", "pypi") in looked_up


class TestProseExtraction:
    """Three survivors in one module, each verified to produce a wrong answer
    on an ordinary README line."""

    def test_a_chained_command_is_still_read(self):
        """`_split_commands` returning the whole line dropped this entirely and
        the run reported all clean."""
        assert [r.name for r in extract("cd app && npm install left-pad")] == ["left-pad"]

    @pytest.mark.parametrize(
        "line",
        ["brew install jq", "apt-get install curl nginx", "dnf install make", "cargo install ripgrep"],
    )
    def test_an_unknown_installer_yields_nothing(self, line):
        """Falling through to the pip branch turned `brew install jq` into a
        PyPI lookup -- a false block on an ordinary README."""
        assert extract(line) == []

    def test_a_pinned_npm_name_keeps_only_the_name(self):
        """`npm install react@18` is the commonest install line there is.
        Without the guard it was checked as the literal name `react@18`."""
        assert [r.name for r in extract("npm install react@18")] == ["react"]


class TestAgeComesFromTheFirstUpload:
    """Measuring from the newest upload made every actively maintained package
    read as days old: mass false warnings, and false blocks under `--strict`."""

    def test_a_long_lived_package_is_not_young(self):
        from ghostpkg.registries import parse_npm

        payload = {
            "name": "thing",
            "time": {"created": "2015-01-01T00:00:00.000Z", "modified": "2026-09-01T00:00:00.000Z"},
            "versions": {"1.0.0": {}, "2.0.0": {}},
            "dist-tags": {"latest": "2.0.0"},
        }
        assert parse_npm("thing", payload).age_days > 4000


class TestTheCacheNeverStoresAMiss:
    """`ttl_for` is tested, the guard inside `put` was not -- so the project's
    stated rule was only enforced second-hand."""

    def test_put_refuses_a_negative(self, tmp_path):
        from ghostpkg.cache import Cache

        cache = Cache(directory=tmp_path)
        cache.put(PackageFacts(name="ghost", ecosystem="pypi", exists=False))
        assert cache.get("pypi", "ghost") is None

    def test_put_stores_a_positive(self, tmp_path):
        from ghostpkg.cache import Cache

        cache = Cache(directory=tmp_path)
        cache.put(facts("flask"))
        assert cache.get("pypi", "flask") is not None


class TestAnIncludeWithATrailingComment:
    """Dropping the inline-comment strip stopped `-r base.txt  # shared deps`
    following the include -- every package in the included file lost in
    silence, with the run still reporting success."""

    def test_the_include_is_followed(self, tmp_path):
        from ghostpkg.manifests import load_manifest

        (tmp_path / "base.txt").write_text("flask\njinja2\n", encoding="utf-8")
        (tmp_path / "requirements.txt").write_text(
            "-r base.txt  # shared deps\nrequests\n", encoding="utf-8"
        )
        found, _ = load_manifest(tmp_path / "requirements.txt")
        assert set(r.name for r in found) == {"flask", "jinja2", "requests"}

    def test_a_comment_after_a_name_is_stripped(self):
        assert [r.name for r in parse_requirements("flask  # web\n")] == ["flask"]


class TestTheErrorSummaryIsReported:
    """The error count in `--json` and the "could not be checked" line both
    survived mutation. A run that could not reach the registry must not read as
    a clean one."""

    def test_json_counts_the_errors(self, tmp_path, monkeypatch, capsys):
        from ghostpkg.registries import RegistryError

        monkeypatch.setattr(
            "ghostpkg.scanner.fetch",
            lambda name, ecosystem: (_ for _ in ()).throw(RegistryError("offline")),
        )
        path = tmp_path / "requirements.txt"
        path.write_text("flask\n", encoding="utf-8")
        main(["scan", str(path), "--no-cache", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"]["errored"] == 1
        assert payload["summary"]["blocked"] == 0

    def test_the_text_summary_says_so(self, tmp_path, monkeypatch, capsys):
        from ghostpkg.registries import RegistryError

        monkeypatch.setattr(
            "ghostpkg.scanner.fetch",
            lambda name, ecosystem: (_ for _ in ()).throw(RegistryError("offline")),
        )
        path = tmp_path / "requirements.txt"
        path.write_text("flask\n", encoding="utf-8")
        main(["scan", str(path), "--no-cache"])
        out = capsys.readouterr().out
        assert "could not be checked" in out
        assert "look fine" not in out
