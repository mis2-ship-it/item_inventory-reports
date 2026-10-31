# =========================================================
# IMPORTS
# =========================================================

import os
import json
import time
import jwt
import requests
import pandas as pd
import smtplib

from datetime import datetime, timedelta

import gspread
from google.oauth2.service_account import Credentials

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

print("🚀 Sales Script Started")

# =========================================================
# AUTH
# =========================================================

API_KEY = os.environ["API_KEY"]
SECRET_KEY = os.environ["SECRET_KEY"]

def get_token():
    payload = {
        "iss": API_KEY,
        "iat": int(time.time())
    }

    return jwt.encode(
        payload,
        SECRET_KEY,
        algorithm="HS256"
    )


def headers():
    return {
        "x-api-key": API_KEY,
        "x-api-token": get_token(),
        "content-type": "application/json"
    }

# =========================================================
# GOOGLE AUTH
# =========================================================

creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_CREDENTIALS"]),
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)

client = gspread.authorize(creds)

spreadsheet = client.open_by_key(
    "19z6KkVBFoLC33_wcNqVhDLyQEC2dDQ8YQE0gE38BhVg"
)

print("✅ Connected Google Sheet")

# =========================================================
# DATE
# =========================================================

fetch_date = (
    datetime.now() - timedelta(days=1)
).strftime("%Y-%m-%d")

print("📅 Fetching Yesterday Data:", fetch_date)



# =========================================================
# HELP SHEET
# =========================================================

help_ws = spreadsheet.worksheet("Help Sheet")

# GET RAW DATA
help_data = help_ws.get()

if not help_data:
    print("❌ Help Sheet Empty")
    exit()

# =========================================================
# HEADER
# =========================================================

raw_headers = help_data[0]

safe_headers = []

for i, h in enumerate(raw_headers):

    h = str(h).strip()

    # blank header fix
    if h == "":
        h = f"blank_col_{i}"

    # duplicate header fix
    if h in safe_headers:
        h = f"{h}_{i}"

    safe_headers.append(h)

header_len = len(safe_headers)

print("✅ Header Count:", header_len)

# =========================================================
# NORMALIZE ROWS
# =========================================================

normalized_rows = []

for row in help_data[1:]:

    row = list(row)

    # ADD MISSING COLUMNS
    if len(row) < header_len:
        row.extend([""] * (header_len - len(row)))

    # REMOVE EXTRA COLUMNS
    elif len(row) > header_len:
        row = row[:header_len]

    normalized_rows.append(row)

# =========================================================
# CREATE DATAFRAME
# =========================================================

help_df = pd.DataFrame(
    normalized_rows,
    columns=safe_headers
)

# =========================================================
# CLEAN COLUMN NAMES
# =========================================================

help_df.columns = (
    help_df.columns
    .astype(str)
    .str.strip()
    .str.lower()
    .str.replace(" ", "")
)

print("✅ Help Sheet Loaded")
print("📋 Columns:", help_df.columns.tolist())

# =========================================================
# CHECK ownership COLUMN
# =========================================================

if "ownership" not in help_df.columns:

    print("❌ ownership column missing")
    print(help_df.columns.tolist())
    exit()

# =========================================================
# FILTER COCO
# =========================================================

help_df = help_df[
    help_df["ownership"]
    .astype(str)
    .str.upper()
    .str.strip() == "COCO"
].copy()

print("✅ COCO Rows:", len(help_df))

# =========================================================
# RENAME COLUMNS
# =========================================================

help_df = help_df.rename(columns={
    "branchcode": "branchCode",
    "storename": "Store Name",
    "amemail": "AM Email",
    "rmemail": "RM Email",
    "amname": "AM Name",
    "ccmail": "CC Mail",
    "region": "Region"
})

# =========================================================
# REQUIRED COLUMNS CHECK
# =========================================================

required_cols = [
    "branchCode",
    "Store Name",
    "AM Email",
    "RM Email",
    "AM Name",
    "CC Mail",
    "Region"
]

missing_cols = [
    c for c in required_cols
    if c not in help_df.columns
]

if missing_cols:

    print("❌ Missing Columns:", missing_cols)
    print("📋 Available Columns:")
    print(help_df.columns.tolist())

    exit()

# =========================================================
# CLEAN BRANCH CODE
# =========================================================

help_df["branchCode"] = (
    help_df["branchCode"]
    .astype(str)
    .str.strip()
)

branches = (
    help_df["branchCode"]
    .dropna()
    .astype(str)
    .str.strip()
    .unique()
    .tolist()
)

print("🏪 COCO Branch Count:", len(branches))

# =========================================================
# SALES API
# =========================================================

sales_url = "https://api.ristaapps.com/v1/sales/page"

