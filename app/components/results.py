"""Render one run's outputs: KPIs, sheet preview, per-comment cards, checklist, verification, downloads."""
import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import pymupdf
import streamlit as st

from components.theme import badge, clean
from rectify.db import runs
from rectify.pipeline.report import RESOLUTION_LABEL

# resolution[*].input_keys -> label of the field on the New run page
INPUT_FIELDS = {"backbox_size": "Back box size", "elevations": "Device elevations",
                "reference_pdf": "Architectural reference drawing", "conduit": "Conduit size, area and max fill",
                "cable_qty": "Cable quantities"}
INPUTS_ANCHOR = "engineer-inputs"
RESOLUTION_KIND = {"resolved": "ok", "partially_resolved": "warn", "ready_to_fix": "warn", "needs_engineer": "muted"}
STATUS = {"auto": ("Applied", "ok"), "partial": ("Partially applied", "warn"), "engineer": ("Engineer action", "muted")}
VERDICT = {"Fixed": "ok", "Partially fixed": "warn", "Not fixed": "fail"}
REVIEW = {"Pending": None, "Approve": "approved", "Reject": "rejected"}
REVIEW_BADGE = {None: ("Pending review", "muted"), "approved": ("Approved", "ok"), "rejected": ("Rejected", "fail")}
DOWNLOADS = [
    ("corrected.pdf", "Corrected drawing (PDF)", "application/pdf"),
    ("corrected_highlighted.pdf", "Corrected drawing with highlighted changes (PDF)", "application/pdf"),
    ("report.html", "Rectification report (HTML)", "text/html"),
    ("checklist.md", "Engineer checklist (Markdown)", "text/markdown"),
    ("verification_report.html", "Verification report (HTML)", "text/html"),
    ("approvals.json", "Reviewer decisions (JSON)", "application/json"),
    ("changes.json", "Changes (JSON)", "application/json"),
    ("plan.json", "Fix plan (JSON)", "application/json"),
    ("comments.json", "Extracted comments (JSON)", "application/json"),
]


def _json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


@st.cache_data(show_spinner=False, max_entries=16)
def _render_page(pdf_path, mtime, dpi):
    page = pymupdf.open(pdf_path)[0]
    return page.get_pixmap(dpi=dpi, annots=True).tobytes("png")


@st.cache_data(show_spinner=False, max_entries=8)
def _zip_folder(folder, mtime):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(Path(folder).rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(folder))
    return buf.getvalue()


def _source_pdf(run_dir):
    found = sorted((run_dir / "source").glob("*.pdf"))
    return found[0] if found else None


def render_run(run, key):
    d = run["run_dir"]
    if run["status"] == "failed":
        st.error(f"This run failed: {clean(run['error'])}")
        return
    if run["status"] != "done" or not (d / "changes.json").exists():
        st.info("This run has not finished.")
        return

    ex, plan, chg = _json(d / "comments.json"), _json(d / "plan.json"), _json(d / "changes.json")
    ver = _json(d / "verification" / "verification.json")

    cols = st.columns(6)
    cols[0].metric("Comments found", run["comments"])
    cols[1].metric("Edits applied", run["edits"])
    cols[2].metric("Checklist items", run["checklist_items"])
    cols[3].metric("Markups removed", run["markups_removed"])
    cols[4].metric("Changed outside edits", f"{run['outside_share']:.3%}")
    cols[5].metric("Processing time", f"{run['duration_s']:.0f} s")
    usd = f" | LLM cost ${run['llm_usd']:.3f}" if run.get("llm_usd") is not None else ""
    st.markdown(f"<p class='muted'>Source: {clean(run['source_name'])} | Planner: {clean(run['planner'])}{usd} | "
                f"Run ID: {run['id']}</p>", unsafe_allow_html=True)
    stem = Path(run["source_name"]).stem
    b1, b2, _ = st.columns([1, 1, 1])
    for col, (name, label, kind) in zip((b1, b2), (("corrected.pdf", "Download corrected drawing (PDF)", "primary"),
                                                   ("corrected_highlighted.pdf", "Download with changes highlighted",
                                                    "secondary"))):
        if (d / name).exists():
            col.download_button(label, (d / name).read_bytes(), file_name=f"{stem}_{name}", mime="application/pdf",
                                key=f"top-{key}-{name}", type=kind, width="stretch")

    names = ["Summary", "Drawing", "Engineer checklist"] + (["Verification"] if ver else []) + ["Downloads"]
    tabs = dict(zip(names, st.tabs(names)))
    with tabs["Summary"]:
        _summary(d, ex, plan, chg, run, key)
    with tabs["Drawing"]:
        _drawing(d, key)
    with tabs["Engineer checklist"]:
        _checklist(plan, chg)
    if ver:
        with tabs["Verification"]:
            _verification(d, ver, run.get("revised_name"))
    with tabs["Downloads"]:
        _downloads(d, run, key)


