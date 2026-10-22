"""Run the pipeline stages and return plain result dicts. Used by the CLI and the web app.

Each stage reads the previous stage's JSON from the output folder, so stages can run one at a time
(an engineer can edit plan.json by hand and re-run `apply_fixes` alone).
"""
import json
import time
from pathlib import Path

from ..core import config


class RectifyError(RuntimeError):
    """A run cannot continue (for example: LLM planner asked for but no API key)."""


def output_dir(pdf, out=None):
    return Path(out) if out else config.OUT_DIR / Path(pdf).stem


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_inputs(path, reference=None):
    """Engineer inputs JSON; `reference` (architectural drawing with space IDs) overrides reference_pdf."""
    inputs = load_json(path) if path else {}
    if reference:
        inputs["reference_pdf"] = str(reference)
    return inputs


def extract_drawing(pdf, out):
    from ..pipeline.extract import extract
    out = Path(out)
    ex, _ = extract(Path(pdf), out)
    return {"comments": len(ex["comments"]), "tips": sum(len(c["tips"]) for c in ex["comments"]),
            "other_markups": len(ex["other_markups"]), "path": out / "comments.json"}


def plan_fixes(out, inputs=None, planner="auto"):
    from ..pipeline.plan import plan
    out = Path(out)
    try:
        p = plan(load_json(out / "comments.json"), load_json(out / "sheet_text.json"), out, inputs or {}, planner)
    except SystemExit as e:
        raise RectifyError(str(e)) from None
    return {"planner": p["planner"], "usage": p.get("usage"),
            "ops": sum(len(c["ops"]) for c in p["comments"]),
            "checklist_items": sum(len(c["checklist"]) for c in p["comments"]),
            "warnings": sorted({c["intent"] for c in p["comments"] if c["intent"].startswith("Planner ")}),
            "path": out / "plan.json"}


def apply_fixes(pdf, out, inputs=None):
    """Apply plan.json to the drawing, then build the report. Returns the apply and report summaries."""
    from ..pipeline.apply import Rectifier
    from ..pipeline.report import build
    out = Path(out)
    ex, p = load_json(out / "comments.json"), load_json(out / "plan.json")
    r = Rectifier(Path(pdf), ex, inputs or {})
    changes, failures = r.run(p)
    r.save(out / "corrected.pdf")
    missing = [f for f in r.findings if f["kind"] == "missing_elevation" and not f.get("added")]
    applied = {"edits": len(changes), "skipped": len(r.skipped), "failures": len(failures),
               "markups_removed": r.markups_removed, "missing_elevations": len(missing),
               "path": out / "corrected.pdf"}
    integ, checklist = build(Path(pdf), out, ex, p, changes, failures, r.markups_removed, r.skipped, r.findings)
    res = load_json(out / "changes.json").get("resolution", [])
    report = {"checklist_items": len(checklist), "integrity": integ, "path": out / "report.html",
              "resolution": {k: sum(1 for x in res if x["status"] == k)
                             for k in ("resolved", "partially_resolved", "ready_to_fix", "needs_engineer")}}
    return applied, report


def verify_against(pdf, revised, out):
    from ..pipeline.verify import verify
    out = Path(out)
    res = verify(Path(pdf), Path(revised) if revised else None, out)
    s = res["summary"]
    return {"fixed": s.get("Fixed", 0), "partial": s.get("Partially fixed", 0), "not_fixed": s.get("Not fixed", 0),
            "checks_passed": sum(c["ok"] for c in res["checks"]), "checks": len(res["checks"]),
            "summary": s, "path": out / "verification_report.html"}


def rectify(pdf, out, inputs=None, planner="auto", progress=None):
    """Full run: extract -> plan -> apply -> report. `progress(stage, result)` is called after each stage."""
    progress = progress or (lambda stage, result: None)
    t = time.time()
    extracted = extract_drawing(pdf, out)
    progress("extract", extracted)
    planned = plan_fixes(out, inputs, planner)
    progress("plan", planned)
    applied, report = apply_fixes(pdf, out, inputs)
    progress("apply", applied)
    progress("report", report)
    return {"extract": extracted, "plan": planned, "apply": applied, "report": report,
            "duration_s": time.time() - t}
