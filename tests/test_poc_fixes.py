"""Missing elevations, space IDs from a reference drawing, conduit checks, resolution status. Offline."""
import pymupdf
import pytest

from rectify.domain.conduit import assumptions, conduit_fill
from rectify.pdf.geometry import open_clean, to_pdf
from rectify.pipeline.apply import Rectifier
from rectify.pipeline.extract import extract
from rectify.pipeline.report import resolution


@pytest.fixture(scope="module")
def ex(sample_pdf, tmp_path_factory):
    out = tmp_path_factory.mktemp("fixes")
    return extract(sample_pdf, out)[0]


def _comment(ex):
    return next(c for c in ex["comments"] if c["tips"])


def test_missing_elevations_marked_without_heights(sample_pdf, ex):
    r = Rectifier(sample_pdf, ex)
    c = _comment(ex)
    r.mark_missing_elevations(c, {"type": "mark_missing_elevations"})
    missing = [f for f in r.findings if f["kind"] == "missing_elevation"]
    assert missing, "the sample has devices without an elevation label"
    assert all(not f["added"] for f in missing)
    assert not r.changes  # nothing drawn without heights
    assert r.failures[-1]["kind"] == "input"
    assert all("/00/" not in f["tag"] for f in missing)  # tag-format key example is not a device


def test_missing_elevations_added_from_inputs(sample_pdf, ex, tmp_path):
    probe = Rectifier(sample_pdf, ex)
    c = _comment(ex)
    probe.mark_missing_elevations(c, {"type": "mark_missing_elevations"})
    types = {f["type"] for f in probe.findings}
    r = Rectifier(sample_pdf, ex, {"elevations": {t: "1234" for t in types}})
    r.mark_missing_elevations(c, {"type": "mark_missing_elevations"})
    assert r.changes and r.changes[0]["items_pdf"]
    r.save(tmp_path / "elev.pdf")
    assert pymupdf.open(tmp_path / "elev.pdf")[0].search_for("H=1234mm")


def test_space_ids_copied_from_reference(sample_pdf, ex, tmp_path):
    # A stand-in reference: the sample sheet plus one room label at a known spot.
    ref, _ = open_clean(sample_pdf)
    page = ref[0]
    spot = pymupdf.Point(1200, 900)  # vis
    page.insert_text(to_pdf(page, spot), "TEST ROOM", fontsize=8, rotate=page.rotation)
    page.insert_text(to_pdf(page, spot + (0, 10)), "M.B1.999", fontsize=8, rotate=page.rotation)
    ref.save(tmp_path / "ref.pdf")

    r = Rectifier(sample_pdf, ex, {"reference_pdf": str(tmp_path / "ref.pdf")})
    r.place_space_ids(_comment(ex), {"type": "place_space_ids"})
    assert r.changes, r.failures
    r.save(tmp_path / "ids.pdf")
    out = pymupdf.open(tmp_path / "ids.pdf")[0]
    hit = out.search_for("M.B1.999")
    assert hit
    vis = hit[0] * out.rotation_matrix
    assert abs(vis.x0 - spot.x) < 15 and abs(vis.y1 - (spot.y + 10)) < 15


def test_space_ids_without_reference_is_input_wait(sample_pdf, ex):
    r = Rectifier(sample_pdf, ex)
    r.place_space_ids(_comment(ex), {"type": "place_space_ids"})
    assert r.failures[-1]["kind"] == "input"
    assert "reference" in r.failures[-1]["inputs_needed"][0]


def test_conduit_check_and_assumptions():
    rows, base = conduit_fill([{"cable_type": "A", "outer_dia_mm": 6.5}, {"cable_type": "B", "outer_dia_mm": 12}])
    assert rows[0]["Check"].startswith("OK")
    assert rows[1]["Check"] == "EXCEEDS"  # 113 mm2 in a 219 mm2 conduit = 51.6%
    text = " ".join(assumptions(base))
    assert "Assumed" in text and "219" in text and "40%" in text


def test_resolution_statuses():
    ex = {"comments": [{"id": f"C-0{i}"} for i in range(1, 5)]}
    plan = {"comments": [{"comment_id": "C-02", "checklist": [{"item": "stamp", "inputs_needed": ["x"]}]},
                         {"comment_id": "C-04", "checklist": [{"item": "move reader", "inputs_needed": ["IFC"]}]}]}
    changes = [{"id": "CH-01", "comment_id": "C-01"}, {"id": "CH-02", "comment_id": "C-02"}]
    failures = [{"comment_id": "C-03", "op": "place_space_ids", "reason": "no ref", "kind": "input",
                 "inputs_needed": ["architectural drawing"]}]
    st = {r["comment_id"]: r["status"] for r in resolution(ex, plan, changes, failures, [])}
    assert st == {"C-01": "resolved", "C-02": "partially_resolved", "C-03": "ready_to_fix",
                  "C-04": "needs_engineer"}
