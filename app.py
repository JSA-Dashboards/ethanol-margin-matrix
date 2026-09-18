"""Ethanol Margin Matrix — JSA
Three tabs:
  1. Report Style  — three-location gross margin matching the JSA Ethanol Margins report
  2. Sensitivity Matrix — AMS cash-price matrix (ethanol × corn grid)
  3. CU Curve & RINs — Chicago ethanol forward curve, corn/gas strips, RINs
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

_APP_DIR = Path(__file__).parent
load_dotenv(_APP_DIR / ".env")

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ethanol_grind

from massive_api import (
    MassiveApiError,
    get_futures_curve,
    get_settlement_histories,
    get_active_contract_tickers,
    get_snapshots,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Ethanol Margins — JSA",
    page_icon="🌽",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background:#f8f9fa; }
[data-testid="stMainBlockContainer"] { padding-top:1.2rem; }
h1 { font-size:1.3rem !important; font-weight:700; }
h5 { font-size:.88rem !important; font-weight:600; color:#495057; margin-bottom:.3rem; }
.block-container { max-width:1360px; }
</style>
""", unsafe_allow_html=True)

today = date.today()

# ── Secrets / env helpers ─────────────────────────────────────────────────────
def _secret(name: str) -> str:
    try:
        v = st.secrets.get(name, "")
    except Exception:
        v = ""
    return v or os.environ.get(name, "")

def get_api_key() -> str:
    return _secret("MASSIVE_API_KEY")

# ── Cached data loaders ────────────────────────────────────────────────────────
@st.cache_data(ttl="6h", show_spinner="Loading AMS ethanol prices…")
def load_ams_weekly(as_of: str) -> pd.DataFrame:
    return ethanol_grind.load_weekly()

@st.cache_data(ttl="6h", show_spinner=False)
def load_ams_plant_corn(as_of: str) -> pd.DataFrame:
    return ethanol_grind.load_plant_corn()

@st.cache_data(ttl="12h", show_spinner=False)
def load_hh() -> pd.Series:
    return ethanol_grind.henry_hub()

@st.cache_data(ttl="5m", show_spinner=False)
def load_curve(code: str, api_key: str, as_of: str, n: int = 12) -> pd.DataFrame:
    if not api_key:
        return pd.DataFrame(columns=["ticker", "expiration", "price"])
    try:
        return get_futures_curve(code, api_key, date.fromisoformat(as_of), n_contracts=n)
    except Exception:
        return pd.DataFrame(columns=["ticker", "expiration", "price"])

@st.cache_data(ttl="5m", show_spinner=False)
def load_hist(tickers: tuple, api_key: str, _as_of: str) -> dict[str, pd.Series]:
    if not api_key or not tickers:
        return {}
    try:
        return get_settlement_histories(list(tickers), api_key)
    except Exception:
        return {}

# ── Futures archive (ZC December for history) ─────────────────────────────────
@st.cache_data(ttl="24h", show_spinner=False)
def load_zc_dec_archive() -> pd.Series:
    """ZC December front (or nearest December) daily history from the archive."""
    try:
        df = pd.read_csv(
            _APP_DIR / "data" / "futures_history_archive.csv",
            parse_dates=["date"],
        )
        zc = df[(df["product_code"] == "ZC") & (df["month"] == "Z")].copy()
        if zc.empty:
            return pd.Series(dtype=float)
        # take the nearest December contract for each date
        zc = zc.sort_values("date")
        # price in cents/bu → $/bu
        s = zc.groupby("date")["price"].first() / 100.0
        return s.sort_index()
    except Exception:
        return pd.Series(dtype=float)

# ── Styling helpers ───────────────────────────────────────────────────────────
JPSI_BLUE  = "#0693e3"
JPSI_DARK  = "#32373c"
GREEN      = "#27ae60"
RED        = "#c0392b"
ORANGE     = "#e8833a"
GOLD       = "#f1c40f"

def _col(val: float) -> str:
    """Cell background for margin value."""
    if pd.isna(val):
        return ""
    if val >= 0:
        intensity = min(abs(val) / 1.5, 1.0)
        r = int(255 - intensity * 140); g = int(220 + intensity * 15); b = int(255 - intensity * 140)
    else:
        intensity = min(abs(val) / 1.5, 1.0)
        r = int(220 + intensity * 35); g = int(255 - intensity * 160); b = int(255 - intensity * 160)
    return f"background-color:rgb({r},{g},{b});color:#222;"

def _delta_col(val):
    if isinstance(val, float):
        return f"color:{'#27ae60' if val >= 0 else '#c0392b'};"
    return ""

def _plotly_base(height=400) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        height=height, margin=dict(l=50, r=20, t=30, b=50),
        font=dict(family="Inter, Arial, sans-serif", size=12),
        plot_bgcolor="white", paper_bgcolor="white",
        xaxis=dict(showgrid=True, gridcolor="#e9ecef", zeroline=False),
        yaxis=dict(showgrid=True, gridcolor="#e9ecef", zeroline=False),
        legend=dict(orientation="h", y=-0.22, x=0),
    )
    return fig

