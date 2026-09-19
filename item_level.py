# =========================================================
# IMPORTS
# =========================================================

import os
import re
import json
import time
import jwt
import requests
import pandas as pd
import numpy as np

from datetime import (
    datetime,
    timedelta
)

import gspread

from google.oauth2.service_account import Credentials

print("🚀 Product Dashboard Started")


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
    json.loads(
        os.environ[
            "GOOGLE_CREDENTIALS"
        ]
    ),
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)

client = gspread.authorize(creds)


spreadsheet = client.open_by_key(
    "1ldgnNMdeubDx_ImtCC1uCD7FGz8gt_edmWEkmO_xRNk"
)

print("✅ Connected Google Sheet")

# =========================================================
# BUSINESS WINDOW
# =========================================================

now = datetime.now()

print("🕒 Current Time:", now)

# =========================================================
# BUSINESS DATE
# =========================================================

if now.hour < 5:

    business_date = (
        now - timedelta(days=1)
    ).date()

else:

    business_date = now.date()

print(
    "📅 Business Date:",
    business_date
)


# =========================================================
# HELP SHEET
# =========================================================

help_ws = spreadsheet.worksheet(
    "Help Sheet"
)

help_data = help_ws.get_all_values()

if not help_data:

    print("❌ Help Sheet Empty")
    exit()

help_rows = []

for row in help_data[1:]:

    row = list(row)

    if len(row) < 7:
        row.extend(
            [""] * (7 - len(row))
        )

    help_rows.append([
        row[0],  # A branchCode
        row[1],  # B Store Name
        row[2],  # C Ownership
        row[3],  # D Region
        row[5],  # F Channel
        row[6],  # G Source
    ])

help_df = pd.DataFrame(
    help_rows,
    columns=[
        "branchCode",
        "Store Name",
        "Ownership",
        "Region",
        "Channel",
        "Source"
    ]
)

help_df["Ownership"] = (
    help_df["Ownership"]
    .astype(str)
    .str.upper()
    .str.strip()
)

help_df["branchCode"] = (
    help_df["branchCode"]
    .astype(str)
    .str.strip()
)

# COCO only
help_df = help_df[
    help_df["Ownership"] == "COCO"
].copy()

print("✅ Help Sheet Loaded")
print(
    "📋 Help Columns:",
    help_df.columns.tolist()
)

print(
    "✅ COCO Stores:",
    len(help_df)
)

# =========================================================
# REQUIRED COLUMNS CHECK
# =========================================================

required_cols = [
    "branchCode",
    "Store Name",
    "Ownership",
    "Region",
    "Channel"
]

missing_cols = [
    c for c in required_cols
    if c not in help_df.columns
]

if missing_cols:
    print("❌ Missing Help Columns:", missing_cols)
    print("📋 Available Columns:", help_df.columns.tolist())
    exit()

# =========================================================
# CHANNEL MAPPING
# =========================================================
# Do NOT rename Source

channel_map = dict(
    zip(
        help_df["Channel"],
        help_df["Source"]
    )
)

print("✅ Channel Mapping:", len(channel_map))
    
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
    .unique()
    .tolist()
)

print("🏪 Branch Count:", len(branches))

# =========================================================
# CHANNEL GROUP MAPPING
# =========================================================

# Keep original Help Sheet columns.
# Do NOT rename Source.

if "Channel" in help_df.columns and "Source" in help_df.columns:

    channel_map = dict(
        zip(
            help_df["Channel"],
            help_df["Source"]
        )
    )

    print("✅ Channel Mapping:", len(channel_map))

else:

    print("⚠️ Channel Mapping Skipped")

# =========================================================
# ITEM GROUP TAB
# =========================================================

item_ws = spreadsheet.worksheet(
    "Item Group"
)

item_data = item_ws.get_all_values()

if not item_data:

    print("❌ Item Group Empty")
    exit()

# =========================================================
# FIXED HEADERS (A:E ONLY)
# =========================================================

item_headers = [
    "Item Name",
    "Item Group Name",
    "Variant(s)",
    "Product Mix",
    "Category Group"
]

normalized_rows = []

for row in item_data[1:]:

    row = list(row)

    # only take A:E
    row = row[:5]

    # fill blanks if less columns
    if len(row) < 5:

        row.extend(
            [""] * (5 - len(row))
        )

    normalized_rows.append(row)

item_df = pd.DataFrame(
    normalized_rows,
    columns=item_headers
)

# =========================================================
# CLEAN COLUMNS
# =========================================================

item_df.columns = (
    item_df.columns
    .astype(str)
    .str.strip()
)

# =========================================================
# CLEAN ITEM NAME
# =========================================================

item_df["Item Name"] = (
    item_df["Item Name"]
    .astype(str)
    .str.strip()
    .str.lower()
)

print(
    "✅ Item Group Loaded"
)

print(
    "📋 Item Rows:",
    len(item_df)
)

# =========================================================
# STANDARDIZE COLUMN NAMES
# =========================================================

item_df = item_df.rename(
    columns={
        "Item Name":
        "Item Name",

        "Item Group Name":
        "Item Group Name",

        "Variant(s)":
        "Variant",

        "Product Mix":
        "Product Mix",

        "Category Group":
        "Category Group"
    }
)

# =========================================================
# CLEAN ITEM NAME
# =========================================================

item_df["Item Name"] = (
    item_df["Item Name"]
    .astype(str)
    .str.strip()
    .str.upper()
)

print(
    "✅ Item Mapping Rows:",
    len(item_df)
)

# =========================================================
# SALES API
# =========================================================

sales_url = (
    "https://api.ristaapps.com/v1/sales/page"
)


# =========================================================
# FETCH SALES
# =========================================================

def fetch_sales_window(
    start_datetime,
    end_datetime,
    tag
):

    all_sales = []

    print(
        f"\n📦 Fetching {tag}"
    )

    print(
        "Window:",
        start_datetime,
        "to",
        end_datetime
    )

    # =====================================================
    # LOOP BRANCHES
    # =====================================================

    for branch in branches:

        print(
            "Fetching:",
            branch
        )

        # -------------------------------------------------
        # RISTA "DAY" PARAMETER
        # -------------------------------------------------

        params = {
            "branch": branch,

            "day": start_datetime.strftime(
                "%Y-%m-%d"
            ),

            "page": 1,
            "limit": 5000
        }

        print(
            "Params:",
            params
        )

        try:

            response = requests.get(
                sales_url,
                headers=headers(),
                params=params,
                timeout=180
            )

            print(
                "Status:",
                response.status_code
            )

            print(
                "Response:",
                response.text[:500]
            )

            if (
                response.status_code
                != 200
            ):
                continue

            response_json = (
                response.json()
            )

            data = (
                response_json
                .get("data", [])
            )

            if not data:

                print(
                    "No Data:",
                    branch
                )

                continue

            df = pd.json_normalize(
                data
            )

            print(
                f"   Raw rows from Rista: {len(df)}"
            )

            # =================================================
            # SAFE BRANCH CODE
            # =================================================

            if (
                "branchCode"
                not in df.columns
            ):

                if (
                    "branch"
                    in df.columns
                ):

                    df[
                        "branchCode"
                    ] = df[
                        "branch"
                    ]

            # =================================================
            # FILTER BY EXACT TIME WINDOW
            # =================================================

            if "createdDate" not in df.columns:

                print(
                    f"⚠️ createdDate not found for {branch}"
                )

                continue

            # -------------------------------------------------
            # CONVERT RISTA CREATED DATE TO IST
            # -------------------------------------------------

            df["_createdDate_IST"] = pd.to_datetime(
                df["createdDate"],
                errors="coerce",
                utc=True
            ).dt.tz_convert(
                "Asia/Kolkata"
            )

            # -------------------------------------------------
            # REMOVE INVALID DATES
            # -------------------------------------------------

            invalid_dates = (
                df["_createdDate_IST"]
                .isna()
                .sum()
            )

            if invalid_dates > 0:

                print(
                    f"   ⚠️ Invalid createdDate rows: "
                    f"{invalid_dates}"
                )

            df = df[
                df["_createdDate_IST"].notna()
            ].copy()

            # -------------------------------------------------
            # EXACT TIME FILTER
            #
            # IMPORTANT:
            # Rista "day" returns the complete day.
            # We therefore filter here.
            # -------------------------------------------------

            before_filter = len(df)

            df = df[
                (
                    df["_createdDate_IST"]
                    >= start_datetime
                )
                &
                (
                    df["_createdDate_IST"]
                    <= end_datetime
                )
            ].copy()

            after_filter = len(df)

            print(
                f"   📊 Rows before time filter: "
                f"{before_filter}"
            )

            print(
                f"   ✅ Rows after time filter: "
                f"{after_filter}"
            )

            # -------------------------------------------------
            # REMOVE TEMPORARY DATE COLUMN
            # -------------------------------------------------

            df.drop(
                columns=[
                    "_createdDate_IST"
                ],
                inplace=True,
                errors="ignore"
            )

            # =================================================
            # TAG DATASET
            # =================================================

            df["DATASET"] = tag

            # =================================================
            # ADD TO RESULT
            # =================================================

            all_sales.append(
                df
            )

        except Exception as e:

            print(
                f"❌ Error {branch}:",
                str(e)
            )

    # =====================================================
    # FINAL CONCAT
    # =====================================================

    if not all_sales:

        print(
            f"❌ No {tag} Data"
        )

        return pd.DataFrame()

    final_df = pd.concat(
        all_sales,
        ignore_index=True
    )

    print(
        f"\n✅ {tag} Final Rows:",
        len(final_df)
    )

    return final_df


# =========================================================
# BUSINESS WINDOW
# =========================================================

from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

ist = ZoneInfo(
    "Asia/Kolkata"
)

ist_now = datetime.now(
    ist
)

print(
    "🕒 Current Time:",
    ist_now
)


# =========================================================
# BUSINESS DATE
#
# Business day:
# 09:00 AM → 08:59:59 AM next day
# =========================================================

if ist_now.hour < 9:

    business_date = (
        ist_now.date()
        - timedelta(days=1)
    )

else:

    business_date = (
        ist_now.date()
    )

print(
    "📅 Business Date:",
    business_date
)


# =========================================================
# CURRENT WINDOW
#
# 09:00 AM → CURRENT TIME
#
# Example:
# 08:24 AM
#
# 17-Sep 09:00
# →
# 18-Sep 08:24:59
# =========================================================

