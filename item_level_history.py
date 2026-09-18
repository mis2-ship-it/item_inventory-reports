import os
import json
import time
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import jwt
import gspread

from google.oauth2.service_account import Credentials


# =========================================================
# CONFIG
# =========================================================

SPREADSHEET_ID = (
    "1ldgnNMdeubDx_ImtCC1uCD7FGz8gt_edmWEkmO_xRNk"
)

HELP_SHEET = "Help Sheet"
ITEM_GROUP_SHEET = "Item Group"

SALES_URL = (
    "https://api.ristaapps.com/v1/sales/page"
)

HISTORY_FOLDER = "historical_data"

IST = ZoneInfo("Asia/Kolkata")


# =========================================================
# DATE CONFIG
# =========================================================

HISTORY_START_DATE = os.getenv(
    "HISTORY_START_DATE",
    "2026-09-17"
)

HISTORY_END_DATE = os.getenv(
    "2026-09-17",
    ""
)


# =========================================================
# RISTA AUTH
# EXACTLY SAME AS item_level.py
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
        os.environ["GOOGLE_CREDENTIALS"]
    ),
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)

client = gspread.authorize(creds)

spreadsheet = client.open_by_key(
    SPREADSHEET_ID
)

print("✅ Connected Google Sheet")


# =========================================================
# TIME
# =========================================================

ist_now = datetime.now(IST)

print(
    "🕒 Current IST Time:",
    ist_now
)


# =========================================================
# BUSINESS DATE
# 9 AM → NEXT 8:59 AM
# =========================================================

if ist_now.hour < 9:

    current_business_date = (
        ist_now.date()
        - timedelta(days=1)
    )

else:

    current_business_date = (
        ist_now.date()
    )


print(
    "📅 Current Business Date:",
    current_business_date
)


# =========================================================
# HISTORY END DATE
# =========================================================

if HISTORY_END_DATE:

    history_end_date = datetime.strptime(
        HISTORY_END_DATE,
        "%Y-%m-%d"
    ).date()

else:

    history_end_date = current_business_date


history_start_date = datetime.strptime(
    HISTORY_START_DATE,
    "%Y-%m-%d"
).date()


print(
    "📅 History Start:",
    history_start_date
)

print(
    "📅 History End:",
    history_end_date
)


if history_start_date > history_end_date:

    raise RuntimeError(
        "HISTORY_START_DATE cannot be after HISTORY_END_DATE."
    )


# =========================================================
# CREATE HISTORY FOLDER
# =========================================================

os.makedirs(
    HISTORY_FOLDER,
    exist_ok=True
)


# =========================================================
# HELP SHEET
# =========================================================

print("\n" + "=" * 60)
print("LOADING HELP SHEET")
print("=" * 60)


help_ws = spreadsheet.worksheet(
    HELP_SHEET
)

help_data = help_ws.get_all_values()


if not help_data:

    raise RuntimeError(
        "Help Sheet is empty."
    )


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


# =========================================================
# CLEAN HELP SHEET
# =========================================================

for col in help_df.columns:

    help_df[col] = (
        help_df[col]
        .astype(str)
        .str.strip()
    )


help_df["Ownership"] = (
    help_df["Ownership"]
    .str.upper()
)


help_df["branchCode"] = (
    help_df["branchCode"]
    .str.strip()
)


# =========================================================
# COCO ONLY
# =========================================================

help_df = help_df[
    help_df["Ownership"] == "COCO"
].copy()


print(
    "COCO branches:",
    len(help_df)
)


# =========================================================
# REQUIRED COLUMNS
# =========================================================

required_help_columns = [
    "branchCode",
    "Store Name",
    "Ownership",
    "Region",
    "Channel",
    "Source"
]


missing_help_columns = [
    col
    for col in required_help_columns
    if col not in help_df.columns
]


if missing_help_columns:

    raise RuntimeError(
        "Missing Help Sheet columns: "
        + str(missing_help_columns)
    )


# =========================================================
# BRANCH MASTER
# =========================================================

branch_master = (
    help_df
    .drop_duplicates(
        subset=["branchCode"]
    )
    .set_index("branchCode")
)


