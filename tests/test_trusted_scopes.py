"""A scope the project already leans on is not a stranger.

268 warnings across four popular repositories was the last barrier a reviewer
named: almost all of them "published under a year ago" on the dozen platform
binaries a compiled tool ships at once -- `@oxfmt/binding-darwin-arm64`,
`@oxc-parser/binding-linux-riscv64-musl`.

The justification is structural rather than statistical: **an npm scope is
owned**. To publish `@oxfmt/anything` you must control `@oxfmt`. So a young
package in a scope the scan already depends on three times is the same
publisher shipping another build.

Measured before it was built. Two members silences 41% of warnings, three
silences 40%, six silences 39% -- it saturates at once, so the threshold sits
where it is defensible rather than where it is largest. In practice: Sentry 114
warnings to 22, Vue 15 to 5, and no change at all to the block count.
"""

from __future__ import annotations

import pytest

from ghostpkg.assess import TRUSTED_SCOPE_MEMBERS, Verdict, assess, trusted_scopes
from ghostpkg.registries import PackageFacts


def young(name, **kwargs):
    base = dict(
        name=name, ecosystem="npm", exists=True,
        age_days=40, release_count=1, has_repo_url=False,
    )
    base.update(kwargs)
    return PackageFacts(**base)


class TestWhichScopesAreTrusted:
    def test_a_scope_seen_often_enough(self):
        names = [f"@oxfmt/binding-{i}" for i in range(TRUSTED_SCOPE_MEMBERS)]
        assert trusted_scopes(names) == {"@oxfmt"}

    def test_one_short_is_not_enough(self):
        names = [f"@oxfmt/binding-{i}" for i in range(TRUSTED_SCOPE_MEMBERS - 1)]
        assert trusted_scopes(names) == set()

    def test_a_squatted_scope_appears_once(self):
        """`@types-node` is not `@types/node`. A squatted scope shows up a
        single time in a scan, which is exactly what the threshold separates."""
        names = ["@types/node", "@types/react", "@types/jest", "@types-node/core"]
        assert trusted_scopes(names) == {"@types"}

    def test_unscoped_names_never_count(self):
        assert trusted_scopes(["react", "vue", "lodash", "express"]) == set()

    def test_scopes_are_counted_separately(self):
        names = ["@a/one", "@a/two", "@a/three", "@b/one", "@b/two"]
        assert trusted_scopes(names) == {"@a"}


class TestWhatBeingTrustedChanges:
    def test_the_soft_signals_are_dropped(self):
        finding = assess(young("@oxfmt/binding-darwin-arm64"), known_scope=True)
        assert finding.verdict is Verdict.OK
        assert finding.reasons == []

    def test_the_same_package_outside_a_trusted_scope_is_warned_about(self):
        finding = assess(young("@oxfmt/binding-darwin-arm64"), known_scope=False)
        assert finding.verdict is Verdict.WARN
        assert len(finding.reasons) == 3

    def test_a_missing_package_is_still_blocked(self):
        """Nothing that blocks is affected. A trusted scope does not make a
        name that does not exist acceptable."""
        facts = PackageFacts(name="@oxfmt/ghost", ecosystem="npm", exists=False)
        assert assess(facts, known_scope=True).verdict is Verdict.BLOCK

    def test_a_bad_pin_is_still_blocked(self):
        facts = young("@oxfmt/binding-x", versions=("1.0.0",), latest_version="1.0.0")
        finding = assess(facts, specifier="99.9.9", known_scope=True)
        assert finding.verdict is Verdict.BLOCK

    def test_a_confiscated_name_is_still_blocked(self):
        facts = young("@oxfmt/binding-x", security_hold=True)
        assert assess(facts, known_scope=True).verdict is Verdict.BLOCK

    def test_install_signals_still_block_a_young_package(self):
        """`--deep` findings are evidence about the package itself, not a guess
        from its age, so the scope says nothing about them."""
        from ghostpkg.inspection import Signal

        signals = [Signal(kind="pipe-to-shell", detail="curl | sh", where="package.json")]
        finding = assess(young("@oxfmt/binding-x"), signals=signals, known_scope=True)
        assert finding.verdict is Verdict.BLOCK


class TestEndToEnd:
    def test_a_family_of_binaries_produces_no_warnings(self, tmp_path, monkeypatch):
        import json

        from ghostpkg.cli import main

        monkeypatch.setattr(
            "ghostpkg.scanner.fetch",
            lambda name, ecosystem: young(name, ecosystem=ecosystem),
        )
        deps = {f"@oxfmt/binding-{p}": "1.0.0" for p in ("darwin-arm64", "linux-x64", "win32-x64")}
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "app", "dependencies": deps}), encoding="utf-8"
        )
        assert main(["scan", str(tmp_path / "package.json"), "--no-cache"]) == 0

    def test_a_lone_scoped_newcomer_is_still_warned_about(self, tmp_path, monkeypatch, capsys):
        import json

        from ghostpkg.cli import main

        monkeypatch.setattr(
            "ghostpkg.scanner.fetch",
            lambda name, ecosystem: young(name, ecosystem=ecosystem),
        )
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "app", "dependencies": {"@stranger/thing": "1.0.0"}}),
            encoding="utf-8",
        )
        main(["scan", str(tmp_path / "package.json"), "--no-cache"])
        assert "WARNING" in capsys.readouterr().out

    def test_strict_still_fails_on_a_lone_newcomer(self, tmp_path, monkeypatch):
        import json

        from ghostpkg.cli import main

        monkeypatch.setattr(
            "ghostpkg.scanner.fetch",
            lambda name, ecosystem: young(name, ecosystem=ecosystem),
        )
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "app", "dependencies": {"@stranger/thing": "1.0.0"}}),
            encoding="utf-8",
        )
        assert main(["scan", str(tmp_path / "package.json"), "--strict", "--no-cache"]) == 1
