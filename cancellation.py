# =========================================================
# 🔥 IMPORTS
# =========================================================
import os, json, time, jwt, requests
import pandas as pd
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
import smtplib
from email.mime.text import MIMEText
import numpy as np

print("🚀 Cancellation Script Started")

# =========================================================
# 🔐 AUTH
# =========================================================
API_KEY = os.environ["API_KEY"]
SECRET_KEY = os.environ["SECRET_KEY"]

def get_token():
    payload = {
        "iss": API_KEY,
        "iat": int(time.time())
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def headers():
    return {
        "x-api-key": API_KEY,
        "x-api-token": get_token(),
        "content-type": "application/json"
    }

# =========================================================
# 🔐 GOOGLE SHEETS
# =========================================================
creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_CREDENTIALS"]),
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)

client = gspread.authorize(creds)
spreadsheet = client.open("Cancellation Dashboard")

raw_ws = spreadsheet.worksheet("Cancellation_Tracker")
try:
    alert_ws = spreadsheet.worksheet("Alert_History")
except Exception:
    alert_ws = spreadsheet.add_worksheet(
        title="Alert_History",
        rows=100000,
        cols=20
    )
mapping_ws = spreadsheet.worksheet("Store_Mapping")
reason_ws = spreadsheet.worksheet("Reason_Map")

reason_data = reason_ws.get_all_records()
reason_map_df = pd.DataFrame(reason_data)

print("✅ Reason Map Loaded:", len(reason_map_df))
print("✅ Google Connected")

# =========================================================
# 📅 DATE
# =========================================================
now = datetime.utcnow() + timedelta(hours=5, minutes=30)

def get_business_day(now):
    return (now - timedelta(days=1)).date() if now.hour < 6 else now.date()

today = get_business_day(now).strftime("%Y-%m-%d")
print("📅 Business Day:", today)

# =========================================================
# 📡 FETCH BRANCHES
# =========================================================
b_resp = requests.get(
    "https://api.ristaapps.com/v1/branch/list",
    headers=headers()
)

data = b_resp.json()

if isinstance(data, dict):
    branch_data = data.get("data", [])
else:
    branch_data = data

branches = [
    b.get("branchCode")
    for b in branch_data
    if b.get("status") == "Active"
]

print("🏪 Branch count:", len(branches))

# =========================================================
# 📡 FETCH SALES DATA (LATEST 5 MINUTES)
# =========================================================
df_list = []

for branch in branches:
    try:
        to_time = datetime.utcnow()
        from_time = to_time - timedelta(minutes=5)

        from_time_str = from_time.strftime("%Y-%m-%d %H:%M:%S")
        to_time_str = to_time.strftime("%Y-%m-%d %H:%M:%S")

        r = requests.get(
            "https://api.ristaapps.com/v1/sales/page",
            headers=headers(),
            params={
                "branch": branch,
                "day": today,
                "fromDate": from_time_str,
                "toDate": to_time_str,
                "page": 1,
                "pageSize": 500,
                "sort": "desc"
            },
            timeout=30
        )

        if r.status_code != 200:
            continue

        resp = r.json()
        data = []

        if isinstance(resp, dict):
            if "data" in resp:
                if isinstance(resp["data"], dict):
                    data = resp["data"].get("rows", [])
                elif isinstance(resp["data"], list):
                    data = resp["data"]
        elif isinstance(resp, list):
            data = resp

        if data:
            temp_df = pd.json_normalize(data)
            if "branchName" not in temp_df.columns:
                temp_df["branchName"] = branch
            df_list.append(temp_df)

    except Exception as e:
        print(f"❌ Error {branch}: {e}")

# =========================================================
# 🔗 COMBINE ALL DATA
# =========================================================
if not df_list:
    print("❌ No data fetched")
    exit()

df = pd.concat(df_list, ignore_index=True)
print("✅ Total rows fetched:", len(df))

# =========================================================
# 🔻 FILTER CANCELLED ONLY
# =========================================================
status_col = None
possible_status_cols = ["status", "orderStatus", "invoiceStatus"]

for col in possible_status_cols:
    if col in df.columns:
        status_col = col
        break

if not status_col:
    print("❌ Status column not found")
    exit()

cancel_df = df[
    df[status_col]
    .astype(str)
    .str.lower()
    .isin(["voided", "cancel"])
].copy()

print("🚨 Cancellation Found:", len(cancel_df))

if cancel_df.empty:
    print("✅ No cancellations in this window.")
    exit()

# =========================================================
# 🧾 CANCEL REASON EXTRACTION (RISTA API)
# =========================================================
possible_reason_cols = [
    "statusInfo.reason",
    "statusInfo.sourceReason",
    "statusInfo.remarks"
]

cancel_df["cancelReason"] = ""
for col in possible_reason_cols:
    if col in cancel_df.columns:
        cancel_df["cancelReason"] = (
            cancel_df[col]
            .fillna("")
            .astype(str)
            .str.strip()
        )
        if (cancel_df["cancelReason"] != "").any():
            break

