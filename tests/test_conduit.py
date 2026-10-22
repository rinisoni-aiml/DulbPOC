from rectify.domain.conduit import conduit_fill


def test_fill_matches_human_revision():
    rows, _ = conduit_fill([{"cable_type": "Card reader compatible", "outer_dia_mm": 6.5}])
    assert rows[0]["Cross-Section Area (mm²)"] == "33.18"
    assert rows[0]["Fill %"] == "15.15%"
    assert rows[0]["Spare %"] == "24.85%"


def test_per_cable_conduit_override():
    rows, _ = conduit_fill([{"cable_type": "230VAC mains", "outer_dia_mm": 9}],
                           overrides={"230VAC mains": {"size_mm": 25, "area_mm2": 346.4}})
    assert rows[0]["Conduit Size (mm)"] == "25"
    assert rows[0]["Fill %"] == "18.37%"


def test_quantity_scales_area():
    rows, _ = conduit_fill([{"cable_type": "CAT6", "outer_dia_mm": 6.5}], quantities={"CAT6": 2})
    assert rows[0]["Total Cable Area (mm²)"] == "66.37"
