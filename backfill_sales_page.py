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
BRANCH_LIST_URL = "https://api.ristaapps.com/v1/branch/list"

# Modes: "DAILY_YESTERDAY", "NOV_2026", or "APR_2026_TO_DATE"
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
    "chargeAmount",
    "AOV",
    "Dis%",
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
# 1. FETCH ALL COCO STORES FROM /v1/branch/list (branchLabels)
#    + ENRICH WITH HELP SHEET METADATA & CHANNEL MAPPINGS
# =========================================================
def load_stores_and_mappings():
    # A. Load Help Sheet (Store metadata fallback + Channel -> Source Group map)
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

    helpsheet_meta = {}
    for row in all_values[1:]:
        if len(row) <= code_idx:
            continue
        b_code = str(row[code_idx]).strip()
        if b_code and b_code.lower() != "branchcode":
            helpsheet_meta[b_code] = {
                "Store Name": str(row[name_idx]).strip() if len(row) > name_idx else "",
                "Ownership": str(row[own_idx]).strip() if len(row) > own_idx else "COCO",
                "Region": str(row[reg_idx]).strip() if (reg_idx >= 0 and len(row) > reg_idx and row[reg_idx]) else ""
            }

    # Parse Channel -> Source Group Mapping (Cols F & G)
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

    # B. Query /v1/branch/list and filter where branchLabels contains "COCO"
    coco_stores = {}
    res = requests.get(BRANCH_LIST_URL, headers=get_headers(), timeout=30)
    if res.status_code == 200:
        payload = res.json()
        branches = payload if isinstance(payload, list) else payload.get("data", [])
        for b in branches:
            code = str(b.get("branchCode") or b.get("code") or "").strip()
            name = str(b.get("branchName") or b.get("name") or "").strip()
            state = str(b.get("state") or b.get("branchState") or "").strip()
            raw_labels = b.get("branchLabels") or b.get("labels") or []

            # Normalize branchLabels whether returned as list of strings, list of dicts, or comma-separated string
            if isinstance(raw_labels, list):
                labels_str = " ".join(
                    str(lbl.get("name") if isinstance(lbl, dict) else lbl) for lbl in raw_labels
                ).upper()
            else:
                labels_str = str(raw_labels).upper()

            if code and "COCO" in labels_str:
                hs_info = helpsheet_meta.get(code, {})
                coco_stores[code] = {
                    "Store Name": name or hs_info.get("Store Name") or code,
                    "Ownership": "COCO",
                    "Region": hs_info.get("Region") or state or ""
                }
        print(f"✅ Found {len(coco_stores)} COCO branches via /v1/branch/list (branchLabels contains 'COCO').")
    else:
        print(f"⚠️ /v1/branch/list returned status {res.status_code}: {res.text}. Falling back to Help Sheet.")

    # Safety union: ensure any COCO store explicitly listed in Help Sheet is also included
    for code, info in helpsheet_meta.items():
        if "COCO" in info.get("Ownership", "").upper() and code not in coco_stores:
            coco_stores[code] = info

    # Log confirmation specifically for Yemalur and Sher-E-Punjab
    for target in ["yemalur", "sher"]:
        matched = [f"{c} ({v['Store Name']})" for c, v in coco_stores.items() if target in v["Store Name"].lower() or target in c.lower()]
        print(f"   🔍 Store check [{target}]: {matched if matched else 'NOT FOUND in branchLabels'}")

    print(f"Total active COCO stores queued: {len(coco_stores)} | Channel mappings: {len(channel_map)}")
    return coco_stores, channel_map

