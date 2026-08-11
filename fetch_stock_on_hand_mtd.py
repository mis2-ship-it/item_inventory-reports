import os
import io
import json
import time
import jwt
import pytz
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

print("🚀 Rista Complete MTD Inventory Activity Pipeline Started")

# =========================================================
# CONFIGURATION & AUTH
# =========================================================
API_KEY = os.environ["API_KEY"]
SECRET_KEY = os.environ["SECRET_KEY"]
RISTA_BASE_URL = "https://api.ristaapps.com/v1"

SPREADSHEET_ID = "130C3oQsVmONGVUulhGbDWroRKpkebwgnFhq3uiny_O0"
DRIVE_FOLDER_ID = "1cS_jlVQqMIMlk0omVozQR-rUBzjw9IUf"
TARGET_FILE_ID = "1tdLOS5XxD1HwazuxDMrY2n2Lp4J03R3B"

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
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_info(google_creds_json, scopes=scopes)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SPREADSHEET_ID)

# =========================================================
# DATE RANGE & DYNAMIC MONTHLY FILE NAME (IST TIMEZONE)
# =========================================================
ist = pytz.timezone('Asia/Kolkata')
today = datetime.now(ist)
start_date = today.replace(day=1)
end_date = today - timedelta(days=1)

date_list = []
curr = start_date
while curr <= end_date:
    date_list.append(curr.strftime("%Y-%m-%d"))
    curr += timedelta(days=1)

print(f"📅 Current IST MTD Range: {date_list[0]} to {date_list[-1]} ({len(date_list)} Days)")
month_year_filename = f"{today.strftime('%Y-%m')}.csv"

# =========================================================
# LOAD EXISTING DATA FROM GOOGLE DRIVE (INCREMENTAL CHECK)
# =========================================================
drive_service = build('drive', 'v3', credentials=creds)
existing_df = pd.DataFrame()
existing_file_id = None

try:
    query = f"name = '{month_year_filename}' and '{DRIVE_FOLDER_ID}' in parents and trashed = false"
    results = drive_service.files().list(
        q=query, 
        fields="files(id, name)", 
        supportsAllDrives=True, 
        includeItemsFromAllDrives=True
    ).execute()
    existing_files = results.get('files', [])

    if existing_files:
        existing_file_id = existing_files[0]['id']
        print(f"📥 Found existing Drive file: '{month_year_filename}' (ID: {existing_file_id}). Reading dataset...")
        request = drive_service.files().get_media(fileId=existing_file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.seek(0)
        existing_df = pd.read_csv(fh, dtype=str)
        print(f"✅ Loaded {len(existing_df)} existing rows from Drive.")
except Exception as e:
    print(f"ℹ️ Could not read existing Drive file (Will fetch all MTD dates): {e}")

existing_dates = set()
if not existing_df.empty and "activityDate" in existing_df.columns:
    existing_dates = set(existing_df["activityDate"].dropna().astype(str).str.strip().unique())

dates_to_fetch = [d for d in date_list if d not in existing_dates]
print(f"📅 Existing Dates in File: {sorted(list(existing_dates))}")
print(f"⚡ Missing Dates to Fetch: {dates_to_fetch}")

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
print(f"🏪 Active Branches Loaded: {len(branches)}")

# =========================================================
# FETCH ALL SKUs (EXHAUSTIVE UNLIMITED PAGINATION LOOP)
# =========================================================
def fetch_branch_day_data(branch, day_str):
    all_records = []
    page = 1
    max_pages = 100
    
    while page <= max_pages:
        try:
            params = {
                "branch": branch,
                "day": day_str,
                "date": day_str,
                "page": page,
                "pageNo": page,
                "limit": 100,
                "count": 100,
                "pageSize": 100,
                "size": 100
            }
            res = session.get(
                f"{RISTA_BASE_URL}/inventory/item/activity/page",
                headers=get_headers(),
                params=params,
                timeout=25
            )
            if res.status_code == 200:
                data = res.json().get("data", [])
                if not data or len(data) == 0:
                    break
                
                all_records.extend(data)
                page += 1
            else:
                break
        except Exception:
            break

    if all_records:
        df = pd.json_normalize(all_records)
        df["branchCode"] = branch
        df["activityDate"] = day_str
        
        if "activities" in df.columns:
            df = df.dropna(subset=["activities"]).copy()
            df = df.explode("activities").reset_index(drop=True)
            activities_df = pd.json_normalize(df["activities"]).add_prefix("activity_")
            df = pd.concat([df.drop(columns=["activities"]), activities_df], axis=1)
            
        return df
    return None

new_data_dfs = []

if dates_to_fetch:
    tasks = [(b, d) for b in branches for d in dates_to_fetch]
    print(f"⚡ Processing {len(tasks)} tasks for missing dates (All SKUs)...")
    
    completed_count = 0
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(fetch_branch_day_data, b, d): (b, d) for b, d in tasks}
        for future in as_completed(futures):
            completed_count += 1
            res_df = future.result()
            if res_df is not None and not res_df.empty:
                new_data_dfs.append(res_df)
            
            if completed_count % 50 == 0 or completed_count == len(tasks):
                print(f"📊 Completed [{completed_count}/{len(tasks)}] fetch tasks...")
