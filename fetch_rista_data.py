import os
import json
import time
import jwt
import requests
import pandas as pd
import gspread
from datetime import datetime
from google.oauth2.service_account import Credentials

# --- Environment & Configuration ---
GSHEET_ID = "1_IX_TKer13kKMTjGtw94FcCdRUt_x_C66kSkotuZPV0"
RISTA_BASE_URL = os.environ.get("RISTA_BASE_URL", "https://api.ristaapps.com").rstrip("/")
API_KEY = os.environ.get("API_KEY")
SECRET_KEY = os.environ.get("SECRET_KEY")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS")

# Dynamic Parameters
BRANCH = os.environ.get("BRANCH", os.environ.get("STORE_CODE", "AECS Layout"))
CHANNEL = os.environ.get("CHANNEL", "DineIn")
TODAY_STR = datetime.now().strftime("%Y-%m-%d")
DAY_VAL = os.environ.get("DAY_VAL", TODAY_STR)

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

def clean_data_for_gsheet(df):
    """Converts nested dicts/lists into clean comma-separated strings."""
    if df.empty:
        return df
    
    df = df.copy()
    for col in df.columns:
        df[col] = df[col].apply(
            lambda val: ", ".join([str(x) for x in val]) if isinstance(val, list)
            else json.dumps(val) if isinstance(val, dict)
            else "" if pd.isna(val) else str(val)
        )
    return df

# --- Flattened Inventory Items ---
def fetch_inventory_items():
    url = f"{RISTA_BASE_URL}/v1/inventory/items"
    try:
        res = requests.get(url, headers=get_headers(), timeout=30)
        if res.status_code != 200:
            return pd.DataFrame()
        
        raw_data = res.json().get("items", [])
        flattened_rows = []

        for item in raw_data:
            base_info = {
                "skuCode": item.get("skuCode", ""),
                "name": item.get("name", ""),
                "category": item.get("category", ""),
                "subCategory": item.get("subCategory", ""),
                "itemType": item.get("itemType", ""),
                "inventoryItemNature": item.get("inventoryItemNature", ""),
                "itemNature": item.get("itemNature", ""),
                "measuringUnit": item.get("measuringUnit", ""),
                "trackingMethod": item.get("trackingMethod", ""),
                "valuationMethod": item.get("valuationMethod", ""),
                "trackInventory": item.get("trackInventory", ""),
                "perishable": item.get("perishable", ""),
                "allowFraction": item.get("allowFraction", ""),
                "barCode": item.get("barCode", ""),
                "itemTaxCode": item.get("itemTaxCode", ""),
                "shelfLife": item.get("shelfLife", ""),
            }

            bom_variants = item.get("bomVariants", [])
            if bom_variants:
                for variant in bom_variants:
                    v_id = variant.get("variantId", "")
                    v_yield = variant.get("bomYield", "")
                    v_channels = variant.get("channels", "")
                    v_stores = variant.get("stores", "")
                    materials = variant.get("materials", [])

                    if materials:
                        for mat in materials:
                            row = base_info.copy()
                            row.update({
                                "bom_variantId": v_id,
                                "bom_yield": v_yield,
                                "bom_channels": v_channels,
                                "bom_stores": v_stores,
                                "material_skuCode": mat.get("skuCode", ""),
                                "material_quantity": mat.get("quantity", ""),
                                "material_unit": mat.get("measuringUnit", ""),
                                "material_consumptionUnit": mat.get("consumptionUnit", ""),
                                "material_yield": mat.get("materialYield", "")
                            })
                            flattened_rows.append(row)
                    else:
                        row = base_info.copy()
                        row.update({
                            "bom_variantId": v_id, "bom_yield": v_yield, "bom_channels": v_channels,
                            "bom_stores": v_stores, "material_skuCode": "", "material_quantity": "",
                            "material_unit": "", "material_consumptionUnit": "", "material_yield": ""
                        })
                        flattened_rows.append(row)
            else:
                row = base_info.copy()
                row.update({
                    "bom_variantId": "", "bom_yield": "", "bom_channels": "", "bom_stores": "",
                    "material_skuCode": "", "material_quantity": "", "material_unit": "",
                    "material_consumptionUnit": "", "material_yield": ""
                })
                flattened_rows.append(row)

        return pd.DataFrame(flattened_rows)
    except Exception as e:
        print(f"Error fetching inventory items: {e}")
        return pd.DataFrame()

