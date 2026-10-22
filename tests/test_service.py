"""The service layer used by the CLI and the web app, with the rules planner (no API cost)."""
import pytest

from rectify.services import rectification as svc


def test_rectify_rules(sample_pdf, tmp_path):
    seen = []
    r = svc.rectify(sample_pdf, tmp_path, {"backbox_size": "75x75x50mm"}, "rules",
                    progress=lambda stage, _: seen.append(stage))
    assert seen == ["extract", "plan", "apply", "report"]
    assert (r["extract"]["comments"], r["extract"]["tips"]) == (9, 15)
    assert r["plan"]["planner"] == "rules" and r["plan"]["usage"] is None
    assert r["apply"]["edits"] == 5 and r["apply"]["markups_removed"] == 24
    assert r["apply"]["failures"] == 2  # waiting for inputs: architectural drawing, device heights
    assert r["apply"]["missing_elevations"] > 0
    assert sum(r["report"]["resolution"].values()) == 9
    for name in ("corrected.pdf", "corrected_highlighted.pdf", "report.html", "checklist.md", "overview.png"):
        assert (tmp_path / name).exists()


def test_llm_without_key_is_a_clean_error(sample_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(svc.config, "ANTHROPIC_API_KEY", "")
    svc.extract_drawing(sample_pdf, tmp_path)
    with pytest.raises(svc.RectifyError):
        svc.plan_fixes(tmp_path, {}, "llm")
