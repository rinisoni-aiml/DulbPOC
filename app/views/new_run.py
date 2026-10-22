"""New run: upload a commented drawing, set the engineer inputs, run the pipeline, show the results."""
import json
import math
import re
import shutil
from pathlib import Path

import pandas as pd
import streamlit as st

from components.results import render_run
from components.theme import clean, header
from rectify.core import config
from rectify.db import runs
from rectify.services import rectification as svc

SAMPLE_PDF = config.SAMPLES_DIR / "DG-NCD-401-0601-ECJ-SDW-SE-2B1-7003447_PDF_01.pdf"
SAMPLE_REVISED = config.SAMPLES_DIR / "DG-NCD-401-0601-ECJ-SDW-SE-2B1-7003447_PDF_02-revised.pdf"
HELP = {
    "source": "The drawing PDF with the consultant's review comments as PDF markups (callouts, leader lines, "
              "boxes). Comments are read from the markups directly, so scanned or flattened comments are not "
              "supported.",
    "backbox": "Used when a comment asks for back box sizes: the size is written into the legend, for example "
               "75x75x50mm. Leave empty to list it for the engineer instead.",
    "conduit_size": "Nominal conduit size used in the conduit fill table, when a comment asks for a fill "
                    "calculation.",
    "conduit_area": "Internal cross-section area of that conduit. Fill % is the total cable area divided by this "
                    "area.",
    "max_fill": "Maximum allowed conduit fill from the project standard (40% is typical). Spare capacity is "
                "measured against it.",
    "overrides": 'Use a different conduit for specific cable types, for example '
                 '{"230VAC mains": {"size_mm": 25, "area_mm2": 346.4}}. Names must match the Cable Type table '
                 'on the drawing exactly. Leave {} if all cables share one conduit.',
    "cable_qty": 'Number of cables of a type in one conduit, when more than one, for example {"CAT6 / Fibre": 2}. '
                 'Names must match the Cable Type table on the drawing exactly. Types not listed count as 1.',
    "device_type": "Device type code as shown in the device tag on the drawing, for example PIR or L1.2.",
    "height": "Mounting height in millimetres, for example 2400.",
    "reference": "Needed only when a comment asks to restore room names or space IDs. Upload the architectural "
                 "background of the same area; its room names and IDs are copied onto the sheet.",
    "reference_file": "Architectural background PDF of the same floor area, showing room names and space IDs.",
    "revised": "Optional check. Upload the drafter's revised version of the same sheet to compare it with the "
               "corrected drawing, comment by comment. No API cost.",
    "revised_file": "The human-revised drawing PDF of the same sheet.",
    "run": "Processing takes about one to two minutes per sheet. Results appear below when it finishes.",
}
STAGES = {"extract": "Reading consultant comments", "plan": "Planning fixes",
          "apply": "Applying vector edits", "report": "Building report and images"}


def _default_inputs():
    """Neutral starting values: a common conduit spec and nothing project-specific. Back box size, overrides,
    quantities and elevations stay empty so only what the user enters is written onto the drawing."""
    return {"conduit": dict(config.CONDUIT)}


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
    """File uploader; offers the local sample drawing as well when samples/ holds it."""
    if sample.exists() and st.radio(label, ["Upload a PDF", "Use the sample drawing"], horizontal=True,
                                    key=f"{key}-mode") == "Use the sample drawing":
        st.caption(f"Sample: {sample.name}")
        return sample
    return st.file_uploader(label, type=["pdf"], key=f"{key}-file", help=help_text)