# --- Endpoint Fetch with Path Version Fallbacks ---
def fetch_endpoint(endpoint, params=None):
    if params is None:
        params = {}
        
    clean_params = {k: v for k, v in params.items() if v}
    paths_to_try = [
        f"{RISTA_BASE_URL}{endpoint}",
        f"{RISTA_BASE_URL}/v1{endpoint}" if not endpoint.startswith("/v1") else f"{RISTA_BASE_URL}{endpoint.replace('/v1', '')}"
    ]

    for url in paths_to_try:
        try:
            res = requests.get(url, headers=get_headers(), params=clean_params, timeout=20)
            if res.status_code == 200:
                js = res.json()
                if isinstance(js, dict):
                    data = js.get("data", js.get("items", js.get("catalog", js.get("resources", js))))
                else:
                    data = js
                
                if isinstance(data, list):
                    return pd.json_normalize(data)
                elif isinstance(data, dict):
                    return pd.json_normalize([data])
        except Exception:
            continue

    print(f"⚠️ Could not retrieve data for {endpoint}")
    return pd.DataFrame()

def push_to_sheet(spreadsheet, tab_name, df):
    if df.empty:
        print(f"Skipping empty sheet: {tab_name}")
        return

    df_clean = clean_data_for_gsheet(df)

    try:
        worksheet = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=tab_name, rows="1000", cols="50")

    worksheet.clear()
    data_matrix = [df_clean.columns.tolist()] + df_clean.astype(str).values.tolist()
    worksheet.update(data_matrix)
    print(f"✅ Updated tab: '{tab_name}' ({len(df_clean)} rows, {len(df_clean.columns)} columns)")

def main():
    print("Connecting to Google Sheets...")
    gc = init_gspread()
    sh = gc.open_by_key(GSHEET_ID)

    # 1. Retained Inventory Endpoints
    print("\n--- INVENTORY ENDPOINTS ---")
    print("Fetching Store List...")
    df_store_list = fetch_endpoint("/v1/inventory/store/list")
    push_to_sheet(sh, "Store_List", df_store_list)

    print("Fetching Inventory Items (Flattened BOM)...")
    df_inv_items = fetch_inventory_items()
    push_to_sheet(sh, "Inventory_Items", df_inv_items)

    # 2. Catalog GET Endpoints
    print("\n--- CATALOG ENDPOINTS ---")
    catalog_targets = [
        {"tab": "Resource_List", "ep": "/resource", "params": {"branch": BRANCH}},
        {"tab": "Catalog_Branch", "ep": "/catalog", "params": {"branch": BRANCH, "channel": CHANNEL}},
        {"tab": "Catalog_Enterprise", "ep": "/catalog/enterprise", "params": {}},
        {"tab": "Catalog_Sync_Status", "ep": "/catalog/sync/status", "params": {}},
        {"tab": "Catalog_Item_List", "ep": "/catalog/item/list", "params": {}},
        {"tab": "Soldout_Items", "ep": "/items/soldout", "params": {"branch": BRANCH}},
        {"tab": "Soldout_History", "ep": "/items/soldout/history", "params": {"branch": BRANCH, "day": DAY_VAL}}
    ]

    for target in catalog_targets:
        tab = target["tab"]
        ep = target["ep"]
        params = target["params"]
        print(f"Fetching {ep} -> Tab: '{tab}'...")
        df = fetch_endpoint(ep, params)
        push_to_sheet(sh, tab, df)

    print("\n🎉 Sync Complete! Inventory and Catalog endpoints populated in separate tabs.")

if __name__ == "__main__":
    main()
