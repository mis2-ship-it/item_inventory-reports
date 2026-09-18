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
import subprocess
from datetime import datetime, timedelta, date

import pandas as pd
import requests
import gspread
import jwt

from google.oauth2.service_account import Credentials


# ============================================================
# CONFIGURATION
# ============================================================

SPREADSHEET_ID = os.getenv(
    "SPREADSHEET_ID",
    "1ldgnNMdeubDx_ImtCC1uCD7FGz8gt_edmWEkmO_xRNk"
)

HELP_SHEET_NAME = "Help Sheet"
ITEM_GROUP_SHEET_NAME = "Item Group"

HISTORICAL_FOLDER = "historical_data"

# ------------------------------------------------------------
# Historical period
# ------------------------------------------------------------
#
# Default:
#   01-Apr-2026 -> current business date
#
# You can change these through GitHub Actions environment
# variables later.
#
# Example:
# HISTORY_START_DATE=2026-01-01
# HISTORY_END_DATE=2026-09-17
#

HISTORY_START_DATE = os.getenv(
    "HISTORY_START_DATE",
    "2026-04-01"
)

HISTORY_END_DATE = os.getenv(
    "HISTORY_END_DATE",
    ""
)

# Rista
API_BASE_URL = os.getenv(
    "RISTA_API_BASE_URL",
    "https://api.ristaapps.com"
)

API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

RISTA_ISSUER = os.getenv(
    "RISTA_ISSUER",
    "rista"
)

RISTA_API_VERSION = os.getenv(
    "RISTA_API_VERSION",
    "3"
)

PAGE_SIZE = 5000

REQUEST_TIMEOUT = 120

MAX_RETRIES = 4

SLEEP_BETWEEN_BRANCHES = 0.15


# ============================================================
# GENERAL HELPERS
# ============================================================

def normalize_header(value):
    """
    Convert headers such as:

        Branch Code
        branchcode
        branch_code
        branchCode

    into a common comparison format.
    """
    return re.sub(
        r"[^a-z0-9]",
        "",
        str(value).strip().lower()
    )


def find_column(df, possible_names):
    """
    Find a dataframe column using case/space/underscore
    insensitive matching.
    """

    normalized = {
        normalize_header(col): col
        for col in df.columns
    }

    for name in possible_names:
        key = normalize_header(name)

        if key in normalized:
            return normalized[key]

    return None


def safe_text(value):
    if pd.isna(value):
        return ""

    return str(value).strip()


def safe_number(series):
    return pd.to_numeric(
        series,
        errors="coerce"
    ).fillna(0)


# ============================================================
# CHANNEL GROUP
# ============================================================

def channel_group(value):
    """
    Convert Rista channel values into dashboard-standard
    channel groups.

    IMPORTANT:
    Handles Frozen Bottle In-Store correctly.
    """

    x = safe_text(value).upper()

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


# ============================================================
# GOOGLE SHEETS AUTHENTICATION
# ============================================================

def get_google_client():

    credentials_json = os.getenv("GOOGLE_CREDENTIALS")

    if not credentials_json:
        raise RuntimeError(
            "GOOGLE_CREDENTIALS environment variable is missing."
        )

    try:
        credentials_info = json.loads(
            credentials_json
        )
    except json.JSONDecodeError as e:
        raise RuntimeError(
            "GOOGLE_CREDENTIALS is not valid JSON."
        ) from e

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]

    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=scopes
    )

    return gspread.authorize(credentials)


# ============================================================
# LOAD HELP SHEET
# ============================================================

