"""Output formats.

The GitHub one exists so that a pull request is annotated on the offending
line instead of the reader scrolling a job log, which is the only form of this
tool's output anyone reads twice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghostpkg.assess import Finding, Verdict
from ghostpkg.report import as_github, as_json
from ghostpkg.rules import Reason


def finding(**kwargs):
    base = dict(name="thing", ecosystem="pypi", verdict=Verdict.BLOCK, reasons=[])
    base.update(kwargs)
    return Finding(**base)


class TestGithubAnnotations:
    def test_a_block_is_an_error_on_its_line(self):
        f = finding(
            reasons=[Reason("GP001", "does not exist on pypi")],
            source="requirements.txt",
            line=2,
        )
        assert as_github([f]) == (
            "::error file=requirements.txt,line=2,title=ghostpkg GP001::"
            "thing: does not exist on pypi"
        )

    def test_a_warning_is_a_warning(self):
        """The annotation and the exit code have to agree about severity, or a
        green job full of red annotations teaches people to ignore both."""
        f = finding(
            verdict=Verdict.WARN,
            reasons=[Reason("GP003", "first published 3 days ago")],
            source="a.txt",
            line=1,
        )
        assert as_github([f]).startswith("::warning ")

    def test_an_unchecked_name_is_an_error_not_a_warning(self):
        f = finding(verdict=Verdict.ERROR, reasons=[Reason("GP000", "could not check")])
        assert as_github([f]).startswith("::error ")

    def test_passing_packages_produce_nothing(self):
        assert as_github([finding(verdict=Verdict.OK)]) == ""

    def test_no_findings_at_all(self):
        assert as_github([]) == ""

    def test_a_path_is_made_relative_to_the_workspace(self, tmp_path):
        (tmp_path / "sub").mkdir()
        path = tmp_path / "sub" / "requirements.txt"
        path.write_text("x\n", encoding="utf-8")
        f = finding(reasons=[Reason("GP001", "gone")], source=str(path), line=1)
        assert "file=sub/requirements.txt," in as_github([f], root=tmp_path)

    def test_a_path_outside_the_workspace_is_left_alone(self, tmp_path):
        f = finding(reasons=[Reason("GP001", "gone")], source="/elsewhere/a.txt")
        assert "file=" in as_github([f], root=tmp_path)

    def test_a_finding_with_no_file_still_appears(self):
        """`ghostpkg check name` has no file behind it, and dropping the
        annotation would hide the finding entirely."""
        out = as_github([finding(reasons=[Reason("GP001", "gone")])])
        assert out.startswith("::error ") and "file=" not in out

    def test_a_line_is_omitted_when_unknown(self):
        f = finding(reasons=[Reason("GP001", "gone")], source="package.json")
        out = as_github([f])
        assert "file=package.json," in out and "line=" not in out

    def test_several_reasons_are_joined(self):
        f = finding(
            reasons=[Reason("GP003", "brand new"), Reason("GP005", "no repository")],
            source="a.txt",
            line=1,
        )
        out = as_github([f])
        # Escaped, because a raw comma separates properties. GitHub decodes it
        # again, so the annotation title reads "ghostpkg GP003,GP005".
        assert "title=ghostpkg GP003%2CGP005" in out
        assert "thing: brand new; no repository" in out

    @pytest.mark.parametrize(
        "text, encoded",
        [("a\nb", "a%0Ab"), ("100%", "100%25"), ("a\rb", "a%0Db")],
    )
    def test_the_message_is_escaped(self, text, encoded):
        """A raw newline ends the workflow command, so the rest of the message
        would be printed as log output and the annotation truncated."""
        out = as_github([finding(reasons=[Reason("GP001", text)])])
        assert encoded in out

    def test_a_comma_in_a_property_is_escaped(self):
        """`,` separates properties, so an unescaped one in a path silently
        splits the annotation."""
        f = finding(reasons=[Reason("GP001", "gone")], source="odd,name.txt")
        assert "file=odd%2Cname.txt" in as_github([f])

    def test_one_line_per_finding(self):
        findings = [
            finding(name="a", reasons=[Reason("GP001", "gone")]),
            finding(name="b", reasons=[Reason("GP001", "gone")]),
            finding(name="c", verdict=Verdict.OK),
        ]
        assert len(as_github(findings).splitlines()) == 2


class TestJsonCarriesTheOrigin:
    def test_source_and_line_are_included(self):
        f = finding(reasons=[Reason("GP001", "gone")], source="requirements.txt", line=7)
        entry = json.loads(as_json([f]))["findings"][0]
        assert (entry["source"], entry["line"]) == ("requirements.txt", 7)

    def test_they_are_null_when_unknown(self):
        entry = json.loads(as_json([finding()]))["findings"][0]
        assert entry["source"] is None and entry["line"] is None


class TestTheSameNameInTwoFiles:
    """One lookup, but a finding per place it was written -- otherwise a name
    listed twice reports whichever file happened to be looked up."""

    def test_each_occurrence_keeps_its_own_origin(self, monkeypatch):
        from ghostpkg.manifests import Requirement
        from ghostpkg.registries import PackageFacts
        from ghostpkg.scanner import evaluate

        monkeypatch.setattr(
            "ghostpkg.scanner.fetch",
            lambda name, ecosystem: PackageFacts(name=name, ecosystem=ecosystem, exists=False),
        )
        findings = evaluate(
            [
                Requirement(name="ghost", source="a.txt", line=1),
                Requirement(name="ghost", source="b.txt", line=9),
            ],
            "pypi",
            cache=None,
        )
        assert [(f.source, f.line) for f in findings] == [("a.txt", 1), ("b.txt", 9)]
        assert all(f.verdict is Verdict.BLOCK for f in findings)
