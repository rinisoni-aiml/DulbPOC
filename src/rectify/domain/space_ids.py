"""Room / space ID labels (e.g. "FIRE PUMP ROOM" over "M.B1.121") read from a reference drawing."""
import re

from ..core import config

SPACE_ID = re.compile(config.SPACE_ID_RE)


def space_labels(lines):
    """Space-ID lines plus the room-name line directly above each one (if any), in vis space."""
    out = []
    for l in lines:
        if not SPACE_ID.match(l["text"].strip()):
            continue
        v = l["vis"]
        cx = (v.x0 + v.x1) / 2
        name = None
        for o in lines:
            ov = o["vis"]
            if o is l or SPACE_ID.match(o["text"].strip()):
                continue
            # directly above: bottom within ~1.6 line heights, horizontally centred on the ID
            if 0 <= v.y0 - ov.y1 <= v.height * 1.6 and abs((ov.x0 + ov.x1) / 2 - cx) <= max(ov.width, v.width) / 2:
                name = o
                break
        span = l["spans"][0]
        out.append({"id": l["text"].strip(), "id_line": l, "name": name["text"] if name else None,
                    "name_line": name, "size": span["size"], "color": span["color"]})
    return out