def load_help_sheet():

    print("=" * 70)
    print("Loading Help Sheet")
    print("=" * 70)

    client = get_google_client()

    spreadsheet = client.open_by_key(
        SPREADSHEET_ID
    )

    worksheet = spreadsheet.worksheet(
        HELP_SHEET_NAME
    )

    values = worksheet.get_all_values()

    if not values:
        raise RuntimeError(
            "Item Group sheet is empty."
        )
    
    headers = values[0]
    
    # Give blank headers unique temporary names
    fixed_headers = []
    
    for i, header in enumerate(headers):
        header = str(header).strip()
    
        if not header:
            header = f"_blank_{i + 1}"
    
        fixed_headers.append(header)
    
    records = [
        dict(zip(fixed_headers, row))
        for row in values[1:]
    ]

    df = pd.DataFrame(records)

    if df.empty:
        raise RuntimeError(
            "Help Sheet is empty."
        )

    print(
        f"Help Sheet rows: {len(df):,}"
    )

    print(
        "Help Sheet columns:",
        list(df.columns)
    )

    branch_col = find_column(
        df,
        [
            "branchcode",
            "branch code",
            "branchCode"
        ]
    )

    store_col = find_column(
        df,
        [
            "storename",
            "store name",
            "Store Name"
        ]
    )

    ownership_col = find_column(
        df,
        [
            "ownership",
            "Store Type",
            "store type"
        ]
    )

    region_col = find_column(
        df,
        [
            "region"
        ]
    )

    channel_col = find_column(
        df,
        [
            "channel"
        ]
    )

    source_col = find_column(
        df,
        [
            "source"
        ]
    )

    required = {
        "branchCode": branch_col,
        "Store Name": store_col,
        "Ownership": ownership_col,
        "Region": region_col,
        "Channel": channel_col,
        "Source": source_col,
    }

    missing = [
        name
        for name, col in required.items()
        if col is None
    ]

    if missing:
        raise RuntimeError(
            f"Help Sheet missing columns: {missing}"
        )

    result = pd.DataFrame()

    result["branchCode"] = (
        df[branch_col]
        .astype(str)
        .str.strip()
    )

    result["Store Name"] = (
        df[store_col]
        .astype(str)
        .str.strip()
    )

    result["Ownership"] = (
        df[ownership_col]
        .astype(str)
        .str.strip()
    )

    result["Region"] = (
        df[region_col]
        .astype(str)
        .str.strip()
    )

    result["Help Channel"] = (
        df[channel_col]
        .astype(str)
        .str.strip()
    )

    result["Source"] = (
        df[source_col]
        .astype(str)
        .str.strip()
    )

    # --------------------------------------------------------
    # COCO ONLY
    # --------------------------------------------------------

    result["Ownership Normalized"] = (
        result["Ownership"]
        .str.upper()
        .str.strip()
    )

    result = result[
        result["Ownership Normalized"] == "COCO"
    ].copy()

    result.drop(
        columns=["Ownership Normalized"],
        inplace=True
    )

    # Remove blank branch codes
    result = result[
        result["branchCode"].ne("")
        & result["branchCode"].ne("NAN")
    ].copy()

    # Remove duplicate branches
    result = result.drop_duplicates(
        subset=["branchCode"],
        keep="last"
    )

    print(
        f"COCO branches: {len(result):,}"
    )

    return result


# ============================================================
# LOAD ITEM GROUP
# ============================================================

def load_item_group():

    print("=" * 70)
    print("Loading Item Group")
    print("=" * 70)

    client = get_google_client()

    spreadsheet = client.open_by_key(
        SPREADSHEET_ID
    )

    worksheet = spreadsheet.worksheet(
        ITEM_GROUP_SHEET_NAME
    )

    records = worksheet.get_all_records()

    df = pd.DataFrame(records)

    if df.empty:
        raise RuntimeError(
            "Item Group sheet is empty."
        )

    print(
        f"Item Group rows: {len(df):,}"
    )

    print(
        "Item Group columns:",
        list(df.columns)
    )

    item_name_col = find_column(
        df,
        [
            "Item Name",
            "itemname",
            "item_shortName"
        ]
    )

    item_group_col = find_column(
        df,
        [
            "Item Group Name",
            "itemgroupname"
        ]
    )

    variant_col = find_column(
        df,
        [
            "Variant"
        ]
    )

    product_mix_col = find_column(
        df,
        [
            "Product Mix",
            "productmix"
        ]
    )

    category_group_col = find_column(
        df,
        [
            "Category Group",
            "categorygroup"
        ]
    )

    if item_name_col is None:
        raise RuntimeError(
            "Item Group sheet does not contain Item Name."
        )

    result = pd.DataFrame()

    result["Item Name"] = (
        df[item_name_col]
        .astype(str)
        .str.strip()
    )

    if item_group_col:
        result["Item Group Name"] = (
            df[item_group_col]
            .astype(str)
            .str.strip()
        )
    else:
        result["Item Group Name"] = ""

    if variant_col:
        result["Variant"] = (
            df[variant_col]
            .astype(str)
            .str.strip()
        )
    else:
        result["Variant"] = ""

    if product_mix_col:
        result["Product Mix"] = (
            df[product_mix_col]
            .astype(str)
            .str.strip()
        )
    else:
        result["Product Mix"] = ""

    if category_group_col:
        result["Category Group"] = (
            df[category_group_col]
            .astype(str)
            .str.strip()
        )
    else:
        result["Category Group"] = ""

    result = result[
        result["Item Name"].ne("")
        & result["Item Name"].ne("NAN")
    ].copy()

    # Avoid duplicate joins
    result = result.drop_duplicates(
        subset=["Item Name"],
        keep="last"
    )

    print(
        f"Usable Item Group rows: {len(result):,}"
    )

    return result