def _plotly_cfg():
    return {"displayModeBar": False}

# ── Month-code helpers ────────────────────────────────────────────────────────
_MONTH_NAMES = {
    "F":"Jan","G":"Feb","H":"Mar","J":"Apr","K":"May","M":"Jun",
    "N":"Jul","Q":"Aug","U":"Sep","V":"Oct","X":"Nov","Z":"Dec",
}
def _friendly(ticker: str, code: str) -> str:
    t = ticker[len(code):]
    if len(t) == 2:
        mc, yr = t[0], t[1]
        return f"{_MONTH_NAMES.get(mc, mc)}'{yr}"
    return ticker

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1  ─  REPORT-STYLE THREE-LOCATION MARGIN
# ─────────────────────────────────────────────────────────────────────────────

# Location configuration — from PDF footnote
LOCATIONS = {
    "Columbus CSX": {
        "corn_basis":  0.17,   # +17¢ over ZC Dec
        "eth_adj":     0.00,   # @ Chicago flat
        "region":      "Eastern corn belt",
    },
    "NE Grp 3": {
        "corn_basis":  0.37,   # +37¢ over ZC Dec
        "eth_adj":    -0.12,   # @ Chicago − 12¢
        "region":      "Western corn belt",
    },
    "Minnesota": {
        "corn_basis":  0.50,   # +50¢ over ZC Dec
        "eth_adj":    -0.12,   # @ Chicago − 12¢
        "region":      "Western corn belt",
    },
}

GAL_PER_BU  = ethanol_grind.DEFAULT_GAL_PER_BU   # 2.85
DDG_LB_PER_BU = ethanol_grind.DEFAULT_DDG_LB_PER_BU   # 15.5
OIL_LB_PER_BU = ethanol_grind.DEFAULT_OIL_LB_PER_BU   # 0.75
GAS_MMBTU_PER_GAL = ethanol_grind.DEFAULT_GAS_MMBTU_PER_GAL  # 0.024
DDGS_PCT_CORN = 0.975   # DDGS valued at 97.5% of corn $/ton


def report_margin(eth_gal: float, corn_bu: float, ddgs_pct: float,
                  gas_price: float, gal: float, ddg_lb: float, oil_lb: float,
                  oil_price_cents_lb: float, gas_use: float,
                  unit: str = "¢/gal") -> dict:
    """Gross margin for one location.
    Returns dict of revenue/cost/margin components."""
    eth_rev_bu   = eth_gal * gal
    # DDGS: ddgs_pct of corn $/ton, times lbs/bu ÷ 2000
    corn_ton     = corn_bu * (2000 / 56)       # $/ton
    ddgs_ton     = corn_ton * ddgs_pct
    ddg_rev_bu   = ddgs_ton * ddg_lb / 2000
    oil_rev_bu   = oil_price_cents_lb / 100 * oil_lb
    gas_cost_bu  = gas_price * gas_use * gal
    revenue_bu   = eth_rev_bu + ddg_rev_bu + oil_rev_bu
    cost_bu      = corn_bu + gas_cost_bu
    margin_bu    = revenue_bu - cost_bu

    if unit == "¢/gal":
        scale = 100 / gal if gal else 0
    elif unit == "$/gal":
        scale = 1 / gal if gal else 0
    else:
        scale = 1.0  # $/bu

    return {
        "eth_rev": eth_rev_bu * scale,
        "ddg_rev": ddg_rev_bu * scale,
        "oil_rev": oil_rev_bu * scale,
        "revenue": revenue_bu * scale,
        "corn_cost": corn_bu * scale,
        "gas_cost": gas_cost_bu * scale,
        "total_cost": cost_bu * scale,
        "margin": margin_bu * scale,
    }


