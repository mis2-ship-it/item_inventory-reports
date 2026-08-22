import os
import json
import time
import jwt
import requests
import pandas as pd
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials

print("🚀 Rista COCO Multi-Endpoint Discount & Item Intelligence Sync Started")

# =========================================================
# AUTHENTICATION & CONFIG
# =========================================================
API_KEY = os.environ["API_KEY"]
SECRET_KEY = os.environ["SECRET_KEY"]
RISTA_BASE_URL = "https://api.ristaapps.com/v1"
SPREADSHEET_ID = "1umqb0k_G0F-cAzMbrmqSYnEz06-NjmCANWtWEa_NS9w"
TARGET_TAB_NAME = "Raw_Analytics_Coupons"

def get_token():
    payload = {
        "iss": API_KEY,
        "iat": int(time.time())
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def headers():
    return {
        "x-api-key": API_KEY,
        "x-api-token": get_token(),
        "content-type": "application/json"
    }

# Google Sheets Connector
creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_CREDENTIALS"]),
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SPREADSHEET_ID)

fetch_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
print("📅 Target Sync Date:", fetch_date)

# =========================================================
# LOAD COCO BRANCHES ONLY
# =========================================================
try:
    help_ws = spreadsheet.worksheet("Help_Sheet")
except Exception:
    help_ws = spreadsheet.worksheet("Help Sheet")

help_data = help_ws.get()
if not help_data:
    print("❌ Help Sheet Empty")
    exit()

raw_headers = [str(h).strip().lower().replace(" ", "") for h in help_data[0]]
rows = help_data[1:]
help_df = pd.DataFrame(rows, columns=raw_headers[:len(rows[0])])

if "ownership" in help_df.columns:
    help_df = help_df[help_df["ownership"].astype(str).str.upper().str.strip() == "COCO"].copy()

if "branchcode" not in help_df.columns:
    print("❌ branchcode column missing in Help Sheet.")
    exit()

branches = help_df["branchcode"].dropna().astype(str).str.strip().unique().tolist()
print(f"🏪 Active COCO Branch Count: {len(branches)}")

# =========================================================
# DATA FETCHING & MERGING (/sales/page + /analytics/discount/transactions)
# =========================================================
def safe_fetch(url, params):
    try:
        res = requests.get(url, headers=headers(), params=params, timeout=60)
        if res.status_code == 200:
            payload = res.json()
            return payload.get("data", []) or payload.get("transactions", []) or payload.get("sales", [])
    except Exception as e:
        print(f"⚠️ Fetch failed on {url}: {e}")
    return []

compiled_discount_records = []