current_window_start = datetime.combine(
    business_date,
    datetime.min.time()
).replace(
    hour=9,
    minute=0,
    second=0,
    microsecond=0,
    tzinfo=ist
)

current_window_end = ist_now.replace(
    minute=59,
    second=59,
    microsecond=0
)


# =========================================================
# LAST WEEK WINDOW
#
# EXACT SAME TIME WINDOW
# 7 DAYS EARLIER
# =========================================================

lw_window_start = (
    current_window_start
    - timedelta(days=7)
)

lw_window_end = (
    current_window_end
    - timedelta(days=7)
)


# =========================================================
# LAST 2 WEEKS WINDOW
#
# EXACT SAME TIME WINDOW
# 14 DAYS EARLIER
# =========================================================

l2w_window_start = (
    current_window_start
    - timedelta(days=14)
)

l2w_window_end = (
    current_window_end
    - timedelta(days=14)
)


# =========================================================
# PRINT WINDOWS
# =========================================================

print(
    "\n" +
    "=" * 70
)

print(
    "🟢 CURRENT WINDOW:"
)

print(
    current_window_start,
    "→",
    current_window_end
)

print(
    "\n🟡 LAST WEEK WINDOW:"
)

print(
    lw_window_start,
    "→",
    lw_window_end
)

print(
    "\n🔵 LAST 2 WEEKS WINDOW:"
)

print(
    l2w_window_start,
    "→",
    l2w_window_end
)

print(
    "=" * 70
)


# =========================================================
# FETCH CURRENT
# =========================================================

current_df = fetch_sales_window(
    current_window_start,
    current_window_end,
    "CURRENT"
)


# =========================================================
# FETCH LAST WEEK
# =========================================================

lw_df = fetch_sales_window(
    lw_window_start,
    lw_window_end,
    "LW"
)


# =========================================================
# FETCH LAST 2 WEEKS
# =========================================================

l2w_df = fetch_sales_window(
    l2w_window_start,
    l2w_window_end,
    "L2W"
)
# =========================================================
# FLATTEN ITEM LEVEL DATA
# =========================================================

def flatten_items(df):

    print("📦 Flattening Item Data...")

    # =====================================================
    # EMPTY DATA CHECK
    # =====================================================

    if df is None or len(df) == 0:
        print("⚠ No records available.")
        return pd.DataFrame()

    # =====================================================
    # ITEMS COLUMN CHECK
    # =====================================================

    if "items" not in df.columns:
        print("⚠ 'items' column not found.")
        print("Available Columns:", df.columns.tolist())
        return pd.DataFrame()

    # =====================================================
    # EXPLODE ITEMS
    # =====================================================

    df = df.explode("items")

    # =====================================================
    # REMOVE NULL ITEMS
    # =====================================================

    df = df[
        df["items"].notna()
    ].copy()

    # If nothing remains after explode
    if df.empty:
        print("⚠ No item records after explode.")
        return pd.DataFrame()

    # =====================================================
    # NORMALIZE ITEM JSON
    # =====================================================

    item_df = pd.json_normalize(
        df["items"]
    )

    item_df.columns = [
        f"item_{col}"
        for col in item_df.columns
    ]

    # =====================================================
    # MERGE ITEM + HEADER
    # =====================================================

    df = (
        df
        .drop(columns=["items"])
        .reset_index(drop=True)
    )

    item_df = (
        item_df
        .reset_index(drop=True)
    )

    df = pd.concat(
        [df, item_df],
        axis=1
    )

    print("✅ Item Flatten Completed")

    print("📋 Item Columns:")

    print(
        [
            c for c in df.columns
            if c.startswith("item_")
        ]
    )

    return df


# =========================================================
# CREATE ITEM LEVEL DATA
# =========================================================

current_df = flatten_items(
    current_df
)

lw_df = flatten_items(
    lw_df
)

l2w_df = flatten_items(
    l2w_df
)

print(
    "✅ Item Level Data Ready"
)

# =========================================================
# CHECK IMPORTANT ITEM COLUMNS
# =========================================================

required_cols = [
    "item_quantity",
    "item_baseNetAmount",
    "item_baseNetDiscountAmount",
    "item_discounts",
    "item_shortName",
    "item_categoryName"
]

for col in required_cols:

    if col in current_df.columns:

        print(
            f"✅ Found: {col}"
        )

    else:

        print(
            f"❌ Missing: {col}"
        )

sales_df = pd.concat(
    [current_df, lw_df],
    ignore_index=True
)

print(
    "✅ Total Rows:",
    len(sales_df)
)

print("📋 API Columns:")
print(sales_df.columns.tolist())

print("CURRENT DF COLUMNS")
print(current_df.columns.tolist())

print("LW DF COLUMNS")
print(lw_df.columns.tolist())

print("L2W DF COLUMNS")
print(l2w_df.columns.tolist())

# =========================================================
# STANDARD DATAFRAME NAMES
# =========================================================

current_sales = current_df.copy()

lw_sales = lw_df.copy()

l2w_sales = l2w_df.copy()

print("✅ Sales DataFrames Created")
print("HELP DF COLUMNS BEFORE MERGE")
print(help_df.columns.tolist())

# =========================================================
# HELP SHEET MAPPING
# =========================================================

help_merge = help_df[
    [
        "branchCode",
        "Store Name",
        "Region",
        "Channel",
        "Source",
        "Ownership"
    ]
].copy()

current_sales = current_sales.merge(
    help_merge,
    on="branchCode",
    how="left"
)

lw_sales = lw_sales.merge(
    help_merge,
    on="branchCode",
    how="left"
)

l2w_sales = l2w_sales.merge(
    help_merge,
    on="branchCode",
    how="left"
)

print("✅ Help Sheet Mapped to Current/LW/L2W")

print("Current Columns")
print(current_sales.columns.tolist())

print("LW Columns")
print(lw_sales.columns.tolist())

print("L2W Columns")
print(l2w_sales.columns.tolist())

# =========================================================
# EMPTY CHECK
# =========================================================

if sales_df.empty:

    print("❌ No Sales Data")
    exit()



# =========================================================
# SAFE COLUMN CREATION
# =========================================================

required_cols = {
    "branchCode": "",
    "branchName": "",
    "brandName": "",
    "invoiceNumber": "",
    "invoiceDay": "",
    "channel": "",
    "status": "",
    "item_shortName": "",
    "item_categoryName": "",
    "item_quantity": 0,
    "item_createdTime": "",
    "item_baseGrossAmount": 0,
    "item_baseNetDiscountAmount": 0,
    "item_baseNetAmount": 0,
    "item_discounts": "",
    "discounts": "",
    "totalMaterialCost": 0
}

for col, default in required_cols.items():

    if col not in sales_df.columns:

        sales_df[col] = default

print("✅ Safe Columns Created")


# =========================================================
# CLEAN BRANCH CODE
# =========================================================

sales_df["branchCode"] = (
    sales_df["branchCode"]
    .astype(str)
    .str.strip()
)


# =========================================================
# MERGE HELP SHEET
# =========================================================

help_merge = help_df[
    [
        "branchCode",
        "Store Name",
        "Region",
        "Channel",
        "Source",
        "Ownership"
    ]
].copy()

sales_df = sales_df.merge(
    help_merge,
    on="branchCode",
    how="left"
)

print("✅ Help Sheet Merged")


# =========================================================
# NUMERIC CLEANING
# =========================================================

numeric_cols = [
    "item_quantity",
    "item_baseGrossAmount",
    "item_baseNetDiscountAmount",
    "item_baseNetAmount",
    "totalMaterialCost"
]

for col in numeric_cols:

    sales_df[col] = pd.to_numeric(
        sales_df[col],
        errors="coerce"
    ).fillna(0)


# =========================================================
# DISCOUNT POSITIVE
# =========================================================

sales_df[
    "item_baseNetDiscountAmount"
] = (
    sales_df[
        "item_baseNetDiscountAmount"
    ]
    .abs()
)


# =========================================================
# CLEAN ITEM NAME
# =========================================================

sales_df["item_shortName"] = (
    sales_df["item_shortName"]
    .astype(str)
    .str.strip()
    .str.upper()
)

item_df["Item Name"] = (
    item_df["Item Name"]
    .astype(str)
    .str.strip()
    .str.upper()
)


# =========================================================
# ITEM GROUP MERGE
# =========================================================

sales_df = sales_df.merge(
    item_df[
        [
            "Item Name",
            "Item Group Name",
            "Product Mix",
            "Category Group"
        ]
    ],
    left_on="item_shortName",
    right_on="Item Name",
    how="left"
)

print("✅ Item Group Merged")


# =========================================================
# REMOVE ADDONS
# =========================================================

sales_df = sales_df[
    sales_df["Product Mix"]
    .astype(str)
    .str.upper()
    != "ADDONS"
].copy()

print(
    "✅ AddOns Removed:",
    len(sales_df)
)


# =========================================================
# CHANNEL GROUPING
# =========================================================

def channel_group(x):

    x = str(x).strip().upper()

    # -----------------------------------------------------
    # SWIGGY
    # -----------------------------------------------------
    if "SWIGGY" in x:
        return "Swiggy"

    # -----------------------------------------------------
    # ZOMATO
    # -----------------------------------------------------
    elif "ZOMATO" in x:
        return "Zomato"

    # -----------------------------------------------------
    # IN STORE
    # -----------------------------------------------------
    elif (
        "POS" in x
        or "DINE" in x
        or "IN-STORE" in x
        or "IN STORE" in x
        or "INSTORE" in x
    ):
        return "In Store"

    # -----------------------------------------------------
    # OWNLY
    # -----------------------------------------------------
    elif (
        "OWNLY" in x
        or "ownly" in x
    ):
        return "Ownly"

    # -----------------------------------------------------
    # WEBSITE
    # -----------------------------------------------------
    elif (
        "WEBSITE" in x
        or "Website" in x
    ):
        return "Website"

    # -----------------------------------------------------
    # OTHERS
    # -----------------------------------------------------
    return "Others"


sales_df["Channel Group"] = (
    sales_df["channel"]
    .apply(channel_group)
)

print("✅ Channel Group Created")

# =========================================================
# CHANNEL GROUP VALIDATION
# =========================================================

print("\n========== CHANNEL GROUP VALIDATION ==========")

