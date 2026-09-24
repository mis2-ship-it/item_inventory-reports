import os
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo
import pandas as pd
import requests
import jwt

IST = ZoneInfo('Asia/Kolkata')
API_BASE = os.getenv('RISTA_API_BASE', 'https://api.ristaapps.com/v1')
API_KEY = os.getenv('API_KEY')
SECRET_KEY = os.getenv('SECRET_KEY')
EMAIL_USER = os.getenv('EMAIL_USER')
EMAIL_PASS = os.getenv('EMAIL_PASS')
EMAIL_TO = os.getenv('EMAIL_TO', '')
EMAIL_CC = os.getenv('EMAIL_CC', '')
REGIONS = ['KA', 'MH', 'TN', 'Kerela']
BRANDS = ['Frozen Bottle', 'Madno', 'Boba Bar']
CHANNELS = ['Swiggy', 'Zomato']

CHANNEL_SOURCE_MAP = {
    'swiggy frozen bottle': 'Swiggy', 'swiggy boba bar': 'Swiggy',
    'swiggy madno': 'Swiggy', 'swiggy lubov': 'Swiggy',
    'zomato boba bar': 'Zomato', 'zomato frozen bottle': 'Zomato',
    'zomato madno': 'Zomato', 'zomato lubov': 'Zomato',
}


def norm(v):
    return str(v or '').strip().lower().replace('–', '-').replace('—', '-').replace('_', '-').replace('  ', ' ')


def brand(v):
    s = norm(v)
    if 'frozen bottle' in s: return 'Frozen Bottle'
    if 'madno' in s: return 'Madno'
    if 'boba bar' in s: return 'Boba Bar'
    return str(v or '').strip()


def source(v):
    return CHANNEL_SOURCE_MAP.get(norm(v), '')


def token():
    now = datetime.now(IST)
    return jwt.encode({'iss': API_KEY, 'iat': int(now.timestamp())}, SECRET_KEY, algorithm='HS256')


def get(endpoint, params=None):
    r = requests.get(
        f'{API_BASE.rstrip("/")}/{endpoint.lstrip("/")}',
        headers={'x-api-key': API_KEY, 'x-api-token': token(), 'Content-Type': 'application/json'},
        params=params or {}, timeout=60)
    r.raise_for_status()
    return r.json()


def branches():
    data = get('/branch/list').get('data', [])
    out = []
    for r in data:
        if r.get('active', r.get('isActive', True)) is False:
            continue
        code = str(r.get('branchCode') or r.get('code') or '').strip()
        if code:
            out.append({'branchCode': code, 'branchName': r.get('branchName') or r.get('name') or code})
    return out


def sales_page(branch_code, day):
    rows, page = [], 1
    while True:
        data = get('/sales/page', {'branch': branch_code, 'day': day, 'page': page, 'limit': 5000}).get('data', [])
        if not isinstance(data, list): data = []
        rows.extend(data)
        if len(data) < 5000: break
        page += 1
    return rows


def outlet_master():
    f = os.getenv('OUTLET_MASTER_FILE', '').strip()
    if not f or not os.path.exists(f):
        return pd.DataFrame(columns=['branchCode', 'Store Name', 'Region'])
    x = pd.read_csv(f)
    return x[['branchCode', 'Store Name', 'Region']].drop_duplicates('branchCode')


def prepare(rows, master):
    if not rows: return pd.DataFrame()
    d = pd.json_normalize(rows)
    aliases = {
        'branchCode': ['branchCode'], 'branchName': ['branchName'],
        'invoiceNumber': ['invoiceNumber', 'invoiceNo'],
        'invoiceDate': ['invoiceDate', 'createdDate', 'modifiedDate'],
        'brandName': ['brandName'], 'channel': ['channel'],
        'fulfillmentStatus': ['fulfillmentStatus', 'status'],
        'cancelReason': ['cancelReason', 'cancellationReason', 'voidReason', 'reason'],
    }
    for target, names in aliases.items():
        if target not in d.columns:
            d[target] = next((d[n] for n in names if n in d.columns), '')
    d['Brand'] = d['brandName'].apply(brand)
    d['Source'] = d['channel'].apply(source)
    d['EventTime'] = pd.to_datetime(d['invoiceDate'], errors='coerce')
    d['EventTime'] = d['EventTime'].apply(lambda x: x.tz_localize(IST) if pd.notna(x) and x.tzinfo is None else (x.tz_convert(IST) if pd.notna(x) else x))
    d['Hour'] = d['EventTime'].dt.floor('h')
    d['Cancel Reason'] = d['cancelReason'].fillna('').astype(str).str.strip()
    d['Fulfillment Status'] = d['fulfillmentStatus'].fillna('').astype(str).str.strip()
    d['Problem'] = d['Fulfillment Status'].apply(norm).str.contains('cancel|reject|void', regex=True, na=False) | d['Cancel Reason'].apply(norm).str.contains('cancel|reject|void|store closed|store busy|out of stock|payment issue', regex=True, na=False)
    d = d.merge(master, on='branchCode', how='left')
    d['Store Name'] = d['Store Name'].fillna(d['branchName'])
    d['Region'] = d['Region'].fillna('').astype(str).str.strip()
    return d


