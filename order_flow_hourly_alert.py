# =========================================================
# HOURLY ORDER FLOW PERFORMANCE
# RISTA ONLY
#
# COCO stores from branchLabels
# Region from taxArea
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
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import jwt
import pandas as pd
import requests


# =========================================================
# CONFIGURATION
# =========================================================

IST = ZoneInfo("Asia/Kolkata")

RISTA_API_BASE = "https://api.ristaapps.com/v1"

API_KEY = os.getenv("API_KEY", "").strip()
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO", "")


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

def get_token():

    now = datetime.now(IST)

    payload = {
        "iss": API_KEY,
        "iat": int(now.timestamp()),
    }

    return jwt.encode(
        payload,
        SECRET_KEY,
        algorithm="HS256",
    )


# =========================================================
# RISTA REQUEST
# =========================================================

def headers():
    if not API_KEY:
        raise RuntimeError(
            "API_KEY is missing. Please add API_KEY to GitHub Actions Secrets."
        )

    if not SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY is missing. Please add SECRET_KEY to GitHub Actions Secrets."
        )

    return {
        "x-api-key": API_KEY,
        "x-api-token": get_token(),
        "Content-Type": "application/json",
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

    response.raise_for_status()

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Rista API returned non-JSON response for {url}: "
            f"{response.text[:500]}"
        ) from exc


# =========================================================
# BRANCH API
#
# IMPORTANT:
# COCO is determined ONLY from branchLabels.
#
# Example:
# branchLabels = "FOFO,ROKerala"
#               -> NOT COCO
#
# branchLabels = "COCO,ROKarnataka"
#               -> COCO
# =========================================================

