# =========================================================
# PAYMENT MODE PERFORMANCE DASHBOARD (STANDALONE PIPELINE)
# =========================================================
# Purpose:
# 1. Fetches raw transaction and invoice records from Rista API
# 2. Reconciles Tender Payments with Billed Amount & Fallback Channels
# 3. Aggregates Gross Amount and Discounts (item_discountAmount)
# 4. Groups at Day + Brand + Region + Store Name + Payment Mode level
# 5. Pushes clean reconciled data to Google Sheets ('Payment_Raw_Data' tab)
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

print("🚀 Payment Dashboard Pipeline Started")

# =========================================================
# CONFIGURATION & CREDENTIALS
# =========================================================

API_KEY = os.environ["API_KEY"]
SECRET_KEY = os.environ["SECRET_KEY"]
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1ldgnNMdeubDx_ImtCC1uCD7FGz8gt_edmWEkmO_xRNk")
SALES_URL = "https://api.ristaapps.com/v1/sales/page"

# Target canonical payment modes
TARGET_MODES = [
    "Swiggy", "Zomato", "Pine Labs - UPI Sale", "UPI", "Cash",
    "Swiggy Dine out", "Amazon Pay", "Pine Labs - Card", "Card",
    "OWNLY", "Zomato Gold", "magicpin", "HOGR", "Bitsila",
    "Pine Labs - other", "EazyDiner"
]
TARGET_MODE_LOOKUP = {m.lower().replace(" ", "").replace("-", ""): m for m in TARGET_MODES}

# =========================================================
# RISTA API AUTHENTICATION
# =========================================================

def get_token():
    payload = {
        "iss": API_KEY,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def api_headers():
    return {
        "x-api-key": API_KEY,
        "x-api-token": get_token(),
        "content-type": "application/json",
    }

# =========================================================
# GOOGLE SHEETS AUTHENTICATION
# =========================================================

creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_CREDENTIALS"]),
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ],
)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SPREADSHEET_ID)
print("✅ Connected to Google Sheets Master Workbook")

# =========================================================
# BUSINESS DATE / TIME WINDOW
# =========================================================

IST = ZoneInfo("Asia/Kolkata")
now = datetime.now(IST)

# Business date starts at 09:00 AM IST
if now.hour < 9:
    business_date = now.date() - timedelta(days=1)
else:
    business_date = now.date()

# Month-to-date window (1st of the active month to today)
month_start_date = business_date.replace(day=1)

