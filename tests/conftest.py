from pathlib import Path

import pytest

NAME = "DG-NCD-401-0601-ECJ-SDW-SE-2B1-7003447_PDF_01.pdf"
ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = (ROOT.parent / "drawings" / NAME, ROOT / "samples" / NAME)


@pytest.fixture(scope="session")
def sample_pdf():
    for path in CANDIDATES:
        if path.exists():
            return path
    pytest.skip(f"sample drawing not found: {CANDIDATES[0]}")
