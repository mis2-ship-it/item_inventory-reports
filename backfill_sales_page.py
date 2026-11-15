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

# Modes: "APR_2026_TO_DATE" (default full Apr->Yesterday), "DAILY_YESTERDAY", or "CUSTOM_MONTH"
BACKFILL_MODE = os.environ.get("BACKFILL_MODE", "APR_2026_TO_DATE").strip().upper()
CUSTOM_MONTH = os.environ.get("CUSTOM_MONTH", "2026-10").strip() # Format: YYYY-MM

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
# 1. LOAD ALL COCO STORES & CHANNEL MAPPINGS
# =========================================================
def load_stores_and_mappings():
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

    for code, info in helpsheet_meta.items():
        if "COCO" in info.get("Ownership", "").upper() and code not in coco_stores:
            coco_stores[code] = info

    print(f"Loaded {len(coco_stores)} COCO stores and {len(channel_map)} Channel mappings.")
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
# 2. FETCH ALL PAGINATED TRANSACTIONS (RETURNS ROWS + SUCCESS FLAG)
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
        request_ok = False
        for attempt in range(8):
            try:
                res = requests.get(SALES_PAGE_URL, headers=get_headers(), params=params, timeout=45)
                if res.status_code == 429 or res.status_code >= 500:
                    time.sleep(min(25, 2.5 * (attempt + 1)))
                    continue
                if res.status_code != 200:
                    # Non-retryable client error (e.g., 400/404 if store wasn't open yet)
                    return rows, True
                page_data = res.json()
                request_ok = True
                break
            except Exception:
                time.sleep(min(20, 2 * (attempt + 1)))

        if not request_ok or page_data is None:
            # Signal failure so this branch-day is queued for the recovery retry pass
            return rows, False

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

            order_charge_amt = float(order.get("chargeAmount") or 0)
            order_gross_amt  = float(order.get("grossAmount") or 0)
            order_net_amt    = float(order.get("netAmount") or 0)
            order_disc_amt   = abs(float(order.get("netDiscountAmount") or order.get("discountAmount") or 0))

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

                is_first_item = (item_idx == 0)
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
                    "chargeAmount": round(order_charge_amt, 2) if is_first_item else 0.0,
                    "AOV": order_aov if is_first_item else 0.0,
                    "Dis%": order_dis_pct if is_first_item else 0.0,
                    "item_itemCost": round(item_cost, 2)
                })

        next_key = page_data.get("lastKey") if isinstance(page_data, dict) else None
        if not next_key or next_key == last_key:
            break
        last_key = next_key

    return rows, True

