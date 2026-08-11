import os
import json
import time
import jwt
import pytz
import requests
import smtplib
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import gspread
from google.oauth2.service_account import Credentials

print("🚀 Rista Hourly Availability Email Dashboard Pipeline Started")

# =========================================================
# CONFIGURATION & AUTH
# =========================================================
API_KEY = os.environ["API_KEY"]
SECRET_KEY = os.environ["SECRET_KEY"]
RISTA_BASE_URL = "https://api.ristaapps.com/v1"

SPREADSHEET_ID = "130C3oQsVmONGVUulhGbDWroRKpkebwgnFhq3uiny_O0"
TARGET_HOURLY_TAB = "Hourly_Availability_Dashboard"

# =========================================================
# EMAIL CONFIGURATION & TEST RECIPIENTS
# =========================================================
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))

EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASSWORD")

# Hardcoded test recipients (add your testing email addresses here)
TO_EMAIL = "your_test_email@example.com"
CC_EMAIL = "your_cc_email@example.com"  # Set to "" if no CC is needed

# Warehouse Branch Codes
WH_BRANCH_CODES = ["90003-2221", "DW", "90003-2216", "90003-2218", "90003-2214", "90003-2215"]

session = requests.Session()

def get_token():
    payload = {"iss": API_KEY, "iat": int(time.time())}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def get_headers():
    return {
        "x-api-key": API_KEY,
        "x-api-token": get_token(),
        "content-type": "application/json"
    }

# Indian Number System Formatter (e.g. 1,22,202)
def format_inr(val):
    try:
        val = int(round(float(val)))
        is_negative = val < 0
        s = str(abs(val))
        if len(s) <= 3:
            res = s
        else:
            res = s[-3:]
            s = s[:-3]
            while len(s) > 2:
                res = s[-2:] + ',' + res
                s = s[:-2]
            res = s + ',' + res
        return ('-' if is_negative else '') + res
    except Exception:
        return "0"

# Google Credentials
google_creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS"])
scopes = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_info(google_creds_json, scopes=scopes)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SPREADSHEET_ID)

# IST Timezone setup
ist = pytz.timezone('Asia/Kolkata')
now_ist = datetime.now(ist)
today_str = now_ist.strftime("%Y-%m-%d")
time_str = now_ist.strftime("%I:%M %p IST")

print(f"📅 Running Hourly Availability for: {today_str} ({time_str})")

# =========================================================
# LOAD HELP SHEET & MAP STORES
# =========================================================
try:
    help_ws = spreadsheet.worksheet("Help_Sheet")
except Exception:
    help_ws = spreadsheet.worksheet("Help Sheet")

help_data = help_ws.get()
if not help_data:
    print("❌ Help Sheet Empty")
    exit()

max_cols = max(len(r) for r in help_data)
header_row = list(help_data[0]) + [""] * (max_cols - len(help_data[0]))
raw_headers = [str(h).strip().lower().replace(" ", "") for h in header_row]

safe_headers = []
for i, h in enumerate(raw_headers):
    h = f"blank_col_{i}" if not h else h
    if h in safe_headers:
        h = f"{h}_{i}"
    safe_headers.append(h)

rows = help_data[1:]
normalized_rows = [list(r) + [""] * (max_cols - len(r)) for r in rows]
help_df = pd.DataFrame(normalized_rows, columns=safe_headers)

branch_cols = [c for c in help_df.columns if "branchcode" in c]
store_cols = [c for c in help_df.columns if "storename" in c or "store" in c]
region_cols = [c for c in help_df.columns if "region" in c]
ownership_cols = [c for c in help_df.columns if "ownership" in c]

branch_col_name = branch_cols[0]
lookup_cols = [branch_col_name]
rename_dict = {branch_col_name: "branchCode"}

if store_cols:
    lookup_cols.append(store_cols[0])
    rename_dict[store_cols[0]] = "Store Name"
if region_cols:
    lookup_cols.append(region_cols[0])
    rename_dict[region_cols[0]] = "Region"

if ownership_cols:
    ownership_series = help_df[ownership_cols[0]].astype(str).str.upper().str.strip()
    branch_series = help_df[branch_col_name].astype(str).str.strip()
    is_coco_wh = ownership_series.str.contains("COCO|WARE|WH", na=False)
    is_explicit = branch_series.isin(WH_BRANCH_CODES)
    help_df = help_df[is_coco_wh | is_explicit].copy()

