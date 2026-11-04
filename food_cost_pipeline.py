# =========================================================
# FAST MULTITHREADED FOOD COST PIPELINE WITH GITHUB MONTHLY STORAGE
# =========================================================
import os
import ast
import json
import time
import requests
import jwt
import gspread
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.oauth2.service_account import Credentials

print("🚀 Fast Food Cost Pipeline Started")

# =========================================================
# CONFIG
# =========================================================
API_KEY = os.environ["API_KEY"]
SECRET_KEY = os.environ["SECRET_KEY"]

HELP_SPREADSHEET_ID = "1CVUS-BSBfDIoQI4Yk2GB4_Zp1CIJRF-9YRfpvCih-FM"
SALES_URL = "https://api.ristaapps.com/v1/sales/page"

# Start Date: September 26, 2026
START_DATE = datetime(2026, 9, 26).date()

IST = ZoneInfo("Asia/Kolkata")
now = datetime.now(IST)
business_date = now.date() - timedelta(days=1) if now.hour < 9 else now.date()

DATA_DIR = os.path.join("data", "food_cost")
os.makedirs(DATA_DIR, exist_ok=True)

# =========================================================
# AUTH
# =========================================================
def get_token():
    return jwt.encode({"iss": API_KEY, "iat": int(time.time())}, SECRET_KEY, algorithm="HS256")

def api_headers():
    return {
        "x-api-key": API_KEY,
        "x-api-token": get_token(),
        "content-type": "application/json",
    }

creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_CREDENTIALS"]),
    scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"],
)
client = gspread.authorize(creds)
help_ss = client.open_by_key(HELP_SPREADSHEET_ID)

# =========================================================
# LOAD COCO STORES & METADATA
# =========================================================
help_ws = help_ss.worksheet("Help Sheet")
help_vals = help_ws.get_all_values()

headers = [str(h).strip() for h in help_vals[0]]
raw_help_df = pd.DataFrame(help_vals[1:], columns=headers)
raw_help_df.columns = [c.strip() for c in raw_help_df.columns]

ownership_col = next((c for c in ["Ownership", "ownership"] if c in raw_help_df.columns), None)
if ownership_col:
    coco_df = raw_help_df[raw_help_df[ownership_col].astype(str).str.upper().str.strip() == "COCO"].copy()
else:
    coco_df = raw_help_df.copy()

branch_col = next((c for c in ["branchCode", "branch_code", "Store Name", "store"] if c in coco_df.columns), None)
store_name_col = next((c for c in ["Store Name", "StoreName", "store"] if c in coco_df.columns), None)
region_col = next((c for c in ["Region", "region"] if c in coco_df.columns), None)

coco_df["branchCode"] = coco_df[branch_col].astype(str).str.strip()
coco_df["Store Name"] = coco_df[store_name_col].astype(str).str.strip() if store_name_col else coco_df["branchCode"]
coco_df["Region"] = coco_df[region_col].astype(str).str.strip() if region_col else "Other"

coco_df = coco_df[["branchCode", "Store Name", "Region"]].drop_duplicates("branchCode")
coco_df = coco_df[coco_df["branchCode"] != ""].copy()
coco_branches = coco_df["branchCode"].tolist()

print(f"🏪 Active COCO Branches Loaded: {len(coco_branches)} stores")

# =========================================================
# CONCURRENT SALES FETCH
# =========================================================
def fetch_single_branch_day(branch, d_str):
    params = {"branch": branch, "day": d_str, "page": 1, "limit": 5000}
    try:
        res = requests.get(SALES_URL, headers=api_headers(), params=params, timeout=45)
        if res.status_code != 200:
            return None
        data = res.json().get("data", [])
        if not data:
            return None
        df_batch = pd.json_normalize(data)
        df_batch["branchCode"] = branch
        df_batch["invoiceDay"] = d_str
        return df_batch
    except Exception:
        return None

# Build tasks
date_range = [START_DATE + timedelta(days=x) for x in range((business_date - START_DATE).days + 1)]
tasks = [(b, dt.strftime("%Y-%m-%d")) for dt in date_range for b in coco_branches]

print(f"⚡ Dispatched {len(tasks)} requests with 15 parallel workers...")
start_clock = time.time()
all_batches = []

with ThreadPoolExecutor(max_workers=15) as executor:
    future_to_task = {executor.submit(fetch_single_branch_day, b, d): (b, d) for b, d in tasks}
    completed_count = 0
    for future in as_completed(future_to_task):
        res_df = future.result()
        if res_df is not None and not res_df.empty:
            all_batches.append(res_df)
        completed_count += 1
        if completed_count % 300 == 0 or completed_count == len(tasks):
            print(f"   Progress: {completed_count}/{len(tasks)} completed ({(completed_count/len(tasks))*100:.1f}%)")

print(f"⏱ Data extraction completed in {round((time.time() - start_clock)/60, 2)} minutes.")

if not all_batches:
    print("❌ No records retrieved.")
    exit(0)

