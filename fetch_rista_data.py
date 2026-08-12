import os
import json
import requests
import gspread
from google.oauth2.service_account import Credentials

# --- Configuration & Credentials from Environment ---
GSHEET_ID = "1_IX_TKer13kKMTjGtw94FcCdRUt_x_C66kSkotuZPV0"
RISTA_BASE_URL = os.environ.get("RISTA_BASE_URL", "https://api.rista.io") # Replace with your actual Rista base URL if different
API_KEY = os.environ.get("API_KEY")
SECRET_KEY = os.environ.get("SECRET_KEY")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS")

# Sample query parameters required by Rista GET endpoints (e.g., storeCode, date, businessId)
# Adjust these default values or retrieve them dynamically if required
STORE_CODE = os.environ.get("STORE_CODE", "DEFAULT_STORE")
DATE_VAL = os.environ.get("DATE_VAL", "2026-08-12")

# --- List of GET Endpoints to Fetch ---
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
    """Authenticate and return Google Sheets client."""
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(credentials)

# Updated Configuration
RISTA_BASE_URL = os.environ.get("RISTA_BASE_URL", "https://api.ristapos.com") # Replace with your official base URL
STORE_CODE = os.environ.get("STORE_CODE", "") # Add your valid Store Code in GitHub Secrets
DATE_VAL = os.environ.get("DATE_VAL", "2026-08-12")

# Endpoint Definitions
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

def fetch_endpoint_data(endpoint, params):
    # Filter out empty string query parameters to prevent clean API calls from failing
    clean_params = {k: v for k, v in params.items() if v}
    
    base = RISTA_BASE_URL.rstrip('/')
    if not base.startswith(('http://', 'https://')):
        base = f"https://{base}"
        
    url = f"{base}{endpoint}"
    
    headers = {
        "x-api-key": API_KEY,
        "x-secret-key": SECRET_KEY,
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, params=clean_params, timeout=30)
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
    print("Connecting to Google Sheets...")
    gc = init_gspread()
    sh = gc.open_by_key(GSHEET_ID)
    
    # Get or create summary sheet
    try:
        worksheet = sh.worksheet("Endpoint_Summary")
    except gspread.WorksheetNotFound:
        worksheet = sh.add_worksheet(title="Endpoint_Summary", rows="100", cols="10")
    
    # Prepare header
    worksheet.clear()
    worksheet.append_row(["HTTP Method", "Endpoint", "Status Code", "Sample Response / Data"])
    
    rows_to_append = []
    
    for item in GET_ENDPOINTS:
        ep = item["endpoint"]
        params = item["params"]
        print(f"Fetching: {ep}...")
        
        status, sample = fetch_endpoint_data(ep, params)
        rows_to_append.append(["GET", ep, status, sample])
    
    # Batch update sheet
    worksheet.append_rows(rows_to_append)
    print("Successfully populated endpoint sample data to Google Sheet!")

if __name__ == "__main__":
    main()
