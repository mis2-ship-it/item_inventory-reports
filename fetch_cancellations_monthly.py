import os, json, time, jwt, requests
import pandas as pd
from datetime import datetime, timedelta

print("🚀 Starting November Cancellation Sample Fetch...")

API_KEY = os.environ["API_KEY"]
SECRET_KEY = os.environ["SECRET_KEY"]

def get_token():
    payload = {"iss": API_KEY, "iat": int(time.time())}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def api_headers():
    return {
        "x-api-key": API_KEY,
        "x-api-token": get_token(),
        "content-type": "application/json"
    }

# 1. Fetch Active Branches
b_resp = requests.get("https://api.ristaapps.com/v1/branch/list", headers=api_headers(), timeout=30)
b_data = b_resp.json()
branches = [
    b.get("branchCode") 
    for b in (b_data.get("data", []) if isinstance(b_data, dict) else b_data) 
    if b.get("status") == "Active" and b.get("branchCode")
]
print(f"🏪 Active Branches: {len(branches)}")

# 2. Test Days in November 2026 (Checking Day by Day for 1st to 10th Nov)
dates_to_check = [f"2026-11-{d:02d}" for d in range(1, 11)]

sample_cancellations = []
sample_raw_json = None

for d in dates_to_check:
    print(f"\n🔍 Querying Day: {d}...")
    day_count = 0
    
    for branch in branches:
        try:
            r = requests.get(
                "https://api.ristaapps.com/v1/sales/page",
                headers=api_headers(),
                params={
                    "branch": branch,
                    "day": d,
                    "page": 1,
                    "pageSize": 500,
                    "sort": "desc"
                },
                timeout=20
            )

            if r.status_code != 200:
                continue

            res = r.json()
            rows = []
            if isinstance(res, dict) and "data" in res:
                rows = res["data"].get("rows", []) if isinstance(res["data"], dict) else res["data"]
            elif isinstance(res, list):
                rows = res

            if not rows:
                continue

            for item in rows:
                status = str(item.get("status") or item.get("orderStatus") or item.get("invoiceStatus") or "").lower()
                
                # Check for void / cancel status
                if status in ["voided", "cancel", "cancelled"] or "statusInfo" in item:
                    if sample_raw_json is None:
                        sample_raw_json = item  # Save first raw item to print its schema

                    status_info = item.get("statusInfo") or {}
                    
                    sample_cancellations.append({
                        "day": d,
                        "branchCode": branch,
                        "branchName": item.get("branchName", branch),
                        "invoiceNumber": item.get("invoiceNumber") or item.get("orderId"),
                        "status": status,
                        "statusInfo.reason": status_info.get("reason"),
                        "statusInfo.sourceReason": status_info.get("sourceReason"),
                        "statusInfo.remarks": status_info.get("remarks"),
                        "note": item.get("note"),
                        "channel": item.get("channel"),
                        "netAmount": item.get("netAmount") or item.get("billAmount")
                    })
                    day_count += 1

        except Exception as e:
            continue
            
    print(f"   -> Found {day_count} cancellations on {d}")
    if len(sample_cancellations) >= 30:
        print("⚡ Reached enough samples to inspect structure!")
        break

# 3. Print Results & Columns
print("\n" + "="*60)
print(f"📊 TOTAL SAMPLES CAPTURED: {len(sample_cancellations)}")
print("="*60)

if sample_raw_json:
    print("\n🔍 SAMPLE RAW RECORD KEYS FROM RISTA:")
    print(list(sample_raw_json.keys()))
    if "statusInfo" in sample_raw_json:
        print("🔍 statusInfo content:", sample_raw_json["statusInfo"])

if sample_cancellations:
    df_sample = pd.DataFrame(sample_cancellations)
    print("\n📋 SAMPLE ROWS PREVIEW:")
    print(df_sample.head(10).to_string())
    
    os.makedirs("data/cancellations", exist_ok=True)
    df_sample.to_csv("data/cancellations/nov_sample_cancellations.csv", index=False)
    print("\n💾 Saved sample to data/cancellations/nov_sample_cancellations.csv")
else:
    print("⚠️ Still 0 cancellations found. Verifying whether status field or query param needs adjustment.")
