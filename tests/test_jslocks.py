"""`pnpm-lock.yaml` and `yarn.lock`.

Every shape here was taken from a real lockfile rather than from the format
documentation -- React's classic `yarn.lock`, Babel's and Jest's berry ones, and
the v9 `pnpm-lock.yaml` of Svelte, Vue and Vite. Together those six parse to
3,292 names and produce zero blocks, which is the measurement that matters:
these are all published packages, so any block would be a false one.
"""

from __future__ import annotations

import pytest

from ghostpkg.jslocks import parse_pnpm_lock, parse_yarn_lock


def names(requirements):
    return [r.name for r in requirements]


class TestPnpmLock:
    def test_v9_keys(self):
        text = (
            "lockfileVersion: '9.0'\n\n"
            "packages:\n\n"
            "  '@babel/parser@7.29.3':\n"
            "    resolution: {integrity: sha512-abc}\n\n"
            "  react@18.2.0:\n"
            "    resolution: {integrity: sha512-def}\n"
        )
        assert names(parse_pnpm_lock(text)) == ["@babel/parser", "react"]

    def test_a_blank_line_does_not_end_the_block(self):
        """`''[:1].isspace()` is False, so a blank line read as a column-zero
        key and closed `packages:` at the first gap between entries. Every real
        lockfile has one there, and the parser returned nothing at all for all
        three files it was tested against."""
        text = "packages:\n\n  react@18.2.0:\n    resolution: {}\n\n  vue@3.4.0:\n    resolution: {}\n"
        assert names(parse_pnpm_lock(text)) == ["react", "vue"]

    def test_v9_peer_resolutions_are_stripped(self):
        """Real key: `'@conventional-changelog/git-client@3.1.0(conventional-commits-filter@6.0.1)'`."""
        text = (
            "packages:\n"
            "  '@conventional-changelog/git-client@3.1.0(conventional-commits-filter@6.0.1)':\n"
            "    resolution: {}\n"
        )
        assert names(parse_pnpm_lock(text)) == ["@conventional-changelog/git-client"]

    def test_v6_slash_prefixed_at_keys(self):
        text = "packages:\n  /react@18.2.0:\n    resolution: {}\n  /@babel/core@7.0.0:\n    resolution: {}\n"
        assert names(parse_pnpm_lock(text)) == ["react", "@babel/core"]

    def test_v5_slash_separated_versions(self):
        text = "packages:\n  /react/18.2.0:\n    resolution: {}\n  /@babel/code-frame/7.12.11:\n    resolution: {}\n"
        assert names(parse_pnpm_lock(text)) == ["react", "@babel/code-frame"]

    def test_v5_peer_suffix_after_underscore(self):
        text = "packages:\n  /react-dom@18.2.0_react@18.2.0:\n    resolution: {}\n"
        assert names(parse_pnpm_lock(text)) == ["react-dom"]

    def test_underscores_in_real_names_survive(self):
        """`string_decoder` and `@types/babel__core` are published packages."""
        text = (
            "packages:\n  string_decoder@1.3.0:\n    resolution: {}\n"
            "  '@types/babel__core@7.20.5':\n    resolution: {}\n"
        )
        assert names(parse_pnpm_lock(text)) == ["string_decoder", "@types/babel__core"]

    def test_the_block_ends_at_the_next_top_level_key(self):
        """v9 writes a `snapshots:` section after `packages:` listing the same
        names again."""
        text = (
            "packages:\n  react@18.2.0:\n    resolution: {}\n\n"
            "snapshots:\n  vue@3.4.0:\n    dependencies: {}\n"
        )
        assert names(parse_pnpm_lock(text)) == ["react"]

    def test_other_top_level_blocks_are_ignored(self):
        text = (
            "importers:\n  .:\n    dependencies:\n      react:\n        specifier: ^18\n\n"
            "packages:\n  vue@3.4.0:\n    resolution: {}\n"
        )
        assert names(parse_pnpm_lock(text)) == ["vue"]

    @pytest.mark.parametrize(
        "key",
        ["file:packages/ui", "link:../shared", "https://example.com/x.tgz", "git+ssh://git@github.com/a/b"],
    )
    def test_a_stated_source_is_skipped(self, key):
        text = f"packages:\n  {key}:\n    resolution: {{}}\n"
        assert names(parse_pnpm_lock(text)) == []

    def test_an_empty_file_is_not_a_crash(self):
        assert parse_pnpm_lock("") == []


