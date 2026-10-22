"""Every threshold, path, model name and price in one place."""
import os
from pathlib import Path

def _project_root():
    """RECTIFY_HOME if set, else the nearest folder (from cwd up) holding pyproject.toml, else cwd."""
    if os.getenv("RECTIFY_HOME"):
        return Path(os.environ["RECTIFY_HOME"]).resolve()
    cwd = Path.cwd().resolve()
    for d in (cwd, *cwd.parents):
        if (d / "pyproject.toml").exists():
            return d
    return cwd


ROOT = _project_root()
OUT_DIR = ROOT / "out"
# Web app storage: SQLite run history + one folder per run. RECTIFY_DATA_DIR overrides.
DATA_DIR = Path(os.getenv("RECTIFY_DATA_DIR") or ROOT / "data").resolve()
RUNS_DIR = DATA_DIR / "runs"
DB_PATH = DATA_DIR / "rectify.db"
SAMPLES_DIR = ROOT / "samples"
INPUTS_EXAMPLE = ROOT / "config" / "inputs.example.json"


def load_env():
    """Load <project root>/.env into os.environ (python-dotenv if present, else a tiny parser)."""
    env = ROOT / ".env"
    if not env.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env)
        return
    except ImportError:
        pass
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

# --- LLM planner ---------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
PLANNER_MODEL = os.getenv("RECTIFY_PLANNER_MODEL", "claude-opus-5-5")
PLANNER_EFFORT = os.getenv("RECTIFY_PLANNER_EFFORT", "medium")  # low | medium | high
PLANNER_WORKERS = int(os.getenv("RECTIFY_PLANNER_WORKERS", "4"))
# Server-side refusal fallback (Claude API beta). Set to 0 if your SDK/endpoint rejects it.
PLANNER_FALLBACKS = os.getenv("RECTIFY_PLANNER_FALLBACKS", "1") == "1"
# USD per million tokens (input, output) for the cost line in the report.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

# --- Extraction ------------------------------------------------------------------
TIP_RADIUS = 60          # pt: drawing words collected around each leader tip
CROP_HALF = 110          # pt: half-size of the tip crop sent to the planner
CROP_DPI = 150
LEADER_ATTACH_DIST = 40  # pt: a Line annot end this close to a comment box belongs to it
STATUS_MAX_CHARS = 12    # FreeText shorter than this with "code" in it is a review-status label

# --- Editing ---------------------------------------------------------------------
TIP_SEARCH_RADIUS = 90   # pt: "where=tip" text replacements must be this close to a tip
FIT_SLACK = 1.15         # replacement text may be this much longer than the original before shrinking
MIN_FONT_SCALE = 0.55    # never shrink replacement text below this fraction of the original size
SYMBOL_TIP_DIST = 4      # pt: seed segments of a device symbol must touch the leader tip
SYMBOL_SEGMENT_MAX = 16  # pt: longer segments are dimensions/walls, never part of a symbol
SYMBOL_MAX_SIZE = 26     # pt: cap on a device symbol's bbox on plan
SYMBOL_JOIN_GAP = 2.0    # pt: segments this close belong to the same symbol
SYMBOL_DARK_MAX = 0.35   # max RGB channel for "dark" symbol ink
WIN_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
FONT_FILES = {           # drawing font family -> local TTF (falls back to Helvetica)
    "ArialNarrow": "ARIALN.TTF",
    "ArialNarrow,Bold": "ARIALNB.TTF",
    "Arial": "arial.ttf",
    "Arial,Bold": "arialbd.ttf",
    "Calibri": "calibri.ttf",
    "Calibri,Bold": "calibrib.ttf",
    "SegoeUI": "segoeui.ttf",
}
# Font for tables/notes the rectifier adds. PDF base-14 Helvetica (Arial is metric-compatible, so it looks
# the same) because embedded Arial maps '-' to a soft hyphen on copy/search; Helvetica extracts cleanly.
NEW_TEXT_FONT = "Helvetica"
CHECKLIST_MAX = 3        # engineer checklist items kept per comment
FREE_SPACE_SCALE = 0.25  # px per pt for the whitespace finder
FREE_SPACE_MARGIN = 60   # pt kept clear of the sheet edge

# --- Conduit fill (C-08 style comments); override in inputs.json -------------------
CONDUIT = {"size_mm": 20, "area_mm2": 219.0, "max_fill_pct": 40.0}
# Printed under the table so the engineer sees what the numbers rest on; override in inputs.json.
CONDUIT_STANDARD = ("per NEC Chapter 9 Table 1 (40% for more than two cables) - "
                    "confirm against the project specification")

# --- Device elevations (C-09 style comments) -----------------------------------------
# Device tag: TYPE /ZONE /LEVEL /NUMBER, e.g. "L1.2 /MUS /B1 /016", "PIR /MUS /B1 /007".
DEVICE_TAG_RE = r"^(?P<type>[A-Z][A-Z0-9_.]*)\s*/\s*[A-Z]+\s*/\s*[A-Z0-9]+(?:\s*/\s*\d+)?\s*$"
DEVICE_TAG_EXCLUDE = {"M"}          # second-line references such as M/BM/FTR/01
ELEVATION_RE = r"H\s*=\s*\d+\s*mm"   # device elevation labels; trunk labels ("H=4000") have no unit
ELEVATION_MATCH_DIST = 150          # pt: an H= label this close (vis) to a tag can belong to it
TAG_KEY_RE = r"REFERENCE NUMBER|EQUIPMENT TYPE"   # the sheet's tag-format key: its example tag is not a device

# --- Space IDs (C-01 style comments) ---------------------------------------------------
SPACE_ID_RE = r"^[A-Z]{1,3}\.[A-Z0-9]{1,4}\.\d{2,4}[A-Z]?$"   # e.g. M.B1.121

# --- Output ----------------------------------------------------------------------
AUTHOR = "AI Rectifier"
HIGHLIGHT_RGB = (0.85, 0.0, 0.0)
FINDING_RGB = (1.0, 0.55, 0.0)  # open findings, e.g. device without an elevation label
NEW_INK_RGB = (0.0, 0.0, 0.0)
BEFORE_AFTER_DPI = 150
BEFORE_AFTER_PAD = 80    # pt around a change in the before/after crop
