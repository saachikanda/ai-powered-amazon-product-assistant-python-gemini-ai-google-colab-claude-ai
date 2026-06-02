"""
AI Web Scraper — v2.0
─────────────────────
Strategy:
  1. Fetch HTML in parallel (network I/O bound, safe to parallelize)
  2. Try BeautifulSoup structured extraction FIRST (zero API calls)
  3. Only call AI when BS4 yields nothing — saves your quota
  4. Model fallback chain: gemini-2.0-flash → gemini-1.5-flash → gemini-1.5-pro-002
  5. Exponential backoff with jitter on 503 / rate-limit errors
  6. Per-minute AND per-day quota guards
"""

import os, re, json, time, hashlib, random, csv
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY")
client           = genai.Client(api_key=GEMINI_API_KEY)

# Model fallback chain — tried in order on failure
MODEL_CHAIN = [
    "gemini-2.0-flash",       # fastest, cheapest, try first
    "gemini-1.5-flash",       # reliable fallback
    "gemini-1.5-flash-8b",    # ultra-light last resort
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

OUTPUT_DIR        = "outputs"
HTML_CACHE_DIR    = os.path.join(OUTPUT_DIR, "html_cache")
PROGRESS_FILE     = os.path.join(OUTPUT_DIR, "progress.json")

PAGES_PER_RUN     = 5        # pages per run (BS4 pages are free!)
TOTAL_PAGES       = 300
CHUNK_SIZE        = 50000    # chars per AI chunk

FETCH_WORKERS     = 4        # parallel HTML fetchers (safe — it's just HTTP)
DELAY_PAGES       = 5        # seconds between AI calls
MAX_RETRIES       = 4        # per chunk
BASE_BACKOFF      = 2        # exponential backoff base (seconds)

# Quota limits (free tier Gemini)
DAILY_LIMIT       = 18       # stay 2 under the 20 cap as safety buffer
RPM_LIMIT         = 14       # requests per minute (free tier ~15 RPM)

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(HTML_CACHE_DIR, exist_ok=True)


# ── QUOTA TRACKER ─────────────────────────────────────────────────────────────
class QuotaTracker:
    """Tracks daily calls and enforces per-minute rate limiting."""

    def __init__(self, daily_limit=DAILY_LIMIT, rpm_limit=RPM_LIMIT):
        self.daily_limit   = daily_limit
        self.rpm_limit     = rpm_limit
        self.daily_calls   = 0
        self.daily_date    = time.strftime("%Y-%m-%d")
        self.minute_calls  = []   # timestamps of calls in the last 60s

    def reset_if_new_day(self):
        today = time.strftime("%Y-%m-%d")
        if today != self.daily_date:
            self.daily_calls = 0
            self.daily_date  = today
            print("  🔄 New day — daily counter reset.")

    def can_call(self):
        self.reset_if_new_day()
        if self.daily_calls >= self.daily_limit:
            return False, "daily"
        now = time.time()
        self.minute_calls = [t for t in self.minute_calls if now - t < 60]
        if len(self.minute_calls) >= self.rpm_limit:
            return False, "rpm"
        return True, "ok"

    def wait_for_rpm(self):
        """Block until we're under the per-minute cap."""
        while True:
            ok, reason = self.can_call()
            if ok:
                return
            if reason == "daily":
                raise RuntimeError("Daily quota exhausted.")
            now = time.time()
            oldest = min(self.minute_calls)
            wait   = 61 - (now - oldest)
            print(f"  ⏳ RPM cap reached — waiting {wait:.1f}s...")
            time.sleep(max(wait, 1))

    def record_call(self):
        self.daily_calls += 1
        self.minute_calls.append(time.time())

    def remaining(self):
        self.reset_if_new_day()
        return self.daily_limit - self.daily_calls

    def load(self, state: dict):
        today = time.strftime("%Y-%m-%d")
        if state.get("daily_reset") == today:
            self.daily_calls = state.get("daily_calls", 0)
        else:
            self.daily_calls = 0
        self.daily_date = today

    def dump(self) -> dict:
        return {"daily_calls": self.daily_calls, "daily_reset": self.daily_date}


quota = QuotaTracker()


# ── PROGRESS ──────────────────────────────────────────────────────────────────
def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {
        "url": "", "what": "", "fmt": "json",
        "completed_pages": [], "session_count": 0,
        "total_records": 0, "daily_calls": 0, "daily_reset": "",
    }

def save_progress(progress):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


# ── HTML CACHE ────────────────────────────────────────────────────────────────
def _cache_path(url):
    return os.path.join(HTML_CACHE_DIR, hashlib.md5(url.encode()).hexdigest() + ".html")

def load_cached(url):
    p = _cache_path(url)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read()
    return None

def save_cached(url, html):
    with open(_cache_path(url), "w", encoding="utf-8") as f:
        f.write(html)


# ── FETCH (single) ────────────────────────────────────────────────────────────
def fetch_html(url):
    cached = load_cached(url)
    if cached:
        return url, cached, "cache"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        save_cached(url, r.text)
        return url, r.text, r.status_code
    except Exception as e:
        return url, None, str(e)


# ── PARALLEL FETCH ────────────────────────────────────────────────────────────
def fetch_pages_parallel(urls):
    """Fetch multiple URLs in parallel. Returns dict {url: html}."""
    results = {}
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
        futures = {ex.submit(fetch_html, u): u for u in urls}
        for fut in as_completed(futures):
            url, html, status = fut.result()
            short = url[-60:]
            if html:
                print(f"  ✅ [{status}] ...{short}")
            else:
                print(f"  ❌ Failed ...{short}: {status}")
            results[url] = html
    return results


# ── CLEAN HTML ────────────────────────────────────────────────────────────────
def clean_html(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script","style","noscript","header","footer",
                     "nav","aside","form","iframe","svg","img",
                     "button","meta","link"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text(separator="\n").splitlines()]
    return "\n".join(l for l in lines if l)


# ── BS4 STRUCTURED EXTRACTORS ─────────────────────────────────────────────────
def _text(el, *selectors, default=None):
    for sel in selectors:
        found = el.select_one(sel)
        if found:
            return found.get_text(strip=True)
    return default

def _attr(el, attr, *selectors, default=None):
    for sel in selectors:
        found = el.select_one(sel)
        if found and found.get(attr):
            return found[attr]
    return default


def extract_amazon(soup, base_url):
    """Parse Amazon search results page directly from BS4."""
    items = []
    for card in soup.select('[data-component-type="s-search-result"]'):
        name   = _text(card, 'h2 .a-link-normal span', 'h2 span')
        price  = _text(card, '.a-price .a-offscreen', '.a-price-whole')
        rating = _text(card, '.a-icon-star-small .a-icon-alt', '[aria-label*="out of"]')
        reviews= _text(card, '[aria-label*="ratings"]', '.a-size-base.s-underline-text')
        brand  = _text(card, '.a-size-base-plus.a-color-base', '.s-line-clamp-1 span')
        link   = _attr(card, 'href', 'h2 a.a-link-normal', 'a.a-link-normal')
        img    = _attr(card, 'src', 'img.s-image')
        asin   = card.get('data-asin')

        if not name:
            continue

        full_url = None
        if link:
            full_url = ("https://www.amazon.ae" + link) if link.startswith("/") else link
            # clean affiliate junk
            full_url = re.sub(r'/ref=.*', '', full_url)

        items.append({
            "name": name, "price": price, "rating": rating,
            "reviews": reviews, "brand": brand,
            "url": full_url, "image": img, "asin": asin,
        })
    return items


def extract_wikipedia(soup, base_url):
    """Extract paragraphs and section headings from a Wikipedia page."""
    items = []
    content = soup.select_one('#mw-content-text')
    if not content:
        return items
    section = "Introduction"
    for el in content.find_all(['h2','h3','p'], recursive=True):
        if el.name in ('h2','h3'):
            section = el.get_text(strip=True).replace('[edit]','').strip()
        elif el.name == 'p':
            text = el.get_text(strip=True)
            if len(text) > 60:
                items.append({"section": section, "text": text})
    return items


def extract_generic(soup, base_url):
    """
    Generic heuristic extractor — finds product-like cards across many sites.
    Looks for repeated block elements containing a name + price signal.
    """
    price_pat = re.compile(r'[\$£€﷼AED]\s?[\d,\.]+|[\d,\.]+\s?(AED|USD|GBP|EUR)', re.I)
    candidates = []

    # Look for elements that appear in repeating lists
    for container in soup.select('ul li, ol li, .product, .item, .card, [class*="product"], [class*="item"]'):
        text = container.get_text(" ", strip=True)
        if len(text) < 20 or len(text) > 3000:
            continue
        price_match = price_pat.search(text)
        name_el = container.select_one('h1,h2,h3,h4,h5,a,[class*="title"],[class*="name"]')
        name = name_el.get_text(strip=True) if name_el else None
        if name and price_match:
            link_el = container.select_one('a[href]')
            href = link_el['href'] if link_el else None
            if href and href.startswith('/'):
                parsed = urlparse(base_url)
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            candidates.append({
                "name": name[:200],
                "price": price_match.group(0),
                "url": href,
            })

    return candidates


def bs4_extract(html, url):
    """
    Try structured BS4 extraction first.
    Returns (items, method_name).
    """
    soup = BeautifulSoup(html, "html.parser")
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if "amazon" in host:
        items = extract_amazon(soup, url)
        if items:
            return items, "bs4:amazon"

    if "wikipedia" in host or "wiki" in host:
        items = extract_wikipedia(soup, url)
        if items:
            return items, "bs4:wikipedia"

    # Generic fallback
    items = extract_generic(soup, url)
    if items:
        return items, "bs4:generic"

    return [], "bs4:none"


# ── AI EXTRACTION (fallback) ──────────────────────────────────────────────────
def call_gemini_with_fallback(prompt):
    """
    Try each model in MODEL_CHAIN. Exponential backoff on 503.
    Raises RuntimeError if all models fail.
    """
    last_error = None
    for model in MODEL_CHAIN:
        for attempt in range(MAX_RETRIES):
            try:
                quota.wait_for_rpm()
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0),
                )
                quota.record_call()
                return resp.text.strip(), model
            except Exception as e:
                err = str(e)
                last_error = err

                if "503" in err or "UNAVAILABLE" in err:
                    wait = (BASE_BACKOFF ** (attempt + 1)) + random.uniform(0, 2)
                    print(f"     ⚠️  {model} unavailable (503). Retry {attempt+1}/{MAX_RETRIES} in {wait:.1f}s...")
                    time.sleep(wait)
                    continue

                if "429" in err or "RESOURCE_EXHAUSTED" in err or "quota" in err.lower():
                    # Try next model immediately (different quota bucket)
                    print(f"     ⚠️  {model} quota hit — trying next model...")
                    break

                # Unknown error — log and try next model
                print(f"     ❌ {model} error: {err[:120]}")
                break

        # If we get here from a non-quota break, try next model
    raise RuntimeError(f"All models failed. Last error: {last_error}")