def fetch_sales_data(fetch_date):

    all_sales = []

    print(f"\n📦 Fetching Data: {fetch_date}")

    for branch in branches:

        print("Fetching:", branch)

        params = {
            "branch": branch,
            "day": fetch_date
        }

        try:

            response = requests.get(
                sales_url,
                headers=headers(),
                params=params,
                timeout=120
            )

            print("Status:", response.status_code)

            if response.status_code != 200:
                continue

            response_json = response.json()

            data = response_json.get("data", [])

            if not data:
                print("No Data:", branch)
                continue

            df = pd.json_normalize(data)

            # branchCode safe creation
            if "branchCode" not in df.columns:

                if "branch" in df.columns:
                    df["branchCode"] = df["branch"]

                elif "storeCode" in df.columns:
                    df["branchCode"] = df["storeCode"]

                elif "Store Code" in df.columns:
                    df["branchCode"] = df["Store Code"]

            all_sales.append(df)

        except Exception as e:
            print(f"❌ Error {branch}: {e}")

    if not all_sales:
        print("❌ No Sales Data Found")
        return pd.DataFrame()

    final_df = pd.concat(
        all_sales,
        ignore_index=True
    )

    print("✅ Total Rows Fetched:", len(final_df))

    return final_df

# =========================================================
# FETCH DATA
# =========================================================

raw_df = fetch_sales_data(fetch_date)

# =========================================================
# FETCH MTD DATA
# =========================================================

mtd_frames = []

start_date = datetime.strptime(
    fetch_date,
    "%Y-%m-%d"
).replace(day=1)

end_date = datetime.strptime(
    fetch_date,
    "%Y-%m-%d"
)

current_day = start_date

while current_day <= end_date:

    day_str = current_day.strftime("%Y-%m-%d")

    print(f"📅 Fetching MTD : {day_str}")

    temp_df = fetch_sales_data(day_str)

    if not temp_df.empty:
        mtd_frames.append(temp_df)

    current_day += timedelta(days=1)

if len(mtd_frames) > 0:

    raw_mtd_df = pd.concat(
        mtd_frames,
        ignore_index=True
    )

else:

    raw_mtd_df = pd.DataFrame()

print(
    "✅ MTD Rows:",
    len(raw_mtd_df)
)

# =========================================================
# PROCESS DATA
# =========================================================

def process_sales_data(df):

    if df.empty:
        print("❌ Empty DataFrame")
        return pd.DataFrame()

    final_df = df.copy()

    # =====================================================
    # SAFE branchCode
    # =====================================================

    if "branchCode" not in final_df.columns:

        print("❌ branchCode Missing")
        print(final_df.columns.tolist())

        return pd.DataFrame()

    final_df["branchCode"] = (
        final_df["branchCode"]
        .astype(str)
        .str.strip()
    )

    # =====================================================
    # 🌟 FIX: Explode and Normalize Nested Items
    # =====================================================
    if "items" in final_df.columns:
        # Explode the list of items into separate rows
        final_df = final_df.explode("items")
        
        # Normalize the dictionary items and prefix them with 'item_'
        items_df = pd.json_normalize(final_df["items"]).add_prefix("item_")
        
        # Reset indices to align and concatenate back
        final_df = final_df.reset_index(drop=True)
        items_df = items_df.reset_index(drop=True)
        final_df = pd.concat([final_df, items_df], axis=1)

    # =====================================================
    # FIX CHANNEL COLUMN
    # =====================================================

    # =====================================================
    # FIX CHANNEL COLUMN
    # =====================================================

    if "Channel" not in final_df.columns:
        if "channel" in final_df.columns:
            final_df["Channel"] = final_df["channel"]

    # =====================================================
    # FILTER CHANNELS
    # =====================================================

    allowed_channels = [
        "Zomato Frozen Bottle",
        "Zomato Boba Bar",
        "Zomato Madno",
        "Swiggy Frozen Bottle",
        "Swiggy Boba Bar",
        "Swiggy Madno",
    ]

    final_df = final_df[
        final_df["Channel"]
        .astype(str)
        .isin(allowed_channels)
    ].copy()

    return final_df

# =========================================================
# PROCESS DATASET
# =========================================================

sales_df = process_sales_data(raw_df)

if sales_df.empty:
    print("❌ No Processed Data Available")
    exit()

mtd_df = process_sales_data(raw_mtd_df)

if mtd_df.empty:
    print("❌ No Processed Data Available")
    exit()

# =========================================================
# MERGE HELP SHEET
# =========================================================

help_merge = help_df[
    [
        "branchCode",
        "Store Name",
        "AM Email",
        "RM Email",
        "AM Name",
        "CC Mail",
        "Region"
    ]
].copy()

sales_df = sales_df.merge(
    help_merge,
    on="branchCode",
    how="left"
)

mtd_df = mtd_df.merge(
    help_merge,
    on="branchCode",
    how="left"
)

print("✅ Help Sheet Merged")

# =========================================================
# DATE FILTER
# =========================================================

if "invoiceDay" in sales_df.columns:

    sales_df = sales_df[
        sales_df["invoiceDay"]
        .astype(str) == fetch_date
    ].copy()

print("✅ Orders:", len(sales_df))

# =========================================================
# COLUMN STANDARDIZATION
# =========================================================

print("📋 API Columns:")
print(sales_df.columns.tolist())

# =========================================================
# CREATE REQUIRED TIMESTAMP COLUMNS
# =========================================================

# ORDER TIME (Correct = invoiceDate)
if "invoiceDate" in sales_df.columns:

    sales_df["Order Time"] = pd.to_datetime(
        sales_df["invoiceDate"],
        errors="coerce"
    )

else:

    sales_df["Order Time"] = pd.NaT


# READY TIME
if "orderReadyTimestamp" in sales_df.columns:

    sales_df["Order Ready Time"] = pd.to_datetime(
        sales_df["orderReadyTimestamp"],
        errors="coerce"
    )

