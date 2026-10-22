"""CLI: rectify run <commented.pdf> [--inputs inputs.json] [--planner auto|llm|rules]
(also: python -m rectify ...)

Stages can also be run one at a time; each reads the previous stage's JSON from the output folder,
so an engineer can edit plan.json by hand and re-run `apply` alone.
"""
import argparse
import sys
import time

from .services import rectification as svc


def cmd_extract(a):
    r = svc.extract_drawing(a.pdf, svc.output_dir(a.pdf, a.out))
    print(f"extract: {r['comments']} comments, {r['tips']} leader tips, "
          f"{r['other_markups']} other markups -> {r['path']}")


def cmd_plan(a):
    try:
        r = svc.plan_fixes(svc.output_dir(a.pdf, a.out), svc.load_inputs(a.inputs), a.planner)
    except svc.RectifyError as e:
        raise SystemExit(str(e)) from None
    cost = f", ${r['usage']['usd']:.3f}" if r["usage"] else ""
    print(f"plan ({r['planner']}{cost}): {r['ops']} ops, {r['checklist_items']} checklist items -> {r['path']}")
    for e in r["warnings"]:
        print(f"  WARNING: {e}")


def cmd_apply(a):
    applied, report = svc.apply_fixes(a.pdf, svc.output_dir(a.pdf, a.out), svc.load_inputs(a.inputs))
    print(f"apply: {applied['edits']} edits, {applied['skipped']} already covered, {applied['failures']} ops left "
          f"for the engineer, {applied['markups_removed']} markups removed -> {applied['path']}")
    print(f"report: {report['checklist_items']} checklist items, {report['integrity']['share']:.4%} of sheet changed "
          f"outside edits -> {report['path']}")


def cmd_verify(a):
    r = svc.verify_against(a.pdf, a.revised, svc.output_dir(a.pdf, a.out))
    print(f"verify: {r['fixed']} fixed, {r['partial']} partially fixed, {r['not_fixed']} not fixed; "
          f"{r['checks_passed']}/{r['checks']} file checks passed -> {r['path']}")


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
    sp = sub.add_parser("verify", help="compare corrected.pdf with the original and a human revision (no API)")
    sp.add_argument("pdf", help="the original consultant-commented PDF")
    sp.add_argument("--revised", help="human-revised PDF to compare against (ground truth)")
    sp.add_argument("--out", help="output folder of a previous run (default out/<pdf name>)")
    sp.set_defaults(fn=cmd_verify)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
