from rectify.core import config
from rectify.db import runs
from rectify.db.database import init_db

RESULT = {"extract": {"comments": 9, "tips": 15}, "plan": {"planner": "rules", "usage": None},
          "apply": {"edits": 5, "failures": 0, "markups_removed": 24},
          "report": {"checklist_items": 8, "integrity": {"share": 3e-05}}, "duration_s": 21.0}


def test_run_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    db = tmp_path / "t.db"
    init_db(db)
    run_dir = config.RUNS_DIR / "r1"
    run_dir.mkdir(parents=True)
    runs.create_run("r1", "a.pdf", "rules", {"backbox_size": "75x75x50mm"}, run_dir, db_path=db)
    assert runs.get_run("r1", db)["status"] == "running"

    runs.finish_run("r1", RESULT, db_path=db)
    runs.set_verification("r1", "rev.pdf", {"Fixed": 1}, db_path=db)
    r = runs.get_run("r1", db)
    assert (r["status"], r["edits"], r["checklist_items"]) == ("done", 5, 8)
    assert r["inputs"] == {"backbox_size": "75x75x50mm"} and r["verify_summary"] == {"Fixed": 1}

    runs.create_run("r2", "b.pdf", "rules", {}, tmp_path / "elsewhere", db_path=db)
    runs.fail_run("r2", "boom", db_path=db)
    assert [x["id"] for x in runs.list_runs(db_path=db)] == ["r2", "r1"]

    runs.delete_run("r1", db_path=db)
    assert runs.get_run("r1", db) is None and not run_dir.exists()