else:

    sales_df["Order Ready Time"] = pd.NaT


# DELIVERY TIME (Correct = delivery.deliveryDate)
if "modifiedDate" in sales_df.columns:

    sales_df["Delivery Time"] = pd.to_datetime(
        sales_df["modifiedDate"],
        errors="coerce"
    )

else:

    sales_df["Delivery Time"] = pd.NaT


# =========================================================
# CREATE KPT COLUMN
# =========================================================

print("✅ Calculating KPT")

sales_df["KPT (Mins)"] = (
    (
        sales_df["Order Ready Time"]
        -
        sales_df["Order Time"]
    ).dt.total_seconds() / 60
)


# remove negatives
sales_df["KPT (Mins)"] = (
    sales_df["KPT (Mins)"]
    .clip(lower=0)
    .round(1)
)


# =========================================================
# CREATE O2D COLUMN
# =========================================================

print("✅ Calculating O2D")

sales_df["O2D (Mins)"] = (
    (
        sales_df["Delivery Time"]
        -
        sales_df["Order Time"]
    ).dt.total_seconds() / 60
)

sales_df["O2D (Mins)"] = (
    sales_df["O2D (Mins)"]
    .clip(lower=0)
    .round(1)
)


# =========================================================
# CLEAN NUMERIC VALUES
# =========================================================

sales_df["KPT (Mins)"] = pd.to_numeric(
    sales_df["KPT (Mins)"],
    errors="coerce"
)

sales_df["O2D (Mins)"] = pd.to_numeric(
    sales_df["O2D (Mins)"],
    errors="coerce"
)


# =========================================================
# REMOVE INVALID ROWS
# =========================================================

sales_df = sales_df[
    sales_df["KPT (Mins)"].notna()
].copy()

sales_df = sales_df[
    sales_df["KPT (Mins)"] >= 0
].copy()

print(
    "✅ Valid KPT Rows:",
    len(sales_df)
)



# =====================================================
# MTD KPT / O2D CALCULATION
# =====================================================

if not mtd_df.empty:

    # ORDER TIME
    mtd_df["Order Time"] = pd.to_datetime(
        mtd_df["invoiceDate"],
        errors="coerce"
    )

    # READY TIME
    mtd_df["Order Ready Time"] = pd.to_datetime(
        mtd_df["orderReadyTimestamp"],
        errors="coerce"
    )

    # DELIVERY TIME
    mtd_df["Delivery Time"] = pd.to_datetime(
        mtd_df["modifiedDate"],
        errors="coerce"
    )

    # KPT
    mtd_df["KPT (Mins)"] = (
        (
            mtd_df["Order Ready Time"]
            -
            mtd_df["Order Time"]
        ).dt.total_seconds() / 60
    )

    mtd_df["KPT (Mins)"] = (
        mtd_df["KPT (Mins)"]
        .clip(lower=0)
        .round(1)
    )

    # O2D
    mtd_df["O2D (Mins)"] = (
        (
            mtd_df["Delivery Time"]
            -
            mtd_df["Order Time"]
        ).dt.total_seconds() / 60
    )

    mtd_df["O2D (Mins)"] = (
        mtd_df["O2D (Mins)"]
        .clip(lower=0)
        .round(1)
    )

    mtd_df = mtd_df[
        mtd_df["KPT (Mins)"].notna()
    ].copy()

    print(
        "✅ MTD KPT Rows:",
        len(mtd_df)
    )
    print(
    mtd_df[
        [
            "KPT (Mins)",
            "O2D (Mins)"
        ]
    ].head()
    )



def sla_metrics(df, group_col, metric, sla_limit):

    g = df.groupby(group_col).agg(
        Orders=(metric, "count"),
        Avg=(metric, "mean"),
        Median=(metric, "median"),
        P80=(metric, lambda x: x.quantile(0.80)),
        Breach=(metric, lambda x: (x > sla_limit).sum())
    ).reset_index()

    g["Breach %"] = (g["Breach"] / g["Orders"] * 100).round(1)

    g["Avg"] = g["Avg"].round(1)
    g["Median"] = g["Median"].round(1)
    g["P80"] = g["P80"].round(1)

    return g



# =========================================================
# CATEGORY / ITEM DASHBOARD BUILDER
# =========================================================

def build_item_dashboard(
    ftd_df,
    mtd_df,
    group_col,
    order_method="nunique"
):

    def agg_data(df):

        if order_method == "count":

            orders = (
                df.groupby(group_col)
                .size()
                .rename("Orders")
            )

        else:

            orders = (
                df.groupby(group_col)["invoiceNumber"]
                .nunique()
                .rename("Orders")
            )

        metrics = (
            df.groupby(group_col)
            .agg(
                KPT=("KPT (Mins)", "mean"),
                KPT_P80=(
                    "KPT (Mins)",
                    lambda x: x.quantile(0.80)
                ),
                KPT_Median=(
                    "KPT (Mins)",
                    "median"
                ),
                O2D=("O2D (Mins)", "mean"),
                O2D_P80=(
                    "O2D (Mins)",
                    lambda x: x.quantile(0.80)
                ),
                O2D_Median=(
                    "O2D (Mins)",
                    "median"
                )
            )
        )

        final = (
            pd.concat(
                [orders, metrics],
                axis=1
            )
            .reset_index()
            .round(2)
        )

        return final

    ftd = agg_data(ftd_df)
    mtd = agg_data(mtd_df)

    dashboard = ftd.merge(
        mtd,
        on=group_col,
        how="outer",
        suffixes=("_FTD", "_MTD")
    )

    return dashboard.fillna(0)