raw_df = pd.concat(all_batches, ignore_index=True)
if "status" in raw_df.columns:
    raw_df = raw_df[raw_df["status"].astype(str).str.upper().str.strip() == "CLOSED"].copy()

# =========================================================
# FLATTEN & CALCULATE METRICS
# =========================================================
raw_df = raw_df.explode("items")
raw_df = raw_df[raw_df["items"].notna()].copy()
items_normalized = pd.json_normalize(raw_df["items"])
items_normalized.columns = [f"item_{c}" for c in items_normalized.columns]

raw_df = raw_df.drop(columns=["items"]).reset_index(drop=True)
items_normalized = items_normalized.reset_index(drop=True)
full_df = pd.concat([raw_df, items_normalized], axis=1)

def get_order_source(channel_str):
    ch = str(channel_str or "").lower()
    if "swiggy" in ch: return "swiggy"
    if "zomato" in ch: return "zomato"
    if "ownly" in ch: return "ownly"
    return "in-store"

channel_col = next((c for c in ["channel", "orderSource", "source"] if c in full_df.columns), None)
full_df["Order Source"] = full_df[channel_col].apply(get_order_source) if channel_col else "in-store"

full_df = full_df.merge(coco_df, on="branchCode", how="left")

full_df["Business Brand"] = full_df.get("brandName", "Frozen Bottle").fillna("Frozen Bottle").astype(str).str.strip()
full_df["SKU Code"] = full_df.get("item_sku", full_df.get("item_code", "")).fillna("").astype(str).str.strip()
full_df["Category Name"] = full_df.get("item_categoryName", full_df.get("item_category", "Others")).fillna("Others").astype(str).str.strip()
full_df["Item Name"] = full_df.get("item_shortName", full_df.get("item_name", "")).fillna("").astype(str).str.strip()

full_df["Qty"] = pd.to_numeric(full_df.get("item_quantity", 1), errors="coerce").fillna(0)
full_df["Sales"] = pd.to_numeric(full_df.get("item_netAmount", 0), errors="coerce").fillna(0)
full_df["Gross Rev"] = pd.to_numeric(full_df.get("item_grossAmount", full_df["Sales"]), errors="coerce").fillna(0)
full_df["Discount"] = pd.to_numeric(full_df.get("item_netDiscountAmount", full_df.get("item_discountAmount", 0)), errors="coerce").abs().fillna(0)
full_df["Material cost"] = pd.to_numeric(full_df.get("item_itemMaterialCost", 0), errors="coerce").fillna(0)

# Group at Day + Store + Brand + Category + Item + Source
agg_df = full_df.groupby([
    "invoiceDay", "Region", "Store Name", "Business Brand", "SKU Code", "Category Name", "Item Name", "Order Source"
], as_index=False).agg({
    "Qty": "sum",
    "Sales": "sum",
    "Material cost": "sum",
    "Gross Rev": "sum",
    "Discount": "sum"
})

agg_df["Food Cost%"] = np.where(agg_df["Sales"] > 0, (agg_df["Material cost"] / agg_df["Sales"]) * 100, 0).round(1)
agg_df["Dis%"] = np.where(agg_df["Gross Rev"] > 0, (agg_df["Discount"] / agg_df["Gross Rev"]) * 100, 0).round(1)

export_columns = [
    "Date", "Region", "Store Name", "Business Brand", "SKU Code", "Category Name", 
    "Item Name", "Order Source", "Qty", "Sales", "Material cost", "Gross Rev", "Discount", "Food Cost%", "Dis%"
]
agg_df = agg_df.rename(columns={"invoiceDay": "Date"})[export_columns]

for col in ["Sales", "Material cost", "Gross Rev", "Discount"]:
    agg_df[col] = agg_df[col].round(2)

# =========================================================
# SAVE MONTHLY CSV FILES IN GITHUB
# =========================================================
agg_df["YearMonth"] = agg_df["Date"].str.slice(0, 7)

for ym, month_df in agg_df.groupby("YearMonth"):
    month_file = os.path.join(DATA_DIR, f"{ym}.csv")
    cleaned_df = month_df.drop(columns=["YearMonth"])

    # If file exists, update with fresh dates
    if os.path.exists(month_file):
        existing_m_df = pd.read_csv(month_file)
        fresh_dates = cleaned_df["Date"].unique().tolist()
        retained = existing_m_df[~existing_m_df["Date"].astype(str).isin(fresh_dates)]
        final_m_df = pd.concat([retained, cleaned_df], ignore_index=True)
    else:
        final_m_df = cleaned_df

    final_m_df.sort_values(by=["Date", "Business Brand", "Category Name"], inplace=True)
    final_m_df.to_csv(month_file, index=False)
    print(f"💾 Saved {len(final_m_df)} rows to {month_file}")

# Save Index manifest
index_payload = {
    "updated_at": datetime.now(IST).isoformat(),
    "months": sorted(agg_df["YearMonth"].unique().tolist()),
    "total_rows": len(agg_df)
}
with open(os.path.join(DATA_DIR, "index.json"), "w") as f:
    json.dump(index_payload, f, indent=2)

print("✅ Data stored in data/food_cost/ monthly folder structure.")