print(f"🕒 Current Execution Time: {now.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"📅 Active Business Date: {business_date}")
print(f"📅 Fetching Month-to-Date Window: {month_start_date} to {business_date}")

# =========================================================
# LOAD HELP SHEET (STORE & REGION METADATA)
# =========================================================

help_ws = spreadsheet.worksheet("Help Sheet")
help_data = help_ws.get_all_values()

if not help_data:
    raise RuntimeError("Help Sheet is empty")

help_rows = []
for row in help_data[1:]:
    row = list(row)
    if len(row) < 7:
        row.extend([""] * (7 - len(row)))
    help_rows.append([
        row[0],  # branchCode
        row[1],  # Store Name
        row[2],  # Ownership
        row[3],  # Region
        row[5],  # Channel
        row[6],  # Source
    ])

help_df = pd.DataFrame(
    help_rows,
    columns=["branchCode", "Store Name", "Ownership", "Region", "Channel", "Source"],
)
help_df["branchCode"] = help_df["branchCode"].astype(str).str.strip()
help_df["Ownership"] = help_df["Ownership"].astype(str).str.upper().str.strip()
help_df["Region"] = help_df["Region"].astype(str).str.strip()

# Target COCO Branches
help_df = help_df[help_df["Ownership"] == "COCO"].copy()
branches = help_df["branchCode"].replace("", np.nan).dropna().unique().tolist()

print(f"🏪 Loaded Active Branches ({len(branches)} COCO stores)")

# =========================================================
# FETCH RISTA SALES RECORDS
# =========================================================

def fetch_sales_data(target_date):
    """Fetches full transaction payload including line items and payment arrays."""
    all_records = []
    date_str = target_date.strftime("%Y-%m-%d")

    for branch in branches:
        params = {
            "branch": branch,
            "day": date_str,
            "page": 1,
            "limit": 5000,
        }
        try:
            res = requests.get(SALES_URL, headers=api_headers(), params=params, timeout=180)
            if res.status_code != 200:
                continue

            payload = res.json()
            data = payload.get("data", [])
            if not data:
                continue

            df = pd.json_normalize(data)
            if "branchCode" not in df.columns:
                df["branchCode"] = df.get("branch", branch)
            df["invoiceDay"] = date_str
            all_records.append(df)
        except Exception as e:
            print(f"⚠ Branch {branch} fetch error on {date_str}: {e}")

    if not all_records:
        return pd.DataFrame()
    return pd.concat(all_records, ignore_index=True)

# Fetch Month-To-Date (MTD) dates so the web app has full historical view
date_range = [month_start_date + timedelta(days=x) for x in range((business_date - month_start_date).days + 1)]
print(f"📥 Fetching data across {len(date_range)} days...")

raw_sales_list = []
for dt in date_range:
    day_df = fetch_sales_data(dt)
    if not day_df.empty:
        raw_sales_list.append(day_df)

if not raw_sales_list:
    print("❌ No transaction records retrieved for the date window.")
    exit(0)

raw_sales_df = pd.concat(raw_sales_list, ignore_index=True)
print(f"✅ Total Raw Invoices Fetched: {len(raw_sales_df)}")

# Filter for CLOSED status only
if "status" in raw_sales_df.columns:
    raw_sales_df["status"] = raw_sales_df["status"].astype(str).str.upper().str.strip()
    raw_sales_df = raw_sales_df[raw_sales_df["status"] == "CLOSED"].copy()

# =========================================================
# PROCESS PAYMENT & SALES DATA ENGINE
# =========================================================

def normalize_mode(raw_mode):
    if not raw_mode or pd.isna(raw_mode):
        return "Cash"
    raw_clean = str(raw_mode).strip()
    key = raw_clean.lower().replace(" ", "").replace("-", "")
    return TARGET_MODE_LOOKUP.get(key, raw_clean)

def process_payment_records(df, help_df_ref):
    if df.empty:
        return pd.DataFrame()

    temp_df = df.copy()

    # 1. Standardize invoice date
    date_col = next((c for c in ["invoiceDay", "date"] if c in temp_df.columns), None)
    temp_df["Date"] = temp_df[date_col].astype(str) if date_col else business_date.strftime("%Y-%m-%d")

    # 2. Standardize Channel and Brand
    channel_col = next((c for c in ["channel", "Channel", "orderSource", "source", "channelName"] if c in temp_df.columns), None)
    temp_df["_clean_channel"] = temp_df[channel_col].astype(str).fillna("In Store") if channel_col else "In Store"

    brand_col = next((c for c in ["brandName", "Brand"] if c in temp_df.columns), None)
    temp_df["_clean_brand"] = temp_df[brand_col].astype(str).str.strip().fillna("Frozen Bottle") if brand_col else "Frozen Bottle"

    branch_col = next((c for c in ["branchCode", "branch"] if c in temp_df.columns), None)
    temp_df["_clean_branch"] = temp_df[branch_col].astype(str).str.strip().fillna("") if branch_col else ""

    # 3. Extract Invoice-Level Gross & Discount from nested items
    gross_list = []
    disc_list = []

    for _, row in temp_df.iterrows():
        inv_gross = 0.0
        inv_disc = 0.0
        items_val = row.get("items")
        if isinstance(items_val, str):
            try:
                items_val = ast.literal_eval(items_val)
            except:
                items_val = None

        if isinstance(items_val, list):
            for item in items_val:
                if isinstance(item, dict):
                    g = item.get("item_grossAmount") or item.get("grossAmount") or 0
                    d = item.get("item_discountAmount") or item.get("discountAmount") or item.get("item_netDiscountAmount") or 0
                    inv_gross += float(pd.to_numeric(g, errors="coerce") or 0)
                    inv_disc += abs(float(pd.to_numeric(d, errors="coerce") or 0))

        # Fallback to order-level billed amount if items are missing
        billed = row.get("billedAmount") or row.get("billAmount") or row.get("totalAmount") or 0
        billed_num = float(pd.to_numeric(billed, errors="coerce") or 0)

        if inv_gross <= 0:
            inv_gross = billed_num

        gross_list.append(inv_gross)
        disc_list.append(inv_disc)

    temp_df["Gross"] = gross_list
    temp_df["Discount"] = disc_list

    billed_col = next((c for c in ["billedAmount", "billAmount", "totalAmount"] if c in temp_df.columns), None)
    temp_df["_billed_num"] = pd.to_numeric(temp_df[billed_col], errors="coerce").fillna(0) if billed_col else temp_df["Gross"]

    # Deduplicate by invoiceNumber
    unique_orders = temp_df.drop_duplicates(subset=["invoiceNumber"]).copy()

    # 4. Explode payments array
    rows_expanded = []
    for _, row in unique_orders.iterrows():
        inv_no = str(row["invoiceNumber"]).strip()
        brand = row["_clean_brand"]
        branch = row["_clean_branch"]
        dt = row["Date"]
        billed = float(row["_billed_num"])
        gross = float(row["Gross"])
        disc = float(row["Discount"])
        channel = str(row["_clean_channel"]).lower()

        p_list = row.get("payments")
        if isinstance(p_list, str):
            try:
                p_list = ast.literal_eval(p_list)
            except:
                p_list = None

        if isinstance(p_list, list) and len(p_list) > 0:
            for p in p_list:
                if isinstance(p, dict):
                    m = p.get("mode")
                    amt = float(p.get("amount", 0))
                    rows_expanded.append({
                        "invoiceNumber": inv_no,
                        "brandName": brand,
                        "branchCode": branch,
                        "Date": dt,
                        "Payment Mode": normalize_mode(m),
                        "Value": amt,
                        "Gross": gross,
                        "Discount": disc
                    })
        else:
            # Fallback tender mode based on channel
            fallback_mode = "Cash"
            if "swiggy" in channel:
                fallback_mode = "Swiggy"
            elif "zomato" in channel:
                fallback_mode = "Zomato"
            elif "ownly" in channel:
                fallback_mode = "OWNLY"

            rows_expanded.append({
                "invoiceNumber": inv_no,
                "brandName": brand,
                "branchCode": branch,
                "Date": dt,
                "Payment Mode": fallback_mode,
                "Value": billed,
                "Gross": gross,
                "Discount": disc
            })

    pay_df = pd.DataFrame(rows_expanded)
    if pay_df.empty:
        return pd.DataFrame()

    # 5. Net out customer change (e.g., 100 Cash - 59 Change = 41 Cash)
    net_inv_pay = pay_df.groupby(
        ["invoiceNumber", "brandName", "branchCode", "Date", "Payment Mode"],
        as_index=False
    ).agg({
        "Value": "sum",
        "Gross": "first",
        "Discount": "first"
    })
    net_inv_pay = net_inv_pay[net_inv_pay["Value"] > 0].copy()

    # 6. Merge Store Name & Region from help_df
    meta_cols = help_df_ref[["branchCode", "Store Name", "Region"]].drop_duplicates("branchCode")
    merged_df = net_inv_pay.merge(meta_cols, on="branchCode", how="left")
    merged_df["Store Name"] = merged_df["Store Name"].fillna(merged_df["branchCode"])
    merged_df["Region"] = merged_df["Region"].fillna("Other")

    # 7. Pre-Group at Day + Brand + Region + Store Name + Payment Mode level
    aggregated_df = merged_df.groupby(
        ["Date", "brandName", "Region", "Store Name", "Payment Mode"],
        as_index=False
    ).agg(
        Value=("Value", "sum"),
        Txn=("invoiceNumber", "nunique"),
        Gross=("Gross", "sum"),
        Discount=("Discount", "sum")
    ).rename(columns={"brandName": "Brand"})

    aggregated_df["Value"] = aggregated_df["Value"].round(2)
    aggregated_df["Gross"] = aggregated_df["Gross"].round(2)
    aggregated_df["Discount"] = aggregated_df["Discount"].round(2)

    # Sort descending by value per day/store
    aggregated_df = aggregated_df.sort_values(
        by=["Date", "Brand", "Store Name", "Value"],
        ascending=[True, True, True, False]
    )

    return aggregated_df[[
        "Date", "Brand", "Region", "Store Name", "Payment Mode", "Value", "Txn", "Gross", "Discount"
    ]]

# =========================================================
# EXECUTE AGGREGATION & SYNC TO GOOGLE SHEETS
# =========================================================

print("🔄 Processing and Reconciling Payment Records...")
payment_export_df = process_payment_records(raw_sales_df, help_df)

if not payment_export_df.empty:
    try:
        try:
            payment_ws = spreadsheet.worksheet("Payment_Raw_Data")
        except gspread.exceptions.WorksheetNotFound:
            payment_ws = spreadsheet.add_worksheet(title="Payment_Raw_Data", rows=50000, cols=15)
            print("🛠 Created new sheet tab: 'Payment_Raw_Data'")

        payment_ws.clear()
        payment_export_df["Date"] = payment_export_df["Date"].astype(str)
        payment_matrix = [payment_export_df.columns.values.tolist()] + payment_export_df.values.tolist()
        payment_ws.update("A1", payment_matrix)

        total_val = payment_export_df["Value"].sum()
        total_txns = payment_export_df["Txn"].sum()
        print(f"✅ Success! Pushed {len(payment_export_df)} store-day-mode rows to 'Payment_Raw_Data'.")
        print(f"💰 Total Reconciled Value: ₹{total_val:,.2f} across {total_txns:,} Transactions.")
    except Exception as e:
        print(f"❌ Error updating Google Sheets: {e}")
        raise
else:
    print("⚠ No payment records produced.")

print("🏁 Payment Pipeline Execution Finished Successfully.")
