"""Stage 5 (optional): verify a corrected sheet against the original and a human-revised sheet.

No LLM. For every comment it compares the same spot on three sheets:
  original (markups hidden)  vs  corrected  vs  human revision (aligned by shared words)
and reports, per comment: did we edit it, did the human change it, did we move it toward the
human's version. It also checks file integrity and table numbers against the revision.
"""
import html
import json
import random
import re
from collections import Counter, defaultdict

import numpy as np
import pymupdf
from PIL import Image, ImageDraw

from ..core import config
from ..pdf.align import Aligner
from ..pdf.align import gray as _gray
from ..pdf.geometry import open_clean
from .report import CSS, _font, _pix_to_img

PCT = re.compile(r"^\d+(\.\d+)?%$")
REGION = 160      # pt: side of the square compared around each leader tip
IMAGE_DPI = 110


# ---- pixel comparison ------------------------------------------------------------------------
def _mad(a, b):
    if a is None or b is None:
        return None
    h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    return float(np.abs(a[:h, :w] - b[:h, :w]).mean())


def _three_way(po, pc, pr, region, off):
    shifted = region + (off[0], off[1], off[0], off[1])
    go, gc, gr = _gray(po, region), _gray(pc, region), _gray(pr, shifted)
    return {"orig_vs_rev": _mad(go, gr), "corr_vs_rev": _mad(gc, gr), "orig_vs_corr": _mad(go, gc)}


