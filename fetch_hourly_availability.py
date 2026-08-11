import os
import json
import time
import jwt
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import gspread
from google.oauth2.service_account import Credentials

print("🚀 Rista Hourly Availability Pipeline Started")

# =========================================================
# CONFIGURATION & AUTH
# =========================================================
API_KEY = os.environ["API_KEY"]
SECRET_KEY = os.environ["SECRET_KEY"]
RISTA_BASE_URL = "https://api.ristaapps.com/v1"

SPREADSHEET_ID = "130C3oQsVmONGVUulhGbDWroRKpkebwgnFhq3uiny_O0"
TARGET_HOURLY_TAB = "Hourly_Availability_Dashboard"

# Warehouse Branch Codes
WH_BRANCH_CODES = ["90003-2221", "DW", "90003-2216", "90003-2218", "90003-2214", "90003-2215"]

session = requests.Session()

def get_token():
    payload = {"iss": API_KEY, "iat": int(time.time())}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def get_headers():
    return {
        "x-api-key": API_KEY,
        "x-api-token": get_token(),
        "content-type": "application/json"
    }

# Google Credentials
google_creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS"])
scopes = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_info(google_creds_json, scopes=scopes)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SPREADSHEET_ID)

# =========================================================
# DATE SELECTION (TODAY - LATEST DATA)
# =========================================================
today_str = datetime.now().strftime("%Y-%m-%d")
print(f"📅 Running Hourly Availability for: {today_str}")

# =========================================================
# LOAD HELP SHEET & MAP STORES
# =========================================================
try:
    help_ws = spreadsheet.worksheet("Help_Sheet")
except Exception:
    help_ws = spreadsheet.worksheet("Help Sheet")

help_data = help_ws.get()
if not help_data:
    print("❌ Help Sheet Empty")
    exit()

max_cols = max(len(r) for r in help_data)
header_row = list(help_data[0]) + [""] * (max_cols - len(help_data[0]))
raw_headers = [str(h).strip().lower().replace(" ", "") for h in header_row]

safe_headers = []
for i, h in enumerate(raw_headers):
    h = f"blank_col_{i}" if not h else h
    if h in safe_headers:
        h = f"{h}_{i}"
    safe_headers.append(h)

rows = help_data[1:]
normalized_rows = [list(r) + [""] * (max_cols - len(r)) for r in rows]
help_df = pd.DataFrame(normalized_rows, columns=safe_headers)

branch_cols = [c for c in help_df.columns if "branchcode" in c]
store_cols = [c for c in help_df.columns if "storename" in c or "store" in c]
region_cols = [c for c in help_df.columns if "region" in c]
ownership_cols = [c for c in help_df.columns if "ownership" in c]

branch_col_name = branch_cols[0]
lookup_cols = [branch_col_name]
rename_dict = {branch_col_name: "branchCode"}

if store_cols:
    lookup_cols.append(store_cols[0])
    rename_dict[store_cols[0]] = "Store Name"
if region_cols:
    lookup_cols.append(region_cols[0])
    rename_dict[region_cols[0]] = "Region"

if ownership_cols:
    ownership_series = help_df[ownership_cols[0]].astype(str).str.upper().str.strip()
    branch_series = help_df[branch_col_name].astype(str).str.strip()
    is_coco_wh = ownership_series.str.contains("COCO|WARE|WH", na=False)
    is_explicit = branch_series.isin(WH_BRANCH_CODES)
    help_df = help_df[is_coco_wh | is_explicit].copy()

help_lookup = help_df[lookup_cols].copy().rename(columns=rename_dict)
help_lookup["branchCode"] = help_lookup["branchCode"].astype(str).str.strip()
help_lookup = help_lookup.drop_duplicates(subset=["branchCode"])

branches = help_lookup["branchCode"].loc[lambda x: x != ""].tolist()

