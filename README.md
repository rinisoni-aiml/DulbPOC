# rectify-poc: drawing comment rectification

Takes a consultant-commented drawing PDF and produces a **corrected vector PDF**. Every change is highlighted and has a before/after image, so the engineer can repeat the change in CAD/Revit. Anything the machine can't settle safely goes onto an **engineer checklist**.

```
commented.pdf ─▶ extract ─▶ plan (Claude or rules) ─▶ apply (PyMuPDF vector edits) ─▶ report
```

| Stage | What it does | LLM? |
|---|---|---|
| `pipeline/extract.py` | Reads the consultant markups straight from the PDF **annotations**: exact text, box, leader tips, fan-out leaders and outlined targets. No OCR. Also collects the drawing words at each tip, a crop image, the legend and a sheet text digest. | no |
| `pipeline/plan.py` | Turns each comment into ops from a fixed set (`replace_text`, `swap_symbol`, `add_conduit_fill_table`, `add_note`) plus checklist items. Uses Claude with strict JSON output; `--planner rules` is a keyword baseline that needs no key. | yes / no |
| `pipeline/apply.py` | Runs the ops on the vector layer. Text edits remove only the glyphs and re-insert text in the same font, size and direction. Symbol swaps redraw the legend's own vector symbol. Tables are computed in code and placed in free space. An op that can't be placed safely is reported, never guessed. | no |
| `pipeline/report.py` | Writes the clean PDF, a highlighted PDF, before/after PNGs, an overview, an integrity check, `report.html` and `checklist.md`. | no |

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
├── pyproject.toml                # package metadata, dependencies, `rectify` command, pytest config
├── requirements.txt              # runtime + web app deps (used by Streamlit Community Cloud)
├── .env.example                  # copy to .env; API key + planner model
├── .streamlit/config.toml        # web app theme and upload limit
├── config/
│   └── inputs.example.json       # engineer inputs (backbox size, conduit spec, ...)
├── samples/                      # local sample drawings (git-ignored: client PDFs)
├── app/                          # frontend: Streamlit web app
│   ├── streamlit_app.py          # entry point and page navigation
│   ├── bootstrap.py              # src/ on sys.path, secrets -> env, database init
│   ├── views/                    # pages: new_run, history, about
│   └── components/               # theme and the shared results view
├── src/rectify/                  # backend package
│   ├── cli.py                    # command line: run | extract | plan | apply | verify
│   ├── core/config.py            # every threshold, model name, price, path
│   ├── pipeline/                 # the pipeline, one module per stage
│   │   ├── extract.py            # 1. read markups (PDF annotations) + drawing context
│   │   ├── plan.py               # 2. comment -> ops + checklist (Claude or rules)
│   │   ├── apply.py              # 3. vector edits with PyMuPDF
│   │   ├── report.py             # 4. highlighted PDF, before/after, HTML, checklist
│   │   └── verify.py             # 5. compare with original + human revision (no API)
│   ├── services/rectification.py # runs the stages; shared by the CLI and the web app
│   ├── db/                       # SQLite run history (database.py, runs.py)
│   ├── pdf/geometry.py           # rotation-aware geometry, text lines, fonts, free space
│   └── domain/conduit.py         # deterministic conduit-fill calculation
├── tests/                        # pytest, offline (uses ../drawings or samples/)
├── data/                         # web app runs + rectify.db (git-ignored)
└── out/                          # CLI run outputs (git-ignored)
```

The project root is the nearest folder above the current directory that holds `pyproject.toml`. `.env`, `out/` and `data/` live there. Set `RECTIFY_HOME` (and `RECTIFY_DATA_DIR` for the web app storage) to override.

## Web app

```powershell
.venv\Scripts\python -m streamlit run app/streamlit_app.py
```

Opens on http://localhost:8501. There is no login. Pages:

- **New run**: upload a commented PDF (or use the local sample), choose the planner, set the engineer inputs (back box size, conduit spec, device elevations per type), optionally add the architectural reference drawing (restores space IDs) and a human-revised PDF to verify against, and run. Results show the KPIs, the comment resolution (resolved, partially resolved, ready to fix, needs engineer), the sheet overview, every comment with its before/after images and any inputs it is waiting for, devices without an elevation label, the engineer checklist, the verification (if requested) and downloads for the corrected PDFs, reports, JSON and a ZIP of everything.
- **Change review**: each applied change (CH-xx) can be approved or rejected, with an optional reviewer note, or all pending changes approved at once. Decisions are saved immediately in SQLite and written to `approvals.json` in the run folder, which is included in the downloads.
- **Run history**: every run is stored in SQLite (`data/rectify.db`) with its files in `data/runs/<run id>/`. Select a run to open it again or delete it.
- **About**: the process, outputs and known limits.

The rules planner is the default because it costs nothing. The Claude planner appears when an API key is configured.

### Deploy free on Streamlit Community Cloud

1. Push this folder to a GitHub repository (it is the repository root). Client PDFs are git-ignored, so the hosted app has no sample drawing: the client uploads their own PDF, and the "Use the sample drawing" option only appears where `samples/` holds the files.
2. On share.streamlit.io choose **Create app**, select the repository and branch, and set **Main file path** to `app/streamlit_app.py`. Python 3.11 or later.
3. Optional: under **Advanced settings > Secrets**, add `ANTHROPIC_API_KEY = "..."` to enable the Claude planner. Anyone with the app link can then spend API credit, so leave it out for an open demo.

On the free host the file system is temporary: run history and outputs are cleared when the app restarts or redeploys. Windows fonts are not installed on Linux, so text edits fall back to Helvetica there.

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

### Verify against a human revision (no API)

```powershell
.venv\Scripts\python -m rectify verify "..\drawings\<commented>.pdf" --revised "..\drawings\<human revision>.pdf"
```

This writes `verification_report.html` and `verification/`, and checks four things:
- **File integrity:** pages, markups removed, no original text lost, nothing changed outside the edits.
- **Per-comment verdict:** Fixed, Partially fixed or Not fixed.
- **Three-way comparison** of each comment's spot: original vs corrected vs human revision. The revision is aligned per region, because revisions move the plan, legend and tables by different amounts.
- **Content checks:** new text and table numbers compared with the revision.

### Engineer inputs (`--inputs`)

These are facts the drawing doesn't contain. They get applied instead of guessed:

```json
{
  "backbox_size": "75x75x50mm",
  "conduit": {"size_mm": 20, "area_mm2": 219.0, "max_fill_pct": 40.0},
  "conduit_overrides": {"230VAC mains": {"size_mm": 25, "area_mm2": 346.4}},
  "cable_qty": {"CAT6 / Fibre": 2},
  "elevations": {"PIR": "2400", "L1.2": "1200"},
  "reference_pdf": "..\\drawings\\<architectural background>.pdf"
}
```

- `elevations`: mounting height per device type, for example from the RFI response. Devices without an `H=…mm` label are always listed and marked orange in `corrected_highlighted.pdf`. The label is drawn only for types given here.
- `reference_pdf` (or `--reference`): the architectural drawing that shows the space IDs. Its room names and IDs are copied onto the sheet at the aligned positions. If it's missing, the "restore space IDs" comment is reported as **ready to fix**, with that drawing named as the input needed.
- `reference_offset` (optional): `[dx, dy]` in points, used when the reference can't be aligned automatically.
- `conduit` (optional): if it's left out, the conduit fill table uses the conduit size the sheet itself states (for example 25 mm in its notes and legend), so the table matches the drawing.
- `revision` (optional): `{"bump": true, "date": "dd/mm/yy", "description": "..."}`. By default the corrected sheet is issued as the next revision:
  - a new top row in the revision table
  - the title block's revision number and date updated
  - APRV / CHK'D left blank for signatures

In `corrected_highlighted.pdf`, every consultant comment is shown again at its original position, with its response: status, what was done, and the inputs still needed.

Every comment ends with one status in `report.html` and `changes.json` (`resolution`):
- **resolved**: fixed on the drawing
- **partially resolved**: some edits applied, items left over
- **ready to fix**: applies automatically once the listed inputs are supplied
- **needs engineer**: a decision or drafting work no input can replace

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