def extract_with_ai(text, url, what, page_num, progress):
    """Chunk the text and call AI for each chunk. Returns (items, exhausted)."""
    # Split into chunks
    chunks, remaining = [], text
    while len(remaining) > CHUNK_SIZE:
        split = remaining.rfind("\n", 0, CHUNK_SIZE)
        if split == -1:
            split = CHUNK_SIZE
        chunks.append(remaining[:split])
        remaining = remaining[split:]
    if remaining.strip():
        chunks.append(remaining)

    print(f"  🤖 AI extraction: {len(chunks)} chunk(s) | quota left: {quota.remaining()}/day")

    all_items    = []
    exhausted    = False

    for i, chunk in enumerate(chunks):
        ok, reason = quota.can_call()
        if not ok:
            print(f"  ⛔ Quota {'daily' if reason == 'daily' else 'RPM'} exhausted.")
            exhausted = True
            break

        print(f"     Chunk {i+1}/{len(chunks)} ({len(chunk):,} chars)")

        prompt = f"""You are a web scraping assistant. Extract structured data from the text below.

URL: {url}
Page: {page_num}
Extract: {what or 'all products — name, price, rating, reviews, brand, URL, image'}

Rules:
- Return ONLY a valid JSON array of objects.
- No markdown, no backticks, no explanation.
- Use snake_case keys. Missing fields = null.
- Empty page → return []

Text:
{chunk}
"""
        try:
            raw, model_used = call_gemini_with_fallback(prompt)
            raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
            raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)
            data = json.loads(raw.strip())
            print(f"     ✅ {len(data)} item(s) via {model_used}")
            all_items.extend(data)
        except RuntimeError as e:
            print(f"     ❌ All models failed for chunk {i+1}: {e}")
            exhausted = True
            break
        except json.JSONDecodeError as e:
            print(f"     ⚠️  Bad JSON from chunk {i+1}: {e}")

        # save quota state after each call
        progress.update(quota.dump())
        save_progress(progress)

        if i < len(chunks) - 1:
            time.sleep(2)

    # Deduplicate
    seen, unique = set(), []
    for item in all_items:
        k = json.dumps(item, sort_keys=True)
        if k not in seen:
            seen.add(k)
            unique.append(item)

    return unique, exhausted