branches = (
    help_df["branchCode"]
    .dropna()
    .loc[
        lambda x: x.astype(str).str.strip() != ""
    ]
    .unique()
    .tolist()
)


print(
    "🏪 COCO Branch Count:",
    len(branches)
)


# =========================================================
# ITEM GROUP SHEET
# =========================================================

print("\n" + "=" * 60)
print("LOADING ITEM GROUP")
print("=" * 60)


item_ws = spreadsheet.worksheet(
    ITEM_GROUP_SHEET
)

item_data = item_ws.get_all_values()


if not item_data:

    raise RuntimeError(
        "Item Group sheet is empty."
    )


# =========================================================
# ONLY A:E
# THIS ALSO AVOIDS BLANK HEADER PROBLEM
# =========================================================

item_headers = [
    "Item Name",
    "Item Group Name",
    "Variant",
    "Product Mix",
    "Category Group"
]


normalized_rows = []


for row in item_data[1:]:

    row = list(row)

    row = row[:5]

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
# CLEAN ITEM GROUP
# =========================================================

for col in item_df.columns:

    item_df[col] = (
        item_df[col]
        .astype(str)
        .str.strip()
    )


item_df["Item Name"] = (
    item_df["Item Name"]
    .str.upper()
)


# Remove completely blank item names

item_df = item_df[
    item_df["Item Name"].str.strip() != ""
].copy()


# Remove duplicate item mappings

item_df = (
    item_df
    .drop_duplicates(
        subset=["Item Name"],
        keep="first"
    )
)


print(
    "Item Group rows:",
    len(item_data) - 1
)

print(
    "Usable Item Group rows:",
    len(item_df)
)


# =========================================================
# ITEM GROUP LOOKUP
# =========================================================

item_lookup = (
    item_df
    .set_index("Item Name")
    .to_dict("index")
)


# =========================================================
# CHANNEL GROUP
# =========================================================

def channel_group(value):

    x = str(value).strip().upper()

    if "SWIGGY" in x:

        return "Swiggy"

    if "ZOMATO" in x:

        return "Zomato"

    if (
        "POS" in x
        or "DINE" in x
        or "IN-STORE" in x
        or "IN STORE" in x
        or "INSTORE" in x
    ):

        return "In Store"

    if (
        "OWNLY" in x
        or "WEBSITE" in x
    ):

        return "Ownly"

    return "Others"


# =========================================================
# BUSINESS WINDOW
# 09:00 AM → NEXT DAY 08:59:59 AM
# =========================================================

def get_business_window(
    business_date
):

    start_dt = datetime(
        business_date.year,
        business_date.month,
        business_date.day,
        9,
        0,
        0,
        tzinfo=IST
    )

    end_dt = (
        start_dt
        + timedelta(days=1)
        - timedelta(seconds=1)
    )

    return start_dt, end_dt


# =========================================================
# RISTA DATE FORMAT
# =========================================================

def rista_day(
    business_date
):

    return business_date.strftime(
        "%Y-%m-%d"
    )


# =========================================================
# FETCH ONE BUSINESS DATE
# =========================================================