class TestYarnClassic:
    def test_one_entry(self):
        text = '# yarn lockfile v1\n\nlodash@^4.17.19:\n  version "4.17.21"\n'
        assert names(parse_yarn_lock(text)) == ["lodash"]

    def test_several_descriptors_on_one_key(self):
        """Real line: `"@ampproject/remapping@^2.1.0", "@ampproject/remapping@^2.2.0":`"""
        text = '"@ampproject/remapping@^2.1.0", "@ampproject/remapping@^2.2.0":\n  version "2.3.0"\n'
        assert names(parse_yarn_lock(text)) == ["@ampproject/remapping"]

    def test_a_scoped_name_keeps_its_scope(self):
        text = '"@babel/code-frame@7.12.11":\n  version "7.12.11"\n'
        assert names(parse_yarn_lock(text)) == ["@babel/code-frame"]

    def test_a_link_protocol_is_skipped(self):
        """Real entry from React's lockfile."""
        text = '"eslint-plugin-react-internal@link:./scripts/eslint-rules":\n  version "0.0.0"\n'
        assert names(parse_yarn_lock(text)) == []

    def test_indented_body_lines_are_not_entries(self):
        text = 'lodash@^4:\n  version "4.17.21"\n  dependencies:\n    other "^1.0.0"\n'
        assert names(parse_yarn_lock(text)) == ["lodash"]

    def test_comments_are_skipped(self):
        text = "# THIS IS AN AUTOGENERATED FILE.\n# yarn lockfile v1\n\nlodash@^4:\n  version \"4.17.21\"\n"
        assert names(parse_yarn_lock(text)) == ["lodash"]


class TestYarnBerry:
    def test_npm_protocol_is_an_ordinary_range(self):
        text = '"@actions/github@npm:9.1.1":\n  version: 9.1.1\n'
        assert names(parse_yarn_lock(text)) == ["@actions/github"]

    def test_a_caret_range_is_not_read_as_an_alias(self):
        text = '"lodash@npm:^4.17.19":\n  version: 4.17.21\n'
        assert names(parse_yarn_lock(text)) == ["lodash"]

    def test_an_alias_is_checked_under_the_installed_name(self):
        """Real entry from Babel's lockfile: the key is a private alias, and
        `@babel/cli` is what actually gets fetched."""
        text = '"@babel-baseline/cli@npm:@babel/cli@7.27.1":\n  version: 7.27.1\n'
        assert names(parse_yarn_lock(text)) == ["@babel/cli"]

    @pytest.mark.parametrize(
        "descriptor",
        [
            '"@babel/benchmark@workspace:benchmark"',
            '"$repo-utils@link:../scripts/repo-utils::locator=x"',
            '"thing@patch:thing@npm%3A1.0.0#./patch.diff"',
            '"thing@portal:../thing"',
            '"thing@exec:./generate.js"',
            '"thing@https://example.com/x.tgz"',
        ],
    )
    def test_every_non_registry_protocol_is_skipped(self, descriptor):
        assert names(parse_yarn_lock(f"{descriptor}:\n  version: 1.0.0\n")) == []

    def test_metadata_is_not_a_package(self):
        text = "__metadata:\n  version: 6\n  cacheKey: 8\n\nlodash@npm:^4:\n  version: 4.17.21\n"
        assert names(parse_yarn_lock(text)) == ["lodash"]


class TestDispatch:
    @pytest.mark.parametrize("filename", ["pnpm-lock.yaml", "yarn.lock"])
    def test_the_file_is_recognised_as_an_npm_manifest(self, tmp_path, filename):
        from ghostpkg.manifests import load_manifest

        path = tmp_path / filename
        if filename == "yarn.lock":
            path.write_text('lodash@^4:\n  version "4.17.21"\n', encoding="utf-8")
        else:
            path.write_text("packages:\n  lodash@4.17.21:\n    resolution: {}\n", encoding="utf-8")
        found, ecosystem = load_manifest(path)
        assert (names(found), ecosystem) == (["lodash"], "npm")