def _summary(d, ex, plan, chg, run, key):
    if (d / "overview.png").exists():
        st.image(str(d / "overview.png"), caption="Whole sheet. Orange: consultant comments. Red: applied edits.",
                 width="stretch")
    status = {p["comment_id"]: p for p in plan["comments"]}
    by_comment = {}
    for ch in chg["changes"]:
        by_comment.setdefault(ch["comment_id"], []).append(ch)
    resolution = {r["comment_id"]: r for r in chg.get("resolution", [])}
    _inputs_check(run.get("inputs") or {}, chg)
    if resolution:
        st.subheader("Comment resolution")
        cols = st.columns(len(RESOLUTION_LABEL))
        for col, (k, label) in zip(cols, RESOLUTION_LABEL.items()):
            col.metric(label, sum(1 for r in resolution.values() if r["status"] == k))
    decisions = runs.get_decisions(run["id"])
    _review_panel(run, chg["changes"], decisions, key)
    st.subheader("Comments")
    for c in ex["comments"]:
        p = status.get(c["id"], {})
        res = resolution.get(c["id"])
        if res:
            label, kind = RESOLUTION_LABEL.get(res["status"], res["status"]), RESOLUTION_KIND.get(res["status"], "muted")
        else:
            label, kind = STATUS.get(p.get("status", "engineer"), STATUS["engineer"])
        with st.container(border=True):
            st.markdown(f"**{c['id']}** &nbsp; {badge(label, kind)}"
                        + (badge(clean(p['scope']).replace('_', ' ')) if p.get("scope") else ""),
                        unsafe_allow_html=True)
            st.markdown(f"<div class='quote'>{clean(p.get('clean_text') or c['text'])}</div>", unsafe_allow_html=True)
            if p.get("intent"):
                st.markdown(f"**Intent:** {clean(p['intent'])}")
            if res:
                _fix_hint(res, key)
            for ch in by_comment.get(c["id"], []):
                label, kind = REVIEW_BADGE[(decisions.get(ch["id"]) or {}).get("decision")]
                st.markdown(f"**{ch['id']}:** {clean(ch['description'])} &nbsp; {badge(label, kind)}",
                            unsafe_allow_html=True)
                if ch.get("image") and (d / ch["image"]).exists():
                    st.image(str(d / ch["image"]), caption=f"{ch['id']}: before (left) and after (right)")
            for s in chg.get("skipped", []):
                if s["comment_id"] == c["id"]:
                    st.markdown(f"<p class='muted'>Covered by another edit ({clean(s['op'])}): {clean(s['reason'])}</p>",
                                unsafe_allow_html=True)
            for f in chg["failures"]:
                if f["comment_id"] == c["id"]:
                    needs = "; ".join(clean(n) for n in f.get("inputs_needed") or [])
                    if f.get("kind") == "input":
                        st.info(f"Waiting for input ({clean(f['op'])}): {clean(f['reason'])}"
                                + (f". Needed: {needs}" if needs else ""))
                    else:
                        st.warning(f"Not applied ({clean(f['op'])}): {clean(f['reason'])}")
            if p.get("checklist"):
                st.markdown("**For the engineer:**\n" + "\n".join(
                    f"- {clean(k['item'])} *(needs: {clean(', '.join(k['inputs_needed']))})*" for k in p["checklist"]))
    _missing_elevations(chg.get("missing_elevations", []))
    others = ex.get("other_markups", [])
    if others:
        st.subheader("Other markups (not comments)")
        st.dataframe(pd.DataFrame([{"Kind": o["kind"], "Text": clean(o["text"]) or "-", "Author": o.get("author", "")}
                                   for o in others]), hide_index=True, width="stretch")


