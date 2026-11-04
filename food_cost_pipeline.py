# =========================================================
# FOOD COST DATA PIPELINE (STANDALONE - COCO ONLY)
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
from google.oauth2.service_account import Credentials

print("🚀 Food Cost Pipeline Started")

# =========================================================
# CONFIG
# =========================================================
API_KEY = os.environ["API_KEY"]
SECRET_KEY = os.environ["SECRET_KEY"]

HELP_SPREADSHEET_ID = "1CVUS-BSBfDIoQI4Yk2GB4_Zp1CIJRF-9YRfpvCih-FM"
RAW_SPREADSHEET_ID = "1ldgnNMdeubDx_ImtCC1uCD7FGz8gt_edmWEkmO_xRNk"
SALES_URL = "https://api.ristaapps.com/v1/sales/page"

# Start Date: September 26, 2026
START_DATE = datetime(2026, 9, 26).date()

IST = ZoneInfo("Asia/Kolkata")
now = datetime.now(IST)
business_date = now.date() - timedelta(days=1) if now.hour < 9 else now.date()

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
raw_ss = client.open_by_key(RAW_SPREADSHEET_ID)

# =========================================================
# LOAD COCO STORES & METADATA (HEADER-AWARE)
# =========================================================
help_ws = help_ss.worksheet("Help Sheet")
help_vals = help_ws.get_all_values()

if not help_vals or len(help_vals) < 2:
    raise RuntimeError("Help Sheet is empty or missing data rows")

headers = [str(h).strip() for h in help_vals[0]]
raw_help_df = pd.DataFrame(help_vals[1:], columns=headers)

# Clean column names
raw_help_df.columns = [c.strip() for c in raw_help_df.columns]

# Resolve Ownership column dynamically
ownership_col = next((c for c in ["Ownership", "ownership", "Type", "type"] if c in raw_help_df.columns), None)
if ownership_col:
    coco_df = raw_help_df[raw_help_df[ownership_col].astype(str).str.upper().str.strip() == "COCO"].copy()
else:
    coco_df = raw_help_df.copy()

# Resolve branchCode column dynamically (falls back to Store Name if branchCode isn't a column)
branch_col = next((c for c in ["branchCode", "branch_code", "Branch Code", "BranchCode", "Store Name", "store"] if c in coco_df.columns), None)
store_name_col = next((c for c in ["Store Name", "StoreName", "store_name", "store"] if c in coco_df.columns), None)
region_col = next((c for c in ["Region", "region"] if c in coco_df.columns), None)

if not branch_col:
    raise KeyError(f"Could not identify a branchCode or Store Name column in Help Sheet. Found headers: {headers}")

# Standardize to expected columns
coco_df["branchCode"] = coco_df[branch_col].astype(str).str.strip()
if store_name_col:
    coco_df["Store Name"] = coco_df[store_name_col].astype(str).str.strip()
else:
    coco_df["Store Name"] = coco_df["branchCode"]

if region_col:
    coco_df["Region"] = coco_df[region_col].astype(str).str.strip()
else:
    coco_df["Region"] = "Other"

coco_df = coco_df[["branchCode", "Store Name", "Region"]].drop_duplicates("branchCode")
coco_df = coco_df[coco_df["branchCode"] != ""].copy()

coco_branches = coco_df["branchCode"].tolist()
print(f"🏪 Active COCO Branches Loaded: {len(coco_branches)} stores")

# =========================================================
# FETCH SALES FROM SEP 26
# =========================================================
date_range = [START_DATE + timedelta(days=x) for x in range((business_date - START_DATE).days + 1)]
print(f"📅 Fetching window: {START_DATE} to {business_date} ({len(date_range)} days)")

all_records = []

for dt in date_range:
    d_str = dt.strftime("%Y-%m-%d")
    for branch in coco_branches:
        params = {"branch": branch, "day": d_str, "page": 1, "limit": 5000}
        try:
            res = requests.get(SALES_URL, headers=api_headers(), params=params, timeout=120)
            if res.status_code != 200:
                continue
            data = res.json().get("data", [])
            if not data:
                continue
            df_batch = pd.json_normalize(data)
            df_batch["branchCode"] = branch
            df_batch["invoiceDay"] = d_str
            all_records.append(df_batch)
        except Exception:
            continue

if not all_records:
    print("❌ No records retrieved.")
    exit(0)

raw_df = pd.concat(all_records, ignore_index=True)
if "status" in raw_df.columns:
    raw_df = raw_df[raw_df["status"].astype(str).str.upper().str.strip() == "CLOSED"].copy()

# =========================================================
# FLATTEN & CALCULATE FOOD COST FORMAT
# =========================================================
if "items" not in raw_df.columns:
    print("❌ No line items found.")
    exit(0)

