"""Shared helpers for the Margins Dashboard — data loaders, styling, and API helpers
used across the summary page and each margin-structure page.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).parent
load_dotenv(APP_DIR / ".env")

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import calc_ethanol
import ethanol_grind
from massive_api import get_futures_curve, get_settlement_histories

# ── Secrets / env helpers ─────────────────────────────────────────────────────
def secret(name: str) -> str:
    try:
        v = st.secrets.get(name, "")
    except Exception:
        v = ""
    return v or os.environ.get(name, "")


def get_api_key() -> str:
    return secret("MASSIVE_API_KEY")


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


def zc_dec_ticker_and_price(zc_curve: pd.DataFrame) -> tuple[str | None, float | None]:
    """December ZC ticker + price ($/bu) from an already-fetched ZC curve."""
    zc_dec = zc_curve[zc_curve["ticker"].str.contains(r"ZCZ\d", regex=True)] if not zc_curve.empty else pd.DataFrame()
    if zc_dec.empty:
        return None, None
    return zc_dec.iloc[0]["ticker"], float(zc_dec.iloc[0]["price"]) / 100.0


def oil_ratio_anchor(weekly: pd.DataFrame, sbo_curve: pd.DataFrame, api_key: str, as_of: str) -> dict | None:
    """AMS distillers-corn-oil ÷ front-month soybean-oil (ZL) ratio, anchored to the last
    AMS report date. Shared by every page that needs a live/forward corn-oil estimate so
    they all use the identical method — see calc_ethanol.ratio_anchor for the mechanics."""
    if sbo_curve.empty:
        return None
    sbo_ticker = sbo_curve.iloc[0]["ticker"]
    sbo_hist = load_hist((sbo_ticker,), api_key, as_of).get(sbo_ticker, pd.Series(dtype=float))
    ams_oil = ethanol_grind.series(weekly, "Distillers Corn Oil") if len(weekly) else pd.Series(dtype=float)
    return calc_ethanol.ratio_anchor(ams_oil, sbo_hist)


def ddg_ratio_anchor(weekly: pd.DataFrame, zc_curve: pd.DataFrame, api_key: str, as_of: str) -> dict | None:
    """AMS distillers-grain (Dried 10%) ÷ December-ZC-corn ($/ton) ratio, anchored to the
    last AMS report date. Shared by every page that needs a live/forward DDG estimate so
    they all use the identical method — see calc_ethanol.ratio_anchor for the mechanics."""
    zc_ticker, _ = zc_dec_ticker_and_price(zc_curve)
    if not zc_ticker:
        return None
    zc_hist = load_hist((zc_ticker,), api_key, as_of).get(zc_ticker, pd.Series(dtype=float))
    zc_hist_ton = (zc_hist / 100.0) * (2000 / 56)  # ¢/bu → $/bu → $/ton
    ams_ddg = ethanol_grind.series(weekly, "Distillers Grain", variety=calc_ethanol.DDG_VARIETY) if len(weekly) else pd.Series(dtype=float)
    return calc_ethanol.ratio_anchor(ams_ddg, zc_hist_ton)


@st.cache_data(ttl="24h", show_spinner=False)
def load_zc_dec_archive() -> pd.Series:
    """ZC December front (or nearest December) daily history from the archive."""
    try:
        df = pd.read_csv(APP_DIR / "data" / "futures_history_archive.csv", parse_dates=["date"])
        zc = df[(df["product_code"] == "ZC") & (df["month"] == "Z")].copy()
        if zc.empty:
            return pd.Series(dtype=float)
        zc = zc.sort_values("date")
        s = zc.groupby("date")["price"].first() / 100.0  # cents/bu → $/bu
        return s.sort_index()
    except Exception:
        return pd.Series(dtype=float)


# ── Styling ────────────────────────────────────────────────────────────────────
JPSI_BLUE = "#0693e3"
JPSI_DARK = "#32373c"
GREEN = "#27ae60"
RED = "#c0392b"
ORANGE = "#e8833a"
GOLD = "#f1c40f"


def inject_css() -> None:
    st.markdown("""
    <style>
    [data-testid="stAppViewContainer"] { background:#f8f9fa; }
    [data-testid="stMainBlockContainer"] { padding-top:1.2rem; }
    h1 { font-size:1.3rem !important; font-weight:700; }
    h5 { font-size:.88rem !important; font-weight:600; color:#495057; margin-bottom:.3rem; }
    .block-container { max-width:1360px; }
    </style>
    """, unsafe_allow_html=True)


def col_style(val: float) -> str:
    """Cell background for a margin value in a styled DataFrame."""
    if pd.isna(val):
        return ""
    if val >= 0:
        intensity = min(abs(val) / 1.5, 1.0)
        r = int(255 - intensity * 140); g = int(220 + intensity * 15); b = int(255 - intensity * 140)
    else:
        intensity = min(abs(val) / 1.5, 1.0)
        r = int(220 + intensity * 35); g = int(255 - intensity * 160); b = int(255 - intensity * 160)
    return f"background-color:rgb({r},{g},{b});color:#222;"


def plotly_base(height: int = 400) -> go.Figure:
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


def plotly_cfg() -> dict:
    return {"displayModeBar": False}


def add_time_range_controls(fig: go.Figure) -> go.Figure:
    """Zoom presets (3m/6m/YTD/1y/3y/5y/All) + a drag-to-zoom slider on a time-series
    x-axis, so a chart with years of history doesn't have to render all of it at once."""
    fig.update_xaxes(
        type="date",
        rangeselector=dict(
            buttons=[
                dict(count=3, label="3m", step="month", stepmode="backward"),
                dict(count=6, label="6m", step="month", stepmode="backward"),
                dict(count=1, label="YTD", step="year", stepmode="todate"),
                dict(count=1, label="1y", step="year", stepmode="backward"),
                dict(count=3, label="3y", step="year", stepmode="backward"),
                dict(count=5, label="5y", step="year", stepmode="backward"),
                dict(step="all", label="All"),
            ],
            bgcolor="#f1f3f5", activecolor="#0693e3",
            font=dict(size=11), y=1.15,
        ),
        rangeslider=dict(visible=True, thickness=0.07, bgcolor="#f8f9fa"),
    )
    fig.update_layout(margin=dict(t=60))
    return fig


# ── Month-code helpers ────────────────────────────────────────────────────────
_MONTH_NAMES = {
    "F": "Jan", "G": "Feb", "H": "Mar", "J": "Apr", "K": "May", "M": "Jun",
    "N": "Jul", "Q": "Aug", "U": "Sep", "V": "Oct", "X": "Nov", "Z": "Dec",
}


def friendly_ticker(ticker: str, code: str) -> str:
    t = ticker[len(code):]
    if len(t) >= 2:
        mc, yr = t[0], t[1:]
        return f"{_MONTH_NAMES.get(mc, mc)}'{yr}"
    return ticker
