# =========================================================
# HOURLY ORDER FLOW PERFORMANCE
# RISTA SALES PAGE + GOOGLE SHEETS HELP SHEET
#
# COCO stores from Help Sheet Ownership = COCO
# Region from Help Sheet Region
# In-Store = Offline
# All other channels = Online
#
# Report:
#   Swiggy Orders : Frozen Bottle | Madno | Boba Bar
#   Zomato Orders : Frozen Bottle | Madno | Boba Bar
#
# Every hour:
#   1. ALERT stores
#   2. NORMAL stores
#
# Alert:
#   Current hour successful orders = 0
#   AND previous hour successful orders > 0
#   OR current hour has Cancel / Reject / Void
#
# Orders = unique invoiceNumber
# =========================================================


# =========================================================
# IMPORTS
# =========================================================

import os
import re
import json
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from zoneinfo import ZoneInfo

import jwt
import pandas as pd
import requests
import gspread

from google.oauth2.service_account import Credentials


# =========================================================
# CONFIGURATION
# =========================================================

IST = ZoneInfo("Asia/Kolkata")

# Rista base is fixed; no RISTA_API_BASE secret is required.
RISTA_API_BASE = "https://api.ristaapps.com/v1"

# Google Sheet -> Help Sheet is the store master.
GOOGLE_SHEET_ID = "19z6KkVBFoLC33_wcNqVhDLyQEC2dDQ8YQE0gE38BhVg"
HELP_SHEET_NAME = "Help Sheet"

API_KEY = os.getenv("API_KEY", "").strip()
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()

EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com").strip()
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587").strip() or "587")
EMAIL_USER = os.getenv("EMAIL_USER", "").strip()
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "").strip()
EMAIL_TO = os.getenv("EMAIL_TO", "").strip()
EMAIL_CC = os.getenv("EMAIL_CC", "").strip()

# =========================================================
# REPORT CONFIGURATION
# =========================================================

REGIONS = [
    "KA",
    "MH",
    "TN",
    "Kerela",
]

BRANDS = [
    "Frozen Bottle",
    "Madno",
    "Boba Bar",
]

SOURCES = [
    "Swiggy",
    "Zomato",
]


# =========================================================
# NORMALIZATION
# =========================================================

def norm(value):

    return (
        str(value or "")
        .strip()
        .lower()
        .replace("–", "-")
        .replace("—", "-")
        .replace("_", "-")
        .replace("  ", " ")
    )


# =========================================================
# BRAND NORMALIZATION
# =========================================================

def normalize_brand(value):

    text = norm(value)

    if "frozen bottle" in text:
        return "Frozen Bottle"

    if "madno" in text:
        return "Madno"

    if "boba bar" in text:
        return "Boba Bar"

    return ""


# =========================================================
# REGION NORMALIZATION
#
# Rista branch API gives taxArea such as:
# Kerala
# Karnataka
# Maharashtra
# Tamil Nadu
#
# Convert to required reporting regions:
# KA / MH / TN / Kerela
# =========================================================

def normalize_region(value):

    text = norm(value)

    # Karnataka
    if (
        text in {
            "karnataka",
            "ka",
            "bangalore",
            "bengaluru",
        }
        or "karnataka" in text
    ):
        return "KA"

    # Maharashtra
    if (
        text in {
            "maharashtra",
            "mh",
            "mumbai",
            "pune",
        }
        or "maharashtra" in text
    ):
        return "MH"

    # Tamil Nadu
    if (
        text in {
            "tamil nadu",
            "tamilnadu",
            "tn",
            "chennai",
        }
        or "tamil" in text
    ):
        return "TN"

    # Kerala
    if (
        text in {
            "kerala",
            "kerela",
            "kl",
        }
        or "kerala" in text
        or "kerela" in text
    ):
        return "Kerela"

    return ""


# =========================================================
# CHANNEL CLASSIFICATION
#
# In-Store = Offline
# Everything else = Online
# =========================================================

def channel_type(channel):

    text = norm(channel)

    if "in-store" in text or "in store" in text:

        return "Offline"

    return "Online"


# =========================================================
# ONLINE SOURCE
#
# Only Swiggy / Zomato are required for this report.
# =========================================================

def source_from_channel(channel):

    text = norm(channel)

    if text.startswith("swiggy "):

        return "Swiggy"

    if text.startswith("zomato "):

        return "Zomato"

    return ""


# =========================================================
# JWT TOKEN
# =========================================================

# =========================================================
# RISTA REQUEST
# =========================================================

def get_token():

    if not API_KEY:
        raise RuntimeError("API_KEY is missing.")

    if not SECRET_KEY:
        raise RuntimeError("SECRET_KEY is missing.")

    payload = {
        "iss": API_KEY,
        "iat": int(datetime.now(IST).timestamp()),
    }

    return jwt.encode(
        payload,
        SECRET_KEY,
        algorithm="HS256",
    )


def headers():

    return {
        "x-api-key": API_KEY,
        "x-api-token": get_token(),
        "content-type": "application/json",
    }


def get(endpoint, params=None):

    endpoint = endpoint.lstrip("/")
    url = f"{RISTA_API_BASE}/{endpoint}"

    response = requests.get(
        url,
        headers=headers(),
        params=params,
        timeout=60,
    )

    print(
        f"Rista GET | {endpoint} | HTTP {response.status_code}"
    )

    response.raise_for_status()

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Rista API returned non-JSON response for {url}: "
            f"{response.text[:500]}"
        ) from exc


# =========================================================
# COCO STORE MASTER
#
# IMPORTANT:
# We do NOT depend on /branch/list to identify COCO stores.
# The Google Sheet Help Sheet is the authoritative store master.
#
# Current Help Sheet columns shown by the user:
# A branchCode
# B Store Name
# C Ownership
# D AM Email
# E RM Email
# F AM Name
# G CC Mail
# H Region
# =========================================================

def get_coco_branches():

    print("=" * 70)
    print("LOADING COCO STORES FROM GOOGLE SHEET")
    print("=" * 70)

    credentials_json = os.getenv("GOOGLE_CREDENTIALS", "").strip()

    if not credentials_json:
        raise RuntimeError(
            "GOOGLE_CREDENTIALS is missing. Add it to GitHub Repository Secrets."
        )

    try:
        credentials_dict = json.loads(credentials_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "GOOGLE_CREDENTIALS is not valid JSON."
        ) from exc

    credentials = Credentials.from_service_account_info(
        credentials_dict,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )

    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    worksheet = spreadsheet.worksheet(HELP_SHEET_NAME)

    values = worksheet.get_all_values()

    if not values:
        raise RuntimeError("Help Sheet is empty.")

    headers_row = [
        str(x).strip()
        for x in values[0]
    ]

    print("Help Sheet columns:", headers_row)

    required = [
        "branchCode",
        "Store Name",
        "Ownership",
        "Region",
    ]

    missing = [
        col
        for col in required
        if col not in headers_row
    ]

    if missing:
        raise RuntimeError(
            "Help Sheet missing required columns: "
            + ", ".join(missing)
        )

    rows = []

    for raw_row in values[1:]:
        row = list(raw_row)

        if len(row) < len(headers_row):
            row.extend(
                [""] * (len(headers_row) - len(row))
            )

        rows.append(
            row[:len(headers_row)]
        )

    help_df = pd.DataFrame(
        rows,
        columns=headers_row,
    )

    for col in required:
        help_df[col] = (
            help_df[col]
            .fillna("")
            .astype(str)
            .str.strip()
        )

    help_df["Ownership"] = (
        help_df["Ownership"]
        .str.upper()
        .str.strip()
    )

    help_df["Region"] = (
        help_df["Region"]
        .apply(normalize_region)
    )

    # COCO ONLY from Help Sheet.
    help_df = help_df[
        help_df["Ownership"] == "COCO"
    ].copy()

    # Required reporting regions only.
    help_df = help_df[
        help_df["Region"].isin(REGIONS)
    ].copy()

    # Valid branch code only.
    help_df = help_df[
        help_df["branchCode"] != ""
    ].copy()

    # One master row per branch.
    branches_df = (
        help_df
        .drop_duplicates("branchCode")
        .sort_values(["Region", "Store Name"])
        .reset_index(drop=True)
    )

    print()
    print(
        f"Help Sheet data rows : {len(values) - 1}"
    )
    print(
        f"COCO stores          : {len(branches_df)}"
    )

    print()
    print("COCO stores by region:")

    print(
        branches_df
        .groupby("Region")
        .size()
        .reindex(REGIONS, fill_value=0)
        .to_string()
    )

    print()
    print("Sample COCO store mapping:")
    print(
        branches_df[
            [
                "branchCode",
                "Store Name",
                "Ownership",
                "Region",
            ]
        ]
        .head(10)
        .to_string(index=False)
    )

    print()

    return branches_df


