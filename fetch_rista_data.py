import os
import json
import time
import jwt
import requests
import gspread
from google.oauth2.service_account import Credentials

# --- Configuration & Credentials ---
GSHEET_ID = "1_IX_TKer13kKMTjGtw94FcCdRUt_x_C66kSkotuZPV0"
RISTA_BASE_URL = os.environ.get("RISTA_BASE_URL", "https://api.ristaapps.com").rstrip("/")
API_KEY = os.environ.get("API_KEY")
SECRET_KEY = os.environ.get("SECRET_KEY")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS")

# Query Parameters
STORE_CODE = os.environ.get("STORE_CODE", "")
DATE_VAL = os.environ.get("DATE_VAL", "2026-08-12")

# --- JWT Auth Headers Builder ---
def get_token():
    payload = {"iss": API_KEY, "iat": int(time.time())}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def build_headers():
    return {
        "x-api-key": API_KEY,
        "x-api-token": get_token(),
        "content-type": "application/json"
    }

# --- Inventory GET Endpoints ---
GET_ENDPOINTS = [
    {"endpoint": "/inventory/indents/page", "params": {"storeCode": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/po/page", "params": {"storeCode": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/grn/page", "params": {"storeCode": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/post_final_grn/page", "params": {"storeCode": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/shrinkage/page", "params": {"storeCode": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/adjustment/page", "params": {"storeCode": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/transfer/page", "params": {"storeCode": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/post_final_ti/page", "params": {"storeCode": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/purchase_return/page", "params": {"storeCode": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/transfer_return/page", "params": {"storeCode": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/audit/page", "params": {"storeCode": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/item/activity/page", "params": {"storeCode": STORE_CODE, "date": DATE_VAL}},
    {"endpoint": "/inventory/store", "params": {"storeCode": STORE_CODE}},
    {"endpoint": "/inventory/store/list", "params": {}},
    {"endpoint": "/inventory/items", "params": {}},
    {"endpoint": "/inventory/store/items", "params": {"storeCode": STORE_CODE}},
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
    # Strip empty values to keep requests clean
    clean_params = {k: v for k, v in params.items() if v}
    url = f"{RISTA_BASE_URL}{endpoint}"
    
    try:
        response = requests.get(url, headers=build_headers(), params=clean_params, timeout=30)
        status = response.status_code
        try:
            data = response.json()
            sample_text = json.dumps(data)[:2000]
        except Exception:
            sample_text = response.text[:2000]
        return status, sample_text
    except Exception as e:
        return "ERROR", str(e)

def main():
    print("Connecting to Google Sheet...")
    gc = init_gspread()
    sh = gc.open_by_key(GSHEET_ID)
    
    try:
        worksheet = sh.worksheet("Endpoint_Summary")
    except gspread.WorksheetNotFound:
        worksheet = sh.add_worksheet(title="Endpoint_Summary", rows="100", cols="10")
    
    worksheet.clear()
    worksheet.append_row(["HTTP Method", "Endpoint", "Status Code", "Sample Response / Data"])
    
    rows_to_append = []
    
    for item in GET_ENDPOINTS:
        ep = item["endpoint"]
        params = item["params"]
        print(f"Fetching: {ep}...")
        
        status, sample = fetch_endpoint_data(ep, params)
        rows_to_append.append(["GET", ep, status, sample])
    
    worksheet.append_rows(rows_to_append)
    print("Successfully pushed data to Google Sheet!")

if __name__ == "__main__":
    main()
