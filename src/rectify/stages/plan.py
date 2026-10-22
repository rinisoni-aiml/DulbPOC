"""Stage 2: turn each comment into a fix plan (edit ops + engineer checklist).

`llm`   - Claude (vision + reasoning), strict JSON via structured outputs. Default when a key is set.
`rules` - keyword baseline that needs no API key. Lets the pipeline run end to end offline.
Either way the ops are validated against the drawing in apply.py before anything is edited.
"""
import base64
import json
from concurrent.futures import ThreadPoolExecutor

from .. import config

SCOPES = ["local", "sheet_wide", "legend", "layout", "calculation", "external_input"]
OP_TYPES = ["replace_text", "swap_symbol", "add_conduit_fill_table", "add_note"]
NULL_STR = {"type": ["string", "null"]}

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["comment_id", "clean_text", "intent", "scope", "status", "ops", "checklist", "confidence"],
    "properties": {
        "comment_id": {"type": "string"},
        "clean_text": {"type": "string"},
        "intent": {"type": "string"},
        "scope": {"type": "string", "enum": SCOPES},
        "status": {"type": "string", "enum": ["auto", "partial", "engineer"]},
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "find", "replace", "where", "from_label", "to_label", "text", "rationale"],
                "properties": {
                    "type": {"type": "string", "enum": OP_TYPES},
                    "find": NULL_STR,
                    "replace": NULL_STR,
                    "where": {"anyOf": [{"type": "string", "enum": ["tip", "legend", "sheet"]},
                                        {"type": "null"}]},
                    "from_label": NULL_STR,
                    "to_label": NULL_STR,
                    "text": NULL_STR,
                    "rationale": {"type": "string"},
                },
            },
        },
        "checklist": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["item", "inputs_needed", "where_on_sheet"],
                "properties": {
                    "item": {"type": "string"},
                    "inputs_needed": {"type": "array", "items": {"type": "string"}},
                    "where_on_sheet": {"type": "string"},
                },
            },
        },
        "confidence": {"type": "number"},
    },
}

SYSTEM = """You are a senior ELV / security-systems drafting engineer. A consultant has returned a \
contractor's shop drawing (PDF) with review comments. For ONE comment at a time you decide how the \
drawing must be corrected, using only edits a program can apply safely to the PDF's vector layer, \
and you list everything else for the engineer.

Available edit ops (the program executes them; anything else goes to the checklist):
- replace_text: change existing drawing text. `find` = an EXACT, case-sensitive string copied from \
SHEET TEXT; `replace` = the new text; `where` = "tip" (only near this comment's leader tips), \
"legend" (only inside the legend) or "sheet" (every occurrence).
- swap_symbol: replace the device symbol at this comment's leader tip(s) with another legend symbol. \
`from_label` = legend label of the symbol currently shown, `to_label` = legend label of the required \
symbol. Both must be legend labels copied exactly from SHEET TEXT (legend=true).
- add_conduit_fill_table: add a conduit fill calculation table (quantity, outer diameter, cross-section \
area, conduit size, fill %, spare %). The program computes every number from the sheet's own Cable Type \
table and ENGINEER INPUTS; you only give the rationale.
- add_note: add a short drafting note (max 120 characters) near the tip, in `text`. Use it only to \
record a reference that is stated in the comment itself (e.g. an RFI number or a drawing cross-reference).

Rules:
- Never invent engineering data: no dimensions, sizes, positions, room names, drawing numbers or \
product data that are not in the comment, SHEET TEXT or ENGINEER INPUTS. If a fix needs such data, \
add a checklist item and name the exact inputs needed.
- Prefer a safe partial fix plus checklist items over doing nothing, and never an unsafe guess.
- Do not delete or move drawing content; that is engineer work (checklist).
- If the comment says a legend entry is mislabelled or misread, also correct that legend label with \
replace_text (where="legend") when the right wording follows from the comment itself.
- Checklist: at most 3 items per comment, each a concrete drafting action with its missing inputs. \
No procedural reminders (WIR timing, resubmission admin) and no repeats of what an op already does.
- Unused op fields are null.
- status: "auto" = the ops fully resolve the comment; "partial" = ops resolve part, checklist the rest; \
"engineer" = no ops.
- where_on_sheet: describe the location in drafting terms (e.g. "legend row CARD + PIN READER", \
"door L1.9 near grid A2.14").
- clean_text: the comment with OCR/typing noise fixed, meaning unchanged. intent: one sentence.
- confidence: 0-1, how sure you are the ops are right.
"""


