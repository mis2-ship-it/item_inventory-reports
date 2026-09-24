import os
import re
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import jwt


# =========================================================
# CONFIG
# =========================================================

IST = ZoneInfo("Asia/Kolkata")

API_BASE = os.getenv(
    "RISTA_API_BASE",
    "https://api.ristaapps.com/v1",
)

API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO", "")
EMAIL_CC = os.getenv("EMAIL_CC", "")

OUTLET_MASTER_FILE = os.getenv(
    "OUTLET_MASTER_FILE",
    "outlet_master.csv",
)


# =========================================================
# BUSINESS CONFIG
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

CHANNELS = [
    "Swiggy",
    "Zomato",
]


# =========================================================
# AUTHORIZED CHANNEL → SOURCE MAP
# =========================================================

CHANNEL_SOURCE_MAP = {

    # Swiggy
    "swiggy frozen bottle": "Swiggy",
    "swiggy boba bar": "Swiggy",
    "swiggy madno": "Swiggy",
    "swiggy lubov": "Swiggy",

    # Zomato
    "zomato boba bar": "Zomato",
    "zomato frozen bottle": "Zomato",
    "zomato madno": "Zomato",
    "zomato lubov": "Zomato",
}


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


def normalize_region(value):
    value = str(value or "").strip()

    if not value:
        return ""

    mapping = {
        "KA": "KA",
        "KARNATAKA": "KA",

        "MH": "MH",
        "MAHARASHTRA": "MH",

        "TN": "TN",
        "TAMIL NADU": "TN",

        "KERALA": "Kerela",
        "KERELA": "Kerela",
    }

    return mapping.get(
        value.upper(),
        value,
    )


# =========================================================
# BRAND
# =========================================================

def brand(value):

    text = norm(value)

    if "frozen bottle" in text:
        return "Frozen Bottle"

    if "madno" in text:
        return "Madno"

    if "boba bar" in text:
        return "Boba Bar"

    return str(value or "").strip()


# =========================================================
# SOURCE
# =========================================================

def source(value):

    return CHANNEL_SOURCE_MAP.get(
        norm(value),
        "",
    )


# =========================================================
# JWT TOKEN
# =========================================================

def token():

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
# RISTA GET
# =========================================================

def get(endpoint, params=None):

    url = (
        f"{API_BASE.rstrip('/')}/"
        f"{endpoint.lstrip('/')}"
    )

    headers = {
        "x-api-key": API_KEY,
        "x-api-token": token(),
        "Content-Type": "application/json",
    }

    response = requests.get(
        url,
        headers=headers,
        params=params or {},
        timeout=60,
    )

    response.raise_for_status()

    return response.json()


# =========================================================
# EXTRACT LIST FROM RISTA RESPONSE
#
# Rista may return:
#
# 1. [...]
#
# OR
#
# 2. {"data": [...]}
#
# =========================================================

def response_list(response):

    if isinstance(response, list):
        return response

    if isinstance(response, dict):

        data = response.get("data", [])

        if isinstance(data, list):
            return data

    return []


# =========================================================
# BRANCH LIST
# =========================================================

def branches():

    response = get("/branch/list")

    data = response_list(response)

    print(
        f"Branch API returned {len(data)} records"
    )

    output = []

    for row in data:

        if not isinstance(row, dict):
            continue

        active = row.get(
            "active",
            row.get(
                "isActive",
                True,
            ),
        )

        if active is False:
            continue

        branch_code = str(
            row.get("branchCode")
            or row.get("code")
            or ""
        ).strip()

        if not branch_code:
            continue

        branch_name = str(
            row.get("branchName")
            or row.get("name")
            or branch_code
        ).strip()

        output.append(
            {
                "branchCode": branch_code,
                "branchName": branch_name,
            }
        )

    print(
        f"Active branches found: {len(output)}"
    )

    return output


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

        response = get(
            "/sales/page",
            {
                "branch": branch_code,
                "day": day,
                "page": page,
                "limit": 5000,
            },
        )

        data = response_list(response)

        rows.extend(data)

        if len(data) < 5000:
            break

        page += 1

    return rows


