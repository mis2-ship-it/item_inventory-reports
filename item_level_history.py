# =========================================================
# ITEM LEVEL HISTORICAL DATA
# =========================================================
#
# PURPOSE:
#   Build and maintain historical item-level sales data
#   for the Item Level Web Dashboard.
#
# IMPORTANT:
#   This is a SEPARATE script.
#   Existing item_level.py is NOT modified.
#
# OUTPUT GOOGLE SHEET:
#   Item Level Historical Data
#
# DATA:
#   Business Date
#   Invoice
#   Store
#   Region
#   Brand
#   Channel
#   Channel Group
#   Source
#   Product Mix
#   Category
#   Item
#   Orders
#   Qty
#   Gross
#   Discount
#   Net Revenue
#
# =========================================================


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
import gspread

from datetime import (
    datetime,
    timedelta
)

from zoneinfo import ZoneInfo

from google.oauth2.service_account import Credentials


print("=" * 70)
print("🚀 ITEM LEVEL HISTORICAL DATA SCRIPT STARTED")
print("=" * 70)


# =========================================================
# CONFIGURATION
# =========================================================

SPREADSHEET_ID = (
    "1ldgnNMdeubDx_ImtCC1uCD7FGz8gt_edmWEkmO_xRNk"
)

HISTORY_SHEET_NAME = (
    "Item Level Historical Data"
)

HELP_SHEET_NAME = (
    "Help Sheet"
)

ITEM_GROUP_SHEET_NAME = (
    "Item Group"
)

IST = ZoneInfo(
    "Asia/Kolkata"
)


# =========================================================
# HISTORY CONFIGURATION
# =========================================================
#
# IMPORTANT:
#
# First run:
#   180 = approximately 6 months
#
# After the first successful load:
#   Change this to 7 or keep 180.
#
# The script automatically removes and replaces
# the dates it is fetching, so reruns are safe.
#
# =========================================================

HISTORY_DAYS = 180


# =========================================================
# RISTA AUTH
# =========================================================

API_KEY = os.environ[
    "API_KEY"
]

SECRET_KEY = os.environ[
    "SECRET_KEY"
]


def get_token():

    payload = {

        "iss": API_KEY,

        "iat": int(
            time.time()
        )

    }

    return jwt.encode(
        payload,
        SECRET_KEY,
        algorithm="HS256"
    )


def get_headers():

    return {

        "x-api-key": API_KEY,

        "x-api-token":
            get_token(),

        "content-type":
            "application/json"

    }


# =========================================================
# GOOGLE AUTH
# =========================================================