else:
    print("✅ All dates already exist in Drive CSV. Skipping API fetches!")

if new_data_dfs:
    fetched_df = pd.concat(new_data_dfs, ignore_index=True)
    if not existing_df.empty:
        final_df = pd.concat([existing_df, fetched_df], ignore_index=True)
    else:
        final_df = fetched_df
else:
    final_df = existing_df

if final_df.empty:
    print("❌ No activity data retrieved or present.")
    exit()

# Convert any unhashable columns (lists/dicts) to string format prior to deduplication
for col in final_df.columns:
    if final_df[col].apply(lambda x: isinstance(x, (list, dict))).any():
        final_df[col] = final_df[col].astype(str)

final_df = final_df.drop_duplicates().copy()
final_df["branchCode"] = final_df["branchCode"].astype(str).str.strip()

for col in ["Store Name", "Region"]:
    if col in final_df.columns:
        final_df = final_df.drop(columns=[col])

final_df = final_df.merge(help_lookup, on="branchCode", how="left")

num_fields = ["activity_quantity", "activity_cost", "openingBalance", "openingCost", "closingBalance", "closingCost"]
for col in num_fields:
    if col in final_df.columns:
        final_df[col] = pd.to_numeric(final_df[col], errors="coerce").fillna(0.0)
    else:
        final_df[col] = 0.0

final_df["activity_quantity"] = final_df["activity_quantity"].abs()
final_df["activity_cost"] = final_df["activity_cost"].abs()

lead_cols = [c for c in ["branchCode", "Store Name", "Region", "activityDate"] if c in final_df.columns]
final_df = final_df[lead_cols + [c for c in final_df.columns if c not in lead_cols]]

# =========================================================
# 1. SAVE MASTER DATA TO CSV & UPDATE GOOGLE DRIVE
# =========================================================
final_df.to_csv(month_year_filename, index=False)
print(f"📁 Local CSV generated: {month_year_filename} ({len(final_df)} total rows)")

try:
    media = MediaFileUpload(month_year_filename, mimetype='text/csv', resumable=True)
    upload_file_id = existing_file_id if existing_file_id else TARGET_FILE_ID
    
    drive_service.files().update(
        fileId=upload_file_id,
        media_body=media,
        addParents=DRIVE_FOLDER_ID,
        supportsAllDrives=True
    ).execute()
    print(f"✅ Master Data updated in Drive File ID '{upload_file_id}' inside Folder '{DRIVE_FOLDER_ID}'")
except Exception as e:
    print(f"❌ Google Drive Update Error: {e}")