def render_report_tab(api_key: str, weekly: pd.DataFrame):
    st.markdown("##### Three-location gross margin — December corn futures + basis methodology")
    st.caption(
        "Matches the JSA Ethanol Margins report. "
        "**Inputs**: December corn futures + location basis; October NG futures − gas basis. "
        "**Outputs**: Chicago ethanol (CU front) ± location adjustment; "
        "DDGS priced at 97.5% of cash corn $/ton; corn oil at AMS spot."
    )

    # ── Live futures ──────────────────────────────────────────────────────────
    zc_curve  = load_curve("ZC", api_key, today.isoformat(), 8)
    cu_curve  = load_curve("CU", api_key, today.isoformat(), 4)
    ng_curve  = load_curve("NG", api_key, today.isoformat(), 8)

    # December corn — ticker format "ZCZ6" (month code at position 2, year digit after)
    zc_dec = zc_curve[zc_curve["ticker"].str.contains(r"ZCZ\d", regex=True)] if not zc_curve.empty else pd.DataFrame()
    corn_zc_raw = float(zc_dec.iloc[0]["price"]) / 100.0 if not zc_dec.empty else None  # ZC in cents → $/bu

    # Front ethanol (CU)
    cu_front = float(cu_curve.iloc[0]["price"]) if not cu_curve.empty else None

    # October NG (V month code)
    ng_oct = ng_curve[ng_curve["ticker"].str.contains("V")] if not ng_curve.empty else pd.DataFrame()
    if ng_oct.empty and not ng_curve.empty:
        ng_oct = ng_curve  # fall back to front
    ng_price_raw = float(ng_oct.iloc[0]["price"]) if not ng_oct.empty else None

    # ── Yield & basis controls ────────────────────────────────────────────────
    with st.expander("Methodology inputs (click to edit)", expanded=False):
        ec1, ec2, ec3, ec4 = st.columns(4)
        with ec1:
            gal = st.number_input("Ethanol gal/bu", 0.0, 4.0, GAL_PER_BU, 0.01, key="rpt_gal")
            ddg_lb = st.number_input("DDGS lb/bu", 0.0, 20.0, DDG_LB_PER_BU, 0.5, key="rpt_ddglb")
        with ec2:
            oil_lb = st.number_input("Corn oil lb/bu", 0.0, 3.0, OIL_LB_PER_BU, 0.05, key="rpt_oillb")
            gas_use = st.number_input("Gas MMBtu/gal", 0.0, 0.20, GAS_MMBTU_PER_GAL,
                                      0.001, format="%.3f", key="rpt_gasuse")
        with ec3:
            ddgs_pct = st.number_input("DDGS % of corn $/ton", 0.0, 1.5,
                                       DDGS_PCT_CORN, 0.005, format="%.3f", key="rpt_ddgspct",
                                       help="Report uses 97.5% of cash corn $/ton")
            gas_basis = st.number_input("Gas basis ($/MMBtu, neg = under)", -2.0, 2.0,
                                        -0.35, 0.01, key="rpt_gasbasis",
                                        help="'35-V' in the report = −$0.35 under October NG")
        with ec4:
            st.markdown("**Corn basis (¢ vs ZC Dec)**")
            b_col = {}
            for loc, cfg in LOCATIONS.items():
                b_col[loc] = st.number_input(
                    loc, value=cfg["corn_basis"], step=0.01, format="%.2f",
                    key=f"rpt_basis_{loc}",
                    help=f"Default: +{cfg['corn_basis']*100:.0f}¢ over Dec ZC"
                )

        st.markdown("**Ethanol location adjustments (¢/gal vs Chicago)**")
        ea_col = st.columns(3)
        eth_adj = {}
        for i, (loc, cfg) in enumerate(LOCATIONS.items()):
            with ea_col[i]:
                eth_adj[loc] = st.number_input(
                    loc, value=cfg["eth_adj"], step=0.01, format="%.2f",
                    key=f"rpt_ethadj_{loc}",
                    help="Eastern = flat; Western = −0.12 (−12¢ vs Chicago)"
                )

    # AMS corn oil (most recent, all states avg)
    oil_spot_cents = None
    if len(weekly):
        oil_s = ethanol_grind.series(weekly, "Distillers Corn Oil")
        if len(oil_s):
            oil_spot_cents = float(oil_s.iloc[-1])
    oil_spot_cents = oil_spot_cents or 75.0

    # Fall back to Henry Hub from AMS/EIA if NG futures unavailable
    if ng_price_raw is None:
        hh = load_hh()
        ng_price_raw = float(hh.iloc[-1]) if len(hh) else None
    gas_price = (ng_price_raw + gas_basis) if ng_price_raw else None

    unit_choice = st.segmented_control("Margin unit", ["¢/gal", "$/gal", "$/bu"],
                                       default="¢/gal", key="rpt_unit") or "¢/gal"
    unit_fmt = ".2f" if unit_choice in ("$/gal", "$/bu") else ".1f"

    # ── Location metrics ──────────────────────────────────────────────────────
    st.markdown("---")
    has_futures = corn_zc_raw and cu_front and gas_price

    if not has_futures:
        if not api_key:
            st.info("Futures unavailable — add `MASSIVE_API_KEY` to secrets or .env.")
        else:
            missing = [x for x, v in [("ZC Dec", corn_zc_raw), ("CU front", cu_front), ("gas price", gas_price)] if not v]
            st.warning(f"Some futures unavailable: {', '.join(missing)}. Showing available data only.")

    lc1, lc2, lc3 = st.columns(3)
    margin_vals = {}
    for col, (loc, cfg) in zip([lc1, lc2, lc3], LOCATIONS.items()):
        with col:
            st.markdown(f"**{loc}** — *{cfg['region']}*")
            if has_futures:
                corn_loc = corn_zc_raw + b_col[loc]
                eth_loc  = cu_front + eth_adj[loc]
                m = report_margin(eth_loc, corn_loc, ddgs_pct, gas_price, gal, ddg_lb,
                                  oil_lb, oil_spot_cents, gas_use, unit_choice)
                margin_vals[loc] = m["margin"]
                mcol, dcol = st.columns(2)
                with mcol:
                    st.metric("Gross margin", f"{m['margin']:+{unit_fmt}}{unit_choice}",
                              border=True, delta_color="off")
                with dcol:
                    st.metric("Ethanol", f"${eth_loc:.3f}/gal", border=True, delta_color="off")
                st.metric("Corn (ZC+basis)", f"${corn_loc:.3f}/bu",
                          f"ZC ${corn_zc_raw:.3f} + {b_col[loc]*100:+.0f}¢",
                          delta_color="off", border=True)
                st.metric("DDGS rev", f"${m['ddg_rev']/100:.3f}/bu" if unit_choice=="¢/gal"
                          else f"${m['ddg_rev']:.3f}/{unit_choice.split('/')[1]}",
                          f"{ddgs_pct*100:.1f}% of ${corn_loc*(2000/56):.0f}/ton corn",
                          delta_color="off", border=True)
                st.metric("Gas (Oct NG+basis)",
                          f"${gas_price:.3f}/MMBtu" if gas_price else "—",
                          f"NG ${ng_price_raw:.3f} + {gas_basis:+.2f}",
                          delta_color="off", border=True)
            else:
                st.metric("Gross margin", "—", border=True, delta_color="off")
                st.caption(f"Corn basis: +{b_col[loc]*100:.0f}¢ | Eth adj: {eth_adj[loc]*100:+.0f}¢")

    if has_futures:
        st.caption(
            f"As of {today:%b %d, %Y} · Dec ZC ${corn_zc_raw*100:.2f}¢/bu · "
            f"CU front ${cu_front:.3f}/gal · Oct NG ${ng_price_raw:.3f}/MMBtu · "
            f"corn oil {oil_spot_cents:.1f}¢/lb (AMS)"
        )

    # ── History chart (AMS-based proxy) ──────────────────────────────────────
    st.markdown("---")
    st.markdown("##### Historical margin — AMS weekly ethanol price + ZC Dec archive + location basis")

    zc_archive = load_zc_dec_archive()
    if len(weekly) and len(zc_archive):
        # Build location margin series from AMS ethanol + ZC archive
        eth_il = ethanol_grind.series(weekly, "Ethanol", "Illinois")
        eth_ia = ethanol_grind.series(weekly, "Ethanol", "Iowa")
        oil_s  = ethanol_grind.series(weekly, "Distillers Corn Oil")
        zc_archive_ts = zc_archive.copy()
        zc_archive_ts.index = pd.to_datetime(zc_archive_ts.index)
        zc_weekly = zc_archive_ts.resample("W-FRI").last().ffill()

        fig = _plotly_base(380)
        loc_colors = [JPSI_BLUE, GREEN, ORANGE]
        for (loc, cfg), clr in zip(LOCATIONS.items(), loc_colors):
            eth_base = eth_ia if ("NE" in loc or "Minnesota" in loc) else eth_il
            eth_loc_s = eth_base.copy()
            eth_loc_s.index = pd.to_datetime(eth_loc_s.index)
            eth_loc_s = eth_loc_s + eth_adj[loc]
            oil_ts = oil_s.copy()
            oil_ts.index = pd.to_datetime(oil_ts.index)
            # Align with ZC
            combined = pd.DataFrame({"eth": eth_loc_s, "zc": zc_weekly, "oil": oil_ts}).dropna()
            if combined.empty:
                continue
            # DDGS = ddgs_pct × corn $/ton × ddg_lb/2000
            corn_s = combined["zc"] + b_col[loc]
            ddgs_s = corn_s * (2000/56) * ddgs_pct * ddg_lb / 2000
            oil_s2 = combined["oil"] / 100 * oil_lb
            # Gas: use Henry Hub from EIA as proxy for historical (or fixed $3)
            gas_s = 3.0 + gas_basis  # rough proxy for historical
            gas_cost_s = gas_s * gas_use * gal
            eth_rev_s = combined["eth"] * gal
            margin_s = (eth_rev_s + ddgs_s + oil_s2 - corn_s - gas_cost_s)
            if unit_choice == "¢/gal":
                margin_s = margin_s / gal * 100
            elif unit_choice == "$/gal":
                margin_s = margin_s / gal
            fig.add_trace(go.Scatter(
                x=[pd.Timestamp(d) for d in margin_s.index],
                y=margin_s.values,
                name=loc, mode="lines",
                line=dict(color=clr, width=2),
                hovertemplate=f"{loc}: %{{y:+.2f}}{unit_choice}<extra></extra>",
            ))
        fig.add_hline(y=0, line_color="#dee2e6", line_width=1)
        fig.update_layout(yaxis_title=f"Gross margin ({unit_choice})")
        st.plotly_chart(fig, width="stretch", config=_plotly_cfg())
        st.caption(
            "Historical ethanol: AMS weekly cash (Illinois/Iowa). "
            "Historical corn: ZC December archive (cents/bu). "
            "Historical gas: $3.00/MMBtu proxy (live data only available for current session)."
        )
    else:
        st.info("Historical chart requires the AMS snapshot and the ZC December archive. "
                "Run `python ethanol_grind.py` to extend the AMS snapshot.")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2  ─  SENSITIVITY MATRIX