def resolve_source_group(raw_channel, channel_map, tags=""):
    if "toing" in str(tags or "").lower():
        return "Toing"

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
# 2. FETCH ALL PAGINATED TRANSACTIONS FOR BRANCH + DAY
# =========================================================
def fetch_branch_day(branch_code, day_str, store_info, channel_map):
    rows = []
    seen_invoices = set()
    last_key = None

    while True:
        params = {"branch": branch_code, "day": day_str}
        if last_key:
            params["lastKey"] = last_key

        page_data = None
        for attempt in range(4):
            try:
                res = requests.get(SALES_PAGE_URL, headers=get_headers(), params=params, timeout=40)
                if res.status_code == 429:
                    time.sleep(2 * (attempt + 1))
                    continue
                if res.status_code != 200:
                    break
                page_data = res.json()
                break
            except Exception:
                time.sleep(1.5 * (attempt + 1))

        if not page_data:
            break

        orders = page_data.get("data", []) if isinstance(page_data, dict) else page_data
        if not orders:
            break

        for order in orders:
            status = str(order.get("status") or "").strip()
            if status.lower() != "closed":
                continue

            invoice_no = str(order.get("invoiceNumber") or "").strip()
            if invoice_no and invoice_no in seen_invoices:
                continue
            if invoice_no:
                seen_invoices.add(invoice_no)

            branch_name = order.get("branchName") or store_info.get("Store Name") or branch_code
            region = store_info.get("Region") or order.get("branchState") or ""
            invoice_date = order.get("invoiceDate") or order.get("createdDate") or day_str
            session_label = order.get("sessionLabel") or ""
            channel = order.get("channel") or ""
            tags = str(order.get("tags") or "[]")
            source_group = resolve_source_group(channel, channel_map, tags)
            order_brand = order.get("brandName") or "Frozen Bottle"

            # Order-level financial metrics (assigned ONCE per unique invoice on item_idx == 0)
            order_charge_amt = float(order.get("chargeAmount") or 0)
            order_gross_amt  = float(order.get("grossAmount") or 0)
            order_net_amt    = float(order.get("netAmount") or 0)
            order_disc_amt   = abs(float(order.get("netDiscountAmount") or order.get("discountAmount") or 0))

            # Order-level AOV (Net Sales + Charges per unique invoice) & Dis%
            order_aov = round(order_net_amt + order_charge_amt, 2)
            order_dis_pct = round((order_disc_amt / order_gross_amt) * 100, 2) if order_gross_amt > 0 else 0.0

            items = order.get("items") or order.get("orderItems") or []
            for item_idx, item in enumerate(items):
                brand_name = item.get("brandName") or item.get("item_brandName") or order_brand
                item_long = (
                    item.get("longName")
                    or item.get("shortName")
                    or item.get("name")
                    or item.get("itemName")
                    or item.get("item_longName")
                    or item.get("item_shortName")
                    or ""
                )

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
                # Convert negative discount values to positive
                net_disc = abs(float(item.get("netDiscountAmount") or item.get("discountAmount") or item.get("item_netDiscountAmount") or 0))
                net_amt = float(item.get("netAmount") or item.get("net") or item.get("item_netAmount") or 0)
                item_cost = float(
                    item.get("itemCost")
                    or item.get("itemMaterialCost")
                    or item.get("materialCost")
                    or item.get("item_itemCost")
                    or item.get("item_itemMaterialCost")
                    or 0
                )

                # Unique invoice-level fields placed ONLY on first item line (item_idx == 0)
                is_first_item = (item_idx == 0)
                row_charge  = round(order_charge_amt, 2) if is_first_item else 0.0
                row_aov     = order_aov if is_first_item else 0.0
                row_dis_pct = order_dis_pct if is_first_item else 0.0

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
                    "chargeAmount": row_charge,
                    "AOV": row_aov,
                    "Dis%": row_dis_pct,
                    "item_itemCost": round(item_cost, 2)
                })

        next_key = page_data.get("lastKey") if isinstance(page_data, dict) else None
        if not next_key or next_key == last_key:
            break
        last_key = next_key

    return rows