# ============================================================
# RISTA AUTHENTICATION
# ============================================================

def generate_token():

    if not API_KEY:
        raise RuntimeError(
            "API_KEY environment variable is missing."
        )

    if not SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY environment variable is missing."
        )

    now = int(time.time())

    payload = {
        "iss": RISTA_ISSUER,
        "iat": now,
    }

    token = jwt.encode(
        payload,
        SECRET_KEY,
        algorithm="HS256"
    )

    if isinstance(token, bytes):
        token = token.decode("utf-8")

    return token


def get_headers():

    return {
        "x-api-key": API_KEY,
        "x-api-token": generate_token(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ============================================================
# RISTA API REQUEST
# ============================================================

def request_with_retry(
    url,
    params,
    branch_code,
    page
):

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            response = requests.get(
                url,
                headers=get_headers(),
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            print(
                f"      Branch {branch_code} | "
                f"Page {page} | "
                f"Status {response.status_code}"
            )

            # Success
            if response.status_code == 200:
                return response

            # Retry temporary errors
            if response.status_code in [
                429,
                500,
                502,
                503,
                504
            ]:

                wait_seconds = 2 ** (
                    attempt - 1
                )

                print(
                    f"      Retry {attempt}/{MAX_RETRIES} "
                    f"after {wait_seconds}s"
                )

                time.sleep(
                    wait_seconds
                )

                continue

            # Permanent error
            response.raise_for_status()

        except Exception as e:

            last_error = e

            print(
                f"      Request error: {e}"
            )

            if attempt < MAX_RETRIES:

                wait_seconds = 2 ** (
                    attempt - 1
                )

                time.sleep(
                    wait_seconds
                )

    raise RuntimeError(
        f"Rista request failed for "
        f"{branch_code}, page {page}: "
        f"{last_error}"
    )


# ============================================================
# FETCH ONE BRANCH / BUSINESS DATE
# ============================================================

def fetch_branch_day(
    branch_code,
    business_date
):

    url = (
        f"{API_BASE_URL.rstrip('/')}"
        f"/v1/sales/page"
    )

    all_rows = []

    page = 1

    while True:

        params = {
            "branch": branch_code,
            "day": business_date.strftime(
                "%Y-%m-%d"
            ),
            "page": page,
            "limit": PAGE_SIZE,
        }

        response = request_with_retry(
            url=url,
            params=params,
            branch_code=branch_code,
            page=page
        )

        payload = response.json()

        if isinstance(payload, dict):

            data = payload.get(
                "data",
                []
            )

        elif isinstance(payload, list):

            data = payload

        else:

            data = []

        if not data:
            break

        if not isinstance(data, list):
            break

        all_rows.extend(data)

        # If less than page size, this is
        # the final page.
        if len(data) < PAGE_SIZE:
            break

        page += 1

        # Safety protection
        if page > 100:
            print(
                f"      WARNING: pagination exceeded "
                f"100 pages for {branch_code}"
            )
            break

    if all_rows:

        df = pd.DataFrame(
            all_rows
        )

    else:

        df = pd.DataFrame()

    return df


# ============================================================
# PROCESS BRANCH DATA
# ============================================================

def process_branch_data(
    df,
    branch_code,
    business_date,
    help_df,
    item_group_df
):

    if df.empty:
        return pd.DataFrame()

    # --------------------------------------------------------
    # Status filter
    # --------------------------------------------------------

    status_col = find_column(
        df,
        [
            "status"
        ]
    )

    if status_col:

        df = df[
            df[status_col]
            .astype(str)
            .str.upper()
            .str.strip()
            .eq("CLOSED")
        ].copy()

    if df.empty:
        return pd.DataFrame()

    # --------------------------------------------------------
    # Ensure branch code
    # --------------------------------------------------------

    branch_col = find_column(
        df,
        [
            "branchCode",
            "branchcode"
        ]
    )

    if branch_col:

        df["branchCode"] = (
            df[branch_col]
            .astype(str)
            .str.strip()
        )

    else:

        df["branchCode"] = branch_code

    # --------------------------------------------------------
    # Created date
    # --------------------------------------------------------

    created_col = find_column(
        df,
        [
            "createdDate",
            "created date"
        ]
    )

    if created_col:

        created = pd.to_datetime(
            df[created_col],
            errors="coerce",
            utc=True
        )

        created = (
            created
            .dt.tz_convert("Asia/Kolkata")
        )

        df["Created Date"] = created

        # ----------------------------------------------------
        # Business date filter
        #
        # Business day:
        # 09:00 AM -> next day 08:59:59 AM
        # ----------------------------------------------------

        start_dt = pd.Timestamp(
            business_date
        ).tz_localize(
            "Asia/Kolkata"
        ) + pd.Timedelta(
            hours=9
        )

        end_dt = (
            start_dt
            + pd.Timedelta(
                days=1
            )
            - pd.Timedelta(
                seconds=1
            )
        )

        df = df[
            (df["Created Date"] >= start_dt)
            & (df["Created Date"] <= end_dt)
        ].copy()

    if df.empty:
        return pd.DataFrame()

    # --------------------------------------------------------
    # Basic order fields
    # --------------------------------------------------------

    invoice_col = find_column(
        df,
        [
            "invoiceNumber",
            "invoice number",
            "invoiceNo"
        ]
    )

    channel_col = find_column(
        df,
        [
            "channel"
        ]
    )

    brand_col = find_column(
        df,
        [
            "brandName",
            "brand"
        ]
    )

    if invoice_col:
        df["invoiceNumber"] = (
            df[invoice_col]
            .astype(str)
            .str.strip()
        )
    else:
        df["invoiceNumber"] = ""

    if channel_col:
        df["Channel"] = (
            df[channel_col]
            .astype(str)
            .str.strip()
        )
    else:
        df["Channel"] = ""

    if brand_col:
        df["Brand"] = (
            df[brand_col]
            .astype(str)
            .str.strip()
        )
    else:
        df["Brand"] = ""

    # --------------------------------------------------------
    # Flatten item list
    # --------------------------------------------------------

    items_col = find_column(
        df,
        [
            "items"
        ]
    )

    if items_col is None:
        return pd.DataFrame()

    item_rows = []

    for _, order in df.iterrows():

        items = order.get(
            items_col,
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
                "branchCode": order.get(
                    "branchCode",
                    branch_code
                ),

                "invoiceNumber": order.get(
                    "invoiceNumber",
                    ""
                ),

                "Brand": order.get(
                    "Brand",
                    ""
                ),

                "Channel": order.get(
                    "Channel",
                    ""
                ),

                "Created Date": order.get(
                    "Created Date",
                    pd.NaT
                ),

                "Business Date": business_date,

                "Item Name": item.get(
                    "item_shortName",
                    ""
                ),

                "Qty": item.get(
                    "item_quantity",
                    0
                ),

                "Gross": item.get(
                    "item_baseGrossAmount",
                    0
                ),

                "Discount": item.get(
                    "item_baseNetDiscountAmount",
                    0
                ),

                "Net Revenue": item.get(
                    "item_baseNetAmount",
                    0
                ),
            }

            item_rows.append(
                row
            )

    if not item_rows:
        return pd.DataFrame()

    result = pd.DataFrame(
        item_rows
    )

    # --------------------------------------------------------
    # Clean item fields
    # --------------------------------------------------------

    result["Item Name"] = (
        result["Item Name"]
        .astype(str)
        .str.strip()
    )

    result = result[
        result["Item Name"].ne("")
        & result["Item Name"].ne("NAN")
    ].copy()

    # --------------------------------------------------------
    # Numeric fields
    # --------------------------------------------------------

    result["Qty"] = safe_number(
        result["Qty"]
    )

    result["Gross"] = safe_number(
        result["Gross"]
    )

    result["Discount"] = safe_number(
        result["Discount"]
    )

    result["Net Revenue"] = safe_number(
        result["Net Revenue"]
    )

    # --------------------------------------------------------
    # Channel Group
    # --------------------------------------------------------

    result["Channel Group"] = (
        result["Channel"]
        .apply(channel_group)
    )

    # --------------------------------------------------------
    # Help Sheet mapping
    # --------------------------------------------------------

    help_merge = help_df[
        [
            "branchCode",
            "Store Name",
            "Ownership",
            "Region",
            "Help Channel",
            "Source",
        ]
    ].copy()

    result["branchCode"] = (
        result["branchCode"]
        .astype(str)
        .str.strip()
    )

    help_merge["branchCode"] = (
        help_merge["branchCode"]
        .astype(str)
        .str.strip()
    )

    result = result.merge(
        help_merge,
        on="branchCode",
        how="left"
    )

    # --------------------------------------------------------
    # Item Group mapping
    # --------------------------------------------------------

    result = result.merge(
        item_group_df,
        on="Item Name",
        how="left",
        suffixes=(
            "",
            "_Map"
        )
    )

    # --------------------------------------------------------
    # Product name
    # --------------------------------------------------------

    result["Item Group Name"] = (
        result["Item Group Name"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    result["Product Name"] = (
        result["Item Group Name"]
        .where(
            result["Item Group Name"].ne(""),
            result["Item Name"]
        )
    )

    # --------------------------------------------------------
    # Remove AddOns
    # --------------------------------------------------------

    result["Product Mix"] = (
        result["Product Mix"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    result = result[
        ~result["Product Mix"]
        .str.upper()
        .eq("ADDONS")
    ].copy()

    # --------------------------------------------------------
    # Discount %
    # --------------------------------------------------------

    result["Discount %"] = 0.0

    mask = result["Gross"] != 0

    result.loc[
        mask,
        "Discount %"
    ] = (
        result.loc[
            mask,
            "Discount"
        ]
        / result.loc[
            mask,
            "Gross"
        ]
        * 100
    )

    # --------------------------------------------------------
    # Month
    # --------------------------------------------------------

    result["Month"] = pd.to_datetime(
        result["Business Date"]
    ).dt.strftime(
        "%Y-%m"
    )

    # --------------------------------------------------------
    # Column order
    # --------------------------------------------------------

    final_columns = [
        "Business Date",
        "Month",
        "branchCode",
        "Store Name",
        "Ownership",
        "Region",
        "Brand",
        "Channel",
        "Channel Group",
        "Help Channel",
        "Source",
        "invoiceNumber",
        "Item Name",
        "Item Group Name",
        "Product Name",
        "Variant",
        "Product Mix",
        "Category Group",
        "Qty",
        "Gross",
        "Discount",
        "Discount %",
        "Net Revenue",
        "Created Date",
    ]

    for col in final_columns:

        if col not in result.columns:
            result[col] = ""

    result = result[
        final_columns
    ]

    return result


# ============================================================
# FETCH ONE BUSINESS DATE
# ============================================================

def fetch_business_date(
    business_date,
    help_df,
    item_group_df
):

    print("\n")
    print("=" * 70)
    print(
        f"BUSINESS DATE: "
        f"{business_date}"
    )
    print("=" * 70)

    branch_codes = (
        help_df["branchCode"]
        .dropna()
        .astype(str)
        .str.strip()
        .unique()
        .tolist()
    )

    daily_parts = []

    date_complete = True

    for index, branch_code in enumerate(
        branch_codes,
        start=1
    ):

        print(
            f"\n[{index}/{len(branch_codes)}] "
            f"Fetching: {branch_code}"
        )

        try:

            raw_df = fetch_branch_day(
                branch_code,
                business_date
            )

            if raw_df.empty:

                print(
                    f"      No data: {branch_code}"
                )

                continue

            processed_df = process_branch_data(
                raw_df,
                branch_code,
                business_date,
                help_df,
                item_group_df
            )

            if not processed_df.empty:

                daily_parts.append(
                    processed_df
                )

                print(
                    f"      Item rows: "
                    f"{len(processed_df):,}"
                )

            else:

                print(
                    f"      No usable item data"
                )

        except Exception as e:

            date_complete = False

            print(
                f"      ERROR for "
                f"{branch_code}: {e}"
            )

        time.sleep(
            SLEEP_BETWEEN_BRANCHES
        )

    if daily_parts:

        daily_df = pd.concat(
            daily_parts,
            ignore_index=True
        )

    else:

        daily_df = pd.DataFrame()

    print("\n")
    print(
        f"Date {business_date} completed: "
        f"{date_complete}"
    )

    print(
        f"Total item rows: "
        f"{len(daily_df):,}"
    )

    return daily_df, date_complete


# ============================================================
# READ EXISTING MONTHLY CSV
# ============================================================

def read_existing_month_file(
    file_path
):

    if not os.path.exists(
        file_path
    ):
        return pd.DataFrame()

    try:

        df = pd.read_csv(
            file_path,
            low_memory=False
        )

        if df.empty:
            return df

        if "Business Date" in df.columns:

            df["Business Date"] = (
                pd.to_datetime(
                    df["Business Date"],
                    errors="coerce"
                ).dt.date
            )

        return df

    except Exception as e:

        raise RuntimeError(
            f"Unable to read existing file "
            f"{file_path}: {e}"
        )


# ============================================================
# SAVE MONTHLY RAW FILE
# ============================================================

def save_month_file(
    month,
    new_df,
    refresh_dates
):

    os.makedirs(
        HISTORICAL_FOLDER,
        exist_ok=True
    )

    file_name = (
        f"item_level_{month.replace('-', '_')}.csv"
    )

    file_path = os.path.join(
        HISTORICAL_FOLDER,
        file_name
    )

    print("\n")
    print("=" * 70)
    print(
        f"UPDATING MONTH: {month}"
    )
    print("=" * 70)

    existing_df = read_existing_month_file(
        file_path
    )

    # --------------------------------------------------------
    # Remove only the dates that we successfully refreshed.
    #
    # This is important:
    # if API fails for one date, old data for that date
    # is NOT deleted.
    # --------------------------------------------------------

    if not existing_df.empty:

        if "Business Date" in existing_df.columns:

            before_count = len(
                existing_df
            )

            existing_df = existing_df[
                ~existing_df[
                    "Business Date"
                ].isin(
                    refresh_dates
                )
            ].copy()

            removed_count = (
                before_count
                - len(existing_df)
            )

            print(
                f"Existing rows removed "
                f"for refreshed dates: "
                f"{removed_count:,}"
            )

    if new_df is None:
        new_df = pd.DataFrame()

    if not new_df.empty:

        new_df = new_df.copy()

        new_df["Business Date"] = (
            pd.to_datetime(
                new_df["Business Date"],
                errors="coerce"
            ).dt.date
        )

    # --------------------------------------------------------
    # Combine
    # --------------------------------------------------------

    parts = []

    if not existing_df.empty:
        parts.append(
            existing_df
        )

    if not new_df.empty:
        parts.append(
            new_df
        )

    if parts:

        final_df = pd.concat(
            parts,
            ignore_index=True
        )

    else:

        final_df = pd.DataFrame()

    if final_df.empty:

        print(
            f"No data to save for {month}"
        )

        return

    # --------------------------------------------------------
    # Sort
    # --------------------------------------------------------

    sort_columns = [
        col
        for col in [
            "Business Date",
            "Region",
            "Store Name",
            "Brand",
            "Channel Group",
            "Product Name",
            "Item Name",
        ]
        if col in final_df.columns
    ]

    if sort_columns:

        final_df = final_df.sort_values(
            sort_columns,
            kind="stable"
        )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    final_df.to_csv(
        file_path,
        index=False,
        encoding="utf-8-sig"
    )

    file_size_mb = (
        os.path.getsize(
            file_path
        )
        / (
            1024 * 1024
        )
    )

    print(
        f"Saved: {file_path}"
    )

    print(
        f"Rows: {len(final_df):,}"
    )

    print(
        f"File size: {file_size_mb:.2f} MB"
    )

    if file_size_mb > 90:

        print(
            "WARNING: Monthly CSV is above "
            "90 MB. GitHub/App Script processing "
            "may become heavy."
        )


# ============================================================
# CREATE MONTHLY INDEX
# ============================================================

def create_index():

    print("\n")
    print("=" * 70)
    print("Creating historical_data/index.json")
    print("=" * 70)

    os.makedirs(
        HISTORICAL_FOLDER,
        exist_ok=True
    )

    files = []

    for file_name in sorted(
        os.listdir(
            HISTORICAL_FOLDER
        )
    ):

        if not (
            file_name.startswith(
                "item_level_"
            )
            and file_name.endswith(
                ".csv"
            )
        ):
            continue

        file_path = os.path.join(
            HISTORICAL_FOLDER,
            file_name
        )

        try:

            df = pd.read_csv(
                file_path,
                usecols=[
                    "Business Date"
                ],
                low_memory=False
            )

            dates = pd.to_datetime(
                df["Business Date"],
                errors="coerce"
            ).dropna()

            if not dates.empty:

                min_date = (
                    dates.min()
                    .strftime("%Y-%m-%d")
                )

                max_date = (
                    dates.max()
                    .strftime("%Y-%m-%d")
                )

            else:

                min_date = ""
                max_date = ""

            row_count = len(df)

        except Exception as e:

            print(
                f"Warning reading "
                f"{file_name}: {e}"
            )

            min_date = ""
            max_date = ""
            row_count = 0

        files.append(
            {
                "month": file_name[
                    len("item_level_")
                    :-len(".csv")
                ].replace(
                    "_",
                    "-"
                ),
                "file": file_name,
                "minDate": min_date,
                "maxDate": max_date,
                "rows": row_count,
            }
        )

    index_data = {
        "generatedAt": datetime.now().isoformat(),
        "folder": HISTORICAL_FOLDER,
        "files": files,
    }

    index_path = os.path.join(
        HISTORICAL_FOLDER,
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
        f"Created: {index_path}"
    )


# ============================================================
# GIT COMMIT + PUSH
# ============================================================

def git_push():

    print("\n")
    print("=" * 70)
    print("PUSHING HISTORICAL DATA TO GITHUB")
    print("=" * 70)

    # Only push when running inside GitHub Actions
    github_actions = (
        os.getenv(
            "GITHUB_ACTIONS",
            ""
        ).lower()
        == "true"
    )

    if not github_actions:

        print(
            "Not running inside GitHub Actions."
        )

        print(
            "Git push skipped."
        )

        return

    # --------------------------------------------------------
    # Git identity
    # --------------------------------------------------------

    subprocess.run(
        [
            "git",
            "config",
            "user.name",
            "github-actions[bot]"
        ],
        check=True
    )

    subprocess.run(
        [
            "git",
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com"
        ],
        check=True
    )

    # --------------------------------------------------------
    # Add historical data
    # --------------------------------------------------------

    subprocess.run(
        [
            "git",
            "add",
            HISTORICAL_FOLDER
        ],
        check=True
    )

    # --------------------------------------------------------
    # Check changes
    # --------------------------------------------------------

    status = subprocess.run(
        [
            "git",
            "status",
            "--porcelain"
        ],
        capture_output=True,
        text=True,
        check=True
    )

    if not status.stdout.strip():

        print(
            "No GitHub changes detected."
        )

        return

    print(
        "Git changes:"
    )

    print(
        status.stdout
    )

    # --------------------------------------------------------
    # Commit
    # --------------------------------------------------------

    commit_message = (
        "Update item level historical data "
        + datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    subprocess.run(
        [
            "git",
            "commit",
            "-m",
            commit_message
        ],
        check=True
    )

    # --------------------------------------------------------
    # Push
    # --------------------------------------------------------

    subprocess.run(
        [
            "git",
            "push"
        ],
        check=True
    )

    print(
        "GitHub push completed successfully."
    )


# ============================================================
# DATE RANGE
# ============================================================

def get_date_range():

    start_date = datetime.strptime(
        HISTORY_START_DATE,
        "%Y-%m-%d"
    ).date()

    if HISTORY_END_DATE:

        end_date = datetime.strptime(
            HISTORY_END_DATE,
            "%Y-%m-%d"
        ).date()

    else:

        # Current business date.
        #
        # Business day starts at 09:00 AM.
        # Before 09:00 AM, today's business date
        # is still yesterday.

        now_ist = (
            pd.Timestamp.now(
                tz="Asia/Kolkata"
            )
        )

        if now_ist.hour < 9:

            end_date = (
                now_ist.date()
                - timedelta(days=1)
            )

        else:

            end_date = (
                now_ist.date()
            )

    if start_date > end_date:

        raise RuntimeError(
            f"Invalid date range: "
            f"{start_date} -> {end_date}"
        )

    return start_date, end_date


# ============================================================
# MAIN
# ============================================================

def main():

    print("\n")
    print("=" * 80)
    print("ITEM LEVEL HISTORICAL DATA")
    print("=" * 80)

    # --------------------------------------------------------
    # Date range
    # --------------------------------------------------------

    start_date, end_date = get_date_range()

    print(
        f"Start Date: {start_date}"
    )

    print(
        f"End Date:   {end_date}"
    )

    print(
        f"Folder:     {HISTORICAL_FOLDER}"
    )

    # --------------------------------------------------------
    # Create folder
    # --------------------------------------------------------

    os.makedirs(
        HISTORICAL_FOLDER,
        exist_ok=True
    )

    # --------------------------------------------------------
    # Load mappings
    # --------------------------------------------------------

    help_df = load_help_sheet()

    item_group_df = load_item_group()

    # --------------------------------------------------------
    # Process month-by-month
    #
    # Only one month's new data is held in memory at a time.
    # This is important for large historical data.
    # --------------------------------------------------------

    current_month = None

    month_parts = []

    successful_dates = []

    failed_dates = []

    current_date = start_date

    while current_date <= end_date:

        month = current_date.strftime(
            "%Y-%m"
        )

        # ----------------------------------------------------
        # If month changes, save previous month.
        # ----------------------------------------------------

        if (
            current_month is not None
            and month != current_month
        ):

            if month_parts:

                month_df = pd.concat(
                    month_parts,
                    ignore_index=True
                )

            else:

                month_df = pd.DataFrame()

            month_refresh_dates = [
                d
                for d in successful_dates
                if d.strftime(
                    "%Y-%m"
                ) == current_month
            ]

            save_month_file(
                current_month,
                month_df,
                month_refresh_dates
            )

            month_parts = []

        current_month = month

        # ----------------------------------------------------
        # Fetch business date
        # ----------------------------------------------------

        daily_df, date_complete = (
            fetch_business_date(
                current_date,
                help_df,
                item_group_df
            )
        )

        if date_complete:

            successful_dates.append(
                current_date
            )

            if not daily_df.empty:

                month_parts.append(
                    daily_df
                )

        else:

            failed_dates.append(
                current_date
            )

            print(
                f"WARNING: {current_date} "
                f"was NOT fully refreshed."
            )

            print(
                "Existing GitHub data for this "
                "date will be preserved."
            )

        current_date += timedelta(
            days=1
        )

    # --------------------------------------------------------
    # Save final month
    # --------------------------------------------------------

    if current_month is not None:

        if month_parts:

            month_df = pd.concat(
                month_parts,
                ignore_index=True
            )

        else:

            month_df = pd.DataFrame()

        month_refresh_dates = [
            d
            for d in successful_dates
            if d.strftime(
                "%Y-%m"
            ) == current_month
        ]

        save_month_file(
            current_month,
            month_df,
            month_refresh_dates
        )

    # --------------------------------------------------------
    # Create index
    # --------------------------------------------------------

    create_index()

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print("\n")
    print("=" * 80)
    print("HISTORICAL DATA SUMMARY")
    print("=" * 80)

    print(
        f"Successful dates: "
        f"{len(successful_dates):,}"
    )

    print(
        f"Failed/incomplete dates: "
        f"{len(failed_dates):,}"
    )

    if failed_dates:

        print(
            "\nFailed dates:"
        )

        for d in failed_dates:

            print(
                f"  - {d}"
            )

        print(
            "\nIMPORTANT:"
        )

        print(
            "Incomplete dates were not removed "
            "from existing monthly GitHub files."
        )

    # --------------------------------------------------------
    # Push
    # --------------------------------------------------------

    git_push()

    print("\n")
    print("=" * 80)
    print("ITEM LEVEL HISTORICAL DATA COMPLETED")
    print("=" * 80)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()


print("\n" + "=" * 70)
print("🏁 SCRIPT FINISHED")
print("=" * 70)