# ─────────────────────────────────────────────────────────────────────────────

def build_matrix(
    eth_prices: np.ndarray, corn_prices: np.ndarray,
    ddg_rev_per_bu: float, oil_rev_per_bu: float,
    gas_cost_per_bu: float, opex_per_bu: float,
    gal_per_bu: float, unit: str = "$/bu",
) -> pd.DataFrame:
    eth_col = eth_prices[:, None] * gal_per_bu + ddg_rev_per_bu + oil_rev_per_bu
    margin  = eth_col - corn_prices[None, :] - gas_cost_per_bu - opex_per_bu
    if unit == "$/gal":
        margin = margin / gal_per_bu if gal_per_bu else margin
    return pd.DataFrame(
        margin,
        index=[f"{p:.2f}" for p in eth_prices],
        columns=[f"{p:.2f}" for p in corn_prices],
        dtype=float,
    )


def render_matrix_tab(weekly: pd.DataFrame, plant_corn: pd.DataFrame):
    st.markdown("##### Sensitivity matrix — AMS cash prices (ethanol × corn grid)")
    st.caption(
        "Co-product revenue from AMS Market News weekly report. "
        "Matrix shows margin across a range of ethanol and corn prices."
    )

    state_list = ["All states"] + ethanol_grind.states(weekly)

    c1, c2, c3, c4, c5 = st.columns([1.6, 1.4, 1.2, 1.2, 1.2])
    with c1:
        state = st.selectbox("State", state_list, index=min(1, len(state_list)-1), key="mx_state")
    with c2:
        ddg_variety = st.selectbox("Distillers grain", ethanol_grind.DDG_VARIETIES, key="mx_ddgvar")
    with c3:
        unit = st.segmented_control("Margin unit", ["$/bu", "$/gal"],
                                    default="$/bu", key="mx_unit") or "$/bu"
    with c4:
        gas_src = st.segmented_control("Gas price", ["Henry Hub", "Fixed"],
                                       default="Henry Hub", key="mx_gassrc") or "Henry Hub"
    hh = load_hh()
    hh_latest = float(hh.iloc[-1]) if len(hh) else 3.00
    with c5:
        if gas_src == "Fixed" or not len(hh):
            gas_price = st.number_input("Gas $/MMBtu", 0.0, 25.0,
                                        round(hh_latest, 2), 0.05, key="mx_gasfix")
        else:
            gas_price = hh_latest
            st.metric("Henry Hub", f"${hh_latest:.2f}/MMBtu",
                      f"{hh.index[-1]:%b %d}", delta_color="off")

    y1, y2, y3, y4, y5 = st.columns([1.2, 1.4, 1.2, 1.4, 1.2])
    with y1:
        gal = st.number_input("Ethanol gal/bu", 0.0, 4.0, GAL_PER_BU, 0.01, key="mx_gal")
    with y2:
        ddg_lb = st.number_input("DDGS lb/bu", 0.0, 20.0, DDG_LB_PER_BU, 0.5, key="mx_ddglb")
    with y3:
        oil_lb = st.number_input("Corn oil lb/bu", 0.0, 3.0, OIL_LB_PER_BU, 0.05, key="mx_oillb")
    with y4:
        opex = st.number_input("Other costs $/bu", 0.0, 3.0, 0.0, 0.05, key="mx_opex",
                               help="Power, enzymes, labour — excluding corn and gas")
    with y5:
        gas_use = st.number_input("Gas MMBtu/gal", 0.0, 0.2, GAS_MMBTU_PER_GAL,
                                  0.001, format="%.3f", key="mx_gasuse")

    eth_spot  = _latest(weekly, "Ethanol", state)
    ddg_spot  = _latest(weekly, "Distillers Grain", state, ddg_variety)
    oil_spot  = _latest(weekly, "Distillers Corn Oil", state)
    corn_spot = _latest(plant_corn, "Corn", state)

    ddg_rev  = (ddg_spot  * ddg_lb / 2000) if ddg_spot  else 0.0
    oil_rev  = (oil_spot  / 100 * oil_lb)  if oil_spot  else 0.0
    gas_cost = gas_price * gas_use * gal
    latest_week = weekly["date"].max() if len(weekly) else today

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    with m1:
        st.metric("Ethanol spot", f"${eth_spot:.3f}/gal" if eth_spot else "—",
                  f"${eth_spot*gal:.2f}/bu" if eth_spot else None, delta_color="off", border=True)
    with m2:
        st.metric("Distillers grain", f"${ddg_spot:.0f}/ton" if ddg_spot else "—",
                  f"${ddg_rev:.2f}/bu", delta_color="off", border=True)
    with m3:
        st.metric("Corn oil", f"{oil_spot:.1f}¢/lb" if oil_spot else "—",
                  f"${oil_rev:.2f}/bu", delta_color="off", border=True)
    with m4:
        st.metric("Plant corn bid", f"${corn_spot:.2f}/bu" if corn_spot else "—",
                  delta_color="off", border=True)
    with m5:
        st.metric("Natural gas", f"${gas_price:.2f}/MMBtu",
                  f"−${gas_cost:.2f}/bu", delta_color="off", border=True)
    with m6:
        if eth_spot and corn_spot:
            sm = eth_spot*gal + ddg_rev + oil_rev - corn_spot - gas_cost - opex
            st.metric("Spot margin", f"${sm:+.2f}/bu",
                      f"${sm/gal:+.3f}/gal", delta_color="off", border=True)
        else:
            st.metric("Spot margin", "—", border=True, delta_color="off")
    st.caption(f"Week of {latest_week:%b %d, %Y} · {state} · {ddg_variety} DDG")

    st.divider()
    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1:
        eth_lo = st.number_input("Ethanol low $/gal", 0.50, 3.50,
                                 round(max(1.20, (eth_spot or 1.80) - 0.50), 2), 0.05, key="mx_ethlo")
    with mc2:
        eth_hi = st.number_input("Ethanol high $/gal", 0.50, 4.00,
                                 round(min(3.50, (eth_spot or 1.80) + 0.50), 2), 0.05, key="mx_ethi")
    with mc3:
        corn_lo = st.number_input("Corn low $/bu", 1.00, 8.00,
                                  round(max(2.50, (corn_spot or 4.50) - 1.50), 2), 0.25, key="mx_cornlo")
    with mc4:
        corn_hi = st.number_input("Corn high $/bu", 1.00, 12.00,
                                  round(min(9.00, (corn_spot or 4.50) + 1.50), 2), 0.25, key="mx_cornhi")

    n_eth  = max(3, round((eth_hi  - eth_lo)  / 0.05) + 1)
    n_corn = max(3, round((corn_hi - corn_lo) / 0.25) + 1)
    eth_prices  = np.linspace(eth_lo,  eth_hi,  n_eth)
    corn_prices = np.linspace(corn_lo, corn_hi, n_corn)

    matrix = build_matrix(eth_prices[::-1], corn_prices, ddg_rev, oil_rev,
                          gas_cost, opex, gal, unit)

    fmt = "{:+.2f}" if unit == "$/bu" else "{:+.3f}"
    styled = (matrix.style.map(_col).format(fmt)
              .set_properties(**{"font-size":"0.78rem","text-align":"center","padding":"3px 8px",
                                 "border":"1px solid #dee2e6"}))

    if eth_spot:
        nr = f"{eth_prices[::-1][np.argmin(np.abs(eth_prices[::-1] - eth_spot))]:.2f}"
        styled = styled.set_properties(**{"border":"2.5px solid #0693e3","font-weight":"700"},
                                       subset=pd.IndexSlice[nr, :])
    if corn_spot:
        nc = f"{corn_prices[np.argmin(np.abs(corn_prices - corn_spot))]:.2f}"
        styled = styled.set_properties(**{"border":"2.5px solid #e8833a","font-weight":"700"},
                                       subset=pd.IndexSlice[:, nc])

    st.markdown(f"**Margin {unit} · {ddg_variety} DDG · gas ${gas_price:.2f}/MMBtu · {gal:.2f} gal/bu**")
    html = styled.to_html()
    st.markdown(f'<div style="overflow-x:auto;border-radius:6px;border:1px solid #dee2e6;">{html}</div>',
                unsafe_allow_html=True)
    st.caption(
        "Blue border = nearest ethanol spot row. Orange border = nearest plant corn bid column. "
        f"Co-products: DDG ${ddg_rev:.2f}/bu + oil ${oil_rev:.2f}/bu = ${ddg_rev+oil_rev:.2f}/bu. "
        "Sources: USDA AMS Market News, EIA."
    )

    with st.expander("Breakeven — ethanol price needed at each corn price"):
        co = ddg_rev + oil_rev
        be = (corn_prices + gas_cost + opex - co) / gal if gal else corn_prices * 0
        fig = _plotly_base(320)
        fig.add_trace(go.Scatter(x=corn_prices, y=be, mode="lines",
                                 line=dict(color=JPSI_BLUE, width=2.5), name="Breakeven ethanol",
                                 hovertemplate="Corn $%{x:.2f} → break-even $%{y:.3f}/gal<extra></extra>"))
        if eth_spot:
            fig.add_hline(y=eth_spot, line=dict(color=JPSI_BLUE, width=1.5, dash="dot"),
                          annotation_text=f"Spot ${eth_spot:.3f}/gal")
        if corn_spot:
            fig.add_vline(x=corn_spot, line=dict(color=ORANGE, width=1.5, dash="dot"),
                          annotation_text=f"Plant bid ${corn_spot:.2f}/bu")
        fig.update_layout(xaxis_title="Corn $/bu", yaxis_title="Break-even ethanol $/gal")
        st.plotly_chart(fig, width='stretch', config=_plotly_cfg())


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3  ─  CU CURVE & RINS
# ─────────────────────────────────────────────────────────────────────────────

