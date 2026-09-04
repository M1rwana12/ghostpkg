"""Lines that parse as a package name but are not one.

Found by scanning fourteen Python repositories. The requirements parser had no
test for "is this actually a requirements file" beyond the file's name, and a
PEP 508 name is permissive enough that a checksum or a file path sails through
it. Every case here produced a block, and a block fails the build.
"""

from __future__ import annotations

import pytest

from ghostpkg.manifests import parse_requirements
from ghostpkg.registries import PackageFacts, normalise_version


def names(text):
    return [r.name for r in parse_requirements(text)]


class TestAChecksumIsNotAPackage:
    """Airflow keeps 127 files under `dev/breeze/doc/images/` whose entire
    content is one MD5, used to detect when a `--help` screen changed. Five of
    them carry `constraints` or `requirements` in the filename, so they were
    classified as pip files -- and a 32-character hex string is a legal PEP 508
    name."""

    @pytest.mark.parametrize(
        "digest",
        [
            "ab2a920a9c7ec445e7edd70051af73e1",
            "9997441e5cb12ba360db1756028718d3",
            "5fb85915e160fde4096b70483e22e17b",
            "a" * 40,
            "0" * 64,
            "AB2A920A9C7EC445E7EDD70051AF73E1",
        ],
    )
    def test_a_bare_digest_is_ignored(self, digest):
        assert names(digest + "\n") == []

    @pytest.mark.parametrize("name", ["cachetools", "attrs", "beautifulsoup4", "aiodns"])
    def test_ordinary_names_are_untouched(self, name):
        assert names(name + "\n") == [name]

    def test_a_hex_looking_name_with_a_version_is_still_read(self):
        """The guard is for a line that is *only* a digest. A package pinned to
        a version is a requirement whatever its name looks like."""
        assert names("abcdef0123456789abcdef0123456789==1.0\n") == [
            "abcdef0123456789abcdef0123456789"
        ]


class TestAFileNameIsNotAPackage:
    """Git materialises a symlink as a plain file holding its target on a
    Windows checkout without symlink support. Ray's
    `requirements_compiled_py3.10.txt` therefore contained the single line
    `requirements_compiled.txt`, which was read as a package name. Two failures
    at once: a false block, and the thousand real pins behind the link never
    checked."""

    @pytest.mark.parametrize("line", ["requirements_compiled.txt", "base.in", "reqs.txt"])
    def test_a_file_name_is_ignored(self, line):
        assert names(line + "\n") == []

    @pytest.mark.parametrize(
        "name",
        ["zope.interface", "backports.zoneinfo", "ruamel.yaml", "ruamel.yaml.clib", "pytest.ini"],
    )
    def test_a_dotted_package_name_is_still_read(self, name):
        """All five of these are published packages. A wider extension list
        looked tidier and would have dropped three of them -- turning a false
        block into a silent miss, which is the worse trade."""
        assert names(name + "\n") == [name]

    def test_a_file_name_with_a_version_is_still_read(self):
        """The guard is for a bare line. Anything pinned is a requirement."""
        assert names("odd.txt==1.0\n") == ["odd.txt"]


class TestLocalVersionIdentifiers:
    """PyPI refuses an upload whose version carries a local segment, so
    `2.9.0+cu128` can never appear in a release list. Ray pins every CUDA build
    that way, and comparing with the segment attached blocked `torch`,
    `torchvision`, `torchaudio`, `torch-scatter` and `torch-sparse`."""

    @pytest.mark.parametrize(
        "pinned, published",
        [
            ("2.9.0+cu128", "2.9.0"),
            ("0.24.0+cu128", "0.24.0"),
            ("2.1.2+cu.12.8.torch.2.9", "2.1.2"),
            ("0.6.18+pt29cu128", "0.6.18"),
            ("0.6.18+pt29cpu", "0.6.18"),
            ("2.7.0+cu128", "2.7.0"),
        ],
    )
    def test_the_local_segment_is_dropped(self, pinned, published):
        assert normalise_version(pinned) == normalise_version(published)

    def test_a_local_pin_matches_the_published_release(self):
        facts = PackageFacts(
            name="torch", ecosystem="pypi", exists=True, versions=("2.9.0",)
        )
        assert facts.has_version("2.9.0+cu128") is True

    def test_a_wrong_base_version_still_fails(self):
        """Dropping the local part must not turn a bad pin into a good one."""
        facts = PackageFacts(
            name="torch", ecosystem="pypi", exists=True, versions=("2.9.0",)
        )
        assert facts.has_version("9.9.9+cu128") is False


class TestTheTimeoutFlagIsHonestAboutZero:
    def test_zero_is_refused_rather_than_ignored(self):
        from ghostpkg.cli import main

        assert main(["check", "requests", "--timeout", "0"]) == 2

    def test_a_negative_is_refused(self):
        from ghostpkg.cli import main

        assert main(["check", "requests", "--timeout", "-5"]) == 2
