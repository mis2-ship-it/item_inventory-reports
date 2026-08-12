import os
import json
import time
import jwt
import requests
import gspread
from google.oauth2.service_account import Credentials

GSHEET_ID = "1_IX_TKer13kKMTjGtw94FcCdRUt_x_C66kSkotuZPV0"
RISTA_BASE_URL = os.environ.get("RISTA_BASE_URL", "https://api.ristaapps.com").rstrip("/")
API_KEY = os.environ.get("API_KEY")
SECRET_KEY = os.environ.get("SECRET_KEY")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS")

STORE_CODE = os.environ.get("STORE_CODE", "")
DATE_VAL = os.environ.get("DATE_VAL", "2026-08-12")

def get_jwt_token():
    payload = {"iss": API_KEY, "iat": int(time.time())}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

# Two header styles used across Rista API versions
HEADER_OPTIONS = [
    {
        "name": "JWT Token Header",
        "headers": {
            "x-api-key": API_KEY,
            "x-api-token": get_jwt_token(),
            "content-type": "application/json"
        }
    },
    {
        "name": "Secret Key Header",
        "headers": {
            "x-api-key": API_KEY,
            "x-secret-key": SECRET_KEY,
            "content-type": "application/json"
        }
    }
]

GET_ENDPOINTS = [
    {"endpoint": "/inventory/indents/page", "params": {"storeCode": STORE_CODE, "branch": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/po/page", "params": {"storeCode": STORE_CODE, "branch": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/grn/page", "params": {"storeCode": STORE_CODE, "branch": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/post_final_grn/page", "params": {"storeCode": STORE_CODE, "branch": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/shrinkage/page", "params": {"storeCode": STORE_CODE, "branch": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/adjustment/page", "params": {"storeCode": STORE_CODE, "branch": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/transfer/page", "params": {"storeCode": STORE_CODE, "branch": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/post_final_ti/page", "params": {"storeCode": STORE_CODE, "branch": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/purchase_return/page", "params": {"storeCode": STORE_CODE, "branch": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/transfer_return/page", "params": {"storeCode": STORE_CODE, "branch": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/audit/page", "params": {"storeCode": STORE_CODE, "branch": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/item/activity/page", "params": {"storeCode": STORE_CODE, "branch": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/store", "params": {"storeCode": STORE_CODE, "branch": STORE_CODE}},
    {"endpoint": "/inventory/store/list", "params": {}},
    {"endpoint": "/inventory/items", "params": {}},
    {"endpoint": "/inventory/store/items", "params": {"storeCode": STORE_CODE, "branch": STORE_CODE}},
    {"endpoint": "/inventory/supplier/list", "params": {}},
    {"endpoint": "/inventory/supplieritem/list", "params": {}},
    {"endpoint": "/inventory/contract/list", "params": {}},
    {"endpoint": "/inventory/contractitem/list", "params": {}},
]

def init_gspread():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(credentials)

def fetch_endpoint_data(endpoint, params):
    clean_params = {k: v for k, v in params.items() if v}
    paths_to_test = [endpoint, f"/v1{endpoint}"]
    
    for path in paths_to_test:
        url = f"{RISTA_BASE_URL}{path}"
        for auth_opt in HEADER_OPTIONS:
            try:
                res = requests.get(url, headers=auth_opt["headers"], params=clean_params, timeout=15)
                if res.status_code in [200, 400, 422]:
                    # Successful connection or parameter-level error (AUTH PASSED)
                    try:
                        sample = json.dumps(res.json())[:2000]
                    except Exception:
                        sample = res.text[:2000]
                    return res.status_code, sample, path, auth_opt["name"]
            except Exception as e:
                continue

    # Default fallback if all fail
    fallback_url = f"{RISTA_BASE_URL}{endpoint}"
    res = requests.get(fallback_url, headers=HEADER_OPTIONS[0]["headers"], params=clean_params, timeout=15)
    try:
        sample = json.dumps(res.json())[:2000]
    except Exception:
        sample = res.text[:2000]
    return res.status_code, sample, endpoint, HEADER_OPTIONS[0]["name"]

def main():
    print("Connecting to Google Sheets...")
    gc = init_gspread()
    sh = gc.open_by_key(GSHEET_ID)
    
    try:
        worksheet = sh.worksheet("Endpoint_Summary")
    except gspread.WorksheetNotFound:
        worksheet = sh.add_worksheet(title="Endpoint_Summary", rows="100", cols="10")
    
    worksheet.clear()
    worksheet.append_row(["HTTP Method", "Path Tested", "Status Code", "Auth Used", "Sample Response / Data"])
    
    rows = []
    for item in GET_ENDPOINTS:
        ep = item["endpoint"]
        params = item["params"]
        print(f"Testing: {ep}...")
        
        status, sample, resolved_path, auth_used = fetch_endpoint_data(ep, params)
        rows.append(["GET", resolved_path, status, auth_used, sample])
    
    worksheet.append_rows(rows)
    print("Execution complete. Check Google Sheet for details.")

if __name__ == "__main__":
    main()