def suggestion_text(key, value):
    if isinstance(value, dict):
        unit = " mm" if key == "elevations" else ""
        return ", ".join(f"{k} {v}{unit}" for k, v in value.items())
    return str(value)


def needed_inputs(run):
    """Input fields the run's comments are waiting for, with sheet-derived suggestions (hints only)."""
    if not run or run.get("status") != "done":
        return [], {}
    chg = _json(run["run_dir"] / "changes.json") or {}
    keys, suggestions = [], {}
    for r in chg.get("resolution", []):
        if r["status"] in ("ready_to_fix", "partially_resolved"):
            keys += [k for k in r.get("input_keys") or [] if k in INPUT_FIELDS and k not in keys]
            for k, v in (r.get("suggestions") or {}).items():
                if isinstance(v, dict):
                    suggestions.setdefault(k, {}).update(v)
                else:
                    suggestions.setdefault(k, v)
    return keys, suggestions


def _fix_hint(res, key):
    """'To fix this, fill: ...' for a comment waiting on engineer inputs."""
    if res["status"] not in ("ready_to_fix", "partially_resolved"):
        return
    fields = [INPUT_FIELDS[k] for k in res.get("input_keys") or [] if k in INPUT_FIELDS]
    if fields:
        if key == "current":
            links = ", ".join(f"[{f}](#{INPUTS_ANCHOR})" for f in fields)
            st.markdown(f"**To fix this, fill:** {links} under Optional: engineer inputs above, then run again.")
        else:
            st.markdown(f"**To fix this, fill:** {', '.join(fields)} under Optional: engineer inputs on the "
                        "New run page, then run again.")
    for k, v in (res.get("suggestions") or {}).items():
        st.caption(f"Values already used on this sheet for {INPUT_FIELDS.get(k, k).lower()}: "
                   f"{suggestion_text(k, v)}. Enter them only if they are confirmed.")


def _conduit_types(chg):
    """Cable types in the conduit fill table this run added, or None when no table was added."""
    for ch in chg["changes"]:
        if ch["op"] == "add_conduit_fill_table":
            try:
                return [r["Cable Type"] for r in json.loads(ch.get("after") or "[]")]
            except (ValueError, TypeError, KeyError):
                return []
    return None


def unused_inputs(inputs, chg):
    """Engineer inputs that had no effect on this run, each with the reason (shown so nothing fails silently)."""
    notes = []
    types = _conduit_types(chg)
    for field, label in (("conduit_overrides", "Conduit override"), ("cable_qty", "Cable quantity")):
        names = list((inputs.get(field) or {}).keys())
        if not names:
            continue
        if types is None:
            notes.append(f"{label} values were not used: no comment asked for a conduit fill calculation.")
            continue
        for name in names:
            if name not in types:
                notes.append(f"{label} '{name}' was not used: it does not match a cable type on this drawing. "
                             f"Cable types on this drawing: {', '.join(types)}.")
    heights = inputs.get("elevations") or {}
    if heights:
        missing = chg.get("missing_elevations")
        unlabelled = sorted({m["type"] for m in missing or []})
        if not missing and not any(f.get("op") == "mark_missing_elevations" for f in chg["failures"]) \
                and not any(c["op"] == "mark_missing_elevations" for c in chg["changes"]):
            notes.append("Device elevations were not used: no comment asked for device elevations.")
        else:
            for kind in heights:
                if kind not in unlabelled:
                    notes.append(f"Device elevation for '{kind}' was not used: no device of that type is missing "
                                 "an elevation label. Types missing a label: "
                                 f"{', '.join(unlabelled) or 'none'}.")
    size = (inputs.get("backbox_size") or "").strip()
    if size and not any(size in f"{c.get('after') or ''} {c['description']}" for c in chg["changes"]):
        notes.append(f"Back box size '{size}' was not used: no change on this drawing needed it.")
    return notes


