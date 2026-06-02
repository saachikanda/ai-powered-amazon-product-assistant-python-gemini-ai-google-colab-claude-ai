# scraper2.py — Smart AI Scraper (Auto Single/Multi Page + Retry Logic)
import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ───────────────────────────────────────────────────────────────────
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config=genai.GenerationConfig(temperature=0)
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

OUTPUT_DIR          = "outputs"
PROGRESS_FILE       = os.path.join(OUTPUT_DIR, "progress.json")
PAGES_PER_RUN = 2       # was 5, now only 2 pages per run
CHUNK_SIZE    = 15000   # was 50000, now smaller chunks = less overwhelming
DELAY_BETWEEN_PAGES = 15  # was 8, now 15 seconds between pages
TOTAL_PAGES         = 300

# Sites that are always single-page (no pagination needed)
SINGLE_PAGE_DOMAINS = [
    "wikipedia.org", "wikimedia.org",
    "medium.com", "britannica.com",
    "investopedia.com",
]

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── DETECT SINGLE VS MULTI PAGE ──────────────────────────────────────────────
def is_single_page_site(url):
    """
    Returns True if site should be treated as single page.
    Checks known single-page domains + whether URL already has pagination params.
    """
    domain = urlparse(url).netloc.lower()

    # Known single-page domains
    for d in SINGLE_PAGE_DOMAINS:
        if d in domain:
            return True

    # If URL has no page-like params and is not a search/listing page → single
    has_pagination_param = bool(re.search(
        r'[?&](page|p|pg|start|offset|pageNumber)=\d*', url, re.IGNORECASE
    ))
    is_listing = bool(re.search(
        r'[?&](s|search|q|query|k|rh|category|i=)=', url, re.IGNORECASE
    ))

    if not has_pagination_param and not is_listing:
        return True

    return False


# ── PROGRESS TRACKER ─────────────────────────────────────────────────────────
def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f)
    return {
        "url": "", "what": "", "fmt": "json",
        "mode": "",               # "single" or "multi"
        "completed_pages": [],
        "session_count": 0,
        "total_records": 0,
    }

def save_progress(progress):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)
    print(f"  💾 Progress saved — {len(progress['completed_pages'])} pages done.")


# ── FETCH HTML (with retry for 503) ──────────────────────────────────────────
def fetch_html(url, retries=3):
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=HEADERS, timeout=20)
            response.raise_for_status()
            print(f"  ✅ Fetched [{response.status_code}]: {url[:80]}...")
            return response.text
        except requests.exceptions.HTTPError as e:
            if response.status_code == 503:
                wait = 30 * (attempt + 1)
                print(f"  ⚠️  503 on fetch, waiting {wait}s... (attempt {attempt+1}/{retries})")
                time.sleep(wait)
            else:
                print(f"  ❌ HTTP error: {e}")
                return None
        except Exception as e:
            print(f"  ❌ Fetch error: {e}")
            return None
    print(f"  ❌ Failed after {retries} attempts.")
    return None


