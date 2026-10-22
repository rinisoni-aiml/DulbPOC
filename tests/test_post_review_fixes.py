"""Revision bump, conduit size from the sheet, input keys/suggestions for comments waiting on inputs."""
import pymupdf
import pytest

from rectify.domain.conduit import sheet_conduit
from rectify.pdf.geometry import open_clean, text_lines
from rectify.pipeline.apply import Rectifier
from rectify.pipeline.extract import extract
from rectify.pipeline.report import resolution


@pytest.fixture(scope="module")
def ex(sample_pdf, tmp_path_factory):
    return extract(sample_pdf, tmp_path_factory.mktemp("post"))[0]


def test_conduit_size_read_from_sheet(sample_pdf):
    doc, _ = open_clean(sample_pdf)
    spec, source = sheet_conduit(text_lines(doc[0]))
    assert spec == {"size_mm": 25, "area_mm2": 346.4}
    assert "sheet" in source


def test_revision_bump(sample_pdf, ex, tmp_path):
    r = Rectifier(sample_pdf, ex, {"revision": {"date": "07/02/26"}})
    c = next(c for c in ex["comments"] if c["tips"])
    r.add_note(c, {"type": "add_note", "text": "REFER TO RFI ECJ-RFI-001632 FOR DEVICE ELEVATIONS"})
    r.bump_revision()
    rev = [ch for ch in r.changes if ch["op"] == "bump_revision"]
    assert rev and rev[0]["before"] == "01" and rev[0]["after"] == "02"
    r.save(tmp_path / "rev.pdf")
    words = [w[4] for w in pymupdf.open(tmp_path / "rev.pdf")[0].get_text("words")]
    assert "07/02/26" in words and "07/02/2026" in words  # table row (dd/mm/yy) and title block (dd/mm/yyyy)
    assert "20/12/2025" not in words


def test_no_revision_bump_without_edits_or_when_disabled(sample_pdf, ex):
    r = Rectifier(sample_pdf, ex)
    r.bump_revision()
    assert not r.changes
    r = Rectifier(sample_pdf, ex, {"revision": {"bump": False}})
    c = next(c for c in ex["comments"] if c["tips"])
    r.add_note(c, {"type": "add_note", "text": "TEST NOTE"})
    r.bump_revision()
    assert all(ch["op"] != "bump_revision" for ch in r.changes)


def test_elevation_suggestions_and_input_keys(sample_pdf, ex):
    r = Rectifier(sample_pdf, ex)
    c = next(c for c in ex["comments"] if c["tips"])
    r.mark_missing_elevations(c, {"type": "mark_missing_elevations"})
    f = r.failures[-1]
    assert f["input_keys"] == ["elevations"]
    assert f["suggestions"]["elevations"]  # e.g. {"L1.2": "1200"}: hints only, nothing drawn
    assert not r.changes
    res = resolution({"comments": [c]}, {"comments": []}, [], r.failures, [])
    assert res[0]["status"] == "ready_to_fix"
    assert res[0]["input_keys"] == ["elevations"] and res[0]["suggestions"]["elevations"]