# =========================================================
# 1. CATEGORY DASHBOARD
# =========================================================
category_dashboard = build_item_dashboard(
    sales_df, mtd_df, "item_categoryName", order_method="nunique"
)

# =========================================================
# 2. ITEM DASHBOARD
# =========================================================
item_dashboard = build_item_dashboard(
    sales_df, mtd_df, "item_shortName", order_method="count"
)

# =========================================================
# 3. REGION LEVEL CATEGORY AND ITEM DASHBOARDS
# =========================================================
region_category_dashboards = {}
region_item_dashboards = {}

for region in sorted(sales_df["Region"].dropna().unique()):
    ftd_region = sales_df[sales_df["Region"] == region]
    mtd_region = mtd_df[mtd_df["Region"] == region]
    
    # Region + Category
    region_category_dashboards[region] = build_item_dashboard(
        ftd_region, mtd_region, "item_categoryName", order_method="nunique"
    )
    
    # Region + Item
    region_item_dashboards[region] = build_item_dashboard(
        ftd_region, mtd_region, "item_shortName", order_method="count"
    )

# =========================================================
# 🌟 UPDATED: RCA ENGINE POWERED BY P80 METRICS
# =========================================================
def generate_rca_analysis(sales_df, category_dash, item_dash, region_cat_dict, region_item_dict):
    rca_rows = []
    
    # Overall Category Issues (Based on P80)
    cat_issues = category_dash[(category_dash["KPT_P80_FTD"] > 12) | (category_dash["O2D_P80_FTD"] > 35)]
    for _, row in cat_issues.iterrows():
        rca_rows.append({
            "Scope": "Overall",
            "Type": "Category",
            "Element Name": row["item_categoryName"],
            "Orders (FTD)": row["Orders_FTD"],
            "KPT P80 (Mins)": row["KPT_P80_FTD"],
            "O2D P80 (Mins)": row["O2D_P80_FTD"],
            "Primary Issue": "High Prep Time (KPT)" if row["KPT_P80_FTD"] > 12 else "Logistics Delay (O2D)"
        })
        
    # Overall Item Issues (Based on P80)
    item_issues = item_dash[((item_dash["KPT_P80_FTD"] > 12) | (item_dash["O2D_P80_FTD"] > 35)) & (item_dash["Orders_FTD"] >= 5)]
    for _, row in item_issues.iterrows():
        rca_rows.append({
            "Scope": "Overall",
            "Type": "Item",
            "Element Name": row["item_shortName"],
            "Orders (FTD)": row["Orders_FTD"],
            "KPT P80 (Mins)": row["KPT_P80_FTD"],
            "O2D P80 (Mins)": row["O2D_P80_FTD"],
            "Primary Issue": "High Prep Time (KPT)" if row["KPT_P80_FTD"] > 12 else "Logistics Delay (O2D)"
        })

    # Region-wise Category Issues (Based on P80)
    for region, df in region_cat_dict.items():
        issues = df[(df["KPT_P80_FTD"] > 12) | (df["O2D_P80_FTD"] > 35)]
        for _, row in issues.iterrows():
            rca_rows.append({
                "Scope": f"Region: {region}",
                "Type": "Category",
                "Element Name": row["item_categoryName"],
                "Orders (FTD)": row["Orders_FTD"],
                "KPT P80 (Mins)": row["KPT_P80_FTD"],
                "O2D P80 (Mins)": row["O2D_P80_FTD"],
                "Primary Issue": "Target Dropped in Region"
            })
            
    # Region-wise Item Issues (Based on P80)
    for region, df in region_item_dict.items():
        issues = df[((df["KPT_P80_FTD"] > 12) | (df["O2D_P80_FTD"] > 35)) & (df["Orders_FTD"] >= 3)]
        for _, row in issues.iterrows():
            rca_rows.append({
                "Scope": f"Region: {region}",
                "Type": "Item",
                "Element Name": row["item_shortName"],
                "Orders (FTD)": row["Orders_FTD"],
                "KPT P80 (Mins)": row["KPT_P80_FTD"],
                "O2D P80 (Mins)": row["O2D_P80_FTD"],
                "Primary Issue": "Target Dropped in Region"
            })

    rca_df = pd.DataFrame(rca_rows)
    if not rca_df.empty:
        rca_df["_scope_sort"] = rca_df["Scope"].apply(lambda x: 0 if x == "Overall" else 1)
        rca_df["_type_sort"] = rca_df["Type"].apply(lambda x: 0 if x == "Category" else 1)
        rca_df = rca_df.sort_values(
            by=["_scope_sort", "Scope", "_type_sort", "Orders (FTD)"], 
            ascending=[True, True, True, False]
        )
        rca_df = rca_df.drop(columns=["_scope_sort", "_type_sort"])
        
    return rca_df

# Generate RCA DataFrame
rca_dashboard = generate_rca_analysis(
    sales_df, category_dashboard, item_dashboard, region_category_dashboards, region_item_dashboards
)
# =========================================================
# WRITE TO GOOGLE SHEET (WITH CONDITIONAL COLOR HIGHLIGHTS)
# =========================================================

