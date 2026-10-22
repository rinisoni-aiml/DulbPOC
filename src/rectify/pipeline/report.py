"""Stage 4: highlighted PDF, before/after images, integrity check, HTML report, engineer checklist."""
import html
import json

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont

from ..core import config
from ..pdf.geometry import open_clean


def _pix_to_img(pix):
    return Image.frombytes("RGB", (pix.w, pix.h), pix.samples)


def _font(size):
    try:
        return ImageFont.truetype(str(config.WIN_FONTS / "arialbd.ttf"), size)
    except OSError:
        return ImageFont.load_default()


def highlighted_pdf(corrected_pdf, changes, path):
    """Copy of the corrected sheet with a toggleable red box + note per change (Acrobat comments)."""
    doc = pymupdf.open(corrected_pdf)
    for ch in changes:
        page = doc[ch["page"]]
        r = pymupdf.Rect(ch["bbox_pdf"]) + (-4, -4, 4, 4)
        a = page.add_rect_annot(r)
        a.set_colors(stroke=config.HIGHLIGHT_RGB)
        a.set_border(width=1.5, dashes=[3, 2])
        a.set_info(title=config.AUTHOR, subject=f"{ch['id']} / {ch['comment_id']}",
                   content=f"{ch['id']} ({ch['comment_id']}): {ch['description']}")
        a.update()
    doc.save(str(path), garbage=3, deflate=True)


def before_after(src_clean, corrected, changes, out_dir):
    """Side-by-side PNG per change: original (markups hidden) vs corrected, same clip."""
    folder = out_dir / "before_after"
    folder.mkdir(exist_ok=True)
    for ch in changes:
        b = pymupdf.Rect(ch["bbox_vis"])
        pad = config.BEFORE_AFTER_PAD
        clip = b + (-pad, -pad, pad, pad)
        page_a, page_b = src_clean[ch["page"]], corrected[ch["page"]]
        clip &= page_a.rect
        # Keep big changes (tables) at a readable size.
        dpi = config.BEFORE_AFTER_DPI if max(clip.width, clip.height) < 400 else 90
        imgs = [_pix_to_img(p.get_pixmap(dpi=dpi, clip=clip, annots=False)) for p in (page_a, page_b)]
        z = dpi / 72
        draw = ImageDraw.Draw(imgs[1])
        draw.rectangle([(b.x0 - clip.x0 - 3) * z, (b.y0 - clip.y0 - 3) * z,
                        (b.x1 - clip.x0 + 3) * z, (b.y1 - clip.y0 + 3) * z], outline=(220, 0, 0), width=3)
        w, h = imgs[0].size
        head = 34
        canvas = Image.new("RGB", (w * 2 + 12, h + head), "white")
        canvas.paste(imgs[0], (0, head))
        canvas.paste(imgs[1], (w + 12, head))
        d = ImageDraw.Draw(canvas)
        d.rectangle([w, 0, w + 11, h + head], fill=(200, 200, 200))
        f = _font(20)
        d.text((8, 6), f"BEFORE  ({ch['comment_id']})", fill=(60, 60, 60), font=f)
        d.text((w + 20, 6), f"AFTER  {ch['id']}", fill=(200, 0, 0), font=f)
        path = folder / f"{ch['id']}_{ch['comment_id']}.png"
        canvas.save(path)
        ch["image"] = f"before_after/{path.name}"


def overview(corrected, changes, extracted, out_dir):
    """Whole-sheet PNG: change boxes (red) and the comment boxes they answer (orange)."""
    page = corrected[0]
    dpi = 40
    img = _pix_to_img(page.get_pixmap(dpi=dpi, annots=False))
    z = dpi / 72
    d = ImageDraw.Draw(img)
    f = _font(14)
    for c in extracted["comments"]:
        if c["page"] == 0:
            r = c["box_vis"]
            d.rectangle([r[0] * z, r[1] * z, r[2] * z, r[3] * z], outline=(255, 150, 0), width=2)
            d.text((r[0] * z + 2, r[1] * z + 2), c["id"], fill=(255, 120, 0), font=f)
    for ch in changes:
        if ch["page"] == 0:
            r = ch["bbox_vis"]
            d.rectangle([r[0] * z - 6, r[1] * z - 6, r[2] * z + 6, r[3] * z + 6], outline=(220, 0, 0), width=3)
            d.text((r[2] * z + 8, r[1] * z - 6), ch["id"], fill=(220, 0, 0), font=f)
    img.save(out_dir / "overview.png")


