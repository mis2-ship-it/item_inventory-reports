import os
import re
import json
import time
import requests
import gspread

# --- CONFIG ---
SHEET_URL = "https://docs.google.com/spreadsheets/d/1svixmiOhtlWvR0zQxQJt1w8pkZGCxrAUcjT4vQO7umM/edit"
WORKSHEET_NAME = "Zomato"

# Check if running in GitHub Actions (Secret) or Local machine (credentials.json)
if os.environ.get("GCP_SA_KEY"):
    creds_dict = json.loads(os.environ["GCP_SA_KEY"])
    gc = gspread.service_account_from_dict(creds_dict)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    CRED_PATH = os.path.join(BASE_DIR, "credentials.json")
    gc = gspread.service_account(filename=CRED_PATH)
    
sh = gc.open_by_url(SHEET_URL)
ws = sh.worksheet(WORKSHEET_NAME)

rows = ws.get_all_values()
headers = rows[0]

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.zomato.com/"
})

for r_idx in range(2, len(rows) + 1):
    row_data = rows[r_idx - 1]
    res_id = str(row_data[0]).strip()
    url = str(row_data[6]).strip()

    if not url and not res_id:
        continue

    # Skip if already scraped with OK status and valid rating & count
    existing_rating = row_data[7] if len(row_data) > 7 else ""
    existing_count = row_data[8] if len(row_data) > 8 else ""
    existing_status = row_data[9] if len(row_data) > 9 else ""
    if existing_status == "OK" and existing_rating and existing_count:
        continue

    print(f"Scraping Row {r_idx}: Res ID {res_id} ...", end=" ", flush=True)
    rating, count, status = "", "", "ERROR"

    try:
        final_url = url if url else f"https://zoma.to/r/{res_id}"
        if "zoma.to/r/" in final_url:
            head_resp = session.head(final_url, allow_redirects=True, timeout=10)
            final_url = head_resp.url

        resp = session.get(final_url, timeout=15)
        html = resp.text

        # -------------------------------------------------------------
        # 1. COUNT: Grab count directly above 'Delivery Ratings'
        # -------------------------------------------------------------
        m_count = re.search(r'>\s*([0-9,]+(?:\s*[KkMm])?)\s*</div>\s*<div[^>]*>\s*Delivery\s*Ratings\s*</div>', html, re.I)
        if m_count:
            count = m_count.group(1).strip()
        else:
            m_del_count = re.search(r'"reviewCount":\s*"([^"]+)"', html)
            if m_del_count:
                count = m_del_count.group(1).strip()

        # -------------------------------------------------------------
        # 2. RATING: Grab Delivery Rating
        # -------------------------------------------------------------
        # Pattern A: Delivery Rating Box (HTML contains color="#267E3E" or preceding Delivery Ratings)
        # We capture the outer block containing "Delivery Ratings" and inspect all rating digits inside it
        m_box = re.search(r'(<div[^>]*color="#267E3E"[\s\S]*?Delivery\s*Ratings</div>)', html, re.I)
        if m_box:
            r_match = re.search(r'>\s*([1-5]\.[0-9])\s*<', m_box.group(1))
            if r_match:
                rating = r_match.group(1).strip()

        # Pattern B: Look backward up to 1200 characters from 'Delivery Ratings'
        if not rating:
            pos = html.lower().find("delivery ratings")
            if pos != -1:
                chunk = html[max(0, pos - 1200):pos]
                # Match any div containing a float rating e.g. >4.3<
                all_r = re.findall(r'>\s*([1-5]\.[0-9])\s*<', chunk)
                if all_r:
                    # Last float right before 'Delivery Ratings' is the delivery score
                    rating = all_r[-1].strip()

        # Pattern C: Check direct JSON key
        if not rating:
            m_json = re.search(r'"deliveryRating":\s*\{[^}]*?"rating":\s*"?([1-5]\.[0-9])"?', html) or \
                     re.search(r'"DELIVERY":\s*\{[^}]*?"rating":\s*"([1-5]\.[0-9])"', html)
            if m_json:
                rating = m_json.group(1).strip()

        # Set status
        if rating and count:
            status = "OK"
        elif rating:
            status = "ONLY_RATING"
        elif count:
            status = "ONLY_COUNT"
        else:
            status = "NO_RATING"

    except Exception as e:
        status = f"ERROR: {str(e)[:30]}"

    # Write back to Google Sheet: H (8), I (9), J (10), K (11)
    timestamp = time.strftime("%d %b %Y, %I:%M %p")
    ws.update(
        range_name=f"H{r_idx}:K{r_idx}",
        values=[[rating, count, status, timestamp]]
    )

    print(f"-> Result: {rating} ★ ({count} reviews) | {status}")
    time.sleep(1.2)

print("\nCompleted successfully!")