help_lookup = help_df[lookup_cols].copy().rename(columns=rename_dict)
help_lookup["branchCode"] = help_lookup["branchCode"].astype(str).str.strip()
help_lookup = help_lookup.drop_duplicates(subset=["branchCode"])

branches = help_lookup["branchCode"].loc[lambda x: x != ""].tolist()

def map_hourly_group(row):
    b_code = str(row["branchCode"]).strip()
    reg = str(row.get("Region", "")).strip().upper()
    
    if b_code in WH_BRANCH_CODES or "WH" in reg or "WAREHOUSE" in reg:
        if "KA" in reg or "KARNATAKA" in reg or "BLR" in reg or "ECITY" in reg or "2214" in b_code:
            return "WH_KA"
        elif "MH" in reg or "PUNE" in reg or "MUMBAI" in reg or "2216" in b_code or "2215" in b_code:
            return "WH_MH"
        elif "KERALA" in reg or "KER" in reg or "2221" in b_code:
            return "WH_Kerala"
        elif "DELHI" in reg or "NCR" in reg or "DW" in b_code:
            return "WH_NCR"
        elif "TN" in reg or "CHENNAI" in reg or "MAA" in reg or "2218" in b_code:
            return "WH_TN"
        return "WH_KA"
    
    if "KA" in reg or "KARNATAKA" in reg or "BLR" in reg:
        return "KA"
    elif "MH" in reg or "MAHARASHTRA" in reg or "PUNE" in reg or "MUMBAI" in reg:
        return "MH"
    elif "TN" in reg or "TAMIL" in reg or "CHENNAI" in reg:
        return "TN"
    elif "KER" in reg or "KERALA" in reg:
        return "Kerala"
    return reg if reg else "KA"

help_lookup["Hourly_Group"] = help_lookup.apply(map_hourly_group, axis=1)

# =========================================================
# FETCH HOURLY STORE DATA (ALL SKUs)
# =========================================================
def fetch_hourly_store(branch):
    all_records = []
    page = 1
    max_pages = 100
    
    while page <= max_pages:
        try:
            params = {
                "branch": branch,
                "day": today_str,
                "date": today_str,
                "page": page,
                "pageNo": page,
                "limit": 100,
                "count": 100,
                "pageSize": 100,
                "size": 100
            }
            res = session.get(
                f"{RISTA_BASE_URL}/inventory/item/activity/page",
                headers=get_headers(),
                params=params,
                timeout=20
            )
            if res.status_code == 200:
                data = res.json().get("data", [])
                if not data or len(data) == 0:
                    break
                all_records.extend(data)
                page += 1
            else:
                break
        except Exception:
            break

    if all_records:
        df = pd.json_normalize(all_records)
        df["branchCode"] = branch
        df["activityDate"] = today_str
        
        if "activities" in df.columns:
            df = df.dropna(subset=["activities"]).copy()
            df = df.explode("activities").reset_index(drop=True)
            activities_df = pd.json_normalize(df["activities"]).add_prefix("activity_")
            df = pd.concat([df.drop(columns=["activities"]), activities_df], axis=1)
            
        return df
    return None

print(f"⚡ Fetching Hourly Availability for {len(branches)} stores...")
hourly_dfs = []

with ThreadPoolExecutor(max_workers=12) as executor:
    futures = {executor.submit(fetch_hourly_store, b): b for b in branches}
    for future in as_completed(futures):
        res_df = future.result()
        if res_df is not None and not res_df.empty:
            hourly_dfs.append(res_df)

if not hourly_dfs:
    print("⚠️ Today's date returned no records yet. Checking yesterday fallback...")
    fallback_date = (now_ist - timedelta(days=1)).strftime("%Y-%m-%d")
    today_str = fallback_date
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(fetch_hourly_store, b): b for b in branches}
        for future in as_completed(futures):
            res_df = future.result()
            if res_df is not None and not res_df.empty:
                hourly_dfs.append(res_df)

if not hourly_dfs:
    print("❌ No hourly availability data retrieved.")
    exit()