def integrity(src_clean, corrected, changes):
    """Share of sheet pixels that changed OUTSIDE the recorded edit boxes (should be ~0)."""
    dpi = 36
    z = dpi / 72
    total, outside = 0, 0
    for pno in range(len(corrected)):
        a = np.asarray(_pix_to_img(src_clean[pno].get_pixmap(dpi=dpi, annots=False)).convert("L"), dtype=np.int16)
        b = np.asarray(_pix_to_img(corrected[pno].get_pixmap(dpi=dpi, annots=False)).convert("L"), dtype=np.int16)
        diff = np.abs(a - b) > 40
        for ch in changes:
            if ch["page"] == pno:
                r = ch["bbox_vis"]
                diff[max(0, int((r[1] - 8) * z)):int((r[3] + 8) * z) + 1,
                     max(0, int((r[0] - 8) * z)):int((r[2] + 8) * z) + 1] = False
        total += diff.size
        outside += int(diff.sum())
    return {"changed_pixels_outside_edits": outside, "share": outside / max(total, 1)}


def _checklist(plan, failures):
    items = []
    for p in plan["comments"]:
        for k in p.get("checklist", []):
            items.append({"comment_id": p["comment_id"], **k, "source": "plan"})
    for f in failures:
        items.append({"comment_id": f["comment_id"], "item": f"Could not auto-apply {f['op']}: {f['reason']}",
                      "inputs_needed": ["drafter to apply manually"], "where_on_sheet": "see comment",
                      "source": "apply"})
    return items