# =========================================================
# SALES PAGE
# =========================================================

def sales_page(
    branch_code,
    day,
):

    rows = []

    page = 1

    while True:

        params = {
            "branch": branch_code,
            "day": day,
            "page": page,
            "limit": 5000,
        }

        response = get(
            "/sales/page",
            params,
        )

        data = response.get(
            "data",
            []
        )

        if not isinstance(
            data,
            list,
        ):

            data = []

        rows.extend(data)

        if len(data) < 5000:

            break

        page += 1

    return rows


# =========================================================
# DATE/TIME CONVERSION
# =========================================================

def convert_to_ist(value):

    timestamp = pd.to_datetime(
        value,
        errors="coerce",
    )

    if pd.isna(timestamp):

        return pd.NaT

    try:

        if timestamp.tzinfo is None:

            return timestamp.tz_localize(
                IST
            )

        return timestamp.tz_convert(
            IST
        )

    except Exception:

        return pd.NaT


# =========================================================
# PREPARE SALES DATA
# =========================================================

def prepare_sales(
    rows,
    branches_df,
):

    if not rows:

        return pd.DataFrame()

    df = pd.json_normalize(
        rows
    )

    # -----------------------------------------------------
    # Required aliases
    # -----------------------------------------------------

    aliases = {

        "branchCode": [
            "branchCode",
            "branch",
            "outletId",
        ],

        "branchName": [
            "branchName",
        ],

        "invoiceNumber": [
            "invoiceNumber",
            "invoiceNo",
            "sourceInfo.invoiceNumber",
        ],

        "invoiceDate": [
            "invoiceDate",
            "sourceInfo.invoiceDate",
            "createdDate",
            "modifiedDate",
        ],

        "brandName": [
            "brandName",
            "brand",
            "sourceInfo.companyName",
        ],

        "channel": [
            "channel",
            "sourceInfo.source",
        ],

        "fulfillmentStatus": [
            "fulfillmentStatus",
            "status",
        ],

        "cancelReason": [
            "cancelReason",
            "cancellationReason",
            "voidReason",
            "statusInfo.reason",
            "statusInfo.sourceReason",
            "reason",
        ],
    }

    for target, names in aliases.items():

        if target in df.columns:

            continue

        found = None

        for name in names:

            if name in df.columns:

                found = df[name]

                break

        if found is None:

            df[target] = ""

        else:

            df[target] = found

    # -----------------------------------------------------
    # Clean branch code
    # -----------------------------------------------------

    df["branchCode"] = (
        df["branchCode"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # -----------------------------------------------------
    # Brand
    # -----------------------------------------------------

    df["Brand"] = (
        df["brandName"]
        .apply(normalize_brand)
    )

    missing_brand = df["Brand"] == ""

    df.loc[missing_brand, "Brand"] = (
        df.loc[missing_brand, "channel"]
        .apply(normalize_brand)
    )

    # -----------------------------------------------------
    # Source
    # -----------------------------------------------------

    df["Source"] = (
        df["channel"]
        .apply(
            source_from_channel
        )
    )

    # -----------------------------------------------------
    # Channel Type
    # -----------------------------------------------------

    df["Channel Type"] = (
        df["channel"]
        .apply(
            channel_type
        )
    )

    # -----------------------------------------------------
    # Keep only Online
    #
    # Swiggy/Zomato are online.
    # In-Store is offline and therefore excluded.
    # -----------------------------------------------------

    df = df[
        df["Channel Type"] == "Online"
    ].copy()

    # -----------------------------------------------------
    # Keep only Swiggy / Zomato
    # -----------------------------------------------------

    df = df[
        df["Source"].isin(SOURCES)
    ].copy()

    # -----------------------------------------------------
    # Keep only required brands
    # -----------------------------------------------------

    df = df[
        df["Brand"].isin(BRANDS)
    ].copy()

    # -----------------------------------------------------
    # Event time
    # -----------------------------------------------------

    df["EventTime"] = (
        df["invoiceDate"]
        .apply(convert_to_ist)
    )

    df = df[
        df["EventTime"].notna()
    ].copy()

    # -----------------------------------------------------
    # Hour
    # -----------------------------------------------------

    df["Hour"] = (
        df["EventTime"]
        .dt.floor("h")
    )

    # -----------------------------------------------------
    # Invoice number
    # -----------------------------------------------------

    df["invoiceNumber"] = (
        df["invoiceNumber"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # -----------------------------------------------------
    # Fulfillment status
    # -----------------------------------------------------

    df["Fulfillment Status"] = (
        df["fulfillmentStatus"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # -----------------------------------------------------
    # Cancel reason
    # -----------------------------------------------------

    df["Cancel Reason"] = (
        df["cancelReason"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # -----------------------------------------------------
    # Problem flag
    #
    # Cancel / Reject / Void
    # -----------------------------------------------------

    status_text = (
        df["Fulfillment Status"]
        .apply(norm)
    )

    reason_text = (
        df["Cancel Reason"]
        .apply(norm)
    )

    df["Problem"] = (
        status_text.str.contains(
            r"cancel|reject|void",
            regex=True,
            na=False,
        )
        |
        reason_text.str.contains(
            r"""
            cancel|
            reject|
            void|
            store\s*closed|
            store\s*busy|
            out\s*of\s*stock|
            payment\s*issue|
            customer\s*cancel
            """,
            regex=True,
            na=False,
        )
    )

    # -----------------------------------------------------
    # Merge only for validating branch universe
    # -----------------------------------------------------

    branch_map = branches_df[
        [
            "branchCode",
            "Store Name",
            "Region",
        ]
    ].drop_duplicates(
        "branchCode"
    )

    df = df.merge(
        branch_map,
        on="branchCode",
        how="inner",
    )

    return df


# =========================================================
# BUILD HOURLY FLOW
# =========================================================

def build_hourly_flow(
    sales_df,
):

    if sales_df.empty:

        return pd.DataFrame()

    keys = [
        "Region",
        "Store Name",
        "branchCode",
        "Brand",
        "Source",
        "Hour",
    ]

    # -----------------------------------------------------
    # Successful orders
    #
    # Unique invoice number
    # -----------------------------------------------------

    normal = sales_df[
        ~sales_df["Problem"]
    ].copy()

    normal = normal[
        normal["invoiceNumber"] != ""
    ].copy()

    successful = (
        normal
        .groupby(
            keys,
            dropna=False,
        )["invoiceNumber"]
        .nunique()
        .reset_index(
            name="Orders"
        )
    )

    # -----------------------------------------------------
    # Problem orders
    # -----------------------------------------------------

    problem = sales_df[
        sales_df["Problem"]
    ].copy()

    problem = problem[
        problem["invoiceNumber"] != ""
    ].copy()

    problem_flow = (
        problem
        .groupby(
            keys,
            dropna=False,
        )
        .agg(
            Problem_Orders=(
                "invoiceNumber",
                "nunique",
            ),

            Cancel_Reasons=(
                "Cancel Reason",
                lambda values:
                    "; ".join(
                        sorted(
                            {
                                str(x).strip()
                                for x in values
                                if str(x).strip()
                            }
                        )
                    ),
            ),
        )
        .reset_index()
    )

    # -----------------------------------------------------
    # Merge
    # -----------------------------------------------------

    flow = successful.merge(
        problem_flow,
        on=keys,
        how="outer",
    )

    flow["Orders"] = (
        pd.to_numeric(
            flow["Orders"],
            errors="coerce",
        )
        .fillna(0)
        .astype(int)
    )

    flow["Problem_Orders"] = (
        pd.to_numeric(
            flow["Problem_Orders"],
            errors="coerce",
        )
        .fillna(0)
        .astype(int)
    )

    flow["Cancel_Reasons"] = (
        flow["Cancel_Reasons"]
        .fillna("")
    )

    return flow


# =========================================================
# BUILD COMPLETE STORE PERFORMANCE
#
# IMPORTANT:
# We create ALL COCO stores ×
# Brand × Source combinations.
#
# This means a store with zero orders
# will still appear in the email.
# =========================================================

def build_store_performance(
    branches_df,
    flow,
    current_hour,
    previous_hour,
):

    combinations = []

    for _, branch in branches_df.iterrows():

        for brand_name in BRANDS:

            for source_name in SOURCES:

                combinations.append(
                    {
                        "Region":
                            branch["Region"],

                        "Store Name":
                            branch["Store Name"],

                        "branchCode":
                            branch["branchCode"],

                        "Brand":
                            brand_name,

                        "Source":
                            source_name,
                    }
                )

    universe = pd.DataFrame(
        combinations
    )

    # -----------------------------------------------------
    # Current hour
    # -----------------------------------------------------

    current = pd.DataFrame(
        columns=[
            "Region",
            "Store Name",
            "branchCode",
            "Brand",
            "Source",
            "Orders",
            "Problem_Orders",
            "Cancel_Reasons",
        ]
    )

    if not flow.empty:

        current = flow[
            flow["Hour"]
            == pd.Timestamp(
                current_hour
            )
        ].copy()

        current = (
            current
            .groupby(
                [
                    "Region",
                    "Store Name",
                    "branchCode",
                    "Brand",
                    "Source",
                ],
                dropna=False,
            )
            .agg(
                Orders=(
                    "Orders",
                    "sum",
                ),

                Problem_Orders=(
                    "Problem_Orders",
                    "sum",
                ),

                Cancel_Reasons=(
                    "Cancel_Reasons",
                    lambda values:
                        "; ".join(
                            sorted(
                                {
                                    str(x).strip()
                                    for x in values
                                    if str(x).strip()
                                }
                            )
                        ),
                ),
            )
            .reset_index()
        )

    current = current.rename(
        columns={
            "Orders":
                "Current_Orders",

            "Problem_Orders":
                "Current_Problem",

            "Cancel_Reasons":
                "Current_Reasons",
        }
    )

    # -----------------------------------------------------
    # Previous hour
    # -----------------------------------------------------

    previous = pd.DataFrame(
        columns=[
            "Region",
            "Store Name",
            "branchCode",
            "Brand",
            "Source",
            "Previous_Orders",
        ]
    )

    if not flow.empty:

        previous = flow[
            flow["Hour"]
            == pd.Timestamp(
                previous_hour
            )
        ].copy()

        previous = (
            previous
            .groupby(
                [
                    "Region",
                    "Store Name",
                    "branchCode",
                    "Brand",
                    "Source",
                ],
                dropna=False,
            )["Orders"]
            .sum()
            .reset_index(
                name="Previous_Orders"
            )
        )

    # -----------------------------------------------------
    # Merge complete universe
    # -----------------------------------------------------

    result = universe.merge(
        current,
        on=[
            "Region",
            "Store Name",
            "branchCode",
            "Brand",
            "Source",
        ],
        how="left",
    )

    result = result.merge(
        previous,
        on=[
            "Region",
            "Store Name",
            "branchCode",
            "Brand",
            "Source",
        ],
        how="left",
    )

    # -----------------------------------------------------
    # Fill numbers
    # -----------------------------------------------------

    for col in [
        "Current_Orders",
        "Current_Problem",
        "Previous_Orders",
    ]:

        result[col] = (
            pd.to_numeric(
                result[col],
                errors="coerce",
            )
            .fillna(0)
            .astype(int)
        )

    result["Current_Reasons"] = (
        result["Current_Reasons"]
        .fillna("")
    )

    # -----------------------------------------------------
    # Alert rule
    # -----------------------------------------------------

    result["Alert"] = (
        (
            result["Current_Orders"]
            == 0
        )
        &
        (
            (
                result["Previous_Orders"]
                > 0
            )
            |
            (
                result["Current_Problem"]
                > 0
            )
        )
    )

    # -----------------------------------------------------
    # Status
    # -----------------------------------------------------

    result["Status"] = "Normal"

    result.loc[
        result["Alert"],
        "Status",
    ] = "Alert"

    # -----------------------------------------------------
    # Remarks
    # -----------------------------------------------------

    def build_remark(row):

        if row["Alert"]:

            remarks = []

            if (
                row["Previous_Orders"]
                > 0
                and row["Current_Orders"]
                == 0
            ):

                remarks.append(
                    "No successful order in current hour"
                )

            if row["Current_Problem"] > 0:

                if row["Current_Reasons"]:

                    remarks.append(
                        "Cancel/Reject/Void: "
                        + row["Current_Reasons"]
                    )

                else:

                    remarks.append(
                        "Cancel/Reject/Void order recorded"
                    )

            return " | ".join(
                remarks
            )

        return "Order flow normal"

    result["Remarks"] = result.apply(
        build_remark,
        axis=1,
    )

    return result


# =========================================================
# HOURLY OVERALL TABLE
# =========================================================

def hourly_overall_table(flow):
    """Build one row per reporting hour for the overall business day."""
    columns = [
        "Hour", "Swiggy Orders", "Zomato Orders", "Total Orders",
        "Swiggy %", "Zomato %"
    ]

    if flow is None or flow.empty:
        return (
            '<table border="1" cellpadding="6" cellspacing="0" '
            'style="border-collapse:collapse;width:100%;">'
            '<tr style="background:#d9eaf7;">' +
            ''.join(f'<th>{escape(c)}</th>' for c in columns) +
            '</tr><tr><td colspan="6" align="center">No order data</td></tr></table>'
        )

    h = flow.copy()
    h["Orders"] = pd.to_numeric(h["Orders"], errors="coerce").fillna(0).astype(int)
    grouped = h.groupby(["Hour", "Source"], dropna=False)["Orders"].sum().unstack(fill_value=0)

    rows = []
    for hour, values in grouped.sort_index().iterrows():
        sw = int(values.get("Swiggy", 0))
        zo = int(values.get("Zomato", 0))
        total = sw + zo
        sw_pct = (sw / total * 100) if total else 0
        zo_pct = (zo / total * 100) if total else 0
        hour_value = pd.Timestamp(hour)
        rows.append(
            f'<tr><td>{escape(hour_value.strftime("%d-%b %I:%M %p"))}</td>'
            f'<td align="center">{sw}</td>'
            f'<td align="center">{zo}</td>'
            f'<td align="center"><b>{total}</b></td>'
            f'<td align="center">{sw_pct:.1f}%</td>'
            f'<td align="center">{zo_pct:.1f}%</td></tr>'
        )

    header = ''.join(f'<th>{escape(c)}</th>' for c in columns)
    return (
        '<table border="1" cellpadding="6" cellspacing="0" '
        'style="border-collapse:collapse;width:100%;font-size:13px;">'
        f'<tr style="background:#d9eaf7;">{header}</tr>'
        + ''.join(rows) +
        '</table>'
    )


# =========================================================
# STORE-LEVEL OVERALL TABLE
# CURRENT vs LAST WEEK SAME DAY / SAME HOUR
# =========================================================

def store_overall_table(flow, lw_flow, performance):

    columns = [
        "Region",
        "Store",
        "Swiggy Frozen Bottle",
        "Swiggy Madno",
        "Swiggy Boba Bar",
        "Zomato Frozen Bottle",
        "Zomato Madno",
        "Zomato Boba Bar",
        "Swiggy Total",
        "Zomato Total",
        "Current Orders",
        "LW Orders",
        "LW %",
        "Remarks",
    ]

    # -----------------------------------------------------
    # STORE MASTER
    # -----------------------------------------------------

    base = performance[
        [
            "Region",
            "Store Name",
            "branchCode",
        ]
    ].drop_duplicates(
        "branchCode"
    ).copy()

    base = base.rename(
        columns={
            "Store Name": "Store"
        }
    )

    # -----------------------------------------------------
    # CURRENT PERIOD
    # -----------------------------------------------------

    if flow is not None and not flow.empty:

        current = flow.copy()

        current["Orders"] = (
            pd.to_numeric(
                current["Orders"],
                errors="coerce",
            )
            .fillna(0)
            .astype(int)
        )

        current_pivot = (
            current
            .groupby(
                [
                    "branchCode",
                    "Source",
                    "Brand",
                ],
                dropna=False,
            )["Orders"]
            .sum()
            .unstack(
                ["Source", "Brand"],
                fill_value=0,
            )
        )

        current_pivot.columns = [
            f"CURRENT_{source}_{brand}"
            for source, brand
            in current_pivot.columns
        ]

        current_pivot = (
            current_pivot
            .reset_index()
        )

        base = base.merge(
            current_pivot,
            on="branchCode",
            how="left",
        )

    # -----------------------------------------------------
    # LAST WEEK
    # -----------------------------------------------------

    if lw_flow is not None and not lw_flow.empty:

        lw = lw_flow.copy()

        lw["Orders"] = (
            pd.to_numeric(
                lw["Orders"],
                errors="coerce",
            )
            .fillna(0)
            .astype(int)
        )

        lw_pivot = (
            lw
            .groupby(
                [
                    "branchCode",
                    "Source",
                    "Brand",
                ],
                dropna=False,
            )["Orders"]
            .sum()
            .unstack(
                ["Source", "Brand"],
                fill_value=0,
            )
        )

        lw_pivot.columns = [
            f"LW_{source}_{brand}"
            for source, brand
            in lw_pivot.columns
        ]

        lw_pivot = (
            lw_pivot
            .reset_index()
        )

        base = base.merge(
            lw_pivot,
            on="branchCode",
            how="left",
        )

    # -----------------------------------------------------
    # ENSURE ALL CURRENT CHANNEL/BRAND COLUMNS EXIST
    # -----------------------------------------------------

    metric_cols = [
        "CURRENT_Swiggy_Frozen Bottle",
        "CURRENT_Swiggy_Madno",
        "CURRENT_Swiggy_Boba Bar",
        "CURRENT_Zomato_Frozen Bottle",
        "CURRENT_Zomato_Madno",
        "CURRENT_Zomato_Boba Bar",

        "LW_Swiggy_Frozen Bottle",
        "LW_Swiggy_Madno",
        "LW_Swiggy_Boba Bar",
        "LW_Zomato_Frozen Bottle",
        "LW_Zomato_Madno",
        "LW_Zomato_Boba Bar",
    ]

    for col in metric_cols:

        if col not in base.columns:
            base[col] = 0

        base[col] = (
            pd.to_numeric(
                base[col],
                errors="coerce",
            )
            .fillna(0)
            .astype(int)
        )

    # -----------------------------------------------------
    # CURRENT SWIGGY / ZOMATO TOTAL
    # -----------------------------------------------------

    base["Swiggy Total"] = base[
        [
            "CURRENT_Swiggy_Frozen Bottle",
            "CURRENT_Swiggy_Madno",
            "CURRENT_Swiggy_Boba Bar",
        ]
    ].sum(axis=1)

    base["Zomato Total"] = base[
        [
            "CURRENT_Zomato_Frozen Bottle",
            "CURRENT_Zomato_Madno",
            "CURRENT_Zomato_Boba Bar",
        ]
    ].sum(axis=1)

    base["Current Orders"] = (
        base["Swiggy Total"]
        + base["Zomato Total"]
    )

    # -----------------------------------------------------
    # LW CHANNEL TOTALS
    # -----------------------------------------------------

    base["LW Swiggy"] = base[
        [
            "LW_Swiggy_Frozen Bottle",
            "LW_Swiggy_Madno",
            "LW_Swiggy_Boba Bar",
        ]
    ].sum(axis=1)

    base["LW Zomato"] = base[
        [
            "LW_Zomato_Frozen Bottle",
            "LW_Zomato_Madno",
            "LW_Zomato_Boba Bar",
        ]
    ].sum(axis=1)

    base["LW Orders"] = (
        base["LW Swiggy"]
        + base["LW Zomato"]
    )

    # -----------------------------------------------------
    # LW %
    #
    # Current vs LW
    # -----------------------------------------------------

    base["LW %"] = base.apply(
        lambda r:
            (
                (r["Current Orders"] - r["LW Orders"])
                / r["LW Orders"]
                * 100
            )
            if r["LW Orders"] > 0
            else None,
        axis=1,
    )

    # -----------------------------------------------------
    # REMARKS
    #
    # Identify weaker channel vs LW
    # -----------------------------------------------------

    def channel_remark(row):

        current_sw = row["Swiggy Total"]
        current_zo = row["Zomato Total"]

        lw_sw = row["LW Swiggy"]
        lw_zo = row["LW Zomato"]

        sw_pct = (
            (current_sw - lw_sw)
            / lw_sw
            * 100
            if lw_sw > 0
            else None
        )

        zo_pct = (
            (current_zo - lw_zo)
            / lw_zo
            * 100
            if lw_zo > 0
            else None
        )

        # Both current channels have zero
        if current_sw == 0 and current_zo == 0:

            if lw_sw > 0 and lw_zo > 0:
                return "Swiggy & Zomato both zero vs LW"

            if lw_sw > 0:
                return "Swiggy zero vs LW"

            if lw_zo > 0:
                return "Zomato zero vs LW"

            return "No orders"

        # Only Swiggy has LW comparison
        if sw_pct is not None and zo_pct is None:

            if sw_pct < 0:
                return f"Swiggy worst ({sw_pct:.1f}% vs LW)"

            return f"Swiggy {sw_pct:+.1f}% vs LW"

        # Only Zomato has LW comparison
        if zo_pct is not None and sw_pct is None:

            if zo_pct < 0:
                return f"Zomato worst ({zo_pct:.1f}% vs LW)"

            return f"Zomato {zo_pct:+.1f}% vs LW"

        # Both available
        if sw_pct is not None and zo_pct is not None:

            if sw_pct < zo_pct:
                return f"Swiggy worst ({sw_pct:.1f}% vs LW)"

            if zo_pct < sw_pct:
                return f"Zomato worst ({zo_pct:.1f}% vs LW)"

            return "Swiggy & Zomato same vs LW"

        return "No LW comparison"

    base["Remarks"] = base.apply(
        channel_remark,
        axis=1,
    )

    # -----------------------------------------------------
    # SORT
    # -----------------------------------------------------

    base = base.sort_values(
        [
            "Region",
            "Store",
        ],
        kind="stable",
    )

    # -----------------------------------------------------
    # HTML ROWS
    # -----------------------------------------------------

    rows = []

    for _, r in base.iterrows():

        lw_pct = (
            f'{r["LW %"]:.1f}%'
            if pd.notna(r["LW %"])
            else "—"
        )

        # Highlight negative performance
        row_style = ""

        if (
            pd.notna(r["LW %"])
            and r["LW %"] < 0
        ):
            row_style = ' style="background:#fff2cc;"'

        cells = [

            escape(str(r["Region"])),

            escape(str(r["Store"])),

            f'{int(r["CURRENT_Swiggy_Frozen Bottle"])}',
            f'{int(r["CURRENT_Swiggy_Madno"])}',
            f'{int(r["CURRENT_Swiggy_Boba Bar"])}',

            f'{int(r["CURRENT_Zomato_Frozen Bottle"])}',
            f'{int(r["CURRENT_Zomato_Madno"])}',
            f'{int(r["CURRENT_Zomato_Boba Bar"])}',

            f'<b>{int(r["Swiggy Total"])}</b>',

            f'<b>{int(r["Zomato Total"])}</b>',

            f'<b>{int(r["Current Orders"])}</b>',

            f'<b>{int(r["LW Orders"])}</b>',

            lw_pct,

            escape(str(r["Remarks"])),
        ]

        html = (
            f'<tr{row_style}>'
            f'<td>{cells[0]}</td>'
            f'<td style="font-weight:bold;">{cells[1]}</td>'
            + ''.join(
                f'<td align="center">{c}</td>'
                for c in cells[2:13]
            )
            + f'<td>{cells[13]}</td>'
            '</tr>'
        )

        rows.append(html)

    header = ''.join(
        f'<th>{escape(c)}</th>'
        for c in columns
    )

    return (
        '<table border="1" cellpadding="5" cellspacing="0" '
        'style="border-collapse:collapse;width:100%;font-size:11px;">'
        f'<tr style="background:#d9eaf7;">'
        f'{header}'
        f'</tr>'
        + ''.join(rows)
        + '</table>'
    )

    # =========================================================
    # ORDER FLOW INSIGHTS
    # =========================================================
    
    def build_order_flow_insights(
        flow,
        current_hour,
    ):
    
        if flow is None or flow.empty:
    
            return (
                '<div style="background:#e2f0d9;'
                'padding:10px;margin:12px 0;">'
                '<b>No order-flow insight available.</b>'
                '</div>'
            )
    
        # -----------------------------------------------------
        # Last 3 completed hours
        # -----------------------------------------------------
    
        hours = [
            current_hour - timedelta(hours=2),
            current_hour - timedelta(hours=1),
            current_hour,
        ]
    
        recent = flow[
            flow["Hour"].isin(
                [
                    pd.Timestamp(h)
                    for h in hours
                ]
            )
        ].copy()
    
        # Store + Channel
        recent_group = (
            recent
            .groupby(
                [
                    "Region",
                    "Store Name",
                    "branchCode",
                    "Source",
                    "Hour",
                ],
                dropna=False,
            )["Orders"]
            .sum()
            .reset_index()
        )
    
        # -----------------------------------------------------
        # Create complete store/channel/hour universe
        # -----------------------------------------------------
    
        stores = (
            flow[
                [
                    "Region",
                    "Store Name",
                    "branchCode",
                ]
            ]
            .drop_duplicates()
        )
    
        combinations = []
    
        for _, store in stores.iterrows():
    
            for source in SOURCES:
    
                for hour in hours:
    
                    combinations.append(
                        {
                            "Region": store["Region"],
                            "Store Name": store["Store Name"],
                            "branchCode": store["branchCode"],
                            "Source": source,
                            "Hour": pd.Timestamp(hour),
                        }
                    )
    
        universe = pd.DataFrame(
            combinations
        )
    
        universe = universe.merge(
            recent_group,
            on=[
                "Region",
                "Store Name",
                "branchCode",
                "Source",
                "Hour",
            ],
            how="left",
        )
    
        universe["Orders"] = (
            pd.to_numeric(
                universe["Orders"],
                errors="coerce",
            )
            .fillna(0)
            .astype(int)
        )
    
        # -----------------------------------------------------
        # Last 2 / Last 3 hour zero checks
        # -----------------------------------------------------
    
        insight_rows = []
    
        for keys, group in universe.groupby(
            [
                "Region",
                "Store Name",
                "branchCode",
                "Source",
            ],
            dropna=False,
        ):
    
            region, store, branch, source = keys
    
            group = group.sort_values("Hour")
    
            values = group["Orders"].tolist()
    
            if len(values) != 3:
                continue
    
            current_orders = values[2]
    
            last_2 = values[1:]
    
            last_3 = values
    
            # Current hour zero
            if current_orders == 0:
    
                if all(x == 0 for x in last_3):
    
                    remark = (
                        f"{source}: "
                        "No orders for last 3 completed hours"
                    )
    
                elif all(x == 0 for x in last_2):
    
                    remark = (
                        f"{source}: "
                        "No orders for last 2 completed hours"
                    )
    
                else:
    
                    remark = (
                        f"{source}: "
                        "Zero orders in current hour"
                    )
    
                insight_rows.append(
                    (
                        region,
                        store,
                        source,
                        values[0],
                        values[1],
                        values[2],
                        remark,
                    )
                )
    
        if not insight_rows:
    
            return (
                '<div style="background:#e2f0d9;'
                'border:1px solid #70ad47;'
                'padding:10px;margin:12px 0;">'
                '<b style="color:#008000;">'
                '✅ No channel-level zero-order issues '
                'in the current / last 2 / last 3 hours.'
                '</b>'
                '</div>'
            )
    
        rows = []
    
        for (
            region,
            store,
            source,
            h3,
            h2,
            h1,
            remark,
        ) in insight_rows:
    
            rows.append(
                f'<tr>'
                f'<td>{escape(str(region))}</td>'
                f'<td style="font-weight:bold;">'
                f'{escape(str(store))}</td>'
                f'<td>{escape(str(source))}</td>'
                f'<td align="center">{h3}</td>'
                f'<td align="center">{h2}</td>'
                f'<td align="center">{h1}</td>'
                f'<td style="color:#c00000;font-weight:bold;">'
                f'{escape(remark)}</td>'
                f'</tr>'
            )
    
        return (
            '<div style="background:#fff2cc;'
            'border:2px solid #c00000;'
            'padding:10px;margin:12px 0;">'
            '<h3 style="color:#c00000;">'
            '🔎 Channel Order-Flow Insights'
            '</h3>'
    
            '<table border="1" cellpadding="6" cellspacing="0" '
            'style="border-collapse:collapse;width:100%;'
            'font-size:12px;">'
    
            '<tr style="background:#f4cccc;">'
            '<th>Region</th>'
            '<th>Store</th>'
            '<th>Channel</th>'
            '<th>3 Hrs Ago</th>'
            '<th>2 Hrs Ago</th>'
            '<th>Current Hr</th>'
            '<th>Remarks</th>'
            '</tr>'
    
            + ''.join(rows)
    
            + '</table>'
            '</div>'
        )

# =========================================================
# ORDER FLOW INSIGHTS
# =========================================================

def build_order_flow_insights(
    flow,
    current_hour,
):

    if flow is None or flow.empty:

        return (
            '<div style="background:#e2f0d9;'
            'border:1px solid #70ad47;'
            'padding:10px;margin:12px 0;">'
            '<b style="color:#008000;">'
            'No order-flow insight available.'
            '</b>'
            '</div>'
        )

    # -----------------------------------------------------
    # Last 3 completed hours
    # -----------------------------------------------------

    hours = [
        current_hour - timedelta(hours=2),
        current_hour - timedelta(hours=1),
        current_hour,
    ]

    recent = flow[
        flow["Hour"].isin(
            [
                pd.Timestamp(h)
                for h in hours
            ]
        )
    ].copy()

    # -----------------------------------------------------
    # Store + Channel + Hour
    # -----------------------------------------------------

    recent_group = (
        recent
        .groupby(
            [
                "Region",
                "Store Name",
                "branchCode",
                "Source",
                "Hour",
            ],
            dropna=False,
        )["Orders"]
        .sum()
        .reset_index()
    )

    # -----------------------------------------------------
    # Store universe
    # -----------------------------------------------------

    stores = (
        flow[
            [
                "Region",
                "Store Name",
                "branchCode",
            ]
        ]
        .drop_duplicates()
    )

    combinations = []

    for _, store in stores.iterrows():

        for source in [
            "Swiggy",
            "Zomato",
        ]:

            for hour in hours:

                combinations.append(
                    {
                        "Region":
                            store["Region"],

                        "Store Name":
                            store["Store Name"],

                        "branchCode":
                            store["branchCode"],

                        "Source":
                            source,

                        "Hour":
                            pd.Timestamp(hour),
                    }
                )

    universe = pd.DataFrame(
        combinations
    )

    universe = universe.merge(
        recent_group,
        on=[
            "Region",
            "Store Name",
            "branchCode",
            "Source",
            "Hour",
        ],
        how="left",
    )

    universe["Orders"] = (
        pd.to_numeric(
            universe["Orders"],
            errors="coerce",
        )
        .fillna(0)
        .astype(int)
    )

    # -----------------------------------------------------
    # Check last 3 hours
    # -----------------------------------------------------

    insight_rows = []

    for keys, group in universe.groupby(
        [
            "Region",
            "Store Name",
            "branchCode",
            "Source",
        ],
        dropna=False,
    ):

        region, store, branch, source = keys

        group = group.sort_values(
            "Hour"
        )

        values = (
            group["Orders"]
            .tolist()
        )

        if len(values) != 3:
            continue

        three_hours_ago = values[0]
        two_hours_ago = values[1]
        current_orders = values[2]

        # -------------------------------------------------
        # Only show channel if CURRENT hour = ZERO
        # -------------------------------------------------

        if current_orders != 0:
            continue

        # -------------------------------------------------
        # Last 3 hours zero
        # -------------------------------------------------

        if (
            three_hours_ago == 0
            and two_hours_ago == 0
            and current_orders == 0
        ):

            remark = (
                f"{source}: "
                "No orders for last 3 completed hours"
            )

        # -------------------------------------------------
        # Last 2 hours zero
        # -------------------------------------------------

        elif (
            two_hours_ago == 0
            and current_orders == 0
        ):

            remark = (
                f"{source}: "
                "No orders for last 2 completed hours"
            )

        # -------------------------------------------------
        # Current hour zero
        # -------------------------------------------------

        else:

            remark = (
                f"{source}: "
                "Zero orders in current hour"
            )

        insight_rows.append(
            (
                region,
                store,
                source,
                three_hours_ago,
                two_hours_ago,
                current_orders,
                remark,
            )
        )

    # -----------------------------------------------------
    # No issues
    # -----------------------------------------------------

    if not insight_rows:

        return (
            '<div style="background:#e2f0d9;'
            'border:1px solid #70ad47;'
            'padding:10px;margin:12px 0;">'
            '<b style="color:#008000;">'
            '✅ No channel-level zero-order issues '
            'in current / last 2 / last 3 hours.'
            '</b>'
            '</div>'
        )

    # -----------------------------------------------------
    # Build HTML
    # -----------------------------------------------------

    rows = []

    for (
        region,
        store,
        source,
        h3,
        h2,
        h1,
        remark,
    ) in insight_rows:

        rows.append(
            f'<tr>'
            f'<td>{escape(str(region))}</td>'
            f'<td style="font-weight:bold;">'
            f'{escape(str(store))}</td>'
            f'<td>{escape(str(source))}</td>'
            f'<td align="center">{h3}</td>'
            f'<td align="center">{h2}</td>'
            f'<td align="center">{h1}</td>'
            f'<td style="color:#c00000;'
            f'font-weight:bold;">'
            f'{escape(remark)}</td>'
            f'</tr>'
        )

    return (
        '<div style="background:#fff2cc;'
        'border:2px solid #c00000;'
        'padding:10px;margin:12px 0;">'

        '<h3 style="color:#c00000;">'
        '🔎 Channel Order-Flow Insights'
        '</h3>'

        '<table border="1" cellpadding="6" '
        'cellspacing="0" '
        'style="border-collapse:collapse;'
        'width:100%;font-size:12px;">'

        '<tr style="background:#f4cccc;">'
        '<th>Region</th>'
        '<th>Store</th>'
        '<th>Channel</th>'
        '<th>3 Hrs Ago</th>'
        '<th>2 Hrs Ago</th>'
        '<th>Current Hr</th>'
        '<th>Remarks</th>'
        '</tr>'

        + ''.join(rows)

        + '</table>'
        '</div>'
    )


# =========================================================
# EMAIL HTML
# =========================================================

def email_html(performance, flow, lw_flow, current_hour, previous_hour, business_start, report_end, lw_business_start, lw_report_end):
    total_alerts=int(performance["Alert"].sum()); total_stores=performance[["branchCode","Store Name"]].drop_duplicates().shape[0]
    current=performance.groupby(["Region","Store Name","branchCode"],as_index=False)["Current_Orders"].sum(); zero=current[current["Current_Orders"]==0].sort_values(["Region","Store Name"]); zero_count=len(zero)
    if zero_count:
        zr=''.join(f'<tr style="background:#fff2cc;"><td>{escape(str(r["Region"]))}</td><td style="font-weight:bold;color:#c00000;">{escape(str(r["Store Name"]))}</td><td align="center">0</td><td>No successful Swiggy/Zomato order in current hour</td></tr>' for _,r in zero.iterrows())
        zero_section=f'''<div style="background:#fce4d6;border:2px solid #c00000;padding:10px;margin:12px 0;"><h3 style="color:#c00000;">🚨 Stores With Zero Orders — Current Hour ({zero_count})</h3><table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;"><tr style="background:#f4cccc;"><th>Region</th><th>Store Name</th><th>Orders</th><th>Remarks</th></tr>{zr}</table></div>'''
    else: zero_section='<div style="background:#e2f0d9;border:1px solid #70ad47;padding:10px;margin:12px 0;"><b style="color:#008000;">✅ No stores with zero orders in the current hour</b></div>'
    alert_section=f'<div style="background:#fff2cc;padding:10px;margin:12px 0;"><b style="color:#c00000;">🚨 {total_alerts} alert row(s) detected in current hour</b></div>' if total_alerts else '<div style="background:#e2f0d9;padding:10px;margin:12px 0;"><b style="color:#008000;">✅ No order-flow alerts detected in the current hour</b></div>'
    h=flow.copy(); sw=int(h.loc[h["Source"]=="Swiggy","Orders"].sum()) if not h.empty else 0; zo=int(h.loc[h["Source"]=="Zomato","Orders"].sum()) if not h.empty else 0; total=sw+zo
    swp=sw/total*100 if total else 0; zop=zo/total*100 if total else 0
    # -----------------------------------------------------
    # LAST WEEK SUMMARY
    # -----------------------------------------------------
    
    if lw_flow is not None and not lw_flow.empty:
    
        lw_sw = int(
            lw_flow.loc[
                lw_flow["Source"] == "Swiggy",
                "Orders",
            ].sum()
        )
    
        lw_zo = int(
            lw_flow.loc[
                lw_flow["Source"] == "Zomato",
                "Orders",
            ].sum()
        )
    
    else:
    
        lw_sw = 0
        lw_zo = 0
    
    lw_total = lw_sw + lw_zo
    
    overall_lw_pct = (
        (total - lw_total)
        / lw_total
        * 100
        if lw_total > 0
        else None
    )
    
    overall_lw_display = (
        f"{overall_lw_pct:+.1f}%"
        if overall_lw_pct is not None
        else "—"
    )
    return f'''<html><body style="font-family:Arial;color:#222;"><h2>🚨 Hourly Order Flow Performance</h2><p><b>Business Date:</b> {business_start.strftime("%d-%b-%Y")}<br><b>Business Window:</b> 09:00 AM → 05:30 AM next day<br><b>Latest Reporting Point:</b> {report_end.strftime("%d-%b-%Y %I:%M %p")}<br><b>Total COCO Stores:</b> {total_stores}</p><div style="background:#f2f2f2;border:1px solid #ccc;padding:10px;margin:12px 0;"><b>Overall Order Summary — Business Day to Current</b><br><br>Swiggy: <b>{sw}</b> ({swp:.1f}%) &nbsp;&nbsp; Zomato: <b>{zo}</b> ({zop:.1f}%) &nbsp;&nbsp; Total: <b>{total}</b></div>{zero_section}{alert_section}{build_order_flow_insights(flow, current_hour)}<hr><h2 style="color:#1f4e78;">📊 Hourly Breakdown — Overall</h2><p style="font-size:12px;color:#555;">Overall hourly order flow only. Store-wise hourly rows are not shown.</p>{hourly_overall_table(flow)}<hr><h2 style="color:#1f4e78;">🏪 Store-Level Overall — Channel & Brand</h2><p style="font-size:12px;color:#555;">Current orders vs Last Week same business day and same reporting hour. LW comparison:{lw_business_start.strftime("%d-%b-%Y %I:%M %p")} → {lw_report_end.strftime("%d-%b-%Y %I:%M %p")}.</p>{store_overall_table(flow,lw_flow,performance)}<p style="font-size:11px;color:#666;">Source: Rista Sales Page + Google Sheet Help Sheet<br>COCO: Help Sheet Ownership = COCO<br>Business hour: 09:00 AM to next day 05:30 AM<br>Orders = unique invoice number.</p></body></html>'''

# =========================================================
# SEND EMAIL
# =========================================================

def send_mail(
    body,
    current_hour,
    alert_count,
):

    if not EMAIL_USER:
        raise RuntimeError("EMAIL_USER is missing.")

    if not EMAIL_PASSWORD:
        raise RuntimeError("EMAIL_PASSWORD is missing.")

    to = [
        "bangaloreterritorymanager1@frozenbottle.in",
        "bangaloreterritorymanager@frozenbottle.in",
        "manohar@frozenbottle.in",
        "tm.chennai@frozenbottle.in",
        "mis3@frozenbottle.in",
        "mis2@frozenbottle.in",
        "faraz@frozenbottle.in",
    ]
    
    cc = [
        "vivek@frozenbottle.in",
    ]

    if not to and not cc:

        raise ValueError(
            "EMAIL_TO / EMAIL_CC is empty"
        )

    msg = MIMEMultipart(
        "alternative"
    )

    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO

    if EMAIL_CC:

        msg["Cc"] = EMAIL_CC

    business_date = current_hour.strftime("%d-%b-%Y")
    business_date_id = current_hour.strftime("%Y%m%d")
    msg["Subject"] = f"Hourly Order Flow | {business_date}"
    daily_thread_id = f"<hourly-order-flow-{business_date_id}@frozenbottle.in>"
    unique_message_id = f"<hourly-order-flow-{business_date_id}-{current_hour.strftime('%H%M')}-{int(time.time())}@frozenbottle.in>"
    msg["Message-ID"] = unique_message_id
    msg["In-Reply-To"] = daily_thread_id
    msg["References"] = daily_thread_id

    msg.attach(
        MIMEText(
            body,
            "html",
        )
    )

    with smtplib.SMTP(
        EMAIL_HOST,
        EMAIL_PORT,
        timeout=60,
    ) as server:

        server.starttls()

        server.login(
            EMAIL_USER,
            EMAIL_PASSWORD,
        )

        server.sendmail(
            EMAIL_USER,
            to + cc,
            msg.as_string(),
        )


# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 70)
    print("HOURLY ORDER FLOW PERFORMANCE")
    print("=" * 70)

    now = datetime.now(
        IST
    )

    # -----------------------------------------------------
    # Completed reporting hour
    # -----------------------------------------------------

    current_hour = (
        now.replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        - timedelta(hours=1)
    )

    previous_hour = (
        current_hour
        - timedelta(hours=1)
    )

    print(
        f"Current time       : "
        f"{now:%d-%b-%Y %I:%M:%S %p}"
    )

    print(
        f"Reporting hour     : "
        f"{current_hour:%d-%b-%Y %I:%M %p}"
        f" - "
        f"{(current_hour + timedelta(hours=1)):%I:%M %p}"
    )

    print(
        f"Previous hour      : "
        f"{previous_hour:%d-%b-%Y %I:%M %p}"
    )

    print("=" * 70)
    print("CONFIGURATION CHECK")
    print("API_KEY exists         :", bool(API_KEY))
    print("SECRET_KEY exists      :", bool(SECRET_KEY))
    print("GOOGLE_CREDENTIALS set :", bool(os.getenv("GOOGLE_CREDENTIALS")))
    print("EMAIL_USER exists      :", bool(EMAIL_USER))
    print("EMAIL_PASSWORD exists  :", bool(EMAIL_PASSWORD))
    print("EMAIL_TO exists        :", bool(EMAIL_TO))
    print("Rista API Base         :", RISTA_API_BASE)
    print("Google Sheet ID        :", GOOGLE_SHEET_ID)
    print("=" * 70)

    # -----------------------------------------------------
    # Get COCO branches
    # -----------------------------------------------------

    branches_df = (
        get_coco_branches()
    )

    if branches_df.empty:

        print(
            "❌ No COCO stores available."
        )

        return

    # -----------------------------------------------------
    # Days required
    #
    # Current hour and previous hour
    # may cross midnight.
    # -----------------------------------------------------

    business_date = (
        now.date() - timedelta(days=1)
        if now.hour < 9
        else now.date()
    )
    
    business_start = (
        datetime.combine(
            business_date,
            datetime.min.time(),
        )
        .replace(
            hour=9,
            tzinfo=IST,
        )
    )
    
    business_end = (
        datetime.combine(
            business_date + timedelta(days=1),
            datetime.min.time(),
        )
        .replace(
            hour=5,
            minute=30,
            tzinfo=IST,
        )
    )
    
    report_end = min(
        current_hour + timedelta(hours=1),
        business_end,
    )
    
    # -----------------------------------------------------
    # LAST WEEK SAME BUSINESS DAY
    # -----------------------------------------------------
    
    lw_business_date = (
        business_date
        - timedelta(days=7)
    )
    
    lw_business_start = (
        datetime.combine(
            lw_business_date,
            datetime.min.time(),
        )
        .replace(
            hour=9,
            tzinfo=IST,
        )
    )
    
    lw_business_end = (
        datetime.combine(
            lw_business_date + timedelta(days=1),
            datetime.min.time(),
        )
        .replace(
            hour=5,
            minute=30,
            tzinfo=IST,
        )
    )
    
    # Same elapsed reporting point as current day
    elapsed = report_end - business_start
    
    lw_report_end = min(
        lw_business_start + elapsed,
        lw_business_end,
    )
    
    # -----------------------------------------------------
    # FETCH BOTH CURRENT + LAST WEEK DATES
    # -----------------------------------------------------
    
    days = sorted(
        {
            business_start.date(),
            business_end.date(),
            lw_business_start.date(),
            lw_business_end.date(),
        }
    )

    # -----------------------------------------------------
    # Fetch sales
    # -----------------------------------------------------

    all_rows = []

    branch_count = len(
        branches_df
    )

    print()
    print(
        f"Fetching Sales Page for "
        f"{branch_count} COCO stores..."
    )

    for index, row in branches_df.iterrows():

        branch_code = (
            row["branchCode"]
        )

        store_name = (
            row["Store Name"]
        )

        print(
            f"[{index + 1}/{branch_count}] "
            f"{store_name} | "
            f"{branch_code}"
        )

        for day in days:

            try:

                sales = sales_page(
                    branch_code,
                    day.strftime(
                        "%Y-%m-%d"
                    ),
                )

                all_rows.extend(
                    sales
                )

                print(
                    f"    {day}: "
                    f"{len(sales)} sales"
                )

            except Exception as exc:

                print(
                    f"    ⚠️ "
                    f"{day}: "
                    f"{exc}"
                )

    print()
    print(
        f"Total raw Sales Page rows: "
        f"{len(all_rows)}"
    )

    # -----------------------------------------------------
    # Prepare sales
    # -----------------------------------------------------

    sales_df = prepare_sales(
        all_rows,
        branches_df,
    )
    
    # -----------------------------------------------------
    # CURRENT PERIOD
    # 09:00 AM → current reporting point
    # -----------------------------------------------------
    
    if not sales_df.empty:
    
        current_sales_df = sales_df[
            (sales_df["EventTime"] >= pd.Timestamp(business_start))
            &
            (sales_df["EventTime"] <= pd.Timestamp(report_end))
        ].copy()
    
    else:
    
        current_sales_df = pd.DataFrame()
    
    
    # -----------------------------------------------------
    # LAST WEEK SAME DAY / SAME HOUR
    # -----------------------------------------------------
    
    if not sales_df.empty:
    
        lw_sales_df = sales_df[
            (sales_df["EventTime"] >= pd.Timestamp(lw_business_start))
            &
            (sales_df["EventTime"] <= pd.Timestamp(lw_report_end))
        ].copy()
    
    else:
    
        lw_sales_df = pd.DataFrame()
    
    
    print()
    print(
        f"Current-period rows : {len(current_sales_df)}"
    )
    
    print(
        f"LW-period rows      : {len(lw_sales_df)}"
    )

    if sales_df.empty:

        print(
            "⚠️ No Swiggy/Zomato "
            "online sales found."
        )

    # -----------------------------------------------------
    # CURRENT FLOW
    # -----------------------------------------------------
    
    flow = build_hourly_flow(
        current_sales_df
    )
    
    # -----------------------------------------------------
    # LAST WEEK FLOW
    # -----------------------------------------------------
    
    lw_flow = build_hourly_flow(
        lw_sales_df
    )

    # -----------------------------------------------------
    # Build complete store performance
    # -----------------------------------------------------

    performance = (
        build_store_performance(
            branches_df,
            flow,
            current_hour,
            previous_hour,
        )
    )

    # -----------------------------------------------------
    # Summary
    # -----------------------------------------------------

    alert_count = int(
        performance["Alert"].sum()
    )

    store_count = (
        performance[
            [
                "branchCode",
                "Store Name",
            ]
        ]
        .drop_duplicates()
        .shape[0]
    )

    print()
    print("=" * 70)
    print("PERFORMANCE SUMMARY")
    print("=" * 70)

    print(
        f"COCO Stores : {store_count}"
    )

    print(
        f"Alerts      : {alert_count}"
    )

    print(
        f"Normal      : {store_count - performance[performance['Alert']].drop_duplicates('branchCode').shape[0]}"
    )

    print()

    # -----------------------------------------------------
    # Print alert rows
    # -----------------------------------------------------

    alerts = performance[
        performance["Alert"]
    ].copy()

    if alerts.empty:

        print(
            "✅ NO ORDER FLOW ALERTS"
        )

    else:

        print(
            "🚨 ALERT STORES"
        )

        print(
            alerts[
                [
                    "Region",
                    "Store Name",
                    "Brand",
                    "Source",
                    "Previous_Orders",
                    "Current_Orders",
                    "Current_Problem",
                    "Remarks",
                ]
            ]
            .sort_values(
                [
                    "Region",
                    "Store Name",
                    "Source",
                    "Brand",
                ]
            )
            .to_string(
                index=False
            )
        )

    # -----------------------------------------------------
    # Send email EVERY hour
    #
    # Even when there are no alerts.
    # -----------------------------------------------------

    body = email_html(
        performance, flow, lw_flow, current_hour, previous_hour, business_start, report_end, lw_business_start, lw_report_end
    )

    send_mail(
        body,
        current_hour,
        alert_count,
    )

    print()
    print(
        "📩 Hourly performance email sent."
    )

    print("=" * 70)


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