# ── CLEAN HTML ────────────────────────────────────────────────────────────────
def clean_html(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer",
                     "nav", "aside", "form", "iframe", "svg", "img",
                     "button", "meta", "link"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


# ── BUILD PAGE URL ────────────────────────────────────────────────────────────
def build_page_url(base_url, page_num):
    parsed = list(urlparse(base_url))
    params = parse_qs(parsed[4], keep_blank_values=True)
    if "page" in params:
        params["page"] = [str(page_num)]
    elif "p" in params:
        params["p"] = [str(page_num)]
    elif "pg" in params:
        params["pg"] = [str(page_num)]
    else:
        params["page"] = [str(page_num)]
    flat = {k: v[0] for k, v in params.items()}
    parsed[4] = urlencode(flat)
    return urlunparse(parsed)


# ── GEMINI EXTRACTION (with 503 + 429 retry) ─────────────────────────────────
def call_gemini_with_retry(prompt, retries=3):
    """Call Gemini with automatic retry for 503 and 429 errors."""
    for attempt in range(retries):
        try:
            response = model.generate_content(prompt)
            return response.text, False  # text, quota_exhausted

        except Exception as e:
            err = str(e)

            # 503 — server overloaded, wait and retry
            if "503" in err or "UNAVAILABLE" in err:
                wait = 30 * (attempt + 1)
                print(f"     ⚠️  Gemini 503 (overloaded). Waiting {wait}s... (attempt {attempt+1}/{retries})")
                time.sleep(wait)
                continue

            # 429 — quota exceeded
            elif "429" in err or "quota" in err.lower() or "RESOURCE_EXHAUSTED" in err:
                delay_match = re.search(r'retryDelay.*?(\d+)s', err)
                wait = int(delay_match.group(1)) + 10 if delay_match else 70
                print(f"     ⚠️  Quota limit hit. Waiting {wait}s then retrying once...")
                time.sleep(wait)
                try:
                    response = model.generate_content(prompt)
                    return response.text, False
                except Exception:
                    print(f"     ❌ Still quota limited. Stopping session.")
                    return None, True  # quota_exhausted

            else:
                print(f"     ❌ Gemini error: {e}")
                return None, False

    print(f"     ❌ Gemini failed after {retries} attempts.")
    return None, False


def extract_with_gemini(text, url, what_to_extract, page_num):
    # Split into chunks
    chunks = []
    temp = text
    while len(temp) > CHUNK_SIZE:
        split_at = temp.rfind("\n", 0, CHUNK_SIZE)
        if split_at == -1:
            split_at = CHUNK_SIZE
        chunks.append(temp[:split_at])
        temp = temp[split_at:]
    if temp.strip():
        chunks.append(temp)

    all_items = []
    quota_exhausted = False

    for i, chunk in enumerate(chunks):
        print(f"     Chunk {i+1}/{len(chunks)} ({len(chunk):,} chars)...")

        prompt = f"""You are a web scraping assistant.
Extract structured data from this webpage text.

URL: {url}
Page number: {page_num}
Extract: {what_to_extract if what_to_extract else "all meaningful data — products, names, prices, ratings, descriptions, facts, sections"}

Rules:
- Return ONLY a valid JSON array of objects.
- No markdown, no explanation, no code fences.
- snake_case keys. Missing fields = null.
- Empty page = []

Text:
{chunk}
"""
        raw_text, quota_hit = call_gemini_with_retry(prompt)

        if quota_hit:
            quota_exhausted = True
            break

        if raw_text:
            try:
                raw = raw_text.strip()
                raw = re.sub(r'^```(?:json)?', '', raw, flags=re.MULTILINE)
                raw = re.sub(r'```$', '', raw, flags=re.MULTILINE)
                data = json.loads(raw.strip())
                print(f"     ✅ {len(data)} item(s) from chunk {i+1}.")
                all_items.extend(data)
            except json.JSONDecodeError:
                print(f"     ⚠️  Non-JSON response, skipping chunk {i+1}.")

        if i < len(chunks) - 1:
            time.sleep(2)

    # Deduplicate
    seen = set()
    unique = []
    for item in all_items:
        key = json.dumps(item, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique, quota_exhausted


# ── SAVE SESSION ──────────────────────────────────────────────────────────────
def save_session(data, session_num, fmt="json"):
    filename = f"session_{session_num:03d}"
    if fmt == "json":
        path = os.path.join(OUTPUT_DIR, filename + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    elif fmt == "csv":
        import csv
        path = os.path.join(OUTPUT_DIR, filename + ".csv")
        if data:
            all_keys = list({k for item in data for k in item.keys()})
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(data)
    print(f"  💾 Session {session_num} saved → {path} ({len(data)} records)")
    return path


# ── SINGLE PAGE MODE ──────────────────────────────────────────────────────────
def run_single_page(url, what, fmt):
    """For Wikipedia and other single-page sites — scrape once, done."""
    print(f"\n🔍 Single-page mode detected.")
    print(f"   Fetching: {url}\n")

    html = fetch_html(url)
    if not html:
        print("❌ Could not fetch the page.")
        return

    text = clean_html(html)
    print(f"  🧹 Cleaned: {len(text):,} chars")

    data, _ = extract_with_gemini(text, url, what, page_num=1)

    if data:
        domain = urlparse(url).netloc.replace("www.", "").replace(".", "_")
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{domain}_{timestamp}"

        if fmt == "json":
            path = os.path.join(OUTPUT_DIR, filename + ".json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        elif fmt == "csv":
            import csv
            path = os.path.join(OUTPUT_DIR, filename + ".csv")
            if data and isinstance(data[0], dict):
                all_keys = list({k for item in data for k in item.keys()})
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(data)

        print(f"\n✅ Done! {len(data)} records saved → {path}")
    else:
        print("\n⚠️  No data extracted.")


# ── MULTI PAGE MODE ───────────────────────────────────────────────────────────
def run_multi_page(progress):
    """Session-based chunked scraping for paginated sites like Amazon."""
    completed     = set(progress["completed_pages"])
    session_num   = progress["session_count"]
    total_records = progress["total_records"]
    url           = progress["url"]
    what          = progress["what"]
    fmt           = progress["fmt"]

    all_pages = list(range(1, TOTAL_PAGES + 1))
    remaining = [p for p in all_pages if p not in completed]

    if not remaining:
        print("\n✅ All 300 pages done! Run: python merge.py")
        return

    this_run    = remaining[:PAGES_PER_RUN]
    session_num += 1
    progress["session_count"] = session_num

    pages_left = len(remaining) - len(this_run)
    runs_left  = (pages_left + PAGES_PER_RUN - 1) // PAGES_PER_RUN

    print(f"\n📋 Session {session_num}")
    print(f"   Scraping pages : {this_run}")
    print(f"   After this run : {pages_left} pages left (~{runs_left} more runs)")
    print(f"   Overall        : {len(completed)}/{TOTAL_PAGES} pages done")
    print("-"*60)

    session_data = []
    quota_hit    = False

    for page_num in this_run:
        page_url = build_page_url(url, page_num)
        print(f"\n[Page {page_num}/{TOTAL_PAGES}]")
        print(f"  URL: {page_url[:80]}...")

        html = fetch_html(page_url)
        if not html:
            print(f"  ⚠️  Skipping page {page_num}.")
            completed.add(page_num)
            continue

        text = clean_html(html)
        print(f"  🧹 Cleaned: {len(text):,} chars")

        data, quota_exhausted = extract_with_gemini(text, page_url, what, page_num)
        session_data.extend(data)
        print(f"  📊 Session total: {len(session_data)} records")

        completed.add(page_num)
        progress["completed_pages"] = list(completed)
        progress["total_records"]   = total_records + len(session_data)
        save_progress(progress)

        if quota_exhausted:
            print("\n⛔ Quota exhausted. Progress saved.")
            print("   Wait ~1 min then re-run.")
            quota_hit = True
            break

        if page_num != this_run[-1]:
            print(f"  ⏳ Waiting {DELAY_BETWEEN_PAGES}s...")
            time.sleep(DELAY_BETWEEN_PAGES)

    if session_data:
        save_session(session_data, session_num, fmt)
        total_records += len(session_data)
        progress["total_records"] = total_records
        save_progress(progress)

    remaining_after = [p for p in all_pages if p not in completed]
    print("\n" + "="*60)
    print(f"✅ Session {session_num} complete!")
    print(f"   Records this session : {len(session_data)}")
    print(f"   Total records so far : {total_records}")
    print(f"   Pages done           : {len(completed)}/{TOTAL_PAGES}")
    print(f"   Pages remaining      : {len(remaining_after)}")

    if not remaining_after:
        print(f"\n🎉 ALL {TOTAL_PAGES} PAGES DONE! Run: python merge.py")
    elif quota_hit:
        print(f"\n⏳ Quota hit — wait ~1 min then re-run.")
    else:
        runs_needed = (len(remaining_after) + PAGES_PER_RUN - 1) // PAGES_PER_RUN
        print(f"\n▶ Re-run to continue. ~{runs_needed} runs left.")
    print("="*60 + "\n")


# ── MAIN ──────────────────────────────────────────────────────────────────────
def run_scraper():
    print("\n" + "="*60)
    print("   🤖 Smart AI Scraper (Auto Single/Multi Page)")
    print("="*60)

    progress = load_progress()

    # ── Resuming a previous multi-page session ──
    if progress["url"] and progress["mode"] == "multi":
        print(f"\n▶ Resuming multi-page session.")
        print(f"  URL     : {progress['url'][:70]}...")
        print(f"  Done    : {sorted(progress['completed_pages'])}")
        print(f"  Records : {progress['total_records']} so far")
        run_multi_page(progress)
        return

    # ── Fresh start — ask for inputs ──
    url = input("\n🌐 Enter URL to scrape: ").strip()
    if not url.startswith("http"):
        url = "https://" + url

    what = input("🎯 What to extract? (Enter = auto-detect): ").strip()
    fmt  = input("💾 Save as json or csv? (default json): ").strip().lower()
    if fmt not in ["json", "csv"]:
        fmt = "json"

    print("\n" + "-"*60)

    # ── Auto-detect mode ──
    if is_single_page_site(url):
        print(f"\n✅ Detected: SINGLE-PAGE site")
        print(f"   (No pagination needed — scraping entire page at once)")
        run_single_page(url, what, fmt)
    else:
        print(f"\n✅ Detected: MULTI-PAGE site")
        print(f"   (Session mode — {PAGES_PER_RUN} pages per run, up to {TOTAL_PAGES} pages)")
        progress["url"]  = url
        progress["what"] = what
        progress["fmt"]  = fmt
        progress["mode"] = "multi"
        save_progress(progress)
        run_multi_page(progress)


if __name__ == "__main__":
    run_scraper()