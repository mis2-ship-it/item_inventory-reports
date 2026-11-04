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

# Target Workbook (Contains 'Help Sheet' with branchCode and 'Food_Cost_Raw_Data')
SPREADSHEET_ID = "1ldgnNMdeubDx_ImtCC1uCD7FGz8gt_edmWEkmO_xRNk"
SALES_URL = "https://api.ristaapps.com/v1/sales/page"

# Temporary backfill window for September:
START_DATE = datetime(2026, 9, 1).date()
business_date = datetime(2026, 9, 25).date()

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
ss = client.open_by_key(SPREADSHEET_ID)

# =========================================================
# LOAD COCO STORES & CHANNEL MAPPING FROM HELP SHEET
# =========================================================
help_ws = ss.worksheet("Help Sheet")
help_vals = help_ws.get_all_values()

if not help_vals or len(help_vals) < 2:
    raise RuntimeError("Help Sheet is empty")

store_meta = []
channel_source_map = {}

for row in help_vals[1:]:
    # 1. Store Extraction: Col A (0): branchCode, Col B (1): Store Name, Col C (2): Ownership, Col D (3): Region
    b_code = str(row[0]).strip() if len(row) > 0 else ""
    s_name = str(row[1]).strip() if len(row) > 1 else ""
    ownership = str(row[2]).strip().upper() if len(row) > 2 else ""
    reg = str(row[3]).strip() if len(row) > 3 else "KA"

    if ownership == "COCO" and b_code:
        store_meta.append({
            "branchCode": b_code,
            "Store Name": s_name if s_name else b_code,
            "Region": reg if reg else "KA"
        })

    # 2. Channel -> Source Mapping: Col F (5): Channel, Col G (6): Source
    if len(row) >= 7:
        ch = str(row[5]).strip()
        src = str(row[6]).strip()
        if ch and src:
            channel_source_map[ch.lower()] = src

coco_df = pd.DataFrame(store_meta, columns=["branchCode", "Store Name", "Region"]).drop_duplicates("branchCode")
coco_branches = coco_df["branchCode"].tolist()

print(f"🏪 Active COCO Branches Loaded: {len(coco_branches)} stores")
print(f"📋 First 5 Branch Codes: {coco_branches[:5]}")
print(f"🔗 Channel Mappings Loaded: {len(channel_source_map)}")

# =========================================================
# CONCURRENT SALES FETCH
# =========================================================
logged_sample_error = False

def fetch_single_branch_day(branch, d_str):
    global logged_sample_error
    params = {
        "branch": str(branch).strip(),
        "day": d_str,
        "page": 1,
        "limit": 5000
    }
    try:
        res = requests.get(SALES_URL, headers=api_headers(), params=params, timeout=45)
        if res.status_code != 200:
            if not logged_sample_error:
                print(f"\n⚠ Sample API Error -> Branch: '{branch}' | Day: '{d_str}' | Status: {res.status_code}")
                print(f"   Body: {res.text[:200]}\n")
                logged_sample_error = True
            return None
        
        data = res.json().get("data", [])
        if not data:
            return None
        
        df_batch = pd.json_normalize(data)
        df_batch["branchCode"] = branch
        df_batch["invoiceDay"] = d_str
        return df_batch
    except Exception as e:
        if not logged_sample_error:
            print(f"❌ Exception on branch '{branch}': {e}")
            logged_sample_error = True
        return None

# Build tasks
date_range = [START_DATE + timedelta(days=x) for x in range((business_date - START_DATE).days + 1)]
tasks = [(b, dt.strftime("%Y-%m-%d")) for dt in date_range for b in coco_branches]

print(f"⚡ Dispatched {len(tasks)} requests with 15 parallel workers across {len(date_range)} days...")
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
    print("❌ No records retrieved. Please check API responses above.")
    exit(0)

raw_df = pd.concat(all_batches, ignore_index=True)

# Filter Closed status
if "status" in raw_df.columns:
    raw_df = raw_df[raw_df["status"].astype(str).str.upper().str.strip() == "CLOSED"].copy()

# =========================================================
# FLATTEN ITEMS & COMPUTE FOOD COST / DISCOUNT
# =========================================================
# If API returned nested 'items', explode it; if already exploded, keep as is
if "items" in raw_df.columns:
    raw_df = raw_df.explode("items")
    raw_df = raw_df[raw_df["items"].notna()].copy()
    items_normalized = pd.json_normalize(raw_df["items"])
    # If keys don't already start with item_, prefix them
    items_normalized.columns = [c if c.startswith("item_") else f"item_{c}" for c in items_normalized.columns]
    raw_df = raw_df.drop(columns=["items"]).reset_index(drop=True)
    items_normalized = items_normalized.reset_index(drop=True)
    full_df = pd.concat([raw_df, items_normalized], axis=1)
else:
    full_df = raw_df.copy()

