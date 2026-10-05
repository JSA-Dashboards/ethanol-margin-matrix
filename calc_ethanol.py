"""Pure Ethanol margin calculations — shared by the Ethanol page and the dashboard summary.

No Streamlit calls here: this module is imported by both a page script (which reruns on
every interaction) and the summary page, so it stays side-effect-free and cheap to import.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import ethanol_grind

GAL_PER_BU = ethanol_grind.DEFAULT_GAL_PER_BU          # 2.85
DDG_LB_PER_BU = ethanol_grind.DEFAULT_DDG_LB_PER_BU     # 15.5
OIL_LB_PER_BU = ethanol_grind.DEFAULT_OIL_LB_PER_BU     # 0.75
GAS_MMBTU_PER_GAL = ethanol_grind.DEFAULT_GAS_MMBTU_PER_GAL  # 0.024
DDG_VARIETY = "Dried 10%"  # AMS variety used for the DDG/corn ratio — matches DDG_LB_PER_BU's yield basis

# Location configuration — from PDF footnote
LOCATIONS = {
    "Columbus CSX": {
        "corn_basis": 0.17,   # +17¢ over ZC Dec
        "eth_adj": 0.00,      # @ Chicago flat
        "region": "Eastern corn belt",
    },
    "NE Grp 3": {
        "corn_basis": 0.37,   # +37¢ over ZC Dec
        "eth_adj": -0.12,     # @ Chicago − 12¢
        "region": "Western corn belt",
    },
    "Minnesota": {
        "corn_basis": 0.50,   # +50¢ over ZC Dec
        "eth_adj": -0.12,     # @ Chicago − 12¢
        "region": "Western corn belt",
    },
}


def report_margin(eth_gal: float, corn_bu: float, ddgs_pct: float,
                   gas_price: float, gal: float, ddg_lb: float, oil_lb: float,
                   oil_price_cents_lb: float, gas_use: float,
                   unit: str = "¢/gal") -> dict:
    """Gross margin for one location. Returns dict of revenue/cost/margin components."""
    eth_rev_bu = eth_gal * gal
    corn_ton = corn_bu * (2000 / 56)       # $/ton
    ddgs_ton = corn_ton * ddgs_pct
    ddg_rev_bu = ddgs_ton * ddg_lb / 2000
    oil_rev_bu = oil_price_cents_lb / 100 * oil_lb
    gas_cost_bu = gas_price * gas_use * gal
    revenue_bu = eth_rev_bu + ddg_rev_bu + oil_rev_bu
    cost_bu = corn_bu + gas_cost_bu
    margin_bu = revenue_bu - cost_bu

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


def build_matrix(
    eth_prices: np.ndarray, corn_prices: np.ndarray,
    ddg_rev_per_bu: float, oil_rev_per_bu: float,
    gas_cost_per_bu: float, opex_per_bu: float,
    gal_per_bu: float, unit: str = "$/bu",
) -> pd.DataFrame:
    eth_col = eth_prices[:, None] * gal_per_bu + ddg_rev_per_bu + oil_rev_per_bu
    margin = eth_col - corn_prices[None, :] - gas_cost_per_bu - opex_per_bu
    if unit == "$/gal":
        margin = margin / gal_per_bu if gal_per_bu else margin
    return pd.DataFrame(
        margin,
        index=[f"{p:.2f}" for p in eth_prices],
        columns=[f"{p:.2f}" for p in corn_prices],
        dtype=float,
    )


def historical_margins(
    weekly: pd.DataFrame, zc_archive: pd.Series, *,
    b_col: dict, eth_adj: dict, ddg_lb: float, oil_lb: float,
    gas_basis: float, gas_use: float, gal: float, unit: str = "¢/gal",
) -> dict[str, pd.Series]:
    """Per-location historical gross margin series (AMS weekly ethanol/DDG/oil + ZC Dec archive).

    DDG and corn oil use the real AMS cash print at each historical date — unlike the live
    snapshot, there's no gap to bridge with a ratio projection since every plotted point
    already lands on an actual AMS report date. Gas uses a fixed $3.00/MMBtu proxy since
    live futures history isn't archived.
    """
    if not len(weekly) or not len(zc_archive):
        return {}

    eth_il = ethanol_grind.series(weekly, "Ethanol", "Illinois")
    eth_ia = ethanol_grind.series(weekly, "Ethanol", "Iowa")
    oil_s = ethanol_grind.series(weekly, "Distillers Corn Oil")
    ddg_s = ethanol_grind.series(weekly, "Distillers Grain", variety=DDG_VARIETY)
    zc_daily = zc_archive.copy()
    zc_daily.index = pd.to_datetime(zc_daily.index)
    zc_daily = zc_daily.sort_index()

    out: dict[str, pd.Series] = {}
    for loc in LOCATIONS:
        eth_base = eth_ia if ("NE" in loc or "Minnesota" in loc) else eth_il
        eth_loc_s = eth_base.copy()
        eth_loc_s.index = pd.to_datetime(eth_loc_s.index)
        eth_loc_s = eth_loc_s + eth_adj[loc]
        oil_ts = oil_s.copy()
        oil_ts.index = pd.to_datetime(oil_ts.index)
        ddg_ts = ddg_s.copy()
        ddg_ts.index = pd.to_datetime(ddg_ts.index)

        # AMS reports on its own weekday (Monday); align corn to each report date by
        # carrying forward the most recent prior settlement rather than assuming a fixed
        # weekly resample boundary that may not land on the same day.
        report_dates = eth_loc_s.index
        zc_on_reports = zc_daily.reindex(zc_daily.index.union(report_dates)).ffill().reindex(report_dates)

        combined = pd.DataFrame({"eth": eth_loc_s, "zc": zc_on_reports, "oil": oil_ts, "ddg": ddg_ts}).dropna()
        if combined.empty:
            continue
        corn_s = combined["zc"] + b_col[loc]
        ddgs_s = combined["ddg"] * ddg_lb / 2000
        oil_s2 = combined["oil"] / 100 * oil_lb
        gas_s = 3.0 + gas_basis  # rough proxy for historical
        gas_cost_s = gas_s * gas_use * gal
        eth_rev_s = combined["eth"] * gal
        margin_s = eth_rev_s + ddgs_s + oil_s2 - corn_s - gas_cost_s
        if unit == "¢/gal":
            margin_s = margin_s / gal * 100
        elif unit == "$/gal":
            margin_s = margin_s / gal
        out[loc] = margin_s
    return out


def ratio_anchor(ams_series: pd.Series, reference_hist: pd.Series) -> dict | None:
    """Ratio of an AMS cash price to a daily-traded reference price, anchored to AMS's
    most recent report date.

    AMS commodities (distillers corn oil, distillers grain) are weekly cash prints;
    their futures proxies (soybean oil ZL, corn ZC) trade every day. Rather than a fixed
    discount or percentage, this holds the ratio AMS itself implied on its last report
    date, so a live reference price projects a same-day AMS-equivalent estimate between
    AMS prints without re-guessing the spread. Used for both corn oil (vs. ZL) and DDG
    (vs. ZC, in $/ton) — same method, different inputs.
    """
    if not len(ams_series) or not len(reference_hist):
        return None
    ams_date = ams_series.index[-1]
    ams_price = float(ams_series.iloc[-1])
    prior = reference_hist[reference_hist.index <= ams_date]
    if not len(prior):
        return None
    ref_date = prior.index[-1]
    ref_price = float(prior.iloc[-1])
    if not ref_price:
        return None
    return {
        "ams_date": ams_date,
        "ams_price": ams_price,
        "ref_date": ref_date,
        "ref_price": ref_price,
        "ratio": ams_price / ref_price,
    }


def forward_margin_curve(
    cu: pd.DataFrame, zc: pd.DataFrame, ng: pd.DataFrame, zl: pd.DataFrame, *,
    b_col: dict, eth_adj: dict, ddgs_pct: float, ddg_lb: float, oil_lb: float,
    gas_use: float, gas_basis: float, oil_ratio: float, gal: float,
    unit: str = "¢/gal",
) -> dict[str, pd.DataFrame]:
    """Projected gross margin at each CU (ethanol) forward-curve expiration.

    Corn and gas stay anchored to the Report Style reference months (December ZC,
    October NG) — the same fixed-basis convention the report methodology uses for its
    single current-margin snapshot — while ethanol follows its own CU curve and corn
    oil follows the nearest ZL contract to each expiration, scaled by the ratio AMS's
    last report implied (held constant, same as the live snapshot).
    """
    if cu.empty:
        return {}
    zc_dec = zc[zc["ticker"].str.contains(r"ZCZ\d", regex=True)] if not zc.empty else pd.DataFrame()
    corn_zc_raw = float(zc_dec.iloc[0]["price"]) / 100.0 if not zc_dec.empty else None
    ng_oct = ng[ng["ticker"].str.contains("V")] if not ng.empty else pd.DataFrame()
    if ng_oct.empty and not ng.empty:
        ng_oct = ng
    ng_price_raw = float(ng_oct.iloc[0]["price"]) if not ng_oct.empty else None
    gas_price = (ng_price_raw + gas_basis) if ng_price_raw else None
    if corn_zc_raw is None or gas_price is None or not oil_ratio or not len(zl):
        return {}

    zl_dates = pd.to_datetime(zl["expiration"])
    out: dict[str, pd.DataFrame] = {}
    for loc in LOCATIONS:
        corn_loc = corn_zc_raw + b_col[loc]
        rows = []
        for r in cu.itertuples(index=False):
            eth_loc = float(r.price) + eth_adj[loc]
            deltas = (zl_dates - pd.Timestamp(r.expiration)).abs()
            oil_price = float(zl.iloc[deltas.values.argmin()]["price"]) * oil_ratio
            m = report_margin(eth_loc, corn_loc, ddgs_pct, gas_price, gal, ddg_lb, oil_lb,
                              oil_price, gas_use, unit)
            rows.append({"expiration": r.expiration, "margin": m["margin"]})
        out[loc] = pd.DataFrame(rows)
    return out


def default_b_col() -> dict[str, float]:
    return {loc: cfg["corn_basis"] for loc, cfg in LOCATIONS.items()}


def default_eth_adj() -> dict[str, float]:
    return {loc: cfg["eth_adj"] for loc, cfg in LOCATIONS.items()}