# Map Custom Hourly Regions (COCO vs WH Grouping)
def map_hourly_group(row):
    b_code = str(row["branchCode"]).strip()
    reg = str(row.get("Region", "")).strip().upper()
    
    # Warehouse Mapping
    if b_code in WH_BRANCH_CODES or "WH" in reg or "WAREHOUSE" in reg:
        if "KA" in reg or "KARNATAKA" in reg or "BLR" in reg or "ECITY" in reg or "2214" in b_code:
            return "WH_KA"
        elif "MH" in reg or "PUNE" in reg or "MUMBAI" in reg or "2216" in b_code or "2215" in b_code:
            return "WH_MH"
        elif "KERALA" in reg or "KER" in reg or "2221" in b_code:
            return "WH_Kerala"
        elif "DELHI" in reg or "NCR" in reg or "DW" in b_code:
            return "WH_NCR"
        elif "TN" in reg or "CHENNAI" in reg or "MAA" in reg or "2218" in b_code:
            return "WH_TN"
        return "WH_KA"
    
    # Retail COCO Grouping
    if "KA" in reg or "KARNATAKA" in reg or "BLR" in reg:
        return "KA"
    elif "MH" in reg or "MAHARASHTRA" in reg or "PUNE" in reg or "MUMBAI" in reg:
        return "MH"
    elif "TN" in reg or "TAMIL" in reg or "CHENNAI" in reg:
        return "TN"
    elif "KER" in reg or "KERALA" in reg:
        return "Kerala"
    return reg if reg else "KA"

help_lookup["Hourly_Group"] = help_lookup.apply(map_hourly_group, axis=1)

# =========================================================
# FETCH TODAY'S HOURLY DATA
# =========================================================
def fetch_hourly_store(branch):
    all_records = []
    page = 1
    max_pages = 50
    
    while page <= max_pages:
        try:
            params = {
                "branch": branch,
                "day": today_str,
                "date": today_str,
                "page": page,
                "limit": 500,
                "count": 500
            }
            res = session.get(
                f"{RISTA_BASE_URL}/inventory/item/activity/page",
                headers=get_headers(),
                params=params,
                timeout=20
            )
            if res.status_code == 200:
                data = res.json().get("data", [])
                if not data:
                    break
                all_records.extend(data)
                if len(data) < 20:
                    break
                page += 1
            else:
                break
        except Exception:
            break

    if all_records:
        df = pd.json_normalize(all_records)
        df["branchCode"] = branch
        df["activityDate"] = today_str
        
        if "activities" in df.columns:
            df = df.dropna(subset=["activities"]).copy()
            df = df.explode("activities").reset_index(drop=True)
            activities_df = pd.json_normalize(df["activities"]).add_prefix("activity_")
            df = pd.concat([df.drop(columns=["activities"]), activities_df], axis=1)
            
        return df
    return None

print(f"⚡ Fetching Hourly Availability for {len(branches)} stores...")
hourly_dfs = []

with ThreadPoolExecutor(max_workers=12) as executor:
    futures = {executor.submit(fetch_hourly_store, b): b for b in branches}
    for future in as_completed(futures):
        res_df = future.result()
        if res_df is not None and not res_df.empty:
            hourly_dfs.append(res_df)

if not hourly_dfs:
    print("⚠️ Today's date (or current hour) returned no records yet. Checking yesterday fallback...")
    fallback_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    today_str = fallback_date
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(fetch_hourly_store, b): b for b in branches}
        for future in as_completed(futures):
            res_df = future.result()
            if res_df is not None and not res_df.empty:
                hourly_dfs.append(res_df)

if not hourly_dfs:
    print("❌ No hourly availability data retrieved.")
    exit()

final_df = pd.concat(hourly_dfs, ignore_index=True)
final_df["branchCode"] = final_df["branchCode"].astype(str).str.strip()
final_df = final_df.merge(help_lookup, on="branchCode", how="left")

