"""PyMuPDF helpers: clean copies, rotation-aware geometry, text lines, fonts, free space.

Coordinate spaces
-----------------
* pdf  - unrotated page space. Annotations, words, drawings and redactions live here.
* vis  - what a viewer shows (page.rect, after /Rotate). Clips for rendering and all
         layout decisions (tables, notes, legend columns) are done here.
vis = pdf * page.rotation_matrix, pdf = vis * page.derotation_matrix.
"""
import math
from collections import Counter

import numpy as np
import pymupdf

from ..core import config


def open_clean(path):
    """Open a PDF and delete every annotation (consultant markups). Returns (doc, removed)."""
    doc = pymupdf.open(path)
    removed = 0
    for page in doc:
        for annot in list(page.annots()):
            page.delete_annot(annot)
            removed += 1
    return doc, removed


def to_vis(page, obj):
    return obj * page.rotation_matrix


def to_pdf(page, obj):
    return obj * page.derotation_matrix


def rect_list(r):
    return [round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)]


def upright_rotate(page):
    """insert_text(rotate=...) value that reads upright in the viewer for this page."""
    return page.rotation % 360


def dir_to_rotate(direction):
    """Line direction vector in pdf space -> insert_text rotate value."""
    dx, dy = direction
    return round(math.degrees(math.atan2(-dy, dx))) % 360


def text_lines(page):
    """All text lines of a (clean) page with pdf and vis boxes and their spans."""
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if not text:
                continue
            bbox = pymupdf.Rect(line["bbox"])
            out.append({
                "text": text,
                "pdf": bbox,
                "vis": to_vis(page, bbox),
                "dir": tuple(line["dir"]),
                "spans": line["spans"],
            })
    return out


def words_near(page_words, pdf_point, radius):
    """Words (pdf space) whose centre is within radius of a point, nearest first."""
    hits = []
    for w in page_words:
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        d = math.hypot(cx - pdf_point.x, cy - pdf_point.y)
        if d <= radius:
            hits.append((d, w[4]))
    return [t for _, t in sorted(hits)]


def find_legend(page, lines):
    """Vis rect of the legend: the 'LEGEND' title with the longest aligned label column below it."""
    best, best_n = None, 0
    for title in (l for l in lines if l["text"].upper() == "LEGEND"):
        t = title["vis"]
        cands = [l for l in lines
                 if t.x0 + 30 <= l["vis"].x0 <= t.x0 + 200 and l["vis"].y0 > t.y1 and l["vis"].height < 40]
        if not cands:
            continue
        # The label column is the dominant left edge; grid bubbles and tag diagrams sit off it.
        col_x = Counter(round(l["vis"].x0 / 5) * 5 for l in cands).most_common(1)[0][0]
        col = sorted((l for l in cands if abs(l["vis"].x0 - col_x) <= 15), key=lambda l: l["vis"].y0)
        members, last_y = [], t.y1
        for l in col:
            if l["vis"].y0 - last_y > 60:
                break
            members.append(l)
            last_y = l["vis"].y1
        if len(members) > best_n:
            best_n = len(members)
            right = max(l["vis"].x1 for l in members)
            best = pymupdf.Rect(t.x0 - 30, t.y0 - 10, right + 20, last_y + 30)
    return best


def span_font(span):
    """(fontname alias, fontfile or None) that best matches an existing span."""
    name = span["font"].split("+")[-1]
    key = name if name in config.FONT_FILES else name.split(",")[0].split("-")[0]
    return font_by_family(key)


def font_by_family(key):
    fname = config.FONT_FILES.get(key)
    if fname:
        path = config.WIN_FONTS / fname
        if path.exists():
            alias = "R" + "".join(ch for ch in key if ch.isalnum())
            return alias, str(path)
    return "helv", None


def text_length(text, fontname, fontfile, size):
    if fontfile:
        return pymupdf.Font(fontfile=fontfile).text_length(text, fontsize=size)
    return pymupdf.get_text_length(text, fontname=fontname, fontsize=size)


def int_to_rgb(c):
    return ((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255


def render_png(page, vis_rect, dpi, path, annots=False):
    pix = page.get_pixmap(dpi=dpi, clip=vis_rect & page.rect, annots=annots)
    pix.save(str(path))
    return path


class FreeSpace:
    """Finds empty sheet areas (vis space) for new tables/notes, using a low-res render."""

    def __init__(self, page):
        s = config.FREE_SPACE_SCALE
        pix = page.get_pixmap(matrix=pymupdf.Matrix(s, s), colorspace=pymupdf.csGRAY, annots=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w)
        self.mask = img < 235
        m = int(config.FREE_SPACE_MARGIN * s)
        self.mask[:m, :] = self.mask[-m:, :] = True
        self.mask[:, :m] = self.mask[:, -m:] = True
        self.s = s

    def occupy(self, vis_rect):
        s = self.s
        self.mask[max(0, int(vis_rect.y0 * s)):int(math.ceil(vis_rect.y1 * s)),
                  max(0, int(vis_rect.x0 * s)):int(math.ceil(vis_rect.x1 * s))] = True

    def find(self, w, h, near, pad=10):
        """Emptiest (then nearest to `near`) w x h vis rect. Returns (rect, ink_fraction)."""
        s = self.s
        ww, hh = int(math.ceil((w + 2 * pad) * s)), int(math.ceil((h + 2 * pad) * s))
        H, W = self.mask.shape
        if ww >= W or hh >= H:
            return None, 1.0
        ii = np.pad(self.mask.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
        sums = ii[hh:, ww:] - ii[:-hh, ww:] - ii[hh:, :-ww] + ii[:-hh, :-ww]
        best = sums.min()
        ys, xs = np.nonzero(sums <= best + max(2, best * 0.05))
        cx, cy = (xs + ww / 2) / s, (ys + hh / 2) / s
        k = int(np.argmin((cx - near.x) ** 2 + (cy - near.y) ** 2))
        x0, y0 = xs[k] / s + pad, ys[k] / s + pad
        rect = pymupdf.Rect(x0, y0, x0 + w, y0 + h)
        return rect, float(best) / (ww * hh)
