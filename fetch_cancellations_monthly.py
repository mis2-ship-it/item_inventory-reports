# =========================================================
# 📦 HISTORICAL RDC CANCELLATIONS & OVERALL ORDER TRACKER
# =========================================================
import os, json, time, calendar, jwt, requests
import pandas as pd
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials

print("🚀 Starting Historical RDC Fetcher (April to Current Month)...")

# =========================================================
# 🔐 AUTH & CONFIG
# =========================================================
API_KEY = os.environ["API_KEY"]
SECRET_KEY = os.environ["SECRET_KEY"]

OUTPUT_DIR = os.path.join("data", "cancellations")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def get_token():
    payload = {"iss": API_KEY, "iat": int(time.time())}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def api_headers():
    return {
        "x-api-key": API_KEY,
        "x-api-token": get_token(),
        "content-type": "application/json"
    }

# =========================================================
# 🗺️ 1. LOAD STORE MAPPING (ONLY LISTED BRANCHES)
# =========================================================
print("📥 Fetching Store_Mapping & Reason_Map from Google Sheet...")
creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_CREDENTIALS"]),
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)
client = gspread.authorize(creds)
spreadsheet = client.open("Cancellation Dashboard")

# Read Store_Mapping safely
store_ws = spreadsheet.worksheet("Store_Mapping")
store_raw = store_ws.get_all_values()
if not store_raw:
    print("❌ Store_Mapping is empty.")
    exit(1)

raw_headers = [str(h).strip() for h in store_raw[0]]
valid_cols = [i for i, h in enumerate(raw_headers) if h]
store_headers = [raw_headers[i] for i in valid_cols]

store_rows = []
for r in store_raw[1:]:
    padded = r + [""] * (len(raw_headers) - len(r))
    store_rows.append([padded[i] for i in valid_cols])

mapping_df = pd.DataFrame(store_rows, columns=store_headers)

# Extract only listed branchCodes
store_map = {}
for _, row in mapping_df.iterrows():
    b_code = str(row.get("branchCode", "")).strip()
    if b_code:
        store_map[b_code] = {
            "storeName": str(row.get("Store Name", "")).strip(),
            "ownership": str(row.get("Ownership", "")).strip().upper(),
            "region": str(row.get("Region", "")).strip().upper(),
            "amName": str(row.get("AM Name", "")).strip()
        }

target_branches = list(store_map.keys())
print(f"✅ Loaded {len(target_branches)} listed stores from Store_Mapping.")

# =========================================================
# 📋 2. LOAD REASON MAP (FILTER ONLY RDC, EXCLUDE NOT RDC)
# =========================================================
reason_ws = spreadsheet.worksheet("Reason_Map")
reason_raw = reason_ws.get_all_values()
headers_r = [str(h).strip() for h in reason_raw[0] if str(h).strip()]
reason_df = pd.DataFrame(reason_raw[1:], columns=reason_raw[0])[headers_r]

rdc_rules = []
for _, row in reason_df.iterrows():
    keyword = str(row.get("Reason (raw, contains)", "")).strip().lower()
    notes = str(row.get("Notes", "")).strip()
    bucket = str(row.get("Bucket", "")).strip()
    
    # Must have RDC and NOT contain 'NOT RDC'
    if keyword and "RDC" in notes.upper() and "NOT RDC" not in notes.upper():
        rdc_rules.append({
            "keyword": keyword,
            "bucket": bucket,
            "notes": notes
        })

print(f"✅ Loaded {len(rdc_rules)} RDC keyword rules.")

# =========================================================
# 📅 3. DEFINE MONTHS (APRIL 2026 TO CURRENT MONTH)
# =========================================================
now = datetime.utcnow() + timedelta(hours=5, minutes=30)
current_year = now.year
current_month = now.month
current_day = now.day

months_to_run = []
start_month = 4  # April

for m in range(start_month, current_month + 1):
    month_name = calendar.month_abbr[m].upper()
    total_days = current_day if m == current_month else calendar.monthrange(current_year, m)[1]
    
    days = [f"{current_year}-{m:02d}-{d:02d}" for d in range(1, total_days + 1)]
    months_to_run.append({
        "label": f"{month_name}_{current_year}",
        "days": days
    })

