"""Which devices on a sheet have no mounting-elevation label (H=...mm)?

Devices are identified by their tags (TYPE /ZONE /LEVEL /NO). Each elevation label is paired with
the nearest tag that has no label yet (greedy, shortest distance first, within a cut-off); tags left
without a label are reported as missing an elevation.
"""
import math
import re

from ..core import config

TAG = re.compile(config.DEVICE_TAG_RE)
ELEV = re.compile(config.ELEVATION_RE, re.I)


def _centre(r):
    return (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2


def device_tags(lines, exclude_rects=()):
    tags = []
    for l in lines:
        m = TAG.match(l["text"])
        if not m or m.group("type") in config.DEVICE_TAG_EXCLUDE:
            continue
        if any(r.contains(l["vis"]) for r in exclude_rects):
            continue
        tags.append({"tag": l["text"], "type": m.group("type"), "vis": l["vis"], "pdf": l["pdf"], "line": l})
    return tags


def elevation_labels(lines):
    out = []
    for l in lines:
        for _ in ELEV.findall(l["text"]):
            out.append({"text": l["text"], "vis": l["vis"], "line": l})
    return out


def missing_elevations(lines, exclude_rects=(), max_dist=None):
    """(tags without an elevation label, all tags, all labels)."""
    max_dist = max_dist or config.ELEVATION_MATCH_DIST
    tags = device_tags(lines, exclude_rects)
    labels = elevation_labels(lines)
    pairs = sorted(((math.dist(_centre(t["vis"]), _centre(h["vis"])), i, j)
                    for i, t in enumerate(tags) for j, h in enumerate(labels)), key=lambda x: x[0])
    tag_done, label_done = set(), set()
    for d, i, j in pairs:
        if d > max_dist:
            break
        if i in tag_done or j in label_done:
            continue
        tag_done.add(i)
        label_done.add(j)
        tags[i]["elevation"] = labels[j]["text"]
    missing = [t for i, t in enumerate(tags) if i not in tag_done]
    return missing, tags, labels
