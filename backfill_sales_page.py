import os
import json
import time
import calendar
import jwt
import requests
import pandas as pd
import gspread
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.oauth2.service_account import Credentials

# =========================================================
# CONFIGURATION
# =========================================================
API_KEY = os.environ["API_KEY"]
SECRET_KEY = os.environ["SECRET_KEY"]
GSHEET_KEY = "1PhVeFoPERJODrGPW68ORAO0kJjToB5ZYMY-yOkGTWDk"
OUTPUT_DIR = "historical_data"
SALES_PAGE_URL = "https://api.ristaapps.com/v1/sales/page"

# Mode from GitHub Action input ("NOV_2026" or "APR_2026_TO_DATE")
BACKFILL_MODE = os.environ.get("BACKFILL_MODE", "NOV_2026").strip().upper()

REQUIRED_COLUMNS = [
    "Business Date",
    "branchCode",
    "branchName",
    "Ownership",
    "Region",
    "brandName",
    "invoiceNumber",
    "invoiceDate",
    "sessionLabel",
    "channel",
    "Source Group",
    "tags",
    "status",
    "item_longName",
    "item_variants",
    "item_categoryName",
    "item_quantity",
    "item_grossAmount",
    "item_netDiscountAmount",
    "item_netAmount",
    "item_itemCost"
]

def get_headers():
    token = jwt.encode({"iss": API_KEY, "iat": int(time.time())}, SECRET_KEY, algorithm="HS256")
    return {
        "x-api-key": API_KEY,
        "x-api-token": token,
        "content-type": "application/json"
    }

# =========================================================
# 1. LOAD COCO BRANCHES & CHANNEL MAPPINGS FROM HELP SHEET
# =========================================================
def load_help_sheet_metadata():
    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    client = gspread.authorize(creds)
    ws = client.open_by_key(GSHEET_KEY).worksheet("Help Sheet")
    all_values = ws.get_all_values()

    store_headers = [h.strip() for h in all_values[0]]
    code_idx = next((i for i, h in enumerate(store_headers) if h.lower().replace(" ", "") in ["branchcode", "storecode", "code"]), 0)
    name_idx = next((i for i, h in enumerate(store_headers) if ("store" in h.lower() or "branch" in h.lower()) and i != code_idx), 1)
    own_idx  = next((i for i, h in enumerate(store_headers) if "ownership" in h.lower() or "type" in h.lower()), 2)
    reg_idx  = next((i for i, h in enumerate(store_headers) if "region" in h.lower() or "state" in h.lower()), -1)

    coco_stores = {}
    for row in all_values[1:]:
        if len(row) <= max(code_idx, own_idx):
            continue
        b_code = str(row[code_idx]).strip()
        ownership = str(row[own_idx]).strip().upper()
        if b_code and ownership == "COCO":
            coco_stores[b_code] = {
                "Store Name": str(row[name_idx]).strip() if len(row) > name_idx else "",
                "Ownership": "COCO",
                "Region": str(row[reg_idx]).strip() if (reg_idx >= 0 and len(row) > reg_idx and row[reg_idx]) else ""
            }

    ch_col, sg_col = 5, 6
    for r_idx in range(min(5, len(all_values))):
        for c_idx, val in enumerate(all_values[r_idx]):
            v = str(val).strip().lower()
            if v == "channel":
                ch_col = c_idx
            elif v == "source group":
                sg_col = c_idx

    channel_map = {}
    for row in all_values[1:]:
        if len(row) > max(ch_col, sg_col):
            ch = str(row[ch_col]).strip()
            sg = str(row[sg_col]).strip()
            if ch and sg and ch.lower() != "channel":
                channel_map[ch.lower()] = sg

    print(f"Loaded {len(coco_stores)} COCO branch codes and {len(channel_map)} Channel mappings.")
    return coco_stores, channel_map