# Map Channel to Order Source using Help Sheet definitions
def resolve_order_source(channel_val):
    ch_str = str(channel_val or "").strip().lower()
    if ch_str in channel_source_map:
        return channel_source_map[ch_str]
    for k, v in channel_source_map.items():
        if k in ch_str:
            return v
    if "swiggy" in ch_str: return "Swiggy"
    if "zomato" in ch_str: return "Zomato"
    if "ownly" in ch_str: return "Ownly"
    if "magicpin" in ch_str: return "Magicpin"
    return "In Store"

channel_col = next((c for c in ["channel", "orderSource", "source"] if c in full_df.columns), None)
full_df["Order Source"] = full_df[channel_col].apply(resolve_order_source) if channel_col else "In Store"

# Merge COCO Metadata
if "Store Name" not in full_df.columns or "Region" not in full_df.columns:
    full_df = full_df.merge(coco_df[["branchCode", "Store Name", "Region"]], on="branchCode", how="left")

# Helper function to extract series safely without AttributeError
def get_series(df, candidate_cols, default=""):
    for col in candidate_cols:
        if col in df.columns:
            return df[col].fillna(default)
    return pd.Series(default, index=df.index)

# Field Extractions matching salepage schema
full_df["Business Brand"] = get_series(full_df, ["item_brandName", "brandName"], default="Frozen Bottle").astype(str).str.strip()
full_df["SKU Code"] = get_series(full_df, ["item_skuCode", "item_sku", "item_barCode"], default="").astype(str).str.strip()
full_df["Category Name"] = get_series(full_df, ["item_categoryName", "item_category"], default="Others").astype(str).str.strip()
full_df["Item Name"] = get_series(full_df, ["item_shortName", "item_longName", "item_name"], default="").astype(str).str.strip()

# Numeric Extractions matching salepage schema
full_df["Qty"] = pd.to_numeric(get_series(full_df, ["item_quantity", "quantity"], default=1), errors="coerce").fillna(0)
full_df["Sales"] = pd.to_numeric(get_series(full_df, ["item_netAmount", "netAmount"], default=0), errors="coerce").fillna(0)
full_df["Gross Rev"] = pd.to_numeric(get_series(full_df, ["item_grossAmount", "grossAmount"], default=0), errors="coerce").fillna(0)
full_df["Discount"] = pd.to_numeric(get_series(full_df, ["item_netDiscountAmount", "item_discountAmount"], default=0), errors="coerce").abs().fillna(0)
full_df["Material cost"] = pd.to_numeric(get_series(full_df, ["item_itemMaterialCost", "item_itemCost"], default=0), errors="coerce").fillna(0)

# If Gross Rev is zero, set it to Sales + Discount
zero_gross_mask = (full_df["Gross Rev"] == 0) & (full_df["Sales"] > 0)
full_df.loc[zero_gross_mask, "Gross Rev"] = full_df.loc[zero_gross_mask, "Sales"] + full_df.loc[zero_gross_mask, "Discount"]

# Date field resolution
date_series = get_series(full_df, ["invoiceDay", "invoiceDate", "createdDate"])
full_df["Date"] = date_series.astype(str).str.slice(0, 10)

# =========================================================
# GROUP AT DAY + STORE + BRAND + CATEGORY + ITEM + SOURCE
# =========================================================
group_cols = [
    "Date", "Region", "Store Name", "Business Brand", "SKU Code", "Category Name", "Item Name", "Order Source"
]

# Ensure no NaN keys
for col in group_cols:
    full_df[col] = full_df[col].fillna("Unknown").astype(str)

agg_df = full_df.groupby(group_cols, as_index=False).agg({
    "Qty": "sum",
    "Sales": "sum",
    "Material cost": "sum",
    "Gross Rev": "sum",
    "Discount": "sum"
})

# Calculate Percentages
agg_df["Food Cost%"] = np.where(agg_df["Sales"] > 0, (agg_df["Material cost"] / agg_df["Sales"]) * 100, 0).round(1)
agg_df["Dis%"] = np.where(agg_df["Gross Rev"] > 0, (agg_df["Discount"] / agg_df["Gross Rev"]) * 100, 0).round(1)

export_columns = [
    "Date", "Region", "Store Name", "Business Brand", "SKU Code", "Category Name", 
    "Item Name", "Order Source", "Qty", "Sales", "Material cost", "Gross Rev", "Discount", "Food Cost%", "Dis%"
]
agg_df = agg_df[export_columns]

for col in ["Sales", "Material cost", "Gross Rev", "Discount"]:
    agg_df[col] = agg_df[col].round(2)

# =========================================================
# SAVE MONTHLY CSV FILES IN GITHUB
# =========================================================
agg_df["YearMonth"] = agg_df["Date"].str.slice(0, 7)

for ym, month_df in agg_df.groupby("YearMonth"):
    month_file = os.path.join(DATA_DIR, f"{ym}.csv")
    cleaned_df = month_df.drop(columns=["YearMonth"])

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

index_payload = {
    "updated_at": datetime.now(IST).isoformat(),
    "months": sorted(agg_df["YearMonth"].unique().tolist()),
    "total_rows": len(agg_df)
}
with open(os.path.join(DATA_DIR, "index.json"), "w") as f:
    json.dump(index_payload, f, indent=2)

print("✅ Data stored in data/food_cost/ monthly folder structure successfully.")