# Print unique reasons from Rista API
print("\n" + "="*50)
print("📌 UNIQUE CANCELLATION REASONS FROM RISTA API:")
print("="*50)
unique_reasons = cancel_df["cancelReason"].value_counts()
for reason, count in unique_reasons.items():
    print(f" • '{reason}' -> Count: {count}")
print("="*50 + "\n")

# =========================================================
# 🔁 STANDARDIZE INVOICE NUMBER
# =========================================================
invoice_col = None
possible_invoice_cols = ["invoiceNumber", "invoiceNo", "orderId", "billNo"]

for col in possible_invoice_cols:
    if col in cancel_df.columns:
        invoice_col = col
        break

if not invoice_col:
    print("❌ Invoice column not found")
    exit()

cancel_df["invoiceNumber"] = cancel_df[invoice_col].astype(str).str.strip()

# =========================================================
# 🧩 STORE MAPPING & FILTER COCO STORES
# =========================================================
mapping_data = mapping_ws.get_all_values()
headers_map = [h.strip() for h in mapping_data[0]]
mapping_df = pd.DataFrame(mapping_data[1:], columns=headers_map)

coco_stores = (
    mapping_df[
        mapping_df["Ownership"]
        .astype(str)
        .str.upper()
        .eq("COCO")
    ]["Store Name"]
    .dropna()
    .unique()
    .tolist()
)

final_df = cancel_df[
    cancel_df["branchName"]
    .astype(str)
    .isin(coco_stores)
].copy()

print(f"✅ COCO cancellations: {len(final_df)}")
if final_df.empty:
    print("✅ No COCO cancellations to process.")
    exit()

# =========================================================
# 🗺️ MERGE REASON MAP (SINGLE MERGE)
# =========================================================
reason_map = reason_map_df[["Reason (raw, contains)", "Bucket", "Notes"]].copy()

final_df["Cancel_Reason"] = final_df["cancelReason"].fillna("Unknown")
final_df["Cancel_Group"] = "Other"
final_df["RDC_Flag"] = "No"

for _, r in reason_map.iterrows():
    keyword = str(r["Reason (raw, contains)"]).lower().strip()
    if not keyword:
        continue

    mask = (
        final_df["Cancel_Reason"]
        .str.lower()
        .str.contains(keyword, na=False)
    )

    final_df.loc[mask, "Cancel_Group"] = r["Bucket"]
    if "RDC" in str(r["Notes"]).upper():
        final_df.loc[mask, "RDC_Flag"] = "Yes"

# Filter only Restaurant Faults (RDC)
rdc_df = final_df[final_df["RDC_Flag"] == "Yes"].copy()
print(f"✅ RDC (Restaurant Fault) COCO cancellations: {len(rdc_df)}")

if rdc_df.empty:
    print("✅ No restaurant-fault cancellations found.")
    exit()

# =========================================================
# 🚫 DEDUPLICATE AGAINST ALERT HISTORY
# =========================================================
try:
    existing_alert_data = alert_ws.get_all_records()
    existing_df = pd.DataFrame(existing_alert_data)
except Exception:
    existing_df = pd.DataFrame(columns=["invoiceNumber"])

if not existing_df.empty and "invoiceNumber" in existing_df.columns:
    alerted_invoices = set(existing_df["invoiceNumber"].astype(str).str.strip())
    rdc_df = rdc_df[~rdc_df["invoiceNumber"].isin(alerted_invoices)].copy()

print(f"New Unalerted RDC Cancellations: {len(rdc_df)}")
if rdc_df.empty:
    print("✅ All cancellations already alerted/tracked.")
    exit()

# =========================================================
# 📊 SAVE TRACKING DATA (Cancellation_Tracker)
# =========================================================
rdc_df["createdAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# Ensure netAmount exists
if "netAmount" not in rdc_df.columns:
    if "billAmount" in rdc_df.columns:
        rdc_df["netAmount"] = rdc_df["billAmount"]
    elif "totalAmount" in rdc_df.columns:
        rdc_df["netAmount"] = rdc_df["totalAmount"]
    else:
        rdc_df["netAmount"] = 0

tracking_df = rdc_df[[
    "invoiceNumber",
    "branchName",
    "channel",
    "Cancel_Reason",
    "netAmount",
    "createdAt"
]].copy()

tracking_df = (
    tracking_df
    .replace([np.inf, -np.inf], 0)
    .fillna("")
    .astype(str)
)

existing_tracker_rows = raw_ws.get_all_values()
if len(existing_tracker_rows) == 0:
    raw_ws.append_row(tracking_df.columns.tolist())

raw_ws.append_rows(
    tracking_df.values.tolist(),
    value_input_option="USER_ENTERED"
)

# Update Alert_History so it won't process again
alert_history_to_add = rdc_df[["invoiceNumber"]].astype(str).values.tolist()
alert_ws.append_rows(
    alert_history_to_add,
    value_input_option="USER_ENTERED"
)

print("✅ Cancellation tracker & alert history updated successfully.")
print("🎉 Flow Completed")
