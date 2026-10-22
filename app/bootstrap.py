"""Prepare the environment before `rectify` is imported: src/ on sys.path, secrets in os.environ, DB ready.

`rectify.core.config` reads the environment once at import, so this module must be imported first.
`sync_secrets()` runs again on every page load, so secrets added in the deployment settings apply without
a reboot.
"""
import os
import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
SECRET_KEYS = ("ANTHROPIC_API_KEY", "RECTIFY_PLANNER_MODEL", "RECTIFY_PLANNER_EFFORT",
               "RECTIFY_PLANNER_WORKERS", "RECTIFY_PLANNER_FALLBACKS", "RECTIFY_DATA_DIR")
# config attributes refreshed from the environment on every page load (attribute, env var, default)
LIVE_SETTINGS = (("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", ""),
                 ("PLANNER_MODEL", "RECTIFY_PLANNER_MODEL", None),
                 ("PLANNER_EFFORT", "RECTIFY_PLANNER_EFFORT", None))

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ.setdefault("RECTIFY_HOME", str(PROJECT_ROOT))


def sync_secrets():
    """Copy Streamlit secrets (deployment settings) into os.environ and the loaded config."""
    try:
        for key in SECRET_KEYS:
            if key in st.secrets and str(st.secrets[key]).strip():
                os.environ[key] = str(st.secrets[key]).strip()
    except Exception:  # no secrets.toml (local runs use .env)
        pass
    cfg = sys.modules.get("rectify.core.config")
    if cfg is not None:
        for attr, env, default in LIVE_SETTINGS:
            value = os.getenv(env, "").strip()
            if value or default is not None:
                setattr(cfg, attr, value or default)


sync_secrets()

from rectify.db.database import init_db  # noqa: E402

init_db()
