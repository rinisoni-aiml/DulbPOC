"""Prepare the environment before `rectify` is imported: src/ on sys.path, secrets in os.environ, DB ready.

`rectify.core.config` reads the environment once at import, so this module must be imported first.
"""
import os
import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
SECRET_KEYS = ("ANTHROPIC_API_KEY", "RECTIFY_PLANNER_MODEL", "RECTIFY_PLANNER_EFFORT",
               "RECTIFY_PLANNER_WORKERS", "RECTIFY_PLANNER_FALLBACKS", "RECTIFY_DATA_DIR")

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ.setdefault("RECTIFY_HOME", str(PROJECT_ROOT))

try:  # Streamlit Cloud secrets; no secrets.toml locally is fine (.env is used instead)
    for key in SECRET_KEYS:
        if key in st.secrets and not os.environ.get(key):
            os.environ[key] = str(st.secrets[key])
except Exception:
    pass

from rectify.db.database import init_db  # noqa: E402

init_db()