def _digest(sheet_text):
    legend = [s["text"] for s in sheet_text if s["legend"]]
    rest = [f'{s["text"]}  (x{s["count"]})' if s["count"] > 1 else s["text"]
            for s in sheet_text if not s["legend"]]
    return ("SHEET TEXT\n== Legend (legend=true) ==\n" + "\n".join(legend)
            + "\n== Rest of sheet (legend=false; (xN) = occurrences) ==\n" + "\n".join(rest))


def _image_block(path):
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def _comment_message(c, out_dir, inputs):
    content = []
    for i, tip in enumerate(c["tips"][:3]):
        content.append({"type": "text", "text": f"Crop around leader tip {i + 1} "
                        "(yellow box = consultant comment, red line = its leader):"})
        content.append(_image_block(out_dir / tip["crop"]))
    payload = {
        "comment_id": c["id"],
        "comment_text": c["text"],
        "author": c["author"],
        "leader_tips": [{"tip": i + 1, "drawing_words_near_tip": t["words"][:25],
                         "points_at_outlined_region": bool(t["target_box_pdf"])}
                        for i, t in enumerate(c["tips"])],
        "engineer_inputs": inputs or {},
    }
    content.append({"type": "text", "text": "Plan the correction for this comment.\n"
                    + json.dumps(payload, indent=1, ensure_ascii=False)})
    return [{"role": "user", "content": content}]