def resolve_source_group(raw_channel, channel_map):
    ch_clean = str(raw_channel or "").strip()
    if not ch_clean:
        return "Others"
    key = ch_clean.lower()
    if key in channel_map:
        return channel_map[key]
    if "swiggy" in key:
        return "Swiggy"
    if "zomato" in key:
        return "Zomato"
    if "in-store" in key or "instore" in key or "pick-up" in key or "pickup" in key:
        return "In Store"
    if "ownly" in key:
        return "Ownly"
    if "magicpin" in key:
        return "Magicpin"
    if "website" in key or "delivery" in key:
        return "Website"
    if "hogr" in key:
        return "HOGR"
    return "Others"

# =========================================================
# 2. FETCH SINGLE BRANCH + DAY FROM /v1/sales/page
# =========================================================
def fetch_branch_day(branch_code, day_str, store_info, channel_map):
    rows = []
    params = {"branch": branch_code, "day": day_str}

    for attempt in range(3):
        try:
            res = requests.get(SALES_PAGE_URL, headers=get_headers(), params=params, timeout=35)
            if res.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            if res.status_code != 200:
                return rows

            orders = res.json().get("data", [])
            for order in orders:
                status = str(order.get("status") or "").strip()
                if status.lower() != "closed":
                    continue

                branch_name = order.get("branchName") or store_info.get("Store Name") or branch_code
                region = store_info.get("Region") or order.get("branchState") or ""
                invoice_no = order.get("invoiceNumber") or ""
                invoice_date = order.get("invoiceDate") or order.get("createdDate") or day_str
                session_label = order.get("sessionLabel") or ""
                channel = order.get("channel") or ""
                source_group = resolve_source_group(channel, channel_map)
                tags = str(order.get("tags") or "[]")
                order_brand = order.get("brandName") or "Frozen Bottle"

                items = order.get("items") or order.get("orderItems") or []
                for item in items:
                    brand_name = (
                        item.get("brandName")
                        or item.get("item_brandName")
                        or order_brand
                    )

                    # Check unprefixed API keys first (longName, shortName, name), then prefixed fallbacks
                    item_long = (
                        item.get("longName")
                        or item.get("shortName")
                        or item.get("name")
                        or item.get("itemName")
                        or item.get("item_longName")
                        or item.get("item_shortName")
                        or ""
                    )

                    # Check unprefixed API variant keys first (variants, variant, variantName)
                    raw_var = (
                        item.get("variants")
                        or item.get("variant")
                        or item.get("variantName")
                        or item.get("variation")
                        or item.get("item_variants")
                        or item.get("item_variant")
                        or ""
                    )
                    variant = str(raw_var).strip() if raw_var is not None else ""
                    if variant.lower() in ["", "nan", "none", "-", "null"]:
                        variant = "Regular"

                    category = (
                        item.get("categoryName")
                        or item.get("category")
                        or item.get("item_categoryName")
                        or ""
                    )

                    qty = float(item.get("quantity") or item.get("qty") or item.get("item_quantity") or 0)
                    gross = float(item.get("grossAmount") or item.get("gross") or item.get("item_grossAmount") or 0)
                    net_disc = float(item.get("netDiscountAmount") or item.get("discountAmount") or item.get("item_netDiscountAmount") or 0)
                    net_amt = float(item.get("netAmount") or item.get("net") or item.get("item_netAmount") or 0)

                    # Check unprefixed API cost keys first (itemCost, itemMaterialCost)
                    item_cost = float(
                        item.get("itemCost")
                        or item.get("itemMaterialCost")
                        or item.get("materialCost")
                        or item.get("item_itemCost")
                        or item.get("item_itemMaterialCost")
                        or 0
                    )

                    rows.append({
                        "Business Date": day_str,
                        "branchCode": branch_code,
                        "branchName": branch_name,
                        "Ownership": "COCO",
                        "Region": region,
                        "brandName": brand_name,
                        "invoiceNumber": invoice_no,
                        "invoiceDate": invoice_date,
                        "sessionLabel": session_label,
                        "channel": channel,
                        "Source Group": source_group,
                        "tags": tags,
                        "status": status,
                        "item_longName": str(item_long).strip(),
                        "item_variants": variant,
                        "item_categoryName": str(category).strip(),
                        "item_quantity": qty,
                        "item_grossAmount": round(gross, 2),
                        "item_netDiscountAmount": round(net_disc, 2),
                        "item_netAmount": round(net_amt, 2),
                        "item_itemCost": round(item_cost, 2)
                    })
            return rows
        except Exception:
            time.sleep(1)
    return rows