raw_df = raw_df.explode("items")
raw_df = raw_df[raw_df["items"].notna()].copy()
items_normalized = pd.json_normalize(raw_df["items"])
items_normalized.columns = [f"item_{c}" for c in items_normalized.columns]

raw_df = raw_df.drop(columns=["items"]).reset_index(drop=True)
items_normalized = items_normalized.reset_index(drop=True)
full_df = pd.concat([raw_df, items_normalized], axis=1)

# Normalize Channel / Order Source
def get_order_source(channel_str):
    ch = str(channel_str or "").lower()
    if "swiggy" in ch: return "swiggy"
    if "zomato" in ch: return "zomato"
    if "ownly" in ch: return "ownly"
    return "in-store"

channel_col = next((c for c in ["channel", "orderSource", "source"] if c in full_df.columns), None)
full_df["Order Source"] = full_df[channel_col].apply(get_order_source) if channel_col else "in-store"

# Merge COCO Metadata
full_df = full_df.merge(coco_df, on="branchCode", how="left")

# Field extractions
full_df["Business Brand"] = full_df.get("brandName", "Frozen Bottle").fillna("Frozen Bottle").astype(str).str.strip()
full_df["SKU Code"] = full_df.get("item_sku", full_df.get("item_code", "")).fillna("").astype(str).str.strip()
full_df["Category Name"] = full_df.get("item_categoryName", full_df.get("item_category", "Others")).fillna("Others").astype(str).str.strip()
full_df["Item Name"] = full_df.get("item_shortName", full_df.get("item_name", "")).fillna("").astype(str).str.strip()

# Metric Columns
full_df["Qty"] = pd.to_numeric(full_df.get("item_quantity", 1), errors="coerce").fillna(0)
full_df["Sales"] = pd.to_numeric(full_df.get("item_netAmount", 0), errors="coerce").fillna(0)
full_df["Gross Rev"] = pd.to_numeric(full_df.get("item_grossAmount", full_df["Sales"]), errors="coerce").fillna(0)
full_df["Discount"] = pd.to_numeric(full_df.get("item_netDiscountAmount", full_df.get("item_discountAmount", 0)), errors="coerce").abs().fillna(0)

# 🌟 TARGET MATERIAL COST COLUMN
full_df["Material cost"] = pd.to_numeric(full_df.get("item_itemMaterialCost", 0), errors="coerce").fillna(0)

# =========================================================
# GROUP AT DAY + STORE + BRAND + CATEGORY + ITEM + SOURCE
# =========================================================
agg_df = full_df.groupby([
    "invoiceDay", "Region", "Store Name", "Business Brand", "SKU Code", "Category Name", "Item Name", "Order Source"
], as_index=False).agg({
    "Qty": "sum",
    "Sales": "sum",
    "Material cost": "sum",
    "Gross Rev": "sum",
    "Discount": "sum"
})

# Calculate Percentages
agg_df["Food Cost%"] = np.where(agg_df["Sales"] > 0, (agg_df["Material cost"] / agg_df["Sales"]) * 100, 0).round(1)
agg_df["Dis%"] = np.where(agg_df["Gross Rev"] > 0, (agg_df["Discount"] / agg_df["Gross Rev"]) * 100, 0).round(1)

# Format Final Export Columns
export_columns = [
    "Date", "Region", "Store Name", "Business Brand", "SKU Code", "Category Name", 
    "Item Name", "Order Source", "Qty", "Sales", "Material cost", "Gross Rev", "Discount", "Food Cost%", "Dis%"
]
agg_df = agg_df.rename(columns={"invoiceDay": "Date"})
agg_df = agg_df[export_columns].sort_values(by=["Date", "Business Brand", "Category Name"], ascending=[True, True, True])

# Round currency fields
for col in ["Sales", "Material cost", "Gross Rev", "Discount"]:
    agg_df[col] = agg_df[col].round(2)

# =========================================================
# SMART PUSH TO GSHEET ('Food_Cost_Raw_Data')
# =========================================================
try:
    try:
        ws = raw_ss.worksheet("Food_Cost_Raw_Data")
    except gspread.exceptions.WorksheetNotFound:
        ws = raw_ss.add_worksheet(title="Food_Cost_Raw_Data", rows=50000, cols=20)
    
    ws.clear()
    agg_df["Date"] = agg_df["Date"].astype(str)
    matrix = [agg_df.columns.tolist()] + agg_df.fillna("").values.tolist()
    ws.update("A1", matrix)
    print(f"✅ Synced {len(agg_df)} records to 'Food_Cost_Raw_Data'. Total Sales: ₹{agg_df['Sales'].sum():,.2f} | Mat Cost: ₹{agg_df['Material cost'].sum():,.2f}")
except Exception as e:
    print("❌ Sheet update error:", e)
