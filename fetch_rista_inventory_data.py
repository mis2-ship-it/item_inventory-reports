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
        raise ValueError("API_KEY and SECRET_KEY environment variables are required.")
    payload = {"iss": API_KEY, "iat": int(time.time())}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def get_headers():
    return {
        "x-api-key": API_KEY,
        "x-api-token": get_jwt_token(),
        "Content-Type": "application/json",
    }


def normalize_data(raw):
    if isinstance(raw, dict):
        data = raw.get("data", raw.get("items", raw.get("results", raw.get("records", []))))
        if isinstance(data, list):
            return pd.json_normalize(data)
        if isinstance(data, dict):
            return pd.json_normalize([data])
        return pd.DataFrame([raw])

    if isinstance(raw, list):
        return pd.json_normalize(raw)

    return pd.DataFrame()


def fetch_rista_endpoint(endpoint, params=None):
    params = params or {}
    url = f"{RISTA_BASE_URL}/v1{endpoint}"
    response = requests.get(url, headers=get_headers(), params=params, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"Rista API request failed for {endpoint}: {response.status_code} - {response.text[:500]}")
    return normalize_data(response.json())


def clean_dataframe(df):
    if df.empty:
        return df

    cleaned = df.copy()
    for col in cleaned.columns:
        cleaned[col] = cleaned[col].apply(
            lambda value: ", ".join(str(v) for v in value) if isinstance(value, list)
            else json.dumps(value, ensure_ascii=False) if isinstance(value, dict)
            else "" if pd.isna(value) else str(value)
        )
    return cleaned


def get_google_sheet_client():
    if not GOOGLE_CREDENTIALS:
        raise ValueError("GOOGLE_CREDENTIALS environment variable is required.")

    creds_info = json.loads(GOOGLE_CREDENTIALS)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    return gspread.authorize(creds)


def write_dataframe_to_sheet(spreadsheet, tab_name, df):
    if df.empty:
        print(f"Skipping empty tab '{tab_name}'")
        return

    sheet = None
    try:
        sheet = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=tab_name, rows="1000", cols="50")

    cleaned = clean_dataframe(df)
    data = [cleaned.columns.tolist()] + cleaned.astype(str).values.tolist()
    sheet.clear()
    sheet.update(data)
    print(f"Updated worksheet '{tab_name}' with {len(cleaned)} rows.")


def main():
    params = {
        "branch": BRANCH_ID,
        "day": DAY,
        "page": PAGE,
        "size": SIZE,
    }

    print(f"Using day={DAY}")
    print("Fetching Rista inventory indents page...")
    indents_df = fetch_rista_endpoint("/inventory/indents/page", params=params)

    print("Fetching Rista item activity page...")
    activity_df = fetch_rista_endpoint("/inventory/item/activity/page", params=params)

    print(f"Connecting to Google Sheet ID: {GOOGLE_SHEET_ID}")
    client = get_google_sheet_client()
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)

    write_dataframe_to_sheet(spreadsheet, "Inventory_Indents_Page", indents_df)
    write_dataframe_to_sheet(spreadsheet, "Inventory_Item_Activity_Page", activity_df)

    print("Finished successfully.")


if __name__ == "__main__":
    main()
