"""Text the rectifier adds must extract cleanly (copy/search in Acrobat), hyphens included."""
import pymupdf

from rectify.pipeline.apply import Rectifier
from rectify.pipeline.extract import extract


def test_note_hyphens_are_searchable(sample_pdf, tmp_path):
    ex, _ = extract(sample_pdf, tmp_path)
    r = Rectifier(sample_pdf, ex)
    c = next(c for c in ex["comments"] if c["tips"])
    r.add_note(c, {"type": "add_note", "text": "REFER TO RFI ECJ-RFI-001632 FOR DEVICE ELEVATIONS"})
    r.save(tmp_path / "note.pdf")
    page = pymupdf.open(tmp_path / "note.pdf")[0]
    assert page.search_for("ECJ-RFI-001632")
    assert "\xad" not in page.get_text()


def test_checklist_cap_constant():
    from rectify.core import config
    assert config.CHECKLIST_MAX == 3