# =========================================================
# 2. SEPARATE OUTBOUND ACTIVITY (SALES / TRANSFER OUT)
# =========================================================
act_type_clean = final_df["activity_type"].astype(str).str.strip().str.upper()
is_wh_store = final_df["branchCode"].isin(WH_BRANCH_CODES)

is_retail_outbound = (~is_wh_store) & (act_type_clean == "SALES")
is_wh_outbound = is_wh_store & act_type_clean.str.contains("TRANSFER OUT|TRANSFEROUT|TRANSFER_OUT", na=False)

outbound_df = final_df[is_retail_outbound | is_wh_outbound].copy()

# =========================================================
# 3. GENERATE STORE-LEVEL SUMMARY (WITH SALES COST & QTY)
# =========================================================
min_date, max_date = date_list[0], date_list[-1]

opening_df = final_df[final_df["activityDate"] == min_date].groupby(["Region", "Store Name"], as_index=False)[["openingCost", "openingBalance"]].sum()
closing_df = final_df[final_df["activityDate"] == max_date].groupby(["Region", "Store Name"], as_index=False)[["closingCost", "closingBalance"]].sum()
outbound_activity_sums = outbound_df.groupby(["Region", "Store Name"], as_index=False)[["activity_cost", "activity_quantity"]].sum()

store_summary = opening_df.merge(closing_df, on=["Region", "Store Name"], how="outer").merge(outbound_activity_sums, on=["Region", "Store Name"], how="outer").fillna(0)

# Stock on Hand Formula: 1 - (Outbound / Closing)
store_summary["Stock on Hand Cost %"] = np.where(
    store_summary["closingCost"] > 0, 
    (1 - (store_summary["activity_cost"] / store_summary["closingCost"])), 
    0.0
)
store_summary["Stock on Hand Qty %"] = np.where(
    store_summary["closingBalance"] > 0, 
    (1 - (store_summary["activity_quantity"] / store_summary["closingBalance"])), 
    0.0
)

store_summary = store_summary.rename(columns={
    "openingCost": "Opening Cost",
    "openingBalance": "Opening Qty",
    "activity_cost": "Sales/Outbound Cost",
    "activity_quantity": "Sales/Outbound Qty",
    "closingCost": "Closing Cost",
    "closingBalance": "Closing Qty"
}).sort_values(by=["Region", "Store Name"])

store_summary_display = store_summary[[
    "Region", "Store Name", 
    "Opening Cost", "Opening Qty", 
    "Sales/Outbound Cost", "Sales/Outbound Qty", 
    "Closing Cost", "Closing Qty", 
    "Stock on Hand Cost %", "Stock on Hand Qty %"
]].copy()

# =========================================================
# 4. GENERATE OVERALL REGION SUMMARY (WITH SALES COST & QTY)
# =========================================================
region_agg = store_summary.groupby("Region", as_index=False)[[
    "Opening Cost", "Opening Qty", 
    "Sales/Outbound Cost", "Sales/Outbound Qty", 
    "Closing Cost", "Closing Qty"
]].sum()

region_agg["Stock on Hand Cost %"] = np.where(
    region_agg["Closing Cost"] > 0, 
    (1 - (region_agg["Sales/Outbound Cost"] / region_agg["Closing Cost"])), 
    0.0
)
region_agg["Stock on Hand Qty %"] = np.where(
    region_agg["Closing Qty"] > 0, 
    (1 - (region_agg["Sales/Outbound Qty"] / region_agg["Closing Qty"])), 
    0.0
)

kpi_cols = [
    "Opening Cost", "Opening Qty", 
    "Sales/Outbound Cost", "Sales/Outbound Qty", 
    "Closing Cost", "Closing Qty", 
    "Stock on Hand Cost %", "Stock on Hand Qty %"
]
region_summary = pd.DataFrame({"KPI Metrics": kpi_cols})