def build_flow(d):
    d = d[d['Source'].isin(CHANNELS) & d['Brand'].isin(BRANDS) & d['Region'].isin(REGIONS)].copy()
    if d.empty: return pd.DataFrame()
    keys = ['Region', 'Store Name', 'branchCode', 'Brand', 'Source', 'Hour']
    normal = d[~d['Problem']].copy()
    normal['invoiceNumber'] = normal['invoiceNumber'].fillna('').astype(str).str.strip()
    normal = normal[normal['invoiceNumber'] != '']
    a = normal.groupby(keys, dropna=False)['invoiceNumber'].nunique().reset_index(name='Orders')
    bad = d[d['Problem']].copy()
    bad['invoiceNumber'] = bad['invoiceNumber'].fillna('').astype(str).str.strip()
    bad = bad[bad['invoiceNumber'] != '']
    b = bad.groupby(keys, dropna=False).agg(Problem_Orders=('invoiceNumber', 'nunique'), Cancel_Reasons=('Cancel Reason', lambda s: '; '.join(sorted({str(x).strip() for x in s if str(x).strip()})))).reset_index()
    x = a.merge(b, on=keys, how='outer')
    x['Orders'] = pd.to_numeric(x['Orders'], errors='coerce').fillna(0).astype(int)
    x['Problem_Orders'] = pd.to_numeric(x['Problem_Orders'], errors='coerce').fillna(0).astype(int)
    x['Cancel_Reasons'] = x['Cancel_Reasons'].fillna('')
    return x


def make_alerts(flow, hour):
    if flow.empty: return pd.DataFrame()
    keys = ['Region', 'Store Name', 'branchCode', 'Brand', 'Source']
    cur = flow[flow['Hour'] == hour]
    prev = flow[flow['Hour'] == hour - pd.Timedelta(hours=1)]
    candidates = pd.concat([cur[keys], prev[keys], cur.loc[cur['Problem_Orders'] > 0, keys]]).drop_duplicates()
    c = cur.groupby(keys, dropna=False).agg(Current_Orders=('Orders', 'sum'), Current_Problem=('Problem_Orders', 'sum'), Current_Reasons=('Cancel_Reasons', lambda s: '; '.join(sorted({str(x).strip() for x in s if str(x).strip()})))).reset_index()
    p = prev.groupby(keys, dropna=False)['Orders'].sum().reset_index(name='Previous_Orders')
    a = candidates.merge(c, on=keys, how='left').merge(p, on=keys, how='left')
    for col in ['Current_Orders', 'Current_Problem', 'Previous_Orders']:
        a[col] = pd.to_numeric(a[col], errors='coerce').fillna(0).astype(int)
    a['Current_Reasons'] = a['Current_Reasons'].fillna('')
    a = a[(a['Current_Orders'] == 0) & ((a['Previous_Orders'] > 0) | (a['Current_Problem'] > 0))].copy()
    a['Remarks'] = a.apply(lambda r: f"{r['Source']} {r['Brand']} no order from last one hour" + (f" | Cancel/Reject/Void: {r['Current_Reasons'] or 'recorded'}" if r['Current_Problem'] else ''), axis=1)
    a['Status'] = 'Alert'
    return a.sort_values(['Region', 'Store Name', 'Brand', 'Source'])


