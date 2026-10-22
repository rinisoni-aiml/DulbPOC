"""Page styling and shared text helpers."""
import streamlit as st

CSS = """
<style>
#MainMenu, footer, [data-testid="stAppDeployButton"] {visibility: hidden; display: none;}
.block-container {padding-top: 2rem; max-width: 1280px;}
h1 {font-weight: 650; letter-spacing: -0.01em;}
.app-sub {color: #5f6670; margin: -0.6rem 0 1.4rem 0; font-size: 0.98rem;}
.badge {display: inline-block; font-size: 0.78rem; font-weight: 600; padding: 2px 10px; border-radius: 999px;
        border: 1px solid; margin-right: 6px; vertical-align: middle;}
.badge-ok {color: #1f7a3d; border-color: #1f7a3d; background: #eef7f1;}
.badge-warn {color: #a15c00; border-color: #c98a2b; background: #fdf5e8;}
.badge-muted {color: #4b5563; border-color: #c4c9d0; background: #f4f6f8;}
.badge-fail {color: #b42318; border-color: #b42318; background: #fdf0ef;}
.quote {color: #374151; border-left: 3px solid #d4a72c; padding: 2px 0 2px 12px; margin: 6px 0 10px 0;}
.muted {color: #5f6670; font-size: 0.9rem;}
[data-testid="stMetricValue"] {font-size: 1.6rem;}
</style>
"""


def apply_theme():
    st.markdown(CSS, unsafe_allow_html=True)


def header(title, subtitle=None):
    st.title(title)
    if subtitle:
        st.markdown(f"<p class='app-sub'>{subtitle}</p>", unsafe_allow_html=True)


def badge(text, kind="muted"):
    return f"<span class='badge badge-{kind}'>{text}</span>"


def clean(text):
    """Plain punctuation for display: no em/en dashes or ellipsis characters."""
    if text is None:
        return ""
    return (str(text).replace("—", " - ").replace("–", "-").replace("…", "...")
            .replace("·", "|"))
