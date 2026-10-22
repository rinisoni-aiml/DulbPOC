"""SQLite connection and schema. Standard library only; one short-lived connection per call."""
import sqlite3
from contextlib import contextmanager

from ..core import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                  TEXT PRIMARY KEY,
    created_at          TEXT NOT NULL,
    source_name         TEXT NOT NULL,
    planner             TEXT,
    status              TEXT NOT NULL,          -- running | done | failed
    duration_s          REAL,
    comments            INTEGER,
    tips                INTEGER,
    edits               INTEGER,
    failures            INTEGER,
    checklist_items     INTEGER,
    markups_removed     INTEGER,
    outside_share       REAL,
    llm_usd             REAL,
    inputs_json         TEXT,
    revised_name        TEXT,
    verify_summary_json TEXT,
    error               TEXT,
    run_dir             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_created ON runs (created_at DESC);

CREATE TABLE IF NOT EXISTS approvals (
    run_id      TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    change_id   TEXT NOT NULL,                  -- changes.json "id", e.g. CH-01
    comment_id  TEXT,
    decision    TEXT NOT NULL,                  -- approved | rejected
    note        TEXT NOT NULL DEFAULT '',
    decided_at  TEXT NOT NULL,
    PRIMARY KEY (run_id, change_id)
);
"""


@contextmanager
def connect(db_path=None):
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


def init_db(db_path=None):
    with connect(db_path) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
