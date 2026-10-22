"""Run history: one row per web-app run, outputs in config.RUNS_DIR/<id>/."""
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..core import config
from .database import connect


def new_run_id():
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def create_run(run_id, source_name, planner, inputs, run_dir, db_path=None):
    with connect(db_path) as con:
        con.execute("INSERT INTO runs (id, created_at, source_name, planner, status, inputs_json, run_dir) "
                    "VALUES (?, ?, ?, ?, 'running', ?, ?)",
                    (run_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), source_name, planner,
                     json.dumps(inputs or {}), str(run_dir)))


def finish_run(run_id, result, db_path=None):
    """Store the summary returned by services.rectification.rectify()."""
    ex, pl, ap, rp = result["extract"], result["plan"], result["apply"], result["report"]
    with connect(db_path) as con:
        con.execute("UPDATE runs SET status='done', planner=?, duration_s=?, comments=?, tips=?, edits=?, failures=?, "
                    "checklist_items=?, markups_removed=?, outside_share=?, llm_usd=? WHERE id=?",
                    (pl["planner"], result["duration_s"], ex["comments"], ex["tips"], ap["edits"], ap["failures"],
                     rp["checklist_items"], ap["markups_removed"], rp["integrity"]["share"],
                     (pl["usage"] or {}).get("usd"), run_id))


def fail_run(run_id, error, db_path=None):
    with connect(db_path) as con:
        con.execute("UPDATE runs SET status='failed', error=? WHERE id=?", (str(error)[:2000], run_id))


def set_verification(run_id, revised_name, summary, db_path=None):
    with connect(db_path) as con:
        con.execute("UPDATE runs SET revised_name=?, verify_summary_json=? WHERE id=?",
                    (revised_name, json.dumps(summary), run_id))


def _row(r):
    d = dict(r)
    d["inputs"] = json.loads(d.pop("inputs_json") or "{}")
    d["verify_summary"] = json.loads(d.pop("verify_summary_json") or "null")
    d["run_dir"] = Path(d["run_dir"])
    return d


def list_runs(limit=200, db_path=None):
    with connect(db_path) as con:
        rows = con.execute("SELECT * FROM runs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
    return [_row(r) for r in rows]


def get_run(run_id, db_path=None):
    with connect(db_path) as con:
        r = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return _row(r) if r else None


def delete_run(run_id, db_path=None):
    run = get_run(run_id, db_path)
    with connect(db_path) as con:
        con.execute("DELETE FROM runs WHERE id=?", (run_id,))
    if run:
        d = run["run_dir"].resolve()
        if d.is_dir() and config.RUNS_DIR.resolve() in d.parents:  # only ever delete inside RUNS_DIR
            shutil.rmtree(d, ignore_errors=True)
