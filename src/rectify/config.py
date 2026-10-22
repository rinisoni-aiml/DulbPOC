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
NEW_TEXT_FONT = "Arial"  # font for tables and notes the rectifier adds
FREE_SPACE_SCALE = 0.25  # px per pt for the whitespace finder
FREE_SPACE_MARGIN = 60   # pt kept clear of the sheet edge

# --- Conduit fill (C-08 style comments); override in inputs.json -------------------
CONDUIT = {"size_mm": 20, "area_mm2": 219.0, "max_fill_pct": 40.0}

# --- Output ----------------------------------------------------------------------
AUTHOR = "AI Rectifier"
HIGHLIGHT_RGB = (0.85, 0.0, 0.0)
NEW_INK_RGB = (0.0, 0.0, 0.0)
BEFORE_AFTER_DPI = 150
BEFORE_AFTER_PAD = 80    # pt around a change in the before/after crop