for _, row in region_agg.iterrows():
    reg = row["Region"]
    region_summary[reg] = [
        round(row["Opening Cost"], 2),
        round(row["Opening Qty"], 2),
        round(row["Sales/Outbound Cost"], 2),
        round(row["Sales/Outbound Qty"], 2),
        round(row["Closing Cost"], 2),
        round(row["Closing Qty"], 2),
        round(row["Stock on Hand Cost %"], 4),
        round(row["Stock on Hand Qty %"], 4)
    ]

# =========================================================
# 5. GENERATE DAILY STOCK ON HAND SHEET
# =========================================================
daily_outbound = outbound_df.groupby(["Region", "Store Name", "activityDate"], as_index=False)["activity_cost"].sum()
daily_closing = final_df.groupby(["Region", "Store Name", "activityDate"], as_index=False)["closingCost"].sum()

daily_merged = daily_closing.merge(daily_outbound, on=["Region", "Store Name", "activityDate"], how="left").fillna(0)
daily_merged["SOH_Cost_Pct"] = np.where(
    daily_merged["closingCost"] > 0, 
    (1 - (daily_merged["activity_cost"] / daily_merged["closingCost"])), 
    0.0
)

daily_pivot = daily_merged.pivot(index=["Region", "Store Name"], columns="activityDate", values="SOH_Cost_Pct").fillna(0.0).reset_index()

# =========================================================
# 6. EXPORT DASHBOARD TABS TO GOOGLE SHEET
# =========================================================
def update_tab(tab_name, df):
    try:
        ws = spreadsheet.worksheet(tab_name)
    except Exception:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=str(len(df) + 100), cols=str(len(df.columns) + 10))
    ws.clear()
    
    cleaned_df = df.replace([np.inf, -np.inf], 0.0).fillna(0.0)
    sheet_data = [cleaned_df.columns.tolist()] + cleaned_df.values.tolist()
    ws.update(sheet_data, "A1")
    print(f"✅ Dashboard populated: '{tab_name}' ({len(df)} rows)")

update_tab("Region_Summary", region_summary)
update_tab("Store_Summary", store_summary_display)
update_tab("Daily_Stock_On_Hand", daily_pivot)

