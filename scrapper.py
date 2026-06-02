# scraper.py
import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
import time
import os
import json

AMAZON_SEARCH_URLS = [
    "https://www.amazon.ae/s?k=grooming+products+men&i=beauty",
    "https://www.amazon.ae/s?k=electric+shaver+trimmer&i=beauty",
    "https://www.amazon.ae/s?k=beard+trimmer+uae&i=beauty",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-AE,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

SPREADSHEET_ID = "16asI7EmNgjN6QCWhXzj8pVC5ZClqtimjkP6tQqbnSqI"
SHEET_GID_NAME = "Sheet1"  # Change to your actual tab name if different


def scrape_amazon_grooming():
    """Scrape grooming products from Amazon UAE search pages."""
    all_products = []

    for url in AMAZON_SEARCH_URLS:
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(response.text, "html.parser")
            results = soup.select('[data-component-type="s-search-result"]')

            for item in results:
                asin = item.get("data-asin", "")
                if not asin:
                    continue

                title_el = item.select_one("h2 span")
                title = title_el.get_text(strip=True) if title_el else ""

                price_whole = item.select_one(".price-whole")
                price_frac = item.select_one(".price-fraction")
                if price_whole:
                    price = f"AED {price_whole.get_text(strip=True)}"
                    if price_frac:
                        price += f".{price_frac.get_text(strip=True)}"
                else:
                    price = "N/A"

                rating_el = item.select_one('[aria-label*="out of 5 stars"]')
                rating = "N/A"
                if rating_el:
                    label = rating_el.get("aria-label", "")
                    rating = label.replace(" out of 5 stars", "").strip()

                if title and asin:
                    all_products.append({
                        "asin": asin,
                        "company": title,
                        "price_aed": price,
                        "rating_out_of_5": rating,
                        "link": f"https://www.amazon.ae/dp/{asin}",
                        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })

            print(f"[Scraper] Got {len(results)} items from: {url}")
            time.sleep(3)  # Be polite — don't hammer Amazon

        except Exception as e:
            print(f"[Scraper] Failed on {url}: {e}")

    # Deduplicate by ASIN
    seen = set()
    unique = []
    for p in all_products:
        if p["asin"] not in seen:
            seen.add(p["asin"])
            unique.append(p)

    print(f"[Scraper] Total unique products scraped: {len(unique)}")
    return unique


def get_gspread_client():
    """Authenticate with Google Sheets using service account credentials."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    # Support credentials as a JSON string env var (for Render) OR a local file
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)

    return gspread.authorize(creds)


def sync_to_sheet(products):
    """Write only NEW products (by ASIN) to the Google Sheet."""
    if not products:
        print("[SheetSync] Nothing to sync.")
        return

    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_GID_NAME)

        # Get all existing ASINs from column E (adjust if your layout differs)
        existing = sheet.col_values(1)  # Column A = ASIN
        existing_asins = set(existing[1:])  # Skip header row

        new_rows = [
            [p["asin"], p["company"], p["price_aed"], p["rating_out_of_5"],
             p["link"], p["scraped_at"]]
            for p in products if p["asin"] not in existing_asins
        ]

        if not new_rows:
            print("[SheetSync] No new products to add.")
            return

        sheet.append_rows(new_rows, value_input_option="RAW")
        print(f"[SheetSync] Added {len(new_rows)} new products to sheet.")

    except Exception as e:
        print(f"[SheetSync] Error writing to sheet: {e}")


def run_scrape_and_sync():
    """Full pipeline: scrape Amazon → sync new products to Google Sheet."""
    print("[Scraper] Starting scrape-and-sync pipeline...")
    products = scrape_amazon_grooming()
    sync_to_sheet(products)
    print("[Scraper] Pipeline complete.")