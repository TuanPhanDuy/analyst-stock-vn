"""
Real-time Vietnamese financial news scraper.

Sources:
  - CafeF (cafef.vn) — general market news and company filings
  - FireAnt (fireant.vn) — ticker-specific news feed
  - VNDirect news API — corporate announcements

Returns a list of headline dicts:
    [{ticker, title, url, source, published_at, category}]

All results are cached to data/cache/ to avoid hammering sources.
"""
import hashlib
import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


def _cache_path(key: str) -> Path:
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    return CACHE_DIR / f"news_{h}.json"


def _load_cache(path: Path, ttl: int = 1800) -> Optional[list]:
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _save_cache(path: Path, data: list) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


# ── FireAnt ticker news ───────────────────────────────────────────────────────

def fetch_fireant(ticker: str, limit: int = 20, ttl: int = 1800) -> List[dict]:
    """
    Fetch ticker-specific news from FireAnt's public API.
    """
    cache_key = f"fa_{ticker}_{date.today().isoformat()}"
    cache_file = _cache_path(cache_key)
    cached = _load_cache(cache_file, ttl)
    if cached is not None:
        return cached

    headlines = []
    try:
        url = f"https://restv2.fireant.vn/posts?symbol={ticker}&limit={limit}&offset=0&type=1"
        sess = _session()
        resp = sess.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for item in data if isinstance(data, list) else data.get("items", []):
            title = (item.get("title") or item.get("content") or "")[:200].strip()
            if not title:
                continue
            headlines.append({
                "ticker": ticker,
                "title": title,
                "url": item.get("url") or f"https://fireant.vn/post/{item.get('postId', '')}",
                "source": "FireAnt",
                "published_at": (item.get("date") or item.get("publishedDate") or "")[:10],
                "category": "news",
            })
    except Exception as e:
        logger.debug("FireAnt fetch %s: %s", ticker, e)

    _save_cache(cache_file, headlines)
    return headlines


# ── CafeF market news ─────────────────────────────────────────────────────────

def fetch_cafef_ticker(ticker: str, limit: int = 15, ttl: int = 1800) -> List[dict]:
    """
    Fetch ticker news from CafeF's JSON API.
    """
    cache_key = f"cf_{ticker}_{date.today().isoformat()}"
    cache_file = _cache_path(cache_key)
    cached = _load_cache(cache_file, ttl)
    if cached is not None:
        return cached

    headlines = []
    try:
        url = f"https://cafef.vn/ajax/more-news.ashx"
        params = {"s": ticker, "c": 0, "p": 1, "count": limit}
        sess = _session()
        resp = sess.get(url, params=params, timeout=10)
        resp.raise_for_status()

        # Response is HTML fragments or JSON depending on endpoint
        text = resp.text
        if text.strip().startswith("[") or text.strip().startswith("{"):
            items = json.loads(text)
            for item in (items if isinstance(items, list) else [items]):
                title = (item.get("Title") or item.get("title") or "").strip()
                href = item.get("Url") or item.get("url") or ""
                if not title:
                    continue
                if href and not href.startswith("http"):
                    href = "https://cafef.vn" + href
                headlines.append({
                    "ticker": ticker,
                    "title": title,
                    "url": href,
                    "source": "CafeF",
                    "published_at": (item.get("Date") or item.get("date") or "")[:10],
                    "category": "news",
                })
        else:
            # Parse anchor tags from HTML
            for m in re.finditer(r'href="([^"]+)"[^>]*>\s*([^<]+)<', text):
                href, title = m.group(1), m.group(2).strip()
                if not title or len(title) < 10:
                    continue
                if href and not href.startswith("http"):
                    href = "https://cafef.vn" + href
                headlines.append({
                    "ticker": ticker,
                    "title": title,
                    "url": href,
                    "source": "CafeF",
                    "published_at": date.today().isoformat(),
                    "category": "news",
                })
    except Exception as e:
        logger.debug("CafeF ticker fetch %s: %s", ticker, e)

    _save_cache(cache_file, headlines)
    return headlines


