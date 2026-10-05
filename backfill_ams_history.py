"""Backfill data/ams_ethanol_weekly.csv and data/ams_plant_corn.csv further into the past.

`ethanol_grind.refresh_snapshots()` only extends *forward* from the snapshot's current
max date — it can't fill in older history before the earliest row already on disk. This
script fetches the gap between a given start date and the snapshot's current earliest
date, and prepends it. AMS's API is slow and occasionally times out on a request; it's
retried with a long timeout rather than treated as "no data" (a plain read timeout and a
genuine empty result look identical unless you check the response, so treat timeouts as
transient, not as an "AMS has nothing here" signal).

Run: python backfill_ams_history.py [start_year]
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

import pandas as pd

import ethanol_grind

DEFAULT_START_YEAR = 2007


def backfill(start: date) -> None:
    for slug, path, chunk in (
        (ethanol_grind.WEEKLY_SLUG, ethanol_grind.WEEKLY_PATH, 120),
        (ethanol_grind.DAILY_SLUG, ethanol_grind.DAILY_PATH, 60),
    ):
        existing = ethanol_grind._read_snapshot(path)
        gap_end = (min(existing["date"]) - timedelta(days=1)) if len(existing) else date.today()
        if start > gap_end:
            print(f"{path.name}: nothing to backfill (start {start} is after existing data).")
            continue
        print(f"{path.name}: backfilling {start} -> {gap_end} ...")
        older = ethanol_grind.fetch(slug, start, gap_end, chunk_days=chunk,
                                    timeout=180, pause=1.5, attempts=4)
        print(f"{path.name}: fetched {len(older):,} older rows"
              + (f" ({older['date'].min()} -> {older['date'].max()})" if len(older) else ""))
        merged = pd.concat([older, existing], ignore_index=True) if len(existing) else older
        merged = merged.drop_duplicates(
            subset=["date", "commodity", "state", "variety", "trans_mode"], keep="last"
        ).sort_values(["date", "commodity", "state"])
        merged.to_csv(path, index=False)
        print(f"{path.name}: now {len(merged):,} rows, {merged['date'].min()} -> {merged['date'].max()}")


if __name__ == "__main__":
    year = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_START_YEAR
    backfill(date(year, 1, 1))