def _cost(usage_list, model):
    p_in, p_out = config.PRICES.get(model, (0, 0))
    tot = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    for u in usage_list:
        tot["input"] += u.input_tokens or 0
        tot["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        tot["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        tot["output"] += u.output_tokens or 0
    usd = (tot["input"] * p_in + tot["cache_write"] * p_in * 1.25 + tot["cache_read"] * p_in * 0.1
           + tot["output"] * p_out) / 1e6
    return {**tot, "usd": round(usd, 4)}


def plan_llm(extracted, sheet_text, out_dir, inputs):
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    system = [
        {"type": "text", "text": SYSTEM},
        {"type": "text", "text": _digest(sheet_text), "cache_control": {"type": "ephemeral"}},
    ]
    usages = []

    def one(c):
        kwargs = dict(
            model=config.PLANNER_MODEL,
            max_tokens=16000,
            system=system,
            messages=_comment_message(c, out_dir, inputs),
            output_config={"effort": config.PLANNER_EFFORT,
                           "format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
        )
        try:
            if config.PLANNER_FALLBACKS:
                resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                   fallbacks="default", **kwargs)
            else:
                resp = client.messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            return _engineer_only(c, f"Planner API error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError as e:
            return _engineer_only(c, f"Planner connection error: {e}")
        usages.append(resp.usage)
        if resp.stop_reason == "refusal":
            return _engineer_only(c, "Planner declined this comment; review manually.")
        if resp.stop_reason == "max_tokens":
            return _engineer_only(c, "Planner output was cut off; review manually.")
        text = next(b.text for b in resp.content if b.type == "text")
        out = json.loads(text)
        out["comment_id"] = c["id"]
        return out

    comments = extracted["comments"]
    # First call alone so it writes the prompt cache (sheet digest); the rest read it in parallel.
    results = [one(comments[0])] if comments else []
    with ThreadPoolExecutor(max_workers=config.PLANNER_WORKERS) as pool:
        results += list(pool.map(one, comments[1:]))
    return {"planner": f"llm:{config.PLANNER_MODEL}", "usage": _cost(usages, config.PLANNER_MODEL),
            "comments": results}


def _engineer_only(c, why):
    return {"comment_id": c["id"], "clean_text": c["text"], "intent": why, "scope": "external_input",
            "status": "engineer", "ops": [], "confidence": 0.0,
            "checklist": [{"item": c["text"], "inputs_needed": ["engineer review"], "where_on_sheet": "see comment"}]}


def _op(type_, rationale, **kw):
    op = {k: None for k in ("find", "replace", "where", "from_label", "to_label", "text")}
    op.update(kw)
    return {"type": type_, "rationale": rationale, **op}


def plan_rules(extracted, sheet_text, inputs):
    """Keyword baseline: same output shape as the LLM, deliberately conservative."""
    inputs = inputs or {}
    legend = {s["text"] for s in sheet_text if s["legend"]}
    out = []
    for c in extracted["comments"]:
        t = c["text"].lower()
        ops, checklist, scope = [], [], "local"
        where = f"leader tip near {', '.join(c['tips'][0]['words'][:3])}" if c["tips"] else "see comment"
        if "installation details" in t or "lock configuration" in t:
            scope = "layout"
            checklist.append({"item": "Provide door installation details for both sides of every lock configuration",
                              "inputs_needed": ["door lock configuration schedule", "reference detail drawing no."],
                              "where_on_sheet": "typical installation details strip"})
        elif "space id" in t:
            scope = "sheet_wide"
            checklist.append({"item": "Restore space/room IDs in every room",
                              "inputs_needed": ["architectural room schedule (room names + IDs)"],
                              "where_on_sheet": "all rooms on plan"})
        elif "legend" in t and "not a lock" in t:
            scope = "sheet_wide"
            if "LOCK TYPE" in legend:
                ops.append(_op("replace_text", "Arrow symbol denotes a configuration/direction, not a lock.",
                               find="LOCK TYPE", replace="CONFIGURATION TYPE", where="legend"))
            checklist.append({"item": "Remove conduit/wiring drawn to the direction-arrow symbol at every door",
                              "inputs_needed": ["drafter to delete wiring in CAD"], "where_on_sheet": "all doors"})
        elif "back box" in t or "backbox" in t:
            scope = "external_input"
            size = inputs.get("backbox_size")
            label = "BACKBOX AS PER APPROVED MATERIAL SUBMITTAL"
            if size and label in legend:
                ops.append(_op("replace_text", "Show back box size in the legend (size from engineer inputs).",
                               find=label, replace=f"{size} {label}", where="legend"))
            else:
                checklist.append({"item": "Add back box sizes to the legend",
                                  "inputs_needed": ["back box size (inputs.json: backbox_size)"],
                                  "where_on_sheet": "legend row BACKBOX"})
            checklist.append({"item": "Drawing to be stamped by the system specialist",
                              "inputs_needed": ["specialist stamp"], "where_on_sheet": "title block"})
        elif "card + pin" in t or ("card reader" in t and "legend of the reader" in t):
            scope = "local"
            if {"CARD + PIN READER", "CARD READER"} <= legend:
                ops.append(_op("swap_symbol", "IFC requires a Card Reader, not a Card + PIN reader.",
                               from_label="CARD + PIN READER", to_label="CARD READER"))
            checklist.append({"item": "Confirm device tag/type for this door matches IFC (Card Reader)",
                              "inputs_needed": ["IFC drawing"], "where_on_sheet": where})
        elif "location" in t and "ifc" in t:
            scope = "external_input"
            checklist.append({"item": "Move card reader back to its IFC position",
                              "inputs_needed": ["IFC drawing position of the card reader"], "where_on_sheet": where})
        elif "conduit fill" in t:
            scope = "calculation"
            ops.append(_op("add_conduit_fill_table", "Consultant requires conduit fill calculation with spare %."))
        elif "elevation" in t:
            scope = "external_input"
            checklist.append({"item": "Add mounting elevation (H=...) to every device",
                              "inputs_needed": ["RFI ECJ-RFI-001632 response", "device elevations"],
                              "where_on_sheet": "all devices"})
        else:
            checklist.append({"item": c["text"], "inputs_needed": ["engineer review"], "where_on_sheet": where})
        status = "engineer" if not ops else ("auto" if not checklist else "partial")
        out.append({"comment_id": c["id"], "clean_text": c["text"], "intent": "(rules baseline)",
                    "scope": scope, "status": status, "ops": ops, "checklist": checklist,
                    "confidence": 0.5 if ops else 0.0})
    return {"planner": "rules", "usage": None, "comments": out}


def plan(extracted, sheet_text, out_dir, inputs=None, mode="auto"):
    if mode == "auto":
        mode = "llm" if config.ANTHROPIC_API_KEY else "rules"
    if mode == "llm":
        if not config.ANTHROPIC_API_KEY:
            raise SystemExit("ANTHROPIC_API_KEY is empty in rectify-poc/.env; use --planner rules or add the key.")
        result = plan_llm(extracted, sheet_text, out_dir, inputs)
    else:
        result = plan_rules(extracted, sheet_text, inputs)
    (out_dir / "plan.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
