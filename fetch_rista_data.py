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

# Active Store Identifiers
STORE_CODE = os.environ.get("STORE_CODE", "FZBBLR034")
BRANCH_NAME = os.environ.get("BRANCH_NAME", "AECS Layout")

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

# --- 1. Flatten Inventory Items & BOM Variants ---
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

# --- 2. Fetch Store Items & Robust Stock/Values Fetch ---
def fetch_store_items_with_stock(store_code, branch_name):
    # A. Fetch Base Store Items Metadata
    url_items = f"{RISTA_BASE_URL}/v1/inventory/store/items"
    items_list = []
    
    for identifier in [store_code, branch_name]:
        try:
            res_items = requests.get(url_items, headers=get_headers(), params={"storeCode": identifier}, timeout=30)
            if res_items.status_code == 200:
                js = res_items.json()
                items_list = js.get("data", js if isinstance(js, list) else [])
                if items_list:
                    print(f"Fetched store catalog metadata using '{identifier}'")
                    break
        except Exception as e:
            print(f"Error fetching catalog with '{identifier}': {e}")

    df_items = pd.json_normalize(items_list) if items_list else pd.DataFrame()

    # B. Robust POST Queries for Live Stock Quantities & Values
    stock_list = []
    stock_endpoints = [
        f"{RISTA_BASE_URL}/v1/inventory/item/stock",
        f"{RISTA_BASE_URL}/inventory/item/stock"
    ]
    
    # Payload matrix covering Rista identifier formats
    payload_options = [
        {"storeCode": store_code},
        {"branch": branch_name},
        {"branch": store_code},
        {"storeCode": branch_name},
        {"store": store_code},
        {"branchCode": store_code}
    ]

    for url in stock_endpoints:
        if stock_list:
            break
        for payload in payload_options:
            try:
                res_stock = requests.post(url, headers=get_headers(), json=payload, timeout=15)
                if res_stock.status_code == 200:
                    js_stock = res_stock.json()
                    extracted = js_stock.get("data", js_stock.get("items", js_stock.get("stock", js_stock if isinstance(js_stock, list) else [])))
                    if isinstance(extracted, list) and len(extracted) > 0:
                        stock_list = extracted
                        print(f"✅ Stock values retrieved from {url} using payload {payload}")
                        break
            except Exception:
                continue

    df_stock = pd.json_normalize(stock_list) if stock_list else pd.DataFrame()

    # C. Merge Stock/Value columns into Store Items Catalog
    if not df_items.empty and not df_stock.empty and "skuCode" in df_stock.columns:
        df_merged = pd.merge(df_items, df_stock, on="skuCode", how="left", suffixes=("", "_stock"))
        return df_merged
    elif not df_stock.empty:
        return df_stock
    return df_items

# --- Generic Fetch for Remaining Endpoints ---
def fetch_generic_endpoint(endpoint, key_name="data"):
    url = f"{RISTA_BASE_URL}{endpoint}"
    try:
        res = requests.get(url, headers=get_headers(), timeout=30)
        if res.status_code == 200:
            js = res.json()
            data = js.get(key_name, js if isinstance(js, list) else [])
            return pd.json_normalize(data)
    except Exception as e:
        print(f"Error fetching {endpoint}: {e}")
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

    print("\n1. Processing Inventory_Items (Flattening BOM)...")
    df_items = fetch_inventory_items()
    push_to_sheet(sh, "Inventory_Items", df_items)

    print(f"\n2. Processing Store_Items & Stock Values for Store '{STORE_CODE}' / '{BRANCH_NAME}'...")
    df_store_items = fetch_store_items_with_stock(STORE_CODE, BRANCH_NAME)
    push_to_sheet(sh, "Store_Items", df_store_items)

    print("\n3. Processing Store_List...")
    df_stores = fetch_generic_endpoint("/v1/inventory/store/list")
    push_to_sheet(sh, "Store_List", df_stores)

    print("\n4. Processing Supplier_List...")
    df_suppliers = fetch_generic_endpoint("/v1/inventory/supplier/list")
    push_to_sheet(sh, "Supplier_List", df_suppliers)

    print("\n5. Processing Supplier_Items...")
    df_sup_items = fetch_generic_endpoint("/v1/inventory/supplieritem/list")
    push_to_sheet(sh, "Supplier_Items", df_sup_items)

    print("\n🎉 Complete! All sheets updated with detailed values.")

if __name__ == "__main__":
    main()