def _inputs_check(inputs, chg):
    notes = unused_inputs(inputs, chg)
    for n in notes:
        st.warning(clean(n))
    shown = {k: v for k, v in inputs.items() if k != "reference_pdf" and v not in ({}, "", None)}
    if inputs.get("reference_pdf"):
        shown["reference_drawing"] = Path(inputs["reference_pdf"]).name
    with st.expander("Engineer inputs used for this run"):
        st.json(shown)


def _missing_elevations(items):
    if not items:
        return
    added = sum(1 for m in items if m.get("added"))
    st.subheader("Devices without an elevation label")
    st.caption(f"{len(items)} device(s) have no H=...mm label; {added} label(s) were added from the inputs. "
               "The rest are marked orange in the highlighted drawing. Enter a mounting height per device type "
               "under Device elevations on the New run page and run again to add them.")
    by_type = {}
    for m in items:
        t = by_type.setdefault(m["type"], {"Device type": m["type"], "Devices": 0, "Labels added": 0, "Tags": []})
        t["Devices"] += 1
        t["Labels added"] += bool(m.get("added"))
        t["Tags"].append(m["tag"])
    st.dataframe(pd.DataFrame([{**t, "Tags": ", ".join(t["Tags"])} for t in by_type.values()]),
                 hide_index=True, width="stretch",
                 column_config={"Tags": st.column_config.TextColumn(width="large")})


def _drawing(d, key):
    src = _source_pdf(d)
    options = {"Corrected": d / "corrected.pdf", "Corrected with highlights": d / "corrected_highlighted.pdf"}
    if src:
        options["Original with comments"] = src
    c1, c2 = st.columns([3, 1])
    choice = c1.radio("Sheet", list(options), horizontal=True, key=f"sheet-{key}")
    dpi = c2.select_slider("Resolution (DPI)", options=[50, 75, 100, 150], value=75, key=f"dpi-{key}")
    path = options[choice]
    with st.spinner("Rendering sheet"):
        st.image(_render_page(str(path), path.stat().st_mtime, dpi), width="stretch")


def _checklist(plan, chg):
    rows = []
    for p in plan["comments"]:
        for k in p.get("checklist", []):
            rows.append({"Comment": p["comment_id"], "Action": clean(k["item"]),
                         "Inputs needed": clean(", ".join(k["inputs_needed"])), "Where": clean(k["where_on_sheet"])})
    for f in chg["failures"]:
        verb = "Waiting for input" if f.get("kind") == "input" else "Could not auto-apply"
        rows.append({"Comment": f["comment_id"], "Action": clean(f"{verb} ({f['op']}): {f['reason']}"),
                     "Inputs needed": clean(", ".join(f.get("inputs_needed") or ["Drafter to apply manually"])),
                     "Where": "See comment"})
    if not rows:
        st.success("No open items for the engineer.")
        return
    st.caption("Items the rectifier could not settle from the drawing alone.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"Action": st.column_config.TextColumn(width="large"),
                                "Inputs needed": st.column_config.TextColumn(width="medium")})