# =========================================================
# 🔄 4. FETCH DATA MONTH BY MONTH
# =========================================================
for period in months_to_run:
    period_label = period["label"]
    days_list = period["days"]
    print(f"\n==========================================")
    print(f"📂 Processing Period: {period_label} ({len(days_list)} days)")
    print(f"==========================================")

    month_rdc_cancellations = []
    month_summary_rows = []

    for day_str in days_list:
        for branch in target_branches:
            meta = store_map[branch]
            page = 1
            has_more = True
            branch_day_total_orders = 0
            branch_day_rdc_count = 0
            branch_day_rdc_loss = 0.0

            while has_more:
                try:
                    r = requests.get(
                        "https://api.ristaapps.com/v1/sales/page",
                        headers=api_headers(),
                        params={
                            "branch": branch,
                            "day": day_str,
                            "page": page,
                            "pageSize": 500,
                            "sort": "desc"
                        },
                        timeout=25
                    )

                    if r.status_code != 200:
                        break

                    res_json = r.json()
                    raw_records = []
                    if isinstance(res_json, dict) and "data" in res_json:
                        raw_records = res_json["data"].get("rows", []) if isinstance(res_json["data"], dict) else res_json["data"]
                    elif isinstance(res_json, list):
                        raw_records = res_json

                    if not raw_records:
                        break

                    # Add count to overall orders for this store & date
                    branch_day_total_orders += len(raw_records)

                    # Scan for cancelled / voided orders
                    for item in raw_records:
                        status = str(item.get("status") or item.get("orderStatus") or item.get("invoiceStatus") or "").lower()
                        if status in ["voided", "cancel", "cancelled"]:
                            status_info = item.get("statusInfo") or {}
                            raw_reason = (
                                status_info.get("sourceReason") 
                                or status_info.get("reason") 
                                or status_info.get("remarks") 
                                or item.get("note") 
                                or ""
                            )
                            raw_reason_clean = str(raw_reason).strip().lower()

                            # Match strictly with RDC rules
                            matched_bucket = None
                            matched_note = None
                            for rule in rdc_rules:
                                if rule["keyword"] in raw_reason_clean:
                                    matched_bucket = rule["bucket"]
                                    matched_note = rule["notes"]
                                    break

                            # Exclude if not an RDC issue
                            if not matched_bucket:
                                continue

                            # Extract metadata fields
                            source_info = item.get("sourceInfo") or {}
                            source_val = source_info.get("source") or item.get("source") or item.get("channel") or "Unknown"
                            session_val = item.get("sessionLabel") or item.get("session") or item.get("sessionName") or "Unknown"
                            inv_date_time = item.get("invoiceDate") or item.get("createdDate") or item.get("modifiedDate") or ""
                            amount = float(item.get("netAmount") or item.get("billAmount") or item.get("totalAmount") or 0)

                            branch_day_rdc_count += 1
                            branch_day_rdc_loss += amount

                            month_rdc_cancellations.append({
                                "invoiceNumber": str(item.get("invoiceNumber") or item.get("orderId") or "").strip(),
                                "day": day_str,
                                "invoiceDateTime": inv_date_time,
                                "branchCode": branch,
                                "storeName": meta["storeName"],
                                "ownership": meta["ownership"],
                                "region": meta["region"],
                                "amName": meta["amName"],
                                "source": source_val,
                                "session": session_val,
                                "channel": item.get("channel") or "Unknown",
                                "status": status,
                                "sourceReason": raw_reason,
                                "rdcBucket": matched_bucket,
                                "rdcNote": matched_note,
                                "netAmount": round(amount, 2)
                            })

                    if len(raw_records) < 500:
                        has_more = False
                    else:
                        page += 1

                except Exception as e:
                    break

            # Calculate store day summary only if store had activity
            if branch_day_total_orders > 0:
                rdc_pct = round((branch_day_rdc_count / branch_day_total_orders) * 100, 2)
                month_summary_rows.append({
                    "day": day_str,
                    "branchCode": branch,
                    "storeName": meta["storeName"],
                    "ownership": meta["ownership"],
                    "region": meta["region"],
                    "amName": meta["amName"],
                    "totalOrders": branch_day_total_orders,
                    "rdcOrders": branch_day_rdc_count,
                    "rdcPercent": rdc_pct,
                    "rdcLossAmount": round(branch_day_rdc_loss, 2)
                })

    # =========================================================
    # 💾 SAVE PER-MONTH FILES
    # =========================================================
    raw_csv = os.path.join(OUTPUT_DIR, f"rdc_cancellations_{period_label}.csv")
    summary_csv = os.path.join(OUTPUT_DIR, f"rdc_summary_{period_label}.csv")

    df_canc = pd.DataFrame(month_rdc_cancellations)
    if not df_canc.empty and "invoiceNumber" in df_canc.columns:
        df_canc.drop_duplicates(subset=["invoiceNumber"], keep="first", inplace=True)
    df_canc.to_csv(raw_csv, index=False)

    df_sum = pd.DataFrame(month_summary_rows)
    df_sum.to_csv(summary_csv, index=False)

    print(f"✅ {period_label} Finished:")
    print(f"   - RDC Orders Saved: {len(df_canc)} → {raw_csv}")
    print(f"   - Store Summaries Saved: {len(df_sum)} → {summary_csv}")

print("\n🎉 All historical months successfully fetched and saved!")