# =========================================================
# 7. APPLY AUTOMATED STRUCTURED STYLING & CONDITIONAL HIGHLIGHTS
# =========================================================
def apply_dashboard_styles():
    print("🎨 Applying Structured Formatting & Conditional Highlights...")
    
    reqs = []
    
    ws_reg = spreadsheet.worksheet("Region_Summary")
    ws_sto = spreadsheet.worksheet("Store_Summary")
    ws_day = spreadsheet.worksheet("Daily_Stock_On_Hand")
    
    id_reg = ws_reg.id
    id_sto = ws_sto.id
    id_day = ws_day.id
    
    NAVY_HEADER = {"red": 0.12, "green": 0.23, "blue": 0.37}
    WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}
    
    LIGHT_RED = {"red": 0.98, "green": 0.80, "blue": 0.80}    # < 30% (#FADBD8)
    LIGHT_YELLOW = {"red": 0.99, "green": 0.95, "blue": 0.78} # < 50% (#FCF3CF)
    LIGHT_GREEN = {"red": 0.83, "green": 0.93, "blue": 0.83}  # >= 50% (#D4EFDF)

    def create_conditional_rules(sheet_id, start_row, end_row, start_col, end_col):
        r_range = {
            "sheetId": sheet_id,
            "startRowIndex": start_row,
            "endRowIndex": end_row,
            "startColumnIndex": start_col,
            "endColumnIndex": end_col
        }
        return [
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [r_range],
                        "booleanRule": {
                            "condition": {
                                "type": "NUMBER_LESS",
                                "values": [{"userEnteredValue": "0.30"}]
                            },
                            "format": {"backgroundColor": LIGHT_RED}
                        }
                    },
                    "index": 0
                }
            },
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [r_range],
                        "booleanRule": {
                            "condition": {
                                "type": "NUMBER_LESS",
                                "values": [{"userEnteredValue": "0.50"}]
                            },
                            "format": {"backgroundColor": LIGHT_YELLOW}
                        }
                    },
                    "index": 1
                }
            },
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [r_range],
                        "booleanRule": {
                            "condition": {
                                "type": "NUMBER_GREATER_THAN_EQ",
                                "values": [{"userEnteredValue": "0.50"}]
                            },
                            "format": {"backgroundColor": LIGHT_GREEN}
                        }
                    },
                    "index": 2
                }
            }
        ]

    # --- STORE SUMMARY FORMATTING ---
    sto_row_count = len(store_summary_display) + 1
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": id_sto, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": NAVY_HEADER,
                    "textFormat": {"bold": True, "foregroundColor": WHITE},
                    "horizontalAlignment": "CENTER"
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"
        }
    })
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": id_sto, "startRowIndex": 1, "endRowIndex": sto_row_count, "startColumnIndex": 2, "endColumnIndex": 8},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}},
            "fields": "userEnteredFormat.numberFormat"
        }
    })
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": id_sto, "startRowIndex": 1, "endRowIndex": sto_row_count, "startColumnIndex": 8, "endColumnIndex": 10},
            "cell": {
                "userEnteredFormat": {
                    "numberFormat": {"type": "PERCENT", "pattern": "0.00%"},
                    "horizontalAlignment": "CENTER"
                }
            },
            "fields": "userEnteredFormat(numberFormat,horizontalAlignment)"
        }
    })
    reqs.append({"updateSheetProperties": {"properties": {"sheetId": id_sto, "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}})
    reqs.extend(create_conditional_rules(id_sto, 1, sto_row_count, 8, 10))

    # --- REGION SUMMARY FORMATTING ---
    reg_col_count = len(region_summary.columns)
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": id_reg, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": NAVY_HEADER,
                    "textFormat": {"bold": True, "foregroundColor": WHITE},
                    "horizontalAlignment": "CENTER"
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"
        }
    })
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": id_reg, "startRowIndex": 1, "endRowIndex": 7, "startColumnIndex": 1, "endColumnIndex": reg_col_count},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}},
            "fields": "userEnteredFormat.numberFormat"
        }
    })
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": id_reg, "startRowIndex": 7, "endRowIndex": 9, "startColumnIndex": 1, "endColumnIndex": reg_col_count},
            "cell": {
                "userEnteredFormat": {
                    "numberFormat": {"type": "PERCENT", "pattern": "0.00%"},
                    "horizontalAlignment": "CENTER"
                }
            },
            "fields": "userEnteredFormat(numberFormat,horizontalAlignment)"
        }
    })
    reqs.extend(create_conditional_rules(id_reg, 7, 9, 1, reg_col_count))

    # --- DAILY STOCK ON HAND FORMATTING ---
    day_row_count = len(daily_pivot) + 1
    day_col_count = len(daily_pivot.columns)
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": id_day, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": NAVY_HEADER,
                    "textFormat": {"bold": True, "foregroundColor": WHITE},
                    "horizontalAlignment": "CENTER"
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"
        }
    })
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": id_day, "startRowIndex": 1, "endRowIndex": day_row_count, "startColumnIndex": 2, "endColumnIndex": day_col_count},
            "cell": {
                "userEnteredFormat": {
                    "numberFormat": {"type": "PERCENT", "pattern": "0.00%"},
                    "horizontalAlignment": "CENTER"
                }
            },
            "fields": "userEnteredFormat(numberFormat,horizontalAlignment)"
        }
    })
    reqs.append({"updateSheetProperties": {"properties": {"sheetId": id_day, "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 2}}, "fields": "gridProperties(frozenRowCount,frozenColumnCount)"}})
    reqs.extend(create_conditional_rules(id_day, 1, day_row_count, 2, day_col_count))

    try:
        spreadsheet.batch_update({"requests": reqs})
        print("✨ Structured styles and conditional highlights applied successfully!")
    except Exception as err:
        print(f"⚠️ Batch formatting warning: {err}")

apply_dashboard_styles()

print("🏁 Final Pipeline Execution Complete!")