final_df = pd.concat(hourly_dfs, ignore_index=True)
final_df["branchCode"] = final_df["branchCode"].astype(str).str.strip()
final_df = final_df.merge(help_lookup, on="branchCode", how="left")

num_fields = ["activity_quantity", "activity_cost", "openingBalance", "openingCost", "closingBalance", "closingCost"]
for col in num_fields:
    if col in final_df.columns:
        final_df[col] = pd.to_numeric(final_df[col], errors="coerce").fillna(0.0)
    else:
        final_df[col] = 0.0

final_df["activity_quantity"] = final_df["activity_quantity"].abs()
final_df["activity_cost"] = final_df["activity_cost"].abs()

# Differentiate Outbound Activity
act_type_clean = final_df["activity_type"].astype(str).str.strip().str.upper()
is_wh_store = final_df["branchCode"].isin(WH_BRANCH_CODES)
is_retail_outbound = (~is_wh_store) & (act_type_clean == "SALES")
is_wh_outbound = is_wh_store & act_type_clean.str.contains("TRANSFER OUT|TRANSFEROUT|TRANSFER_OUT", na=False)

outbound_df = final_df[is_retail_outbound | is_wh_outbound].copy()

# =========================================================
# AGGREGATIONS & METRICS
# =========================================================
closing_grp = final_df.groupby(["Hourly_Group", "Region", "branchCode", "Store Name"], as_index=False)[["closingCost", "closingBalance"]].sum()
outbound_grp = outbound_df.groupby(["Hourly_Group", "Region", "branchCode", "Store Name"], as_index=False)[["activity_cost", "activity_quantity"]].sum()

store_metrics = closing_grp.merge(outbound_grp, on=["Hourly_Group", "Region", "branchCode", "Store Name"], how="left").fillna(0.0)

store_metrics["Availability_Cost_Pct"] = np.where(
    store_metrics["closingCost"] > 0, 
    (1 - (store_metrics["activity_cost"] / store_metrics["closingCost"])), 
    0.0
)

total_stores = int(len(store_metrics))
total_closing_cost = float(store_metrics["closingCost"].sum())
avg_availability_pct = float(store_metrics["Availability_Cost_Pct"].mean()) * 100
below_30_count = int((store_metrics["Availability_Cost_Pct"] < 0.30).sum())
between_30_50_count = int(((store_metrics["Availability_Cost_Pct"] >= 0.30) & (store_metrics["Availability_Cost_Pct"] < 0.50)).sum())
above_50_count = int((store_metrics["Availability_Cost_Pct"] >= 0.50).sum())

coco_groups = ["KA", "MH", "TN", "Kerala"]
wh_groups = ["WH_KA", "WH_MH", "WH_Kerala", "WH_NCR", "WH_TN"]

def calculate_group_summary(groups_list):
    group_df = store_metrics[store_metrics["Hourly_Group"].isin(groups_list)].groupby("Hourly_Group", as_index=False).agg({
        "closingCost": "sum",
        "activity_cost": "sum",
        "branchCode": "count"
    })
    group_df["Availability_Pct"] = np.where(group_df["closingCost"] > 0, (1 - (group_df["activity_cost"] / group_df["closingCost"])) * 100, 0.0)
    return group_df

coco_summary_df = calculate_group_summary(coco_groups)
wh_summary_df = calculate_group_summary(wh_groups)

# Helper for availability badges (whole percentage)
def get_status_badge(pct_val):
    rounded_pct = int(round(pct_val))
    if pct_val < 30:
        return f'<span style="background-color: #FADBD8; color: #78281F; padding: 3px 8px; border-radius: 4px; font-weight: bold;">{rounded_pct}%</span>'
    elif pct_val < 50:
        return f'<span style="background-color: #FCF3CF; color: #7D6608; padding: 3px 8px; border-radius: 4px; font-weight: bold;">{rounded_pct}%</span>'
    else:
        return f'<span style="background-color: #D4EFDF; color: #145A32; padding: 3px 8px; border-radius: 4px; font-weight: bold;">{rounded_pct}%</span>'