# =========================================================
# OUTLET MASTER
#
# Expected:
#
# branchCode
# Store Name
# Region
#
# Ownership is optional.
# If Ownership exists, COCO is used.
# =========================================================

def outlet_master():

    if (
        not OUTLET_MASTER_FILE
        or not os.path.exists(
            OUTLET_MASTER_FILE
        )
    ):

        print(
            "⚠️ Outlet master file not found."
        )

        return pd.DataFrame(
            columns=[
                "branchCode",
                "Store Name",
                "Region",
                "Ownership",
            ]
        )

    df = pd.read_csv(
        OUTLET_MASTER_FILE
    )

    required = [
        "branchCode",
        "Store Name",
        "Region",
    ]

    missing = [
        c
        for c in required
        if c not in df.columns
    ]

    if missing:

        raise ValueError(
            "Outlet master missing columns: "
            + ", ".join(missing)
        )

    if "Ownership" not in df.columns:
        df["Ownership"] = ""

    df["branchCode"] = (
        df["branchCode"]
        .astype(str)
        .str.strip()
    )

    df["Store Name"] = (
        df["Store Name"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    df["Region"] = (
        df["Region"]
        .apply(normalize_region)
    )

    df["Ownership"] = (
        df["Ownership"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    return (
        df[
            [
                "branchCode",
                "Store Name",
                "Region",
                "Ownership",
            ]
        ]
        .drop_duplicates(
            "branchCode"
        )
    )


# =========================================================
# PREPARE SALES DATA
# =========================================================

def prepare(
    rows,
    master,
):

    if not rows:
        return pd.DataFrame()

    df = pd.json_normalize(rows)

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
            "date",
        ],

        "brandName": [
            "brandName",
            "brand",
        ],

        "channel": [
            "channel",
        ],

        "fulfillmentStatus": [
            "fulfillmentStatus",
            "status",
            "orderStatus",
        ],

        "cancelReason": [
            "cancelReason",
            "cancellationReason",
            "voidReason",
            "reason",
            "Cancel Reason",
        ],
    }

    # -----------------------------------------------------
    # CREATE STANDARD COLUMNS
    # -----------------------------------------------------

    for target, names in aliases.items():

        if target not in df.columns:

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
    # BRANCH CODE
    # -----------------------------------------------------

    df["branchCode"] = (
        df["branchCode"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # -----------------------------------------------------
    # BRAND
    # -----------------------------------------------------

    df["Brand"] = (
        df["brandName"]
        .apply(brand)
    )

    # -----------------------------------------------------
    # SOURCE
    # -----------------------------------------------------

    df["Source"] = (
        df["channel"]
        .apply(source)
    )

    # -----------------------------------------------------
    # EVENT TIME
    # -----------------------------------------------------

    df["EventTime"] = pd.to_datetime(
        df["invoiceDate"],
        errors="coerce",
    )

    def convert_time(value):

        if pd.isna(value):
            return pd.NaT

        try:

            if value.tzinfo is None:

                return value.tz_localize(
                    IST
                )

            return value.tz_convert(
                IST
            )

        except Exception:

            return pd.NaT

    df["EventTime"] = (
        df["EventTime"]
        .apply(convert_time)
    )

    # -----------------------------------------------------
    # HOUR
    # -----------------------------------------------------

    df["Hour"] = (
        df["EventTime"]
        .dt.floor("h")
    )

    # -----------------------------------------------------
    # CANCEL REASON
    # -----------------------------------------------------

    df["Cancel Reason"] = (
        df["cancelReason"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    df["Fulfillment Status"] = (
        df["fulfillmentStatus"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # -----------------------------------------------------
    # PROBLEM FLAG
    #
    # Cancel / Reject / Void
    # Store Closed
    # Store Busy
    # Out of Stock
    # Payment Issue
    # -----------------------------------------------------

    status_problem = (
        df["Fulfillment Status"]
        .apply(norm)
        .str.contains(
            r"cancel|reject|void",
            regex=True,
            na=False,
        )
    )

    reason_problem = (
        df["Cancel Reason"]
        .apply(norm)
        .str.contains(
            r"cancel|reject|void|"
            r"store closed|store busy|"
            r"out of stock|payment issue",
            regex=True,
            na=False,
        )
    )

    df["Problem"] = (
        status_problem
        | reason_problem
    )

    # -----------------------------------------------------
    # MERGE STORE MASTER
    # -----------------------------------------------------

    master = master.copy()

    df = df.merge(
        master,
        on="branchCode",
        how="left",
        suffixes=(
            "",
            "_master",
        ),
    )

    # -----------------------------------------------------
    # STORE NAME
    # -----------------------------------------------------

    df["Store Name"] = (
        df["Store Name"]
        .fillna(
            df["branchName"]
        )
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # -----------------------------------------------------
    # REGION
    # -----------------------------------------------------

    df["Region"] = (
        df["Region"]
        .fillna("")
        .apply(normalize_region)
    )

    # -----------------------------------------------------
    # OWNERSHIP
    # -----------------------------------------------------

    if "Ownership" not in df.columns:
        df["Ownership"] = ""

    df["Ownership"] = (
        df["Ownership"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    return df


# =========================================================
# BUILD ORDER FLOW
# =========================================================

def build_flow(df):

    if df.empty:
        return pd.DataFrame()

    # -----------------------------------------------------
    # ONLY REQUIRED CHANNELS / BRANDS / REGIONS
    # -----------------------------------------------------

    df = df[
        df["Source"].isin(CHANNELS)
        & df["Brand"].isin(BRANDS)
        & df["Region"].isin(REGIONS)
    ].copy()

    if df.empty:
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
    # SUCCESSFUL ORDERS
    #
    # UNIQUE INVOICE NUMBERS
    # -----------------------------------------------------

    normal = df[
        ~df["Problem"]
    ].copy()

    normal["invoiceNumber"] = (
        normal["invoiceNumber"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    normal = normal[
        normal["invoiceNumber"] != ""
    ]

    normal_orders = (
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
    # PROBLEM ORDERS
    # -----------------------------------------------------

    bad = df[
        df["Problem"]
    ].copy()

    bad["invoiceNumber"] = (
        bad["invoiceNumber"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    bad = bad[
        bad["invoiceNumber"] != ""
    ]

    problem_orders = (
        bad
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
    # MERGE
    # -----------------------------------------------------

    flow = normal_orders.merge(
        problem_orders,
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
# STORE UNIVERSE
#
# Creates every:
#
# Store × Source × Brand
#
# combination so that zero-order stores/brands are also
# visible in the hourly report.
# =========================================================

def create_store_universe(
    branches_list,
    master,
):

    rows = []

    master_lookup = {}

    if not master.empty:

        for _, row in master.iterrows():

            code = str(
                row["branchCode"]
            ).strip()

            master_lookup[code] = {
                "Store Name": str(
                    row["Store Name"]
                    or ""
                ).strip(),

                "Region": normalize_region(
                    row["Region"]
                ),

                "Ownership": str(
                    row.get(
                        "Ownership",
                        "",
                    )
                    or ""
                ).strip(),
            }

    for branch in branches_list:

        code = str(
            branch["branchCode"]
        ).strip()

        master_row = master_lookup.get(
            code,
            {},
        )

        store_name = (
            master_row.get(
                "Store Name"
            )
            or branch.get(
                "branchName",
                code,
            )
        )

        region = normalize_region(
            master_row.get(
                "Region",
                "",
            )
        )

        ownership = master_row.get(
            "Ownership",
            "",
        )

        # -------------------------------------------------
        # ONLY REGIONS REQUIRED
        # -------------------------------------------------

        if region not in REGIONS:
            continue

        # -------------------------------------------------
        # IF OWNERSHIP COLUMN EXISTS,
        # USE COCO ONLY
        # -------------------------------------------------

        if ownership:

            if norm(ownership) != "coco":
                continue

        for source_name in CHANNELS:

            for brand_name in BRANDS:

                rows.append(
                    {
                        "Region": region,
                        "Store Name": store_name,
                        "branchCode": code,
                        "Brand": brand_name,
                        "Source": source_name,
                    }
                )

    return pd.DataFrame(rows)


# =========================================================
# BUILD HOURLY PERFORMANCE
# =========================================================

def build_hourly_performance(
    universe,
    flow,
    current_hour,
    previous_hour,
):

    if universe.empty:
        return pd.DataFrame()

    performance = universe.copy()

    # -----------------------------------------------------
    # CURRENT HOUR
    # -----------------------------------------------------

    current = flow[
        flow["Hour"] == current_hour
    ].copy()

    if not current.empty:

        current = (
            current[
                [
                    "Region",
                    "Store Name",
                    "branchCode",
                    "Brand",
                    "Source",
                    "Orders",
                    "Problem_Orders",
                    "Cancel_Reasons",
                ]
            ]
            .groupby(
                [
                    "Region",
                    "Store Name",
                    "branchCode",
                    "Brand",
                    "Source",
                ],
                as_index=False,
            )
            .agg(
                Current_Orders=(
                    "Orders",
                    "sum",
                ),

                Current_Problem=(
                    "Problem_Orders",
                    "sum",
                ),

                Current_Reasons=(
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
        )

    else:

        current = pd.DataFrame(
            columns=[
                "Region",
                "Store Name",
                "branchCode",
                "Brand",
                "Source",
                "Current_Orders",
                "Current_Problem",
                "Current_Reasons",
            ]
        )

    # -----------------------------------------------------
    # PREVIOUS HOUR
    # -----------------------------------------------------

    previous = flow[
        flow["Hour"] == previous_hour
    ].copy()

    if not previous.empty:

        previous = (
            previous[
                [
                    "Region",
                    "Store Name",
                    "branchCode",
                    "Brand",
                    "Source",
                    "Orders",
                ]
            ]
            .groupby(
                [
                    "Region",
                    "Store Name",
                    "branchCode",
                    "Brand",
                    "Source",
                ],
                as_index=False,
            )["Orders"]
            .sum()
            .rename(
                columns={
                    "Orders":
                    "Previous_Orders"
                }
            )
        )

    else:

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

    # -----------------------------------------------------
    # MERGE CURRENT + PREVIOUS
    # -----------------------------------------------------

    keys = [
        "Region",
        "Store Name",
        "branchCode",
        "Brand",
        "Source",
    ]

    performance = (
        performance
        .merge(
            current,
            on=keys,
            how="left",
        )
        .merge(
            previous,
            on=keys,
            how="left",
        )
    )

    # -----------------------------------------------------
    # NUMERIC CLEANUP
    # -----------------------------------------------------

    for column in [
        "Current_Orders",
        "Current_Problem",
        "Previous_Orders",
    ]:

        performance[column] = (
            pd.to_numeric(
                performance[column],
                errors="coerce",
            )
            .fillna(0)
            .astype(int)
        )

    performance["Current_Reasons"] = (
        performance["Current_Reasons"]
        .fillna("")
        .astype(str)
    )

    # =====================================================
    # ALERT LOGIC
    # =====================================================

    # Alert when:
    #
    # 1. Current hour = 0
    # AND previous hour > 0
    #
    # OR
    #
    # 2. Current hour has cancellation/rejection/void
    #
    performance["Alert"] = (
        (
            (
                performance[
                    "Current_Orders"
                ] == 0
            )
            &
            (
                performance[
                    "Previous_Orders"
                ] > 0
            )
        )
        |
        (
            performance[
                "Current_Problem"
            ] > 0
        )
    )

    # -----------------------------------------------------
    # REMARKS
    # -----------------------------------------------------

    def build_remark(row):

        source_name = row["Source"]
        brand_name = row["Brand"]

        current_orders = row[
            "Current_Orders"
        ]

        previous_orders = row[
            "Previous_Orders"
        ]

        problem = row[
            "Current_Problem"
        ]

        reasons = row[
            "Current_Reasons"
        ]

        if row["Alert"]:

            parts = []

            if (
                current_orders == 0
                and previous_orders > 0
            ):

                parts.append(
                    f"{source_name} "
                    f"{brand_name} "
                    f"no order in current hour"
                )

            if problem > 0:

                parts.append(
                    "Cancel/Reject/Void"
                )

                if reasons:

                    parts.append(
                        reasons
                    )

            if not parts:

                parts.append(
                    "Order flow alert"
                )

            return " | ".join(parts)

        return "Order flow normal"

    performance["Remarks"] = (
        performance.apply(
            build_remark,
            axis=1,
        )
    )

    performance["Status"] = (
        performance["Alert"]
        .map(
            {
                True: "Alert",
                False: "Normal",
            }
        )
    )

    # =====================================================
    # STORE-LEVEL STATUS
    #
    # If ANY brand/channel combination has an alert,
    # the complete store is marked Alert.
    # =====================================================

    store_keys = [
        "Region",
        "Store Name",
        "branchCode",
    ]

    store_status = (
        performance
        .groupby(
            store_keys,
            as_index=False,
        )["Alert"]
        .max()
        .rename(
            columns={
                "Alert":
                "Store_Alert"
            }
        )
    )

    performance = performance.merge(
        store_status,
        on=store_keys,
        how="left",
    )

    performance["Store Status"] = (
        performance["Store_Alert"]
        .map(
            {
                True: "Alert",
                False: "Normal",
            }
        )
    )

    return performance


# =========================================================
# STORE SUMMARY
#
# Converts 6 brand/source rows into one store row.
# =========================================================

def build_store_summary(
    performance,
):

    if performance.empty:
        return pd.DataFrame()

    rows = []

    group_columns = [
        "Region",
        "Store Name",
        "branchCode",
    ]

    for (
        region,
        store_name,
        branch_code,
    ), group in performance.groupby(
        group_columns,
        sort=False,
    ):

        values = {
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
        }

        remarks = []

        for _, row in group.iterrows():

            src = row["Source"]
            br = row["Brand"]

            if (
                src in values
                and br in values[src]
            ):

                values[src][br] = int(
                    row["Current_Orders"]
                )

            if row["Alert"]:

                remark = str(
                    row["Remarks"]
                ).strip()

                if remark and (
                    remark not in remarks
                ):

                    remarks.append(
                        remark
                    )

        store_alert = bool(
            group["Alert"].any()
        )

        total_sw = sum(
            values["Swiggy"].values()
        )

        total_zo = sum(
            values["Zomato"].values()
        )

        rows.append(
            {
                "Region": region,
                "Store Name": store_name,
                "branchCode": branch_code,

                "Swiggy FB":
                    values["Swiggy"][
                        "Frozen Bottle"
                    ],

                "Swiggy Madno":
                    values["Swiggy"][
                        "Madno"
                    ],

                "Swiggy Boba":
                    values["Swiggy"][
                        "Boba Bar"
                    ],

                "Zomato FB":
                    values["Zomato"][
                        "Frozen Bottle"
                    ],

                "Zomato Madno":
                    values["Zomato"][
                        "Madno"
                    ],

                "Zomato Boba":
                    values["Zomato"][
                        "Boba Bar"
                    ],

                "Swiggy Total":
                    total_sw,

                "Zomato Total":
                    total_zo,

                "Total Orders":
                    total_sw + total_zo,

                "Status":
                    "Alert"
                    if store_alert
                    else "Normal",

                "Remarks":
                    " | ".join(
                        remarks
                    )
                    if remarks
                    else "Order flow normal",
            }
        )

    result = pd.DataFrame(rows)

    # -----------------------------------------------------
    # ALERT FIRST
    # -----------------------------------------------------

    result["_alert_sort"] = (
        result["Status"]
        .map(
            {
                "Alert": 0,
                "Normal": 1,
            }
        )
        .fillna(1)
    )

    result = result.sort_values(
        [
            "_alert_sort",
            "Region",
            "Store Name",
        ]
    )

    return result.drop(
        columns="_alert_sort"
    )


# =========================================================
# HTML ESCAPE
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
# HTML TABLE
# =========================================================

def region_table(
    region_df,
):

    if region_df.empty:
        return ""

    rows = []

    for _, row in region_df.iterrows():

        alert = (
            row["Status"] == "Alert"
        )

        if alert:

            row_style = (
                "background:#fff2cc;"
            )

            store_style = (
                "color:#c00000;"
                "font-weight:bold;"
            )

            status_style = (
                "color:#c00000;"
                "font-weight:bold;"
            )

        else:

            row_style = (
                "background:#ffffff;"
            )

            store_style = (
                "color:#222222;"
                "font-weight:normal;"
            )

            status_style = (
                "color:#008000;"
                "font-weight:bold;"
            )

        rows.append(
            f"""
            <tr style="{row_style}">
                <td style="{store_style}">
                    {esc(row["Store Name"])}
                </td>

                <td>
                    {esc(row["Swiggy FB"])}
                </td>

                <td>
                    {esc(row["Swiggy Madno"])}
                </td>

                <td>
                    {esc(row["Swiggy Boba"])}
                </td>

                <td>
                    {esc(row["Zomato FB"])}
                </td>

                <td>
                    {esc(row["Zomato Madno"])}
                </td>

                <td>
                    {esc(row["Zomato Boba"])}
                </td>

                <td>
                    <b>{esc(row["Total Orders"])}</b>
                </td>

                <td style="{status_style}">
                    {esc(row["Status"])}
                </td>

                <td style="font-size:11px;">
                    {esc(row["Remarks"])}
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

        <tr
            style="
                background:#d9eaf7;
                font-weight:bold;
            "
        >
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
                Total
            </th>

            <th rowspan="2">
                Status
            </th>

            <th rowspan="2">
                Remarks
            </th>
        </tr>

        <tr
            style="
                background:#eaf3f8;
                font-weight:bold;
            "
        >

            <th>
                Frozen Bottle
            </th>

            <th>
                Madno
            </th>

            <th>
                Boba Bar
            </th>

            <th>
                Frozen Bottle
            </th>

            <th>
                Madno
            </th>

            <th>
                Boba Bar
            </th>

        </tr>

        {''.join(rows)}

    </table>
    """


# =========================================================
# EMAIL HTML
# =========================================================

def email_html(
    summary,
    hour,
):

    if summary.empty:

        return """
        <html>
        <body>
            <h2>Hourly Order Flow Report</h2>
            <p>No store data available.</p>
        </body>
        </html>
        """

    alert_count = int(
        (
            summary["Status"]
            == "Alert"
        ).sum()
    )

    normal_count = int(
        (
            summary["Status"]
            == "Normal"
        ).sum()
    )

    total_orders = int(
        summary[
            "Total Orders"
        ].sum()
    )

    swiggy_orders = int(
        summary[
            "Swiggy Total"
        ].sum()
    )

    zomato_orders = int(
        summary[
            "Zomato Total"
        ].sum()
    )

    region_sections = []

    for region in REGIONS:

        region_df = summary[
            summary["Region"]
            == region
        ].copy()

        if region_df.empty:
            continue

        region_sections.append(
            f"""
            <h3
                style="
                    margin-top:24px;
                    color:#1f4e78;
                "
            >
                Region: {esc(region)}
            </h3>

            {region_table(region_df)}
            """
        )

    return f"""
    <html>

    <body
        style="
            font-family:Arial;
            color:#222;
        "
    >

        <h2
            style="
                color:#1f4e78;
            "
        >
            📊 Hourly Order Flow Performance
        </h2>

        <p>
            <b>Date:</b>
            {hour.strftime("%d-%b-%Y")}
            <br>

            <b>Completed Hour:</b>
            {hour.strftime("%I:%M %p")}
            -
            {(hour + timedelta(hours=1)).strftime("%I:%M %p")}
        </p>

        <!-- SUMMARY -->

        <table
            border="1"
            cellpadding="7"
            cellspacing="0"
            style="
                border-collapse:collapse;
                font-family:Arial;
                font-size:13px;
                margin-bottom:20px;
            "
        >

            <tr
                style="
                    background:#d9eaf7;
                    font-weight:bold;
                "
            >
                <th>
                    Metric
                </th>

                <th>
                    Count
                </th>
            </tr>

            <tr>
                <td>
                    Total Stores
                </td>

                <td>
                    {len(summary)}
                </td>
            </tr>

            <tr
                style="
                    background:#fff2cc;
                    color:#c00000;
                    font-weight:bold;
                "
            >
                <td>
                    🔴 Alert Stores
                </td>

                <td>
                    {alert_count}
                </td>
            </tr>

            <tr
                style="
                    color:#008000;
                    font-weight:bold;
                "
            >
                <td>
                    🟢 Normal Stores
                </td>

                <td>
                    {normal_count}
                </td>
            </tr>

            <tr>
                <td>
                    Swiggy Orders
                </td>

                <td>
                    {swiggy_orders}
                </td>
            </tr>

            <tr>
                <td>
                    Zomato Orders
                </td>

                <td>
                    {zomato_orders}
                </td>
            </tr>

            <tr
                style="
                    background:#e2f0d9;
                    font-weight:bold;
                "
            >
                <td>
                    Total Orders
                </td>

                <td>
                    {total_orders}
                </td>
            </tr>

        </table>

        <h3>
            🔴 Alert stores are shown first.
        </h3>

        <p
            style="
                font-size:12px;
                color:#666;
            "
        >
            Every active COCO store is included in the report.
            Alert stores are identified when the current completed
            hour has no successful order after having order flow
            in the previous hour, or when cancellation/rejection/
            void activity is recorded.
        </p>

        {''.join(region_sections)}

        <br>

        <p
            style="
                font-size:11px;
                color:#777;
            "
        >
            Order count is based on unique invoice numbers.
            This report is an order-flow monitoring alert and
            does not by itself prove that a channel is offline.
        </p>

    </body>

    </html>
    """


# =========================================================
# SEND EMAIL
# =========================================================

def send_mail(
    body,
    hour,
    alert_count,
):

    to = [
        x.strip()
        for x in EMAIL_TO.split(",")
        if x.strip()
    ]

    cc = [
        x.strip()
        for x in EMAIL_CC.split(",")
        if x.strip()
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

    msg["Subject"] = (
        "📊 Hourly Order Flow Performance"
        f" | {hour.strftime('%d-%b-%Y %I:%M %p')}"
        f" | 🔴 {alert_count} Alert"
    )

    msg.attach(
        MIMEText(
            body,
            "html",
        )
    )

    with smtplib.SMTP(
        "smtp.gmail.com",
        587,
        timeout=60,
    ) as smtp:

        smtp.starttls()

        smtp.login(
            EMAIL_USER,
            EMAIL_PASS,
        )

        smtp.sendmail(
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

    now = datetime.now(IST)

    # -----------------------------------------------------
    # LAST COMPLETED HOUR
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
        f"{now.strftime('%d-%b-%Y %I:%M:%S %p')}"
    )

    print(
        f"Reporting hour     : "
        f"{current_hour.strftime('%d-%b-%Y %I:%M %p')}"
        f" - "
        f"{(current_hour + timedelta(hours=1)).strftime('%I:%M %p')}"
    )

    print(
        f"Previous hour      : "
        f"{previous_hour.strftime('%d-%b-%Y %I:%M %p')}"
    )

    # -----------------------------------------------------
    # GET BRANCHES
    # -----------------------------------------------------

    branch_list = branches()

    if not branch_list:

        print(
            "❌ No active branches found."
        )

        return

    # -----------------------------------------------------
    # OUTLET MASTER
    # -----------------------------------------------------

    master = outlet_master()

    print(
        f"Outlet master rows : {len(master)}"
    )

    # -----------------------------------------------------
    # CREATE STORE UNIVERSE
    # -----------------------------------------------------

    universe = create_store_universe(
        branch_list,
        master,
    )

    if universe.empty:

        print(
            "❌ No stores available "
            "for configured regions."
        )

        return

    store_count = (
        universe[
            [
                "branchCode",
            ]
        ]
        .drop_duplicates()
        .shape[0]
    )

    print(
        f"Stores monitored    : {store_count}"
    )

    # -----------------------------------------------------
    # FETCH LAST TWO HOURS' DATES
    # -----------------------------------------------------

    days = sorted(
        {
            current_hour.date(),
            previous_hour.date(),
        }
    )

    # -----------------------------------------------------
    # FETCH SALES
    # -----------------------------------------------------

    all_rows = []

    for branch in branch_list:

        branch_code = (
            branch["branchCode"]
        )

        # Only fetch stores that are actually in
        # the monitored universe.

        if not universe[
            universe["branchCode"]
            == branch_code
        ].empty:

            for day in days:

                try:

                    rows = sales_page(
                        branch_code,
                        day.strftime(
                            "%Y-%m-%d"
                        ),
                    )

                    all_rows.extend(rows)

                    print(
                        f"✓ {branch_code} "
                        f"{day} "
                        f"{len(rows)} rows"
                    )

                except Exception as exc:

                    print(
                        f"⚠️ {branch_code} "
                        f"{day}: {exc}"
                    )

    print(
        f"Total sales records : "
        f"{len(all_rows)}"
    )

    # -----------------------------------------------------
    # PREPARE
    # -----------------------------------------------------

    sales_df = prepare(
        all_rows,
        master,
    )

    if sales_df.empty:

        print(
            "⚠️ No Sales Page data."
        )

        # Still send an hourly report showing
        # all monitored stores with zero orders.

        summary = universe.copy()

        summary["Swiggy FB"] = 0
        summary["Swiggy Madno"] = 0
        summary["Swiggy Boba"] = 0

        summary["Zomato FB"] = 0
        summary["Zomato Madno"] = 0
        summary["Zomato Boba"] = 0

        summary["Swiggy Total"] = 0
        summary["Zomato Total"] = 0
        summary["Total Orders"] = 0

        summary = (
            summary[
                [
                    "Region",
                    "Store Name",
                    "branchCode",
                ]
            ]
            .drop_duplicates()
        )

        summary["Status"] = "Normal"
        summary["Remarks"] = (
            "No Sales Page records returned"
        )

        alert_count = 0

        send_mail(
            email_html(
                summary,
                current_hour,
            ),
            current_hour,
            alert_count,
        )

        print(
            "📩 Hourly report sent."
        )

        return

    # -----------------------------------------------------
    # BUILD FLOW
    # -----------------------------------------------------

    flow = build_flow(
        sales_df
    )

    # -----------------------------------------------------
    # BUILD HOURLY PERFORMANCE
    # -----------------------------------------------------

    performance = (
        build_hourly_performance(
            universe,
            flow,
            pd.Timestamp(
                current_hour
            ),
            pd.Timestamp(
                previous_hour
            ),
        )
    )

    # -----------------------------------------------------
    # BUILD STORE SUMMARY
    # -----------------------------------------------------

    summary = build_store_summary(
        performance
    )

    if summary.empty:

        print(
            "⚠️ No performance data."
        )

        return

    # -----------------------------------------------------
    # COUNTS
    # -----------------------------------------------------

    alert_count = int(
        (
            summary["Status"]
            == "Alert"
        ).sum()
    )

    normal_count = int(
        (
            summary["Status"]
            == "Normal"
        ).sum()
    )

    total_orders = int(
        summary[
            "Total Orders"
        ].sum()
    )

    # -----------------------------------------------------
    # CONSOLE OUTPUT
    # -----------------------------------------------------

    print("")
    print("=" * 70)
    print(
        f"🔴 Alert Stores : "
        f"{alert_count}"
    )

    print(
        f"🟢 Normal Stores: "
        f"{normal_count}"
    )

    print(
        f"📦 Total Orders : "
        f"{total_orders}"
    )

    print("=" * 70)

    print(
        summary[
            [
                "Region",
                "Store Name",
                "Swiggy FB",
                "Swiggy Madno",
                "Swiggy Boba",
                "Zomato FB",
                "Zomato Madno",
                "Zomato Boba",
                "Total Orders",
                "Status",
                "Remarks",
            ]
        ].to_string(
            index=False
        )
    )

    # -----------------------------------------------------
    # SEND EMAIL
    # -----------------------------------------------------

    body = email_html(
        summary,
        current_hour,
    )

    send_mail(
        body,
        current_hour,
        alert_count,
    )

    print("")
    print(
        "📩 Hourly performance email sent successfully."
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
