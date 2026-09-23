# =========================================================
# ITEM LEVEL SALES DASHBOARD
# =========================================================
# Final dashboard set:
# 1. Source Summary
# 2. Brand Source Analysis
# 3. Overall Category Dashboard
# 4. Category Dashboard
# 5. Brand X Category Dashboard (Orders)
# 6. Brand X Region X Category Dashboard (Orders)
# 7. Swiggy Discount Dashboard by Brand
# 8. Zomato Discount Dashboard by Brand
#
# Source rules:
# - Toing is identified from Rista sales-page tags and shown separately.
# - Other sources continue to use Help Sheet Source mapping.
# - Ownly is included everywhere except Region dashboards.
# - Discount % is ALWAYS item_netDiscountAmount / item_grossAmount * 100.
# - Orders are ALWAYS unique invoiceNumber.
# =========================================================

import os
import re
import ast
import json
import time
import smtplib
import requests
import jwt
import gspread
import numpy as np
import pandas as pd

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.oauth2.service_account import Credentials


print("🚀 Item Level Dashboard Started")

# =========================================================
# CONFIG
# =========================================================

API_KEY = os.environ["API_KEY"]
SECRET_KEY = os.environ["SECRET_KEY"]

SPREADSHEET_ID = "1ldgnNMdeubDx_ImtCC1uCD7FGz8gt_edmWEkmO_xRNk"

SALES_URL = "https://api.ristaapps.com/v1/sales/page"

BRANDS = {
    "Frozen Bottle": "Frozen Bottle",
    "Boba Bar": "Boba Bar",
    "Madno": "Madno",
    "Lubov": "Lubov- Patisserie",
}

SOURCES = [
    "In Store",
    "Swiggy",
    "Zomato",
    "Toing",
    "Ownly",
]

REGIONS = [
    "KA",
    "MH",
    "TN",
    "Kerela",
]


# =========================================================
# RISTA AUTH
# =========================================================

def get_token():
    payload = {
        "iss": API_KEY,
        "iat": int(time.time()),
    }

    return jwt.encode(
        payload,
        SECRET_KEY,
        algorithm="HS256",
    )


def api_headers():
    return {
        "x-api-key": API_KEY,
        "x-api-token": get_token(),
        "content-type": "application/json",
    }


# =========================================================
# GOOGLE AUTH
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

print("✅ Connected Google Sheet")


# =========================================================
# TIME / BUSINESS DATE
# =========================================================

IST = ZoneInfo("Asia/Kolkata")
now = datetime.now(IST)

# Business day changes at 09:00 AM for this dashboard.
if now.hour < 9:
    business_date = now.date() - timedelta(days=1)
else:
    business_date = now.date()

current_start = datetime.combine(
    business_date,
    datetime.min.time(),
).replace(
    hour=9,
    minute=0,
    second=0,
    microsecond=0,
    tzinfo=IST,
)

current_end = now.replace(
    minute=59,
    second=59,
    microsecond=0,
)

lw_start = current_start - timedelta(days=7)
lw_end = current_end - timedelta(days=7)

print("🕒 Current Time:", now)
print("📅 Business Date:", business_date)
print("🟢 Current Window:", current_start, "to", current_end)
print("🟡 LW Window:", lw_start, "to", lw_end)


# =========================================================
# HELP SHEET
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
        "Source",
    ],
)

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

help_df["Source"] = (
    help_df["Source"]
    .astype(str)
    .str.strip()
)

help_df["Region"] = (
    help_df["Region"]
    .astype(str)
    .str.strip()
)

# COCO only
help_df = help_df[
    help_df["Ownership"] == "COCO"
].copy()

branches = (
    help_df["branchCode"]
    .replace("", np.nan)
    .dropna()
    .unique()
    .tolist()
)

print("✅ Help Sheet Loaded")
print("📋 Help Columns:", help_df.columns.tolist())
print("🏪 COCO Branch Count:", len(branches))


# =========================================================
# ITEM GROUP SHEET
# =========================================================

item_ws = spreadsheet.worksheet("Item Group")
item_data = item_ws.get_all_values()

if not item_data:
    raise RuntimeError("Item Group sheet is empty")

item_rows = []

for row in item_data[1:]:
    row = list(row[:5])

    if len(row) < 5:
        row.extend([""] * (5 - len(row)))

    item_rows.append(row)

item_df = pd.DataFrame(
    item_rows,
    columns=[
        "Item Name",
        "Item Group Name",
        "Variant",
        "Product Mix",
        "Category Group",
    ],
)

item_df["Item Name"] = (
    item_df["Item Name"]
    .astype(str)
    .str.strip()
    .str.upper()
)

for col in [
    "Item Group Name",
    "Product Mix",
    "Category Group",
]:
    item_df[col] = (
        item_df[col]
        .fillna("Others")
        .astype(str)
        .str.strip()
    )