print(
    sales_df[
        ["channel", "Channel Group"]
    ]
    .drop_duplicates()
    .sort_values(
        ["Channel Group", "channel"]
    )
    .to_string(index=False)
)

print("\nChannel Group Counts:")

print(
    sales_df["Channel Group"]
    .value_counts(dropna=False)
)

print("==============================================")

# =========================================================
# COPY CHANNEL GROUP TO ALL DATASETS
# =========================================================

if "Channel Group" not in current_sales.columns:
    current_sales["Channel Group"] = (
        current_sales["channel"]
        .apply(channel_group)
    )

if "Channel Group" not in lw_sales.columns:
    lw_sales["Channel Group"] = (
        lw_sales["channel"]
        .apply(channel_group)
    )

if "Channel Group" not in l2w_sales.columns:
    l2w_sales["Channel Group"] = (
        l2w_sales["channel"]
        .apply(channel_group)
    )

print("✅ Channel Group Created")
print(current_sales.columns.tolist())
print(lw_sales.columns.tolist())
print(l2w_sales.columns.tolist())

# =========================================================
# DISCOUNT EXTRACTION
# =========================================================

def extract_zomato_discount(x):

    x = str(x)

    match = re.search(
        r"Merchant Voucher Code\s*\((.*?)\)",
        x
    )

    if match:
        return match.group(1)

    match2 = re.search(
        r"'name':\s*'(.*?)'",
        x
    )

    if match2:
        return match2.group(1)

    return "No Offer"


def extract_swiggy_discount(x):

    x = str(x)

    match = re.search(
        r"Restaurant Discount\s*\((.*?)\)",
        x
    )

    if match:
        return match.group(1)

    return "No Offer"


sales_df[
    "Zomato Discount Code"
] = sales_df[
    "discounts"
].apply(
    extract_zomato_discount
)

sales_df[
    "Swiggy Discount Code"
] = sales_df[
    "item_discounts"
].apply(
    extract_swiggy_discount
)

print("✅ Discount Codes Extracted")


# =========================================================
# CLOSED / OPEN / VOIDED
# =========================================================

sales_df["status"] = (
    sales_df["status"]
    .astype(str)
    .str.upper()
)

closed_df = sales_df[
    sales_df["status"]
    == "CLOSED"
].copy()

open_orders = (
    sales_df[
        sales_df["status"]
        == "OPEN"
    ]["invoiceNumber"]
    .nunique()
)

voided_orders = (
    sales_df[
        sales_df["status"]
        == "VOIDED"
    ]["invoiceNumber"]
    .nunique()
)

print(
    "✅ Closed Rows:",
    len(closed_df)
)

print(
    "🟡 Open Orders:",
    open_orders
)

print(
    "🔴 Voided Orders:",
    voided_orders
)


# =========================================================
# ORDER TIME CONVERSION
# =========================================================

closed_df["Order Time"] = pd.to_datetime(
    closed_df["createdDate"],
    utc=True,
    errors="coerce"
).dt.tz_convert("Asia/Kolkata")

print("✅ Order Time Converted")


# =========================================================
# CURRENT WINDOW FILTER
# =========================================================

current_sales = closed_df[
    (
        closed_df["Order Time"]
        >= current_window_start
    )
    &
    (
        closed_df["Order Time"]
        <= current_window_end
    )
].copy()


# =========================================================
# LAST WEEK WINDOW FILTER
# =========================================================

lw_sales = closed_df[
    (
        closed_df["Order Time"]
        >= lw_window_start
    )
    &
    (
        closed_df["Order Time"]
        <= lw_window_end
    )
].copy()


print(
    "✅ Current Rows:",
    len(current_sales)
)

print(
    "✅ LW Rows:",
    len(lw_sales)
)

# =========================================================
# DEBUG CHECK - CURRENT VS LW SALES
# =========================================================

print("CURRENT SALES")
print(current_sales.shape)

print(
    current_sales[
        [
            "brandName",
            "Order Time"
        ]
    ]
    .head(20)
)

print("LW SALES")
print(lw_sales.shape)

print(
    lw_sales[
        [
            "brandName",
            "Order Time"
        ]
    ]
    .head(20)
)

print(
    "✅ Current Rows:",
    len(current_sales)
)

print(
    "✅ LW Rows:",
    len(lw_sales)
)

print("Current Brands:")
print(
    current_sales["brandName"]
    .value_counts(dropna=False)
)

print("LW Brands:")
print(
    lw_sales["brandName"]
    .value_counts(dropna=False)
)

print("Current Columns:")
print(
    current_sales.columns.tolist()
)

print("LW Columns:")
print(
    lw_sales.columns.tolist()
)

print(
    "Current Time Range:",
    current_sales["Order Time"].min(),
    "to",
    current_sales["Order Time"].max()
)

print(
    "LW Time Range:",
    lw_sales["Order Time"].min(),
    "to",
    lw_sales["Order Time"].max()
)
# =========================================================
# FIX PRODUCT MIX COLUMN
# =========================================================

if "Product Mix_x" in current_sales.columns:
    current_sales["Product Mix"] = current_sales["Product Mix_x"]
elif "Product Mix_y" in current_sales.columns:
    current_sales["Product Mix"] = current_sales["Product Mix_y"]

if "Product Mix_x" in lw_sales.columns:
    lw_sales["Product Mix"] = lw_sales["Product Mix_x"]
elif "Product Mix_y" in lw_sales.columns:
    lw_sales["Product Mix"] = lw_sales["Product Mix_y"]

if "Product Mix_x" in l2w_sales.columns:   # NEW
    l2w_sales["Product Mix"] = l2w_sales["Product Mix_x"]
elif "Product Mix_y" in l2w_sales.columns: # NEW
    l2w_sales["Product Mix"] = l2w_sales["Product Mix_y"]

print("✅ Product Mix Fixed")


# =========================================================
# CREATE HELP CHANNEL
# =========================================================

for df in [current_sales, lw_sales, l2w_sales]:

    if "Help Channel" not in df.columns:

        if "Source" in df.columns:

            df["Help Channel"] = df["Source"]

        elif "Channel" in df.columns:

            df["Help Channel"] = df["Channel"]

        elif "channel" in df.columns:

            df["Help Channel"] = df["channel"]

print("✅ Help Channel Created")
print(current_sales.columns.tolist())

print("\n===== CURRENT SALES =====")
print(current_sales[["branchCode","Channel","Source","Help Channel"]].head(10))

print("\n===== LW SALES =====")
print(lw_sales[["branchCode","Channel","Source","Help Channel"]].head(10))

print("\n===== L2W SALES =====")
print(l2w_sales[["branchCode","Channel","Source","Help Channel"]].head(10))

# =========================================================
# ITEM GROUP FOR WINDOW DATA
# =========================================================

merge_cols = [
    "Item Name",
    "Item Group Name",
    "Product Mix",
    "Category Group"
]

