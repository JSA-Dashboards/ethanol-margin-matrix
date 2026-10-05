"""Margins Dashboard — summary across all margin structures tracked by JSA."""
from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import calc_ethanol
from common import (
    GREEN, JPSI_BLUE, ORANGE,
    add_time_range_controls, ddg_ratio_anchor, get_api_key, load_ams_weekly, load_curve,
    load_hh, load_zc_dec_archive, oil_ratio_anchor, plotly_base, plotly_cfg,
)

today = date.today()

st.title("📊 Margins Dashboard")
st.caption("Summary of gross-margin structures tracked by JSA — Ethanol is live; "
           "Soy Crush, Poultry, Cattle, and Hogs will populate as their data and methodology arrive.")

api_key = get_api_key()
weekly = load_ams_weekly(today.isoformat())
zc_archive = load_zc_dec_archive()

# ── Current margin snapshot ───────────────────────────────────────────────────
st.markdown("##### Current gross margin — by structure")

zc_curve = load_curve("ZC", api_key, today.isoformat(), 8)
cu_curve = load_curve("CU", api_key, today.isoformat(), 4)
ng_curve = load_curve("NG", api_key, today.isoformat(), 8)

zc_dec = zc_curve[zc_curve["ticker"].str.contains(r"ZCZ\d", regex=True)] if not zc_curve.empty else pd.DataFrame()
corn_zc = float(zc_dec.iloc[0]["price"]) / 100.0 if not zc_dec.empty else None
cu_front = float(cu_curve.iloc[0]["price"]) if not cu_curve.empty else None

ng_oct = ng_curve[ng_curve["ticker"].str.contains("V")] if not ng_curve.empty else pd.DataFrame()
if ng_oct.empty and not ng_curve.empty:
    ng_oct = ng_curve
ng_price = float(ng_oct.iloc[0]["price"]) if not ng_oct.empty else None
if ng_price is None:
    hh = load_hh()
    ng_price = float(hh.iloc[-1]) if len(hh) else None
gas_price = (ng_price - 0.35) if ng_price else None

sbo_curve = load_curve("ZL", api_key, today.isoformat(), 3)
sbo_front = float(sbo_curve.iloc[0]["price"]) if not sbo_curve.empty else None
oil_anchor = oil_ratio_anchor(weekly, sbo_curve, api_key, today.isoformat())
oil_spot_cents = (sbo_front * oil_anchor["ratio"]) if (sbo_front is not None and oil_anchor) else None

ddg_anchor = ddg_ratio_anchor(weekly, zc_curve, api_key, today.isoformat())
ddgs_pct = ddg_anchor["ratio"] if ddg_anchor else None

ethanol_margin = None
if corn_zc and cu_front and gas_price and oil_spot_cents and ddgs_pct:
    margins = []
    for loc, cfg in calc_ethanol.LOCATIONS.items():
        corn_loc = corn_zc + cfg["corn_basis"]
        eth_loc = cu_front + cfg["eth_adj"]
        m = calc_ethanol.report_margin(
            eth_loc, corn_loc, ddgs_pct, gas_price,
            calc_ethanol.GAL_PER_BU, calc_ethanol.DDG_LB_PER_BU, calc_ethanol.OIL_LB_PER_BU,
            oil_spot_cents, calc_ethanol.GAS_MMBTU_PER_GAL, "¢/gal",
        )
        margins.append(m["margin"])
    ethanol_margin = sum(margins) / len(margins)

cards = [
    ("🌽 Ethanol", ethanol_margin, "¢/gal"),
    ("🫘 Soy Crush", None, ""),
    ("🐔 Poultry", None, ""),
    ("🐄 Cattle", None, ""),
    ("🐖 Hogs", None, ""),
]
cols = st.columns(5)
for col, (label, val, unit) in zip(cols, cards):
    with col:
        if val is not None:
            st.metric(label, f"{val:+.1f}{unit}", border=True, delta_color="off")
        else:
            st.metric(label, "—", border=True, delta_color="off")
            st.caption("Data to follow")

st.caption(f"Ethanol margin averaged across the three report locations, as of {today:%b %d, %Y}.")

# ── Historical patterns ────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("##### Historical margin trends")

hist = calc_ethanol.historical_margins(
    weekly, zc_archive,
    b_col=calc_ethanol.default_b_col(), eth_adj=calc_ethanol.default_eth_adj(),
    ddg_lb=calc_ethanol.DDG_LB_PER_BU,
    oil_lb=calc_ethanol.OIL_LB_PER_BU, gas_basis=-0.35, gas_use=calc_ethanol.GAS_MMBTU_PER_GAL,
    gal=calc_ethanol.GAL_PER_BU, unit="¢/gal",
)

if hist:
    fig = plotly_base(380)
    loc_colors = [JPSI_BLUE, GREEN, ORANGE]
    for (loc, margin_s), clr in zip(hist.items(), loc_colors):
        fig.add_trace(go.Scatter(
            x=[pd.Timestamp(d) for d in margin_s.index],
            y=margin_s.values,
            name=f"Ethanol — {loc}", mode="lines",
            line=dict(color=clr, width=2),
            hovertemplate=f"Ethanol — {loc}: %{{y:+.2f}}¢/gal<extra></extra>",
        ))
    fig.add_hline(y=0, line_color="#dee2e6", line_width=1)
    fig.update_layout(yaxis_title="Gross margin (¢/gal)")
    add_time_range_controls(fig)
    st.plotly_chart(fig, width="stretch", config=plotly_cfg())
    st.caption(
        "Ethanol margin history shown above (see the Ethanol tab for full methodology). "
        "Soy crush, poultry, cattle, and hog margin history will appear here once their "
        "data and methodology are added."
    )
else:
    st.info("Historical chart requires the AMS snapshot and the ZC December archive. "
            "Run `python ethanol_grind.py` to extend the AMS snapshot.")

# ── Coming soon ─────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("##### Coming soon")
soon_cols = st.columns(4)
for col, label in zip(soon_cols, ["Soy Crush", "Poultry", "Cattle", "Hogs"]):
    with col:
        st.info(f"**{label}** — margin data and methodology to follow.")