print("✅ Item Group Loaded:", len(item_df))


# =========================================================
# RISTA SALES FETCH
# =========================================================

def fetch_sales_window(start_datetime, end_datetime, dataset):

    all_sales = []

    print("\n" + "=" * 60)
    print(f"📦 FETCHING {dataset}")
    print("Window:", start_datetime, "to", end_datetime)
    print("=" * 60)

    for branch in branches:

        params = {
            "branch": branch,
            "day": start_datetime.strftime("%Y-%m-%d"),
            "page": 1,
            "limit": 5000,
        }

        try:
            response = requests.get(
                SALES_URL,
                headers=api_headers(),
                params=params,
                timeout=180,
            )

            print(
                f"{dataset} | Branch {branch} | HTTP {response.status_code}"
            )

            if response.status_code != 200:
                print("Response:", response.text[:500])
                continue

            payload = response.json()
            data = payload.get("data", [])

            if not data:
                continue

            df = pd.json_normalize(data)

            if "branchCode" not in df.columns:
                if "branch" in df.columns:
                    df["branchCode"] = df["branch"]
                else:
                    df["branchCode"] = branch

            df["DATASET"] = dataset
            all_sales.append(df)

        except Exception as exc:
            print(
                f"❌ {dataset} | Branch {branch} | {exc}"
            )

    if not all_sales:
        print(f"⚠ No {dataset} data returned")
        return pd.DataFrame()

    final_df = pd.concat(
        all_sales,
        ignore_index=True,
    )

    print(f"✅ {dataset} API rows: {len(final_df)}")
    return final_df


current_raw = fetch_sales_window(
    current_start,
    current_end,
    "CURRENT",
)

lw_raw = fetch_sales_window(
    lw_start,
    lw_end,
    "LW",
)


# =========================================================
# FLATTEN ITEM DATA
# =========================================================

def flatten_items(df):

    if df is None or df.empty:
        return pd.DataFrame()

    if "items" not in df.columns:
        print("⚠ items column missing")
        return pd.DataFrame()

    df = df.explode("items")
    df = df[df["items"].notna()].copy()

    if df.empty:
        return pd.DataFrame()

    item_part = pd.json_normalize(df["items"])
    item_part.columns = [
        f"item_{col}" for col in item_part.columns
    ]

    df = df.drop(columns=["items"]).reset_index(drop=True)
    item_part = item_part.reset_index(drop=True)

    df = pd.concat(
        [df, item_part],
        axis=1,
    )

    return df


current_sales = flatten_items(current_raw)
lw_sales = flatten_items(lw_raw)

print("CURRENT ITEM ROWS:", len(current_sales))
print("LW ITEM ROWS:", len(lw_sales))


# =========================================================
# SAFE COLUMNS
# =========================================================

def ensure_columns(df):

    defaults = {
        "branchCode": "",
        "invoiceNumber": "",
        "brandName": "",
        "channel": "",
        "status": "",
        "createdDate": "",
        "tags": "",
        "item_shortName": "",
        "item_quantity": 0,
        "item_netAmount": 0,
        "item_netDiscountAmount": 0,
        "item_grossAmount": 0,
        "item_discounts": "",
        "discounts": "",
    }

    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default

    return df


current_sales = ensure_columns(current_sales)
lw_sales = ensure_columns(lw_sales)


# =========================================================
# BASIC CLEANING
# =========================================================

def clean_numeric(df, columns):
    for col in columns:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        ).fillna(0)
    return df


numeric_columns = [
    "item_quantity",
    "item_netAmount",
    "item_netDiscountAmount",
    "item_grossAmount",
]

current_sales = clean_numeric(current_sales, numeric_columns)
lw_sales = clean_numeric(lw_sales, numeric_columns)

for df in [current_sales, lw_sales]:
    df["branchCode"] = (
        df["branchCode"]
        .astype(str)
        .str.strip()
    )

    df["invoiceNumber"] = (
        df["invoiceNumber"]
        .astype(str)
        .str.strip()
    )

    df["item_shortName"] = (
        df["item_shortName"]
        .astype(str)
        .str.strip()
        .str.upper()
    )


# =========================================================
# STATUS FILTER
# =========================================================

for df in [current_sales, lw_sales]:
    df["status"] = (
        df["status"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

current_sales = current_sales[
    current_sales["status"] == "CLOSED"
].copy()

lw_sales = lw_sales[
    lw_sales["status"] == "CLOSED"
].copy()


# =========================================================
# ORDER TIME
# =========================================================

for df in [current_sales, lw_sales]:
    df["Order Time"] = pd.to_datetime(
        df["createdDate"],
        utc=True,
        errors="coerce",
    ).dt.tz_convert(IST)

current_sales = current_sales[
    (current_sales["Order Time"] >= current_start)
    & (current_sales["Order Time"] <= current_end)
].copy()

lw_sales = lw_sales[
    (lw_sales["Order Time"] >= lw_start)
    & (lw_sales["Order Time"] <= lw_end)
].copy()

print("✅ CLOSED CURRENT ROWS:", len(current_sales))
print("✅ CLOSED LW ROWS:", len(lw_sales))


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
        "Ownership",
    ]
].drop_duplicates("branchCode")

