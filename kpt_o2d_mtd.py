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
                    print(
                        f"⚠ No sales data returned | "
                        f"Branch: {branch} | "
                        f"Date: {fetch_date}"
                    )
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
    # STANDARDIZE branchCode
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
    # STANDARDIZE CHANNEL
    # =====================================================

    if "Channel" not in final_df.columns:

        if "channel" in final_df.columns:

            final_df["Channel"] = (
                final_df["channel"]
                .astype(str)
                .str.strip()
            )

        else:

            print("❌ Channel column missing")

            final_df["Channel"] = ""

    else:

        final_df["Channel"] = (
            final_df["Channel"]
            .astype(str)
            .str.strip()
        )

    # =====================================================
    # KEEP ONLY ONLINE CHANNELS
    #
    # Do NOT use exact channel names here.
    # Rista channel naming can vary by store/brand.
    # =====================================================

    channel_lower = (
        final_df["Channel"]
        .astype(str)
        .str.strip()
        .str.lower()
    )

    online_mask = (
        channel_lower.str.contains("swiggy", na=False)
        |
        channel_lower.str.contains("zomato", na=False)
    )

    final_df = final_df[
        online_mask
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


# =========================================================
# KPI
# =========================================================

swiggy_avg = round(
    sales_df[
        sales_df["Channel"]
        .str.contains("Swiggy", na=False)
    ]["KPT (Mins)"].mean(),
    1
)

zomato_avg = round(
    sales_df[
        sales_df["Channel"]
        .str.contains("Zomato", na=False)
    ]["KPT (Mins)"].mean(),
    1
)

overall_avg = round(
    sales_df["KPT (Mins)"].mean(),
    1
)

overall_o2d = round(
    sales_df["O2D (Mins)"].mean(),
    1
)

total_orders = len(sales_df)

top_kpi = pd.DataFrame({

    "Metric": [
        "Swiggy Avg KPT",
        "Zomato Avg KPT",
        "Overall Avg KPT",
        "Overall Avg O2D",
        "Total Orders"
    ],

    "Value": [
        swiggy_avg,
        zomato_avg,
        overall_avg,
        overall_o2d,
        total_orders
    ]
})

print("✅ KPI Created")

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

def overall_dashboard(df):

    swiggy = df[df["Channel"].str.contains("Swiggy")]
    zomato = df[df["Channel"].str.contains("Zomato")]

    out = pd.DataFrame({
        "Parameters": [
            "Orders",
            "KPT",
            "O2D",
            "KPT P80",
            "O2D P80",
            "KPT Median",
            "O2D Median",
            "Breached Orders KPT",
            "Breached Orders O2D"
        ],

        "Swiggy": [
            len(swiggy),
            swiggy["KPT (Mins)"].mean(),
            swiggy["O2D (Mins)"].mean(),
            swiggy["KPT (Mins)"].quantile(0.80),
            swiggy["O2D (Mins)"].quantile(0.80),
            swiggy["KPT (Mins)"].median(),
            swiggy["O2D (Mins)"].median(),
            (swiggy["KPT (Mins)"] > 12).sum(),
            (swiggy["O2D (Mins)"] > 30).sum()
        ],

        "Zomato": [
            len(zomato),
            zomato["KPT (Mins)"].mean(),
            zomato["O2D (Mins)"].mean(),
            zomato["KPT (Mins)"].quantile(0.80),
            zomato["O2D (Mins)"].quantile(0.80),
            zomato["KPT (Mins)"].median(),
            zomato["O2D (Mins)"].median(),
            (zomato["KPT (Mins)"] > 12).sum(),
            (zomato["O2D (Mins)"] > 30).sum()
        ],

        "Overall": [
            len(df),
            df["KPT (Mins)"].mean(),
            df["O2D (Mins)"].mean(),
            df["KPT (Mins)"].quantile(0.80),
            df["O2D (Mins)"].quantile(0.80),
            df["KPT (Mins)"].median(),
            df["O2D (Mins)"].median(),
            (df["KPT (Mins)"] > 12).sum(),
            (df["O2D (Mins)"] > 30).sum()
        ]
    })

    return out.round(1)

# =========================================================
# MTD DASHBOARD
# =========================================================

def overall_dashboard_mtd(
    ftd_df,
    mtd_df
):

    ftd_swiggy = ftd_df[
        ftd_df["Channel"].str.contains(
            "Swiggy",
            na=False
        )
    ]

    ftd_zomato = ftd_df[
        ftd_df["Channel"].str.contains(
            "Zomato",
            na=False
        )
    ]

    mtd_swiggy = mtd_df[
        mtd_df["Channel"].str.contains(
            "Swiggy",
            na=False
        )
    ]

    mtd_zomato = mtd_df[
        mtd_df["Channel"].str.contains(
            "Zomato",
            na=False
        )
    ]

    dashboard = pd.DataFrame({

        "Parameters": [
            "Orders",
            "KPT",
            "O2D",
            "KPT P80",
            "O2D P80",
            "KPT Median",
            "O2D Median",
            "Breached Orders KPT",
            "Breached Orders O2D"
        ],

        "FTD Swiggy": [
            len(ftd_swiggy),
            round(ftd_swiggy["KPT (Mins)"].mean(),1),
            round(ftd_swiggy["O2D (Mins)"].mean(),1),
            round(ftd_swiggy["KPT (Mins)"].quantile(.80),1),
            round(ftd_swiggy["O2D (Mins)"].quantile(.80),1),
            round(ftd_swiggy["KPT (Mins)"].median(),1),
            round(ftd_swiggy["O2D (Mins)"].median(),1),
            (ftd_swiggy["KPT (Mins)"] > 12).sum(),
            (ftd_swiggy["O2D (Mins)"] > 30).sum()
        ],

        "FTD Zomato": [
            len(ftd_zomato),
            round(ftd_zomato["KPT (Mins)"].mean(),1),
            round(ftd_zomato["O2D (Mins)"].mean(),1),
            round(ftd_zomato["KPT (Mins)"].quantile(.80),1),
            round(ftd_zomato["O2D (Mins)"].quantile(.80),1),
            round(ftd_zomato["KPT (Mins)"].median(),1),
            round(ftd_zomato["O2D (Mins)"].median(),1),
            (ftd_zomato["KPT (Mins)"] > 12).sum(),
            (ftd_zomato["O2D (Mins)"] > 30).sum()
        ],

        "FTD Overall": [
            len(ftd_df),
            round(ftd_df["KPT (Mins)"].mean(),1),
            round(ftd_df["O2D (Mins)"].mean(),1),
            round(ftd_df["KPT (Mins)"].quantile(.80),1),
            round(ftd_df["O2D (Mins)"].quantile(.80),1),
            round(ftd_df["KPT (Mins)"].median(),1),
            round(ftd_df["O2D (Mins)"].median(),1),
            (ftd_df["KPT (Mins)"] > 12).sum(),
            (ftd_df["O2D (Mins)"] > 30).sum()
        ],

        "MTD Swiggy": [
            len(mtd_swiggy),
            round(mtd_swiggy["KPT (Mins)"].mean(),1),
            round(mtd_swiggy["O2D (Mins)"].mean(),1),
            round(mtd_swiggy["KPT (Mins)"].quantile(.80),1),
            round(mtd_swiggy["O2D (Mins)"].quantile(.80),1),
            round(mtd_swiggy["KPT (Mins)"].median(),1),
            round(mtd_swiggy["O2D (Mins)"].median(),1),
            (mtd_swiggy["KPT (Mins)"] > 12).sum(),
            (mtd_swiggy["O2D (Mins)"] > 30).sum()
        ],

        "MTD Zomato": [
            len(mtd_zomato),
            round(mtd_zomato["KPT (Mins)"].mean(),1),
            round(mtd_zomato["O2D (Mins)"].mean(),1),
            round(mtd_zomato["KPT (Mins)"].quantile(.80),1),
            round(mtd_zomato["O2D (Mins)"].quantile(.80),1),
            round(mtd_zomato["KPT (Mins)"].median(),1),
            round(mtd_zomato["O2D (Mins)"].median(),1),
            (mtd_zomato["KPT (Mins)"] > 12).sum(),
            (mtd_zomato["O2D (Mins)"] > 30).sum()
        ],

        "MTD Overall": [
            len(mtd_df),
            round(mtd_df["KPT (Mins)"].mean(),1),
            round(mtd_df["O2D (Mins)"].mean(),1),
            round(mtd_df["KPT (Mins)"].quantile(.80),1),
            round(mtd_df["O2D (Mins)"].quantile(.80),1),
            round(mtd_df["KPT (Mins)"].median(),1),
            round(mtd_df["O2D (Mins)"].median(),1),
            (mtd_df["KPT (Mins)"] > 12).sum(),
            (mtd_df["O2D (Mins)"] > 30).sum()
        ]
    })

    return dashboard

# =========================================================
# OVERALL DASHBOARD
# =========================================================

overall_df = overall_dashboard_mtd(
    sales_df,
    mtd_df
)

# =========================================================
# REGION DASHBOARDS
# =========================================================

region_dashboards = {}

for r in sales_df["Region"].dropna().unique():

    ftd_temp = sales_df[
        sales_df["Region"] == r
    ].copy()

    mtd_temp = mtd_df[
        mtd_df["Region"] == r
    ].copy()

    region_dashboards[r] = (
        overall_dashboard_mtd(
            ftd_temp,
            mtd_temp
        )
    )

print("FTD Columns")
print(sales_df.columns.tolist())

print("MTD Columns")
print(mtd_df.columns.tolist())


# =========================================================
# WRITE TO GOOGLE SHEET
# =========================================================

try:

    report_ws = spreadsheet.worksheet("Sales Dashboard")

    report_ws.clear()

    report_ws.update(
        [top_kpi.columns.values.tolist()] +
        top_kpi.values.tolist(),
        "A1"
    )

    print("✅ Dashboard Updated in GSheet")

except Exception as e:

    print(
        "❌ Sheet Update Error:",
        str(e)
    )

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

    # replace nan with "-"
    df = df.fillna("-")

    html = """
    <table style="
        border-collapse:collapse;
        width:70%;
        font-family:Arial;
        font-size:18px;
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
        padding:8px;
        text-align:center;
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

    
            # highlight only KPI columns
            highlight_cols = [
                "KPT",
                "O2D",
                "Avg",
                "P80",
                "Median"
            ]
            
            if any(x in str(col) for x in highlight_cols):
            
                try:
                    float(val)
            
                    # O2D columns
                    if "O2D" in str(col):
                        style = get_cell_color(
                            val,
                            "O2D"
                        )
            
                    # KPT columns
                    else:
                        style = get_cell_color(
                            val,
                            "KPT"
                        )
            
                except:
                    pass

            html += f"""
            <td style="
            border:1px solid #d9d9d9;
            padding:8px;
            text-align:center;
            {style}
            ">
            {val}
            </td>
            """

        html += "</tr>"

    html += "</table>"

    return html

# =========================================================
# REGION HTML
# =========================================================

region_html = ""

for region, df in region_dashboards.items():

    region_html += f"""
    <h3>{region}</h3>
    {style_dashboard_table(df)}
    <br>
    """


# =========================================================
# REGION + STORE DASHBOARD
# =========================================================

# =========================================================
# REGION + STORE DASHBOARD
# =========================================================
#
# IMPORTANT:
# Build the store list from Help Sheet.
#
# This guarantees that every COCO store appears even when:
# - FTD has no online orders
# - MTD has no online orders
# - KPT/O2D data is unavailable
#
# =========================================================

region_store_html = ""

# ---------------------------------------------------------
# MASTER COCO STORE LIST
# ---------------------------------------------------------

master_stores = (
    help_df[
        [
            "branchCode",
            "Store Name",
            "Region"
        ]
    ]
    .drop_duplicates(subset=["branchCode"])
    .copy()
)

master_stores["branchCode"] = (
    master_stores["branchCode"]
    .astype(str)
    .str.strip()
)

master_stores["Store Name"] = (
    master_stores["Store Name"]
    .astype(str)
    .str.strip()
)

master_stores["Region"] = (
    master_stores["Region"]
    .astype(str)
    .str.strip()
)

# ---------------------------------------------------------
# REGION LIST FROM HELP SHEET
# ---------------------------------------------------------

for region in sorted(
    master_stores["Region"]
    .dropna()
    .unique()
):

    # =====================================================
    # REGION MASTER STORES
    # =====================================================

    region_stores = (
        master_stores[
            master_stores["Region"] == region
        ]
        .copy()
    )

    # =====================================================
    # FTD DATA
    # =====================================================

    temp = sales_df[
        sales_df["Region"] == region
    ].copy()

    # =====================================================
    # MTD DATA
    # =====================================================

    mtd_temp = mtd_df[
        mtd_df["Region"] == region
    ].copy()

    # =====================================================
    # SWIGGY
    # =====================================================

    swiggy = temp[
        temp["Channel"]
        .astype(str)
        .str.contains(
            "Swiggy",
            case=False,
            na=False
        )
    ].copy()

    # =====================================================
    # SWIGGY FTD
    # =====================================================

    swiggy_store = (
        swiggy.groupby(
            "branchCode",
            as_index=False
        )
        .agg(
            **{
                "FTD Orders": (
                    "KPT (Mins)",
                    "count"
                ),
                "FTD KPT": (
                    "KPT (Mins)",
                    "mean"
                ),
                "FTD KPT P80": (
                    "KPT (Mins)",
                    lambda x: x.quantile(0.80)
                ),
                "FTD KPT Median": (
                    "KPT (Mins)",
                    "median"
                ),
                "FTD O2D": (
                    "O2D (Mins)",
                    "mean"
                ),
                "FTD O2D P80": (
                    "O2D (Mins)",
                    lambda x: x.quantile(0.80)
                ),
                "FTD O2D Median": (
                    "O2D (Mins)",
                    "median"
                )
            }
        )
    )

    # =====================================================
    # SWIGGY MTD
    # =====================================================

    mtd_swiggy = mtd_temp[
        mtd_temp["Channel"]
        .astype(str)
        .str.contains(
            "Swiggy",
            case=False,
            na=False
        )
    ].copy()

    mtd_swiggy_store = (
        mtd_swiggy.groupby(
            "branchCode",
            as_index=False
        )
        .agg(
            **{
                "MTD Orders": (
                    "KPT (Mins)",
                    "count"
                ),
                "MTD KPT": (
                    "KPT (Mins)",
                    "mean"
                ),
                "MTD KPT P80": (
                    "KPT (Mins)",
                    lambda x: x.quantile(0.80)
                ),
                "MTD KPT Median": (
                    "KPT (Mins)",
                    "median"
                ),
                "MTD O2D": (
                    "O2D (Mins)",
                    "mean"
                ),
                "MTD O2D P80": (
                    "O2D (Mins)",
                    lambda x: x.quantile(0.80)
                ),
                "MTD O2D Median": (
                    "O2D (Mins)",
                    "median"
                )
            }
        )
    )

    # =====================================================
    # SWIGGY - LEFT JOIN TO MASTER STORE LIST
    # =====================================================

    swiggy_store = (
        region_stores[
            ["branchCode", "Store Name"]
        ]
        .merge(
            swiggy_store,
            on="branchCode",
            how="left"
        )
        .merge(
            mtd_swiggy_store,
            on="branchCode",
            how="left"
        )
    )

    # =====================================================
    # ZOMATO
    # =====================================================

    zomato = temp[
        temp["Channel"]
        .astype(str)
        .str.contains(
            "Zomato",
            case=False,
            na=False
        )
    ].copy()

    # =====================================================
    # ZOMATO FTD
    # =====================================================

    zomato_store = (
        zomato.groupby(
            "branchCode",
            as_index=False
        )
        .agg(
            **{
                "FTD Orders": (
                    "KPT (Mins)",
                    "count"
                ),
                "FTD KPT": (
                    "KPT (Mins)",
                    "mean"
                ),
                "FTD KPT P80": (
                    "KPT (Mins)",
                    lambda x: x.quantile(0.80)
                ),
                "FTD KPT Median": (
                    "KPT (Mins)",
                    "median"
                ),
                "FTD O2D": (
                    "O2D (Mins)",
                    "mean"
                ),
                "FTD O2D P80": (
                    "O2D (Mins)",
                    lambda x: x.quantile(0.80)
                ),
                "FTD O2D Median": (
                    "O2D (Mins)",
                    "median"
                )
            }
        )
    )

    # =====================================================
    # ZOMATO MTD
    # =====================================================

    mtd_zomato = mtd_temp[
        mtd_temp["Channel"]
        .astype(str)
        .str.contains(
            "Zomato",
            case=False,
            na=False
        )
    ].copy()

    mtd_zomato_store = (
        mtd_zomato.groupby(
            "branchCode",
            as_index=False
        )
        .agg(
            **{
                "MTD Orders": (
                    "KPT (Mins)",
                    "count"
                ),
                "MTD KPT": (
                    "KPT (Mins)",
                    "mean"
                ),
                "MTD KPT P80": (
                    "KPT (Mins)",
                    lambda x: x.quantile(0.80)
                ),
                "MTD KPT Median": (
                    "KPT (Mins)",
                    "median"
                ),
                "MTD O2D": (
                    "O2D (Mins)",
                    "mean"
                ),
                "MTD O2D P80": (
                    "O2D (Mins)",
                    lambda x: x.quantile(0.80)
                ),
                "MTD O2D Median": (
                    "O2D (Mins)",
                    "median"
                )
            }
        )
    )

    # =====================================================
    # ZOMATO - LEFT JOIN TO MASTER STORE LIST
    # =====================================================

    zomato_store = (
        region_stores[
            ["branchCode", "Store Name"]
        ]
        .merge(
            zomato_store,
            on="branchCode",
            how="left"
        )
        .merge(
            mtd_zomato_store,
            on="branchCode",
            how="left"
        )
    )

    # =====================================================
    # REPLACE MISSING KPI VALUES WITH ZERO
    # =====================================================

    numeric_columns = [
        "FTD Orders",
        "FTD KPT",
        "FTD KPT P80",
        "FTD KPT Median",
        "FTD O2D",
        "FTD O2D P80",
        "FTD O2D Median",
        "MTD Orders",
        "MTD KPT",
        "MTD KPT P80",
        "MTD KPT Median",
        "MTD O2D",
        "MTD O2D P80",
        "MTD O2D Median"
    ]

    for col in numeric_columns:

        if col in swiggy_store.columns:
            swiggy_store[col] = (
                pd.to_numeric(
                    swiggy_store[col],
                    errors="coerce"
                )
                .fillna(0)
            )

        if col in zomato_store.columns:
            zomato_store[col] = (
                pd.to_numeric(
                    zomato_store[col],
                    errors="coerce"
                )
                .fillna(0)
            )

    swiggy_store = swiggy_store.round(2)
    zomato_store = zomato_store.round(2)

    # =====================================================
    # DISPLAY
    # =====================================================

    region_store_html += f"""
    <h2>{region}</h2>

    <h3>Swiggy</h3>
    {style_dashboard_table(swiggy_store)}

    <br>

    <h3>Zomato</h3>
    {style_dashboard_table(zomato_store)}

    <br><br>
    """

# =========================================================
# SUMMARY HTML
# =========================================================

summary_html = f"""
<html>

<body style="font-family:Arial;">

<h2>
📊 KPT and O2D Performance Dashboard
({fetch_date})
</h2>

<br>

<h2>Overall Dashboard</h2>
{style_dashboard_table(
    overall_dashboard_mtd(
        sales_df,
        mtd_df
    )
)}

<br>

<h2>Region Dashboards</h2>
{region_html}

<br>

<h2>
🏪 Region + Store Dashboard
</h2>

{region_store_html}

<br><br>

<p>
Regards,
<br>
MIS Team
</p>

</body>
</html>
"""

print("✅ Overall Dashboard Ready")

print(
    "✅ Region Dashboards:",
    len(region_dashboards)
)

print("✅ Region Store Dashboard Ready")
# =========================================================
# SEND EMAIL
# =========================================================

EMAIL_USER = os.environ["EMAIL_USER"]
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]

msg = MIMEMultipart()

msg["From"] = EMAIL_USER
msg["To"] = "ops.all@frozenbottle.in"
msg["CC"] = "vivek@frozenbottle.in"


msg["Subject"] = f"📊 KPT & O2D Dashboard - {fetch_date}"

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
        "ops.all@frozenbottle.in",
        "vivek@frozenbottle.in"
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
