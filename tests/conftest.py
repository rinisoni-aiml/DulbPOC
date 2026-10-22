from pathlib import Path

import pytest

SAMPLE = (Path(__file__).resolve().parents[2] / "drawings"
          / "DG-NCD-401-0601-ECJ-SDW-SE-2B1-7003447_PDF_01.pdf")


@pytest.fixture(scope="session")
def sample_pdf():
    if not SAMPLE.exists():
        pytest.skip(f"sample drawing not found: {SAMPLE}")
    return SAMPLE
