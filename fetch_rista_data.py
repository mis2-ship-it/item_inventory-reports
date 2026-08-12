import os
import json
import time
import jwt
import requests
import gspread
from datetime import datetime
from google.oauth2.service_account import Credentials

# --- Configuration & Environment Secrets ---
GSHEET_ID = "1_IX_TKer13kKMTjGtw94FcCdRUt_x_C66kSkotuZPV0"
RISTA_BASE_URL = os.environ.get("RISTA_BASE_URL", "https://api.ristaapps.com").rstrip("/")
API_KEY = os.environ.get("API_KEY")
SECRET_KEY = os.environ.get("SECRET_KEY")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS")

# Default Parameters
DEFAULT_BRANCH = os.environ.get("BRANCH", os.environ.get("STORE_CODE", ""))
TODAY_STR = datetime.now().strftime("%Y-%m-%d")
DAY_VAL = os.environ.get("DAY_VAL", os.environ.get("DATE_VAL", TODAY_STR))

def get_jwt_token():
    payload = {"iss": API_KEY, "iat": int(time.time())}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def get_headers():
    return {
        "x-api-key": API_KEY,
        "x-api-token": get_jwt_token(),
        "content-type": "application/json"
    }

def init_gspread():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(credentials)

def get_active_branch_code():
    """Fetch first active store/branch code dynamically if none is provided."""
    if DEFAULT_BRANCH:
        return DEFAULT_BRANCH
    try:
        url = f"{RISTA_BASE_URL}/v1/inventory/store/list"
        res = requests.get(url, headers=get_headers(), timeout=15)
        if res.status_code == 200:
            stores = res.json()
            for s in stores:
                if s.get("status") == "Active":
                    return s.get("branchName") or s.get("storeCode")
    except Exception as e:
        print(f"Could not fetch store list dynamically: {e}")
    return "AECS Layout"

def fetch_endpoint_data(endpoint, params):
    clean_params = {k: v for k, v in params.items() if v}
    url = f"{RISTA_BASE_URL}{endpoint}"
    
    try:
        res = requests.get(url, headers=get_headers(), params=clean_params, timeout=20)
        status = res.status_code
        try:
            sample = json.dumps(res.json())[:2000]
        except Exception:
            sample = res.text[:2000]
        return status, sample
    except Exception as e:
        return "ERROR", str(e)

def main():
    branch_code = get_active_branch_code()
    print(f"Using Branch/Store Code: '{branch_code}' | Date: '{DAY_VAL}'")

    # Corrected parameter definitions matching Rista API schema
    endpoints_to_test = [
        {"endpoint": "/v1/inventory/indents/page", "params": {"branch": branch_code, "day": DAY_VAL}},
        {"endpoint": "/v1/inventory/po/page", "params": {"branch": branch_code, "day": DAY_VAL}},
        {"endpoint": "/v1/inventory/grn/page", "params": {"branch": branch_code, "day": DAY_VAL}},
        {"endpoint": "/v1/inventory/post_final_grn/page", "params": {"branch": branch_code, "day": DAY_VAL}},
        {"endpoint": "/v1/inventory/shrinkage/page", "params": {"branch": branch_code, "day": DAY_VAL}},
        {"endpoint": "/v1/inventory/adjustment/page", "params": {"branch": branch_code, "day": DAY_VAL}},
        {"endpoint": "/v1/inventory/transfer/page", "params": {"branch": branch_code, "day": DAY_VAL}},
        {"endpoint": "/v1/inventory/post_final_ti/page", "params": {"branch": branch_code, "day": DAY_VAL}},
        {"endpoint": "/v1/inventory/purchase_return/page", "params": {"branch": branch_code, "day": DAY_VAL}},
        {"endpoint": "/v1/inventory/transfer_return/page", "params": {"branch": branch_code, "day": DAY_VAL}},
        {"endpoint": "/v1/inventory/audit/page", "params": {"branch": branch_code, "day": DAY_VAL}},
        {"endpoint": "/v1/inventory/item/activity/page", "params": {"branch": branch_code, "day": DAY_VAL}},
        {"endpoint": "/v1/inventory/store", "params": {"storeCode": branch_code}},
        {"endpoint": "/v1/inventory/store/list", "params": {}},
        {"endpoint": "/v1/inventory/items", "params": {}},
        {"endpoint": "/v1/inventory/store/items", "params": {"storeCode": branch_code}},
        {"endpoint": "/v1/inventory/supplier/list", "params": {}},
        {"endpoint": "/v1/inventory/supplieritem/list", "params": {}},
        {"endpoint": "/v1/inventory/contract/list", "params": {}},
        {"endpoint": "/v1/inventory/contractitem/list", "params": {}},
    ]

    print("Connecting to Google Sheets...")
    gc = init_gspread()
    sh = gc.open_by_key(GSHEET_ID)
    
    try:
        worksheet = sh.worksheet("Endpoint_Summary")
    except gspread.WorksheetNotFound:
        worksheet = sh.add_worksheet(title="Endpoint_Summary", rows="100", cols="10")
    
    worksheet.clear()
    worksheet.append_row(["HTTP Method", "Endpoint", "Status Code", "Sample Response / Data"])
    
    rows = []
    for item in endpoints_to_test:
        ep = item["endpoint"]
        params = item["params"]
        print(f"Fetching {ep}...")
        
        status, sample = fetch_endpoint_data(ep, params)
        rows.append(["GET", ep, status, sample])
    
    worksheet.append_rows(rows)
    print("Google Sheet updated successfully with valid sample data!")

if __name__ == "__main__":
    main()