def esc(v):
    return str(v or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def region_table(d):
    stores = {}
    for _, r in d.iterrows():
        s = r['Store Name']
        stores.setdefault(s, {'Swiggy': {'Frozen Bottle': 0, 'Madno': 0, 'Boba Bar': 0}, 'Zomato': {'Frozen Bottle': 0, 'Madno': 0, 'Boba Bar': 0}, 'remarks': []})
        stores[s][r['Source']][r['Brand']] = 0
        if r['Remarks'] not in stores[s]['remarks']: stores[s]['remarks'].append(r['Remarks'])
    rows = ''.join(f'<tr style="background:#fff2cc;"><td><b style="color:#c00000;">{esc(s)}</b></td><td>{v["Swiggy"]["Frozen Bottle"]}</td><td>{v["Swiggy"]["Madno"]}</td><td>{v["Swiggy"]["Boba Bar"]}</td><td>{v["Zomato"]["Frozen Bottle"]}</td><td>{v["Zomato"]["Madno"]}</td><td>{v["Zomato"]["Boba Bar"]}</td><td style="color:#c00000;"><b>{"<br>".join(esc(x) for x in v["remarks"])}</b></td></tr>' for s, v in stores.items())
    return f'<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;font-family:Arial;font-size:12px;"><tr style="background:#d9eaf7;"><th rowspan="2">Store Name</th><th colspan="3">Swiggy Orders</th><th colspan="3">Zomato Orders</th><th rowspan="2">Remarks</th></tr><tr style="background:#eaf3f8;"><th>Frozen Bottle</th><th>Madno</th><th>Boba Bar</th><th>Frozen Bottle</th><th>Madno</th><th>Boba Bar</th></tr>{rows}</table>'


def email_html(alerts, hour):
    sections = ''.join(f'<h3>Region Wise: {esc(region)}</h3>{region_table(alerts[alerts["Region"] == region])}<br>' for region in REGIONS if not alerts[alerts['Region'] == region].empty)
    return f'<html><body style="font-family:Arial;"><h2>🚨 Hourly Order Flow Alert</h2><p><b>Date:</b> {hour.strftime("%d-%b-%Y")}<br><b>Hour:</b> {hour.strftime("%I:%M %p")} - {(hour+timedelta(hours=1)).strftime("%I:%M %p")}</p><p><b>Rule:</b> Only Status = Alert is published. Normal stores are not published.</p>{sections}<p style="font-size:11px;color:#666;">Store Name is highlighted for every Alert.</p></body></html>'


def send_mail(body, hour, count):
    to = [x.strip() for x in EMAIL_TO.split(',') if x.strip()]
    cc = [x.strip() for x in EMAIL_CC.split(',') if x.strip()]
    if not to and not cc: raise ValueError('EMAIL_TO / EMAIL_CC is empty')
    msg = MIMEMultipart('alternative')
    msg['From'] = EMAIL_USER
    msg['To'] = EMAIL_TO
    if EMAIL_CC: msg['Cc'] = EMAIL_CC
    msg['Subject'] = f'🚨 Hourly Order Flow Alert | {hour.strftime("%d-%b-%Y %I:%M %p")} | {count} Alert(s)'
    msg.attach(MIMEText(body, 'html'))
    with smtplib.SMTP('smtp.gmail.com', 587, timeout=60) as s:
        s.starttls(); s.login(EMAIL_USER, EMAIL_PASS); s.sendmail(EMAIL_USER, to + cc, msg.as_string())


def main():
    now = datetime.now(IST)
    hour = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    days = {hour.date(), (hour - timedelta(hours=1)).date()}
    rows = []
    for b in branches():
        for day in sorted(days):
            try: rows.extend(sales_page(b['branchCode'], day.strftime('%Y-%m-%d')))
            except Exception as e: print(f'⚠️ {b["branchCode"]} {day}: {e}')
    d = prepare(rows, outlet_master())
    if d.empty: print('No Sales Page data.'); return
    flow = build_flow(d)
    alerts = make_alerts(flow, pd.Timestamp(hour))
    if alerts.empty: print(f'✅ NORMAL | {hour:%d-%b-%Y %I:%M %p} | No alert.'); return
    print(alerts[['Region','Store Name','Brand','Source','Previous_Orders','Current_Orders','Current_Problem','Remarks']].to_string(index=False))
    send_mail(email_html(alerts, hour), hour, len(alerts))
    print('📩 Alert email sent.')


if __name__ == '__main__':
    main()
