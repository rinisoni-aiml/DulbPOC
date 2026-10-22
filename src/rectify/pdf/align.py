"""Align two versions of a sheet (vis space): where does a spot on sheet A sit on sheet B?

Revisions and reference drawings move the plan viewport, legend and tables by different amounts,
so offsets are clustered from words that occur exactly once on both sheets. A region takes the
cluster best supported by the anchors nearest to it; an optional pixel search refines it.
"""
from collections import Counter, defaultdict

import numpy as np
import pymupdf
from PIL import Image, ImageFilter

METRIC_DPI = 50


def word_index(page):
    """word text -> list of vis-space centres."""
    idx = defaultdict(list)
    m = page.rotation_matrix
    for w in page.get_text("words"):
        r = pymupdf.Rect(w[:4]) * m
        idx[w[4]].append(pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
    return idx


def gray(page, rect, dpi=METRIC_DPI):
    """Blurred grayscale render of a vis rect (markups hidden), or None if the clip is empty."""
    clip = rect & page.rect
    if clip.is_empty or clip.width < 5 or clip.height < 5:
        return None
    pix = page.get_pixmap(dpi=dpi, clip=clip, annots=False)
    img = Image.frombytes("RGB", (pix.w, pix.h), pix.samples).convert("L")
    return np.asarray(img.filter(ImageFilter.GaussianBlur(1.2)), dtype=np.float32)


class Aligner:
    def __init__(self, page_a, page_b, min_votes=5):
        self.a, self.b = page_a, page_b
        ia, ib = word_index(page_a), word_index(page_b)
        self.anchors = [(ia[t][0], ib[t][0]) for t in ia
                        if len(ia[t]) == 1 and len(ib.get(t, [])) == 1 and len(t) >= 3]
        bins = Counter((round((q.x - p.x) / 5) * 5, round((q.y - p.y) / 5) * 5) for p, q in self.anchors)
        self.clusters = []
        for (bx, by), n in bins.most_common():
            if n < min_votes:
                break
            if any(abs(bx - cx) <= 10 and abs(by - cy) <= 10 for cx, cy in self.clusters):
                continue
            members = [(q.x - p.x, q.y - p.y) for p, q in self.anchors
                       if abs(q.x - p.x - bx) <= 10 and abs(q.y - p.y - by) <= 10]
            self.clusters.append((sum(m[0] for m in members) / len(members),
                                  sum(m[1] for m in members) / len(members)))
        self.global_offset = self.clusters[0] if self.clusters else (0.0, 0.0)

    def cluster_for(self, region, k=9):
        """Majority offset cluster among the k anchor words nearest to the region (A -> B)."""
        c = (region.tl + region.br) / 2
        votes = Counter()
        for p, q in sorted(self.anchors, key=lambda pq: abs(pq[0] - c)):
            for i, (cx, cy) in enumerate(self.clusters):
                if abs(q.x - p.x - cx) <= 10 and abs(q.y - p.y - cy) <= 10:
                    votes[i] += 1
                    break
            if sum(votes.values()) >= k:
                break
        return self.clusters[votes.most_common(1)[0][0]] if votes else self.global_offset

    def offset(self, region, search=30):
        """Cluster offset near the region, refined by the best pixel match within +-search pt."""
        cx, cy = self.cluster_for(region)
        ga = gray(self.a, region)
        gb = gray(self.b, region + (cx - search, cy - search, cx + search, cy + search))
        if ga is None or gb is None:
            return (cx, cy), "anchors"
        z = METRIC_DPI / 72
        h, w = ga.shape
        best, best_d = (0, 0), None
        for dy in range(0, gb.shape[0] - h + 1, 2):
            for dx in range(0, gb.shape[1] - w + 1, 2):
                d = float(np.abs(gb[dy:dy + h, dx:dx + w] - ga).mean())
                if best_d is None or d < best_d:
                    best, best_d = (dx, dy), d
        return (cx - search + best[0] / z, cy - search + best[1] / z), "anchors+pixels"
