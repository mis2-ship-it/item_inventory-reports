import os
import json
import glob
import pandas as pd
from datetime import datetime, timedelta

DATA_DIR = "historical_data"
OUT_FILE = os.path.join(DATA_DIR, "dashboard_cube.json")

def resolve_source_group(row):
    tags = str(row.get("tags", "") or "").lower()
    if "toing" in tags:
        return "Toing"
    sg = str(row.get("Source Group", "") or "").strip()
    if sg and sg != "Others":
        return sg
    ch = str(row.get("channel", "") or "").lower()
    if not ch:
        return "Others"
    if "swiggy" in ch:
        return "Swiggy"
    if "zomato" in ch:
        return "Zomato"
    if any(k in ch for k in ["in-store", "instore", "pick-up", "pickup"]):
        return "In Store"
    if "ownly" in ch:
        return "Ownly"
    if "magicpin" in ch:
        return "Magicpin"
    if "website" in ch or "delivery" in ch:
        return "Website"
    if "hogr" in ch:
        return "HOGR"
    return "Others"

def main():
    csv_files = sorted(glob.glob(os.path.join(DATA_DIR, "daily_item_level_history_*.csv")))
    if not csv_files:
        print("No historical CSV files found.")
        return

    time_frames = []
    prod_frames = []

    use_cols = [
        "Business Date", "Ownership", "Region", "brandName", "invoiceNumber",
        "sessionLabel", "channel", "Source Group", "tags", "status",
        "item_longName", "item_categoryName", "item_quantity",
        "item_grossAmount", "item_netDiscountAmount", "item_netAmount",
        "chargeAmount", "item_itemCost"
    ]

    for fp in csv_files:
        print(f"Processing {os.path.basename(fp)}...")
        df = pd.read_csv(fp, usecols=lambda c: c in use_cols, low_memory=False)
        if df.empty:
            continue

        if "status" in df.columns:
            st = df["status"].fillna("").astype(str).str.strip().str.lower()
            df = df[~st.isin(["cancelled", "void", "voided"])]

        df["d"] = df["Business Date"].astype(str).str.strip().str.slice(0, 10)
        df = df[df["d"].str.len() == 10]

        df["b"] = df.get("brandName", "Frozen Bottle").fillna("Frozen Bottle").astype(str).str.strip()
        df["st"] = df.get("Ownership", "COCO").fillna("COCO").astype(str).str.strip()
        df["r"] = df.get("Region", "Unknown").fillna("Unknown").astype(str).str.strip()
        df["ses"] = df.get("sessionLabel", "All Day").fillna("All Day").astype(str).str.strip()
        df["src"] = df.apply(resolve_source_group, axis=1)
        df["cat"] = df.get("item_categoryName", "Uncategorized").fillna("Uncategorized").astype(str).str.strip()
        df["itm"] = df.get("item_longName", "Unknown Item").fillna("Unknown Item").astype(str).str.strip()
        df["inv"] = df.get("invoiceNumber", "").fillna("").astype(str).str.strip()

        for col in ["item_quantity", "item_grossAmount", "item_netDiscountAmount", "item_netAmount", "chargeAmount", "item_itemCost"]:
            df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0.0)

        df["g"] = df["item_grossAmount"]
        df["dsc"] = df["item_netDiscountAmount"].abs()
        df["n"] = df["item_netAmount"] + df["chargeAmount"]  # Exact Net Sales formula
        df["fc"] = df["item_itemCost"]
        df["q"] = df["item_quantity"]

        # Aggregate Time Cube for this month
        t_grp = df.groupby(["d", "b", "st", "r", "src", "ses"], as_index=False).agg(
            g=("g", "sum"),
            n=("n", "sum"),
            dsc=("dsc", "sum"),
            fc=("fc", "sum"),
            q=("q", "sum"),
            ord=("inv", "nunique")
        )
        time_frames.append(t_grp)

        # Aggregate Daily Product Cube for this month
        p_grp = df.groupby(["d", "b", "st", "r", "src", "cat", "itm"], as_index=False).agg(
            g=("g", "sum"),
            n=("n", "sum"),
            dsc=("dsc", "sum"),
            fc=("fc", "sum"),
            q=("q", "sum"),
            ord=("inv", "nunique")
        )
        prod_frames.append(p_grp)

    full_time = pd.concat(time_frames, ignore_index=True)
    full_prod = pd.concat(prod_frames, ignore_index=True)

    max_date_str = full_time["d"].max()
    max_dt = datetime.strptime(max_date_str, "%Y-%m-%d")
    cutoff_str = (max_dt - timedelta(days=21)).strftime("%Y-%m-%d")

    def bucket_period(d_str):
        if d_str >= cutoff_str:
            return d_str
        dt = datetime.strptime(d_str, "%Y-%m-%d")
        monday = dt - timedelta(days=dt.weekday())
        return "Wk " + monday.strftime("%Y-%m-%d")

    full_prod["p"] = full_prod["d"].apply(bucket_period)
    full_prod["m"] = full_prod["d"].str.slice(0, 7)

    merged_prod = full_prod.groupby(["p", "m", "b", "st", "r", "src", "cat", "itm"], as_index=False).agg(
        g=("g", "sum"),
        n=("n", "sum"),
        dsc=("dsc", "sum"),
        fc=("fc", "sum"),
        q=("q", "sum"),
        ord=("ord", "sum")
    )

    for col in ["g", "n", "dsc", "fc", "q"]:
        full_time[col] = full_time[col].round(2)
        merged_prod[col] = merged_prod[col].round(2)

    session_order = ["Breakfast", "Lunch", "Snacks", "Dinner", "Post Dinner (Closing / Late Night)", "Late Night", "All Day"]
    sessions = sorted(
        full_time["ses"].unique().tolist(),
        key=lambda x: (session_order.index(x) if x in session_order else 999, x)
    )

    apr_rows = full_time[full_time["d"].str.startswith("2026-04")]
    apr_net = round(apr_rows["n"].sum())
    apr_min = apr_rows["d"].min() if not apr_rows.empty else ""
    apr_max = apr_rows["d"].max() if not apr_rows.empty else ""

    payload = {
        "records": full_time.to_dict(orient="records"),
        "products": merged_prod.to_dict(orient="records"),
        "filters": {
            "brands": sorted(full_time["b"].unique().tolist()),
            "storeTypes": sorted(full_time["st"].unique().tolist()),
            "regions": sorted(full_time["r"].unique().tolist()),
            "sources": sorted(full_time["src"].unique().tolist()),
            "sessions": sessions,
            "months": sorted(full_time["d"].str.slice(0, 7).unique().tolist(), reverse=True)
        },
        "lastUpdated": f"Apr: {apr_min}..{apr_max} (Net ₹{apr_net:,})"
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))

    size_kb = os.path.getsize(OUT_FILE) / 1024
    print(f"Saved {OUT_FILE} ({size_kb:.1f} KB) | {payload['lastUpdated']}")

if __name__ == "__main__":
    main()
