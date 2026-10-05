"""Margins Dashboard — JSA
Entry point: page config, global styling, and left-pane navigation across margin structures.
"""
from __future__ import annotations

import streamlit as st

from common import inject_css

st.set_page_config(
    page_title="Margins Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_css()

pg = st.navigation({
    "Overview": [
        st.Page("views/home.py", title="Summary", icon="📊", default=True),
    ],
    "Margins": [
        st.Page("views/ethanol.py", title="Ethanol", icon="🌽"),
        st.Page("views/soy_crush.py", title="Soy Crush", icon="🫘"),
        st.Page("views/poultry.py", title="Poultry", icon="🐔"),
        st.Page("views/cattle.py", title="Cattle", icon="🐄"),
        st.Page("views/hogs.py", title="Hogs", icon="🐖"),
    ],
})
pg.run()
