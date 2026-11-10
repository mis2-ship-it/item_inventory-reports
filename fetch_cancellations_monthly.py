# =========================================================
# 📊 RDC CANCELLATIONS & RDC% CALCULATION PIPELINE
# =========================================================
import os, json, time, calendar, jwt, requests
import pandas as pd
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials

print("🚀 Starting RDC Cancellation & RDC% Pipeline...")

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
# 🗺️ GOOGLE SHEETS: LOAD STORE & REASON MAPPING
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

# 1. Store Mapping
mapping_ws = spreadsheet.worksheet("Store_Mapping")
store_raw = mapping_ws.get_all_values()
headers_store = [str(h).strip() for h in store_raw[0] if str(h).strip()]
store_df = pd.DataFrame(store_raw[1:], columns=store_raw[0])
store_df = store_df[headers_store]

store_map = {}
for _, row in store_df.iterrows():
    b_code = str(row.get("branchCode", "")).strip()
    if b_code:
        store_map[b_code] = {
            "storeName": str(row.get("Store Name", "")).strip(),
            "ownership": str(row.get("Ownership", "")).strip().upper(),
            "region": str(row.get("Region", "")).strip().upper(),
            "amName": str(row.get("AM Name", "")).strip()
        }

# 2. Reason Map (Keep only RDC, drop "Not RDC")
reason_ws = spreadsheet.worksheet("Reason_Map")
reason_raw = reason_ws.get_all_values()
headers_reason = [str(h).strip() for h in reason_raw[0] if str(h).strip()]
reason_df = pd.DataFrame(reason_raw[1:], columns=reason_raw[0])
reason_df = reason_df[headers_reason]

rdc_rules = []
for _, row in reason_df.iterrows():
    keyword = str(row.get("Reason (raw, contains)", "")).strip().lower()
    notes = str(row.get("Notes", "")).strip()
    bucket = str(row.get("Bucket", "")).strip()
    
    # Strictly consider RDC, skip 'Not RDC'
    if keyword and "RDC" in notes.upper() and "NOT RDC" not in notes.upper():
        rdc_rules.append({
            "keyword": keyword,
            "bucket": bucket,
            "notes": notes
        })

print(f"✅ Loaded {len(store_map)} Stores and {len(rdc_rules)} RDC Match Rules.")

# =========================================================
# 📅 DEFINE DAYS TO FETCH (CURRENT MONTH TO DATE)
# =========================================================
now = datetime.utcnow() + timedelta(hours=5, minutes=30)
current_year = now.year
current_month = now.month
current_day = now.day

# Fetch all days from 1st of current month up to yesterday/today
date_list = [f"{current_year}-{current_month:02d}-{d:02d}" for d in range(1, current_day + 1)]
month_label = f"{calendar.month_abbr[current_month].upper()}_{current_year}"

# =========================================================
# 📡 FETCH ACTIVE BRANCHES
# =========================================================
b_resp = requests.get("https://api.ristaapps.com/v1/branch/list", headers=api_headers(), timeout=30)
b_data = b_resp.json()
branches = [
    b.get("branchCode") 
    for b in (b_data.get("data", []) if isinstance(b_data, dict) else b_data) 
    if b.get("status") == "Active" and b.get("branchCode")
]
print(f"🏪 Active Branches: {len(branches)}")

# =========================================================
# 🔄 FETCH ORDERS & PROCESS CANCELLATIONS
# =========================================================
rdc_cancellations = []
store_order_counts = {}  # { (branchCode, day): total_orders }