def _cell(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip()


def _elevations(defaults):
    st.markdown("**Device elevations**")
    st.caption("Optional. Mounting height per device type, for example from the RFI response. Devices of these "
               "types without an H=...mm label get one added; all others are listed for the engineer.")
    table = st.data_editor(
        pd.DataFrame([{"Device type": k, "Height (mm)": str(v)} for k, v in defaults.items()],
                     columns=["Device type", "Height (mm)"], dtype="object"),
        num_rows="dynamic", hide_index=True, width="stretch", key="elevations",
        column_config={"Device type": st.column_config.TextColumn(help=HELP["device_type"]),
                       "Height (mm)": st.column_config.TextColumn(help=HELP["height"])})
    out = {}
    for _, row in table.iterrows():
        kind, height = _cell(row.get("Device type")), _cell(row.get("Height (mm)"))
        if not kind and not height:
            continue
        if not kind or not re.fullmatch(r"\d+(\s*mm)?", height, re.IGNORECASE):
            return None, f"Device elevations: enter a device type and a height in mm (row '{kind or height}')."
        out[kind] = re.sub(r"\D", "", height)
    return out, None


def _inputs_form():
    d = _default_inputs()
    conduit = {**config.CONDUIT, **d.get("conduit", {})}
    st.markdown("**Engineer inputs**")
    st.caption("Facts the drawing does not contain. They are applied as given, never guessed.")
    c1, c2, c3, c4 = st.columns(4)
    backbox = c1.text_input("Back box size", d.get("backbox_size", ""), help=HELP["backbox"])
    size = c2.number_input("Conduit size (mm)", value=float(conduit["size_mm"]), min_value=1.0, step=1.0,
                           help=HELP["conduit_size"])
    area = c3.number_input("Conduit area (mm2)", value=float(conduit["area_mm2"]), min_value=1.0, step=1.0,
                           help=HELP["conduit_area"])
    fill = c4.number_input("Max fill (%)", value=float(conduit["max_fill_pct"]), min_value=1.0, max_value=100.0,
                           help=HELP["max_fill"])
    with st.expander("Advanced: per-cable conduit overrides and cable quantities (JSON)"):
        overrides = st.text_area("Conduit overrides", json.dumps(d.get("conduit_overrides", {}), indent=2),
                                 height=110, help=HELP["overrides"])
        qty = st.text_area("Cable quantities", json.dumps(d.get("cable_qty", {}), indent=2), height=80,
                           help=HELP["cable_qty"])
    inputs = {"conduit": {"size_mm": int(size) if size.is_integer() else size, "area_mm2": area,
                          "max_fill_pct": fill}}
    if backbox.strip():
        inputs["backbox_size"] = backbox.strip()
    try:
        inputs["conduit_overrides"] = json.loads(overrides or "{}")
        inputs["cable_qty"] = json.loads(qty or "{}")
    except ValueError as e:
        return None, f"Advanced inputs are not valid JSON: {e}"
    inputs["elevations"], error = _elevations(d.get("elevations") or {})
    return (None, error) if error else (inputs, None)


def _run(source, revised, reference, planner, inputs):
    run_id = runs.new_run_id()
    run_dir = config.RUNS_DIR / run_id
    pdf = _save(source, run_dir / "source")
    if reference is not None:
        inputs = {**inputs, "reference_pdf": str(_save(reference, run_dir / "reference"))}
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
            for w in result["plan"]["warnings"]:
                st.warning(clean(w))
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
    # Claude plans the fixes whenever an API key is configured; the offline rules planner is the fallback
    # (and what the local tests use).
    planner = "llm" if config.ANTHROPIC_API_KEY else "rules"

    with st.container(border=True):
        source = _pick_pdf("Commented drawing (PDF)", SAMPLE_PDF, "src", HELP["source"])
        st.divider()
        inputs, error = _inputs_form()
        st.divider()
        reference = None
        if st.toggle("Add an architectural reference drawing", help=HELP["reference"]):
            reference = st.file_uploader("Architectural reference drawing (PDF)", type=["pdf"], key="ref-file",
                                         help=HELP["reference_file"])
        revised = None
        if st.toggle("Also verify against a human-revised drawing", help=HELP["revised"]):
            revised = _pick_pdf("Revised drawing (PDF)", SAMPLE_REVISED, "rev", HELP["revised_file"])
        start = st.button("Run rectification", type="primary", disabled=source is None or error is not None,
                          help=HELP["run"])
        if error:
            st.error(error)

    if start:
        run_id = _run(source, revised, reference, planner, inputs)
        if run_id:
            st.session_state["current_run"] = run_id

    run_id = st.session_state.get("current_run")
    run = runs.get_run(run_id) if run_id else None
    if run:
        st.subheader("Results")
        render_run(run, key="current")
