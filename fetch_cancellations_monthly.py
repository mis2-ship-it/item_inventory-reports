# =========================================================
# 📦 MONTHLY CANCELLATION FETCHER WITH REGION & STORE MAPPING
# =========================================================
import os, json, time, calendar, jwt, requests
import pandas as pd
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials

print("🚀 Starting Historical Monthly Cancellation Fetcher...")

# =========================================================
# 🔐 AUTH & CONFIG
# =========================================================
API_KEY = os.environ["API_KEY"]
SECRET_KEY = os.environ["SECRET_KEY"]

OUTPUT_DIR = os.path.join("data", "cancellations")
os.makedirs(OUTPUT_DIR, exist_ok=True)

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

# =========================================================
# 🗺️ LOAD STORE MAPPING (REGION, OWNERSHIP, AM NAME)
# =========================================================
print("📥 Fetching Store Mapping from Google Sheet...")
creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_CREDENTIALS"]),
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)
client = gspread.authorize(creds)
spreadsheet = client.open("Cancellation Dashboard")
mapping_ws = spreadsheet.worksheet("Store_Mapping")

mapping_data = mapping_ws.get_all_records()
mapping_df = pd.DataFrame(mapping_data)

# Normalize column names
mapping_df.columns = [c.strip() for c in mapping_df.columns]

# Create lookup dictionary by branchCode
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

print(f"✅ Mapped {len(store_map)} stores with Region & Ownership.")

# =========================================================
# 📅 GENERATE MONTH WINDOWS (APRIL TO CURRENT MONTH)
# =========================================================
now = datetime.utcnow() + timedelta(hours=5, minutes=30)
current_year = now.year
current_month = now.month

months_to_fetch = []
start_month = 4  # April

for m in range(start_month, current_month + 1):
    month_name = calendar.month_abbr[m].upper()  # APR, MAY, JUN...
    first_day = f"{current_year}-{m:02d}-01"
    
    if m == current_month:
        last_day = now.strftime("%Y-%m-%d")
    else:
        last_day_num = calendar.monthrange(current_year, m)[1]
        last_day = f"{current_year}-{m:02d}-{last_day_num:02d}"
        
    months_to_fetch.append({
        "label": f"{month_name}_{current_year}",
        "fromDate": f"{first_day} 00:00:00",
        "toDate": f"{last_day} 23:59:59"
    })

print(f"📋 Target Periods: {[m['label'] for m in months_to_fetch]}")

# =========================================================
# 📡 FETCH ACTIVE BRANCHES FROM RISTA
# =========================================================
b_resp = requests.get(
    "https://api.ristaapps.com/v1/branch/list",
    headers=headers(),
    timeout=30
)
b_data = b_resp.json()
branches = [
    b.get("branchCode") 
    for b in (b_data.get("data", []) if isinstance(b_data, dict) else b_data) 
    if b.get("status") == "Active" and b.get("branchCode")
]

print(f"🏪 Active Branches in Rista: {len(branches)}")

# =========================================================
# 🔄 LOOP THROUGH EACH MONTH
# =========================================================
for period in months_to_fetch:
    period_label = period["label"]
    from_date = period["fromDate"]
    to_date = period["toDate"]
    out_file = os.path.join(OUTPUT_DIR, f"cancellations_{period_label}.csv")

    print(f"\n📂 Processing {period_label} ({from_date} → {to_date})...")

    month_rows = []

    for branch in branches:
        page = 1
        has_more = True

        # Grab mapped attributes for this branch
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
                    headers=headers(),
                    params={
                        "branch": branch,
                        "fromDate": from_date,
                        "toDate": to_date,
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

                for item in raw_records:
                    status = str(item.get("status") or item.get("orderStatus") or item.get("invoiceStatus") or "").lower()
                    if status in ["voided", "cancel", "cancelled"]:
                        status_info = item.get("statusInfo") or {}
                        reason = (
                            status_info.get("reason")
                            or status_info.get("sourceReason")
                            or status_info.get("remarks")
                            or item.get("note")
                            or ""
                        )
                        
                        month_rows.append({
                            "invoiceNumber": item.get("invoiceNumber") or item.get("orderId") or "",
                            "branchCode": branch,
                            "storeName": meta["storeName"] if meta["storeName"] else (item.get("branchName") or branch),
                            "ownership": meta["ownership"],
                            "region": meta["region"],
                            "amName": meta["amName"],
                            "channel": item.get("channel") or "Unknown",
                            "status": status,
                            "cancelReason": str(reason).strip(),
                            "netAmount": item.get("netAmount") or item.get("billAmount") or item.get("totalAmount") or 0,
                            "createdDate": item.get("createdDate") or item.get("invoiceDate") or ""
                        })

                if len(raw_records) < 500:
                    has_more = False
                else:
                    page += 1

            except Exception as e:
                print(f"⚠️ Error {branch} (Page {page}): {e}")
                break

    # Save to CSV
    if month_rows:
        df_month = pd.DataFrame(month_rows)
        if "invoiceNumber" in df_month.columns:
            df_month.drop_duplicates(subset=["invoiceNumber"], keep="first", inplace=True)
            
        df_month.to_csv(out_file, index=False)
        print(f"✅ Saved {len(df_month)} cancelled orders → {out_file}")
    else:
        print(f"⚠️ No cancellations found for {period_label}. Writing empty file.")
        pd.DataFrame(columns=[
            "invoiceNumber", "branchCode", "storeName", "ownership", 
            "region", "amName", "channel", "status", "cancelReason", 
            "netAmount", "createdDate"
        ]).to_csv(out_file, index=False)

print("\n🎉 Monthly Cancellation Fetch Completed!")