# =========================================================
# 3. EXECUTE FULL MONTH-BY-MONTH FETCH (1ST TO END OF MONTH)
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
    elif BACKFILL_MODE == "CUSTOM_MONTH":
        y, m = map(int, CUSTOM_MONTH.split("-"))
        _, last_d = calendar.monthrange(y, m)
        start_date = datetime(y, m, 1).date()
        end_date = min(yesterday, datetime(y, m, last_d).date())
    else:
        # Default: Full sweep from 1st April 2026 to yesterday
        start_date = datetime(2026, 4, 1).date()
        end_date = yesterday

    print(f"==================================================================")
    print(f"RUNNING MODE: {BACKFILL_MODE} | RANGE: {start_date} to {end_date}")
    print(f"==================================================================")

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

        print(f"\n==================================================================")
        print(f"📦 MONTH: {month_abbr} | PERIOD: {m_start} (1st) to {m_end} (End)")
        print(f"   Total API Calls: {len(tasks)} ({len(coco_stores)} stores x {(m_end - m_start).days + 1} days)")
        print(f"==================================================================")

        t0 = time.time()
        fetched_rows = []
        failed_tasks = []
        completed = 0

        # Pass 1: Concurrent fetch with safe concurrency (max_workers=6)
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(fetch_branch_day, b, ds, si, channel_map): (b, ds, si)
                for b, ds, si in tasks
            }
            for future in as_completed(futures):
                task_info = futures[future]
                res_rows, ok = future.result()
                if res_rows:
                    fetched_rows.extend(res_rows)
                if not ok:
                    failed_tasks.append(task_info)
                completed += 1
                if completed % 400 == 0 or completed == len(tasks):
                    print(f"   [{month_abbr}] Pass 1 Progress: {completed}/{len(tasks)} calls done...")

        # Pass 2: Automatic recovery retry for any rate-limited/timed-out store-days
        if failed_tasks:
            print(f"   🔄 [{month_abbr}] Retrying {len(failed_tasks)} throttled/timed-out store-day calls after 10s cooldown...")
            time.sleep(10)
            still_failed = 0
            with ThreadPoolExecutor(max_workers=3) as executor:
                retry_futures = {
                    executor.submit(fetch_branch_day, b, ds, si, channel_map): (b, ds, si)
                    for b, ds, si in failed_tasks
                }
                for future in as_completed(retry_futures):
                    res_rows, ok = future.result()
                    if res_rows:
                        fetched_rows.extend(res_rows)
                    if not ok:
                        still_failed += 1
            print(f"   🔄 [{month_abbr}] Recovery pass finished. Unresolved calls: {still_failed}")

        if fetched_rows:
            df_new = pd.DataFrame(fetched_rows)[REQUIRED_COLUMNS]

            if BACKFILL_MODE == "DAILY_YESTERDAY" and os.path.exists(out_path):
                df_existing = pd.read_csv(out_path)
                fetched_dates = set(df_new["Business Date"].astype(str).unique())
                df_existing = df_existing[~df_existing["Business Date"].astype(str).isin(fetched_dates)]
                df_final = pd.concat([df_existing, df_new], ignore_index=True)
            else:
                df_final = df_new

            df_final = df_final.sort_values(by=["Business Date", "branchCode", "invoiceNumber"])
            df_final.to_csv(out_path, index=False)

            # Full 1st-to-End-of-Month Calendar Verification Table
            daily_summary = df_final.groupby("Business Date").agg(
                stores=("branchCode", "nunique"),
                orders=("invoiceNumber", "nunique"),
                net=("item_netAmount", "sum"),
                chg=("chargeAmount", "sum")
            ).to_dict(orient="index")

            print(f"\n📅 {month_abbr} FULL CALENDAR FETCH REPORT ({m_start} -> {m_end}):")
            print(f"   {'Date':<12} | {'Status':<16} | {'Stores':>6} | {'Orders':>7} | {'Net + Charges':>15}")
            print(f"   {'-'*68}")

            check_d = m_start
            missing_days = 0
            while check_d <= m_end:
                d_key = check_d.strftime("%Y-%m-%d")
                if d_key in daily_summary:
                    info = daily_summary[d_key]
                    st_cnt = int(info["stores"])
                    ord_cnt = int(info["orders"])
                    tot_val = info["net"] + info["chg"]
                    status_lbl = "✅ FETCHED" if st_cnt >= 65 else "⚠️ LOW STORES"
                    print(f"   {d_key:<12} | {status_lbl:<16} | {st_cnt:>6} | {ord_cnt:>7,} | ₹{tot_val:>14,.2f}")
                else:
                    missing_days += 1
                    print(f"   {d_key:<12} | ❌ MISSING DATE  | {0:>6} | {0:>7} | ₹{0:>14,.2f}")
                check_d += timedelta(days=1)

            uniq_inv = df_final["invoiceNumber"].nunique()
            net_sum = df_final["item_netAmount"].sum()
            chg_sum = df_final["chargeAmount"].sum()
            print(f"   {'-'*68}")
            print(
                f"✅ SAVED {out_path} | Period: {m_start} to {m_end} | Missing Days: {missing_days} | "
                f"Rows: {len(df_final):,} | Orders: {uniq_inv:,} | Total Net+Chg: ₹{(net_sum + chg_sum):,.2f} | Time: {round(time.time() - t0, 1)}s"
            )
        else:
            print(f"⚠️ No CLOSED COCO rows returned for {month_abbr} ({m_start} to {m_end}).")

        # Brief 5s pause between months so Rista API rate-limit window resets cleanly
        time.sleep(5)

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
    print("\n✅ Updated historical_data/daily_history_index.json")

if __name__ == "__main__":
    run_backfill()