# ── URL BUILDER ───────────────────────────────────────────────────────────────
def build_page_url(base_url, page_num):
    parsed = list(urlparse(base_url))
    params = parse_qs(parsed[4], keep_blank_values=True)
    for key in ("page", "p", "pg"):
        if key in params:
            params[key] = [str(page_num)]
            break
    else:
        params["page"] = [str(page_num)]
    parsed[4] = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed)


# ── DETECT PAGE COUNT ─────────────────────────────────────────────────────────
def detect_total_pages(html, base_url, user_total):
    """Try to auto-detect pagination from the first page."""
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for el in soup.select('a[href], span, li'):
        txt = el.get_text(strip=True)
        if txt.isdigit() and 1 < int(txt) <= 9999:
            candidates.append(int(txt))
    if candidates:
        detected = max(candidates)
        if detected < user_total:
            print(f"  ℹ️  Auto-detected {detected} pages (you set {user_total}). Using {detected}.")
            return detected
    return user_total


# ── SAVE OUTPUT ───────────────────────────────────────────────────────────────
def save_session(data, session_num, fmt="json"):
    if not data:
        print("  ⚠️  No data to save for this session.")
        return None
    name = f"session_{session_num:03d}"
    if fmt == "csv":
        path = os.path.join(OUTPUT_DIR, name + ".csv")
        keys = list({k for item in data for k in item})
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader(); w.writerows(data)
    else:
        path = os.path.join(OUTPUT_DIR, name + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  💾 Saved session {session_num} → {path} ({len(data)} records)")
    return path


# ── MAIN ──────────────────────────────────────────────────────────────────────
def run():
    print("\n" + "="*62)
    print("   🤖 AI Scraper v2.0 — BS4-first + AI fallback + quota guard")
    print("="*62)

    progress = load_progress()
    quota.load(progress)

    print(f"\n  📊 Daily AI calls used: {quota.daily_calls}/{quota.daily_limit}")
    if quota.remaining() <= 0:
        print("⛔ Daily quota exhausted. Try again tomorrow.")
        return

    # ── Setup ──────────────────────────────────────────────────────────────
    if not progress["url"]:
        url  = input("\n🌐 URL to scrape: ").strip()
        if not url.startswith("http"):
            url = "https://" + url
        what = input("🎯 What to extract? (Enter = auto): ").strip()
        fmt  = input("💾 Format — json or csv? [json]: ").strip().lower()
        if fmt not in ("json", "csv"):
            fmt = "json"
        total_input = input(f"📄 Total pages? [{TOTAL_PAGES}]: ").strip()
        total_pages = int(total_input) if total_input.isdigit() else TOTAL_PAGES

        # fetch page 1 to detect real page count
        print("\n  🔍 Fetching page 1 to detect pagination...")
        _, html1, _ = fetch_html(url)
        if html1:
            total_pages = detect_total_pages(html1, url, total_pages)

        progress.update({
            "url": url, "what": what, "fmt": fmt,
            "total_pages": total_pages,
        })
        save_progress(progress)
    else:
        url         = progress["url"]
        what        = progress["what"]
        fmt         = progress["fmt"]
        total_pages = progress.get("total_pages", TOTAL_PAGES)
        print(f"\n▶ Resuming — {url[:70]}")
        print(f"  Done: {sorted(progress['completed_pages'])}")

    completed   = set(progress["completed_pages"])
    session_num = progress.get("session_count", 0)
    total_rec   = progress.get("total_records", 0)

    all_pages   = list(range(1, total_pages + 1))
    remaining   = [p for p in all_pages if p not in completed]

    if not remaining:
        print("\n✅ All pages done! Run: python merge.py")
        return

    this_run    = remaining[:PAGES_PER_RUN]
    session_num += 1
    progress["session_count"] = session_num

    print(f"\n📋 Session {session_num}")
    print(f"   Pages this run      : {this_run}")
    print(f"   Quota remaining     : {quota.remaining()} AI calls today")
    print(f"   Pages done          : {len(completed)}/{total_pages}")
    print("-"*62)

    # ── Parallel HTML fetch ────────────────────────────────────────────────
    print(f"\n📥 Fetching {len(this_run)} pages in parallel ({FETCH_WORKERS} workers)...")
    page_urls = {p: build_page_url(url, p) for p in this_run}
    html_map  = fetch_pages_parallel(list(page_urls.values()))

    # ── Process pages ──────────────────────────────────────────────────────
    session_data = []
    quota_hit    = False

    for page_num in this_run:
        page_url = page_urls[page_num]
        html     = html_map.get(page_url)

        print(f"\n[Page {page_num}/{total_pages}]")

        if not html:
            print(f"  ⚠️  Skipping — fetch failed.")
            completed.add(page_num)
            continue

        # ── Step 1: BS4 extraction (free) ──────────────────────────────
        items, method = bs4_extract(html, page_url)
        print(f"  🔍 BS4: {len(items)} item(s) via {method}")

        # ── Step 2: AI fallback only if BS4 found nothing ─────────────
        if not items:
            text = clean_html(html)
            print(f"  🧹 Cleaned text: {len(text):,} chars")

            # quick signal check before burning a quota call
            signals = ["aed","price","rating","stars","add to","buy","product","item","results"]
            if sum(1 for s in signals if s in text.lower()) < 2:
                print(f"  ⏭️  Looks empty — skipping AI call.")
            else:
                items, quota_hit = extract_with_ai(text, page_url, what, page_num, progress)

        session_data.extend(items)
        print(f"  📊 Session total: {len(session_data)} records")

        completed.add(page_num)
        progress["completed_pages"] = list(completed)
        progress["total_records"]   = total_rec + len(session_data)
        progress.update(quota.dump())
        save_progress(progress)

        if quota_hit:
            print("\n⛔ Quota exhausted. Progress saved. Run again tomorrow.")
            break

        if page_num != this_run[-1]:
            time.sleep(DELAY_PAGES)

    # ── Save session output ────────────────────────────────────────────────
    if session_data:
        save_session(session_data, session_num, fmt)
        total_rec += len(session_data)
        progress["total_records"] = total_rec
        progress.update(quota.dump())
        save_progress(progress)

    remaining_after = [p for p in all_pages if p not in completed]

    print("\n" + "="*62)
    print(f"✅ Session {session_num} done!")
    print(f"   Records this session : {len(session_data)}")
    print(f"   Total records        : {total_rec}")
    print(f"   Pages done           : {len(completed)}/{total_pages}")
    print(f"   AI calls used today  : {quota.daily_calls}/{quota.daily_limit}")
    if remaining_after:
        runs_left = (len(remaining_after) + PAGES_PER_RUN - 1) // PAGES_PER_RUN
        print(f"   Runs remaining       : ~{runs_left}")
        print(f"\n▶ Re-run the script to continue.")
    else:
        print(f"\n🎉 ALL DONE! Run: python merge.py")
    print("="*62 + "\n")


if __name__ == "__main__":
    run()