def wipe_gsheet_formats(ws):
    """Clears all old color formatting so previous day colors don't bleed into new data."""
    try:
        ws.spreadsheet.batch_update({
            "requests": [{"updateCells": {"range": {"sheetId": ws.id}, "fields": "userEnteredFormat"}}]
        })
    except:
        pass

def beautify_gsheet_table(ws, df, start_row=1):
    """Applies exact email HTML colors (Red, Yellow, Green) to Google Sheet cells dynamically."""
    formats = []
    
    # Helper to convert col/row index to A1 notation (e.g. 1, 2 -> "B1")
    def get_a1(row, col):
        dividend = col
        column_name = ""
        while dividend > 0:
            modulo = (dividend - 1) % 26
            column_name = chr(65 + modulo) + column_name
            dividend = int((dividend - modulo) / 26)
        return f"{column_name}{row}"
        
    # 1. Format the Table Header (Blue Background, White Text)
    formats.append({
        "range": f"A{start_row}:{get_a1(start_row, len(df.columns))}",
        "format": {
            "backgroundColor": {"red": 0.12, "green": 0.30, "blue": 0.47}, # #1F4E78
            "textFormat": {"foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "bold": True},
            "horizontalAlignment": "CENTER"
        }
    })
    
    # 2. Row by Row SLA Evaluation
    for row_idx, row in enumerate(df.values):
        sheet_row = start_row + 1 + row_idx
        for col_idx, val in enumerate(row):
            col_name = str(df.columns[col_idx])
            sheet_col = col_idx + 1
            
            # Align Category/Item names cleanly to the left and bold them
            if any(x in col_name for x in ["Name", "shortName", "categoryName", "Element Name", "Scope", "Primary Issue"]):
                formats.append({
                    "range": get_a1(sheet_row, sheet_col),
                    "format": {"horizontalAlignment": "LEFT", "textFormat": {"bold": True}}
                })
                continue
            
            # 🌟 FIX: Clean target detection ensuring headers like "KPT P80 (Mins)" match correctly
            col_clean = col_name.upper().replace(" ", "")
            if "KPT" in col_clean or "O2D" in col_clean or any(x in col_clean for x in ["AVG", "P80", "MEDIAN"]):
                try:
                    f_val = float(val)
                    metric = "O2D" if "O2D" in col_clean else "KPT"
                    bg, txt = None, None
                    
                    if metric == "KPT":
                        if f_val < 12: bg, txt = {"red": 0.85, "green": 0.91, "blue": 0.82}, {"red": 0.0, "green": 0.38, "blue": 0.0}
                        elif f_val <= 15: bg, txt = {"red": 1.0, "green": 0.94, "blue": 0.80}, {"red": 0.61, "green": 0.39, "blue": 0.0}
                        else: bg, txt = {"red": 0.95, "green": 0.80, "blue": 0.80}, {"red": 0.61, "green": 0.0, "blue": 0.02}
                    else: # O2D Logic
                        if f_val < 30: bg, txt = {"red": 0.85, "green": 0.91, "blue": 0.82}, {"red": 0.0, "green": 0.38, "blue": 0.0}
                        elif f_val <= 35: bg, txt = {"red": 1.0, "green": 0.94, "blue": 0.80}, {"red": 0.61, "green": 0.39, "blue": 0.0}
                        else: bg, txt = {"red": 0.95, "green": 0.80, "blue": 0.80}, {"red": 0.61, "green": 0.0, "blue": 0.02}
                    
                    if bg:
                        formats.append({
                            "range": get_a1(sheet_row, sheet_col),
                            "format": {"backgroundColor": bg, "textFormat": {"foregroundColor": txt, "bold": True}, "horizontalAlignment": "CENTER"}
                        })
                except:
                    pass
    
    # 3. Apply formatting safely in chunk sizes to respect Google API limits
    for i in range(0, len(formats), 2000):
        try:
            ws.batch_format(formats[i:i+2000])
        except Exception as e:
            print(f"⚠️ Formatting error chunks: {e}")

# =========================================================
# SHEET UPDATE EXECUTION
# =========================================================

def safe_update_sheet(target_spreadsheet, sheet_title, dataframe, start_row=1):
    try:
        try:
            ws = target_spreadsheet.worksheet(sheet_title)
        except gspread.exceptions.WorksheetNotFound:
            ws = target_spreadsheet.add_worksheet(title=sheet_title, rows="1000", cols="20")
            print(f"🛠 Created new worksheet tab: '{sheet_title}'")
        
        # Wipe old styling and clear text
        wipe_gsheet_formats(ws)
        ws.clear()
        
        export_df = dataframe.fillna("").copy()
        data_matrix = [export_df.columns.values.tolist()] + export_df.values.tolist()
        ws.update(data_matrix, "A1")
        
        # Inject the new beautiful highlighting!
        beautify_gsheet_table(ws, export_df, start_row=start_row)
        
        print(f"✅ Tab '{sheet_title}' synchronized & styled successfully.")
    except Exception as sheet_err:
        print(f"❌ Failed to sync tab '{sheet_title}': {str(sheet_err)}")

try:
    # 1. Push Core Categories
    safe_update_sheet(spreadsheet, "Sales Dashboard", category_dashboard)

    # 2. Push organized Troubleshoot Log to RCA workbook
    print("🔗 Connecting specifically to the Target Workbook...")
    rca_workbook = client.open_by_key("1sO0I_0z7z8zLv3t0QyHihxwvWKn1sS2cMxsN3lHiEAw")
    
    if not rca_dashboard.empty:
        safe_update_sheet(rca_workbook, "RCA Analysis", rca_dashboard)
    else:
        print("✅ RCA Log is empty. Pushing clean status row to Google Sheet.")
        all_clear_df = pd.DataFrame([{
            "Scope": "Overall", "Type": "Status Log", "Element Name": "All Categories & Items",
            "Orders (FTD)": 0, "Avg KPT (Mins)": 0, "Avg O2D (Mins)": 0,
            "Primary Issue": "✅ All metrics within targeted SLA parameters yesterday!"
        }])
        safe_update_sheet(rca_workbook, "RCA Analysis", all_clear_df)

    # 3. Create Region-Wise Tabs in Target Workbook
    for region in sorted(sales_df["Region"].dropna().unique()):
        tab_name = f"Region_{region}"
        
        cat_df = region_category_dashboards.get(region, pd.DataFrame()).fillna("")
        item_df = region_item_dashboards.get(region, pd.DataFrame()).fillna("")
        
        combined_rows = []
        
        # Combine Matrices
        combined_rows.append([f"📋 {region} - CATEGORY BREAKDOWN LEVEL OUTPUT", "", "", "", "", "", "", ""])
        combined_rows.append(cat_df.columns.values.tolist())
        combined_rows.extend(cat_df.values.tolist())
        
        combined_rows.extend([[] for _ in range(3)])
        
        combined_rows.append([f"📦 {region} - ITEM BREAKDOWN LEVEL OUTPUT", "", "", "", "", "", "", ""])
        combined_rows.append(item_df.columns.values.tolist())
        combined_rows.extend(item_df.values.tolist())
        
        try:
            try:
                ws = rca_workbook.worksheet(tab_name)
            except gspread.exceptions.WorksheetNotFound:
                ws = rca_workbook.add_worksheet(title=tab_name, rows="2000", cols="20")
            
            # Wipe formats and push data
            wipe_gsheet_formats(ws)
            ws.clear()
            ws.update(combined_rows, "A1")
            
            # 🌟 Colorize both tables in the same sheet dynamically!
            # Category table header starts at row 2
            beautify_gsheet_table(ws, cat_df, start_row=2)
            
            # Item table header starts after Cat table length + 7 spacing rows
            item_start_row = len(cat_df) + 7
            beautify_gsheet_table(ws, item_df, start_row=item_start_row)
            
            print(f"✅ Regional Workspace Tab '{tab_name}' fully styled.")
        except Exception as reg_sheet_err:
            print(f"❌ Failed to populate regional tab '{tab_name}': {str(reg_sheet_err)}")

except Exception as e:
    print("❌ Critical System Error updating Google Sheet workspaces:", str(e))
    
# =========================================================
# EMAIL LIST
# =========================================================

cc_mails = []

for x in help_df["CC Mail"].dropna():

    for m in str(x).split(","):

        if m.strip():
            cc_mails.append(m.strip())

cc_mails = list(set(cc_mails))

# =========================================================
# HTML STYLE FUNCTION
# =========================================================

def get_cell_color(val, metric="KPT"):

    try:
        val = float(val)

        

        # =====================================================
        # KPT LOGIC
        # Green <12
        # Yellow 12-15
        # Red >15
        # =====================================================
        if metric == "KPT":

            if val < 12:
                return (
                    "background-color:#d9ead3;"
                    "color:#006100;"
                    "font-weight:bold;"
                )

            elif val <= 15:
                return (
                    "background-color:#fff2cc;"
                    "color:#9c6500;"
                    "font-weight:bold;"
                )

            else:
                return (
                    "background-color:#f4cccc;"
                    "color:#9c0006;"
                    "font-weight:bold;"
                )

        # =====================================================
        # O2D LOGIC
        # Green <30
        # Yellow 30-35
        # Red >35
        # =====================================================
        elif metric == "O2D":

            if val < 30:
                return (
                    "background-color:#d9ead3;"
                    "color:#006100;"
                    "font-weight:bold;"
                )

            elif val <= 35:
                return (
                    "background-color:#fff2cc;"
                    "color:#9c6500;"
                    "font-weight:bold;"
                )

            else:
                return (
                    "background-color:#f4cccc;"
                    "color:#9c0006;"
                    "font-weight:bold;"
                )

    except:
        return ""

    return ""


def style_dashboard_table(df, metric="KPT"):

    if df.empty:
        return "<p>No Data</p>"

    df = df.fillna("-")

    html = """
    <table style="
        border-collapse:collapse;
        width:80%;
        font-family:Arial;
        font-size:11px;
    ">
    """

    # =====================================================
    # HEADER
    # =====================================================

    html += "<tr>"

    for col in df.columns:

        html += f"""
        <th style="
        background:#1F4E78;
        color:white;
        border:1px solid #d9d9d9;
        padding:12px;
        text-align:center;
        white-space:nowrap;
        ">
        {col}
        </th>
        """

    html += "</tr>"

    # =====================================================
    # BODY
    # =====================================================

    for _, row in df.iterrows():

        html += "<tr>"

        for col in df.columns:

            val = row[col]

            style = ""

           # 🌟 FIX: Check for metric columns and strictly isolate O2D rules from KPT rules
            if any(x in str(col) for x in ["KPT", "O2D", "Avg", "P80", "Median"]):
                try:
                    float(val)
                    # Force O2D rule if O2D string matches anywhere in the header
                    if "O2D" in str(col):
                        style = get_cell_color(val, "O2D")
                    else:
                        style = get_cell_color(val, "KPT")
                except:
                    pass

            # 🌟 FIX: Force names/categories to display as full long text without wrapping
            # Text alignment left makes long item names much easier to read than centering them
            if "Name" in str(col) or "shortName" in str(col) or "categoryName" in str(col) or col in ["Element Name", "Scope"]:
                style += "white-space:nowrap; text-align:left; font-weight:bold; padding-right:15px;"
            else:
                style += "text-align:center;"

            html += f"""
            <td style="
            border:1px solid #d9d9d9;
            padding:8px;
            {style}
            ">
            {val}
            </td>
            """

        html += "</tr>"

    html += "</table>"

    return html


# =========================================================
# 🌟 UPDATED HTML RENDER SECTIONS
# =========================================================

category_html = f"""
<h2>Category Dashboard</h2>
{style_dashboard_table(category_dashboard)}
"""

item_html = f"""
<h2>Item Dashboard</h2>
{style_dashboard_table(item_dashboard)}
"""

# RCA Executive Summary for Email
rca_html = "<h2>🚨 Root Cause Analysis (Critical Operational Bottlenecks)</h2>"
if not rca_dashboard.empty:
    rca_html += style_dashboard_table(rca_dashboard)
else:
    rca_html += "<p style='color:green; font-weight:bold;'>✅ All items and categories met performance SLA targets yesterday!</p>"

# Region-wise breakdown sections
region_breakdown_html = ""
for region in sorted(sales_df["Region"].dropna().unique()):
    region_breakdown_html += f"""
    <hr style="border:1px solid #d9d9d9;">
    <h2>📍 Region: {region}</h2>
    <h3>{region} - Category Performance</h3>
    {style_dashboard_table(region_category_dashboards.get(region, pd.DataFrame()))}
    <br>
    <h3>{region} - Item Performance</h3>
    {style_dashboard_table(region_item_dashboards.get(region, pd.DataFrame()))}
    <br><br>
    """

# =========================================================
# DEBUG LOGS
# =========================================================
print("FTD Rows:", len(sales_df))
print("MTD Rows:", len(mtd_df))
print("Category Dashboard Rows:", len(category_dashboard))
print("Item Dashboard Rows:", len(item_dashboard))
print("RCA Dashboard Rows:", len(rca_dashboard))

# =========================================================
# CLEAN RESTRUCTURED SUMMARY HTML EMAIL
# =========================================================
gsheet_url = "https://docs.google.com/spreadsheets/d/14jVSgxEmyNLulEOAJxgkILqPDAQv6lyJORjrAmnlxXM/edit"

summary_html = f"""
<html>
<body style="font-family:Arial; color:#333;">

<h2>📊 Operational KPT & O2D Performance Dashboard ({fetch_date})</h2>

<!-- 🌟 ADDED: Direct Access Link for the Boss at the top -->
<div style="background-color: #f8f9fa; border-left: 6px solid #1F4E78; padding: 15px; margin-bottom: 20px;">
    <p style="margin: 0; font-size: 14px;">
        🔗 <b>Direct Workspace Link:</b> 
        <a href="{gsheet_url}" target="_blank" style="color: #1F4E78; font-weight: bold; text-decoration: underline;">
            Open Google Sheets Master RCA Workspace
        </a>
    </p>
</div>

<p><i>Note: Full tracking data has been dynamically compiled. Detailed logs are available in the centralized Google Sheets master workspace under the 'Sales Dashboard' and 'RCA Analysis' tabs.</i></p>

<br>
{rca_html}
<br>

<h2>1. Overall Category Performance</h2>
{category_html}
<br>

<h2>2. Overall Item Performance</h2>
{item_html}
<br>

<h2>3. Regional Operational Performance Breakdowns</h2>
{region_breakdown_html}

<br><br>
<p>Regards,<br><b>MIS Team</b></p>
</body>
</html>
"""

# =========================================================
# LOGS
# =========================================================

print("✅ Category Dashboard Ready")
print("✅ Item Dashboard Ready")
print("✅ Region + Category Dashboard Ready")
print("✅ Region + Item Dashboard Ready")
print("✅ Root Cause Analysis (RCA) Engine Completed")


# =========================================================
# SEND EMAIL
# =========================================================

EMAIL_USER = os.environ["EMAIL_USER"]
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]

