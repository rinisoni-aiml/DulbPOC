"""Render one run's outputs: KPIs, sheet preview, per-comment cards, checklist, verification, downloads."""
import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import pymupdf
import streamlit as st

from components.theme import badge, clean

STATUS = {"auto": ("Applied", "ok"), "partial": ("Partially applied", "warn"), "engineer": ("Engineer action", "muted")}
VERDICT = {"Fixed": "ok", "Partially fixed": "warn", "Not fixed": "fail"}
DOWNLOADS = [
    ("corrected.pdf", "Corrected drawing (PDF)", "application/pdf"),
    ("corrected_highlighted.pdf", "Corrected drawing with highlighted changes (PDF)", "application/pdf"),
    ("report.html", "Rectification report (HTML)", "text/html"),
    ("checklist.md", "Engineer checklist (Markdown)", "text/markdown"),
    ("verification_report.html", "Verification report (HTML)", "text/html"),
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

    names = ["Summary", "Drawing", "Engineer checklist"] + (["Verification"] if ver else []) + ["Downloads"]
    tabs = dict(zip(names, st.tabs(names)))
    with tabs["Summary"]:
        _summary(d, ex, plan, chg)
    with tabs["Drawing"]:
        _drawing(d, key)
    with tabs["Engineer checklist"]:
        _checklist(plan, chg)
    if ver:
        with tabs["Verification"]:
            _verification(d, ver, run.get("revised_name"))
    with tabs["Downloads"]:
        _downloads(d, run, key)


def _summary(d, ex, plan, chg):
    if (d / "overview.png").exists():
        st.image(str(d / "overview.png"), caption="Whole sheet. Orange: consultant comments. Red: applied edits.",
                 use_container_width=True)
    status = {p["comment_id"]: p for p in plan["comments"]}
    by_comment = {}
    for ch in chg["changes"]:
        by_comment.setdefault(ch["comment_id"], []).append(ch)
    st.subheader("Comments")
    for c in ex["comments"]:
        p = status.get(c["id"], {})
        label, kind = STATUS.get(p.get("status", "engineer"), STATUS["engineer"])
        with st.container(border=True):
            st.markdown(f"**{c['id']}** &nbsp; {badge(label, kind)}"
                        + (badge(clean(p['scope']).replace('_', ' ')) if p.get("scope") else ""),
                        unsafe_allow_html=True)
            st.markdown(f"<div class='quote'>{clean(p.get('clean_text') or c['text'])}</div>", unsafe_allow_html=True)
            if p.get("intent"):
                st.markdown(f"**Intent:** {clean(p['intent'])}")
            for ch in by_comment.get(c["id"], []):
                st.markdown(f"**{ch['id']}:** {clean(ch['description'])}")
                if ch.get("image") and (d / ch["image"]).exists():
                    st.image(str(d / ch["image"]), caption=f"{ch['id']}: before (left) and after (right)")
            for s in chg.get("skipped", []):
                if s["comment_id"] == c["id"]:
                    st.markdown(f"<p class='muted'>Covered by another edit ({clean(s['op'])}): {clean(s['reason'])}</p>",
                                unsafe_allow_html=True)
            for f in chg["failures"]:
                if f["comment_id"] == c["id"]:
                    st.warning(f"Not applied ({clean(f['op'])}): {clean(f['reason'])}")
            if p.get("checklist"):
                st.markdown("**For the engineer:**\n" + "\n".join(
                    f"- {clean(k['item'])} *(needs: {clean(', '.join(k['inputs_needed']))})*" for k in p["checklist"]))
    others = ex.get("other_markups", [])
    if others:
        st.subheader("Other markups (not comments)")
        st.dataframe(pd.DataFrame([{"Kind": o["kind"], "Text": clean(o["text"]) or "-", "Author": o.get("author", "")}
                                   for o in others]), hide_index=True, use_container_width=True)


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
        st.image(_render_page(str(path), path.stat().st_mtime, dpi), use_container_width=True)


def _checklist(plan, chg):
    rows = []
    for p in plan["comments"]:
        for k in p.get("checklist", []):
            rows.append({"Comment": p["comment_id"], "Action": clean(k["item"]),
                         "Inputs needed": clean(", ".join(k["inputs_needed"])), "Where": clean(k["where_on_sheet"])})
    for f in chg["failures"]:
        rows.append({"Comment": f["comment_id"], "Action": clean(f"Could not auto-apply {f['op']}: {f['reason']}"),
                     "Inputs needed": "Drafter to apply manually", "Where": "See comment"})
    if not rows:
        st.success("No open items for the engineer.")
        return
    st.caption("Items the rectifier could not settle from the drawing alone.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
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
                 hide_index=True, use_container_width=True)
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
                    st.image(str(d / r["image"]), use_container_width=True)


def _downloads(d, run, key):
    stem = Path(run["source_name"]).stem
    for name, label, mime in DOWNLOADS:
        f = d / name
        if f.exists():
            st.download_button(label, f.read_bytes(), file_name=f"{stem}_{name}", mime=mime,
                               key=f"dl-{key}-{name}", use_container_width=True,
                               type="primary" if name == "corrected.pdf" else "secondary")
    st.download_button("All outputs (ZIP)", _zip_folder(str(d), d.stat().st_mtime), file_name=f"{stem}_outputs.zip",
                       mime="application/zip", key=f"dl-{key}-zip", use_container_width=True)
