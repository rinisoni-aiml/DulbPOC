"""Stage 1: read consultant markups from PDF annotations (exact, no OCR) plus drawing context."""
import json
import math
from collections import Counter

import pymupdf

from .. import config
from ..pdf.geometry import (find_legend, open_clean, rect_list, render_png, text_lines, to_vis,
                     words_near)


def _dist_point_rect(pt, rect):
    dx = max(rect.x0 - pt.x, 0, pt.x - rect.x1)
    dy = max(rect.y0 - pt.y, 0, pt.y - rect.y1)
    return math.hypot(dx, dy)


def _read_annots(page):
    out = []
    for a in page.annots():
        info = a.info
        out.append({
            "type": a.type[1],
            "rect": pymupdf.Rect(a.rect),
            "text": " ".join((info.get("content") or "").split()),
            "author": info.get("title") or "",
            "vertices": [pymupdf.Point(v) for v in (a.vertices or [])],
            "stroke": a.colors.get("stroke") or [],
            "fill": a.colors.get("fill") or [],
        })
    return out


def _classify(annots):
    """Split annotations into comments (with leader tips) and other markups."""
    comments, others, lines, squares = [], [], [], []
    for a in annots:
        if a["type"] == "FreeText":
            short = len(a["text"]) <= config.STATUS_MAX_CHARS
            if not a["text"] or (short and "code" in a["text"].lower()):
                others.append({"kind": "review_status", **a})
            else:
                # FreeText callout: first vertex is the arrow tip, the rest is the knee/box side.
                tips = [a["vertices"][0]] if a["vertices"] else []
                comments.append({**a, "tips": tips})
        elif a["type"] == "Line":
            lines.append(a)
        elif a["type"] == "Square":
            (squares if a["stroke"] else others).append(
                a if a["stroke"] else {"kind": "highlight", **a})
        else:
            others.append({"kind": a["type"].lower(), **a})

    # Free-standing leader lines: the end touching a comment box belongs to it, the other end is a tip.
    for ln in lines:
        if len(ln["vertices"]) < 2:
            continue
        p, q = ln["vertices"][0], ln["vertices"][-1]
        best = min(comments, key=lambda c: min(_dist_point_rect(p, c["rect"]), _dist_point_rect(q, c["rect"])),
                   default=None)
        if best is None:
            continue
        dp, dq = _dist_point_rect(p, best["rect"]), _dist_point_rect(q, best["rect"])
        if min(dp, dq) <= config.LEADER_ATTACH_DIST:
            best["tips"].append(q if dp <= dq else p)
        else:
            others.append({"kind": "leader_line", **ln})

    # Outline boxes mark the target region of the tip that lands on/in them.
    targets = []
    for sq in squares:
        owner = None
        for c in comments:
            for i, t in enumerate(c["tips"]):
                if _dist_point_rect(t, sq["rect"]) <= 15:
                    owner = (c, i)
        if owner:
            targets.append((owner, sq["rect"]))
        else:
            others.append({"kind": "target_box", **sq})
    return comments, others, targets


def extract(pdf_path, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "crops").mkdir(exist_ok=True)
    marked = pymupdf.open(pdf_path)
    clean, _ = open_clean(pdf_path)

    result = {"source_pdf": str(pdf_path), "pages": [], "comments": [], "other_markups": []}
    sheet_text = []
    for pno, page in enumerate(marked):
        cpage = clean[pno]
        comments, others, targets = _classify(_read_annots(page))
        lines = text_lines(cpage)
        words = cpage.get_text("words")
        legend = find_legend(cpage, lines)
        result["pages"].append({
            "page": pno, "rotation": page.rotation, "vis_size": [page.rect.width, page.rect.height],
            "legend_vis": rect_list(legend) if legend else None,
        })

        # Reading order on the sheet as the viewer sees it: left-to-right bands, top-to-bottom.
        band = page.rect.height / 4
        comments.sort(key=lambda c: (int(to_vis(page, c["rect"]).y0 // band), to_vis(page, c["rect"]).x0))
        target_map = {}
        for (c, i), r in targets:
            target_map[(id(c), i)] = r

        for c in comments:
            cid = f"C-{len(result['comments']) + 1:02d}"
            tips = []
            for i, t in enumerate(c["tips"]):
                vis_t = to_vis(page, t)
                crop = pymupdf.Rect(vis_t.x - config.CROP_HALF, vis_t.y - config.CROP_HALF,
                                    vis_t.x + config.CROP_HALF, vis_t.y + config.CROP_HALF)
                crop_path = out_dir / "crops" / f"{cid}_tip{i + 1}.png"
                render_png(page, crop, config.CROP_DPI, crop_path, annots=True)
                tgt = target_map.get((id(c), i))
                tips.append({
                    "pdf": [round(t.x, 1), round(t.y, 1)],
                    "vis": [round(vis_t.x, 1), round(vis_t.y, 1)],
                    "target_box_pdf": rect_list(tgt) if tgt else None,
                    "words": words_near(words, t, config.TIP_RADIUS),
                    "crop": f"crops/{crop_path.name}",
                })
            result["comments"].append({
                "id": cid, "page": pno, "text": c["text"], "author": c["author"],
                "box_pdf": rect_list(c["rect"]), "box_vis": rect_list(to_vis(page, c["rect"])),
                "tips": tips,
            })

        for o in others:
            result["other_markups"].append({
                "page": pno, "kind": o.get("kind", o["type"].lower()), "type": o["type"],
                "text": o["text"], "author": o["author"], "box_pdf": rect_list(o["rect"]),
            })

        # Sheet digest for the planner: unique text lines, counts, one vis position each.
        counts = Counter(l["text"] for l in lines)
        seen = set()
        for l in sorted(lines, key=lambda l: (l["vis"].y0, l["vis"].x0)):
            if l["text"] in seen:
                continue
            seen.add(l["text"])
            in_legend = bool(legend and legend.contains(l["vis"]))
            sheet_text.append({"page": pno, "text": l["text"], "count": counts[l["text"]],
                               "vis": [round(l["vis"].x0), round(l["vis"].y0)], "legend": in_legend})

    (out_dir / "comments.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "sheet_text.json").write_text(json.dumps(sheet_text, indent=1, ensure_ascii=False), encoding="utf-8")
    return result, sheet_text
