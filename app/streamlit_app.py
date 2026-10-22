"""Web app entry point: streamlit run app/streamlit_app.py"""
import streamlit as st

st.set_page_config(page_title="Drawing Rectification", page_icon=":material/architecture:", layout="wide")

import bootstrap  # noqa: E402  (must run before any rectify import)
from components.theme import apply_theme  # noqa: E402
from views import about, history, new_run  # noqa: E402

bootstrap.sync_secrets()
apply_theme()

page = st.navigation([
    st.Page(new_run.render, title="New run", icon=":material/upload_file:", url_path="new-run", default=True),
    st.Page(history.render, title="Run history", icon=":material/history:", url_path="history"),
    st.Page(about.render, title="About", icon=":material/info:", url_path="about"),
])
page.run()