current_sales = current_sales.merge(
    help_merge,
    on="branchCode",
    how="left",
)

lw_sales = lw_sales.merge(
    help_merge,
    on="branchCode",
    how="left",
)


# =========================================================
# ITEM GROUP MAPPING
# =========================================================

item_merge = item_df[
    [
        "Item Name",
        "Item Group Name",
        "Product Mix",
        "Category Group",
    ]
].drop_duplicates("Item Name")

current_sales = current_sales.merge(
    item_merge,
    left_on="item_shortName",
    right_on="Item Name",
    how="left",
)

lw_sales = lw_sales.merge(
    item_merge,
    left_on="item_shortName",
    right_on="Item Name",
    how="left",
)

for df in [current_sales, lw_sales]:
    for col in [
        "Item Group Name",
        "Product Mix",
        "Category Group",
    ]:
        df[col] = (
            df[col]
            .fillna("Others")
            .astype(str)
            .str.strip()
        )


# =========================================================
# REMOVE ADDONS
# =========================================================

current_sales = current_sales[
    current_sales["Product Mix"].str.upper() != "ADDONS"
].copy()

lw_sales = lw_sales[
    lw_sales["Product Mix"].str.upper() != "ADDONS"
].copy()


# =========================================================
# SOURCE / TOING LOGIC
# =========================================================

def tags_text(value):
    """Convert Rista tags in list/dict/string form into plain text."""

    if value is None:
        return ""

    if isinstance(value, (list, tuple, set)):
        return " ".join(tags_text(x) for x in value)

    if isinstance(value, dict):
        return " ".join(
            tags_text(k) + " " + tags_text(v)
            for k, v in value.items()
        )

    text = str(value)

    # Try a Python/JSON representation when possible.
    if text.startswith("[") or text.startswith("{"):
        try:
            parsed = ast.literal_eval(text)
            return tags_text(parsed)
        except Exception:
            pass

    return text


def has_toing_tag(value):
    text = tags_text(value).lower()

    return bool(
        re.search(
            r"(?<![a-z0-9])toing(?![a-z0-9])",
            text,
        )
    )


def normalize_source(value):
    text = str(value).strip()
    lower = text.lower()

    mapping = {
        "in store": "In Store",
        "instore": "In Store",
        "in-store": "In Store",
        "swiggy": "Swiggy",
        "zomato": "Zomato",
        "ownly": "Ownly",
        "website": "Ownly",
    }

    return mapping.get(lower, text if text else "Others")


def create_source_group(df):
    df = df.copy()

    mapped_source = df["Source"].apply(normalize_source)

    toing_mask = df["tags"].apply(has_toing_tag)

    df["Source Group"] = mapped_source
    df.loc[toing_mask, "Source Group"] = "Toing"

    return df


current_sales = create_source_group(current_sales)
lw_sales = create_source_group(lw_sales)

print("\nCURRENT SOURCE GROUP")
print(current_sales["Source Group"].value_counts(dropna=False))

print("\nLW SOURCE GROUP")
print(lw_sales["Source Group"].value_counts(dropna=False))


# =========================================================
# CHANNEL / BRAND CLEANING
# =========================================================

