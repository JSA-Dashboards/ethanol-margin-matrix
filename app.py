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

st.markdown("""<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
</style>""", unsafe_allow_html=True)

# Hide the Streamlit Community Cloud viewer badge (the profile avatar that links
# to the creator's other apps) for a clean, client-facing footer.
# The Streamlit Cloud badge (creator avatar + logo) is drawn by Cloud's outer page,
# outside this iframe, so CSS here can't reach it; add the rule to the parent
# document instead (same-origin). No-op when run locally.
_BADGE_JS = """<script>(function(){try{var w=window;while(w.parent&&w.parent!==w){try{void w.parent.document;w=w.parent;}catch(e){break;}}var d=w.document;if(d.getElementById('jsa-hide-cloud-badge'))return;var s=d.createElement('style');s.id='jsa-hide-cloud-badge';s.textContent="[class*='_profileContainer_'],[class*='_viewerBadge_']{display:none !important;}";d.head.appendChild(s);}catch(e){}})();</script>"""
try:
    st.html(_BADGE_JS, unsafe_allow_javascript=True)
except TypeError:  # older Streamlit without st.html JS support
    import streamlit.components.v1 as _stc
    _stc.html(_BADGE_JS, height=0)

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