# =========================================================
# GENERATE STRUCTURED HTML EMAIL BODY
# =========================================================
html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; background-color: #f4f6f9; margin: 0; padding: 20px; color: #333; }}
        .container {{ max-width: 850px; margin: 0 auto; background: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.08); }}
        .header {{ background-color: #1F3A5F; color: #ffffff; padding: 20px; text-align: center; }}
        .header h2 {{ margin: 0; font-size: 22px; font-weight: 600; letter-spacing: 0.5px; }}
        .header p {{ margin: 5px 0 0 0; font-size: 13px; opacity: 0.85; }}
        .content {{ padding: 25px; }}
        .section-title {{ font-size: 16px; font-weight: bold; color: #1F3A5F; border-bottom: 2px solid #1F3A5F; padding-bottom: 5px; margin-top: 25px; margin-bottom: 15px; text-transform: uppercase; }}
        
        /* KPI Grid */
        .kpi-table {{ width: 100%; border-collapse: separate; border-spacing: 10px; margin-bottom: 20px; }}
        .kpi-card {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px; text-align: center; }}
        .kpi-label {{ font-size: 11px; text-transform: uppercase; color: #64748b; font-weight: 600; margin-bottom: 4px; }}
        .kpi-val {{ font-size: 20px; font-weight: bold; color: #0f172a; }}
        
        /* Data Tables */
        table.data-table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 13px; }}
        table.data-table th {{ background-color: #f1f5f9; color: #334155; text-align: left; padding: 10px; font-weight: 600; border-bottom: 2px solid #cbd5e1; }}
        table.data-table td {{ padding: 9px 10px; border-bottom: 1px solid #e2e8f0; color: #334155; }}
        table.data-table tr:nth-child(even) {{ background-color: #f8fafc; }}
        .text-right {{ text-align: right; }}
        .text-center {{ text-align: center; }}
        .region-header {{ background-color: #e2e8f0 !important; font-weight: bold; color: #1e293b; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>HOURLY AVAILABILITY DASHBOARD</h2>
            <p>Report Generated: {today_str} | {time_str}</p>
        </div>
        <div class="content">
            
            <!-- TOP KPI BOXES -->
            <table class="kpi-table">
                <tr>
                    <td class="kpi-card" width="33%">
                        <div class="kpi-label">Active Stores</div>
                        <div class="kpi-val">{total_stores}</div>
                    </td>
                    <td class="kpi-card" width="33%">
                        <div class="kpi-label">Total Closing Stock</div>
                        <div class="kpi-val">₹{format_inr(total_closing_cost)}</div>
                    </td>
                    <td class="kpi-card" width="33%">
                        <div class="kpi-label">Avg Availability</div>
                        <div class="kpi-val">{int(round(avg_availability_pct))}%</div>
                    </td>
                </tr>
                <tr>
                    <td class="kpi-card" style="background-color: #FADBD8;">
                        <div class="kpi-label" style="color: #78281F;">🔴 Less Than 30%</div>
                        <div class="kpi-val" style="color: #78281F;">{below_30_count} Stores</div>
                    </td>
                    <td class="kpi-card" style="background-color: #FCF3CF;">
                        <div class="kpi-label" style="color: #7D6608;">🟡 30% to 50%</div>
                        <div class="kpi-val" style="color: #7D6608;">{between_30_50_count} Stores</div>
                    </td>
                    <td class="kpi-card" style="background-color: #D4EFDF;">
                        <div class="kpi-label" style="color: #145A32;">🟢 50% and Above</div>
                        <div class="kpi-val" style="color: #145A32;">{above_50_count} Stores</div>
                    </td>
                </tr>
            </table>

            <!-- GROUP COCO SUMMARY -->
            <div class="section-title">Group COCO - Region Summary</div>
            <table class="data-table">
                <thead>
                    <tr>
                        <th>Region</th>
                        <th class="text-center">Stores</th>
                        <th class="text-right">Outbound Cost (₹)</th>
                        <th class="text-right">Closing Stock (₹)</th>
                        <th class="text-center">Availability %</th>
                    </tr>
                </thead>
                <tbody>
"""

for _, r in coco_summary_df.iterrows():
    avail_pct = float(r["Availability_Pct"])
    html_content += f"""
                    <tr>
                        <td><b>{r['Hourly_Group']}</b></td>
                        <td class="text-center">{int(r['branchCode'])}</td>
                        <td class="text-right">₹{format_inr(r['activity_cost'])}</td>
                        <td class="text-right">₹{format_inr(r['closingCost'])}</td>
                        <td class="text-center">{get_status_badge(avail_pct)}</td>
                    </tr>"""

html_content += """
                </tbody>
            </table>

            <!-- WAREHOUSE SUMMARY -->
            <div class="section-title">Warehouse Summary</div>
            <table class="data-table">
                <thead>
                    <tr>
                        <th>Warehouse Region</th>
                        <th class="text-center">Stores</th>
                        <th class="text-right">Transfer Out Cost (₹)</th>
                        <th class="text-right">Closing Stock (₹)</th>
                        <th class="text-center">Availability %</th>
                    </tr>
                </thead>
                <tbody>
"""

for _, r in wh_summary_df.iterrows():
    avail_pct = float(r["Availability_Pct"])
    html_content += f"""
                    <tr>
                        <td><b>{r['Hourly_Group']}</b></td>
                        <td class="text-center">{int(r['branchCode'])}</td>
                        <td class="text-right">₹{format_inr(r['activity_cost'])}</td>
                        <td class="text-right">₹{format_inr(r['closingCost'])}</td>
                        <td class="text-center">{get_status_badge(avail_pct)}</td>
                    </tr>"""

html_content += """
                </tbody>
            </table>

            <!-- STORE DETAILED BREAKDOWN -->
            <div class="section-title">Region & Store Wise Availability Details</div>
            <table class="data-table">
                <thead>
                    <tr>
                        <th>Branch Code</th>
                        <th>Store Name</th>
                        <th class="text-right">Outbound/Transfer Cost (₹)</th>
                        <th class="text-right">Closing Cost (₹)</th>
                        <th class="text-center">Availability %</th>
                    </tr>
                </thead>
                <tbody>
"""

all_groups = coco_groups + wh_groups
for grp in all_groups:
    grp_stores = store_metrics[store_metrics["Hourly_Group"] == grp].sort_values(by="Store Name")
    if grp_stores.empty:
        continue
    
    html_content += f"""
                    <tr class="region-header">
                        <td colspan="5">📍 REGION: {grp}</td>
                    </tr>"""
    
    for _, sr in grp_stores.iterrows():
        avail_pct = float(sr["Availability_Cost_Pct"]) * 100
        html_content += f"""
                    <tr>
                        <td>{sr['branchCode']}</td>
                        <td>{sr['Store Name']}</td>
                        <td class="text-right">₹{format_inr(sr['activity_cost'])}</td>
                        <td class="text-right">₹{format_inr(sr['closingCost'])}</td>
                        <td class="text-center">{get_status_badge(avail_pct)}</td>
                    </tr>"""

html_content += """
                </tbody>
            </table>

        </div>
    </div>
</body>
</html>
"""

# =========================================================
# SEND EMAIL DASHBOARD TO OPS TEAM
# =========================================================
# =========================================================
# CONFIGURATION & RECIPIENTS (TESTING MODE)
# =========================================================
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASSWORD")

# Hardcoded test recipients
TO_EMAIL = "your_test_email@example.com"  # Replace with your primary email
CC_EMAIL = "your_cc_email@example.com"    # Replace with CC email or leave as "" if none

# =========================================================
# SEND EMAIL DASHBOARD FUNCTION
# =========================================================
def send_email_dashboard(html_body):
    if not EMAIL_USER or not EMAIL_PASS:
        print("⚠️ Sender credentials (EMAIL_USER / EMAIL_PASS) missing in environment.")
        return

    # Process TO and CC lists
    to_list = [e.strip() for e in TO_EMAIL.split(",") if e.strip()]
    cc_list = [e.strip() for e in CC_EMAIL.split(",") if e.strip()] if CC_EMAIL else []
    all_recipients = to_list + cc_list

    if not all_recipients:
        print("⚠️ No valid recipient email addresses provided.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📊 Hourly Availability Dashboard — {today_str} ({time_str})"
    msg["From"] = EMAIL_USER
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)

    msg.attach(MIMEText(html_body, "html"))

    try:
        print(f"📧 Sending email dashboard to TO: {to_list} | CC: {cc_list}...")
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, all_recipients, msg.as_string())
        server.quit()
        print("✅ Hourly Availability Dashboard Email sent successfully!")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

send_email_dashboard(html_content)

print("🏁 Hourly Availability Dashboard & Email Delivery Pipeline Complete!")