for idx, branch in enumerate(branches):
    print(f"🔄 [{idx+1}/{len(branches)}] Processing Branch: {branch}")

    # 1. Fetch Sales Data with items array
    sales_raw = safe_fetch(f"{RISTA_BASE_URL}/sales/page", {"branch": branch, "day": fetch_date, "date": fetch_date})
    
    # Build fast lookup dictionary: invoiceNumber -> List of Item Metadata
    invoice_item_map = {}
    if sales_raw:
        sales_df = pd.json_normalize(sales_raw)
        
        # Flatten items array
        if "items" in sales_df.columns:
            for _, sale in sales_df.iterrows():
                inv_no = str(sale.get("invoiceNumber", "") or sale.get("orderNumber", "")).strip()
                if not inv_no:
                    continue
                
                items_list = sale.get("items", [])
                if isinstance(items_list, list) and len(items_list) > 0:
                    for itm in items_list:
                        if not isinstance(itm, dict):
                            continue
                        brand_name = itm.get("brandName") or itm.get("brand") or sale.get("brandName") or "Frozen Bottle"
                        cat_name = itm.get("categoryName") or itm.get("category") or "General"
                        item_name = itm.get("itemName") or itm.get("name") or itm.get("item") or "Miscellaneous"
                        
                        if inv_no not in invoice_item_map:
                            invoice_item_map[inv_no] = []
                        invoice_item_map[inv_no].append({
                            "brandName": brand_name,
                            "categoryName": cat_name,
                            "itemName": item_name
                        })

    # 2. Fetch Discount Transactions
    disc_raw = safe_fetch(f"{RISTA_BASE_URL}/analytics/discount/transactions", {"branch": branch, "day": fetch_date, "date": fetch_date})
    if not disc_raw:
        continue

    disc_df = pd.json_normalize(disc_raw)
    disc_df["branchCode"] = branch

    # 3. Enrich Discount Transactions with Brand, Category & Item Name
    for _, row in disc_df.iterrows():
        inv_no = str(row.get("invoiceNumber", "")).strip()
        matched_items = invoice_item_map.get(inv_no, [])

        disc_amt = abs(float(row.get("discountAmount", 0) or 0))
        sale_amt = float(row.get("saleAmount", 0) or 0)
        net_amt = sale_amt - disc_amt
        dis_pct = (disc_amt / sale_amt * 100) if sale_amt > 0 else 0.0

        if matched_items:
            # If multi-item invoice, distribute or tag primary items
            brands_found = list({i["brandName"] for i in matched_items if i["brandName"]})
            cats_found = list({i["categoryName"] for i in matched_items if i["categoryName"]})
            items_found = list({i["itemName"] for i in matched_items if i["itemName"]})

            row_dict = row.to_dict()
            row_dict.update({
                "brandName": ", ".join(brands_found) if brands_found else "Frozen Bottle",
                "categoryName": ", ".join(cats_found) if cats_found else "General",
                "itemName": ", ".join(items_found) if items_found else "Order Level Discount",
                "discountAmount": disc_amt,
                "saleAmount": sale_amt,
                "netAmount": net_amt,
                "disPct": round(dis_pct, 2)
            })
            compiled_discount_records.append(row_dict)
        else:
            # Direct/Cart level discount fallback
            row_dict = row.to_dict()
            row_dict.update({
                "brandName": row.get("brandName") or "Frozen Bottle",
                "categoryName": "General",
                "itemName": "Cart / Order Level Discount",
                "discountAmount": disc_amt,
                "saleAmount": sale_amt,
                "netAmount": net_amt,
                "disPct": round(dis_pct, 2)
            })
            compiled_discount_records.append(row_dict)

# =========================================================
# EXPORT TO GOOGLE SHEETS TAB
# =========================================================
try:
    ws = spreadsheet.worksheet(TARGET_TAB_NAME)
except Exception:
    ws = spreadsheet.add_worksheet(title=TARGET_TAB_NAME, rows="1000", cols="20")

ws.clear()

if not compiled_discount_records:
    ws.update([["Status"], [f"No discount transactions returned for {fetch_date}."]], "A1")
    print("⚠️ No records compiled.")
else:
    final_df = pd.DataFrame(compiled_discount_records)
    final_df = final_df.fillna("")

    # Clean nested JSON columns
    for col in final_df.columns:
        if final_df[col].apply(lambda x: isinstance(x, (list, dict))).any():
            final_df[col] = final_df[col].apply(lambda x: json.dumps(x) if isinstance(x, (list, dict)) else x)

    # Standardize column hierarchy
    col_order = [
        "branchName", "branchCode", "invoiceNumber", "invoiceDate", "brandName",
        "categoryName", "itemName", "discountCode", "discountAmount", "saleAmount",
        "netAmount", "disPct", "appliedBy", "reason", "couponCode"
    ]
    existing = [c for c in col_order if c in final_df.columns]
    remaining = [c for c in final_df.columns if c not in col_order]
    final_df = final_df[existing + remaining]

    ws.update([final_df.columns.tolist()] + final_df.values.tolist(), "A1")
    print(f"✅ Tab '{TARGET_TAB_NAME}' updated with {len(final_df)} enriched rows.")