# =========================================================
# 3. EXECUTE DAILY APPEND OR FULL MONTH BACKFILL
# =========================================================
def run_backfill():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    coco_stores, channel_map = load_stores_and_mappings()

    if not coco_stores:
        print("❌ No COCO stores found. Exiting.")
        exit(1)

    ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    yesterday = (ist_now - timedelta(days=1)).date()

    if BACKFILL_MODE == "DAILY_YESTERDAY":
        start_date = yesterday
        end_date = yesterday
    elif BACKFILL_MODE == "APR_2026_TO_DATE":
        start_date = datetime(2026, 4, 1).date()
        end_date = yesterday
    else:
        start_date = datetime(2026, 11, 1).date()
        end_date = yesterday

    print(f"Running Mode: {BACKFILL_MODE} ({start_date} to {end_date})")

    months_to_process = []
    curr = start_date.replace(day=1)
    while curr <= end_date:
        months_to_process.append((curr.year, curr.month))
        curr = curr.replace(year=curr.year + 1, month=1, day=1) if curr.month == 12 else curr.replace(month=curr.month + 1, day=1)

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

        month_abbr = datetime(year, month, 1).strftime("%b-%Y")
        file_name = f"daily_item_level_history_{month_abbr}.csv"
        out_path = os.path.join(OUTPUT_DIR, file_name)

        print(f"\nFetching {month_abbr} ({m_start} to {m_end}) | {len(tasks)} COCO branch-day calls...")
        t0 = time.time()
        fetched_rows = []
        store_row_counts = {b: 0 for b in coco_stores}
        completed = 0

        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = {
                executor.submit(fetch_branch_day, b, ds, si, channel_map): (b, ds)
                for b, ds, si in tasks
            }
            for future in as_completed(futures):
                b_code, _ = futures[future]
                res = future.result()
                if res:
                    fetched_rows.extend(res)
                    store_row_counts[b_code] += len(res)
                completed += 1
                if completed % 300 == 0 or completed == len(tasks):
                    print(f"   [{month_abbr}] {completed}/{len(tasks)} requests done...")

        # Print row counts specifically for Yemalur & Sher-E-Punjab
        for b_code, s_info in coco_stores.items():
            if any(k in s_info["Store Name"].lower() for k in ["yemalur", "sher"]):
                print(f"   ✅ {s_info['Store Name']} ({b_code}): {store_row_counts[b_code]} item rows fetched")

        if fetched_rows:
            df_new = pd.DataFrame(fetched_rows)[REQUIRED_COLUMNS]

            if BACKFILL_MODE == "DAILY_YESTERDAY" and os.path.exists(out_path):
                df_existing = pd.read_csv(out_path)
                fetched_dates = set(df_new["Business Date"].astype(str).unique())
                df_existing = df_existing[~df_existing["Business Date"].astype(str).isin(fetched_dates)]
                df_final = pd.concat([df_existing, df_new], ignore_index=True)
            else:
                df_final = df_new

            df_final.sort_values(by=["Business Date", "branchCode", "invoiceNumber"], inplace=True)
            df_final.to_csv(out_path, index=False)

            uniq_inv = df_final["invoiceNumber"].nunique()
            net_sum = df_final["item_netAmount"].sum()
            chg_sum = df_final["chargeAmount"].sum()
            disc_sum = df_final["item_netDiscountAmount"].sum()
            gross_sum = df_final["item_grossAmount"].sum()
            overall_aov = (net_sum + chg_sum) / uniq_inv if uniq_inv > 0 else 0
            overall_dis_pct = (disc_sum / gross_sum * 100) if gross_sum > 0 else 0

            print(
                f"✅ Saved {out_path} | Rows: {len(df_final):,} | Uniq Orders: {uniq_inv:,} | "
                f"Total Net+Chg: ₹{(net_sum + chg_sum):,.2f} | AOV: ₹{overall_aov:,.2f} | "
                f"Dis%: {overall_dis_pct:.2f}% | Time: {round(time.time() - t0, 1)}s"
            )
        else:
            print(f"⚠️ No CLOSED COCO rows returned for {month_abbr}.")

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