# =========================================================
# 3. PROCESS MONTH BY MONTH -> daily_item_level_history_<Mon>-<Year>.csv
# =========================================================
def run_backfill():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    coco_stores, channel_map = load_help_sheet_metadata()

    if not coco_stores:
        print("No COCO stores found in Help Sheet. Exiting.")
        exit(1)

    ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    yesterday = (ist_now - timedelta(days=1)).date()

    if BACKFILL_MODE == "APR_2026_TO_DATE":
        start_date = datetime(2026, 4, 1).date()
    else:
        start_date = datetime(2026, 11, 1).date()

    end_date = yesterday
    print(f"Running Backfill Mode: {BACKFILL_MODE} ({start_date} to {end_date})")

    months_to_process = []
    curr = start_date.replace(day=1)
    while curr <= end_date:
        months_to_process.append((curr.year, curr.month))
        if curr.month == 12:
            curr = curr.replace(year=curr.year + 1, month=1, day=1)
        else:
            curr = curr.replace(month=curr.month + 1, day=1)

    for year, month in months_to_process:
        _, last_day = calendar.monthrange(year, month)
        m_start = max(start_date, datetime(year, month, 1).date())
        m_end = min(end_date, datetime(year, month, last_day).date())

        tasks = []
        d = m_start
        while d <= m_end:
            d_str = d.strftime("%Y-%m-%d")
            for b_code, s_info in coco_stores.items():
                tasks.append((b_code, d_str, s_info))
            d += timedelta(days=1)

        # Format filename label as Nov-2026, Apr-2026, etc.
        month_abbr = datetime(year, month, 1).strftime("%b-%Y")
        file_name = f"daily_item_level_history_{month_abbr}.csv"
        print(f"\nFetching {month_abbr} ({m_start} to {m_end}) | {len(tasks)} COCO branch-day calls...")

        t0 = time.time()
        month_rows = []
        completed = 0

        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = {
                executor.submit(fetch_branch_day, b, ds, si, channel_map): (b, ds)
                for b, ds, si in tasks
            }
            for future in as_completed(futures):
                res = future.result()
                if res:
                    month_rows.extend(res)
                completed += 1
                if completed % 300 == 0 or completed == len(tasks):
                    print(f"   [{month_abbr}] {completed}/{len(tasks)} requests done...")

        if month_rows:
            df_month = pd.DataFrame(month_rows)[REQUIRED_COLUMNS]
            df_month.sort_values(by=["Business Date", "branchCode", "invoiceNumber"], inplace=True)
            out_path = os.path.join(OUTPUT_DIR, file_name)
            df_month.to_csv(out_path, index=False)
            net_sum = df_month["item_netAmount"].sum()
            cost_sum = df_month["item_itemCost"].sum()
            print(f"Saved {out_path} | Rows: {len(df_month):,} | Net Sales: {net_sum:,.2f} | Item Cost: {cost_sum:,.2f} | Time: {round(time.time() - t0, 1)}s")
        else:
            print(f"No CLOSED COCO rows returned for {month_abbr}.")

    # Maintain a dedicated index file without touching existing index.json
    history_files = sorted([
        f for f in os.listdir(OUTPUT_DIR)
        if f.startswith("daily_item_level_history_") and f.endswith(".csv")
    ])
    index_payload = {
        "updated_at_ist": ist_now.strftime("%Y-%m-%d %H:%M:%S"),
        "ownership_filter": "COCO",
        "files": history_files
    }
    with open(os.path.join(OUTPUT_DIR, "daily_history_index.json"), "w") as f:
        json.dump(index_payload, f, indent=2)
    print("Updated historical_data/daily_history_index.json")

if __name__ == "__main__":
    run_backfill()