msg = MIMEMultipart()

msg["From"] = EMAIL_USER
msg["To"] = "faraz@frozenbottle.in"
msg["CC"] = "vivek@frozenbottle.in"


msg["Subject"] = f"📊 Item Level KPT & O2D Dashboard - {fetch_date}"

msg.attach(
    MIMEText(summary_html, "html")
)

try:

    server = smtplib.SMTP(
        "smtp.gmail.com",
        587
    )

    server.starttls()

    server.login(
        EMAIL_USER,
        EMAIL_PASSWORD
    )

    recipients = [
        "mis2@frozenbottle.in"
        
    ]
    server.sendmail(
        EMAIL_USER,
        recipients,
        msg.as_string()
    )

    server.quit()

    print("✅ Mail Sent Successfully")

except Exception as e:
    print("❌ Mail Error:", e)

print("✅ Script Completed")

# =========================================================
# PAYMENT SUMMARY DATA PIPELINE (UPDATED GROSS & DISCOUNT)
# =========================================================

def process_payment_records(df, help_df):
    if df.empty:
        return pd.DataFrame()

    target_modes = [
        "Swiggy", "Zomato", "Pine Labs - UPI Sale", "UPI", "Cash",
        "Swiggy Dine out", "Amazon Pay", "Pine Labs - Card", "Card",
        "OWNLY", "Zomato Gold", "magicpin", "HOGR", "Bitsila",
        "Pine Labs - other", "EazyDiner"
    ]
    target_mode_lookup = {m.lower().replace(" ", "").replace("-", ""): m for m in target_modes}

    # 1. Calculate Gross and Discount at the Invoice Level
    # Look for item-level columns, falling back to top-level order columns if needed
    gross_col = "item_grossAmount" if "item_grossAmount" in df.columns else "grossAmount"
    disc_col = "item_discountAmount" if "item_discountAmount" in df.columns else "discountAmount"

    temp_calc = df.copy()
    temp_calc["_gross_num"] = pd.to_numeric(temp_calc.get(gross_col, 0), errors="coerce").fillna(0)
    # Convert discount to absolute positive value
    temp_calc["_disc_num"] = pd.to_numeric(temp_calc.get(disc_col, 0), errors="coerce").abs().fillna(0)

    # Sum item gross and discount per invoice
    order_totals = temp_calc.groupby("invoiceNumber", as_index=False).agg({
        "_gross_num": "sum",
        "_disc_num": "sum"
    }).rename(columns={
        "_gross_num": "Gross",
        "_disc_num": "Discount"
    })

    # 2. Get base unique invoice records for payment explosion
    base_orders = df.drop_duplicates(subset=["invoiceNumber"]).copy()
    base_orders = base_orders.merge(order_totals, on="invoiceNumber", how="left")

    if "payments" not in base_orders.columns:
        print("❌ 'payments' column not found in transaction data")
        return pd.DataFrame()

    pay_df = base_orders.explode("payments")
    pay_df = pay_df[pay_df["payments"].notna()].copy()

    # 3. Parse nested payment dictionaries/strings
    def parse_payment(row):
        p = row["payments"]
        if isinstance(p, str):
            try:
                import ast
                p = ast.literal_eval(p)
            except:
                return pd.Series([None, 0])
        if isinstance(p, dict):
            return pd.Series([p.get("mode"), float(p.get("amount", 0))])
        return pd.Series([None, 0])

    pay_df[["pay_mode_raw", "pay_amount"]] = pay_df.apply(parse_payment, axis=1)
    pay_df = pay_df[pay_df["pay_mode_raw"].notna()].copy()

    # 4. Standardize Payment Mode names without forcing into "Other"
    def normalize_mode(raw_mode):
        raw_clean = str(raw_mode).strip()
        key = raw_clean.lower().replace(" ", "").replace("-", "")
        return target_mode_lookup.get(key, raw_clean)

    pay_df["Payment Mode"] = pay_df["pay_mode_raw"].apply(normalize_mode)

    # 5. Net out customer change adjustments (e.g. 100 Cash - 59 Cash refund = 41 net)
    net_payments = pay_df.groupby(
        ["invoiceNumber", "branchCode", "invoiceDay", "brandName", "Payment Mode"],
        as_index=False
    ).agg({
        "pay_amount": "sum",
        "Gross": "first",
        "Discount": "first"
    })

    # Filter out balanced zero-sum entries
    net_payments = net_payments[net_payments["pay_amount"] > 0].copy()

    # 6. Merge Store Name & Region from Help Sheet
    meta_cols = help_df[["branchCode", "Store Name", "Region"]].drop_duplicates()
    final_payments = net_payments.merge(meta_cols, on="branchCode", how="left")

    # Rename & format
    final_payments = final_payments.rename(columns={
        "invoiceDay": "Date",
        "brandName": "Brand",
        "pay_amount": "Value"
    })

    final_payments["Txn"] = 1

    return final_payments[[
        "Date", "Brand", "Region", "Store Name", "Payment Mode", "Value", "Txn", "Gross", "Discount"
    ]]

print("🔄 Processing Payment Records with Item Gross & Positive Discount...")
payment_export_df = process_payment_records(raw_mtd_df, help_df)

if not payment_export_df.empty:
    try:
        payment_ws = None
        try:
            payment_ws = spreadsheet.worksheet("Payment_Raw_Data")
        except gspread.exceptions.WorksheetNotFound:
            payment_ws = spreadsheet.add_worksheet(title="Payment_Raw_Data", rows="50000", cols="15")
        
        payment_ws.clear()
        payment_export_df["Date"] = payment_export_df["Date"].astype(str)
        payment_data = [payment_export_df.columns.values.tolist()] + payment_export_df.values.tolist()
        payment_ws.update(payment_data, "A1")
        print(f"✅ Payment Data synchronized to 'Payment_Raw_Data' ({len(payment_export_df)} rows)")
    except Exception as e:
        print("❌ Error exporting Payment Data:", e)