def get_coco_branches():

    print("=" * 70)
    print("FETCHING RISTA BRANCHES")
    print("=" * 70)

    # -----------------------------------------------------
    # Rista Branch API
    # -----------------------------------------------------

    response = get("/branch/list")

    # -----------------------------------------------------
    # Handle API response
    #
    # get() should return the JSON dictionary
    # -----------------------------------------------------

    if isinstance(response, dict):

        data = response.get("data", [])

    else:

        data = []

    if not isinstance(data, list):

        data = []

    print(
        f"Branch API returned {len(data)} records"
    )

    # -----------------------------------------------------
    # Print sample response for verification
    # -----------------------------------------------------

    if data:

        print()
        print("Sample branch API response:")
        print(data[0])
        print()

    coco = []

    # =====================================================
    # PROCESS BRANCHES
    # =====================================================

    for row in data:

        if not isinstance(row, dict):

            continue

        # -------------------------------------------------
        # ACTIVE CHECK
        # -------------------------------------------------

        status = norm(
            row.get("status")
        )

        active_value = row.get(
            "active",
            row.get("isActive", None)
        )

        # Explicit inactive flag
        if active_value is False:

            continue

        # Status check
        if status and status not in {
            "active",
            "open",
        }:

            continue

        # -------------------------------------------------
        # BRANCH LABELS
        # -------------------------------------------------

        raw_branch_labels = (
            row.get("branchLabels")
            or ""
        )

        branch_labels = norm(
            raw_branch_labels
        )

        # -------------------------------------------------
        # COCO ONLY
        #
        # Valid:
        # COCO
        # COCO,ROKA
        # ROKA,COCO
        # COCO,ROKerala
        #
        # Invalid:
        # COCOABC
        # ABC_COCO
        # FOCOCO
        # -------------------------------------------------

        if not re.search(
            r"(^|[,;\s])coco([,;\s]|$)",
            branch_labels,
            flags=re.IGNORECASE,
        ):

            continue

        # -------------------------------------------------
        # BRANCH CODE
        # -------------------------------------------------

        branch_code = str(
            row.get("branchCode")
            or row.get("code")
            or ""
        ).strip()

        if not branch_code:

            continue

        # -------------------------------------------------
        # STORE NAME
        # -------------------------------------------------

        store_name = str(
            row.get("branchName")
            or row.get("name")
            or branch_code
        ).strip()

        # -------------------------------------------------
        # REGION
        #
        # Primary:
        # taxArea
        #
        # Fallback:
        # address.state
        # -------------------------------------------------

        address = row.get(
            "address",
            {}
        )

        if not isinstance(address, dict):

            address = {}

        region_value = (
            row.get("taxArea")
            or address.get("state")
            or ""
        )

        region = normalize_region(
            region_value
        )

        # -------------------------------------------------
        # CHANNELS
        # -------------------------------------------------

        branch_channels = []

        channels = row.get(
            "channels",
            []
        )

        if isinstance(channels, list):

            for channel in channels:

                if isinstance(channel, dict):

                    channel_name = str(
                        channel.get("name")
                        or ""
                    ).strip()

                else:

                    channel_name = str(
                        channel or ""
                    ).strip()

                if channel_name:

                    branch_channels.append(
                        channel_name
                    )

        # -------------------------------------------------
        # ADD COCO BRANCH
        # -------------------------------------------------

        coco.append(
            {
                "branchCode": branch_code,
                "Store Name": store_name,
                "Region": region,
                "branchLabels": str(
                    raw_branch_labels
                ).strip(),
                "Channels": branch_channels,
            }
        )

    # =====================================================
    # CREATE DATAFRAME
    # =====================================================

    branches_df = pd.DataFrame(
        coco
    )

    if branches_df.empty:

        print()
        print(
            "❌ No COCO branches found."
        )

        return branches_df

    # =====================================================
    # REGION FILTER
    # =====================================================

    branches_df = branches_df[
        branches_df["Region"].isin(
            REGIONS
        )
    ].copy()

    # =====================================================
    # REMOVE DUPLICATES
    # =====================================================

    branches_df = (
        branches_df
        .drop_duplicates(
            subset=["branchCode"]
        )
        .sort_values(
            by=[
                "Region",
                "Store Name",
            ]
        )
        .reset_index(
            drop=True
        )
    )

    # =====================================================
    # SUMMARY
    # =====================================================

    print(
        f"Active COCO branches found: "
        f"{len(branches_df)}"
    )

    print()

    print(
        "COCO stores by region:"
    )

    region_counts = (
        branches_df
        .groupby("Region")
        .size()
        .reindex(
            REGIONS,
            fill_value=0
        )
    )

    print(
        region_counts.to_string()
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
        ],

        "branchName": [
            "branchName",
        ],

        "invoiceNumber": [
            "invoiceNumber",
            "invoiceNo",
        ],

        "invoiceDate": [
            "invoiceDate",
            "createdDate",
            "modifiedDate",
        ],

        "brandName": [
            "brandName",
        ],

        "channel": [
            "channel",
        ],

        "fulfillmentStatus": [
            "fulfillmentStatus",
            "status",
        ],

        "cancelReason": [
            "cancelReason",
            "cancellationReason",
            "voidReason",
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
        .apply(
            normalize_brand
        )
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
# EMAIL HTML ESCAPE
# =========================================================

def esc(value):

    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# =========================================================
# REGION TABLE
# =========================================================

def region_table(
    data,
):

    if data.empty:

        return ""

    stores = {}

    for _, row in data.iterrows():

        store = row["Store Name"]

        if store not in stores:

            stores[store] = {

                "Swiggy": {
                    "Frozen Bottle": 0,
                    "Madno": 0,
                    "Boba Bar": 0,
                },

                "Zomato": {
                    "Frozen Bottle": 0,
                    "Madno": 0,
                    "Boba Bar": 0,
                },

                "status": "Normal",

                "remarks": [],
            }

        source_name = row["Source"]
        brand_name = row["Brand"]

        stores[store][
            source_name
        ][
            brand_name
        ] = int(
            row["Current_Orders"]
        )

        if row["Status"] == "Alert":

            stores[store][
                "status"
            ] = "Alert"

            remark = str(
                row["Remarks"]
                or ""
            ).strip()

            if (
                remark
                and remark
                not in stores[store]["remarks"]
            ):

                stores[store]["remarks"].append(
                    f"{source_name} "
                    f"{brand_name}: "
                    f"{remark}"
                )

    # -----------------------------------------------------
    # Alert first
    # -----------------------------------------------------

    sorted_stores = sorted(
        stores.items(),
        key=lambda item: (
            0
            if item[1]["status"]
            == "Alert"
            else 1,
            item[0],
        ),
    )

    rows = []

    for store, values in sorted_stores:

        is_alert = (
            values["status"]
            == "Alert"
        )

        if is_alert:

            bg = "#fff2cc"
            store_style = (
                "color:#c00000;"
                "font-weight:bold;"
            )
            status_style = (
                "color:#c00000;"
                "font-weight:bold;"
            )

            status = "🚨 ALERT"

        else:

            bg = "#ffffff"
            store_style = ""
            status_style = (
                "color:#008000;"
                "font-weight:bold;"
            )

            status = "Normal"

        remarks = (
            "<br>".join(
                esc(x)
                for x in values["remarks"]
            )
            if values["remarks"]
            else "Order flow normal"
        )

        rows.append(
            f"""
            <tr style="background:{bg};">

                <td style="{store_style}">
                    {esc(store)}
                </td>

                <td align="center">
                    {values["Swiggy"]["Frozen Bottle"]}
                </td>

                <td align="center">
                    {values["Swiggy"]["Madno"]}
                </td>

                <td align="center">
                    {values["Swiggy"]["Boba Bar"]}
                </td>

                <td align="center">
                    {values["Zomato"]["Frozen Bottle"]}
                </td>

                <td align="center">
                    {values["Zomato"]["Madno"]}
                </td>

                <td align="center">
                    {values["Zomato"]["Boba Bar"]}
                </td>

                <td style="{status_style}">
                    {status}
                </td>

                <td>
                    {remarks}
                </td>

            </tr>
            """
        )

    return f"""
    <table
        border="1"
        cellpadding="6"
        cellspacing="0"
        style="
            border-collapse:collapse;
            width:100%;
            font-family:Arial;
            font-size:12px;
        "
    >

        <tr style="background:#d9eaf7;">

            <th rowspan="2">
                Store Name
            </th>

            <th colspan="3">
                Swiggy Orders
            </th>

            <th colspan="3">
                Zomato Orders
            </th>

            <th rowspan="2">
                Status
            </th>

            <th rowspan="2">
                Remarks
            </th>

        </tr>

        <tr style="background:#eaf3f8;">

            <th>Frozen Bottle</th>
            <th>Madno</th>
            <th>Boba Bar</th>

            <th>Frozen Bottle</th>
            <th>Madno</th>
            <th>Boba Bar</th>

        </tr>

        {"".join(rows)}

    </table>
    """


# =========================================================
# EMAIL HTML
# =========================================================

def email_html(
    performance,
    current_hour,
    previous_hour,
):

    total_alerts = int(
        performance["Alert"].sum()
    )

    total_stores = (
        performance[
            [
                "branchCode",
                "Store Name",
            ]
        ]
        .drop_duplicates()
        .shape[0]
    )

    sections = []

    for region in REGIONS:

        region_data = performance[
            performance["Region"]
            == region
        ].copy()

        if region_data.empty:

            continue

        alert_count = int(
            region_data["Alert"].sum()
        )

        store_count = (
            region_data[
                [
                    "branchCode",
                    "Store Name",
                ]
            ]
            .drop_duplicates()
            .shape[0]
        )

        sections.append(
            f"""
            <h3 style="
                margin-top:24px;
                margin-bottom:8px;
            ">
                {esc(region)}
                —
                {store_count} Stores
                |
                {alert_count} Alert(s)
            </h3>

            {region_table(region_data)}
            """
        )

    alert_summary = ""

    if total_alerts > 0:

        alert_summary = f"""
        <div style="
            background:#fff2cc;
            border:1px solid #f4b183;
            padding:10px;
            margin:12px 0;
        ">
            <b style="color:#c00000;">
                🚨 {total_alerts} alert(s) detected
            </b>
            <br>
            Stores with alert are shown first
            within each region.
        </div>
        """

    else:

        alert_summary = """
        <div style="
            background:#e2f0d9;
            border:1px solid #70ad47;
            padding:10px;
            margin:12px 0;
        ">
            <b style="color:#008000;">
                ✅ No order-flow alerts detected
            </b>
        </div>
        """

    return f"""
    <html>

    <body
        style="
            font-family:Arial;
            color:#222;
        "
    >

        <h2>
            🚨 Hourly Order Flow Performance
        </h2>

        <p>

            <b>Date:</b>
            {current_hour.strftime("%d-%b-%Y")}

            <br>

            <b>Reporting Hour:</b>
            {current_hour.strftime("%I:%M %p")}
            -
            {(current_hour + timedelta(hours=1)).strftime("%I:%M %p")}

            <br>

            <b>Previous Hour:</b>
            {previous_hour.strftime("%I:%M %p")}

            <br>

            <b>Total COCO Stores:</b>
            {total_stores}

        </p>

        {alert_summary}

        <div style="
            background:#f2f2f2;
            padding:10px;
            margin-bottom:15px;
        ">

            <b>Alert Rule:</b>

            Current hour successful orders = 0

            <br>

            AND previous hour successful orders &gt; 0

            <br>

            OR current hour has
            Cancel / Reject / Void order.

            <br><br>

            <b>Order Definition:</b>
            Unique invoice number

            <br>

            <b>Channel:</b>
            In-Store = Offline;
            Swiggy/Zomato = Online

        </div>

        {"".join(sections)}

        <br>

        <p style="
            font-size:11px;
            color:#666;
        ">

            Source:
            Rista Branch API + Rista Sales Page

            <br>

            COCO classification:
            Rista branchLabels

            <br>

            Region:
            Rista taxArea

        </p>

    </body>

    </html>
    """


# =========================================================
# SEND EMAIL
# =========================================================

def send_mail(
    body,
    current_hour,
    alert_count,
):

    to = [
        x.strip()
        for x in EMAIL_TO.split(",")
        if x.strip()
    ]

    if not to:
        raise ValueError("EMAIL_TO is empty")

    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO

    subject_prefix = "🚨 ALERT" if alert_count > 0 else "✅ NORMAL"

    msg["Subject"] = (
        f"{subject_prefix} | "
        f"Hourly Order Flow | "
        f"{current_hour.strftime('%d-%b-%Y %I:%M %p')} | "
        f"{alert_count} Alert(s)"
    )

    msg.attach(MIMEText(body, "html"))

    with smtplib.SMTP(
        EMAIL_HOST,
        EMAIL_PORT,
        timeout=60,
    ) as server:

        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_USER, to, msg.as_string())


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

    days = sorted(
        {
            current_hour.date(),
            previous_hour.date(),
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

    if sales_df.empty:

        print(
            "⚠️ No Swiggy/Zomato "
            "online sales found."
        )

    # -----------------------------------------------------
    # Build hourly flow
    # -----------------------------------------------------

    flow = build_hourly_flow(
        sales_df
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
        performance,
        current_hour,
        previous_hour,
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
