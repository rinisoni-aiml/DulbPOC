"""New run: upload a commented drawing, set the engineer inputs, run the pipeline, show the results."""
import json
import math
import re
import shutil
from pathlib import Path

import pandas as pd
import streamlit as st

from components.results import INPUT_FIELDS, INPUTS_ANCHOR, needed_inputs, render_run, suggestion_text
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
                    "calculation. Leave empty to use the conduit size stated on the drawing (20 mm if it states "
                    "none); the table footnote states the values used.",
    "conduit_area": "Internal cross-section area of that conduit. Fill % is the total cable area divided by this "
                    "area. Leave empty to use the standard area for the conduit size.",
    "max_fill": "Maximum allowed conduit fill from the project standard. Spare capacity is measured against it. "
                "Leave empty to use 40%.",
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
    "revision": "Adds a new row to the drawing's revision table and updates the revision number and date in the "
                "title block, as a drafter would when reissuing the sheet.",
    "revision_date": "Date for the new revision. Leave empty to use today's date.",
    "revision_text": "Description for the new revision row. Leave empty for the standard wording.",
    "run": "Processing takes about one to two minutes per sheet. Results appear below when it finishes.",
}
STAGES = {"extract": "Reading consultant comments", "plan": "Planning fixes",
          "apply": "Applying vector edits", "report": "Building report and images"}


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


def _elevations(defaults, suggestion=None):
    st.markdown("**Device elevations**")
    st.caption("Optional. Mounting height per device type, for example from the RFI response. Devices of these "
               "types without an H=...mm label get one added; all others are listed for the engineer.")
    if suggestion:
        st.caption(f"Heights already used on this sheet for these types: {suggestion_text('elevations', suggestion)}. "
                   "Enter them only if they are confirmed.")
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


def _revision():
    st.markdown("**Drawing revision**")
    bump = st.checkbox("Add a revision entry to the drawing", value=True, help=HELP["revision"])
    c1, c2 = st.columns([1, 2])
    date = c1.date_input("Revision date", value=None, format="DD/MM/YYYY", disabled=not bump,
                         help=HELP["revision_date"])
    text = c2.text_input("Revision description", placeholder="Revised as per consultant comments",
                         disabled=not bump, help=HELP["revision_text"])
    rev = {"bump": bump}
    if bump and date:
        rev["date"] = date.strftime("%d/%m/%y")
    if bump and text.strip():
        rev["description"] = text.strip()
    return rev


def _inputs_form(needed=(), suggestions=None):
    """Optional engineer inputs, all empty by default: the PDF alone is enough to run. Only values the user
    enters are applied; fixes that need a missing value are reported as ready to fix. Opens by itself when the
    last run is waiting for inputs."""
    suggestions = suggestions or {}
    st.markdown(f"<div id='{INPUTS_ANCHOR}'></div>", unsafe_allow_html=True)
    with st.expander("Optional: engineer inputs", expanded=bool(needed)):
        st.caption("Facts the drawing does not contain, such as sizes from the project specification. Leave "
                   "everything empty to process the PDF alone; anything entered is applied as given, never guessed.")
        if needed:
            st.info("The last run is waiting for: " + ", ".join(INPUT_FIELDS[k] for k in needed)
                    + ". Fill what you have and run again.")
        c1, c2, c3, c4 = st.columns(4)
        backbox = c1.text_input("Back box size", placeholder="e.g. 75x75x50mm", help=HELP["backbox"])
        size = c2.number_input("Conduit size (mm)", value=None, min_value=1.0, step=1.0,
                               placeholder="From drawing", help=HELP["conduit_size"])
        area = c3.number_input("Conduit area (mm2)", value=None, min_value=1.0, step=1.0,
                               placeholder="Standard for size", help=HELP["conduit_area"])
        fill = c4.number_input("Max fill (%)", value=None, min_value=1.0, max_value=100.0,
                               placeholder=f"Default {config.CONDUIT['max_fill_pct']:g}", help=HELP["max_fill"])
        st.markdown("**Per-cable conduit overrides and cable quantities (JSON)**")
        c5, c6 = st.columns(2)
        overrides = c5.text_area("Conduit overrides", "", height=90, help=HELP["overrides"],
                                 placeholder='{"230VAC mains": {"size_mm": 25, "area_mm2": 346.4}}')
        qty = c6.text_area("Cable quantities", "", height=90, help=HELP["cable_qty"],
                           placeholder='{"CAT6 / Fibre": 2}')
        heights, error = _elevations({}, suggestions.get("elevations"))
        st.markdown("**Architectural reference drawing**")
        reference = None
        if st.toggle("Add an architectural reference drawing", help=HELP["reference"]):
            reference = st.file_uploader("Architectural reference drawing (PDF)", type=["pdf"], key="ref-file",
                                         help=HELP["reference_file"])
        revision = _revision()
    if error:
        return None, None, error
    conduit = {k: v for k, v in (("size_mm", size), ("area_mm2", area), ("max_fill_pct", fill)) if v is not None}
    if "size_mm" in conduit and float(conduit["size_mm"]).is_integer():
        conduit["size_mm"] = int(conduit["size_mm"])
    inputs = {"conduit": conduit} if conduit else {}
    if backbox.strip():
        inputs["backbox_size"] = backbox.strip()
    try:
        for field, text in (("conduit_overrides", overrides), ("cable_qty", qty)):
            value = json.loads(text) if text.strip() else {}
            if not isinstance(value, dict):
                raise ValueError("expected an object like {...}")
            if value:
                inputs[field] = value
    except ValueError as e:
        return None, None, f"Conduit overrides and cable quantities must be valid JSON objects: {e}"
    if heights:
        inputs["elevations"] = heights
    inputs["revision"] = revision
    return inputs, reference, None


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
    last = st.session_state.get("current_run")
    needed, suggestions = needed_inputs(runs.get_run(last) if last else None)

    with st.container(border=True):
        source = _pick_pdf("Commented drawing (PDF)", SAMPLE_PDF, "src", HELP["source"])
        st.divider()
        inputs, reference, error = _inputs_form(needed, suggestions)
        st.divider()
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