def fetch_business_date(
    business_date
):

    start_dt, end_dt = get_business_window(
        business_date
    )

    day = rista_day(
        business_date
    )

    print("\n" + "-" * 60)

    print(
        "📅 Business Date:",
        business_date
    )

    print(
        "🕘 Window:",
        start_dt,
        "→",
        end_dt
    )

    print("-" * 60)


    all_sales = []


    # =====================================================
    # LOOP COCO BRANCHES
    # =====================================================

    for branch in branches:

        print(
            f"\n🏪 Branch: {branch}"
        )


        page = 1

        branch_rows = []


        while True:

            params = {

                "branch": branch,

                "day": day,

                "page": page,

                "limit": 5000

            }


            print(
                f"   Page {page}"
            )


            try:

                response = requests.get(

                    SALES_URL,

                    headers=headers(),

                    params=params,

                    timeout=180

                )


                print(
                    "   Status:",
                    response.status_code
                )


                if response.status_code != 200:

                    print(
                        "   ❌ API Error:",
                        response.text[:500]
                    )

                    raise RuntimeError(
                        f"Rista request failed for "
                        f"{branch} "
                        f"date={day} "
                        f"page={page} "
                        f"status={response.status_code}"
                    )


                response_json = (
                    response.json()
                )


                data = (
                    response_json
                    .get("data", [])
                )


                if not data:

                    print(
                        "   No more data."
                    )

                    break


                branch_rows.extend(
                    data
                )


                print(
                    "   Records:",
                    len(data)
                )


                # =================================================
                # PAGINATION
                # =================================================

                # Rista can return less than the limit
                # when there are no more records.

                if len(data) < 5000:

                    break


                page += 1


            except Exception as e:

                print(
                    f"   ❌ ERROR {branch}:",
                    str(e)
                )

                raise


        if branch_rows:

            print(
                f"   ✅ Branch records:",
                len(branch_rows)
            )

            all_sales.extend(
                branch_rows
            )

        else:

            print(
                "   ⚠️ No sales"
            )


    # =====================================================
    # NO DATA
    # =====================================================

    if not all_sales:

        print(
            f"⚠️ No sales for {business_date}"
        )

        return pd.DataFrame()


    # =====================================================
    # NORMALIZE SALES
    # =====================================================

    sales_df = pd.json_normalize(
        all_sales
    )


    print(
        "📦 Raw Sales Rows:",
        len(sales_df)
    )


    # =====================================================
    # BRANCH CODE
    # =====================================================

    if "branchCode" not in sales_df.columns:

        if "branch" in sales_df.columns:

            sales_df["branchCode"] = (
                sales_df["branch"]
            )

        else:

            raise RuntimeError(
                "branchCode not found in Rista sales data."
            )


    sales_df["branchCode"] = (
        sales_df["branchCode"]
        .astype(str)
        .str.strip()
    )


    # =====================================================
    # STATUS
    # =====================================================

    if "status" in sales_df.columns:

        sales_df["status"] = (
            sales_df["status"]
            .astype(str)
            .str.upper()
            .str.strip()
        )

        sales_df = sales_df[
            sales_df["status"] == "CLOSED"
        ].copy()


    print(
        "✅ CLOSED Sales Rows:",
        len(sales_df)
    )


    if sales_df.empty:

        return pd.DataFrame()


    # =====================================================
    # STORE MAPPING
    # =====================================================

    sales_df["Store Name"] = (
        sales_df["branchCode"]
        .map(
            branch_master["Store Name"]
        )
    )


    sales_df["Ownership"] = (
        sales_df["branchCode"]
        .map(
            branch_master["Ownership"]
        )
    )


    sales_df["Region"] = (
        sales_df["branchCode"]
        .map(
            branch_master["Region"]
        )
    )


    sales_df["Help Channel"] = (
        sales_df["branchCode"]
        .map(
            branch_master["Channel"]
        )
    )


    sales_df["Source"] = (
        sales_df["branchCode"]
        .map(
            branch_master["Source"]
        )
    )


    sales_df["Source"] = (
        sales_df["Source"]
        .fillna(
            sales_df["Help Channel"]
        )
    )


    sales_df["Source"] = (
        sales_df["Source"]
        .apply(channel_group)
    )


    # =====================================================
    # FLATTEN ITEMS
    # =====================================================

    item_rows = []


    for _, sale in sales_df.iterrows():

        items = sale.get(
            "items",
            []
        )


        if not isinstance(
            items,
            list
        ):

            continue


        for item in items:

            if not isinstance(
                item,
                dict
            ):

                continue


            row = {

                "Business Date":
                    business_date.strftime(
                        "%Y-%m-%d"
                    ),

                "branchCode":
                    sale.get(
                        "branchCode",
                        ""
                    ),

                "Store Name":
                    sale.get(
                        "Store Name",
                        ""
                    ),

                "Ownership":
                    sale.get(
                        "Ownership",
                        ""
                    ),

                "Region":
                    sale.get(
                        "Region",
                        ""
                    ),

                "Source":
                    sale.get(
                        "Source",
                        ""
                    ),

                "invoiceNumber":
                    sale.get(
                        "invoiceNumber",
                        ""
                    ),

                "createdDate":
                    sale.get(
                        "createdDate",
                        ""
                    ),

                "brandName":
                    sale.get(
                        "brandName",
                        ""
                    ),

                "channel":
                    sale.get(
                        "channel",
                        ""
                    ),

                "status":
                    sale.get(
                        "status",
                        ""
                    ),

                "Item Name":
                    item.get(
                        "item_shortName",
                        ""
                    ),

                "Qty":
                    item.get(
                        "item_quantity",
                        0
                    ),

                "Gross Amount":
                    item.get(
                        "item_baseGrossAmount",
                        0
                    ),

                "Discount":
                    item.get(
                        "item_baseNetDiscountAmount",
                        0
                    ),

                "Net Amount":
                    item.get(
                        "item_baseNetAmount",
                        0
                    )

            }


            item_rows.append(
                row
            )


    # =====================================================
    # NO ITEM DATA
    # =====================================================

    if not item_rows:

        print(
            "⚠️ No item-level records."
        )

        return pd.DataFrame()


    item_sales_df = pd.DataFrame(
        item_rows
    )


    # =====================================================
    # CLEAN ITEM NAME
    # =====================================================

    item_sales_df["Item Name"] = (
        item_sales_df["Item Name"]
        .astype(str)
        .str.strip()
        .str.upper()
    )


    # =====================================================
    # ITEM GROUP MAPPING
    # =====================================================

    item_sales_df["Item Group Name"] = (
        item_sales_df["Item Name"]
        .map(
            lambda x:
            item_lookup.get(
                x,
                {}
            ).get(
                "Item Group Name",
                ""
            )
        )
    )


    item_sales_df["Variant"] = (
        item_sales_df["Item Name"]
        .map(
            lambda x:
            item_lookup.get(
                x,
                {}
            ).get(
                "Variant",
                ""
            )
        )
    )


    item_sales_df["Product Mix"] = (
        item_sales_df["Item Name"]
        .map(
            lambda x:
            item_lookup.get(
                x,
                {}
            ).get(
                "Product Mix",
                ""
            )
        )
    )


    item_sales_df["Category Group"] = (
        item_sales_df["Item Name"]
        .map(
            lambda x:
            item_lookup.get(
                x,
                {}
            ).get(
                "Category Group",
                ""
            )
        )
    )


    # =====================================================
    # NUMERIC COLUMNS
    # =====================================================

    numeric_columns = [

        "Qty",

        "Gross Amount",

        "Discount",

        "Net Amount"

    ]


    for col in numeric_columns:

        item_sales_df[col] = pd.to_numeric(

            item_sales_df[col],

            errors="coerce"

        ).fillna(0)


    # =====================================================
    # FINAL COLUMN ORDER
    # =====================================================

    final_columns = [

        "Business Date",

        "branchCode",

        "Store Name",

        "Ownership",

        "Region",

        "Source",

        "invoiceNumber",

        "createdDate",

        "brandName",

        "channel",

        "status",

        "Item Name",

        "Item Group Name",

        "Variant",

        "Product Mix",

        "Category Group",

        "Qty",

        "Gross Amount",

        "Discount",

        "Net Amount"

    ]


    for col in final_columns:

        if col not in item_sales_df.columns:

            item_sales_df[col] = ""


    item_sales_df = item_sales_df[
        final_columns
    ]


    print(
        "📊 Item-Level Rows:",
        len(item_sales_df)
    )


    return item_sales_df