num_fields = ["activity_quantity", "activity_cost", "openingBalance", "openingCost", "closingBalance", "closingCost"]
for col in num_fields:
    if col in final_df.columns:
        final_df[col] = pd.to_numeric(final_df[col], errors="coerce").fillna(0.0)
    else:
        final_df[col] = 0.0

final_df["activity_quantity"] = final_df["activity_quantity"].abs()
final_df["activity_cost"] = final_df["activity_cost"].abs()

# Differentiate Outbound Activity
act_type_clean = final_df["activity_type"].astype(str).str.strip().str.upper()
is_wh_store = final_df["branchCode"].isin(WH_BRANCH_CODES)
is_retail_outbound = (~is_wh_store) & (act_type_clean == "SALES")
is_wh_outbound = is_wh_store & act_type_clean.str.contains("TRANSFER OUT|TRANSFEROUT|TRANSFER_OUT", na=False)

outbound_df = final_df[is_retail_outbound | is_wh_outbound].copy()

# =========================================================
# AGGREGATIONS & METRICS
# =========================================================
closing_grp = final_df.groupby(["Hourly_Group", "Region", "branchCode", "Store Name"], as_index=False)[["closingCost", "closingBalance"]].sum()
outbound_grp = outbound_df.groupby(["Hourly_Group", "Region", "branchCode", "Store Name"], as_index=False)[["activity_cost", "activity_quantity"]].sum()

store_metrics = closing_grp.merge(outbound_grp, on=["Hourly_Group", "Region", "branchCode", "Store Name"], how="left").fillna(0.0)

store_metrics["Availability_Cost_Pct"] = np.where(
    store_metrics["closingCost"] > 0, 
    (1 - (store_metrics["activity_cost"] / store_metrics["closingCost"])), 
    0.0
)

# Overall KPI Card Aggregation Values
total_stores = len(store_metrics)
total_closing_cost = store_metrics["closingCost"].sum()
avg_availability_pct = store_metrics["Availability_Cost_Pct"].mean()
below_30_count = (store_metrics["Availability_Cost_Pct"] < 0.30).sum()
between_30_50_count = ((store_metrics["Availability_Cost_Pct"] >= 0.30) & (store_metrics["Availability_Cost_Pct"] < 0.50)).sum()
above_50_count = (store_metrics["Availability_Cost_Pct"] >= 0.50).sum()

# COCO and WH Group Summaries
coco_groups = ["KA", "MH", "TN", "Kerala"]
wh_groups = ["WH_KA", "WH_MH", "WH_Kerala", "WH_NCR", "WH_TN"]

def calculate_group_summary(groups_list):
    group_df = store_metrics[store_metrics["Hourly_Group"].isin(groups_list)].groupby("Hourly_Group", as_index=False).agg({
        "closingCost": "sum",
        "activity_cost": "sum",
        "branchCode": "count"
    })
    group_df["Availability_Pct"] = np.where(group_df["closingCost"] > 0, 1 - (group_df["activity_cost"] / group_df["closingCost"]), 0.0)
    return group_df

coco_summary_df = calculate_group_summary(coco_groups)
wh_summary_df = calculate_group_summary(wh_groups)

# =========================================================
# BUILD DASHBOARD MATRIX LAYOUT
# =========================================================
output_rows = []

# Row 1: Dashboard Title & Timestamp
output_rows.append([f"HOURLY AVAILABILITY REPORT — {today_str} ({datetime.now().strftime('%I:%M %p')})", "", "", "", "", ""])
output_rows.append(["", "", "", "", "", ""])

# Rows 3-7: Top KPI Summary Boxes
output_rows.append(["KPI METRIC", "VALUE", "", "AVAILABILITY RANGE", "STORE COUNT"])
output_rows.append(["Total Active Stores", total_stores, "", "🔴 Less than 30%", below_30_count])
output_rows.append(["Total Closing Stock Value", total_closing_cost, "", "🟡 30% to 50%", between_30_50_count])
output_rows.append(["Overall Avg Availability %", avg_availability_pct, "", "🟢 50% and Above", above_50_count])
output_rows.append(["", "", "", "", ""])

