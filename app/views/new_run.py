"""New run: upload a commented drawing, set the engineer inputs, run the pipeline, show the results."""
import json
import re
import shutil
from pathlib import Path

import streamlit as st

from components.results import render_run
from components.theme import clean, header
from rectify.core import config
from rectify.db import runs
from rectify.services import rectification as svc

SAMPLE_PDF = config.SAMPLES_DIR / "DG-NCD-401-0601-ECJ-SDW-SE-2B1-7003447_PDF_01.pdf"
SAMPLE_REVISED = config.SAMPLES_DIR / "DG-NCD-401-0601-ECJ-SDW-SE-2B1-7003447_PDF_02-revised.pdf"
PLANNERS = {"rules": "Rules (offline, no API cost)", "llm": "Claude (LLM planner)"}
STAGES = {"extract": "Reading consultant comments", "plan": "Planning fixes",
          "apply": "Applying vector edits", "report": "Building report and images"}


def _default_inputs():
    try:
        return json.loads(config.INPUTS_EXAMPLE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _safe_name(name):
    name = re.sub(r"[^A-Za-z0-9._() -]+", "_", Path(name).name).strip() or "drawing.pdf"
    return name if name.lower().endswith(".pdf") else name + ".pdf"


def _save(source, folder):
    """Copy a sample Path or write an uploaded file into folder; return the saved path."""
    folder.mkdir(parents=True, exist_ok=True)
    if isinstance(source, Path):
        dest = folder / source.name
        shutil.copyfile(source, dest)
    else:
        dest = folder / _safe_name(source.name)
        dest.write_bytes(source.getbuffer())
    return dest


def _pick_pdf(label, sample, key, help_text):
    options = ["Upload a PDF"] + (["Use the sample drawing"] if sample.exists() else [])
    mode = st.radio(label, options, horizontal=True, key=f"{key}-mode")
    if mode == "Upload a PDF":
        return st.file_uploader(help_text, type=["pdf"], key=f"{key}-file")
    st.caption(f"Sample: {sample.name}")
    return sample


def _inputs_form():
    d = _default_inputs()
    conduit = {**config.CONDUIT, **d.get("conduit", {})}
    st.markdown("**Engineer inputs**")
    st.caption("Facts the drawing does not contain. They are applied as given, never guessed.")
    c1, c2, c3, c4 = st.columns(4)
    backbox = c1.text_input("Back box size", d.get("backbox_size", ""), help="Example: 75x75x50mm")
    size = c2.number_input("Conduit size (mm)", value=float(conduit["size_mm"]), min_value=1.0, step=1.0)
    area = c3.number_input("Conduit area (mm2)", value=float(conduit["area_mm2"]), min_value=1.0, step=1.0)
    fill = c4.number_input("Max fill (%)", value=float(conduit["max_fill_pct"]), min_value=1.0, max_value=100.0)
    with st.expander("Advanced: per-cable conduit overrides and cable quantities (JSON)"):
        overrides = st.text_area("Conduit overrides", json.dumps(d.get("conduit_overrides", {}), indent=2), height=110)
        qty = st.text_area("Cable quantities", json.dumps(d.get("cable_qty", {}), indent=2), height=80)
    inputs = {"conduit": {"size_mm": int(size) if size.is_integer() else size, "area_mm2": area,
                          "max_fill_pct": fill}}
    if backbox.strip():
        inputs["backbox_size"] = backbox.strip()
    try:
        inputs["conduit_overrides"] = json.loads(overrides or "{}")
        inputs["cable_qty"] = json.loads(qty or "{}")
    except ValueError as e:
        return None, f"Advanced inputs are not valid JSON: {e}"
    return inputs, None


def _run(source, revised, planner, inputs):
    run_id = runs.new_run_id()
    run_dir = config.RUNS_DIR / run_id
    pdf = _save(source, run_dir / "source")
    runs.create_run(run_id, pdf.name, planner, inputs, run_dir)
    with st.status("Processing drawing", expanded=True) as status:
        order = list(STAGES)

        def progress(stage, result):
            st.write(f"{STAGES[stage]}: done")
            nxt = order.index(stage) + 1
            if nxt < len(order) and order[nxt] != "report":
                st.write(f"{STAGES[order[nxt]]}...")
        try:
            st.write(f"{STAGES['extract']}...")
            result = svc.rectify(pdf, run_dir, inputs, planner, progress)
            runs.finish_run(run_id, result)
            if revised is not None:
                st.write("Verifying against the revised drawing...")
                rev = _save(revised, run_dir / "revised")
                v = svc.verify_against(pdf, rev, run_dir)
                runs.set_verification(run_id, rev.name, v["summary"])
            status.update(label=f"Completed in {result['duration_s']:.0f} s", state="complete", expanded=False)
        except Exception as e:  # shown to the user and stored with the run
            runs.fail_run(run_id, e)
            status.update(label="Processing failed", state="error", expanded=True)
            st.error(clean(e))
            return None
    return run_id


def render():
    header("Drawing rectification",
           "Upload a consultant-commented drawing PDF. Comments are read from the PDF, applied to the drawing as "
           "vector edits and removed, and anything that needs an engineer goes on a checklist.")
    has_key = bool(config.ANTHROPIC_API_KEY)

    with st.container(border=True):
        source = _pick_pdf("Commented drawing", SAMPLE_PDF, "src", "Consultant-commented drawing (PDF)")
        st.divider()
        planners = ["rules", "llm"] if has_key else ["rules"]
        planner = st.selectbox("Fix planner", planners, format_func=PLANNERS.get)
        if planner == "llm":
            st.caption(f"Uses {config.PLANNER_MODEL} through the Anthropic API, about $0.25 to $0.50 per sheet.")
        if not has_key:
            st.caption("The Claude planner is not available because no API key is configured. "
                       "The offline rules planner is used.")
        st.divider()
        inputs, error = _inputs_form()
        st.divider()
        revised = None
        if st.toggle("Also verify against a human-revised drawing",
                     help="Compares the corrected drawing with a revision made by a drafter. No API cost."):
            revised = _pick_pdf("Revised drawing", SAMPLE_REVISED, "rev", "Human-revised drawing (PDF)")
        start = st.button("Run rectification", type="primary", disabled=source is None or error is not None)
        if error:
            st.error(error)

    if start:
        run_id = _run(source, revised, planner, inputs)
        if run_id:
            st.session_state["current_run"] = run_id

    run_id = st.session_state.get("current_run")
    run = runs.get_run(run_id) if run_id else None
    if run:
        st.subheader("Results")
        render_run(run, key="current")
