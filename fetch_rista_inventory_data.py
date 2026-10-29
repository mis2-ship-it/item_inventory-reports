import json
import os
import time
from datetime import datetime

import jwt
import pandas as pd
import requests
import gspread
from google.oauth2.service_account import Credentials

RISTA_BASE_URL = os.environ.get("RISTA_BASE_URL", "https://api.ristaapps.com").rstrip("/")
API_KEY = os.environ.get("API_KEY")
SECRET_KEY = os.environ.get("SECRET_KEY")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "19z6KkVBFoLC33_wcNqVhDLyQEC2dDQ8YQE0gE38BhVg")
BRANCH_ID = os.environ.get("BRANCH_ID", "FZBBLR023")
DAY = os.environ.get("DAY") or datetime.utcnow().strftime("%Y-%m-%d")
PAGE = int(os.environ.get("PAGE", "1"))
SIZE = int(os.environ.get("SIZE", "50"))

def get_jwt_token():
    if not API_KEY or not SECRET_KEY:
        raise ValueError("API_KEY and SECRET_KEY are required.")
    payload = {"iss": API_KEY, "iat": int(time.time())}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def get_headers():
    return {
        "x-api-key": API_KEY,
        "x-api-token": get_jwt_token(),
        "Content-Type": "application/json",
    }

def normalize_data(raw):
    if raw is None:
        return pd.DataFrame()

    if isinstance(raw, dict):
        for key in ["data", "items", "results", "records", "content"]:
            value = raw.get(key)
            if isinstance(value, list):
                return pd.json_normalize(value)
            if isinstance(value, dict):
                return pd.json_normalize([value])
        return pd.DataFrame([raw])

    if isinstance(raw, list):
        return pd.json_normalize(raw)

    return pd.DataFrame()

def fetch_any_variant(endpoint_candidates, params_variants):
    for endpoint in endpoint_candidates:
        for params in params_variants:
            try:
                url = f"{RISTA_BASE_URL}/v1{endpoint}"
                response = requests.get(url, headers=get_headers(), params=params, timeout=30)
                print("TRY:", endpoint, params, "status:", response.status_code)
                print(response.text[:800])

                if response.status_code != 200:
                    continue

                data = response.json()
                df = normalize_data(data)

                # if response is valid but empty, still treat it as empty DataFrame
                if df.empty:
                    print(f"Endpoint {endpoint} returned empty data.")
                    return df, endpoint, params

                return df, endpoint, params

            except Exception as e:
                print("EXCEPTION:", endpoint, e)
                continue

    raise RuntimeError(f"No valid response found for indents endpoints: {endpoint_candidates}")

def clean_dataframe(df):
    if df.empty:
        return df
    out = df.copy()
    for col in out.columns:
        out[col] = out[col].apply(
            lambda value: ", ".join(str(v) for v in value) if isinstance(value, list)
            else json.dumps(value, ensure_ascii=False) if isinstance(value, dict)
            else "" if pd.isna(value) else str(value)
        )
    return out

def get_google_sheet_client():
    if not GOOGLE_CREDENTIALS:
        raise ValueError("GOOGLE_CREDENTIALS is required.")
    creds_info = json.loads(GOOGLE_CREDENTIALS)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
    )
    return gspread.authorize(creds)

def write_sheet(spreadsheet, tab_name, df):
    if df.empty:
        print(f"Writing empty tab: {tab_name}")
        # Create a simple empty sheet with one placeholder cell
        try:
            ws = spreadsheet.worksheet(tab_name)
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=tab_name, rows="1000", cols="20")
        ws.clear()
        ws.update("A1", [["No data returned for this endpoint"]])
        return

    cleaned = clean_dataframe(df)
    try:
        ws = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab_name, rows="1000", cols="50")

    ws.clear()
    rows = [cleaned.columns.tolist()] + cleaned.astype(str).values.tolist()
    ws.update(rows)

def main():
    endpoint_candidates = [
        "/inventory/indents/page",
        "/inventory/indent/page",
        "/inventory/indents",
        "/stockStatus",
        "/inventory/store/items"
        "/inventory/indent",
    ]

    param_variants = [
        {"branch": BRANCH_ID, "day": DAY, "page": PAGE, "size": SIZE},
        {"branchCode": BRANCH_ID, "day": DAY, "page": PAGE, "size": SIZE},
        {"branch": BRANCH_ID, "date": DAY, "page": PAGE, "size": SIZE},
        {"branchCode": BRANCH_ID, "date": DAY, "page": PAGE, "size": SIZE},
    ]

    print("Trying indents endpoint variants...")
    indents_df, used_endpoint, used_params = fetch_any_variant(endpoint_candidates, param_variants)

    print("Connecting to Google Sheet...")
    client = get_google_sheet_client()
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)

    write_sheet(spreadsheet, "Inventory_Indents_Page", indents_df)

    print("Finished.")
    print("Used endpoint:", used_endpoint)
    print("Used params:", used_params)

if __name__ == "__main__":
    main()