# =========================================================
# LOAD EXISTING MONTH FILE
# =========================================================

def load_month_file(
    month
):

    file_path = os.path.join(

        HISTORY_FOLDER,

        f"item_level_{month}.csv"

    )


    if not os.path.exists(
        file_path
    ):

        return pd.DataFrame()


    print(
        f"📂 Existing file found: {file_path}"
    )


    try:

        df = pd.read_csv(
            file_path
        )

        print(
            "   Existing rows:",
            len(df)
        )

        return df


    except Exception as e:

        print(
            "⚠️ Could not read existing file:",
            e
        )

        return pd.DataFrame()


# =========================================================
# SAVE MONTH
# =========================================================

def save_month_file(
    month,
    new_df
):

    file_path = os.path.join(

        HISTORY_FOLDER,

        f"item_level_{month}.csv"

    )


    existing_df = load_month_file(
        month
    )


    if existing_df.empty:

        final_df = new_df.copy()

    elif new_df.empty:

        final_df = existing_df.copy()

    else:

        final_df = pd.concat(

            [
                existing_df,
                new_df
            ],

            ignore_index=True

        )


    # =====================================================
    # REMOVE DUPLICATES
    # =====================================================

    duplicate_columns = [

        "Business Date",

        "branchCode",

        "invoiceNumber",

        "Item Name",

        "createdDate"

    ]


    available_duplicate_columns = [

        col

        for col in duplicate_columns

        if col in final_df.columns

    ]


    if available_duplicate_columns:

        before = len(final_df)


        final_df = (
            final_df
            .drop_duplicates(
                subset=available_duplicate_columns,
                keep="last"
            )
        )


        removed = (
            before
            - len(final_df)
        )


        if removed:

            print(
                f"🧹 Removed duplicates: {removed}"
            )


    # =====================================================
    # SORT
    # =====================================================

    sort_columns = [

        "Business Date",

        "Store Name",

        "invoiceNumber",

        "Item Name"

    ]


    available_sort_columns = [

        col

        for col in sort_columns

        if col in final_df.columns

    ]


    if available_sort_columns:

        final_df = (
            final_df
            .sort_values(
                available_sort_columns
            )
            .reset_index(
                drop=True
            )
        )


    # =====================================================
    # SAVE
    # =====================================================

    final_df.to_csv(

        file_path,

        index=False,

        encoding="utf-8-sig"

    )


    print(
        f"💾 Saved: {file_path}"
    )

    print(
        f"   Rows: {len(final_df)}"
    )


    return final_df


