"""Ethanol grind (crush) margins from USDA AMS cash prices.

One bushel of corn yields roughly 2.8 gallons of ethanol plus co-products, so a plant's
gross margin is what those co-products fetch less what the corn costs:

    revenue = gal_per_bu x ethanol($/gal)
            + ddg_lb_per_bu/2000 x distillers grain($/ton)
            + oil_lb_per_bu x distillers corn oil($/lb)
    margin  = revenue - corn($/bu) - operating costs($/bu)

Prices come from USDA AMS Market News (marsapi.ams.usda.gov), which publishes the cash
market the plants actually trade rather than a futures proxy:

* **National Weekly Ethanol Report** (slug 3616) — ethanol $/gal, distillers grain $/ton
  and distillers corn oil c/lb, by state, weekly.
* **National Daily Ethanol Report** (slug 3617) — corn bids at ethanol plants, $/bu and
  as basis to the futures month, by state, daily.

CME's Chicago Ethanol (Platts) future (CU) exists on Massive but trades too thinly to
price a forward grind — most months have no settlements at all — so the board strip is
offered only as a sanity check with its last trade date shown, not as the main number.

The API is slow and rate-limited: a single multi-year request can hang for the better
part of an hour. Requests are therefore chunked, and everything is cached in committed
snapshots (data/ams_ethanol_weekly.csv, data/ams_plant_corn.csv). Refresh with
`python ethanol_grind.py`, which extends the snapshots with whatever is missing.
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests


def _key(name: str) -> str:
    """API key from Streamlit secrets, else the environment. Never hardcoded: this repo
    is public."""
    try:
        import streamlit as st

        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return value or os.environ.get(name, "")


BASE_URL = "https://marsapi.ams.usda.gov/services/v1.2/reports"
WEEKLY_SLUG = 3616
DAILY_SLUG = 3617

DATA_DIR = Path(__file__).parent / "data"
WEEKLY_PATH = DATA_DIR / "ams_ethanol_weekly.csv"
DAILY_PATH = DATA_DIR / "ams_plant_corn.csv"

# Default plant yields per bushel of corn. Editable in the app — a dry mill with oil
# extraction runs near these; they are the standard industry rule-of-thumb figures.
DEFAULT_GAL_PER_BU = 2.85
DEFAULT_DDG_LB_PER_BU = 15.5   # dried distillers grains, after oil extraction
DEFAULT_OIL_LB_PER_BU = 0.75   # distillers corn oil
DEFAULT_GAS_MMBTU_PER_GAL = 0.024  # thermal energy per gallon; a dry mill runs ~23-25k BTU
DEFAULT_OPEX_PER_BU = 0.0      # power, enzymes, labour, denaturant — user's own number

# Natural gas comes from EIA rather than the futures feed: Massive carries no NG contracts
# under this entitlement. Henry Hub daily spot, $/MMBtu, public domain. Both this and the
# AMS key are read from secrets (USDA_MARS_API_KEY, EIA_API_KEY).
EIA_URL = "https://api.eia.gov/v2/natural-gas/pri/fut/data/"
HENRY_HUB_SERIES = "RNGWHHD"

DDG_VARIETIES = ("Dried 10%", "Modified Wet 55-60%", "Wet 65-70%")
_LAST_SOURCE = "none"


def source() -> str:
    """'ams', 'snapshot', 'snapshot+ams' or 'none' — what served the last load."""
    return _LAST_SOURCE


def _get(slug: int, begin: date, end: date, timeout: float) -> list[dict]:
    resp = requests.get(
        f"{BASE_URL}/{slug}/Report%20Detail",
        auth=(_key("USDA_MARS_API_KEY"), ""),
        params={"q": f"report_begin_date={begin:%m/%d/%Y}:{end:%m/%d/%Y}"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("results", []) or []


def _rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["date", "commodity", "state", "variety", "trans_mode",
                                     "price", "price_min", "price_max", "unit"])
    frame = pd.DataFrame([{
        "date": pd.to_datetime(r.get("report_date")).date(),
        "commodity": r.get("commodity"),
        "state": r.get("state/Province"),
        "variety": r.get("variety") if r.get("variety") not in (None, "N/A") else "",
        "trans_mode": r.get("trans_mode"),
        "price": r.get("avg_price"),
        "price_min": r.get("price Min"),
        "price_max": r.get("price Max"),
        "unit": r.get("price_unit"),
    } for r in rows])
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    return frame.dropna(subset=["price"]).sort_values("date")


def fetch(slug: int, begin: date, end: date, chunk_days: int = 120,
          timeout: float = 90.0, pause: float = 1.0, attempts: int = 2) -> pd.DataFrame:
    """Report rows over [begin, end], requested in chunks so one slow window can't
    stall the whole range. Chunks that keep failing are skipped, not raised."""
    frames, cursor = [], begin
    while cursor <= end:
        stop = min(cursor + timedelta(days=chunk_days), end)
        for attempt in range(attempts):
            try:
                frames.append(_rows_to_frame(_get(slug, cursor, stop, timeout)))
                break
            except Exception:
                if attempt + 1 < attempts:
                    time.sleep(pause * 3)
        cursor = stop + timedelta(days=1)
        time.sleep(pause)
    if not frames:
        return _rows_to_frame([])
    return pd.concat(frames, ignore_index=True).drop_duplicates()


def _read_snapshot(path: Path) -> pd.DataFrame:
    if not path.exists():
        return _rows_to_frame([])
    frame = pd.read_csv(path)
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    frame["variety"] = frame["variety"].fillna("")
    return frame


def load_weekly(live_days: int = 45, timeout: float = 45.0) -> pd.DataFrame:
    """Weekly ethanol / distillers grain / corn oil prices: the committed snapshot,
    topped up with the most recent weeks from AMS when it answers in time."""
    global _LAST_SOURCE
    snapshot = _read_snapshot(WEEKLY_PATH)
    _LAST_SOURCE = "snapshot" if len(snapshot) else "none"
    try:
        recent = fetch(WEEKLY_SLUG, date.today() - timedelta(days=live_days), date.today(),
                       chunk_days=live_days, timeout=timeout, attempts=1)
    except Exception:
        recent = _rows_to_frame([])
    if not len(recent):
        return snapshot
    _LAST_SOURCE = "snapshot+ams" if len(snapshot) else "ams"
    merged = pd.concat([snapshot, recent], ignore_index=True)
    return merged.drop_duplicates(
        subset=["date", "commodity", "state", "variety", "trans_mode"], keep="last"
    ).sort_values("date")


def load_plant_corn(live_days: int = 10, timeout: float = 45.0) -> pd.DataFrame:
    """Daily corn bids at ethanol plants, snapshot plus the last few sessions."""
    snapshot = _read_snapshot(DAILY_PATH)
    try:
        recent = fetch(DAILY_SLUG, date.today() - timedelta(days=live_days), date.today(),
                       chunk_days=live_days, timeout=timeout, attempts=1)
    except Exception:
        recent = _rows_to_frame([])
    if not len(recent):
        return snapshot
    merged = pd.concat([snapshot, recent], ignore_index=True)
    return merged.drop_duplicates(
        subset=["date", "commodity", "state", "variety", "trans_mode"], keep="last"
    ).sort_values("date")


def states(weekly: pd.DataFrame) -> list[str]:
    """States with an ethanol quote, most recent first by coverage."""
    eth = weekly[(weekly["commodity"] == "Ethanol") & (weekly["state"] != "N/A")]
    return sorted(eth["state"].dropna().unique())


def series(frame: pd.DataFrame, commodity: str, state: str | None = None,
           variety: str | None = None) -> pd.Series:
    """Weekly average price for one commodity, averaged across quotes in the state."""
    rows = frame[frame["commodity"] == commodity]
    if state and state != "All states":
        in_state = rows[rows["state"] == state]
        rows = in_state if len(in_state) else rows[rows["state"] == "N/A"]
    if variety:
        rows = rows[rows["variety"] == variety]
    if not len(rows):
        return pd.Series(dtype=float)
    return rows.groupby("date")["price"].mean().sort_index()


def henry_hub(start: date = date(2022, 1, 1), timeout: float = 30.0) -> pd.Series:
    """Henry Hub natural gas spot, $/MMBtu by date. Empty if EIA is unreachable."""
    try:
        resp = requests.get(EIA_URL, timeout=timeout, params={
            "api_key": _key("EIA_API_KEY"), "frequency": "daily", "data[0]": "value",
            "facets[series][]": HENRY_HUB_SERIES, "start": start.isoformat(),
            "sort[0][column]": "period", "sort[0][direction]": "desc", "length": 5000,
        })
        resp.raise_for_status()
        rows = resp.json()["response"]["data"]
    except Exception:
        return pd.Series(dtype=float)
    series = pd.Series(
        {pd.to_datetime(r["period"]).date(): float(r["value"]) for r in rows if r.get("value")}
    )
    return series.sort_index()


def margin_frame(weekly: pd.DataFrame, corn: pd.Series, state: str, ddg_variety: str,
                 gal_per_bu: float, ddg_lb_per_bu: float, oil_lb_per_bu: float,
                 opex_per_bu: float, gas_price=None,
                 gas_mmbtu_per_gal: float = DEFAULT_GAS_MMBTU_PER_GAL) -> pd.DataFrame:
    """Per-bushel revenue, cost and margin on every week both sides are quoted.

    `corn` is $/bu indexed by date — plant bids or futures — and is carried forward to
    each weekly report date."""
    ethanol = series(weekly, "Ethanol", state)
    ddg = series(weekly, "Distillers Grain", state, ddg_variety)
    oil = series(weekly, "Distillers Corn Oil", state)
    if not len(ethanol):
        return pd.DataFrame()

    frame = pd.DataFrame({"ethanol": ethanol, "ddg": ddg, "oil": oil})
    frame = frame.sort_index()
    frame[["ddg", "oil"]] = frame[["ddg", "oil"]].ffill()
    if corn is not None and len(corn):
        aligned = corn.reindex(sorted(set(corn.index) | set(frame.index))).ffill()
        frame["corn"] = aligned.reindex(frame.index)
    else:
        frame["corn"] = float("nan")

    frame["ethanol_rev"] = frame["ethanol"] * gal_per_bu
    frame["ddg_rev"] = frame["ddg"] * ddg_lb_per_bu / 2000
    frame["oil_rev"] = frame["oil"] / 100 * oil_lb_per_bu   # quoted in cents per pound
    frame["revenue"] = frame[["ethanol_rev", "ddg_rev", "oil_rev"]].sum(axis=1, min_count=1)
    if isinstance(gas_price, pd.Series) and len(gas_price):
        gas_aligned = gas_price.reindex(sorted(set(gas_price.index) | set(frame.index))).ffill()
        frame["gas"] = gas_aligned.reindex(frame.index)
    else:
        frame["gas"] = float(gas_price) if gas_price is not None else float("nan")
    frame["gas_cost"] = frame["gas"] * gas_mmbtu_per_gal * gal_per_bu
    frame["opex"] = opex_per_bu
    frame["margin"] = (frame["revenue"] - frame["corn"]
                       - frame["gas_cost"].fillna(0) - opex_per_bu)
    frame["margin_per_gal"] = frame["margin"] / gal_per_bu if gal_per_bu else float("nan")
    return frame.dropna(subset=["margin"])


def refresh_snapshots(start: date = date(2022, 1, 1)) -> tuple[int, int]:
    """Extend the committed snapshots up to today. Returns (weekly rows, daily rows)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for slug, path, chunk in ((WEEKLY_SLUG, WEEKLY_PATH, 120), (DAILY_SLUG, DAILY_PATH, 60)):
        existing = _read_snapshot(path)
        begin = max(existing["date"]) + timedelta(days=1) if len(existing) else start
        fresh = fetch(slug, begin, date.today(), chunk_days=chunk, timeout=180, attempts=3)
        merged = pd.concat([existing, fresh], ignore_index=True) if len(existing) else fresh
        merged = merged.drop_duplicates(
            subset=["date", "commodity", "state", "variety", "trans_mode"], keep="last"
        ).sort_values(["date", "commodity", "state"])
        merged.to_csv(path, index=False)
        out.append(len(merged))
    return tuple(out)


if __name__ == "__main__":
    weekly_rows, daily_rows = refresh_snapshots()
    print(f"weekly: {weekly_rows:,} rows -> {WEEKLY_PATH}")
    print(f"daily:  {daily_rows:,} rows -> {DAILY_PATH}")