def _verification(d, ver, revised_name):
    s = ver["summary"]
    st.caption(f"Compared with the human revision: {clean(revised_name or 'not provided')}")
    cols = st.columns(4)
    cols[0].metric("Fixed", s.get("Fixed", 0))
    cols[1].metric("Partially fixed", s.get("Partially fixed", 0))
    cols[2].metric("Not fixed", s.get("Not fixed", 0))
    cols[3].metric("File checks passed", f"{sum(c['ok'] for c in ver['checks'])}/{len(ver['checks'])}")
    st.subheader("File checks")
    st.dataframe(pd.DataFrame([{"Check": clean(c["check"]), "Result": "Pass" if c["ok"] else "Fail",
                                "Detail": clean(c["detail"])} for c in ver["checks"]]),
                 hide_index=True, width="stretch")
    st.subheader("Per comment")
    for r in ver["comments"]:
        with st.container(border=True):
            st.markdown(f"**{r['id']}** &nbsp; {badge(r['verdict'], VERDICT.get(r['verdict'], 'muted'))}",
                        unsafe_allow_html=True)
            st.markdown(f"<div class='quote'>{clean(r['comment'])}</div>", unsafe_allow_html=True)
            if r.get("open_items"):
                st.markdown("\n".join(f"- {clean(i)}" for i in r["open_items"]))
            if r.get("image") and (d / r["image"]).exists():
                with st.expander("Original, corrected and human revision"):
                    st.image(str(d / r["image"]), width="stretch")


def _downloads(d, run, key):
    stem = Path(run["source_name"]).stem
    for name, label, mime in DOWNLOADS:
        f = d / name
        if f.exists():
            st.download_button(label, f.read_bytes(), file_name=f"{stem}_{name}", mime=mime,
                               key=f"dl-{key}-{name}", width="stretch",
                               type="primary" if name == "corrected.pdf" else "secondary")
    st.download_button("All outputs (ZIP)", _zip_folder(str(d), d.stat().st_mtime), file_name=f"{stem}_outputs.zip",
                       mime="application/zip", key=f"dl-{key}-zip", width="stretch")


def _choice_label(decision):
    return next(label for label, value in REVIEW.items() if value == decision)


def _save_decision(run_id, ch, wkey):
    choice = REVIEW[st.session_state[f"{wkey}-choice"]]
    note = st.session_state.get(f"{wkey}-note", "") if choice else ""
    runs.set_decision(run_id, ch["id"], choice, ch["comment_id"], note)


def _approve_pending(run_id, changes, decisions, key):
    for ch in changes:
        if ch["id"] not in decisions:
            runs.set_decision(run_id, ch["id"], "approved", ch["comment_id"])
        st.session_state.pop(f"rev-{key}-{ch['id']}-choice", None)  # widgets re-read the stored decision


def _review_panel(run, changes, decisions, key):
    """Approve or reject each applied change. Decisions are saved at once (SQLite + approvals.json)."""
    if not changes:
        return
    approved = sum(d["decision"] == "approved" for d in decisions.values())
    rejected = sum(d["decision"] == "rejected" for d in decisions.values())
    pending = len(changes) - approved - rejected
    st.subheader("Change review")
    st.caption("Approve or reject each change applied to the drawing. Decisions are saved immediately and "
               "included in the downloads as approvals.json.")
    cols = st.columns([1, 1, 1, 2])
    cols[0].metric("Approved", approved)
    cols[1].metric("Rejected", rejected)
    cols[2].metric("Pending", pending)
    cols[3].button("Approve all pending", key=f"approve-all-{key}", disabled=pending == 0,
                   on_click=_approve_pending, args=(run["id"], changes, decisions, key))
    for ch in changes:
        dec = decisions.get(ch["id"]) or {}
        wkey = f"rev-{key}-{ch['id']}"
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 2, 3], vertical_alignment="center")
            c1.markdown(f"**{ch['id']}** ({ch['comment_id']}): {clean(ch['description'])}")
            c2.radio("Decision", list(REVIEW), index=list(REVIEW).index(_choice_label(dec.get("decision"))),
                     horizontal=True, key=f"{wkey}-choice", label_visibility="collapsed",
                     on_change=_save_decision, args=(run["id"], ch, wkey))
            c3.text_input("Reviewer note", value=dec.get("note", ""), key=f"{wkey}-note",
                          placeholder="Reviewer note (optional)", label_visibility="collapsed",
                          disabled=not dec, on_change=_save_decision, args=(run["id"], ch, wkey))
