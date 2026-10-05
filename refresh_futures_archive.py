"""Refresh data/futures_history_archive.csv with December-ZC corn settlement history.

The archive was a one-time static dump that stops at 2021-12-14 — nothing in the app
extends it. Massive keeps 1-2+ years of settlement history per contract, so a handful of
December tickers (this year back through the archive's last date) cover the whole gap.
Run `python refresh_futures_archive.py` to bring it current; safe to re-run any time.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd

import massive_api

APP_DIR = Path(__file__).parent
ARCHIVE_PATH = APP_DIR / "data" / "futures_history_archive.csv"


def _key(name: str) -> str:
    try:
        import streamlit as st
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return value or os.environ.get(name, "")


def refresh(start_year: int = 2022) -> int:
    """Fetch December ZC settlement history for start_year..next year and merge into the
    archive. Returns the number of rows in the merged archive."""
    api_key = _key("MASSIVE_API_KEY")
    if not api_key:
        raise SystemExit("MASSIVE_API_KEY not found in secrets.toml or the environment.")

    existing = pd.read_csv(ARCHIVE_PATH, parse_dates=["date"]) if ARCHIVE_PATH.exists() else pd.DataFrame(
        columns=["product_code", "month", "year", "date", "price"])

    end_year = date.today().year + 1
    frames = [existing]
    for year in range(start_year, end_year + 1):
        ticker = f"ZCZ{year % 10}"
        hist = massive_api.get_settlement_history(ticker, api_key)
        if not len(hist):
            continue
        frames.append(pd.DataFrame({
            "product_code": "ZC",
            "month": "Z",
            "year": year,
            "date": pd.to_datetime(list(hist.index)),
            "price": hist.values,
        }))
        print(f"{ticker} ({year}): {len(hist)} rows, {hist.index.min()} to {hist.index.max()}")

    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=["product_code", "month", "year", "date"], keep="last")
    merged = merged.sort_values(["product_code", "month", "year", "date"])
    merged.to_csv(ARCHIVE_PATH, index=False)
    return len(merged)


if __name__ == "__main__":
    total = refresh()
    print(f"Archive refreshed: {total:,} rows -> {ARCHIVE_PATH}")