creds = (
    Credentials
    .from_service_account_info(
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
)


client = gspread.authorize(
    creds
)


spreadsheet = (
    client
    .open_by_key(
        SPREADSHEET_ID
    )
)


print(
    "✅ Connected Google Sheet"
)


# =========================================================
# GET / CREATE SHEET
# =========================================================

def get_or_create_sheet(
    sheet_name
):

    try:

        return spreadsheet.worksheet(
            sheet_name
        )

    except gspread.WorksheetNotFound:

        print(
            f"🆕 Creating sheet: {sheet_name}"
        )

        return spreadsheet.add_worksheet(
            title=sheet_name,
            rows=1000,
            cols=30
        )


# =========================================================
# HELP SHEET
# =========================================================

print("\n" + "=" * 70)
print("📋 LOADING HELP SHEET")
print("=" * 70)


help_ws = spreadsheet.worksheet(
    HELP_SHEET_NAME
)


help_data = (
    help_ws
    .get_all_values()
)


if not help_data:

    print(
        "❌ Help Sheet is empty"
    )

    raise SystemExit


help_rows = []


for row in help_data[1:]:

    row = list(row)

    if len(row) < 7:

        row.extend(
            [""] *
            (
                7 - len(row)
            )
        )

    help_rows.append(
        [
            row[0],   # branchCode
            row[1],   # Store Name
            row[2],   # Ownership
            row[3],   # Region
            row[5],   # Channel
            row[6],   # Source
        ]
    )


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


# =========================================================
# CLEAN HELP SHEET
# =========================================================

help_df["branchCode"] = (
    help_df["branchCode"]
    .astype(str)
    .str.strip()
)


help_df["Ownership"] = (
    help_df["Ownership"]
    .astype(str)
    .str.upper()
    .str.strip()
)


help_df["Store Name"] = (
    help_df["Store Name"]
    .astype(str)
    .str.strip()
)


help_df["Region"] = (
    help_df["Region"]
    .astype(str)
    .str.strip()
)


help_df["Channel"] = (
    help_df["Channel"]
    .astype(str)
    .str.strip()
)


help_df["Source"] = (
    help_df["Source"]
    .astype(str)
    .str.strip()
)


# =========================================================
# COCO ONLY
# =========================================================

help_df = help_df[
    help_df["Ownership"]
    == "COCO"
].copy()


print(
    "✅ COCO Stores:",
    len(help_df)
)


# =========================================================
# REQUIRED HELP COLUMNS
# =========================================================

required_help_cols = [

    "branchCode",

    "Store Name",

    "Ownership",

    "Region",

    "Channel",

    "Source"

]


missing_help_cols = [

    c

    for c in required_help_cols

    if c not in help_df.columns

]


if missing_help_cols:

    print(
        "❌ Missing Help Sheet Columns:",
        missing_help_cols
    )

    raise SystemExit


# =========================================================
# RENAME HELP COLUMNS
# =========================================================

help_df = help_df.rename(
    columns={

        "Channel":
            "Help Channel",

        "Source":
            "Help Source"

    }
)


# =========================================================
# BRANCH LIST
# =========================================================

branches = (
    help_df[
        "branchCode"
    ]
    .dropna()
    .astype(str)
    .str.strip()
    .unique()
    .tolist()
)


print(
    "🏪 COCO Branch Count:",
    len(branches)
)


# =========================================================
# ITEM GROUP
# =========================================================

print("\n" + "=" * 70)
print("🍨 LOADING ITEM GROUP")
print("=" * 70)


item_ws = spreadsheet.worksheet(
    ITEM_GROUP_SHEET_NAME
)


item_data = (
    item_ws
    .get_all_values()
)


if not item_data:

    print(
        "❌ Item Group is empty"
    )

    raise SystemExit


item_headers = [

    "Item Name",

    "Item Group Name",

    "Variant",

    "Product Mix",

    "Category Group"

]


item_rows = []


for row in item_data[1:]:

    row = list(row[:5])

    if len(row) < 5:

        row.extend(
            [""] *
            (
                5 - len(row)
            )
        )

    item_rows.append(
        row
    )


item_df = pd.DataFrame(
    item_rows,
    columns=item_headers
)


# =========================================================
# CLEAN ITEM GROUP
# =========================================================

item_df["Item Name"] = (
    item_df["Item Name"]
    .astype(str)
    .str.strip()
    .str.upper()
)


item_df["Item Group Name"] = (
    item_df["Item Group Name"]
    .astype(str)
    .str.strip()
)


item_df["Product Mix"] = (
    item_df["Product Mix"]
    .astype(str)
    .str.strip()
)


item_df["Category Group"] = (
    item_df["Category Group"]
    .astype(str)
    .str.strip()
)


print(
    "✅ Item Group Rows:",
    len(item_df)
)


# =========================================================
# RISTA SALES API
# =========================================================

SALES_URL = (
    "https://api.ristaapps.com/v1/sales/page"
)


# =========================================================
# FETCH ONE BUSINESS DATE
# =========================================================

def fetch_business_date(
    business_date
):

    print("\n" + "-" * 70)

    print(
        "📅 Fetching Business Date:",
        business_date
    )

    print("-" * 70)


    all_sales = []


    # -----------------------------------------------------
    # BUSINESS WINDOW
    #
    # Existing item_level.py uses 9 AM business start.
    #
    # For historical full-day data:
    #
    # Start:
    #   09:00 AM business date
    #
    # End:
    #   08:59:59 AM next calendar date
    #
    # -----------------------------------------------------

    window_start = datetime.combine(
        business_date,
        datetime.min.time()
    ).replace(
        hour=9,
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=IST
    )


    window_end = (
        window_start
        + timedelta(
            days=1
        )
        - timedelta(
            seconds=1
        )
    )


    print(
        "Window:",
        window_start,
        "to",
        window_end
    )


    # -----------------------------------------------------
    # LOOP COCO BRANCHES
    # -----------------------------------------------------

    for branch in branches:

        print(
            "Fetching:",
            branch
        )


        params = {

            "branch":
                branch,

            "day":
                business_date.strftime(
                    "%Y-%m-%d"
                ),

            "page":
                1,

            "limit":
                5000

        }


        try:

            response = requests.get(

                SALES_URL,

                headers=get_headers(),

                params=params,

                timeout=180

            )


            print(
                "Status:",
                response.status_code
            )


            if response.status_code != 200:

                print(
                    "⚠️ API Error:",
                    branch,
                    response.text[:300]
                )

                continue


            response_json = (
                response.json()
            )


            data = (
                response_json
                .get(
                    "data",
                    []
                )
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


            # -------------------------------------------------
            # SAFE BRANCH CODE
            # -------------------------------------------------

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


            # -------------------------------------------------
            # ORDER TIME
            # -------------------------------------------------

            if (
                "createdDate"
                not in df.columns
            ):

                print(
                    "⚠️ createdDate missing:",
                    branch
                )

                continue


            df[
                "Order Time"
            ] = pd.to_datetime(

                df[
                    "createdDate"
                ],

                utc=True,

                errors="coerce"

            ).dt.tz_convert(
                "Asia/Kolkata"
            )


            # -------------------------------------------------
            # BUSINESS WINDOW FILTER
            # -------------------------------------------------

            df = df[
                (
                    df["Order Time"]
                    >= window_start
                )
                &
                (
                    df["Order Time"]
                    <= window_end
                )
            ].copy()


            if df.empty:

                print(
                    "No rows inside business window:",
                    branch
                )

                continue


            # -------------------------------------------------
            # BUSINESS DATE
            # -------------------------------------------------

            df[
                "Business Date"
            ] = (
                business_date
                .strftime(
                    "%Y-%m-%d"
                )
            )


            all_sales.append(
                df
            )


        except Exception as e:

            print(
                f"❌ Error {branch}:",
                str(e)
            )


    # =====================================================
    # COMBINE
    # =====================================================

    if not all_sales:

        print(
            "❌ No data for:",
            business_date
        )

        return pd.DataFrame()


    final_df = pd.concat(
        all_sales,
        ignore_index=True
    )


    print(
        "✅ Invoice Rows:",
        len(final_df)
    )


    return final_df


# =========================================================
# FLATTEN ITEM DATA
# =========================================================

def flatten_items(
    df
):

    if df.empty:

        return pd.DataFrame()


    if "items" not in df.columns:

        print(
            "❌ items column missing"
        )

        return pd.DataFrame()


    print(
        "📦 Flattening items..."
    )


    # -----------------------------------------------------
    # EXPLODE
    # -----------------------------------------------------

    df = df.explode(
        "items"
    )


    df = df[
        df["items"].notna()
    ].copy()


    if df.empty:

        return pd.DataFrame()


    # -----------------------------------------------------
    # NORMALIZE ITEM JSON
    # -----------------------------------------------------

    item_part = pd.json_normalize(
        df["items"]
    )


    item_part.columns = [

        f"item_{col}"

        for col in
        item_part.columns

    ]


    # -----------------------------------------------------
    # RESET INDEX
    # -----------------------------------------------------

    df = (
        df
        .drop(
            columns=[
                "items"
            ]
        )
        .reset_index(
            drop=True
        )
    )


    item_part = (
        item_part
        .reset_index(
            drop=True
        )
    )


    # -----------------------------------------------------
    # COMBINE
    # -----------------------------------------------------

    result = pd.concat(
        [
            df,
            item_part
        ],
        axis=1
    )


    print(
        "✅ Item Rows:",
        len(result)
    )


    return result


# =========================================================
# CHANNEL GROUP
# =========================================================

def channel_group(
    value
):

    x = (
        str(value)
        .strip()
        .upper()
    )


    # -----------------------------------------------------
    # SWIGGY
    # -----------------------------------------------------

    if "SWIGGY" in x:

        return "Swiggy"


    # -----------------------------------------------------
    # ZOMATO
    # -----------------------------------------------------

    if "ZOMATO" in x:

        return "Zomato"


    # -----------------------------------------------------
    # IN STORE
    # -----------------------------------------------------

    if (
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

    if (
        "OWNLY" in x
        or "WEBSITE" in x
    ):

        return "Ownly"


    # -----------------------------------------------------
    # OTHERS
    # -----------------------------------------------------

    return "Others"


# =========================================================
# SAFE COLUMNS
# =========================================================

def ensure_columns(
    df
):

    defaults = {

        "branchCode":
            "",

        "branchName":
            "",

        "brandName":
            "",

        "invoiceNumber":
            "",

        "channel":
            "",

        "status":
            "",

        "item_shortName":
            "",

        "item_quantity":
            0,

        "item_baseGrossAmount":
            0,

        "item_baseNetDiscountAmount":
            0,

        "item_baseNetAmount":
            0

    }


    for col, default in defaults.items():

        if col not in df.columns:

            df[col] = default


    return df


# =========================================================
# PROCESS ONE BUSINESS DATE
# =========================================================

def process_business_date(
    business_date
):

    raw_df = fetch_business_date(
        business_date
    )


    if raw_df.empty:

        return pd.DataFrame()


    # -----------------------------------------------------
    # FLATTEN ITEMS
    # -----------------------------------------------------

    sales_df = flatten_items(
        raw_df
    )


    if sales_df.empty:

        return pd.DataFrame()


    # -----------------------------------------------------
    # SAFE COLUMNS
    # -----------------------------------------------------

    sales_df = ensure_columns(
        sales_df
    )


    # -----------------------------------------------------
    # CLEAN BRANCH
    # -----------------------------------------------------

    sales_df[
        "branchCode"
    ] = (
        sales_df[
            "branchCode"
        ]
        .astype(str)
        .str.strip()
    )


    # -----------------------------------------------------
    # MERGE HELP SHEET
    # -----------------------------------------------------

    help_merge = help_df[
        [
            "branchCode",
            "Store Name",
            "Region",
            "Help Channel",
            "Help Source"
        ]
    ].copy()


    sales_df = sales_df.merge(

        help_merge,

        on="branchCode",

        how="left"

    )


    print(
        "✅ Help Sheet Merged"
    )


    # -----------------------------------------------------
    # NUMERIC
    # -----------------------------------------------------

    numeric_cols = [

        "item_quantity",

        "item_baseGrossAmount",

        "item_baseNetDiscountAmount",

        "item_baseNetAmount"

    ]


    for col in numeric_cols:

        sales_df[col] = pd.to_numeric(

            sales_df[col],

            errors="coerce"

        ).fillna(0)


    # -----------------------------------------------------
    # DISCOUNT POSITIVE
    # -----------------------------------------------------

    sales_df[
        "item_baseNetDiscountAmount"
    ] = (
        sales_df[
            "item_baseNetDiscountAmount"
        ]
        .abs()
    )


    # -----------------------------------------------------
    # ITEM NAME
    # -----------------------------------------------------

    sales_df[
        "item_shortName"
    ] = (
        sales_df[
            "item_shortName"
        ]
        .astype(str)
        .str.strip()
        .str.upper()
    )


    # -----------------------------------------------------
    # ITEM GROUP MERGE
    # -----------------------------------------------------

    item_merge = item_df[
        [
            "Item Name",
            "Item Group Name",
            "Product Mix",
            "Category Group"
        ]
    ].copy()


    sales_df = sales_df.merge(

        item_merge,

        left_on="item_shortName",

        right_on="Item Name",

        how="left"

    )


    print(
        "✅ Item Group Merged"
    )


    # -----------------------------------------------------
    # SAFE MAPPING FIELDS
    # -----------------------------------------------------

    for col in [

        "Item Group Name",

        "Product Mix",

        "Category Group"

    ]:

        sales_df[col] = (

            sales_df[col]

            .fillna("Others")

            .astype(str)

            .str.strip()

        )


    # -----------------------------------------------------
    # REMOVE ADDONS
    # -----------------------------------------------------

    before_addons = len(
        sales_df
    )


    sales_df = sales_df[
        sales_df[
            "Product Mix"
        ]
        .astype(str)
        .str.upper()
        != "ADDONS"
    ].copy()


    print(
        "✅ AddOns Removed:",
        before_addons
        - len(sales_df)
    )


    # -----------------------------------------------------
    # CHANNEL GROUP
    # -----------------------------------------------------

    sales_df[
        "Channel Group"
    ] = (
        sales_df[
            "channel"
        ]
        .apply(
            channel_group
        )
    )


    print(
        "✅ Channel Group Created"
    )


    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    sales_df[
        "status"
    ] = (
        sales_df[
            "status"
        ]
        .astype(str)
        .str.upper()
        .str.strip()
    )


    # -----------------------------------------------------
    # CLOSED ONLY
    # -----------------------------------------------------

    sales_df = sales_df[
        sales_df[
            "status"
        ]
        == "CLOSED"
    ].copy()


    print(
        "✅ Closed Item Rows:",
        len(sales_df)
    )


    # -----------------------------------------------------
    # BUSINESS DATE
    # -----------------------------------------------------

    sales_df[
        "Business Date"
    ] = business_date.strftime(
        "%Y-%m-%d"
    )


    return sales_df


# =========================================================
# CONVERT TO HISTORY FORMAT
# =========================================================

def prepare_history(
    df
):

    if df.empty:

        return pd.DataFrame()


    # -----------------------------------------------------
    # CREATE SOURCE
    # -----------------------------------------------------

    df[
        "Source"
    ] = (
        df[
            "Help Source"
        ]
        .fillna("")
        .astype(str)
        .str.strip()
    )


    # -----------------------------------------------------
    # STORE
    # -----------------------------------------------------

    df[
        "Store Name"
    ] = (
        df[
            "Store Name"
        ]
        .fillna("")
        .astype(str)
        .str.strip()
    )


    # -----------------------------------------------------
    # REGION
    # -----------------------------------------------------

    df[
        "Region"
    ] = (
        df[
            "Region"
        ]
        .fillna("")
        .astype(str)
        .str.strip()
    )


    # -----------------------------------------------------
    # BRAND
    # -----------------------------------------------------

    df[
        "Brand"
    ] = (
        df[
            "brandName"
        ]
        .fillna("")
        .astype(str)
        .str.strip()
    )


    # -----------------------------------------------------
    # ORDERS
    #
    # Item-level rows can have multiple rows per invoice.
    # Apps Script will use unique invoiceNumber for Orders.
    # -----------------------------------------------------

    df[
        "Orders"
    ] = 1


    # -----------------------------------------------------
    # FINAL COLUMNS
    # -----------------------------------------------------

    history_columns = [

        "Business Date",

        "invoiceNumber",

        "branchCode",

        "Store Name",

        "Region",

        "Brand",

        "channel",

        "Channel Group",

        "Source",

        "Item Name",

        "Item Group Name",

        "Product Mix",

        "Category Group",

        "Orders",

        "item_quantity",

        "item_baseGrossAmount",

        "item_baseNetDiscountAmount",

        "item_baseNetAmount"

    ]


    # -----------------------------------------------------
    # ENSURE ALL
    # -----------------------------------------------------

    for col in history_columns:

        if col not in df.columns:

            df[col] = ""


    history = df[
        history_columns
    ].copy()


    # -----------------------------------------------------
    # RENAME FOR WEB DASHBOARD
    # -----------------------------------------------------

    history = history.rename(

        columns={

            "channel":
                "Channel",

            "item_quantity":
                "Qty",

            "item_baseGrossAmount":
                "Gross",

            "item_baseNetDiscountAmount":
                "Discount",

            "item_baseNetAmount":
                "Net Revenue"

        }

    )


    # -----------------------------------------------------
    # FINAL ORDER
    # -----------------------------------------------------

    history_columns_final = [

        "Business Date",

        "invoiceNumber",

        "branchCode",

        "Store Name",

        "Region",

        "Brand",

        "Channel",

        "Channel Group",

        "Source",

        "Item Name",

        "Item Group Name",

        "Product Mix",

        "Category Group",

        "Orders",

        "Qty",

        "Gross",

        "Discount",

        "Net Revenue"

    ]


    history = history[
        history_columns_final
    ]


    return history


# =========================================================
# DATE RANGE
# =========================================================

ist_now = datetime.now(
    IST
)


print("\n" + "=" * 70)

print(
    "🕒 Current IST:",
    ist_now
)

print("=" * 70)


# ---------------------------------------------------------
# CURRENT BUSINESS DATE
#
# Existing item_level.py:
# before 9 AM = previous business date
# ---------------------------------------------------------

if ist_now.hour < 9:

    current_business_date = (
        ist_now.date()
        - timedelta(
            days=1
        )
    )

else:

    current_business_date = (
        ist_now.date()
    )


# ---------------------------------------------------------
# HISTORY START
# ---------------------------------------------------------

history_start = (
    current_business_date
    - timedelta(
        days=HISTORY_DAYS - 1
    )
)


history_end = (
    current_business_date
)


print(
    "📅 History Start:",
    history_start
)

print(
    "📅 History End:",
    history_end
)

print(
    "📊 Number of Days:",
    HISTORY_DAYS
)


# =========================================================
# FETCH HISTORY
# =========================================================

all_history = []


current_date = (
    history_start
)


while current_date <= history_end:

    try:

        processed = (
            process_business_date(
                current_date
            )
        )


        if not processed.empty:

            history_part = (
                prepare_history(
                    processed
                )
            )


            if not history_part.empty:

                all_history.append(
                    history_part
                )


                print(
                    "✅ Prepared:",
                    current_date,
                    "| Rows:",
                    len(history_part)
                )

        else:

            print(
                "⚠️ No usable data:",
                current_date
            )


    except Exception as e:

        print(
            "❌ Date Error:",
            current_date,
            str(e)
        )


    current_date += timedelta(
        days=1
    )


# =========================================================
# COMBINE FETCHED DATA
# =========================================================

if all_history:

    new_history_df = pd.concat(
        all_history,
        ignore_index=True
    )

else:

    new_history_df = pd.DataFrame()


print("\n" + "=" * 70)

print(
    "📦 NEW HISTORY ROWS:",
    len(new_history_df)
)

print("=" * 70)


if new_history_df.empty:

    print(
        "❌ No historical data fetched."
    )

    raise SystemExit


# =========================================================
# CLEAN HISTORY
# =========================================================

new_history_df = (
    new_history_df
    .fillna("")
)


new_history_df[
    "Business Date"
] = (
    new_history_df[
        "Business Date"
    ]
    .astype(str)
    .str[:10]
)


# =========================================================
# NUMERIC HISTORY
# =========================================================

for col in [

    "Orders",

    "Qty",

    "Gross",

    "Discount",

    "Net Revenue"

]:

    new_history_df[col] = pd.to_numeric(

        new_history_df[col],

        errors="coerce"

    ).fillna(0)


# =========================================================
# REMOVE DUPLICATE ITEM ROWS
#
# Prevent duplicate data when same date is rerun.
# =========================================================

dedupe_cols = [

    "Business Date",

    "invoiceNumber",

    "branchCode",

    "Item Name",

    "Qty",

    "Gross",

    "Discount",

    "Net Revenue"

]


new_history_df = (
    new_history_df
    .drop_duplicates(
        subset=dedupe_cols
    )
    .copy()
)


print(
    "🧹 Rows after deduplication:",
    len(new_history_df)
)


# =========================================================
# GET HISTORY SHEET
# =========================================================

history_ws = get_or_create_sheet(
    HISTORY_SHEET_NAME
)


# =========================================================
# READ EXISTING HISTORY
# =========================================================

try:

    existing_values = (
        history_ws
        .get_all_values()
    )

except Exception:

    existing_values = []


# =========================================================
# HISTORY COLUMNS
# =========================================================

history_columns = [

    "Business Date",

    "invoiceNumber",

    "branchCode",

    "Store Name",

    "Region",

    "Brand",

    "Channel",

    "Channel Group",

    "Source",

    "Item Name",

    "Item Group Name",

    "Product Mix",

    "Category Group",

    "Orders",

    "Qty",

    "Gross",

    "Discount",

    "Net Revenue"

]


# =========================================================
# EXISTING HISTORY
# =========================================================

if existing_values:

    existing_header = [
        str(x).strip()
        for x in existing_values[0]
    ]


    existing_rows = (
        existing_values[1:]
    )


    existing_df = pd.DataFrame(

        existing_rows,

        columns=existing_header

    )


    # -----------------------------------------------------
    # ADD MISSING COLUMNS
    # -----------------------------------------------------

    for col in history_columns:

        if col not in existing_df.columns:

            existing_df[col] = ""


    existing_df = existing_df[
        history_columns
    ].copy()


    # -----------------------------------------------------
    # REMOVE DATES BEING REFRESHED
    # -----------------------------------------------------

    refresh_dates = set(

        new_history_df[
            "Business Date"
        ]
        .astype(str)
        .tolist()

    )


    existing_df = existing_df[
        ~existing_df[
            "Business Date"
        ]
        .astype(str)
        .isin(
            refresh_dates
        )
    ].copy()


else:

    existing_df = pd.DataFrame(
        columns=history_columns
    )


# =========================================================
# COMBINE
# =========================================================

final_history_df = pd.concat(

    [

        existing_df,

        new_history_df

    ],

    ignore_index=True

)


# =========================================================
# CLEAN
# =========================================================

final_history_df = (
    final_history_df
    .fillna("")
)


# =========================================================
# NUMERIC
# =========================================================

for col in [

    "Orders",

    "Qty",

    "Gross",

    "Discount",

    "Net Revenue"

]:

    final_history_df[col] = (
        pd.to_numeric(
            final_history_df[col],
            errors="coerce"
        )
        .fillna(0)
    )


# =========================================================
# FINAL SORT
# =========================================================

final_history_df = (
    final_history_df
    .sort_values(
        [
            "Business Date",

            "Brand",

            "Product Mix",

            "Item Group Name"
        ],

        ascending=[
            False,

            True,

            True,

            True
        ]
    )
    .reset_index(
        drop=True
    )
)


# =========================================================
# WRITE GOOGLE SHEET
# =========================================================

print("\n" + "=" * 70)

print(
    "📤 WRITING ITEM LEVEL HISTORICAL DATA"
)

print("=" * 70)


history_ws.clear()


output_values = [

    history_columns

]


output_values.extend(

    final_history_df[
        history_columns
    ]
    .values
    .tolist()

)


history_ws.update(

    "A1",

    output_values,

    value_input_option="USER_ENTERED"

)


# =========================================================
# FORMAT HEADER
# =========================================================

try:

    history_ws.format(
        "A1:R1",
        {
            "textFormat": {
                "bold": True
            },

            "horizontalAlignment":
                "CENTER"
        }
    )

except Exception as e:

    print(
        "⚠️ Header formatting skipped:",
        str(e)
    )


# =========================================================
# FINAL VALIDATION
# =========================================================

print("\n" + "=" * 70)

print(
    "✅ ITEM LEVEL HISTORICAL DATA COMPLETED"
)

print("=" * 70)


print(
    "📊 Total Rows:",
    len(final_history_df)
)


if not final_history_df.empty:

    print(
        "📅 Minimum Date:",
        final_history_df[
            "Business Date"
        ].min()
    )

    print(
        "📅 Maximum Date:",
        final_history_df[
            "Business Date"
        ].max()
    )


    print(
        "🏪 Unique Stores:",
        final_history_df[
            "Store Name"
        ].nunique()
    )


    print(
        "🍨 Unique Items:",
        final_history_df[
            "Item Group Name"
        ].nunique()
    )


    print(
        "\n📡 Channel Group:"
    )

    print(
        final_history_df[
            "Channel Group"
        ]
        .value_counts(
            dropna=False
        )
    )


    print(
        "\n📊 Date Coverage:"
    )

    print(
        final_history_df[
            "Business Date"
        ]
        .value_counts()
        .sort_index(
            ascending=False
        )
        .head(20)
    )


print("\n" + "=" * 70)
print("🏁 SCRIPT FINISHED")
print("=" * 70)
