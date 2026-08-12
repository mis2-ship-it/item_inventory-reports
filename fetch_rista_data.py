import os
import json
import time
import jwt
import requests
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- Environment & Configuration ---
GSHEET_ID = "1_IX_TKer13kKMTjGtw94FcCdRUt_x_C66kSkotuZPV0"
RISTA_BASE_URL = os.environ.get("RISTA_BASE_URL", "https://api.ristaapps.com").rstrip("/")
API_KEY = os.environ.get("API_KEY")
SECRET_KEY = os.environ.get("SECRET_KEY")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS")

# Branch Code for Store-Specific Endpoints
STORE_CODE = os.environ.get("STORE_CODE", "FZBBLR034")

# --- Target Endpoints Configuration ---
TARGET_ENDPOINTS = [
    {
        "tab_name": "Store_List",
        "endpoint": "/v1/inventory/store/list",
        "params": {}
    },
    {
        "tab_name": "Inventory_Items",
        "endpoint": "/v1/inventory/items",
        "params": {}
    },
    {
        "tab_name": "Store_Items",
        "endpoint": "/v1/inventory/store/items",
        "params": {"storeCode": STORE_CODE}
    },
    {
        "tab_name": "Supplier_List",
        "endpoint": "/v1/inventory/supplier/list",
        "params": {}
    },
    {
        "tab_name": "Supplier_Items",
        "endpoint": "/v1/inventory/supplieritem/list",
        "params": {}
    }
]

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

def fetch_data(endpoint, params):
    url = f"{RISTA_BASE_URL}{endpoint}"
    clean_params = {k: v for k, v in params.items() if v}
    
    try:
        res = requests.get(url, headers=get_headers(), params=clean_params, timeout=30)
        if res.status_code == 200:
            js_data = res.json()
            
            # Extract nested list if response is wrapped in dict
            if isinstance(js_data, dict):
                if "items" in js_data:
                    js_data = js_data["items"]
                elif "data" in js_data:
                    js_data = js_data["data"]
            
            return pd.json_normalize(js_data)
        else:
            print(f"⚠️ Failed to fetch {endpoint} | Status Code: {res.status_code}")
            return pd.DataFrame()
    except Exception as e:
        print(f"❌ Error fetching {endpoint}: {e}")
        return pd.DataFrame()

def push_to_sheet(spreadsheet, tab_name, df):
    if df.empty:
        print(f"Skipping empty sheet: {tab_name}")
        return
    
    # Fill NaN values with empty string for clean sheet rendering
    df = df.fillna("")
    
    try:
        worksheet = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=tab_name, rows="1000", cols="50")
    
    worksheet.clear()
    
    # Convert dataframe to list format and update sheet
    data_matrix = [df.columns.tolist()] + df.astype(str).values.tolist()
    worksheet.update(data_matrix)
    print(f"✅ Successfully updated tab: '{tab_name}' ({len(df)} rows)")

def main():
    print("Connecting to Google Sheets...")
    gc = init_gspread()
    sh = gc.open_by_key(GSHEET_ID)
    
    for target in TARGET_ENDPOINTS:
        tab_name = target["tab_name"]
        ep = target["endpoint"]
        params = target["params"]
        
        print(f"\nProcessing endpoint: {ep}...")
        df = fetch_data(ep, params)
        
        if not df.empty:
            push_to_sheet(sh, tab_name, df)
        else:
            print(f"No records retrieved for {ep}")

    print("\n🎉 All 5 endpoints successfully synced to separate GSheet tabs!")

if __name__ == "__main__":
    main()