for df in [current_sales, lw_sales]:
    df["brandName"] = (
        df["brandName"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    df["Region"] = (
        df["Region"]
        .fillna("")
        .astype(str)
        .str.strip()
    )


# =========================================================
# DISCOUNT CODE EXTRACTION
# =========================================================

def parse_discount_objects(value):

    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        return [value]

    text = str(value).strip()

    if not text or text.lower() in {"nan", "none", "null", "[]", "{}"}:
        return []

    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
    except Exception:
        pass

    return []


def extract_swiggy_codes(value):
    codes = []

    for obj in parse_discount_objects(value):
        name = str(obj.get("name", "")).strip()

        if not name:
            continue

        match = re.search(
            r"Restaurant Discount\s*\((.*?)\)",
            name,
            flags=re.IGNORECASE,
        )

        if match:
            codes.append(match.group(1).strip())
        else:
            codes.append(name)

    if not codes:
        return "No Offer"

    return " | ".join(dict.fromkeys(codes))


def extract_zomato_codes(value):
    codes = []

    for obj in parse_discount_objects(value):
        name = str(obj.get("name", "")).strip()

        if not name:
            continue

        match = re.search(
            r"Merchant Voucher Code\s*\((.*?)\)",
            name,
            flags=re.IGNORECASE,
        )

        if match:
            codes.append(match.group(1).strip())
        else:
            codes.append(name)

    if not codes:
        return "No Offer"

    return " | ".join(dict.fromkeys(codes))


for df in [current_sales, lw_sales]:
    df["Swiggy Code"] = df["item_discounts"].apply(extract_swiggy_codes)
    df["Zomato Code"] = df["discounts"].apply(extract_zomato_codes)


# =========================================================
# METRIC HELPERS
# =========================================================

def unique_orders(df):
    if df.empty:
        return 0

    valid = df["invoiceNumber"].astype(str).str.strip()
    valid = valid[valid != ""]
    return int(valid.nunique())


def safe_sum(df, column):
    if df.empty or column not in df.columns:
        return 0.0
    return float(
        pd.to_numeric(
            df[column],
            errors="coerce",
        ).fillna(0).sum()
    )


def discount_pct(df):
    """
    IMPORTANT:
    Discount % is always calculated from item-level columns.
    No invoice-level dedupe is used for the amount calculation.
    """

    gross = safe_sum(df, "item_grossAmount")
    discount = abs(safe_sum(df, "item_netDiscountAmount"))

    if gross <= 0:
        return 0.0

    return round((discount / gross) * 100, 2)


def growth_pct(today_value, lw_value):
    if lw_value > 0:
        return round(((today_value / lw_value) - 1) * 100, 2)
    return 0.0


def brand_filter(df, brand_filter):
    return df[
        df["brandName"]
        .astype(str)
        .str.strip()
        .str.upper()
        == brand_filter.upper()
    ].copy()


def source_filter(df, source):
    return df[
        df["Source Group"]
        .astype(str)
        .str.strip()
        .str.upper()
        == source.upper()
    ].copy()


# =========================================================
# 1. SOURCE SUMMARY
# =========================================================

def create_source_summary(today_df, lw_df):

    rows = []

    source_sets = [
        (today_df, lw_df),
    ]

    for source in SOURCES:
        source_sets.append(
            (
                source,
                source_filter(today_df, source),
                source_filter(lw_df, source),
            )
        )

    for source, today, lw in source_sets:

        today_rev = safe_sum(today, "item_netAmount")
        lw_rev = safe_sum(lw, "item_netAmount")

        rows.append({
            "Source Group": source,
            "Today Rev": round(today_rev, 2),
            "LW Rev": round(lw_rev, 2),
            "Growth %": growth_pct(today_rev, lw_rev),
            "Today Dis %": discount_pct(today),
            "LW Dis %": discount_pct(lw),
            "Dis Change %": round(
                discount_pct(today) - discount_pct(lw),
                2,
            ),
        })

    return pd.DataFrame(rows)


source_summary = create_source_summary(
    current_sales,
    lw_sales,
)


# =========================================================
# 2. BRAND SOURCE ANALYSIS
# =========================================================

def create_brand_source_analysis(today_df, lw_df):

    rows = []

    for brand_name, brand_filter_value in BRANDS.items():

        today_brand = brand_filter(today_df, brand_filter_value)
        lw_brand = brand_filter(lw_df, brand_filter_value)

        # Total row first - matches the requested layout.
        for label, today, lw in [
            ("Total", today_brand, lw_brand),
            *[
                (
                    source,
                    source_filter(today_brand, source),
                    source_filter(lw_brand, source),
                )
                for source in SOURCES
            ],
        ]:

            today_rev = safe_sum(today, "item_netAmount")
            lw_rev = safe_sum(lw, "item_netAmount")
            today_dis = discount_pct(today)
            lw_dis = discount_pct(lw)

            rows.append({
                "Brand": brand_name,
                "Source Group": label,
                "Today Rev": round(today_rev, 2),
                "LW Rev": round(lw_rev, 2),
                "Growth %": growth_pct(today_rev, lw_rev),
                "Today Dis %": today_dis,
                "LW Dis %": lw_dis,
                "Dis Change %": round(
                    today_dis - lw_dis,
                    2,
                ),
            })

    return pd.DataFrame(rows)


brand_source_analysis = create_brand_source_analysis(
    current_sales,
    lw_sales,
)


# =========================================================
# 3. OVERALL CATEGORY DASHBOARD
# =========================================================
def create_overall_category_dashboard(today_df, lw_df):

    categories = sorted(
        set(today_df["Category Group"].dropna().unique())
        | set(lw_df["Category Group"].dropna().unique())
    )

    rows = []

    for category in categories:

        today_cat = today_df[
            today_df["Category Group"] == category
        ]

        lw_cat = lw_df[
            lw_df["Category Group"] == category
        ]

        row = {
            "Category": category,
            "Overall": unique_orders(today_cat),
        }

        for source in SOURCES:
            row[source] = unique_orders(
                source_filter(today_cat, source)
            )

        row["LW % for Overall"] = growth_pct(
            unique_orders(today_cat),
            unique_orders(lw_cat),
        )

        for source in SOURCES:
            row[f"LW % {source}"] = growth_pct(
                unique_orders(source_filter(today_cat, source)),
                unique_orders(source_filter(lw_cat, source)),
            )

        rows.append(row)

    columns = [
        "Category",
        "In Store",
        "Swiggy",
        "Zomato",
        "Toing",
        "Ownly",
        "LW % In Store",
        "LW % Swiggy",
        "LW % Zomato",
        "LW % Toing",
        "LW % Ownly",
    ]

    return (
        pd.DataFrame(rows, columns=columns)
        .sort_values(ascending=False)
        .reset_index(drop=True)
    )


overall_category_dashboard = create_overall_category_dashboard(
    current_sales,
    lw_sales,
)


# =========================================================
# 4. CATEGORY DASHBOARD
# =========================================================
def create_category_dashboard(today_df, lw_df):

    rows = []

    categories = sorted(
        set(today_df["Category Group"].dropna().unique())
        | set(lw_df["Category Group"].dropna().unique())
    )

    for category in categories:

        today = today_df[
            today_df["Category Group"] == category
        ]

        lw = lw_df[
            lw_df["Category Group"] == category
        ]

        rows.append({
            "Category Group": category,
            "Orders": unique_orders(today),
            "Qty_Sold": round(safe_sum(today, "item_quantity"), 2),
            "Net_Rev": round(safe_sum(today, "item_netAmount"), 2),
            "Discount": round(abs(safe_sum(today, "item_netDiscountAmount")), 2),
            "Dis %": discount_pct(today),
            "LW_Net_Rev": round(safe_sum(lw, "item_netAmount"), 2),
            "LW_Qty": round(safe_sum(lw, "item_quantity"), 2),
            "LW_Orders": unique_orders(lw),
            "LW_Discount": round(abs(safe_sum(lw, "item_netDiscountAmount")), 2),
            "LW Dis %": discount_pct(lw),
            "Growth %": growth_pct(
                safe_sum(today, "item_netAmount"),
                safe_sum(lw, "item_netAmount"),
            ),
        })

    columns = [
        "Category Group",
        "Orders",
        "Qty_Sold",
        "Net_Rev",
        "Discount",
        "Dis %",
        "LW_Net_Rev",
        "LW_Qty",
        "LW_Orders",
        "LW_Discount",
        "LW Dis %",
        "Growth %",
    ]

    return (
        pd.DataFrame(rows, columns=columns)
        .sort_values("Net_Rev", ascending=False)
        .reset_index(drop=True)
    )


category_dashboard = create_category_dashboard(
    current_sales,
    lw_sales,
)


# =========================================================
# 5. BRAND X CATEGORY DASHBOARD - ORDERS
# =========================================================
def create_brand_category_orders(today_df, lw_df):

    rows = []

    for brand_name, brand_filter_value in BRANDS.items():

        today_brand = brand_filter(today_df, brand_filter_value)
        lw_brand = brand_filter(lw_df, brand_filter_value)

        categories = sorted(
            set(today_brand["Category Group"].dropna().unique())
            | set(lw_brand["Category Group"].dropna().unique())
        )

        for category in categories:

            today = today_brand[
                today_brand["Category Group"] == category
            ]

            lw = lw_brand[
                lw_brand["Category Group"] == category
            ]

            row = {
                "Brand Name by Category": f"{brand_name} - {category}",
                "Overall": unique_orders(today),
            }

            for source in SOURCES:
                row[source] = unique_orders(
                    source_filter(today, source)
                )

            row["LW % for Overall"] = growth_pct(
                unique_orders(today),
                unique_orders(lw),
            )

            for source in SOURCES:
                row[f"LW % {source}"] = growth_pct(
                    unique_orders(source_filter(today, source)),
                    unique_orders(source_filter(lw, source)),
                )

            rows.append(row)

    columns = [
        "Brand Name by Category",
        "In Store",
        "Swiggy",
        "Zomato",
        "Toing",
        "Ownly",
        "LW % In Store",
        "LW % Swiggy",
        "LW % Zomato",
        "LW % Toing",
        "LW % Ownly",
    ]

    return (
        pd.DataFrame(rows, columns=columns)
        .sort_values(ascending=False)
        .reset_index(drop=True)
    )


brand_category_orders = create_brand_category_orders(
    current_sales,
    lw_sales,
)


# =========================================================
# REGION HELPERS
# =========================================================

def region_filter(df, region):
    return df[
        df["Region"]
        .astype(str)
        .str.strip()
        .str.upper()
        == region.upper()
    ].copy()


# =========================================================
# 6. BRAND X REGION X CATEGORY - ORDERS
# =========================================================
def create_brand_region_category_orders(today_df, lw_df):

    dashboards = {
        "Offline (In Store)": pd.DataFrame(),
        "Online (Except In Store)": pd.DataFrame(),
    }

    # IMPORTANT: Region dashboard intentionally excludes Ownly and Toing.
    region_sources = {
        "Offline (In Store)": ["In Store"],
        "Online (Except In Store)": ["Swiggy", "Zomato", "Toing"],
    }

    for mode, allowed_sources in region_sources.items():

        rows = []

        for brand_name, brand_filter_value in BRANDS.items():

            today_brand = brand_filter(today_df, brand_filter_value)
            lw_brand = brand_filter(lw_df, brand_filter_value)

            today_brand = today_brand[
                today_brand["Source Group"].isin(allowed_sources)
            ]

            lw_brand = lw_brand[
                lw_brand["Source Group"].isin(allowed_sources)
            ]

            categories = sorted(
                set(today_brand["Category Group"].dropna().unique())
                | set(lw_brand["Category Group"].dropna().unique())
            )

            for category in categories:

                today = today_brand[
                    today_brand["Category Group"] == category
                ]

                lw = lw_brand[
                    lw_brand["Category Group"] == category
                ]

                row = {
                    "Brand Name by Category": f"{brand_name} - {category}",
                    "Overall": unique_orders(today),
                }

                for region in REGIONS:
                    row[region] = unique_orders(
                        region_filter(today, region)
                    )

                row["LW % for Overall"] = growth_pct(
                    unique_orders(today),
                    unique_orders(lw),
                )

                for region in REGIONS:
                    row[f"LW % {region}"] = growth_pct(
                        unique_orders(region_filter(today, region)),
                        unique_orders(region_filter(lw, region)),
                    )

                rows.append(row)

        columns = [
            "Brand Name by Category",
            *REGIONS,
            "LW % for Overall",
            *[f"LW % {r}" for r in REGIONS],
        ]

        dashboards[mode] = (
            pd.DataFrame(rows, columns=columns)
            .sort_values(ascending=False)
            .reset_index(drop=True)
        )

    return dashboards


brand_region_category_orders = create_brand_region_category_orders(
    current_sales,
    lw_sales,
)


# =========================================================
# 7/8. DISCOUNT DASHBOARDS
# =========================================================
def create_discount_dashboard(
    today_df,
    lw_df,
    code_column,
    source,
):

    dashboards = {}

    today_source = source_filter(today_df, source)
    lw_source = source_filter(lw_df, source)

    for brand_name, brand_filter_value in BRANDS.items():

        today_brand = brand_filter(today_source, brand_filter_value)
        lw_brand = brand_filter(lw_source, brand_filter_value)

        # Exclude no-offer rows from the discount-code dashboard.
        today_brand = today_brand[
            today_brand[code_column].astype(str).str.strip().str.lower()
            != "no offer"
        ].copy()

        lw_brand = lw_brand[
            lw_brand[code_column].astype(str).str.strip().str.lower()
            != "no offer"
        ].copy()

        codes = sorted(
            set(today_brand[code_column].dropna().unique())
            | set(lw_brand[code_column].dropna().unique())
        )

        rows = []

        for code in codes:

            today = today_brand[
                today_brand[code_column] == code
            ]

            lw = lw_brand[
                lw_brand[code_column] == code
            ]

            today_orders = unique_orders(today)
            lw_orders = unique_orders(lw)

            today_net = safe_sum(today, "item_netAmount")
            lw_net = safe_sum(lw, "item_netAmount")

            rows.append({
                code_column: code,
                "Orders": today_orders,
                "Qty_Sold": round(safe_sum(today, "item_quantity"), 2),
                "Net_Rev": round(today_net, 2),
                "Discount_Given": round(
                    abs(safe_sum(today, "item_netDiscountAmount")),
                    2,
                ),
                "Dis %": discount_pct(today),
                "AOV": round(
                    today_net / today_orders,
                    2,
                ) if today_orders else 0,
                "LW_Orders": lw_orders,
                "LW_Qty": round(safe_sum(lw, "item_quantity"), 2),
                "LW_Net_Rev": round(lw_net, 2),
                "LW_Discount": round(
                    abs(safe_sum(lw, "item_netDiscountAmount")),
                    2,
                ),
                "LW Dis %": discount_pct(lw),
                "LW AOV": round(
                    lw_net / lw_orders,
                    2,
                ) if lw_orders else 0,
            })

        columns = [
            code_column,
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
            "LW AOV",
        ]

        dashboards[brand_name] = (
            pd.DataFrame(rows, columns=columns)
            .sort_values("Orders", ascending=False)
            .reset_index(drop=True)
        )

    return dashboards


swiggy_discount_dashboard = create_discount_dashboard(
    current_sales,
    lw_sales,
    "Swiggy Code",
    "Swiggy",
)

zomato_discount_dashboard = create_discount_dashboard(
    current_sales,
    lw_sales,
    "Zomato Code",
    "Zomato",
)


# =========================================================
# GOOGLE SHEET OUTPUT
# =========================================================

def get_or_create_sheet(sheet_name):
    try:
        return spreadsheet.worksheet(sheet_name)
    except Exception:
        return spreadsheet.add_worksheet(
            title=sheet_name,
            rows=1000,
            cols=50,
        )


def write_dataframe_sheet(sheet_name, df):

    ws = get_or_create_sheet(sheet_name)
    ws.clear()

    if df is None or df.empty:
        ws.update("A1", [["No Data Available"]])
        return

    data = [df.columns.tolist()] + df.fillna("").values.tolist()
    ws.update("A1", data)

    print(f"✅ Google Sheet Updated: {sheet_name}")


def write_multi_dataframe_sheet(sheet_name, dashboard_dict):

    ws = get_or_create_sheet(sheet_name)
    ws.clear()

    row_num = 1

    for title, df in dashboard_dict.items():

        ws.update(
            f"A{row_num}",
            [[title]],
        )
        row_num += 1

        if df is None or df.empty:
            ws.update(
                f"A{row_num}",
                [["No Data Available"]],
            )
            row_num += 3
            continue

        data = [df.columns.tolist()] + df.fillna("").values.tolist()
        ws.update(
            f"A{row_num}",
            data,
        )
        row_num += len(data) + 3

    print(f"✅ Google Sheet Updated: {sheet_name}")


# Only the requested dashboards are refreshed.
write_dataframe_sheet(
    "Source Summary",
    source_summary,
)

write_dataframe_sheet(
    "Brand Source Analysis",
    brand_source_analysis,
)

write_dataframe_sheet(
    "Overall Category Dashboard",
    overall_category_dashboard,
)

write_dataframe_sheet(
    "Category Dashboard",
    category_dashboard,
)

write_dataframe_sheet(
    "Brand X Category Orders",
    brand_category_orders,
)

write_multi_dataframe_sheet(
    "Brand X Region X Category",
    brand_region_category_orders,
)

write_multi_dataframe_sheet(
    "Swiggy Discount Dashboard",
    swiggy_discount_dashboard,
)

write_multi_dataframe_sheet(
    "Zomato Discount Dashboard",
    zomato_discount_dashboard,
)


# =========================================================
# HTML HELPERS
# =========================================================

def fmt_value(value, column):

    if pd.isna(value):
        return "0"

    if isinstance(value, (np.integer, int)):
        return f"{int(value):,}"

    if isinstance(value, (np.floating, float)):
        if "%" in column or "Growth" in column or "Dis %" in column:
            return f"{float(value):,.2f}%"
        return f"{float(value):,.2f}"

    return str(value)


def dataframe_to_html(df):

    if df is None or df.empty:
        return "<p>No Data Available</p>"

    html = [
        '<table class="dashboard-table">',
        "<thead><tr>",
    ]

    for col in df.columns:
        html.append(f"<th>{col}</th>")

    html.extend([
        "</tr></thead>",
        "<tbody>",
    ])

    for _, row in df.iterrows():

        html.append("<tr>")

        for col in df.columns:
            value = row[col]

            if (
                "Growth" in col
                or "LW %" in col
            ):
                try:
                    numeric = float(value)
                except Exception:
                    numeric = 0

                cls = (
                    "positive"
                    if numeric >= 0
                    else "negative"
                )

                html.append(
                    f'<td class="growth {cls}">'
                    f'{fmt_value(value, col)}</td>'
                )
            else:
                html.append(
                    f'<td>{fmt_value(value, col)}</td>'
                )

        html.append("</tr>")

    html.extend([
        "</tbody>",
        "</table><br>",
    ])

    return "".join(html)


def dashboard_dict_to_html(dashboard_dict):

    html = ""

    for title, df in dashboard_dict.items():
        html += f"<h4>{title}</h4>"
        html += dataframe_to_html(df)

    return html


# =========================================================
# CREATE EMAIL HTML
# =========================================================

end_hour = now.strftime("%I:%M %p")
hourly_window = f"09:00 AM - {end_hour}"

summary_html = f"""
<html>
<head>
<style>
body {{
    font-family: Arial, Helvetica, sans-serif;
    color: #222;
    background: #ffffff;
}}
.dashboard-table {{
    border-collapse: collapse;
    width: 100%;
    font-size: 11px;
    margin-bottom: 18px;
}}
.dashboard-table th {{
    background: #1f4e78;
    color: #ffffff;
    padding: 7px;
    border: 1px solid #d9d9d9;
    text-align: center;
    white-space: nowrap;
}}
.dashboard-table td {{
    padding: 6px;
    border: 1px solid #d9d9d9;
    text-align: center;
    white-space: nowrap;
}}
.dashboard-table td:first-child {{
    text-align: left;
}}
.growth.positive {{
    background: #d9ead3;
    color: #166534;
    font-weight: bold;
}}
.growth.negative {{
    background: #f4cccc;
    color: #991b1b;
    font-weight: bold;
}}
.section {{
    margin-top: 22px;
}}
h2 {{
    color: #1f1f1f;
    margin-bottom: 6px;
}}
h3 {{
    color: #1f4e78;
    margin-bottom: 8px;
}}
h4 {{
    color: #333333;
    margin-bottom: 5px;
}}
.note {{
    font-size: 11px;
    color: #666666;
}}
</style>
</head>
<body>

<h2>📊 Item Level Sales Dashboard</h2>
<p>
<b>Business Date:</b> {business_date.strftime('%d-%b-%y')}<br>
<b>Hourly Window:</b> {hourly_window}<br>
<b>COCO:</b> Yes
</p>

<div class="section">
<h3>🔖 Source Summary</h3>
{dataframe_to_html(source_summary)}
</div>

<div class="section">
<h3>🔖 Brand Source Analysis</h3>
{dashboard_dict_to_html({
    brand: df.reset_index(drop=True)
    for brand, df in brand_source_analysis.groupby("Brand", sort=False)
})}
</div>

<div class="section">
<h3>📦 Overall Category Dashboard</h3>
{dataframe_to_html(overall_category_dashboard)}
</div>

<div class="section">
<h3>📦 Category Dashboard</h3>
{dataframe_to_html(category_dashboard)}
</div>

<div class="section">
<h3>🏪 Brand X Category Dashboard (Orders)</h3>
{dataframe_to_html(brand_category_orders)}
</div>

<div class="section">
<h3>📍 Brand X Region X Category Dashboard (Orders)</h3>
<h4>Offline (In Store)</h4>
{dataframe_to_html(brand_region_category_orders['Offline (In Store)'])}
<h4>Online (Except In Store)</h4>
{dataframe_to_html(brand_region_category_orders['Online (Except In Store)'])}
</div>

<div class="section">
<h3>🟠 Swiggy Discount Dashboard by Brand</h3>
{dashboard_dict_to_html(swiggy_discount_dashboard)}
</div>

<div class="section">
<h3>🔴 Zomato Discount Dashboard by Brand</h3>
{dashboard_dict_to_html(zomato_discount_dashboard)}
</div>

<p class="note">
Discount % is calculated using item_netDiscountAmount / item_grossAmount × 100.
Orders are calculated using unique invoiceNumber.
Toing is identified separately from Rista sales tags and is not included in Swiggy.
Ownly is excluded from the Region dashboards as requested.
</p>

</body>
</html>
"""

print("✅ Email HTML Created")
print("📧 HTML Length:", len(summary_html))


# =========================================================
# SEND EMAIL
# =========================================================

EMAIL_USER = os.environ["EMAIL_USER"]
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]

# Keep your existing recipients here.
to_mails = [
    "faraz@frozenbottle.in, vivek@frozenbottle.in, mis3@frozenbottle.in",
]

cc_mails = [
    "mis2@frozenbottle.in",
]

all_recipients = to_mails + cc_mails

mail_subject = (
    f"Hourly Item Level Sales Dashboard - {business_date}"
)

msg = MIMEMultipart()
msg["From"] = EMAIL_USER
msg["To"] = ", ".join(to_mails)
msg["CC"] = ", ".join(cc_mails)
msg["Subject"] = mail_subject

message_id = (
    f"<item-level-dashboard-{business_date}"
    f"@frozenbottle.in>"
)

msg["Message-ID"] = message_id
msg["In-Reply-To"] = message_id
msg["References"] = message_id

msg.attach(
    MIMEText(summary_html, "html")
)

try:

    print("📧 Email User:", EMAIL_USER)
    print("📧 Recipients:", all_recipients)
    print("📧 Subject:", mail_subject)

    server = smtplib.SMTP(
        "smtp.gmail.com",
        587,
        timeout=60,
    )

    server.starttls()
    server.login(
        EMAIL_USER,
        EMAIL_PASSWORD,
    )

    server.sendmail(
        EMAIL_USER,
        all_recipients,
        msg.as_string(),
    )

    server.quit()

    print("✅ Hourly Mail Sent Successfully")

except Exception as exc:

    print("❌ Hourly Mail Error:", str(exc))
    raise


print("=" * 60)
print("✅ ITEM LEVEL DASHBOARD COMPLETED")
print("=" * 60)
