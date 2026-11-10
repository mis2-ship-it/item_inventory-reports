# =========================================================
# 📦 MONTHLY CANCELLATION FETCHER (APRIL TO CURRENT MONTH)
# =========================================================
import os, json, time, calendar, jwt, requests
import pandas as pd
from datetime import datetime, timedelta

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
# 📅 GENERATE MONTH WINDOWS (APRIL 2026 TO CURRENT MONTH)
# =========================================================
now = datetime.utcnow() + timedelta(hours=5, minutes=30)
current_year = now.year
current_month = now.month

# List of months starting from April (Month 4)
months_to_fetch = []
start_month = 4

for m in range(start_month, current_month + 1):
    month_name = calendar.month_abbr[m].upper()  # e.g., APR, MAY, JUN
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
# 📡 FETCH ACTIVE BRANCHES
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

print(f"🏪 Active Branches: {len(branches)}")

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

    for idx, branch in enumerate(branches, start=1):
        page = 1
        has_more = True

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

                # Filter only Voided / Cancelled rows in-memory to save RAM
                for item in raw_records:
                    status = str(item.get("status") or item.get("orderStatus") or item.get("invoiceStatus") or "").lower()
                    if status in ["voided", "cancel", "cancelled"]:
                        # Extract nested reasons safely
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
                            "branchName": item.get("branchName") or branch,
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
        # Drop duplicates by invoiceNumber if any
        if "invoiceNumber" in df_month.columns:
            df_month.drop_duplicates(subset=["invoiceNumber"], keep="first", inplace=True)
            
        df_month.to_csv(out_file, index=False)
        print(f"✅ Saved {len(df_month)} cancelled orders → {out_file}")
    else:
        print(f"⚠️ No cancellations found for {period_label}. Writing empty file.")
        pd.DataFrame(columns=[
            "invoiceNumber", "branchCode", "branchName", "channel", 
            "status", "cancelReason", "netAmount", "createdDate"
        ]).to_csv(out_file, index=False)

print("\n🎉 Monthly Cancellation Fetch Completed!")
