"""Stage 3: apply planned ops to the PDF's vector layer with PyMuPDF.

Every op is validated against the drawing first; an op that cannot be placed safely is not
guessed at but recorded as a failure, which the report turns into an engineer checklist item.
Redactions only ever remove glyphs (text ops) or paths fully inside a device symbol (symbol
swaps), so the rest of the drawing stays byte-for-byte the same content.
"""
import json
import textwrap

import pymupdf

from ..core import config
from ..domain.conduit import conduit_fill, find_cable_table
from ..pdf.geometry import (FreeSpace, dir_to_rotate, font_by_family, int_to_rgb, open_clean, rect_list,
                     span_font, text_length, text_lines, to_pdf, to_vis, upright_rotate)

TEXT_KEEP = getattr(pymupdf, "PDF_REDACT_TEXT_NONE", 1)


def _dot(p, d):
    return p.x * d[0] + p.y * d[1]


def _dist_point_rect(pt, r):
    dx = max(r.x0 - pt.x, 0, pt.x - r.x1)
    dy = max(r.y0 - pt.y, 0, pt.y - r.y1)
    return (dx * dx + dy * dy) ** 0.5


def _is_dark(d):
    """Black/near-black stroke or fill: device symbols, not grey hatch or coloured services."""
    for c in (d.get("color"), d.get("fill")):
        if c and max(c) <= config.SYMBOL_DARK_MAX:
            return True
    return False