for day_str in date_list:
    print(f"\n📅 Fetching Day: {day_str}...")

    for branch in branches:
        page = 1
        has_more = True
        branch_day_total_orders = 0
        
        meta = store_map.get(branch, {
            "storeName": branch,
            "ownership": "UNKNOWN",
            "region": "UNKNOWN",
            "amName": "UNKNOWN"
        })

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
                    timeout=30
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

                # Count overall unique orders
                branch_day_total_orders += len(raw_records)

                # Process Void / Cancelled Records
                for item in raw_records:
                    status = str(item.get("status") or item.get("orderStatus") or item.get("invoiceStatus") or "").lower()
                    
                    if status in ["voided", "cancel", "cancelled"]:
                        status_info = item.get("statusInfo") or {}
                        
                        # Primary: statusInfo.sourceReason
                        raw_reason = (
                            status_info.get("sourceReason") 
                            or status_info.get("reason") 
                            or status_info.get("remarks") 
                            or item.get("note") 
                            or ""
                        )
                        raw_reason_clean = str(raw_reason).strip().lower()

                        # Match against RDC rules
                        matched_bucket = None
                        matched_note = None
                        for rule in rdc_rules:
                            if rule["keyword"] in raw_reason_clean:
                                matched_bucket = rule["bucket"]
                                matched_note = rule["notes"]
                                break

                        # Skip if Not RDC
                        if not matched_bucket:
                            continue

                        # Extract Source & Session
                        source_info = item.get("sourceInfo") or {}
                        source_val = source_info.get("source") or item.get("source") or item.get("channel") or "Unknown"
                        session_val = item.get("sessionLabel") or item.get("session") or item.get("sessionName") or "Unknown"
                        
                        # Extract Invoice Date & Time
                        inv_date_time = item.get("invoiceDate") or item.get("createdDate") or item.get("modifiedDate") or ""

                        rdc_cancellations.append({
                            "invoiceNumber": item.get("invoiceNumber") or item.get("orderId") or "",
                            "day": day_str,
                            "invoiceDateTime": inv_date_time,
                            "branchCode": branch,
                            "storeName": meta["storeName"] if meta["storeName"] else (item.get("branchName") or branch),
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
                            "netAmount": item.get("netAmount") or item.get("billAmount") or item.get("totalAmount") or 0
                        })

                if len(raw_records) < 500:
                    has_more = False
                else:
                    page += 1

            except Exception as e:
                print(f"⚠️ Error {branch} on {day_str} (Page {page}): {e}")
                break
                
        store_order_counts[(branch, day_str)] = branch_day_total_orders

# =========================================================
# 📊 CALCULATE RDC% AND AGGREGATES
# =========================================================
rdc_df = pd.DataFrame(rdc_cancellations)
if not rdc_df.empty:
    rdc_df.drop_duplicates(subset=["invoiceNumber"], keep="first", inplace=True)

# Build summary rows (Total Orders vs RDC Orders -> RDC%)
summary_rows = []
for (b_code, d_str), total_orders in store_order_counts.items():
    if total_orders == 0:
        continue
    
    meta = store_map.get(b_code, {"storeName": b_code, "ownership": "UNKNOWN", "region": "UNKNOWN", "amName": "UNKNOWN"})
    
    if not rdc_df.empty:
        rdc_count = len(rdc_df[(rdc_df["branchCode"] == b_code) & (rdc_df["day"] == d_str)])
        rdc_loss = rdc_df[(rdc_df["branchCode"] == b_code) & (rdc_df["day"] == d_str)]["netAmount"].sum()
    else:
        rdc_count = 0
        rdc_loss = 0

    rdc_pct = round((rdc_count / total_orders) * 100, 2) if total_orders > 0 else 0.0

    summary_rows.append({
        "day": d_str,
        "branchCode": b_code,
        "storeName": meta["storeName"],
        "ownership": meta["ownership"],
        "region": meta["region"],
        "amName": meta["amName"],
        "totalOrders": total_orders,
        "rdcOrders": rdc_count,
        "rdcPercent": rdc_pct,
        "rdcLossAmount": round(rdc_loss, 2)
    })

summary_df = pd.DataFrame(summary_rows)

# =========================================================
# 💾 EXPORT CSV FILES
# =========================================================
raw_file = os.path.join(OUTPUT_DIR, f"rdc_cancellations_{month_label}.csv")
summary_file = os.path.join(OUTPUT_DIR, f"rdc_summary_{month_label}.csv")

rdc_df.to_csv(raw_file, index=False)
summary_df.to_csv(summary_file, index=False)

print("\n" + "="*60)
print(f"✅ RDC Detailed Orders Saved: {len(rdc_df)} → {raw_file}")
print(f"✅ Store-level RDC% Summary Saved: {len(summary_df)} → {summary_file}")
print("="*60)
