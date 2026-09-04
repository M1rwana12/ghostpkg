"""Suppression rules.

The main case this exists for: a company with its own package index. Every
internal name is absent from public PyPI, so every one of them was blocked --
the exact false-positive class the project's own rules forbid.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from ghostpkg.assess import Verdict, assess
from ghostpkg.cli import main
from ghostpkg.policy import CONFIG_ENV, PolicyError, Rule, apply, load, locate, parse
from ghostpkg.registries import PackageFacts


def rules(*entries) -> list[Rule]:
    return parse(json.dumps({"ignore": list(entries)}))


def missing(name="acme-internal", ecosystem="pypi"):
    return assess(PackageFacts(name=name, ecosystem=ecosystem, exists=False))


class TestTheFileIsNeverReadFromTheProject:
    """ghostpkg guards a coding agent, and an agent with shell access can edit
    files in the repository it works on. A suppression list next to the code
    would be one the guarded thing can rewrite."""

    def test_a_file_in_the_working_directory_is_ignored(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv(CONFIG_ENV, raising=False)
        for name in (".ghostpkgignore", "ghostpkg.json", "ignore.json"):
            (tmp_path / name).write_text(
                json.dumps({"ignore": [{"package": "*", "reason": "sneaky"}]}),
                encoding="utf-8",
            )
        found, _ = load()
        assert found == []

    def test_an_explicit_path_is_honoured(self, tmp_path):
        path = tmp_path / "ignore.json"
        path.write_text(
            json.dumps({"ignore": [{"package": "x", "reason": "because"}]}),
            encoding="utf-8",
        )
        found, used_path = load(path)
        assert len(found) == 1
        assert used_path == path

    def test_the_environment_variable_is_honoured(self, tmp_path, monkeypatch):
        path = tmp_path / "elsewhere.json"
        path.write_text(
            json.dumps({"ignore": [{"package": "x", "reason": "because"}]}),
            encoding="utf-8",
        )
        monkeypatch.setenv(CONFIG_ENV, str(path))
        assert locate() == path


class TestAReasonIsRequired:
    def test_an_entry_without_a_reason_is_refused(self):
        with pytest.raises(PolicyError, match="reason"):
            rules({"package": "x"})

    def test_an_entry_without_a_package_is_refused(self):
        with pytest.raises(PolicyError, match="package"):
            rules({"reason": "x"})


class TestMalformedIsAnErrorNotAShrug:
    """Degrading quietly to 'no suppressions' would be safe; degrading quietly
    to 'no protection' would not, and the two look identical from outside."""

    def test_broken_json(self):
        with pytest.raises(PolicyError, match="not valid JSON"):
            parse("{ broken")

    def test_wrong_shape(self):
        with pytest.raises(PolicyError, match="expected a list"):
            parse(json.dumps({"ignore": "everything"}))

    def test_bad_date(self):
        with pytest.raises(PolicyError, match="expires"):
            rules({"package": "x", "reason": "y", "expires": "next tuesday"})

    def test_a_broken_file_makes_the_run_fail(self, tmp_path):
        path = tmp_path / "ignore.json"
        path.write_text("{ broken", encoding="utf-8")
        assert main(["check", "requests", "--no-cache", "--config", str(path)]) == 2

    def test_a_missing_file_named_explicitly_fails(self, tmp_path):
        assert main(["check", "requests", "--config", str(tmp_path / "nope.json")]) == 2


class TestSuppression:
    def test_the_private_index_case(self):
        """Why this feature exists."""
        finding, used = apply(
            missing(), rules({"package": "acme-*", "rule": "GP001", "reason": "our index"})
        )
        assert finding.verdict is Verdict.OK
        assert used[0].reason == "our index"

    def test_a_non_matching_name_is_untouched(self):
        finding, used = apply(missing("other-lib"), rules({"package": "acme-*", "reason": "x"}))
        assert finding.verdict is Verdict.BLOCK
        assert used == []

    def test_a_non_matching_rule_is_untouched(self):
        finding, _ = apply(missing(), rules({"package": "*", "rule": "GP003", "reason": "x"}))
        assert finding.verdict is Verdict.BLOCK

    def test_ecosystem_can_be_narrowed(self):
        entry = {"package": "*", "ecosystem": "npm", "reason": "x"}
        assert apply(missing(ecosystem="pypi"), rules(entry))[0].verdict is Verdict.BLOCK
        assert apply(missing(ecosystem="npm"), rules(entry))[0].verdict is Verdict.OK

    def test_suppressing_one_of_several_reasons_keeps_the_rest(self):
        finding = assess(
            PackageFacts(
                name="thing", ecosystem="pypi", exists=True,
                age_days=3, release_count=1, has_repo_url=False,
            )
        )
        before = len(finding.reasons)
        assert before > 1
        finding, _ = apply(finding, rules({"package": "*", "rule": "GP004", "reason": "fine"}))
        assert finding.verdict is Verdict.WARN
        assert len(finding.reasons) == before - 1
        assert not any(r.rule == "GP004" for r in finding.reasons)

    def test_a_block_downgrades_when_its_blocking_reason_is_suppressed(self):
        """A young package with an install-script signal blocks on GP007 while
        its other reasons are only advisory, so suppressing GP007 must leave a
        warning rather than a block with nothing behind it."""
        from ghostpkg.inspection import scan_text

        finding = assess(
            PackageFacts(
                name="thing", ecosystem="pypi", exists=True,
                age_days=3, release_count=1, has_repo_url=False,
            ),
            signals=scan_text("import subprocess\nsubprocess.run([])\n", "setup.py"),
        )
        assert finding.verdict is Verdict.BLOCK
        assert finding.blocking == ("GP007",)
        finding, _ = apply(finding, rules({"package": "*", "rule": "GP007", "reason": "known"}))
        assert finding.verdict is Verdict.WARN

    def test_strict_mode_keeps_blocking_while_any_reason_remains(self):
        """Under --strict every reason blocks, so removing one is not enough."""
        finding = assess(
            PackageFacts(
                name="thing", ecosystem="pypi", exists=True,
                age_days=3, release_count=1, has_repo_url=False,
            ),
            strict=True,
        )
        finding, _ = apply(finding, rules({"package": "*", "rule": "GP003", "reason": "fine"}))
        assert finding.verdict is Verdict.BLOCK


class TestExpiry:
    def test_an_expired_entry_does_not_apply(self):
        entry = {
            "package": "*",
            "reason": "temporary",
            "expires": str(date.today() - timedelta(days=1)),
        }
        finding, used = apply(missing(), rules(entry))
        assert finding.verdict is Verdict.BLOCK
        assert used == []

    def test_an_entry_expiring_in_the_future_still_applies(self):
        entry = {
            "package": "*",
            "reason": "temporary",
            "expires": str(date.today() + timedelta(days=30)),
        }
        assert apply(missing(), rules(entry))[0].verdict is Verdict.OK

    def test_no_expiry_means_permanent(self):
        assert apply(missing(), rules({"package": "*", "reason": "x"}))[0].verdict is Verdict.OK


class TestSuppressionEndToEnd:
    """The ignore file is loaded and applied in `main()` only, and nothing
    asserted its effect on a verdict or an exit code. The whole policy call
    could be replaced with `used = []` and all 539 tests still passed -- which
    is how a stubbed-out suppression layer sat in a working tree unnoticed.
    """

    def ignore_file(self, tmp_path, package, rule="GP001"):
        path = tmp_path / "ignore.json"
        path.write_text(
            json.dumps({"ignore": [{
                "package": package, "rule": rule,
                "reason": "documented example, checked by hand",
            }]}),
            encoding="utf-8",
        )
        return path

    def manifest(self, tmp_path):
        path = tmp_path / "requirements.txt"
        path.write_text("ghost-pkg-991-does-not-exist\n", encoding="utf-8")
        return path

    def test_without_the_file_the_scan_blocks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "ghostpkg.scanner.fetch",
            lambda name, ecosystem: PackageFacts(name=name, ecosystem=ecosystem, exists=False),
        )
        assert main(["scan", str(self.manifest(tmp_path)), "--no-cache"]) == 1

    def test_with_the_file_the_verdict_is_downgraded(self, tmp_path, monkeypatch, capsys):
        """Exit 0, and the run says what was suppressed and by which file --
        a silent clean result would be indistinguishable from no protection."""
        monkeypatch.setattr(
            "ghostpkg.scanner.fetch",
            lambda name, ecosystem: PackageFacts(name=name, ecosystem=ecosystem, exists=False),
        )
        config = self.ignore_file(tmp_path, "ghost-pkg-991-does-not-exist")
        code = main([
            "scan", str(self.manifest(tmp_path)), "--no-cache", "--config", str(config),
        ])
        out = capsys.readouterr().out
        assert code == 0
        assert "suppressed" in out
        assert "ignore.json" in out

    def test_a_rule_that_does_not_match_suppresses_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "ghostpkg.scanner.fetch",
            lambda name, ecosystem: PackageFacts(name=name, ecosystem=ecosystem, exists=False),
        )
        config = self.ignore_file(tmp_path, "some-other-package")
        assert main([
            "scan", str(self.manifest(tmp_path)), "--no-cache", "--config", str(config),
        ]) == 1
