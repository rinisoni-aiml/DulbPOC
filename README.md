# rectify-poc: drawing comment rectification

Takes a consultant-commented drawing PDF and produces a **corrected vector PDF**. Every change is highlighted and has a before/after image, so the engineer can repeat the change in CAD/Revit. Anything the machine can't settle safely goes onto an **engineer checklist**.

```
commented.pdf ─▶ extract ─▶ plan (Claude or rules) ─▶ apply (PyMuPDF vector edits) ─▶ report
```

| Stage | What it does | LLM? |
|---|---|---|
| `stages/extract.py` | Reads the consultant markups straight from the PDF **annotations**: exact text, box, leader tips, fan-out leaders and outlined targets. No OCR. Also collects the drawing words at each tip, a crop image, the legend and a sheet text digest. | no |
| `stages/plan.py` | Turns each comment into ops from a fixed set (`replace_text`, `swap_symbol`, `add_conduit_fill_table`, `add_note`) plus checklist items. Uses Claude with strict JSON output; `--planner rules` is a keyword baseline that needs no key. | yes / no |
| `stages/apply.py` | Runs the ops on the vector layer. Text edits remove only the glyphs and re-insert text in the same font, size and direction. Symbol swaps redraw the legend's own vector symbol. Tables are computed in code and placed in free space. An op that can't be placed safely is reported, never guessed. | no |
| `stages/report.py` | Writes the clean PDF, a highlighted PDF, before/after PNGs, an overview, an integrity check, `report.html` and `checklist.md`. | no |

## Setup (Windows, Python 3.11+)

```powershell
cd rectify-poc
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env   # then set ANTHROPIC_API_KEY (leave blank to use the rules baseline)
.venv\Scripts\python -m pytest    # offline tests, no API calls
```

## Project structure

```
rectify-poc/
├── pyproject.toml            # package metadata, dependencies, `rectify` command, pytest config
├── requirements.txt          # same runtime deps, for a plain pip install
├── .env.example              # copy to .env; API key + planner model
├── config/
│   └── inputs.example.json   # engineer inputs (backbox size, conduit spec, ...)
├── src/rectify/
│   ├── cli.py                # command line: run | extract | plan | apply
│   ├── config.py             # every threshold, model name, price, path
│   ├── stages/               # the pipeline, one module per stage
│   │   ├── extract.py        # 1. read markups (PDF annotations) + drawing context
│   │   ├── plan.py           # 2. comment -> ops + checklist (Claude or rules)
│   │   ├── apply.py          # 3. vector edits with PyMuPDF
│   │   └── report.py         # 4. highlighted PDF, before/after, HTML, checklist
│   ├── pdf/geometry.py       # rotation-aware geometry, text lines, fonts, free space
│   └── engineering/conduit.py# deterministic conduit-fill calculation
├── tests/                    # pytest, offline (uses ../drawings sample if present)
└── out/                      # run outputs (git-ignored)
```

The project root is the nearest folder above the current directory that holds `pyproject.toml`. `.env` and `out/` live there. Set `RECTIFY_HOME` to override.

## Run

```powershell
.venv\Scripts\python -m rectify run "..\drawings\DG-NCD-401-0601-ECJ-SDW-SE-2B1-7003447_PDF_01.pdf" --inputs config/inputs.example.json
```

Outputs go to `out/<pdf name>/`:

- `corrected.pdf`: clean corrected drawing, with consultant markups removed and edits applied as vectors and real text
- `corrected_highlighted.pdf`: the same drawing plus a red dashed box and a note per change (toggle them in Acrobat's comments pane)
- `before_after/CH-xx_C-yy.png`: original vs corrected for each change
- `overview.png`: the whole sheet with comments (orange) and edits (red)
- `report.html`: per-comment summary with images and the engineer checklist
- `checklist.md`, `changes.json`, `plan.json`, `comments.json`

The stages also run one at a time: `extract`, `plan`, `apply`. For example, an engineer can edit `plan.json` by hand and re-run `apply` alone.

### Engineer inputs (`--inputs`)

These are facts the drawing doesn't contain. They get applied instead of guessed:

```json
{
  "backbox_size": "75x75x50mm",
  "conduit": {"size_mm": 20, "area_mm2": 219.0, "max_fill_pct": 40.0},
  "conduit_overrides": {"230VAC mains": {"size_mm": 25, "area_mm2": 346.4}},
  "cable_qty": {"CAT6 / Fibre": 2}
}
```

## LLM choice and cost

| Task | Model | Cost per sheet (9 comments) |
|---|---|---|
| Comment → fix plan (vision + reasoning, strict JSON) | `claude-opus-5-5` (default), effort `medium` | ~$0.40-0.50 |
| Same, cheaper | `claude-sonnet-5-5` (set `RECTIFY_PLANNER_MODEL` in `.env`) | ~$0.20 |
| Reading comments | none: the text comes straight from the PDF annotations | $0 |

There is one provider and one key. The cost line in `report.html` comes from the real token usage. Refusal fallback is on by default (`RECTIFY_PLANNER_FALLBACKS=1`); set it to `0` if your SDK version rejects the parameter.

## Results on the sample (DG-NCD-…-7003447, 9 comments)

With the offline rules planner and `inputs.example.json`, a full run takes about 60 s and gives:

| Comment | Result |
|---|---|
| Legend arrow is not a lock | Legend `LOCK TYPE` → `CONFIGURATION TYPE` (vector text). Wiring removal goes on the checklist. |
| Card + PIN reader shown, IFC needs Card Reader (×2) | The device symbol at each leader tip is swapped for the legend's CARD READER symbol (vector). |
| Back box sizes | Legend row becomes `75x75x50mm BACKBOX AS PER…` (size from inputs). Specialist stamp goes on the checklist. |
| Conduit fill calculation | New table computed from the sheet's own Cable Type table. It matches the human revision: 15.15% fill / 24.85% spare. |
| Space IDs, IFC position, door details, elevations/RFI | Checklist with the inputs needed (architectural room schedule, IFC, RFI ECJ-RFI-001632, reference drawing no.). |

- Integrity check: **0.003%** of sheet pixels changed outside the edit boxes (anti-aliasing at box edges).
- All 24 consultant annotations are removed in `corrected.pdf`.

## Known limits

- Assumes the comments are PDF annotations, as in this sample. Flattened markups would need a yellow-box fallback (not built).
- Symbol swap handles devices drawn in the same orientation as the legend. Rotated devices go to the checklist.
- Text edits use the closest local Windows font (Arial Narrow, Arial, Calibri). They render in the same style but are not the CAD SHX font.
- Comment numbering follows sheet reading order (C-01 is top-left). It does not follow the numbering in the project brief.
