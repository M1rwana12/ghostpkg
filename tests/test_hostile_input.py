"""Input that is malformed, unusual, or simply written by a Windows editor.

Every case here was found by throwing hostile input at the parsers rather than
by reading them. The byte-order-mark group is the important one: seven of the
eight supported formats broke on a file saved as "UTF-8 with BOM", and four did
so in silence -- returning nothing, or dropping only the first line while the
tool printed "all packages look fine".
"""

from __future__ import annotations

import json

import pytest

from ghostpkg.assess import exact_pin
from ghostpkg.manifests import load_manifest, parse_package_json, parse_requirements


def names(requirements):
    return [r.name for r in requirements]


#: One sample per format, with how many dependencies it declares.
FORMATS = {
    "requirements.txt": ("requests\nflask\n", 2),
    "pyproject.toml": ("[project]\nname='a'\ndependencies=['requests','flask']\n", 2),
    "package.json": ('{"dependencies":{"react":"^18","vue":"^3"}}', 2),
    "package-lock.json": (
        '{"lockfileVersion":3,"packages":{"node_modules/react":{"version":"18"}}}', 1),
    "poetry.lock": ('[[package]]\nname = "requests"\nversion = "1"\n', 1),
    "uv.lock": (
        '[[package]]\nname = "requests"\nversion = "1"\nsource = { registry = "x" }\n', 1),
    "yarn.lock": ('lodash@^4:\n  version "4"\n', 1),
    "pnpm-lock.yaml": ("packages:\n  react@18.0.0:\n    resolution: {}\n", 1),
    "AGENTS.md": ("pip install flask\n", 1),
}


class TestAByteOrderMark:
    """U+FEFF at the start of a file, written by Notepad, PowerShell's
    `Out-File`, and any editor set to "UTF-8 with BOM"."""

    @pytest.mark.parametrize("filename", sorted(FORMATS))
    def test_the_file_reads_the_same_with_and_without_one(self, tmp_path, filename):
        text, expected = FORMATS[filename]
        with_bom = tmp_path / "bom" / filename
        without = tmp_path / "plain" / filename
        for path, encoding in ((with_bom, "utf-8-sig"), (without, "utf-8")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding=encoding)
        assert len(load_manifest(with_bom)[0]) == expected
        assert len(load_manifest(without)[0]) == expected

    def test_the_silent_case(self, tmp_path):
        """requirements.txt lost only its first line, so the run finished
        successfully having never checked that package."""
        path = tmp_path / "requirements.txt"
        path.write_text("requests\nflask\n", encoding="utf-8-sig")
        assert names(load_manifest(path)[0]) == ["requests", "flask"]

    def test_text_handed_in_directly_is_stripped_too(self):
        """The CLI is not the only entry point; a hook or an editor may read
        the file itself."""
        assert names(parse_requirements("﻿requests\n")) == ["requests"]


class TestNamesThatAreNotNames:
    def test_an_empty_key_is_not_a_package(self):
        """Left in, the npm client requested https://registry.npmjs.org/ --
        the registry root -- and read whatever came back as facts about it."""
        text = json.dumps({"dependencies": {"": "^1", "  ": "^1", "react": "^18"}})
        assert names(parse_package_json(text)) == ["react"]

    def test_surrounding_whitespace_is_trimmed(self):
        text = json.dumps({"dependencies": {" react ": "^18"}})
        assert names(parse_package_json(text)) == ["react"]


class TestVersionSpecifiers:
    @pytest.mark.parametrize(
        "specifier, expected",
        [
            ("==1.2.3", "1.2.3"),
            ("=== 1.2.3", "1.2.3"),
            ("===1.2.3", "1.2.3"),
            ("==1.2.*", None),
            (">=1.0", None),
            ("~=1.2", None),
            ("", None),
        ],
    )
    def test_pypi(self, specifier, expected):
        """`===` is PEP 440 arbitrary equality: a pin like any other, and the
        only way to name a version that does not normalise. It was not
        recognised, so such a pin went unchecked."""
        assert exact_pin(specifier, "pypi") == expected

    def test_an_environment_marker_does_not_hide_the_pin(self):
        """The requirements parser strips markers first, so this is not
        reachable from the CLI -- but the function is public."""
        assert exact_pin('==1.2.3 ; python_version < "3.9"', "pypi") == "1.2.3"

    @pytest.mark.parametrize(
        "specifier, expected",
        [("1.2.3", "1.2.3"), ("v1.2.3", "1.2.3"), ("^1.2.3", None), ("latest", None)],
    )
    def test_npm(self, specifier, expected):
        assert exact_pin(specifier, "npm") == expected


class TestOtherMalformedInput:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("requests\r\nflask\r\n", ["requests", "flask"]),
            ('requests; python_version < "3.9"\n', ["requests"]),
            ("requests[socks]==2.31.0\n", ["requests"]),
            ("-e git+https://x/y#egg=z\nflask\n", ["flask"]),
            ("\trequests\t==\t2.31.0\n", ["requests"]),
            ("requests", ["requests"]),
            ("   \n\t\n", []),
        ],
    )
    def test_requirements_shapes(self, text, expected):
        assert names(parse_requirements(text)) == expected

    @pytest.mark.parametrize(
        "text",
        ['{"dependencies": null}', '{"dependencies": ["a"]}', '{"dependencies": {"a": null}}'],
    )
    def test_package_json_shapes_do_not_crash(self, text):
        parse_package_json(text)

    def test_a_top_level_array_is_refused(self, tmp_path):
        """Refused rather than guessed at -- the CLI turns this into exit 2."""
        path = tmp_path / "package.json"
        path.write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError):
            load_manifest(path)
