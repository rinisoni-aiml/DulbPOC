"""End-to-end on the sample drawing with the rules planner: no API key, no cost."""
import json

import pymupdf
import pytest

from rectify.stages.apply import Rectifier
from rectify.stages.extract import extract
from rectify.stages.plan import plan


@pytest.fixture(scope="module")
def extracted(sample_pdf, tmp_path_factory):
    out = tmp_path_factory.mktemp("run")
    ex, sheet_text = extract(sample_pdf, out)
    return out, ex, sheet_text


def test_extract_reads_all_comments(extracted):
    _, ex, sheet_text = extracted
    assert len(ex["comments"]) == 9
    assert sum(len(c["tips"]) for c in ex["comments"]) == 15
    assert ex["pages"][0]["legend_vis"] is not None
    assert any(s["legend"] and s["text"] == "CARD READER" for s in sheet_text)


def test_rules_plan_and_apply(extracted, sample_pdf):
    out, ex, sheet_text = extracted
    inputs = {"backbox_size": "75x75x50mm"}
    p = plan(ex, sheet_text, out, inputs, mode="rules")
    r = Rectifier(sample_pdf, ex, inputs)
    changes, failures = r.run(p)
    r.save(out / "corrected.pdf")

    assert not failures
    assert {c["op"] for c in changes} == {"replace_text", "swap_symbol", "add_conduit_fill_table"}
    page = pymupdf.open(out / "corrected.pdf")[0]
    assert not list(page.annots())
    assert page.search_for("CONFIGURATION TYPE")
    assert not page.search_for("LOCK TYPE")
    assert page.search_for("75x75x50mm BACKBOX")
    json.dumps(changes)  # serialisable for changes.json