class Rectifier:
    def __init__(self, pdf_path, extracted, inputs=None):
        self.doc, self.markups_removed = open_clean(pdf_path)
        self.ex = extracted
        self.inputs = inputs or {}
        self.changes, self.failures, self.skipped = [], [], []
        self._free, self._drawings = {}, {}
        self.pages = {p["page"]: p for p in extracted["pages"]}

    # ---- shared helpers -------------------------------------------------------------
    def _page(self, pno):
        return self.doc[pno]

    def _legend(self, pno):
        r = self.pages[pno].get("legend_vis")
        return pymupdf.Rect(r) if r else None

    def _free_space(self, pno):
        if pno not in self._free:
            self._free[pno] = FreeSpace(self._page(pno))
        return self._free[pno]

    def _drawings_of(self, pno):
        """Original vector paths (pdf space) with their vis rects; read once, before edits add paths."""
        if pno not in self._drawings:
            page = self._page(pno)
            ds = page.get_drawings()
            for d in ds:
                d["vis"] = to_vis(page, d["rect"])
            self._drawings[pno] = ds
        return self._drawings[pno]

    def _record(self, c, op, page, pdf_rect, description, before=None, after=None):
        vis = to_vis(page, pdf_rect)
        self.changes.append({
            "id": f"CH-{len(self.changes) + 1:02d}", "comment_id": c["id"], "page": page.number,
            "op": op["type"], "description": description, "before": before, "after": after,
            "bbox_pdf": rect_list(pdf_rect), "bbox_vis": rect_list(vis),
        })
        self._free_space(page.number).occupy(vis + (-6, -6, 6, 6))

    def _fail(self, c, op, why):
        self.failures.append({"comment_id": c["id"], "op": op["type"], "reason": why, "op_detail": op})

    def _skip(self, c, op, why):
        """Op already satisfied by another comment's edit: not a failure, nothing to draw."""
        self.skipped.append({"comment_id": c["id"], "op": op["type"], "reason": why})

    # ---- replace_text -----------------------------------------------------------------
    def replace_text(self, c, op):
        find, repl, where = op.get("find"), op.get("replace"), op.get("where") or "sheet"
        if not find or repl is None:
            return self._fail(c, op, "replace_text without find/replace")
        page = self._page(c["page"])
        tips = [pymupdf.Point(t["pdf"]) for t in c["tips"]]
        legend = self._legend(c["page"])
        lines = text_lines(page)
        targets = []
        for hit in page.search_for(find):
            line = next((l for l in lines if l["pdf"].intersects(hit) and find in l["text"]), None)
            if line is None:  # search_for is case-insensitive; we are not
                continue
            if where == "tip" and not any(_dist_point_rect(t, hit) <= config.TIP_SEARCH_RADIUS for t in tips):
                continue
            if where == "legend" and not (legend and legend.contains(to_vis(page, hit))):
                continue
            targets.append((hit, line))
        if not targets:
            done = next((ch for ch in self.changes if ch["op"] == "replace_text" and ch["before"] == find), None)
            if done:  # another comment already made this edit
                return self._skip(c, op, f"already done by {done['id']} ({done['comment_id']})")
            return self._fail(c, op, f"text '{find}' not found ({where})")

        for hit, line in targets:
            d = line["dir"]
            span = next((s for s in line["spans"] if pymupdf.Rect(s["bbox"]).intersects(hit)), line["spans"][0])
            origin = pymupdf.Point(span["origin"])
            corners = [hit.tl, hit.tr, hit.bl, hit.br]
            start = min(_dot(p, d) for p in corners)
            avail = max(_dot(p, d) for p in corners) - start
            point = origin + pymupdf.Point(d) * (start - _dot(origin, d))
            fontname, fontfile = span_font(span)
            if fontfile and "-" in repl:
                # Embedded TTFs map '-' to a soft hyphen on extraction; keep the text searchable.
                fontname, fontfile = font_by_family(config.NEW_TEXT_FONT)
            size = span["size"]
            new_len = text_length(repl, fontname, fontfile, size)
            if new_len > avail * config.FIT_SLACK:
                size *= max(config.MIN_FONT_SCALE, avail * config.FIT_SLACK / new_len)
                new_len = text_length(repl, fontname, fontfile, size)
            if line["vis"].width < 80 and abs(line["vis"].width - to_vis(page, hit).width) < 2:
                point += pymupdf.Point(d) * ((avail - new_len) / 2)  # short centred caption: keep centred

            # Glyph-only redaction, shrunk across the baseline so neighbouring lines are untouched.
            thick = (pymupdf.Rect(hit).width if abs(d[1]) > abs(d[0]) else pymupdf.Rect(hit).height)
            shrink = thick * 0.15
            red = (hit + (shrink, 0, -shrink, 0)) if abs(d[1]) > abs(d[0]) else (hit + (0, shrink, 0, -shrink))
            page.add_redact_annot(red)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                  text=pymupdf.PDF_REDACT_TEXT_REMOVE)
            page.insert_text(point, repl, fontsize=size, fontname=fontname, fontfile=fontfile,
                             color=int_to_rgb(span["color"]), rotate=dir_to_rotate(d))
            end = point + pymupdf.Point(d) * new_len * 1.06  # small margin for metric differences
            bbox = pymupdf.Rect(hit) | pymupdf.Rect(point, end).normalize()
            # Size the change box from the text line actually on the page now.
            for l in text_lines(page):
                if repl in l["text"] and l["pdf"].intersects(bbox + (-2, -2, 2, 2)):
                    bbox |= l["pdf"]
            self._record(c, op, page, bbox, f"Text '{find}' -> '{repl}'", before=find, after=repl)

    # ---- swap_symbol -------------------------------------------------------------------
    def _legend_symbol(self, pno, label):
        """Paths of the legend symbol drawn left of `label`, and their vis bbox."""
        page = self._page(pno)
        legend = self._legend(pno)
        line = next((l for l in text_lines(page)
                     if l["text"] == label and legend and legend.contains(l["vis"])), None)
        if line is None:
            return None, None
        L = line["vis"]
        cy = (L.y0 + L.y1) / 2
        paths = [d for d in self._drawings_of(pno)
                 if L.x0 - 110 <= d["vis"].x0 and d["vis"].x1 <= L.x0 - 3
                 and cy - 18 <= d["vis"].y0 and d["vis"].y1 <= cy + 18
                 and d["vis"].width < 60 and d["vis"].height < 36]
        if not paths:
            return None, None
        box = pymupdf.Rect(paths[0]["vis"])
        for d in paths[1:]:
            box |= d["vis"]
        return paths, box

    def _symbol_at(self, pno, tip_vis):
        """Device symbol at a leader tip.

        CAD exports often explode blocks into many tiny segments, so a symbol is found as the
        cluster of short, dark segments touching the tip. Long lines (dimensions, extension lines,
        walls) and grey/coloured ink (hatch, conduits, pull boxes) are never part of it.
        """
        seg_max, cap, g = config.SYMBOL_SEGMENT_MAX, config.SYMBOL_MAX_SIZE, config.SYMBOL_JOIN_GAP
        window = pymupdf.Rect(tip_vis, tip_vis) + (-cap, -cap, cap, cap)
        small = [d for d in self._drawings_of(pno)
                 if window.intersects(d["vis"]) and max(d["vis"].width, d["vis"].height) <= seg_max
                 and _is_dark(d)]
        cluster = [d for d in small if _dist_point_rect(tip_vis, d["vis"]) <= config.SYMBOL_TIP_DIST]
        if not cluster:
            return [], None
        box = pymupdf.Rect(cluster[0]["vis"])
        for d in cluster[1:]:
            box |= d["vis"]
        grown = True
        while grown:
            grown = False
            for d in small:
                if any(d is x for x in cluster) or not (box + (-g, -g, g, g)).intersects(d["vis"]):
                    continue
                nb = box | d["vis"]
                if nb.width <= cap and nb.height <= cap:
                    cluster.append(d)
                    box = nb
                    grown = True
        return cluster, box

    def swap_symbol(self, c, op):
        pno = c["page"]
        page = self._page(pno)
        src_paths, src_box = self._legend_symbol(pno, op.get("to_label") or "")
        from_paths, from_box = self._legend_symbol(pno, op.get("from_label") or "")
        if not src_paths:
            return self._fail(c, op, f"legend symbol for '{op.get('to_label')}' not found")
        ref = from_box or src_box
        done = 0
        for t in c["tips"]:
            tip_vis = pymupdf.Point(t["vis"])
            cluster, dst = self._symbol_at(pno, tip_vis)
            if not cluster or min(dst.width, dst.height) < 3:
                self._fail(c, op, f"no device symbol found at tip {t['vis']}")
                continue
            # A rotated device (aspect flipped vs its legend entry) is left to the drafter.
            if (dst.width >= dst.height) != (ref.width >= ref.height):
                self._fail(c, op, f"device at tip {t['vis']} is rotated relative to the legend; left for drafter")
                continue
            # Scale so the new symbol takes the old one's width (like-for-like with its legend entry).
            k = min(dst.width / ref.width, dst.height / ref.height)
            # Remove the old symbol: only paths entirely inside its box, no text, no images.
            page.add_redact_annot(to_pdf(page, dst + (-0.8, -0.8, 0.8, 0.8)))
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                  text=TEXT_KEEP)
            # Redraw the legend's symbol there, as vectors (pdf -> vis -> scale/move -> pdf).
            sc, dc = (src_box.tl + src_box.br) / 2, (dst.tl + dst.br) / 2
            m = (page.rotation_matrix * pymupdf.Matrix(1, 0, 0, 1, -sc.x, -sc.y) * pymupdf.Matrix(k, k)
                 * pymupdf.Matrix(1, 0, 0, 1, dc.x, dc.y) * page.derotation_matrix)
            shape = page.new_shape()
            for p in src_paths:
                for item in p["items"]:
                    kind = item[0]
                    if kind == "l":
                        shape.draw_line(item[1] * m, item[2] * m)
                    elif kind == "c":
                        shape.draw_bezier(item[1] * m, item[2] * m, item[3] * m, item[4] * m)
                    elif kind == "re":
                        shape.draw_quad(pymupdf.Rect(item[1]).quad * m)
                    elif kind == "qu":
                        shape.draw_quad(item[1] * m)
                lc = p.get("lineCap")
                shape.finish(color=p.get("color"), fill=p.get("fill"), dashes=p.get("dashes"),
                             even_odd=bool(p.get("even_odd")), closePath=bool(p.get("closePath")),
                             lineCap=max(lc) if isinstance(lc, (tuple, list)) and lc else (lc or 0),
                             lineJoin=int(p.get("lineJoin") or 0), width=(p.get("width") or 0) * k,
                             stroke_opacity=p.get("stroke_opacity") or 1, fill_opacity=p.get("fill_opacity") or 1)
            shape.commit()
            done += 1
            self._record(c, op, page, to_pdf(page, dst),
                         f"Symbol '{op.get('from_label')}' -> '{op.get('to_label')}' at leader tip",
                         before=op.get("from_label"), after=op.get("to_label"))
        return done

    # ---- tables and notes ------------------------------------------------------------------
    def _draw_text_vis(self, page, x, baseline, text, size, font=config.NEW_TEXT_FONT, color=None):
        fontname, fontfile = font_by_family(font)
        page.insert_text(to_pdf(page, pymupdf.Point(x, baseline)), text, fontsize=size,
                         fontname=fontname, fontfile=fontfile, color=color or config.NEW_INK_RGB,
                         rotate=upright_rotate(page))
        return text_length(text, fontname, fontfile, size)

    def _draw_line_vis(self, shape, page, a, b):
        shape.draw_line(to_pdf(page, pymupdf.Point(a)), to_pdf(page, pymupdf.Point(b)))

    def add_conduit_fill_table(self, c, op):
        pno = c["page"]
        done = next((ch for ch in self.changes if ch["op"] == "add_conduit_fill_table" and ch["page"] == pno), None)
        if done:  # one table per sheet answers every comment that asks for it
            return self._skip(c, op, f"conduit fill table already added by {done['id']} ({done['comment_id']})")
        page = self._page(pno)
        table_rect, cables = find_cable_table(page, text_lines(page))
        if not cables:
            return self._fail(c, op, "no 'Cable Type' table with outer diameters found on the sheet")
        rows, conduit = conduit_fill(cables, self.inputs.get("conduit"), self.inputs.get("cable_qty"),
                                     self.inputs.get("conduit_overrides"))
        cols = list(rows[0].keys())
        fs, pad = 9.0, 4.0
        fontname, fontfile = font_by_family(config.NEW_TEXT_FONT)
        head = [textwrap.wrap(h, 14) or [h] for h in cols]
        widths = [max([text_length(w, fontname, fontfile, fs) for w in head[i]]
                      + [text_length(r[col], fontname, fontfile, fs) for r in rows]) + 2 * pad
                  for i, col in enumerate(cols)]
        row_h = fs * 1.9
        head_h = fs * 1.25 * max(len(h) for h in head) + 2 * pad
        title_h, foot_h = fs * 2.2, fs * 3.2
        W, H = sum(widths), title_h + head_h + row_h * len(rows) + foot_h
        near = pymupdf.Point((table_rect.x0 + table_rect.x1) / 2, table_rect.y1 + H / 2)
        box, ink = self._free_space(pno).find(W, H, near)
        if box is None:
            return self._fail(c, op, "no free area on the sheet for the conduit fill table")

        shape = page.new_shape()
        x0, y = box.x0, box.y0
        self._draw_text_vis(page, x0, y + fs * 1.3,
                            "CONDUIT FILL CALCULATION - TYPICAL ONE DOOR ACCESS CONTROL CABLES", fs * 1.15)
        top = y + title_h
        ys = [top, top + head_h] + [top + head_h + row_h * (i + 1) for i in range(len(rows))]
        for yy in ys:
            self._draw_line_vis(shape, page, (x0, yy), (x0 + W, yy))
        xs = [x0]
        for w in widths:
            xs.append(xs[-1] + w)
        for xx in xs:
            self._draw_line_vis(shape, page, (xx, top), (xx, ys[-1]))
        shape.finish(color=config.NEW_INK_RGB, width=0.6)
        shape.commit()
        for i, h in enumerate(head):
            for j, part in enumerate(h):
                self._draw_text_vis(page, xs[i] + pad, top + pad + fs * (1.0 + 1.25 * j), part, fs)
        for r_i, r in enumerate(rows):
            base = ys[1 + r_i] + row_h * 0.68
            for i, col in enumerate(cols):
                self._draw_text_vis(page, xs[i] + pad, base, r[col], fs)
        foot = (f"Fill % = total cable cross-section / conduit internal area. Spare % = "
                f"{conduit['max_fill_pct']:g}% max fill - Fill %. Diameters from the cable type table on this sheet.")
        for j, part in enumerate(textwrap.wrap(foot, 150)):
            self._draw_text_vis(page, x0, ys[-1] + fs * (1.4 + 1.2 * j), part, fs * 0.9)
        self._record(c, op, page, to_pdf(page, box),
                     f"Added conduit fill table ({len(rows)} cable types, {conduit['size_mm']:g} mm conduit)"
                     + (f"; placed over {ink:.0%} existing ink" if ink > 0.01 else ""),
                     after=json.dumps(rows, ensure_ascii=False))

    def add_note(self, c, op):
        text = (op.get("text") or "").strip()
        if not text:
            return self._fail(c, op, "add_note without text")
        pno = c["page"]
        page = self._page(pno)
        fs = 9.0
        fontname, fontfile = font_by_family(config.NEW_TEXT_FONT)
        parts = textwrap.wrap(text, 48, break_on_hyphens=False)  # keep refs like ECJ-RFI-001632 whole
        W = max(text_length(p, fontname, fontfile, fs) for p in parts) + 8
        H = fs * 1.3 * len(parts) + 8
        near = pymupdf.Point(c["tips"][0]["vis"]) if c["tips"] else pymupdf.Point(c["box_vis"][:2])
        box, ink = self._free_space(pno).find(W, H, near, pad=6)
        if box is None:
            return self._fail(c, op, "no free area for note")
        shape = page.new_shape()
        shape.draw_quad(box.quad * page.derotation_matrix)
        shape.finish(color=config.NEW_INK_RGB, width=0.5)
        shape.commit()
        for j, p in enumerate(parts):
            self._draw_text_vis(page, box.x0 + 4, box.y0 + 4 + fs * (1.0 + 1.3 * j), p, fs)
        self._record(c, op, page, to_pdf(page, box), f"Added note: {text}", after=text)

    # ---- driver ----------------------------------------------------------------------------
    def run(self, plan):
        by_id = {c["id"]: c for c in self.ex["comments"]}
        handlers = {"replace_text": self.replace_text, "swap_symbol": self.swap_symbol,
                    "add_conduit_fill_table": self.add_conduit_fill_table, "add_note": self.add_note}
        for p in plan["comments"]:
            c = by_id.get(p["comment_id"])
            for op in p.get("ops", []):
                h = handlers.get(op.get("type"))
                if c is None or h is None:
                    self._fail(c or {"id": p["comment_id"]}, op, "unknown comment or op type")
                    continue
                try:
                    h(c, op)
                except Exception as e:  # one bad op must not sink the whole sheet
                    self._fail(c, op, f"{type(e).__name__}: {e}")
        return self.changes, self.failures

    def save(self, path):
        self.doc.save(str(path), garbage=3, deflate=True)
        return path
