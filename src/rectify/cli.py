"""CLI: rectify run <commented.pdf> [--inputs inputs.json] [--planner auto|llm|rules]
(also: python -m rectify ...)

Stages can also be run one at a time; each reads the previous stage's JSON from the output folder,
so an engineer can edit plan.json by hand and re-run `apply` alone.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from . import config


def _out(pdf, out):
    return Path(out) if out else config.OUT_DIR / Path(pdf).stem


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _inputs(path):
    return _load(path) if path else {}


def cmd_extract(a):
    from .stages.extract import extract
    out = _out(a.pdf, a.out)
    ex, _ = extract(Path(a.pdf), out)
    print(f"extract: {len(ex['comments'])} comments, {sum(len(c['tips']) for c in ex['comments'])} leader tips, "
          f"{len(ex['other_markups'])} other markups -> {out / 'comments.json'}")


def cmd_plan(a):
    from .stages.plan import plan
    out = _out(a.pdf, a.out)
    p = plan(_load(out / "comments.json"), _load(out / "sheet_text.json"), out, _inputs(a.inputs), a.planner)
    ops = sum(len(c["ops"]) for c in p["comments"])
    cost = f", ${p['usage']['usd']:.3f}" if p.get("usage") else ""
    print(f"plan ({p['planner']}{cost}): {ops} ops, "
          f"{sum(len(c['checklist']) for c in p['comments'])} checklist items -> {out / 'plan.json'}")
    errors = {c["intent"] for c in p["comments"] if c["intent"].startswith("Planner ")}
    for e in errors:
        print(f"  WARNING: {e}")


def cmd_apply(a):
    from .stages.apply import Rectifier
    from .stages.report import build
    out = _out(a.pdf, a.out)
    ex, p = _load(out / "comments.json"), _load(out / "plan.json")
    r = Rectifier(Path(a.pdf), ex, _inputs(a.inputs))
    changes, failures = r.run(p)
    r.save(out / "corrected.pdf")
    print(f"apply: {len(changes)} edits, {len(r.skipped)} already covered, {len(failures)} ops left for the "
          f"engineer, {r.markups_removed} markups removed -> {out / 'corrected.pdf'}")
    integ, checklist = build(Path(a.pdf), out, ex, p, changes, failures, r.markups_removed, r.skipped)
    print(f"report: {len(checklist)} checklist items, {integ['share']:.4%} of sheet changed outside edits "
          f"-> {out / 'report.html'}")


def cmd_run(a):
    t = time.time()
    cmd_extract(a)
    cmd_plan(a)
    cmd_apply(a)
    print(f"done in {time.time() - t:.0f}s")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rectify", description="Apply consultant comments to a drawing PDF.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("run", cmd_run), ("extract", cmd_extract), ("plan", cmd_plan), ("apply", cmd_apply)):
        sp = sub.add_parser(name)
        sp.add_argument("pdf", help="consultant-commented drawing PDF")
        sp.add_argument("--out", help="output folder (default out/<pdf name>)")
        sp.add_argument("--inputs", help="engineer inputs JSON (backbox size, conduit spec, ...)")
        sp.add_argument("--planner", choices=["auto", "llm", "rules"], default="auto",
                        help="auto = Claude if ANTHROPIC_API_KEY is set, else the rules baseline")
        sp.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