# Group COCO Summary
output_rows.append(["GROUP COCO - REGION SUMMARY", "", "", "", ""])
output_rows.append(["Region", "Stores", "Outbound Cost", "Closing Stock", "Availability %"])
for _, r in coco_summary_df.iterrows():
    output_rows.append([r["Hourly_Group"], r["branchCode"], r["activity_cost"], r["closingCost"], r["Availability_Pct"]])

output_rows.append(["", "", "", "", ""])

# Group Warehouse Summary
output_rows.append(["WAREHOUSE SUMMARY", "", "", "", ""])
output_rows.append(["Warehouse Region", "Stores", "Transfer Out Cost", "Closing Stock", "Availability %"])
for _, r in wh_summary_df.iterrows():
    output_rows.append([r["Hourly_Group"], r["branchCode"], r["activity_cost"], r["closingCost"], r["Availability_Pct"]])

output_rows.append(["", "", "", "", ""])

# Region + Store Details Section
output_rows.append(["REGION & STORE WISE AVAILABILITY DETAILS", "", "", "", ""])

all_groups = coco_groups + wh_groups
for grp in all_groups:
    grp_stores = store_metrics[store_metrics["Hourly_Group"] == grp].sort_values(by="Store Name")
    if grp_stores.empty:
        continue
    
    output_rows.append([f"📍 REGION: {grp}", "", "", "", ""])
    output_rows.append(["Branch Code", "Store Name", "Outbound/Transfer Cost", "Closing Cost", "Availability %"])
    
    for _, sr in grp_stores.iterrows():
        output_rows.append([sr["branchCode"], sr["Store Name"], sr["activity_cost"], sr["closingCost"], sr["Availability_Cost_Pct"]])
    
    output_rows.append(["", "", "", "", ""])

# =========================================================
# EXPORT TO GOOGLE SHEET & APPLY STYLING
# =========================================================
try:
    ws_hourly = spreadsheet.worksheet(TARGET_HOURLY_TAB)
except Exception:
    ws_hourly = spreadsheet.add_worksheet(title=TARGET_HOURLY_TAB, rows="500", cols="10")

ws_hourly.clear()
ws_hourly.update(output_rows, "A1")
print(f"✅ Data populated in '{TARGET_HOURLY_TAB}' tab.")

# Apply Formatting Request
def apply_hourly_styles():
    sheet_id = ws_hourly.id
    reqs = []
    
    NAVY = {"red": 0.12, "green": 0.23, "blue": 0.37}
    WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}
    GRAY_HEADER = {"red": 0.90, "green": 0.92, "blue": 0.95}
    
    # Title Row
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 5},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": NAVY,
                    "textFormat": {"bold": True, "foregroundColor": WHITE, "fontSize": 12},
                    "horizontalAlignment": "CENTER"
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"
        }
    })
    
    # KPI Box Headers
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 0, "endColumnIndex": 5},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": GRAY_HEADER,
                    "textFormat": {"bold": True},
                    "horizontalAlignment": "CENTER"
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"
        }
    })
    
    # Number Formats for KPI Block
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 4, "endRowIndex": 5, "startColumnIndex": 1, "endColumnIndex": 2},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}},
            "fields": "userEnteredFormat.numberFormat"
        }
    })
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 5, "endRowIndex": 6, "startColumnIndex": 1, "endColumnIndex": 2},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}}},
            "fields": "userEnteredFormat.numberFormat"
        }
    })

    try:
        spreadsheet.batch_update({"requests": reqs})
        print("🎨 Formatting applied successfully!")
    except Exception as err:
        print(f"⚠️ Formatting warning: {err}")

apply_hourly_styles()
print("🏁 Hourly Availability Pipeline Complete!")