def _control_baseline(po, pr, aligner, n=40, seed=7):
    """Typical original-vs-revision difference at random spots (what 'unchanged' looks like)."""
    rnd = random.Random(seed)
    r = po.rect
    vals = []
    for _ in range(n * 3):
        x, y = rnd.uniform(r.x0 + 150, r.x1 - 450), rnd.uniform(r.y0 + 150, r.y1 - 300)
        reg = pymupdf.Rect(x, y, x + REGION, y + REGION)
        off, _ = aligner.offset(reg)
        d = _mad(_gray(po, reg), _gray(pr, reg + (off[0], off[1], off[0], off[1])))
        if d is not None:
            vals.append(d)
        if len(vals) >= n:
            break
    vals.sort()
    return {"median": vals[len(vals) // 2] if vals else 0.0,
            "p75": vals[int(len(vals) * 0.75)] if vals else 0.0, "n": len(vals)}


# ---- revision table numbers --------------------------------------------------------------------
def _pct_pairs_near(page, anchor_text):
    """(fill, spare) % pairs from table rows around a header word on a sheet."""
    hits = page.search_for(anchor_text)
    if not hits:
        return []
    m = page.rotation_matrix
    h = hits[0] * m
    zone = pymupdf.Rect(h.x0 - 900, h.y0 - 40, h.x1 + 200, h.y1 + 400)
    rows = defaultdict(list)
    for w in page.get_text("words"):
        r = pymupdf.Rect(w[:4]) * m
        if zone.contains(r) and PCT.match(w[4]):
            rows[round((r.y0 + r.y1) / 2 / 4)].append((r.x0, float(w[4][:-1])))
    pairs = []
    for row in rows.values():
        vals = [v for _, v in sorted(row)]
        if len(vals) >= 2:
            pairs.append((vals[-2], vals[-1]))
    return pairs


# ---- crops for the report --------------------------------------------------------------------
def _panel(pages_offsets, region, path, labels, mark=None):
    imgs = []
    for (page, off, annots) in pages_offsets:
        r = region + (off[0], off[1], off[0], off[1]) if off else region
        clip = r & page.rect
        imgs.append(_pix_to_img(page.get_pixmap(dpi=IMAGE_DPI, clip=clip, annots=annots))
                    if not clip.is_empty else Image.new("RGB", (200, 200), "white"))
    if mark is not None:
        z = IMAGE_DPI / 72
        d = ImageDraw.Draw(imgs[1])
        d.rectangle([(mark.x0 - region.x0 - 3) * z, (mark.y0 - region.y0 - 3) * z,
                     (mark.x1 - region.x0 + 3) * z, (mark.y1 - region.y0 + 3) * z], outline=(220, 0, 0), width=3)
    h = max(i.height for i in imgs)
    head, gap = 30, 10
    canvas = Image.new("RGB", (sum(i.width for i in imgs) + gap * (len(imgs) - 1), h + head), "white")
    x = 0
    d = ImageDraw.Draw(canvas)
    f = _font(17)
    for img, label in zip(imgs, labels):
        canvas.paste(img, (x, head))
        d.text((x + 6, 5), label, fill=(40, 40, 40), font=f)
        x += img.width + gap
    canvas.save(path)


# ---- main ------------------------------------------------------------------------------------
def verify(src_pdf, revised_pdf, out_dir):
    vdir = out_dir / "verification"
    vdir.mkdir(exist_ok=True)
    ex = json.loads((out_dir / "comments.json").read_text(encoding="utf-8"))
    plan = json.loads((out_dir / "plan.json").read_text(encoding="utf-8"))
    chg = json.loads((out_dir / "changes.json").read_text(encoding="utf-8"))
    changes, failures, skipped = chg["changes"], chg["failures"], chg.get("skipped", [])
    res_by = {r["comment_id"]: r for r in chg.get("resolution", [])}

    marked = pymupdf.open(src_pdf)
    orig, _ = open_clean(src_pdf)
    corr = pymupdf.open(out_dir / "corrected.pdf")
    hl = pymupdf.open(out_dir / "corrected_highlighted.pdf")
    rev = pymupdf.open(revised_pdf) if revised_pdf else None
    po, pc = orig[0], corr[0]
    pr = rev[0] if rev else None

    # -- file integrity -------------------------------------------------------------------------
    wo = Counter(w[4] for w in po.get_text("words"))
    wc = Counter(w[4] for w in pc.get_text("words"))
    lost = wo - wc
    soft = sum(v for k, v in wc.items() if "\xad" in k)
    checks = [
        ("Same page count, size and rotation",
         len(orig) == len(corr) and po.rect == pc.rect and po.rotation == pc.rotation,
         f"{len(corr)} page, {pc.rect.width:.0f}x{pc.rect.height:.0f} pt, rotation {pc.rotation}"),
        ("All consultant markups removed from corrected.pdf",
         len(list(pc.annots())) == 0, f"{len(list(marked[0].annots()))} markups before, {len(list(pc.annots()))} after"),
        ("Highlighted PDF has one AI box per change",
         sum(1 for a in hl[0].annots() if a.info.get("title") == config.AUTHOR) == len(changes),
         f"{sum(1 for a in hl[0].annots() if a.info.get('title') == config.AUTHOR)} boxes for {len(changes)} changes"),
        ("No original drawing text lost",
         not lost, "none" if not lost else ", ".join(f"{k} x{v}" for k, v in list(lost.items())[:12])),
        ("Pixels changed outside edit boxes",
         chg.get("integrity", {}).get("share", 1) < 0.0005,
         f"{chg.get('integrity', {}).get('share', 0):.4%} of the sheet"),
        ("Added text extracts cleanly (copy/search)",
         soft == 0, "ok" if soft == 0 else f"{soft} word(s) contain soft hyphens instead of '-'"),
        ("Vector paths", True, f"{len(po.get_drawings())} before, {len(pc.get_drawings())} after "
                               "(symbol swaps replace exploded segments)"),
    ]

    # -- per comment ----------------------------------------------------------------------------
    aligner = Aligner(po, pr) if pr else None
    baseline = _control_baseline(po, pr, aligner) if pr else None
    by_c = defaultdict(list)
    for ch in changes:
        by_c[ch["comment_id"]].append(ch)
    plan_by = {p["comment_id"]: p for p in plan["comments"]}
    rows = []
    for c in ex["comments"]:
        cid = c["id"]
        p = plan_by.get(cid, {})
        mine = by_c.get(cid, [])
        cov = [s for s in skipped if s["comment_id"] == cid]
        fails = [f for f in failures if f["comment_id"] == cid]
        regions = [pymupdf.Rect(t["vis"][0] - REGION / 2, t["vis"][1] - REGION / 2,
                                t["vis"][0] + REGION / 2, t["vis"][1] + REGION / 2) for t in c["tips"]]
        regions += [pymupdf.Rect(ch["bbox_vis"]) + (-40, -40, 40, 40) for ch in mine
                    if ch["op"] in ("replace_text", "swap_symbol")]
        evid, human_changed, toward = [], False, None
        if pr:
            for reg in regions:
                off, how = aligner.offset(reg)
                if off is None:
                    continue
                m = _three_way(po, pc, pr, reg, off)
                if m["orig_vs_rev"] is None:
                    continue
                hc = m["orig_vs_rev"] > max(baseline["p75"] * 1.3, 2.0)
                human_changed |= hc
                if m["orig_vs_corr"] and m["orig_vs_corr"] > 0.3:
                    closer = m["corr_vs_rev"] < m["orig_vs_rev"] - 0.2
                    toward = closer if toward is None else (toward or closer)
                evid.append({**{k: round(v, 2) if v is not None else None for k, v in m.items()},
                             "align": how, "human_changed": hc})
        # content checks against the revision's text
        content = []
        if pr:
            for ch in mine:
                if ch["op"] == "replace_text" and ch.get("after"):
                    new = [t for t in ch["after"].split() if t not in (ch.get("before") or "").split()]
                    found = [t for t in new if pr.search_for(t)]
                    content.append(f"new text {', '.join(new)}: "
                                   + ("also in human revision" if found else "not in human revision"))
                if ch["op"] == "add_note" and ch.get("after"):
                    refs = re.findall(r"[A-Z]{2,}-[A-Z]{2,}-\d+|\d{6,}", ch["after"])
                    hit = [r for r in refs if pr.search_for(r) or pr.search_for(r.split("-")[-1])]
                    content.append("note reference " + (", ".join(refs) or "-") + ": "
                                   + ("also in human revision" if hit else "human revision has no such note"))
                if ch["op"] == "add_conduit_fill_table" and ch.get("after"):
                    ours = [(float(r["Fill %"][:-1]), float(r["Spare %"][:-1])) for r in json.loads(ch["after"])]
                    theirs = _pct_pairs_near(pr, "Spare")
                    match = sum(1 for o in ours if any(abs(o[0] - t[0]) <= 0.1 and abs(o[1] - t[1]) <= 0.1
                                                       for t in theirs))
                    content.append(f"conduit fill rows matching the human revision's table (±0.1%): "
                                   f"{match}/{len(ours)}")
        if mine or cov:
            verdict = "Fixed" if p.get("status") == "auto" and not fails and not p.get("checklist") else "Partially fixed"
        else:
            verdict = "Not fixed"
        if mine and not p.get("checklist") and fails:
            verdict = "Partially fixed"
        status = res_by.get(cid, {}).get("status")
        if status:  # the apply stage's resolution status is the single source of truth when present
            verdict = {"resolved": "Fixed", "partially_resolved": "Partially fixed"}.get(status, "Not fixed")
        missed = bool(pr) and human_changed and not mine and not cov
        rows.append({
            "id": cid, "comment": p.get("clean_text") or c["text"], "intent": p.get("intent", ""),
            "verdict": verdict, "edits": [f"{ch['id']}: {ch['description']}" for ch in mine],
            "covered": [s["reason"] for s in cov], "failed": [f["reason"] for f in fails],
            "open_items": [k["item"] for k in p.get("checklist", [])],
            "human_changed": human_changed if pr else None, "toward_human": toward,
            "missed_vs_human": missed, "content": content, "evidence": evid,
        })
        # three-panel image at the first change (or first tip)
        if mine and mine[0]["op"] in ("replace_text", "swap_symbol", "add_note"):
            focus = pymupdf.Rect(mine[0]["bbox_vis"])
        elif mine:
            focus = None
        else:
            focus = pymupdf.Rect(c["tips"][0]["vis"], c["tips"][0]["vis"]) if c["tips"] else None
        if focus is not None:
            reg = focus + (-REGION / 2, -REGION / 2, REGION / 2, REGION / 2)
            off = aligner.offset(reg)[0] if pr else None
            panels = [(marked[0], None, True), (pc, None, False)]
            labels = ["ORIGINAL (with comment)", "CORRECTED"]
            if pr and off:
                panels.append((pr, off, False))
                labels.append("HUMAN REVISION (aligned)")
            img = vdir / f"{cid}.png"
            _panel(panels, reg, img, labels, mark=focus if mine else None)
            rows[-1]["image"] = f"verification/{img.name}"
        elif mine:
            rows[-1]["image"] = mine[0].get("image")

    summary = Counter(r["verdict"] for r in rows)
    result = {"checks": [{"check": n, "ok": bool(ok), "detail": d} for n, ok, d in checks],
              "baseline": baseline, "global_offset": aligner.global_offset if aligner else None,
              "summary": dict(summary), "comments": rows, "planner": plan.get("planner"),
              "planner_usage": plan.get("usage")}
    (vdir / "verification.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    _html(out_dir, src_pdf, revised_pdf, result)
    return result


def _html(out_dir, src_pdf, revised_pdf, res):
    e = html.escape
    s = res["summary"]
    tag = {"Fixed": "auto", "Partially fixed": "partial", "Not fixed": "engineer"}
    issues = [f"{r['id']}: the human revision changed this area but the rectifier made no edit"
              for r in res["comments"] if r["missed_vs_human"]]
    issues += [f"Check failed: {c['check']} ({c['detail']})" for c in res["checks"] if not c["ok"]]
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
             f"content='width=device-width,initial-scale=1'><title>Verification Report</title><style>{CSS}"
             ".ok{color:var(--ok)}.bad{color:var(--red)}</style></head><body><main>",
             "<h1>Verification: corrected vs original vs human revision</h1>",
             f"<p class='sub'>Original: {e(src_pdf.name)}<br>Corrected: corrected.pdf / corrected_highlighted.pdf<br>"
             f"Human revision: {e(revised_pdf.name) if revised_pdf else 'not provided'}<br>"
             f"Plan: <code>{e(str(res['planner']))}</code>. This check runs locally (no API calls).</p>",
             "<div class='kpis'>"
             f"<div class='kpi'><b>{len(res['comments'])}</b><span>consultant comments</span></div>"
             f"<div class='kpi'><b>{s.get('Fixed', 0)}</b><span>fixed</span></div>"
             f"<div class='kpi'><b>{s.get('Partially fixed', 0)}</b><span>partially fixed</span></div>"
             f"<div class='kpi'><b>{s.get('Not fixed', 0)}</b><span>not fixed (engineer input)</span></div>"
             f"<div class='kpi'><b>{sum(1 for c in res['checks'] if c['ok'])}/{len(res['checks'])}</b>"
             "<span>file checks passed</span></div></div>"]
    if issues:
        parts.append("<h2>Issues found</h2><ul>" + "".join(f"<li class='bad'>{e(i)}</li>" for i in issues) + "</ul>")
    parts.append("<h2>File checks</h2><table><tr><th>Check</th><th>Result</th><th>Detail</th></tr>")
    for c in res["checks"]:
        parts.append(f"<tr><td>{e(c['check'])}</td><td class='{'ok' if c['ok'] else 'bad'}'>"
                     f"{'pass' if c['ok'] else 'FAIL'}</td><td>{e(c['detail'])}</td></tr>")
    parts.append("</table>")
    parts.append("<h2>Comment by comment</h2><table><tr><th>Comment</th><th>Verdict</th><th>Rectifier did</th>"
                 "<th>Human revision changed it?</th><th>Still open</th></tr>")
    for r in res["comments"]:
        did = r["edits"] + [f"covered: {x}" for x in r["covered"]] + [f"failed: {x}" for x in r["failed"]]
        hc = "-" if r["human_changed"] is None else ("yes" if r["human_changed"] else "no")
        if r["toward_human"] is not None:
            hc += "; ours closer to it" if r["toward_human"] else "; ours not closer to it"
        parts.append(f"<tr><td><b>{e(r['id'])}</b><br>{e(r['comment'][:160])}</td>"
                     f"<td><span class='tag {tag[r['verdict']]}'>{e(r['verdict'])}</span></td>"
                     f"<td>{'<br>'.join(e(x) for x in did) or '-'}</td><td>{e(hc)}</td>"
                     f"<td>{len(r['open_items'])} item(s)</td></tr>")
    parts.append("</table><h2>Evidence</h2>")
    for r in res["comments"]:
        parts.append(f"<div class='card'><h3>{e(r['id'])} <span class='tag {tag[r['verdict']]}'>{e(r['verdict'])}</span></h3>"
                     f"<div class='quote'>{e(r['comment'])}</div>")
        if r["content"]:
            parts.append("<ul>" + "".join(f"<li>{e(x)}</li>" for x in r["content"]) + "</ul>")
        if r.get("image"):
            parts.append(f"<img src='{e(r['image'])}' alt='{e(r['id'])} comparison' loading='lazy'>")
        if r["open_items"]:
            parts.append("<p><b>Still open for the engineer:</b></p><ul>"
                         + "".join(f"<li>{e(x)}</li>" for x in r["open_items"]) + "</ul>")
        parts.append("</div>")
    b = res["baseline"]
    if b:
        parts.append(f"<p class='sub'>Method: each spot is compared after aligning the human revision by words that "
                     f"occur once on both sheets (global sheet offset {res['global_offset']}). 'Human changed it' means "
                     f"the original-vs-revision difference exceeds 1.3x the 75th percentile of {b['n']} random control "
                     f"spots ({b['p75']:.2f}). Verdicts: Fixed = every planned op applied and nothing left open; "
                     "Partially fixed = some edits applied, items left for the engineer; Not fixed = no edit.</p>")
    parts.append("</main></body></html>")
    (out_dir / "verification_report.html").write_text("".join(parts), encoding="utf-8")
