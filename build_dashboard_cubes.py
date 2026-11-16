import os
import json
import pandas as pd
from datetime import datetime, timedelta

OUTPUT_DIR = "historical_data"

def get_week_start(date_str):
    d = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    monday = d - timedelta(days=d.weekday())
    return "Wk " + monday.strftime("%Y-%m-%d")

def build_cubes():
    csv_files = sorted([
        os.path.join(OUTPUT_DIR, f) for f in os.listdir(OUTPUT_DIR)
        if f.startswith("daily_item_level_history_") and f.endswith(".csv")
    ])
    if not csv_files:
        print("No historical CSV files found to build cubes.")
        return

    print(f"Building lightweight dashboard cubes from {len(csv_files)} monthly CSV files...")
    dfs = [pd.read_csv(f, low_memory=False) for f in csv_files]
    df = pd.concat(dfs, ignore_index=True)

    df = df[df["status"].astype(str).str.strip().str.lower() == "closed"].copy()
    df["Business Date"] = df["Business Date"].astype(str).str.slice(0, 10)
    df["brandName"] = df["brandName"].fillna("Frozen Bottle").astype(str).str.strip()
    df["Ownership"] = df["Ownership"].fillna("COCO").astype(str).str.strip()
    df["Region"] = df["Region"].fillna("Unknown").astype(str).str.strip()
    df["Source Group"] = df["Source Group"].fillna("Others").astype(str).str.strip()
    df["sessionLabel"] = df["sessionLabel"].fillna("All Day").astype(str).str.strip()
    df["item_categoryName"] = df["item_categoryName"].fillna("Uncategorized").astype(str).str.strip()
    df["item_longName"] = df["item_longName"].fillna("Unknown Item").astype(str).str.strip()

    for col in ["item_grossAmount", "item_netAmount", "chargeAmount", "item_netDiscountAmount", "item_itemCost", "item_quantity"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df["item_netDiscountAmount"] = df["item_netDiscountAmount"].abs()
    df["total_net"] = df["item_netAmount"] + df["chargeAmount"]

    # 1. Time-Series Cube (Daily grain across dimensions)
    t_grp = df.groupby(
        ["Business Date", "brandName", "Ownership", "Region", "Source Group", "sessionLabel"],
        as_index=False
    ).agg(
        g=("item_grossAmount", "sum"),
        n=("total_net", "sum"),
        dsc=("item_netDiscountAmount", "sum"),
        fc=("item_itemCost", "sum"),
        q=("item_quantity", "sum"),
        ord=("invoiceNumber", "nunique")
    )
    t_grp.rename(columns={
        "Business Date": "d", "brandName": "b", "Ownership": "st",
        "Region": "r", "Source Group": "src", "sessionLabel": "ses"
    }, inplace=True)
    for c in ["g", "n", "dsc", "fc", "q"]:
        t_grp[c] = t_grp[c].round(2)

    # 2. Product Cube:
    # - Daily grain for the last 21 days (supports Daily DoD comparisons)
    # - Weekly grain for older dates (supports Weekly WoW & Monthly MoM comparisons without bloat)
    max_date = df["Business Date"].max()
    cutoff_date = (datetime.strptime(max_date, "%Y-%m-%d").date() - timedelta(days=21)).strftime("%Y-%m-%d")

    df["period_bucket"] = df["Business Date"].apply(
        lambda x: x if x >= cutoff_date else get_week_start(x)
    )
    df["month_bucket"] = df["Business Date"].str.slice(0, 7)

    p_grp = df.groupby(
        ["period_bucket", "month_bucket", "brandName", "Ownership", "Region", "Source Group", "item_categoryName", "item_longName"],
        as_index=False
    ).agg(
        g=("item_grossAmount", "sum"),
        n=("item_netAmount", "sum"),
        dsc=("item_netDiscountAmount", "sum"),
        fc=("item_itemCost", "sum"),
        q=("item_quantity", "sum"),
        ord=("invoiceNumber", "nunique")
    )
    p_grp.rename(columns={
        "period_bucket": "p", "month_bucket": "m", "brandName": "b", "Ownership": "st",
        "Region": "r", "Source Group": "src", "item_categoryName": "cat", "item_longName": "itm"
    }, inplace=True)
    for c in ["g", "n", "dsc", "fc", "q"]:
        p_grp[c] = p_grp[c].round(2)

    session_order = ["Breakfast", "Lunch", "Snacks", "Dinner", "Post Dinner (Closing / Late Night)", "Late Night", "All Day"]
    sessions = sorted(
        t_grp["ses"].unique().tolist(),
        key=lambda s: (session_order.index(s) if s in session_order else 99, s)
    )

    filters = {
        "brands": sorted(t_grp["b"].unique().tolist()),
        "storeTypes": sorted(t_grp["st"].unique().tolist()),
        "regions": sorted(t_grp["r"].unique().tolist()),
        "sources": sorted(t_grp["src"].unique().tolist()),
        "sessions": sessions,
        "months": sorted(df["month_bucket"].unique().tolist(), reverse=True)
    }

    time_payload = {
        "records": t_grp.to_dict(orient="records"),
        "filters": filters,
        "built_at_ist": (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%d %b %Y, %I:%M %p")
    }
    prod_payload = {
        "products": p_grp.to_dict(orient="records")
    }

    time_path = os.path.join(OUTPUT_DIR, "dashboard_time_cube.json")
    prod_path = os.path.join(OUTPUT_DIR, "dashboard_product_cube.json")

    with open(time_path, "w") as f:
        json.dump(time_payload, f, separators=(",", ":"))
    with open(prod_path, "w") as f:
        json.dump(prod_payload, f, separators=(",", ":"))

    print(f"✅ Built {time_path} ({os.path.getsize(time_path)/1024:.1f} KB) & {prod_path} ({os.path.getsize(prod_path)/1024:.1f} KB)")

if __name__ == "__main__":
    build_cubes()
