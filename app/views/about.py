"""About: what the tool does, what it produces and where it stops."""
import streamlit as st

from components.theme import header
from rectify.core import config


def render():
    header("About", "How the drawing rectification proof of concept works.")
    st.markdown("""
#### Process

| Step | What happens |
|---|---|
| 1. Read comments | Consultant markups are read directly from the PDF annotations: text, box, leader tips and outlined targets. No OCR. |
| 2. Plan fixes | Each comment becomes a set of operations (replace text, swap symbol, add conduit fill table, add note) or an engineer checklist item. Planned by Claude, or by an offline rules baseline. |
| 3. Apply edits | Operations are applied to the vector drawing. Text keeps its font, size and direction; symbols are redrawn from the legend; tables are computed in code. Anything that cannot be placed safely is reported, not guessed. |
| 4. Report | Clean corrected PDF, highlighted PDF, before and after images, sheet overview, integrity check and engineer checklist. |
| 5. Verify (optional) | Compares the corrected drawing with a human revision: file integrity, per-comment verdict and a three-way image comparison. |

#### Outputs

- **Corrected drawing**: consultant markups removed, edits applied as vectors and real text.
- **Highlighted drawing**: the same sheet with a red box and a note for each change.
- **Engineer checklist**: what the drawing alone cannot settle, with the inputs needed.
- **Reports**: HTML report, verification report and JSON data for every stage.

#### Known limits

- Comments must be PDF annotations. Flattened (printed) markups are not supported yet.
- Symbol swaps handle devices drawn in the same orientation as the legend. Rotated devices go to the checklist.
- Edited text uses the closest available font. On the hosted demo (Linux) Windows fonts are not installed, so edits fall back to Helvetica.
- Runs stored on the hosted demo are temporary and may be cleared when the app restarts.

#### Where data is stored

- **Uploaded drawings and generated files** are kept on the app server, one folder per run: the uploaded PDFs, the corrected PDFs, images, reports and JSON files.
- **Run history and review decisions** are kept in a small SQLite database on the same server.
- **Downloads** go straight to your computer. Download anything you want to keep, because the hosted demo storage is temporary.
- **Claude planner**: to plan the fixes, the comment text, the text found on the sheet (legend and labels), small image crops around each comment and the engineer inputs are sent to the Anthropic API. The drawing file itself is not uploaded.
""")
    if config.ANTHROPIC_API_KEY:
        st.caption(f"Planner model: {config.PLANNER_MODEL}")