def _md_checklist(items, path):
    lines = ["# Engineer checklist", "",
             "Items the rectifier could not settle from the drawing alone.", "",
             "| Comment | Action | Inputs needed | Where |", "|---|---|---|---|"]
    for i in items:
        lines.append(f"| {i['comment_id']} | {i['item']} | {', '.join(i['inputs_needed'])} | {i['where_on_sheet']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


CSS = """
:root{--ink:#1d1f23;--muted:#5f6670;--line:#e3e5e8;--bg:#f7f7f5;--card:#fff;--red:#c40000;--ok:#1f7a3d;--warn:#a15c00}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 16px 60px}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 10px}
.sub{color:var(--muted);margin:0 0 20px}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}.kpi b{display:block;font-size:22px}
.kpi span{color:var(--muted);font-size:13px}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;margin:12px 0}
.card h3{margin:0 0 6px;font-size:16px}.quote{color:var(--muted);border-left:3px solid #f2c200;padding-left:10px;margin:6px 0 10px}
.tag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;border:1px solid var(--line);margin-right:6px}
.auto{color:var(--ok);border-color:var(--ok)}.partial{color:var(--warn);border-color:var(--warn)}.engineer{color:var(--muted)}
img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:4px;margin:6px 0}
table{border-collapse:collapse;width:100%;background:var(--card)}th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top;font-size:14px}
th{background:#f0f1f2}ul{margin:4px 0 4px 18px;padding:0}.fail{color:var(--red)}code{font-size:13px}
.files a{margin-right:14px}
"""


def html_report(out_dir, extracted, plan, changes, failures, checklist, integ, meta):
    e = html.escape
    by_comment = {}
    for ch in changes:
        by_comment.setdefault(ch["comment_id"], []).append(ch)
    status = {p["comment_id"]: p for p in plan["comments"]}
    auto = sum(1 for p in plan["comments"] if p["status"] == "auto" and p["comment_id"] in by_comment)
    usage = plan.get("usage") or {}
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
             f"content='width=device-width,initial-scale=1'><title>Rectification Report</title><style>{CSS}</style></head><body><main>",
             f"<h1>Drawing rectification report</h1><p class='sub'>{e(meta['source_name'])} | planner: "
             f"<code>{e(plan['planner'])}</code>" + (f" | LLM cost ${usage.get('usd', 0):.3f}" if usage else "") + "</p>",
             "<div class='kpis'>",
             f"<div class='kpi'><b>{len(extracted['comments'])}</b><span>consultant comments</span></div>",
             f"<div class='kpi'><b>{len(changes)}</b><span>vector edits applied</span></div>",
             f"<div class='kpi'><b>{len(by_comment)}</b><span>comments with edits ({auto} fully)</span></div>",
             f"<div class='kpi'><b>{len(checklist)}</b><span>engineer checklist items</span></div>",
             f"<div class='kpi'><b>{meta['markups_removed']}</b><span>markups removed</span></div>",
             f"<div class='kpi'><b>{integ['share']:.3%}</b><span>sheet changed outside edits</span></div>",
             "</div>",
             "<p class='files'><a href='corrected.pdf'>corrected.pdf</a><a href='corrected_highlighted.pdf'>"
             "corrected_highlighted.pdf</a><a href='checklist.md'>checklist.md</a><a href='changes.json'>changes.json</a>"
             "<a href='plan.json'>plan.json</a></p>",
             "<h2>Sheet overview</h2><p class='sub'>Orange: consultant comments. Red: AI edits.</p>"
             "<img src='overview.png' alt='Corrected sheet with edit locations'>",
             "<h2>Comments</h2>"]
    for c in extracted["comments"]:
        p = status.get(c["id"], {})
        st = p.get("status", "engineer")
        parts.append(f"<div class='card'><h3>{e(c['id'])} <span class='tag {st}'>{e(st)}</span>"
                     f"<span class='tag'>{e(p.get('scope', ''))}</span></h3>"
                     f"<div class='quote'>{e(p.get('clean_text') or c['text'])}</div>")
        if p.get("intent"):
            parts.append(f"<p><b>Intent:</b> {e(p['intent'])}</p>")
        for ch in by_comment.get(c["id"], []):
            parts.append(f"<p><b>{e(ch['id'])}</b>: {e(ch['description'])}</p>")
            if ch.get("image"):
                parts.append(f"<img src='{e(ch['image'])}' alt='Before and after {e(ch['id'])}' loading='lazy'>")
        notes = [f"<li>Covered ({e(s['op'])}): {e(s['reason'])}</li>"
                 for s in meta.get("skipped", []) if s["comment_id"] == c["id"]]
        notes += [f"<li class='fail'>Not applied ({e(f['op'])}): {e(f['reason'])}</li>"
                  for f in failures if f["comment_id"] == c["id"]]
        if notes:
            parts.append("<ul>" + "".join(notes) + "</ul>")
        if p.get("checklist"):
            parts.append("<p><b>For the engineer:</b></p><ul>" + "".join(
                f"<li>{e(k['item'])} <i>(needs: {e(', '.join(k['inputs_needed']))})</i></li>"
                for k in p["checklist"]) + "</ul>")
        parts.append("</div>")
    parts.append("<h2>Engineer checklist</h2><table><tr><th>Comment</th><th>Action</th><th>Inputs needed</th><th>Where</th></tr>")
    for i in checklist:
        parts.append(f"<tr><td>{e(i['comment_id'])}</td><td>{e(i['item'])}</td>"
                     f"<td>{e(', '.join(i['inputs_needed']))}</td><td>{e(i['where_on_sheet'])}</td></tr>")
    parts.append("</table>")
    others = extracted.get("other_markups", [])
    if others:
        parts.append("<h2>Other markups (not comments)</h2><ul>" + "".join(
            f"<li>{e(o['kind'])}: {e(o['text'] or '-')}</li>" for o in others) + "</ul>")
    parts.append("<p class='sub'>Integrity check: the original sheet (markups hidden) and the corrected sheet are "
                 f"rendered and compared; {integ['changed_pixels_outside_edits']} pixel(s) differ outside the "
                 "recorded edit boxes.</p></main></body></html>")
    (out_dir / "report.html").write_text("".join(parts), encoding="utf-8")


def build(src_pdf, out_dir, extracted, plan, changes, failures, markups_removed, skipped=()):
    corrected_pdf = out_dir / "corrected.pdf"
    src_clean, _ = open_clean(src_pdf)
    corrected = pymupdf.open(corrected_pdf)
    highlighted_pdf(corrected_pdf, changes, out_dir / "corrected_highlighted.pdf")
    before_after(src_clean, corrected, changes, out_dir)
    overview(corrected, changes, extracted, out_dir)
    integ = integrity(src_clean, corrected, changes)
    checklist = _checklist(plan, failures)
    _md_checklist(checklist, out_dir / "checklist.md")
    (out_dir / "changes.json").write_text(json.dumps({"changes": changes, "failures": failures,
                                                      "skipped": list(skipped), "integrity": integ},
                                                     indent=2, ensure_ascii=False), encoding="utf-8")
    html_report(out_dir, extracted, plan, changes, failures, checklist, integ,
                {"source_name": src_pdf.name, "markups_removed": markups_removed, "skipped": list(skipped)})
    return integ, checklist
