"""Run history: every run stored in SQLite, open one to see its results again."""
from datetime import datetime

import pandas as pd
import streamlit as st

from components.results import render_run
from components.theme import clean, header
from rectify.db import runs

STATUS = {"done": "Completed", "failed": "Failed", "running": "Running"}


def _when(iso):
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def render():
    header("Run history", "Previous runs with their outputs. Select a row to open it.")
    rows = runs.list_runs()
    if not rows:
        st.info("No runs yet. Start one from the New run page.")
        return

    table = pd.DataFrame([{
        "Date": _when(r["created_at"]),
        "Drawing": clean(r["source_name"]),
        "Status": STATUS.get(r["status"], r["status"]),
        "Planner": r["planner"],
        "Comments": r["comments"],
        "Edits": r["edits"],
        "Checklist": r["checklist_items"],
        "Verified": "Yes" if r["verify_summary"] else "No",
        "Time (s)": round(r["duration_s"]) if r["duration_s"] is not None else None,
    } for r in rows])
    event = st.dataframe(table, hide_index=True, use_container_width=True, on_select="rerun",
                         selection_mode="single-row", key="history-table")
    picked = event.selection.rows
    if not picked:
        return
    run = rows[picked[0]]

    st.divider()
    c1, c2 = st.columns([4, 1])
    c1.subheader(clean(run["source_name"]))
    with c2.popover("Delete run", use_container_width=True):
        st.write("This removes the run and all of its files.")
        if st.button("Confirm delete", type="primary", key=f"del-{run['id']}"):
            runs.delete_run(run["id"])
            if st.session_state.get("current_run") == run["id"]:
                st.session_state.pop("current_run")
            st.rerun()
    render_run(run, key=f"hist-{run['id']}")
