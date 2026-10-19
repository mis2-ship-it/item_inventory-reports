import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
HISTORY_DIR = "historical_data"
OUT_DIR = os.path.join("dashboard_data", "history")
INDEX_PATH = os.path.join("dashboard_data", "history_index.json")

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)

    # Remove old weekly partitions so deleted/empty source months do not leave stale data.
    for name in os.listdir(OUT_DIR):
        if name.endswith(".csv") and name.startswith("item_level_week_"):
            os.remove(os.path.join(OUT_DIR, name))

    files = []
    brands, regions, categories = set(), set(), set()
    monthly_frames = []

    # Read every monthly archive first. Several months can contribute records
    # to the same calendar week, so write each weekly partition only once.
    for name in sorted(os.listdir(HISTORY_DIR)):
        if not (name.startswith("item_level_") and name.endswith(".csv")):
            continue
        path = os.path.join(HISTORY_DIR, name)
        if os.path.getsize(path) <= 1:
            print(f"Skipping empty history file: {name}")
            continue
        try:
            df = pd.read_csv(path, low_memory=False)
        except pd.errors.EmptyDataError:
            print(f"Skipping empty history file: {name}")
            continue
        if df.empty or "Business Date" not in df.columns:
            print(f"Skipping file without usable rows/date: {name}")
            continue

        for col, target in [("brandName", brands), ("Region", regions), ("Category Group", categories)]:
            if col in df.columns:
                target.update(x for x in df[col].dropna().astype(str).str.strip().unique() if x)

        df["Business Date"] = pd.to_datetime(df["Business Date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df = df[df["Business Date"].notna()].copy()
        if not df.empty:
            monthly_frames.append(df)
            print(f"Loaded {name}: {len(df)} usable rows")

    if monthly_frames:
        all_history = pd.concat(monthly_frames, ignore_index=True, sort=False)

        # If a stable item-line identifier exists, use it to remove exact archive
        # overlap. Otherwise preserve rows rather than risk collapsing valid items.
        if "Item Line ID" in all_history.columns:
            all_history = all_history.drop_duplicates(subset=["Item Line ID"], keep="last")

        all_history["_week_start"] = (
            pd.to_datetime(all_history["Business Date"])
            .dt.to_period("W-SUN").dt.start_time.dt.strftime("%Y-%m-%d")
        )

        for start, part in all_history.groupby("_week_start", sort=True):
            part = part.drop(columns=["_week_start"])
            filename = f"item_level_week_{start}.csv"
            output_path = os.path.join(OUT_DIR, filename)
            part.to_csv(output_path, index=False, encoding="utf-8-sig")
            dates = pd.to_datetime(part["Business Date"], errors="coerce").dropna()
            size = os.path.getsize(output_path)
            files.append({
                "file": filename,
                "path": f"history/{filename}",
                "start_date": dates.min().strftime("%Y-%m-%d"),
                "end_date": dates.max().strftime("%Y-%m-%d"),
                "rows": int(len(part)),
                "size_bytes": int(size)
            })
            print(f"Published {filename}: {len(part)} rows, {size:,} bytes")

    index_data = {
        "updated_at": datetime.now(IST).isoformat(),
        "brands": sorted(brands),
        "regions": sorted(regions),
        "categories": sorted(categories),
        "files": sorted(files, key=lambda x: x["start_date"])
    }
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index_data, f, indent=2)
    print(f"History index saved: {INDEX_PATH}; partitions={len(files)}")

if __name__ == "__main__":
    main()
