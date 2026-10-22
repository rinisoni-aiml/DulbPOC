"""Deterministic conduit-fill calculation. Numbers never come from the LLM."""
import math
import re

import pymupdf

from ..core import config

NUM = re.compile(r"^\d+(\.\d+)?$")


def _size(line):
    return max(s["size"] for s in line["spans"])


def find_cable_table(page, lines):
    """Locate the sheet's 'Cable Type' table (vis space) and read its (name, outer diameter) rows.

    Table cells share the header's font size; neighbouring title-block text is much smaller,
    so cells are told apart by size rather than by gaps.
    """
    header = next((l for l in lines if l["text"].strip().lower() == "cable type"), None)
    if header is None:
        return None, []
    h, size = header["vis"], _size(header)
    cells = [l for l in lines
             if abs(_size(l) - size) <= 0.3 * size and l["vis"].x0 >= h.x0 - 5 and l["vis"].y0 >= h.y0 - 40]
    band = [l for l in cells if l["vis"].y0 <= h.y1 + 30]
    right = max(l["vis"].x1 for l in band) + 10

    # Body: cells under the header inside the table width, grouped into rows by centre line.
    body = sorted((l for l in cells if l["vis"].x1 <= right and l["vis"].y0 > h.y1 + 5),
                  key=lambda l: (l["vis"].y0 + l["vis"].y1) / 2)
    rows = []
    for l in body:
        cy = (l["vis"].y0 + l["vis"].y1) / 2
        if rows and cy - rows[-1]["cy"] <= 6:
            rows[-1]["cells"].append(l)
        elif rows and cy - rows[-1]["cy"] > 80:
            break  # table ended
        else:
            rows.append({"cy": cy, "cells": [l]})

    cables, bottom = [], h.y1
    for row in rows:
        name, dia = [], None
        for l in sorted(row["cells"], key=lambda l: l["vis"].x0):
            if NUM.match(l["text"]):
                dia = float(l["text"])
                break
            name.append(l["text"])
        if name and dia:
            cables.append({"cable_type": " ".join(name).replace("–", "-"), "outer_dia_mm": dia})
            bottom = max(bottom, max(l["vis"].y1 for l in row["cells"]))
    return pymupdf.Rect(h.x0 - 10, h.y0 - 10, right, bottom + 10), cables


def conduit_fill(cables, conduit=None, quantities=None, overrides=None):
    """One row per cable type in a single conduit: area, fill %, spare % against max fill.

    `overrides` maps a cable type to its own conduit spec, e.g. mains in a 25 mm conduit.
    """
    base = {**config.CONDUIT, **(conduit or {})}
    q = quantities or {}
    rows = []
    for cab in cables:
        c = {**base, **(overrides or {}).get(cab["cable_type"], {})}
        qty = int(q.get(cab["cable_type"], 1))
        area = math.pi * cab["outer_dia_mm"] ** 2 / 4
        total = qty * area
        fill = total / c["area_mm2"] * 100
        rows.append({
            "Cable Type": cab["cable_type"],
            "Qty": str(qty),
            "Outer Ø (mm)": f"{cab['outer_dia_mm']:g}",
            "Cross-Section Area (mm²)": f"{area:.2f}",
            "Total Cable Area (mm²)": f"{total:.2f}",
            "Conduit Size (mm)": f"{c['size_mm']:g}",
            "Conduit Area (mm²)": f"{c['area_mm2']:g}",
            "Fill %": f"{fill:.2f}%",
            "Spare %": f"{c['max_fill_pct'] - fill:.2f}%",
            "Check": f"OK (<= {c['max_fill_pct']:g}%)" if fill <= c["max_fill_pct"] else "EXCEEDS",
        })
    return rows, base


def assumptions(base, overrides=None, quantities=None, standard=None):
    """Plain-language basis of the calculation, printed under the table."""
    lines = [
        "Fill % = total cable cross-section / conduit internal area. Spare % = max fill - Fill %.",
        f"Cable outer diameters: 'Assumed' values from the Cable Type table on this sheet. "
        f"Conduit: {base['size_mm']:g} mm, internal area {base['area_mm2']:g} mm2.",
        f"Max fill {base['max_fill_pct']:g}%: {standard or config.CONDUIT_STANDARD}.",
    ]
    if overrides:
        lines.append("Own conduit: " + "; ".join(f"{k} {v.get('size_mm', base['size_mm']):g} mm / "
                                                  f"{v.get('area_mm2', base['area_mm2']):g} mm2"
                                                  for k, v in overrides.items()) + ".")
    if not quantities:
        lines.append("Quantity 1 per cable type per conduit; actual cable counts per run to be confirmed.")
    return lines