def _spread_label(i: int, tickers: list[str], code: str) -> str:
    if i + 1 >= len(tickers):
        return ""
    m1 = tickers[i][len(code)][0]
    m2 = tickers[i+1][len(code)][0]
    return f"{m1}/{m2}"


def render_curve_tab(api_key: str, weekly: pd.DataFrame):
    st.markdown("##### Chicago Ethanol forward curve, corn & nat gas strips, and RINs")

    cu = load_curve("CU", api_key, today.isoformat(), 10)
    zc = load_curve("ZC", api_key, today.isoformat(), 6)
    ng = load_curve("NG", api_key, today.isoformat(), 6)

    no_api = cu.empty and zc.empty and ng.empty
    if no_api:
        st.info("Add `MASSIVE_API_KEY` to your Streamlit secrets or .env to load live futures data.")

    # ── CU Table ──────────────────────────────────────────────────────────────
    left, right = st.columns([1.3, 1.0])

    with left:
        st.markdown("###### Chicago Ethanol Curve w/ Spreads")
        if not cu.empty:
            hist = load_hist(tuple(cu["ticker"]), api_key, today.isoformat())
            rows = []
            prev_price = None
            for i, r in enumerate(cu.itertuples(index=False)):
                series = hist.get(r.ticker)
                chg = float(r.price - series.iloc[-2]) if series is not None and len(series) >= 2 else None
                spd_label = _spread_label(i, list(cu["ticker"]), "CU")
                if prev_price is not None and spd_label:
                    spd_val = round(float(r.price) - prev_price, 4)
                else:
                    spd_val = None
                rows.append({
                    "Contract": pd.Timestamp(r.expiration).strftime("%b-%y"),
                    "Settle":   round(float(r.price), 4),
                    "+/-":      round(chg, 4) if chg is not None else None,
                    "Vol":      None,
                    "OI":       None,
                    "CU Spread": spd_label,
                    "Spread":   round(spd_val, 4) if spd_val is not None else None,
                })
                prev_price = float(r.price)

            df_cu = pd.DataFrame(rows)

            def _cu_style(col):
                if col.name == "+/-":
                    return [f"color:{'#27ae60' if (v or 0)>=0 else '#c0392b'};" if v is not None else "" for v in col]
                if col.name == "Spread":
                    return [f"color:{'#27ae60' if (v or 0)>=0 else '#c0392b'};" if v is not None else "" for v in col]
                return [""] * len(col)

            styled_cu = (df_cu.style
                         .apply(_cu_style)
                         .format({"Settle": "{:.4f}", "+/-": "{:+.4f}", "Spread": "{:+.4f}"},
                                 na_rep="—")
                         .set_properties(**{"font-size": "0.80rem", "text-align": "center"}))
            st.dataframe(styled_cu, hide_index=True, width='stretch',
                         height=min(36*(len(df_cu)+1)+3, 420))
        else:
            st.caption("Live CU data unavailable.")

    with right:
        # CU Forward Curve chart
        if not cu.empty:
            st.markdown("###### CU Forward Curve")
            fig = _plotly_base(200)
            fig.add_trace(go.Scatter(
                x=[pd.Timestamp(d) for d in cu["expiration"]],
                y=cu["price"].tolist(), mode="lines+markers",
                line=dict(color=JPSI_BLUE, width=2),
                marker=dict(size=6),
                hovertemplate="%{x|%b %y}: $%{y:.4f}/gal<extra></extra>",
            ))
            fig.update_layout(
                margin=dict(l=40, r=10, t=20, b=40),
                yaxis_title="$/gal", height=200,
            )
            st.plotly_chart(fig, width='stretch', config=_plotly_cfg())

    st.divider()

    # ── Corn & Gas tables ─────────────────────────────────────────────────────
    cc1, cc2, cc3 = st.columns([1.1, 1.1, 0.8])

    with cc1:
        st.markdown("###### Corn Futures")
        if not zc.empty:
            hist_zc = load_hist(tuple(zc["ticker"]), api_key, today.isoformat())
            rows_zc = []
            for r in zc.itertuples(index=False):
                s = hist_zc.get(r.ticker)
                chg = float(r.price - s.iloc[-2]) if s is not None and len(s) >= 2 else None
                rows_zc.append({
                    "Contract": _friendly(r.ticker, "ZC"),
                    "Last": round(r.price, 2),
                    "+/-": round(chg, 2) if chg is not None else None,
                })
            df_zc = pd.DataFrame(rows_zc)

            def _zc_style(col):
                if col.name == "+/-":
                    return [f"color:{'#27ae60' if (v or 0)>=0 else '#c0392b'};" if v is not None else "" for v in col]
                return [""] * len(col)

            st.dataframe(
                df_zc.style.apply(_zc_style)
                     .format({"Last": "{:.2f}", "+/-": "{:+.2f}"}, na_rep="—")
                     .set_properties(**{"font-size": "0.80rem", "text-align": "center"}),
                hide_index=True, width='stretch',
                height=min(36*(len(df_zc)+1)+3, 280),
            )
        else:
            st.caption("Live ZC data unavailable.")

    with cc2:
        st.markdown("###### Nat Gas Futures")
        if not ng.empty:
            hist_ng = load_hist(tuple(ng["ticker"]), api_key, today.isoformat())
            rows_ng = []
            for r in ng.itertuples(index=False):
                s = hist_ng.get(r.ticker)
                chg = float(r.price - s.iloc[-2]) if s is not None and len(s) >= 2 else None
                rows_ng.append({
                    "Contract": _friendly(r.ticker, "NG"),
                    "Last": round(r.price, 3),
                    "+/-": round(chg, 3) if chg is not None else None,
                })
            df_ng = pd.DataFrame(rows_ng)

            def _ng_style(col):
                if col.name == "+/-":
                    return [f"color:{'#27ae60' if (v or 0)>=0 else '#c0392b'};" if v is not None else "" for v in col]
                return [""] * len(col)

            st.dataframe(
                df_ng.style.apply(_ng_style)
                     .format({"Last": "{:.3f}", "+/-": "{:+.3f}"}, na_rep="—")
                     .set_properties(**{"font-size": "0.80rem", "text-align": "center"}),
                hide_index=True, width='stretch',
                height=min(36*(len(df_ng)+1)+3, 280),
            )
        else:
            st.caption("Live NG data unavailable.")

    with cc3:
        st.markdown("###### RINs")
        st.caption("OPIS D6 Ethanol & D4 Biodiesel")
        # Manual entry — OPIS RIN data requires separate subscription
        rin_year = today.year
        d6 = st.number_input(f"D6 Ethanol {rin_year}", 0.0, 5.0, 2.12, 0.005,
                             format="%.4f", key="rin_d6")
        d6_chg = st.number_input("D6 +/-", -1.0, 1.0, -0.005, 0.001,
                                  format="%.4f", key="rin_d6chg")
        d4 = st.number_input(f"D4 Biodiesel {rin_year}", 0.0, 5.0, 2.175, 0.005,
                             format="%.4f", key="rin_d4")
        d4_chg = st.number_input("D4 +/-", -1.0, 1.0, -0.005, 0.001,
                                  format="%.4f", key="rin_d4chg")
        st.caption("Enter from OPIS RIN report")

    st.divider()

    # ── Ethanol vs Corn historical chart ──────────────────────────────────────
    st.markdown("###### Ethanol vs Corn futures — historical")
    zc_archive = load_zc_dec_archive()
    eth_il = ethanol_grind.series(weekly, "Ethanol", "Illinois") if len(weekly) else pd.Series(dtype=float)

    if len(zc_archive) or len(eth_il):
        fig = _plotly_base(380)
        if len(zc_archive):
            fig.add_trace(go.Scatter(
                x=[pd.Timestamp(d) for d in zc_archive.index],
                y=(zc_archive * 100).values,  # ¢/bu for display
                name="CBOT Corn (¢/bu)", mode="lines",
                line=dict(color=GOLD, width=1.5),
                yaxis="y2",
                hovertemplate="Corn: %{y:.1f}¢/bu<extra></extra>",
            ))
        if len(eth_il):
            fig.add_trace(go.Scatter(
                x=[pd.Timestamp(d) for d in eth_il.index],
                y=eth_il.values,
                name="Chicago Ethanol $/gal (AMS)", mode="lines",
                line=dict(color=JPSI_BLUE, width=2),
                hovertemplate="Ethanol: $%{y:.3f}/gal<extra></extra>",
            ))
        fig.update_layout(
            yaxis=dict(title="Ethanol $/gal", gridcolor="#e9ecef"),
            yaxis2=dict(title="Corn ¢/bu", overlaying="y", side="right", showgrid=False),
            legend=dict(orientation="h", y=-0.22),
        )
        st.plotly_chart(fig, width='stretch', config=_plotly_cfg())
        st.caption("Corn: ZC December archive. Ethanol: AMS Illinois weekly cash.")
    else:
        st.info("No historical data available.")


# ─────────────────────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────────────────────
def _latest(frame: pd.DataFrame, commodity: str, state: str,
            variety: str | None = None) -> float | None:
    s = ethanol_grind.series(frame, commodity, state, variety)
    return float(s.iloc[-1]) if len(s) else None


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
st.title("🌽 Ethanol Margins — JSA")

api_key    = get_api_key()

with st.sidebar:
    if api_key:
        st.success("Futures API: connected")
    else:
        st.error("MASSIVE_API_KEY not found — add to .env or Streamlit secrets")

weekly     = load_ams_weekly(today.isoformat())
plant_corn = load_ams_plant_corn(today.isoformat())

if not len(weekly):
    st.error("No AMS ethanol data. Run `python ethanol_grind.py` in the ethanol-margin-matrix folder.")
    st.stop()

tab1, tab2, tab3 = st.tabs(["Report Style", "Sensitivity Matrix", "CU Curve & RINs"])

with tab1:
    render_report_tab(api_key, weekly)

with tab2:
    render_matrix_tab(weekly, plant_corn)

with tab3:
    render_curve_tab(api_key, weekly)