# =========================================================
# BUILD INDEX
# =========================================================

def build_index():

    files = []


    for filename in sorted(
        os.listdir(
            HISTORY_FOLDER
        )
    ):

        if not filename.startswith(
            "item_level_"
        ):

            continue


        if not filename.endswith(
            ".csv"
        ):

            continue


        file_path = os.path.join(

            HISTORY_FOLDER,

            filename

        )


        try:

            df = pd.read_csv(
                file_path
            )


            month = filename[
                len("item_level_"):
                -len(".csv")
            ]


            files.append({

                "month": month,

                "file": filename,

                "rows": len(df)

            })


        except Exception as e:

            print(
                f"⚠️ Index skipped {filename}: {e}"
            )


    index_data = {

        "updated_at":
            datetime.now(IST).isoformat(),

        "files":
            files

    }


    index_path = os.path.join(

        HISTORY_FOLDER,

        "index.json"

    )


    with open(

        index_path,

        "w",

        encoding="utf-8"

    ) as f:

        json.dump(

            index_data,

            f,

            indent=2

        )


    print(
        f"📚 Index updated: {index_path}"
    )


# =========================================================
# MAIN
# =========================================================

def main():

    print("\n")
    print("=" * 70)
    print("ITEM LEVEL HISTORICAL DATA")
    print("=" * 70)

    print(
        "Start Date:",
        history_start_date
    )

    print(
        "End Date:",
        history_end_date
    )

    print(
        "Folder:",
        HISTORY_FOLDER
    )


    # =====================================================
    # LOOP BUSINESS DATES
    # =====================================================

    current_date = history_start_date


    while current_date <= history_end_date:

        month = current_date.strftime(
            "%Y_%m"
        )


        try:

            daily_df = fetch_business_date(
                current_date
            )


            # =================================================
            # IMPORTANT:
            # If API returns no data, do NOT destroy
            # existing historical data.
            # =================================================

            if daily_df.empty:

                print(
                    f"⚠️ No new data for {current_date}"
                )

            else:

                save_month_file(

                    month,

                    daily_df

                )


        except Exception as e:

            print("\n")
            print("=" * 70)

            print(
                f"❌ FAILED DATE: {current_date}"
            )

            print(
                "ERROR:",
                str(e)
            )

            print("=" * 70)

            raise


        current_date += timedelta(
            days=1
        )


    # =====================================================
    # INDEX
    # =====================================================

    build_index()


    print("\n")
    print("=" * 70)
    print("✅ HISTORICAL ITEM DATA COMPLETED")
    print("=" * 70)


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()