def fetch_cafef_market(limit: int = 30, ttl: int = 1800) -> List[dict]:
    """
    Fetch general VN market headlines from CafeF's main finance section.
    """
    cache_key = f"cfmkt_{date.today().isoformat()}"
    cache_file = _cache_path(cache_key)
    cached = _load_cache(cache_file, ttl)
    if cached is not None:
        return cached

    headlines = []
    try:
        url = "https://cafef.vn/thi-truong-chung-khoan.chn"
        sess = _session()
        resp = sess.get(url, timeout=10)
        resp.raise_for_status()

        # Extract article titles from HTML
        for m in re.finditer(
            r'<h[23][^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>([^<]{15,200})</a>',
            resp.text,
            re.DOTALL,
        ):
            href, title = m.group(1).strip(), m.group(2).strip()
            title = re.sub(r'\s+', ' ', title)
            if not title or len(title) < 15:
                continue
            if href and not href.startswith("http"):
                href = "https://cafef.vn" + href
            headlines.append({
                "ticker": None,
                "title": title,
                "url": href,
                "source": "CafeF",
                "published_at": date.today().isoformat(),
                "category": "market",
            })
            if len(headlines) >= limit:
                break
    except Exception as e:
        logger.debug("CafeF market fetch: %s", e)

    _save_cache(cache_file, headlines)
    return headlines


# ── VNDirect announcements ────────────────────────────────────────────────────

def fetch_vndirect_announcements(ticker: str, days: int = 14, ttl: int = 3600) -> List[dict]:
    """
    Fetch official company announcements from VNDirect events API.
    """
    cache_key = f"vnd_ann_{ticker}_{date.today().isoformat()}"
    cache_file = _cache_path(cache_key)
    cached = _load_cache(cache_file, ttl)
    if cached is not None:
        return cached

    headlines = []
    try:
        start = (date.today() - timedelta(days=days)).isoformat()
        url = "https://finfo-api.vndirect.com.vn/v4/news"
        params = {
            "q": f"code:{ticker}~date:gte:{start}",
            "sort": "date:desc",
            "size": 20,
        }
        sess = _session()
        resp = sess.get(url, params=params, timeout=10)
        resp.raise_for_status()
        for item in resp.json().get("data", []):
            title = (item.get("title") or "").strip()
            if not title:
                continue
            headlines.append({
                "ticker": ticker,
                "title": title,
                "url": item.get("newsUrl") or "",
                "source": "VNDirect",
                "published_at": (item.get("date") or "")[:10],
                "category": item.get("categoryId") or "announcement",
            })
    except Exception as e:
        logger.debug("VNDirect announcements %s: %s", ticker, e)

    _save_cache(cache_file, headlines)
    return headlines


# ── Aggregate ─────────────────────────────────────────────────────────────────

def fetch_all(ticker: str, days: int = 7, ttl: int = 1800) -> List[dict]:
    """
    Fetch headlines for a single ticker from all sources.
    Deduplicates by title (case-insensitive first 60 chars).
    """
    results = []
    seen = set()

    for fn in (fetch_fireant, fetch_cafef_ticker, fetch_vndirect_announcements):
        try:
            items = fn(ticker) if fn != fetch_vndirect_announcements else fn(ticker, days=days)
            for item in items:
                key = item["title"][:60].lower().strip()
                if key not in seen:
                    seen.add(key)
                    results.append(item)
        except Exception as e:
            logger.debug("fetch_all %s %s: %s", ticker, fn.__name__, e)
        time.sleep(0.05)

    # Filter by recency
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    filtered = [
        r for r in results
        if not r.get("published_at") or r["published_at"] >= cutoff
    ]

    return filtered or results   # fall back to unfiltered if date parsing fails


def fetch_bulk(tickers: List[str], days: int = 7) -> dict:
    """
    Fetch headlines for multiple tickers. Returns {ticker: [headlines]}.
    Also fetches general market news and tags tickers mentioned.
    """
    result = {}
    market_news = fetch_cafef_market(limit=30)

    for ticker in tickers:
        ticker_news = fetch_all(ticker, days=days)
        # Tag any market headlines that mention this ticker
        for item in market_news:
            if ticker in item["title"].upper():
                copy = {**item, "ticker": ticker}
                if copy["title"] not in {n["title"] for n in ticker_news}:
                    ticker_news.append(copy)
        result[ticker] = ticker_news
        time.sleep(0.05)

    return result


def format_for_claude(headlines: List[dict], max_items: int = 20) -> str:
    """
    Format headlines into a concise string for use in Claude prompts.
    """
    if not headlines:
        return "(no recent headlines found)"

    lines = []
    for item in headlines[:max_items]:
        ticker_prefix = f"[{item['ticker']}] " if item.get("ticker") else ""
        date_prefix = f"{item.get('published_at', '')} " if item.get("published_at") else ""
        lines.append(f"- {date_prefix}{ticker_prefix}{item['title']} ({item.get('source', '?')})")

    return "\n".join(lines)