# Clean item_shortName for all three datasets
for df in [current_sales, lw_sales, l2w_sales]:
    df["item_shortName"] = (
        df["item_shortName"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

# Merge item group info into all three datasets
current_sales = current_sales.merge(
    item_df[merge_cols],
    left_on="item_shortName",
    right_on="Item Name",
    how="left"
)

lw_sales = lw_sales.merge(
    item_df[merge_cols],
    left_on="item_shortName",
    right_on="Item Name",
    how="left"
)

l2w_sales = l2w_sales.merge(   # NEW
    item_df[merge_cols],
    left_on="item_shortName",
    right_on="Item Name",
    how="left"
)

print("✅ Product Mix Merged")


# =========================================================
# SAFE PRODUCT MIX
# =========================================================

for df in [current_df, lw_df, l2w_df]:

    if "Product Mix" not in df.columns:

        print("⚠ Product Mix Missing")

        print(df.columns.tolist())

        df["Product Mix"] = "Others"

    df["Product Mix"] = (
        df["Product Mix"]
        .fillna("Others")
        .astype(str)
        .str.strip()
    )

print("✅ Product Mix Safe")


# =========================================================
# PRODUCT MIX DASHBOARD
# =========================================================

def create_product_mix_dashboard(
    current_sales,
    lw_sales,
    l2w_sales
):

    print("Function Current Columns")
    print(current_sales.columns.tolist())

    print("Function LW Columns")
    print(lw_sales.columns.tolist())

    brands = {
    "Frozen Bottle": "Frozen Bottle",
    "Boba Bar": "Boba Bar",
    "Madno": "Madno",
    "Lubov": "Lubov- Patisserie"
    }

    product_mix_dashboard = {}

    for brand, brand_filter in brands.items():

        curr = current_sales[
            current_sales["brandName"]
            .astype(str)
            .str.upper()
            ==
            brand_filter.upper()
        ].copy()

        lw = lw_sales[
            lw_sales["brandName"]
            .astype(str)
            .str.upper()
            ==
            brand_filter.upper()
        ].copy()

        l2w = l2w_sales[
            l2w_sales["brandName"]
            .astype(str)
            .str.upper()
            ==
            brand_filter.upper()
        ].copy()

        # =============================================
        # FIX MISSING METRIC COLUMNS
        # =============================================

        required_cols = [
            "item_quantity",
            "item_baseNetAmount",
            "item_baseNetDiscountAmount"
        ]

        for col in required_cols:

            if col not in curr.columns:

                print(
                    f"⚠ Missing Column in Current: {col}"
                )

                curr[col] = 0

            if col not in lw.columns:

                print(
                    f"⚠ Missing Column in LW: {col}"
                )

                lw[col] = 0

            if col not in l2w.columns:

                print(
                    f"⚠ Missing Column in LW: {col}"
                )

                l2w[col] = 0

        print("Curr Columns:")
        print(curr.columns.tolist())

        # =============================================
        # CURRENT MIX
        # =============================================

        curr_mix = (
            curr.groupby("Product Mix")
            .agg(
                **{
                    "Today Orders": (
                        "invoiceNumber",
                        "nunique"
                    ),
                    "Today Gross": (
                        "item_baseGrossAmount",
                        "sum"
                    ),
                    "Today Discount": (
                        "item_baseNetDiscountAmount",
                        lambda x: abs(x.sum())
                    )
                }
            )
            .reset_index()
        )
        
        curr_mix["Today Dis %"] = np.where(
            curr_mix["Today Gross"] > 0,
            curr_mix["Today Discount"] /
            curr_mix["Today Gross"] * 100,
            0
        ).round(1)



        # =============================================
        # LW MIX
        # =============================================

        lw_mix = (
            lw.groupby("Product Mix")
            .agg(
                **{
                    "LW Orders": (
                        "invoiceNumber",
                        "nunique"
                    ),
                    "LW Gross": (
                        "item_baseGrossAmount",
                        "sum"
                    ),
                    "LW Discount": (
                        "item_baseNetDiscountAmount",
                        lambda x: abs(x.sum())
                    )
                }
            )
            .reset_index()
        )
        
        lw_mix["LW Dis %"] = np.where(
            lw_mix["LW Gross"] > 0,
            lw_mix["LW Discount"] /
            lw_mix["LW Gross"] * 100,
            0
        ).round(1)

        l2w_mix = (
            l2w.groupby("Product Mix")
            .agg(
                **{
                    "L2W Orders": (
                        "invoiceNumber",
                        "nunique"
                    )
                }
            )
            .reset_index()
        )


        # =============================================
        # MERGE
        # =============================================

        final_mix = (
            curr_mix
            .merge(
                lw_mix,
                on="Product Mix",
                how="left"
            )
            .merge(
                l2w_mix,
                on="Product Mix",
                how="left"
            )
        )

        final_mix = final_mix.fillna(0)

        final_mix["Today Growth %"] = np.where(
            final_mix["LW Orders"] > 0,
            (
                final_mix["Today Orders"] /
                final_mix["LW Orders"] - 1
            ) * 100,
            0
        ).round(1)

        final_mix["LW Growth %"] = np.where(
            final_mix["L2W Orders"] > 0,
            (
                final_mix["LW Orders"] /
                final_mix["L2W Orders"] - 1
            ) * 100,
            0
        ).round(1)

        final_mix = final_mix[
            [
                "Product Mix",
                "Today Orders",
                "LW Orders",
                "L2W Orders",
                "Today Dis %",
                "LW Dis %",
                "Today Growth %",
                "LW Growth %"
            ]
        ]

        product_mix_dashboard[
            brand
        ] = final_mix

    return product_mix_dashboard


# =========================================================
# FIX ITEM METRIC COLUMNS
# =========================================================

metric_cols = [
    "item_quantity",
    "item_baseNetAmount",
    "item_baseNetDiscountAmount"
]

for col in metric_cols:

    x_col = f"{col}_x"
    y_col = f"{col}_y"

    # CURRENT SALES
    if (
        col not in current_sales.columns
        and x_col in current_sales.columns
    ):

        current_sales[col] = (
            current_sales[x_col]
            .combine_first(
                current_sales.get(y_col)
            )
        )

    # LW SALES
    if (
        col not in lw_sales.columns
        and x_col in lw_sales.columns
    ):

        lw_sales[col] = (
            lw_sales[x_col]
            .combine_first(
                lw_sales.get(y_col)
            )
        )

    # L2W SALES
    if (
        col not in l2w_sales.columns
        and x_col in l2w_sales.columns
    ):
    
        l2w_sales[col] = (
            l2w_sales[x_col]
            .combine_first(
                l2w_sales.get(y_col)
            )
        )
    # NUMERIC CONVERSION
    current_sales[col] = pd.to_numeric(
        current_sales[col],
        errors="coerce"
    ).fillna(0)

    lw_sales[col] = pd.to_numeric(
        lw_sales[col],
        errors="coerce"
    ).fillna(0)

    l2w_sales[col] = pd.to_numeric(
        l2w_sales[col],
        errors="coerce"
    ).fillna(0)

print("✅ Item Metric Columns Fixed")


# =========================================================
# FIX DASHBOARD COLUMNS
# =========================================================

dashboard_cols = [
    "Product Mix",
    "Category Group",
    "Item Group Name"
]

for col in dashboard_cols:

    x_col = f"{col}_x"
    y_col = f"{col}_y"

    if (
        col not in current_sales.columns
        and x_col in current_sales.columns
    ):

        current_sales[col] = (
            current_sales[x_col]
            .combine_first(
                current_sales.get(y_col)
            )
        )

    if (
        col not in lw_sales.columns
        and x_col in lw_sales.columns
    ):

        lw_sales[col] = (
            lw_sales[x_col]
            .combine_first(
                lw_sales.get(y_col)
            )
        )

    if (
        col not in l2w_sales.columns
        and x_col in l2w_sales.columns
    ):
    
        l2w_sales[col] = (
            l2w_sales[x_col]
            .combine_first(
                l2w_sales.get(y_col)
            )
        )
print("✅ Dashboard Columns Fixed")


# =========================================================
# DEBUG CHECK
# =========================================================

print(
    current_sales[
        [
            "item_quantity",
            "item_baseNetAmount",
            "item_baseNetDiscountAmount"
        ]
    ].head()
)


# =========================================================
# FIX DASHBOARD COLUMNS - ALL THREE WINDOWS
# =========================================================

for df_name, df in [
    ("CURRENT", current_sales),
    ("LW", lw_sales),
    ("L2W", l2w_sales)
]:

    for col in [
        "Product Mix",
        "Category Group",
        "Item Group Name"
    ]:

        if col not in df.columns:

            x_col = f"{col}_x"
            y_col = f"{col}_y"

            if x_col in df.columns:
                df[col] = df[x_col]
                if y_col in df.columns:
                    df[col] = df[col].combine_first(df[y_col])

            elif y_col in df.columns:
                df[col] = df[y_col]

        if col in df.columns:
            df[col] = (
                df[col]
                .fillna("Others")
                .astype(str)
                .str.strip()
            )

    print(f"✅ {df_name} Dashboard Columns Checked")


# =========================================================
# CREATE PRODUCT MIX DASHBOARD
# =========================================================

product_mix_dashboard = (
    create_product_mix_dashboard(
        current_sales,
        lw_sales,
        l2w_sales
    )
)

print(
    "✅ Product Mix Dashboard Created"
)

for k, v in (
    product_mix_dashboard.items()
):
    print(k, len(v))


    print(current_sales[["Channel", "Source"]].head(20))
    
# =========================================================
# PRODUCT MIX SOURCE DASHBOARD
# =========================================================

def create_product_mix_source_dashboard(
    current_sales,
    lw_sales,
    l2w_sales
):

    dashboard = {}

    for source in [
        "In Store",
        "Swiggy",
        "Zomato"
    ]:

        curr = current_sales[
            current_sales["Channel Group"]
            .astype(str)
            .str.strip()
            .str.upper()
            ==
            source.upper()
        ].copy()

        lw = lw_sales[
            lw_sales["Channel Group"]
            .astype(str)
            .str.strip()
            .str.upper()
            ==
            source.upper()
        ].copy()

        l2w = l2w_sales[
            l2w_sales["Channel Group"]
            .astype(str)
            .str.strip()
            .str.upper()
            ==
            source.upper()
        ].copy()

        dashboard[source] = create_product_mix_dashboard(
            curr,
            lw,
            l2w
        )

    return dashboard


def create_category_dashboard(
    current_sales,
    lw_sales,
    l2w_sales
):

    brands = {
        "Frozen Bottle": "Frozen Bottle",
        "Boba Bar": "Boba Bar",
        "Madno": "Madno",
        "Lubov": "Lubov- Patisserie"
    }

    category_dashboard = {}

    for brand, brand_filter in brands.items():

        curr = current_sales[
            current_sales["brandName"]
            .str.upper()
            ==
            brand_filter.upper()
        ].copy()

        lw = lw_sales[
            lw_sales["brandName"]
            .str.upper()
            ==
            brand_filter.upper()
        ].copy()

        l2w = l2w_sales[
            l2w_sales["brandName"]
            .str.upper()
            ==
            brand_filter.upper()
        ].copy()

        # ---------------- TODAY ----------------

        curr_grp = (
            curr.groupby("Category Group")
            .agg(
                **{
                    "Today Orders": (
                        "invoiceNumber",
                        "nunique"
                    ),
                    "Today Gross Rev": (
                        "item_baseGrossAmount",
                        "sum"
                    ),
                    "Today Discount": (
                        "item_baseNetDiscountAmount",
                        lambda x: abs(x.sum())
                    )
                }
            )
            .reset_index()
        )

        curr_grp["Today Dis %"] = np.where(
            curr_grp["Today Gross Rev"] > 0,
            (
                curr_grp["Today Discount"]
                /
                curr_grp["Today Gross Rev"]
            ) * 100,
            0
        ).round(1)

        # ---------------- LW ----------------

        lw_grp = (
            lw.groupby("Category Group")
            .agg(
                **{
                    "LW Orders": (
                        "invoiceNumber",
                        "nunique"
                    ),
                    "LW Gross Rev": (
                        "item_baseGrossAmount",
                        "sum"
                    ),
                    "LW Discount": (
                        "item_baseNetDiscountAmount",
                        lambda x: abs(x.sum())
                    )
                }
            )
            .reset_index()
        )

        lw_grp["LW Dis %"] = np.where(
            lw_grp["LW Gross Rev"] > 0,
            (
                lw_grp["LW Discount"]
                /
                lw_grp["LW Gross Rev"]
            ) * 100,
            0
        ).round(1)

        # ---------------- L2W ----------------

        l2w_grp = (
            l2w.groupby("Category Group")
            .agg(
                **{
                    "L2W Orders": (
                        "invoiceNumber",
                        "nunique"
                    )
                }
            )
            .reset_index()
        )

        # ---------------- MERGE ----------------

        final = (
            curr_grp
            .merge(
                lw_grp,
                on="Category Group",
                how="outer"
            )
            .merge(
                l2w_grp,
                on="Category Group",
                how="outer"
            )
        )

        final = final.fillna(0)

        final["Today Growth %"] = np.where(
            final["LW Orders"] > 0,
            (
                final["Today Orders"]
                /
                final["LW Orders"]
                - 1
            ) * 100,
            0
        ).round(1)

        final["LW Growth %"] = np.where(
            final["L2W Orders"] > 0,
            (
                final["LW Orders"]
                /
                final["L2W Orders"]
                - 1
            ) * 100,
            0
        ).round(1)

        final = final[
            [
                "Category Group",
                "Today Orders",
                "LW Orders",
                "L2W Orders",
                "Today Dis %",
                "LW Dis %",
                "Today Growth %",
                "LW Growth %"
            ]
        ]

        category_dashboard[brand] = final

    return category_dashboard

# =========================================================
# CATEGORY SOURCE DASHBOARD
# =========================================================

def create_category_source_dashboard(
    current_sales,
    lw_sales,
    l2w_sales
):

    dashboard = {}

    for source in [
        "In Store",
        "Swiggy",
        "Zomato"
    ]:

        curr = current_sales[
            current_sales["Channel Group"]
            .astype(str)
            .str.strip()
            .str.upper()
            ==
            source.upper()
        ].copy()

        lw = lw_sales[
            lw_sales["Channel Group"]
            .astype(str)
            .str.strip()
            .str.upper()
            ==
            source.upper()
        ].copy()

        l2w = l2w_sales[
            l2w_sales["Channel Group"]
            .astype(str)
            .str.strip()
            .str.upper()
            ==
            source.upper()
        ].copy()

        dashboard[source] = create_category_dashboard(
            curr,
            lw,
            l2w
        )

    return dashboard


# =========================================================
# REGION + CATEGORY DASHBOARD
# =========================================================

def create_region_category_dashboard(
    current_sales,
    lw_sales,
    l2w_sales
):

    dashboard = {}

    regions = sorted(
        current_sales["Region"]
        .dropna()
        .unique()
    )

    for region in regions:

        curr = current_sales[
            current_sales["Region"] == region
        ].copy()

        lw = lw_sales[
            lw_sales["Region"] == region
        ].copy()

        l2w = l2w_sales[
            l2w_sales["Region"] == region
        ].copy()

        dashboard[region] = (
            create_category_dashboard(
                curr,
                lw,
                l2w
            )
        )

    return dashboard

# =========================================================
# REGION + PRODUCT MIX SOURCE DASHBOARD
# =========================================================

def create_region_product_mix_source_dashboard(
    current_sales,
    lw_sales,
    l2w_sales
):

    dashboard = {}

    regions = sorted(
        current_sales["Region"]
        .dropna()
        .unique()
    )

    sources = [
        "In Store",
        "Swiggy",
        "Zomato"
    ]

    for region in regions:

        dashboard[region] = {}

        for source in sources:

            curr = current_sales[
                (current_sales["Region"] == region)
                &
                (
                    current_sales["Source"]
                    .astype(str)
                    .str.contains(
                        source,
                        na=False
                    )
                )
            ].copy()

            lw = lw_sales[
                (lw_sales["Region"] == region)
                &
                (
                    lw_sales["Source"]
                    .astype(str)
                    .str.contains(
                        source,
                        na=False
                    )
                )
            ].copy()

            l2w = l2w_sales[
                (l2w_sales["Region"] == region)
                &
                (
                    l2w_sales["Source"]
                    .astype(str)
                    .str.contains(
                        source,
                        na=False
                    )
                )
            ].copy()

            dashboard[region][source] = (
                create_product_mix_dashboard(
                    curr,
                    lw,
                    l2w
                )
            )

    return dashboard

# =========================================================
# CATEGORY CHANNEL DASHBOARD - FIXED BUG
# =========================================================

def create_category_channel_dashboard(
    current_sales,
    lw_sales,
    l2w_sales
):

    channel_groups = [
        "In Store",
        "Swiggy",
        "Zomato"
    ]

    dashboard = {}

    for channel in channel_groups:

        print(f"\n===== {channel} =====")
    
        curr = current_sales[
            current_sales["Channel Group"]
            .astype(str)
            .str.strip()
            .str.upper()
            ==
            channel.upper()
        ].copy()
    
        lw = lw_sales[
            lw_sales["Channel Group"]
            .astype(str)
            .str.strip()
            .str.upper()
            ==
            channel.upper()
        ].copy()

        l2w = l2w_sales[
            l2w_sales["Channel Group"]
            .astype(str)
            .str.strip()
            .str.upper()
            ==
            channel.upper()
        ].copy()
    
        print(
            "Current Rows:",
            len(curr)
        )
    
        print(
            "LW Rows:",
            len(lw)
        )
        
        print(
            "L2W Rows:",
            len(l2w)
        )

        # =========================================
        # CURRENT AGGREGATION - FIXED
        # =========================================

        curr_cat = (
            curr.groupby("Category Group")
            .agg(
                Orders=("invoiceNumber", "nunique"),
                Qty_Sold=("item_quantity", "sum"),
                Net_Rev=("item_baseNetAmount", "sum"),
                Gross_Rev=("item_baseGrossAmount", "sum"),
                Discount=(
                    "item_baseNetDiscountAmount",
                    lambda x: abs(x.sum())
                )
            )
            .reset_index()
        )

        curr_cat["Dis %"] = np.where(
            curr_cat["Gross_Rev"] > 0,
            (
                curr_cat["Discount"]
                / curr_cat["Gross_Rev"]
            ) * 100,
            0
        ).round(1)

        # =========================================
        # LAST WEEK AGGREGATION - FIXED BUG
        # Changed from wrong column names to correct ones
        # Changed from nunique to sum, and sum to nunique
        # =========================================

        lw_cat = (
            lw.groupby("Category Group")
            .agg(
                LW_Net_Rev=("item_baseNetAmount", "sum"),
                LW_Gross_Rev=("item_baseGrossAmount", "sum"),
                LW_Qty=("item_quantity", "sum"),
                LW_Orders=("invoiceNumber", "nunique"),
                LW_Discount=(
                    "item_baseNetDiscountAmount",
                    lambda x: abs(x.sum())
                )
            )
            .reset_index()
        )

        lw_cat["LW Dis %"] = np.where(
            lw_cat["LW_Gross_Rev"] > 0,
            (
                lw_cat["LW_Discount"]
                / lw_cat["LW_Gross_Rev"]
            ) * 100,
            0
        ).round(1)

        final_cat = (
            curr_cat.merge(
                lw_cat,
                on="Category Group",
                how="left"
            )
            .fillna(0)
        )

        final_cat["Growth %"] = np.where(
            final_cat["LW_Net_Rev"] > 0,
            (
                (
                    final_cat["Net_Rev"]
                    - final_cat["LW_Net_Rev"]
                )
                / final_cat["LW_Net_Rev"]
            ) * 100,
            0
        ).round(1)

        final_cat = final_cat.sort_values(
            "Net_Rev",
            ascending=False
        )

        dashboard[channel] = final_cat

    return dashboard

# =========================================================
# FIX CATEGORY GROUP COLUMN
# =========================================================

if (
    "Category Group" not in current_sales.columns
    and
    "Category Group_x" in current_sales.columns
):

    current_sales["Category Group"] = (
        current_sales["Category Group_x"]
        .combine_first(
            current_sales.get(
                "Category Group_y"
            )
        )
    )

if (
    "Category Group" not in lw_sales.columns
    and
    "Category Group_x" in lw_sales.columns
):

    lw_sales["Category Group"] = (
        lw_sales["Category Group_x"]
        .combine_first(
            lw_sales.get(
                "Category Group_y"
            )
        )
    )

print("✅ Category Group Fixed")

# =========================================================
# CREATE CATEGORY DASHBOARD
# =========================================================

category_dashboard = (
    create_category_dashboard(
        current_sales,
        lw_sales,
        l2w_sales
    )
)

print(
    "✅ Category Dashboard Created"
)

for k, v in (
    category_dashboard.items()
):
    print(
        k,
        len(v)
    )

# =========================================================
# CATEGORY SOURCE DASHBOARD
# =========================================================

category_source_dashboard = (
    create_category_source_dashboard(
        current_sales,
        lw_sales,
        l2w_sales
    )
)

print("✅ Category Source Dashboard Created")

for k, v in (
    category_source_dashboard.items()
):
    print(
        k,
        len(v)
    )

# =========================================================
# CATEGORY CHANNEL DASHBOARD
# =========================================================

category_channel_dashboard = (
    create_category_channel_dashboard(
        current_sales,
        lw_sales,
        l2w_sales
        
    )
)

print(
    "✅ Category Channel Dashboard Created"
)

for k, v in (
    category_channel_dashboard.items()
):
    print(
        k,
        len(v)
    )


# =========================================================
# Product Mix Source DASHBOARD
# =========================================================

product_mix_source_dashboard = (
    create_product_mix_source_dashboard(
        current_sales,
        lw_sales,
        l2w_sales
        
    )
)

print(
    "✅ Product Mix Source Dashboard Created"
)

for k, v in (
    product_mix_source_dashboard.items()
):
    print(
        k,
        len(v)
    )


# =========================================================
# ITEM DASHBOARD
# =========================================================

def create_item_dashboard(
    current_sales,
    lw_sales,
    l2w_sales
):

    brands = {
        "Frozen Bottle": "Frozen Bottle",
        "Boba Bar": "Boba Bar",
        "Madno": "Madno",
        "Lubov": "Lubov- Patisserie"
    }

    item_dashboard = {}

    for brand, brand_filter in brands.items():

        curr = current_sales[
            current_sales["brandName"]
            .str.upper()
            ==
            brand_filter.upper()
        ].copy()

        lw = lw_sales[
            lw_sales["brandName"]
            .str.upper()
            ==
            brand_filter.upper()
        ].copy()

        l2w = l2w_sales[
            l2w_sales["brandName"]
            .str.upper()
            ==
            brand_filter.upper()
        ].copy()

        # ---------------- TODAY ----------------

        curr_grp = (
            curr.groupby("Item Group Name")
            .agg(
                **{
                    "Today Orders": (
                        "invoiceNumber",
                        "nunique"
                    ),

                    "Today Gross Rev": (
                        "item_baseGrossAmount",
                        "sum"
                    ),

                    "Today Discount": (
                        "item_baseNetDiscountAmount",
                        lambda x: abs(x.sum())
                    )
                }
            )
            .reset_index()
        )

        curr_grp["Today Dis %"] = np.where(
            curr_grp["Today Gross Rev"] > 0,
            (
                curr_grp["Today Discount"]
                /
                curr_grp["Today Gross Rev"]
            ) * 100,
            0
        ).round(1)

        # ---------------- LW ----------------

        lw_grp = (
            lw.groupby("Item Group Name")
            .agg(
                **{
                    "LW Orders": (
                        "invoiceNumber",
                        "nunique"
                    ),

                    "LW Gross Rev": (
                        "item_baseGrossAmount",
                        "sum"
                    ),

                    "LW Discount": (
                        "item_baseNetDiscountAmount",
                        lambda x: abs(x.sum())
                    )
                }
            )
            .reset_index()
        )

        lw_grp["LW Dis %"] = np.where(
            lw_grp["LW Gross Rev"] > 0,
            (
                lw_grp["LW Discount"]
                /
                lw_grp["LW Gross Rev"]
            ) * 100,
            0
        ).round(1)

        # ---------------- L2W ----------------

        l2w_grp = (
            l2w.groupby("Item Group Name")
            .agg(
                **{
                    "L2W Orders": (
                        "invoiceNumber",
                        "nunique"
                    )
                }
            )
            .reset_index()
        )

        # ---------------- MERGE ----------------

        final = (
            curr_grp
            .merge(
                lw_grp,
                on="Item Group Name",
                how="outer"
            )
            .merge(
                l2w_grp,
                on="Item Group Name",
                how="outer"
            )
        )

        final = final.fillna(0)

        final["Today Growth %"] = np.where(
            final["LW Orders"] > 0,
            (
                final["Today Orders"]
                /
                final["LW Orders"]
                - 1
            ) * 100,
            0
        ).round(1)

        final["LW Growth %"] = np.where(
            final["L2W Orders"] > 0,
            (
                final["LW Orders"]
                /
                final["L2W Orders"]
                - 1
            ) * 100,
            0
        ).round(1)

        final = final[
            [
                "Item Group Name",
                "Today Orders",
                "LW Orders",
                "L2W Orders",
                "Today Dis %",
                "LW Dis %",
                "Today Growth %",
                "LW Growth %"
            ]
        ]

        item_dashboard[brand] = final

    return item_dashboard

# =========================================================
# ITEM SOURCE DASHBOARD
# =========================================================

def create_item_source_dashboard(
    current_sales,
    lw_sales,
    l2w_sales
):

    dashboard = {}

    for source in [
        "In Store",
        "Swiggy",
        "Zomato"
    ]:

        curr = current_sales[
            current_sales["Channel Group"]
            .astype(str)
            .str.strip()
            .str.upper()
            ==
            source.upper()
        ].copy()

        lw = lw_sales[
            lw_sales["Channel Group"]
            .astype(str)
            .str.strip()
            .str.upper()
            ==
            source.upper()
        ].copy()

        l2w = l2w_sales[
            l2w_sales["Channel Group"]
            .astype(str)
            .str.strip()
            .str.upper()
            ==
            source.upper()
        ].copy()

        dashboard[source] = create_item_dashboard(
            curr,
            lw,
            l2w
        )

    return dashboard

# =========================================================
# FIX ITEM GROUP NAME COLUMN
# =========================================================

if (
    "Item Group Name" not in current_sales.columns
    and
    "Item Group Name_x" in current_sales.columns
):

    current_sales["Item Group Name"] = (
        current_sales["Item Group Name_x"]
        .combine_first(
            current_sales.get(
                "Item Group Name_y"
            )
        )
    )

if (
    "Item Group Name" not in lw_sales.columns
    and
    "Item Group Name_x" in lw_sales.columns
):

    lw_sales["Item Group Name"] = (
        lw_sales["Item Group Name_x"]
        .combine_first(
            lw_sales.get(
                "Item Group Name_y"
            )
        )
    )

print("✅ Item Group Name Fixed")

# =========================================================
# CREATE ITEM DASHBOARD
# =========================================================

item_dashboard = (
    create_item_dashboard(
        current_sales,
        lw_sales,
        l2w_sales
    )
)

print("✅ Item Dashboard Created")

for k, v in (
    item_dashboard.items()
):
    print(
        k,
        len(v)
    )

# =========================================================
# ITEM SOURCE DASHBOARD
# =========================================================

item_source_dashboard = (
    create_item_source_dashboard(
        current_sales,
        lw_sales,
        l2w_sales
    )
)

print("✅ Item Source Dashboard Created")

for k, v in (
    item_source_dashboard.items()
):
    print(
        k,
        len(v)
    )

# =========================================================
# REGION CATEGORY DASHBOARD
# =========================================================

region_category_dashboard = (
    create_region_category_dashboard(
        current_sales,
        lw_sales,
        l2w_sales
    )
)

print("✅ Region Category Dashboard Created")

for k, v in (
    region_category_dashboard.items()
):
    print(
        k,
        len(v)
    )

# =========================================================
# REGION + PRODUCT MIX SOURCE DASHBOARD
# =========================================================

def create_region_product_mix_source_dashboard(
    current_sales,
    lw_sales,
    l2w_sales
):

    dashboard = {}

    regions = sorted(
        current_sales["Region"]
        .dropna()
        .astype(str)
        .str.strip()
        .unique()
    )

    sources = [
        "In Store",
        "Swiggy",
        "Zomato"
    ]

    for region in regions:

        dashboard[region] = {}

        for source in sources:

            curr = current_sales[
                (
                    current_sales["Region"]
                    .astype(str)
                    .str.strip()
                    ==
                    region
                )
                &
                (
                    current_sales["Channel Group"]
                    .astype(str)
                    .str.strip()
                    .str.upper()
                    ==
                    source.upper()
                )
            ].copy()

            lw = lw_sales[
                (
                    lw_sales["Region"]
                    .astype(str)
                    .str.strip()
                    ==
                    region
                )
                &
                (
                    lw_sales["Channel Group"]
                    .astype(str)
                    .str.strip()
                    .str.upper()
                    ==
                    source.upper()
                )
            ].copy()

            l2w = l2w_sales[
                (
                    l2w_sales["Region"]
                    .astype(str)
                    .str.strip()
                    ==
                    region
                )
                &
                (
                    l2w_sales["Channel Group"]
                    .astype(str)
                    .str.strip()
                    .str.upper()
                    ==
                    source.upper()
                )
            ].copy()

            dashboard[region][source] = (
                create_product_mix_dashboard(
                    curr,
                    lw,
                    l2w
                )
            )

    return dashboard

# =========================================================
# DISCOUNT CODE EXTRACTION
# =========================================================

import ast
import re


def extract_swiggy_code(x):

    try:

        if pd.isna(x):
            return "No Offer"

        if isinstance(x, str):

            x = ast.literal_eval(x)

        if not x:
            return "No Offer"

        first = x[0]

        name = first.get(
            "name",
            ""
        )

        # Example:
        # Restaurant Discount (15% off)

        match = re.search(
            r"\((.*?)\)",
            name
        )

        if match:
            return match.group(1)

        return name

    except:
        return "No Offer"


def extract_zomato_code(x):

    try:

        if pd.isna(x):
            return "No Offer"

        if isinstance(x, str):

            x = ast.literal_eval(x)

        if not x:
            return "No Offer"

        first = x[0]

        name = first.get(
            "name",
            ""
        )

        # Merchant Voucher Code (TRYNEW)

        match = re.search(
            r"Merchant Voucher Code\s*\((.*?)\)",
            name
        )

        if match:
            return match.group(1)

        return name

    except:
        return "No Offer"

# =========================================================
# FIX ITEM METRIC COLUMNS
# =========================================================

for col in [
    "item_quantity",
    "item_baseNetAmount",
    "item_baseNetDiscountAmount"
]:

    if (
        col not in current_sales.columns
        and
        f"{col}_x" in current_sales.columns
    ):

        current_sales[col] = (
            current_sales[f"{col}_x"]
            .combine_first(
                current_sales.get(
                    f"{col}_y"
                )
            )
        )

    # convert numeric
    current_sales[col] = pd.to_numeric(
        current_sales[col],
        errors="coerce"
    ).fillna(0)

print("✅ Item Metric Columns Fixed")


# =========================================================
# FIX DISCOUNT COLUMNS
# =========================================================

if (
    "item_discounts" not in current_sales.columns
    and
    "item_discounts_x" in current_sales.columns
):

    current_sales["item_discounts"] = (
        current_sales["item_discounts_x"]
        .combine_first(
            current_sales.get(
                "item_discounts_y"
            )
        )
    )

if (
    "discounts" not in current_sales.columns
    and
    "discounts_x" in current_sales.columns
):

    current_sales["discounts"] = (
        current_sales["discounts_x"]
        .combine_first(
            current_sales.get(
                "discounts_y"
            )
        )
    )

print("✅ Discount Columns Fixed")

# =========================================================
# FIX LW DISCOUNT COLUMNS
# =========================================================

if (
    "item_discounts" not in lw_sales.columns
    and
    "item_discounts_x" in lw_sales.columns
):

    lw_sales["item_discounts"] = (
        lw_sales["item_discounts_x"]
        .combine_first(
            lw_sales.get(
                "item_discounts_y"
            )
        )
    )

if (
    "discounts" not in lw_sales.columns
    and
    "discounts_x" in lw_sales.columns
):

    lw_sales["discounts"] = (
        lw_sales["discounts_x"]
        .combine_first(
            lw_sales.get(
                "discounts_y"
            )
        )
    )

print("✅ LW Discount Columns Fixed")

# =========================================================
# CREATE CODE COLUMNS - CURRENT
# =========================================================

current_sales["Swiggy Code"] = (
    current_sales["item_discounts"]
    .apply(extract_swiggy_code)
)

current_sales["Zomato Code"] = (
    current_sales["discounts"]
    .apply(extract_zomato_code)
)

print("✅ Current Discount Codes Extracted")

# =========================================================
# CREATE CODE COLUMNS - LW
# =========================================================

lw_sales["Swiggy Code"] = (
    lw_sales["item_discounts"]
    .apply(extract_swiggy_code)
)

lw_sales["Zomato Code"] = (
    lw_sales["discounts"]
    .apply(extract_zomato_code)
)

print("✅ LW Discount Codes Extracted")

# =========================================================
# DASHBOARD FUNCTION - FIXED BUG
# =========================================================

def create_discount_dashboard(
    current_sales,
    lw_sales,
    code_col,
    channel_name
):

    brands = {
        "Frozen Bottle": "Frozen Bottle",
        "Boba Bar": "Boba Bar",
        "Madno": "Madno",
        "Lubov": "Lubov- Patisserie"
    }

    dashboard = {}

    for brand, brand_filter in brands.items():

        # =====================================================
        # CURRENT SALES
        # =====================================================

        temp = current_sales[
            (
                current_sales["brandName"]
                .astype(str)
                .str.upper()
                ==
                brand_filter.upper()
            )
            &
            (
                current_sales["channel"]
                .astype(str)
                .str.upper()
                .str.contains(
                    channel_name.upper(),
                    na=False
                )
            )
        ].copy()

        # =====================================================
        # LAST WEEK SALES
        # =====================================================

        lw_temp = lw_sales[
            (
                lw_sales["brandName"]
                .astype(str)
                .str.upper()
                ==
                brand_filter.upper()
            )
            &
            (
                lw_sales["channel"]
                .astype(str)
                .str.upper()
                .str.contains(
                    channel_name.upper(),
                    na=False
                )
            )
        ].copy()

        # =====================================================
        # CURRENT SUMMARY - FIXED
        # =====================================================

        code_df = (
            temp.groupby(code_col)
            .agg(
                Orders=("invoiceNumber", "nunique"),
                Qty_Sold=("item_quantity", "sum"),
                Gross_Rev=("item_baseGrossAmount", "sum"),
                Net_Rev=("item_baseNetAmount", "sum"),
                Discount_Given=(
                    "item_baseNetDiscountAmount",
                    lambda x: abs(x.sum())
                )
            )
            .reset_index()
        )

        # =====================================================
        # CURRENT DIS %
        # =====================================================

        code_df["Dis %"] = np.where(
            code_df["Net_Rev"] > 0,
            (
                code_df["Discount_Given"]
                /
                code_df["Net_Rev"]
            ) * 100,
            0
        ).round(1)

        # =====================================================
        # CURRENT AOV
        # =====================================================

        code_df["AOV"] = np.where(
            code_df["Orders"] > 0,
            code_df["Net_Rev"]
            /
            code_df["Orders"],
            0
        ).round(1)

        # =====================================================
        # LW SUMMARY - FIXED
        # =====================================================

        lw_code_df = (
            lw_temp.groupby(code_col)
            .agg(
                LW_Orders=("invoiceNumber", "nunique"),
                LW_Gross_Rev=("item_baseGrossAmount", "sum"),
                LW_Qty=("item_quantity", "sum"),
                LW_Net_Rev=("item_baseNetAmount", "sum"),
                LW_Discount=(
                    "item_baseNetDiscountAmount",
                    lambda x: abs(x.sum())
                )
            )
            .reset_index()
        )

        # =====================================================
        # MERGE CURRENT + LW
        # =====================================================

        code_df = code_df.merge(
            lw_code_df,
            on=code_col,
            how="left"
        ).fillna(0)

        # =====================================================
        # LW DIS %
        # =====================================================

        code_df["LW Dis %"] = np.where(
            code_df["LW_Net_Rev"] > 0,
            (
                code_df["LW_Discount"]
                /
                code_df["LW_Gross_Rev"]
            ) * 100,
            0
        ).round(1)

        # =====================================================
        # LW AOV
        # =====================================================

        code_df["LW AOV"] = np.where(
            code_df["LW_Orders"] > 0,
            code_df["LW_Net_Rev"]
            /
            code_df["LW_Orders"],
            0
        ).round(1)

        # =====================================================
        # SORT
        # =====================================================

        code_df = (
            code_df
            .sort_values(
                "Orders",
                ascending=False
            )
            .head(15)
        )

        # =====================================================
        # FINAL COLUMN ORDER
        # =====================================================

        code_df = code_df[
            [
                code_col,
                "Orders",
                "Qty_Sold",
                "Net_Rev",
                "Discount_Given",
                "Dis %",
                "AOV",
                "LW_Orders",
                "LW_Qty",
                "LW_Net_Rev",
                "LW_Discount",
                "LW Dis %",
                "LW AOV"
            ]
        ]

        dashboard[brand] = code_df

    return dashboard


# =========================================================
# SWIGGY DASHBOARD
# =========================================================

swiggy_discount_dashboard = (
    create_discount_dashboard(
        current_sales,
        lw_sales,
        "Swiggy Code",
        "Swiggy"
    )
)

print(
    "✅ Swiggy Discount Dashboard Created"
)

# =========================================================
# ZOMATO DASHBOARD
# =========================================================

zomato_discount_dashboard = (
    create_discount_dashboard(
        current_sales,
        lw_sales,
        "Zomato Code",
        "Zomato"
    )
)

print(
    "✅ Zomato Discount Dashboard Created"
)

# =========================================================
# CHECK
# =========================================================

for k, v in (
    swiggy_discount_dashboard.items()
):
    print(
        "Swiggy",
        k,
        len(v)
    )

for k, v in (
    zomato_discount_dashboard.items()
):
    print(
        "Zomato",
        k,
        len(v)
    )

# =========================================================
# GOOGLE SHEET DASHBOARD UPDATE
# =========================================================

DASHBOARD_SPREADSHEET_ID = (
    "1ldgnNMdeubDx_ImtCC1uCD7FGz8gt_edmWEkmO_xRNk"
)

dashboard_spreadsheet = (
    client.open_by_key(
        DASHBOARD_SPREADSHEET_ID
    )
)

print("✅ Dashboard File Connected")


# =========================================================
# SAFE TAB FUNCTION
# =========================================================

def get_or_create_sheet(sheet_name):

    try:
        ws = dashboard_spreadsheet.worksheet(
            sheet_name
        )

    except:

        ws = (
            dashboard_spreadsheet
            .add_worksheet(
                title=sheet_name,
                rows=1000,
                cols=50
            )
        )

    return ws


# =========================================================
# UPDATE SHEET FUNCTION
# =========================================================

def update_sheet(
    sheet_name,
    df,
    start_cell="A1"
):

    ws = get_or_create_sheet(
        sheet_name
    )

    # refresh tab
    ws.clear()

    if df.empty:

        print(
            f"⚠ No data for sheet: {sheet_name}"
        )
        return

    data = (
        [df.columns.tolist()]
        +
        df.fillna("")
        .values
        .tolist()
    )

    ws.update(
        start_cell,
        data
    )

    print(
        f"✅ Updated: {sheet_name}"
    )


# =========================================================
# 2. PRODUCT MIX
# =========================================================

product_ws = get_or_create_sheet(
    "Product Mix Dashboard"
)

product_ws.clear()

row_num = 1

for brand, df in (
    product_mix_dashboard.items()
):

    product_ws.update(
        f"A{row_num}",
        [[brand]]
    )

    row_num += 1

    data = (
        [df.columns.tolist()]
        +
        df.fillna("")
        .values.tolist()
    )

    product_ws.update(
        f"A{row_num}",
        data
    )

    row_num += len(df) + 4

print(
    "✅ Product Mix Updated"
)


# =========================================================
# 3. CATEGORY DASHBOARD
# =========================================================

category_ws = get_or_create_sheet(
    "Category Dashboard"
)

category_ws.clear()

row_num = 1

for brand, df in (
    category_dashboard.items()
):

    category_ws.update(
        f"A{row_num}",
        [[brand]]
    )

    row_num += 1

    data = (
        [df.columns.tolist()]
        +
        df.fillna("")
        .values.tolist()
    )

    category_ws.update(
        f"A{row_num}",
        data
    )

    row_num += len(df) + 4

print(
    "✅ Category Dashboard Updated"
)


# =========================================================
# 4. TOP ITEMS
# =========================================================

item_ws = get_or_create_sheet(
    "Top 15 Items"
)

item_ws.clear()

row_num = 1

for brand, df in (
    item_dashboard.items()
):

    item_ws.update(
        f"A{row_num}",
        [[brand]]
    )

    row_num += 1

    data = (
        [df.columns.tolist()]
        +
        df.fillna("")
        .values.tolist()
    )

    item_ws.update(
        f"A{row_num}",
        data
    )

    row_num += len(df) + 4

print(
    "✅ Item Dashboard Updated"
)


# =========================================================
# 5. SWIGGY DISCOUNT
# =========================================================

swiggy_ws = get_or_create_sheet(
    "Swiggy Discount Dashboard"
)

swiggy_ws.clear()

row_num = 1

for brand, df in (
    swiggy_discount_dashboard.items()
):

    swiggy_ws.update(
        f"A{row_num}",
        [[brand]]
    )

    row_num += 1

    data = (
        [df.columns.tolist()]
        +
        df.fillna("")
        .values.tolist()
    )

    swiggy_ws.update(
        f"A{row_num}",
        data
    )

    row_num += len(df) + 4

print(
    "✅ Swiggy Dashboard Updated"
)


# =========================================================
# 6. ZOMATO DISCOUNT
# =========================================================

zomato_ws = get_or_create_sheet(
    "Zomato Discount Dashboard"
)

zomato_ws.clear()

row_num = 1

for brand, df in (
    zomato_discount_dashboard.items()
):

    zomato_ws.update(
        f"A{row_num}",
        [[brand]]
    )

    row_num += 1

    data = (
        [df.columns.tolist()]
        +
        df.fillna("")
        .values.tolist()
    )

    zomato_ws.update(
        f"A{row_num}",
        data
    )

    row_num += len(df) + 4

print(
    "✅ Zomato Dashboard Updated"
)

print(
    "✅ ALL DASHBOARDS REFRESHED"
)

# =========================================================
# HOURLY WINDOW
# =========================================================

from datetime import datetime
import pytz

ist = pytz.timezone("Asia/Kolkata")

current_time = datetime.now(ist)

start_hour = "09:00 AM"
end_hour = current_time.strftime("%I:%M %p")

hourly_window = (
    f"{start_hour} - {end_hour}"
)

print(
    "⏰ Hourly Window:",
    hourly_window
)

# =========================================================
# FORMAT DATE
# =========================================================

formatted_date = (
    business_date
    .strftime("%d-%m-%Y")
)

print(
    f"📅 Formatted Date: "
    f"{formatted_date}"
)

# =========================================================
# DISCOUNT HTML TABLE
# =========================================================

def create_discount_html(
    dashboard,
    title
):

    html = f"""
    <hr>
    <h3>{title}</h3>
    """

    for brand, df in dashboard.items():

        html += f"""
        <h4>{brand}</h4>
        """
        
        if df.empty:

            html += """
            <p>No Data Available</p>
            """
            continue

        html += apply_growth_style(df)

        html += "<br>"

    return html


# =========================================================
# APPLY GROWTH STYLE
# =========================================================

def apply_growth_style(df):

    html = """
    <table border="0"
    style="
    border-collapse:collapse;
    width:100%;
    text-align:center;
    font-family:Arial;
    font-size:12px;
    ">
    """

    # ==========================================
    # HEADER
    # ==========================================

    html += "<tr>"

    for col in df.columns:

        html += f"""
        <th style="
        background:#1f4e78;
        color:white;
        padding:8px;
        border:1px solid #ddd;
        text-align:center;
        ">
        {col}
        </th>
        """
        
    html += "</tr>"

    # ==========================================
    # ROWS
    # ==========================================

    for _, row in df.iterrows():

        html += "<tr>"

        for col in df.columns:

            val = row[col]

            # ==========================================
            # REMOVE DECIMALS
            # ==========================================

            try:

                if isinstance(val, (int, float)):

                    val = round(val)

            except:
                pass
            # Growth Highlight
            if "Growth" in col:

                color = (
                    "#d9ead3"
                    if val >= 0
                    else "#f4cccc"
                )

                html += f"""
                <td style="
                background:{color};
                padding:6px;
                border:1px solid #ddd;
                text-align:center;
                font-weight:bold;
                ">
                {val}%
                </td>
                """
                
            else:

                html += f"""
                <td style="
                padding:6px;
                border:1px solid #ddd;
                text-align:center;
                ">
                {val}
                </td>
                """
                
        html += "</tr>"
        
    html += "</table><br>"
    
    return html
    

# =========================================================
# CREATE HTML SUMMARY
# =========================================================

print("📄 Creating Summary HTML...")


# =========================================================
# TIME WINDOW
# =========================================================

from datetime import zoneinfo
from zoneinfo import ZoneInfo

ist = ZoneInfo("Asia/Kolkata")

current_time = datetime.now(ist)

start_hour = "09:00 AM"

end_hour = (
    current_time
    .replace(
        minute=0,
        second=0,
        microsecond=0
    )
    .strftime("%I:%M %p")
)

hourly_window = (
    f"{start_hour} - {end_hour}"
)

print(
    "⏰ Hourly Window:",
    hourly_window
)

table_style = """
<style>

table {
    border-collapse: collapse;
    width: 100%;
    text-align: center;
    font-size: 12px;
}

th {
    background: #1f4e78;
    color: white;
    padding: 8px;
    border: 1px solid #ddd;
}

td {
    padding: 6px;
    border: 1px solid #ddd;
    text-align: center;
}

.growth-positive {
    background-color: #d9ead3;
    font-weight: bold;
}

.growth-negative {
    background-color: #f4cccc;
    font-weight: bold;
}

</style>
"""


# =========================================================
# HTML BODY
# =========================================================

summary_html = f"""
<html>

<head>
{table_style}
</head>

<body>

<h2>📊 Product Level Sales Dashboard</h2>

<p>
<b>Business Date:</b> {business_date}
</p>

<p>
<b>Hourly Window:</b>
{hourly_window}
</p>

<hr>

<h3>🍨 Product Mix Dashboard</h3>

"""


# =========================================================
# PRODUCT MIX DASHBOARD
# =========================================================

for brand, df in product_mix_dashboard.items():

    summary_html += f"""
    <h4>{brand}</h4>
    """

    if df.empty:

        summary_html += """
        <p>No Data Available</p>
        """

    else:

        summary_html += apply_growth_style(df)


# =========================================================
# PRODUCT MIX SOURCE DASHBOARD
# =========================================================

summary_html += """
<hr>

<h3>🍨 Product Mix Source Dashboard</h3>
"""

for source, brand_data in product_mix_source_dashboard.items():

    summary_html += f"""
    <h3>{source}</h3>
    """

    for brand, df in brand_data.items():

        summary_html += f"""
        <h4>{brand}</h4>
        """

        if df.empty:

            summary_html += """
            <p>No Data Available</p>
            """

        else:

            summary_html += apply_growth_style(df)

# =========================================================
# CATEGORY DASHBOARD
# =========================================================

summary_html += """
<hr>
<h3>📦 Category Dashboard</h3>
"""

for brand, df in category_dashboard.items():

    summary_html += f"""
    <h4>{brand}</h4>
    """

    if df.empty:

        summary_html += """
        <p>No Data Available</p>
        """

    else:

        summary_html += (
            apply_growth_style(
                df.head(10)
            )
        )

# =========================================================
# CATEGORY SOURCE DASHBOARD
# =========================================================

summary_html += """
<hr>

<h3>🍨 Category Source Dashboard</h3>
"""

for source, brand_data in category_source_dashboard.items():

    summary_html += f"""
    <h3>{source}</h3>
    """

    for brand, df in brand_data.items():

        summary_html += f"""
        <h4>{brand}</h4>
        """

        if df.empty:

            summary_html += """
            <p>No Data Available</p>
            """

        else:

            summary_html += apply_growth_style(df)


# =========================================================
# CATEGORY CHANNEL DASHBOARD
# =========================================================

summary_html += """
<h2>📦 Category Channel Dashboard</h2>
"""

for channel, df in (
    category_channel_dashboard.items()
):

    summary_html += f"""
    <h3>{channel}</h3>
    """

    if df.empty:

        summary_html += """
        <p>No Data Available</p>
        """

    else:

        summary_html += (
            apply_growth_style(
                df
            )
        )


# =========================================================
# Item Dashboard
# =========================================================

summary_html += """
<hr>
<h3>🏆 Item Dashboard </h3>
"""

for brand, df in item_dashboard.items():

    summary_html += f"""
    <h4>{brand}</h4>
    """

    if df.empty:

        summary_html += """
        <p>No Data Available</p>
        """

    else:

        summary_html += (
            apply_growth_style(
                df.head(10)
            )
        )

# =========================================================
# ITEM SOURCE DASHBOARD
# =========================================================

summary_html += """
<hr>

<h3>🍨 Item Source Dashboard</h3>
"""

for source, brand_data in item_source_dashboard.items():

    summary_html += f"""
    <h3>{source}</h3>
    """

    for brand, df in brand_data.items():

        summary_html += f"""
        <h4>{brand}</h4>
        """

        if df.empty:

            summary_html += """
            <p>No Data Available</p>
            """

        else:

            summary_html += apply_growth_style(df)


# =========================================================
# REGION CATEGORY DASHBOARD
# =========================================================

summary_html += """
<hr>

<h3>📍 Region + Category Dashboard</h3>
"""

for region, brand_data in region_category_dashboard.items():

    summary_html += f"""
    <h2>{region}</h2>
    """

    for brand, df in brand_data.items():

        summary_html += f"""
        <h3>{brand}</h3>
        """

        if df.empty:

            summary_html += """
            <p>No Data Available</p>
            """

        else:

            summary_html += apply_growth_style(df)

    summary_html += "<br><br>"

# =========================================================
# REGION PRODUCT MIX SOURCE DASHBOARD
# =========================================================

summary_html += """
<hr>

<h3>📍 Region + Product Mix Source Dashboard</h3>
"""

# =========================================================
# CREATE REGION + PRODUCT MIX SOURCE DASHBOARD
# =========================================================

region_product_mix_source_dashboard = (
    create_region_product_mix_source_dashboard(
        current_sales,
        lw_sales,
        l2w_sales
    )
)

# =========================================================
# REGION + PRODUCT MIX SOURCE DASHBOARD OUTPUT
# =========================================================

for region, source_data in (
    region_product_mix_source_dashboard.items()
):

    summary_html += f"""
    <h2>{region}</h2>
    """

    for source, brand_data in source_data.items():

        summary_html += f"""
        <h3>{source}</h3>
        """

        for brand, df in brand_data.items():

            summary_html += f"""
            <h4>{brand}</h4>
            """

            if df.empty:

                summary_html += """
                <p>No Data Available</p>
                """

            else:

                summary_html += (
                    apply_growth_style(df)
                )

        summary_html += "<br>"

    summary_html += "<br><br>"

# =========================================================
# SWIGGY DISCOUNT DASHBOARD
# =========================================================

summary_html += create_discount_html(
    swiggy_discount_dashboard,
    "🟠 Swiggy Discount Dashboard"
)


# =========================================================
# ZOMATO DISCOUNT DASHBOARD
# =========================================================

summary_html += create_discount_html(
    zomato_discount_dashboard,
    "🔴 Zomato Discount Dashboard"
)


# =========================================================
# HTML CLOSE
# =========================================================

summary_html += """
</body>
</html>
"""

print("✅ Summary HTML Created")


# =========================================================
# HOURLY PRODUCT DASHBOARD MAIL
# =========================================================

import smtplib
import os

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

print("📧 Preparing Hourly Mail...")

EMAIL_USER = os.environ["EMAIL_USER"]
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]

to_mails = [
    "mis2@frozenbottle.in"
]

cc_mails = [
    "mis2@frozenbottle.in"
]

all_recipients = (
    to_mails +
    cc_mails
)

# =========================================================
# SAME SUBJECT FOR SAME DAY
# =========================================================

mail_subject = (
    f"Hourly Product Level Sales Dashboard - "
    f"{business_date}"
)

msg = MIMEMultipart()

msg["From"] = EMAIL_USER

msg["To"] = ", ".join(
    to_mails
)

msg["CC"] = ", ".join(
    cc_mails
)

msg["Subject"] = (
    mail_subject
)

# =========================================================
# FIXED THREAD ID
# SAME FOR ENTIRE DAY
# =========================================================

message_id = (
    f"<product-dashboard-{business_date}"
    f"@frozenbottle.in>"
)

print(
    "Thread Message ID:",
    message_id
)

msg["Message-ID"] = (
    message_id
)

msg["In-Reply-To"] = (
    message_id
)

msg["References"] = (
    message_id
)

# =========================================================
# HTML BODY
# =========================================================

msg.attach(
    MIMEText(
        summary_html,
        "html"
    )
)

# =========================================================
# SEND MAIL
# =========================================================

try:

    print("📧 Email User:", EMAIL_USER)
    print("📧 Recipients:", all_recipients)
    print("📧 Subject:", mail_subject)
    print("📧 HTML Length:", len(summary_html))

    server = smtplib.SMTP(
        "smtp.gmail.com",
        587
    )

    server.starttls()

    server.login(
        EMAIL_USER,
        EMAIL_PASSWORD
    )

    server.sendmail(
        EMAIL_USER,
        all_recipients,
        msg.as_string()
    )

    server.quit()

    print(
        "✅ Hourly Mail Sent Successfully"
    )

except Exception as e:

    print(
        "❌ Hourly Mail Error:",
        str(e)
    )
