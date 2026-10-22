"""Comment resolution labels: a comment blocked only by an engineer input is ready to fix."""
from rectify.pipeline.report import _input_only, resolution

EX = {"comments": [{"id": "C-01"}, {"id": "C-02"}, {"id": "C-03"}]}


def _plan(checklists):
    return {"comments": [{"comment_id": cid, "checklist": items} for cid, items in checklists.items()]}


def test_input_only_items():
    assert _input_only({"inputs_needed": ["back box size (inputs.json: backbox_size)"]})
    assert _input_only({"inputs_needed": ["Back box size for each device type"]})
    assert not _input_only({"inputs_needed": ["specialist stamp"]})
    assert not _input_only({"inputs_needed": ["back box size", "IFC drawing"]})
    assert not _input_only({"inputs_needed": []})


def test_missing_input_is_ready_to_fix():
    plan = _plan({
        "C-01": [{"item": "Add back box sizes to the legend",
                  "inputs_needed": ["back box size (inputs.json: backbox_size)"]},
                 {"item": "Drawing to be stamped by the system specialist", "inputs_needed": ["specialist stamp"]}],
        "C-02": [{"item": "Move card reader back to its IFC position",
                  "inputs_needed": ["IFC drawing position of the card reader"]}],
        "C-03": [{"item": "Specialist stamp", "inputs_needed": ["specialist stamp"]}],
    })
    changes = [{"id": "CH-01", "comment_id": "C-03"}]
    res = {r["comment_id"]: r for r in resolution(EX, plan, changes, [], [])}
    assert res["C-01"]["status"] == "ready_to_fix"
    assert res["C-01"]["inputs_needed"] == ["back box size (inputs.json: backbox_size)"]
    assert res["C-01"]["engineer_actions"] == ["Drawing to be stamped by the system specialist"]
    assert res["C-02"]["status"] == "needs_engineer"
    assert res["C-03"]["status"] == "partially_resolved"
