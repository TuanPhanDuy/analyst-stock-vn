"""
Earnings calendar and corporate event detection for VN stocks.

Data source: VNDirect FINFO API (dividends, corporate actions).
Upcoming events within N days are flagged to avoid entering new positions
pre-announcement (event risk = increased volatility, unpredictable direction).

Events tracked:
  - Dividend ex-dates (cổ tức tiền mặt / cổ phiếu thưởng)
  - Rights issue / bonus shares
  - AGM / EGM announcements
"""
import hashlib
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}
_VNDIRECT_BASE = "https://finfo-api.vndirect.com.vn/v4"


def _cache_path(key: str) -> Path:
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    return CACHE_DIR / f"earn_{h}.json"


def _load_cache(path: Path, ttl: int = 3600):
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def get_events(ticker: str, days_ahead: int = 60, ttl: int = 3600) -> List[dict]:
    """
    Fetch upcoming corporate events for a ticker.

    Returns list of event dicts:
        [{type, date, days_to, detail}]
    """
    cache_key = f"earn_{ticker}_{date.today().isoformat()}_{days_ahead}"
    cache_file = _cache_path(cache_key)
    cached = _load_cache(cache_file, ttl)
    if cached is not None:
        return cached

    events = []
    today = date.today()
    future_cutoff = (today + timedelta(days=days_ahead)).isoformat()

    # ── Dividends ─────────────────────────────────────────────────────────────
    try:
        url = f"{_VNDIRECT_BASE}/dividends"
        params = {
            "q": f"code:{ticker}~exDivDate:gte:{today.isoformat()}~exDivDate:lte:{future_cutoff}",
            "sort": "exDivDate",
            "size": 10,
        }
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=8)
        resp.raise_for_status()
        for item in resp.json().get("data", []):
            ex_date_str = (item.get("exDivDate") or "")[:10]
            if not ex_date_str:
                continue
            try:
                ex_date = date.fromisoformat(ex_date_str)
                days_to = (ex_date - today).days
                div_type = item.get("dividendType") or "CASH"
                rate = item.get("cashDividendRate") or item.get("stockDividendRate") or ""
                events.append({
                    "type": "DIVIDEND",
                    "date": ex_date_str,
                    "days_to": days_to,
                    "detail": f"Ex-div {div_type} rate={rate}",
                })
            except ValueError:
                pass
    except Exception as e:
        logger.debug("earnings dividend fetch %s: %s", ticker, e)

    # ── Rights issues / bonus shares ─────────────────────────────────────────
    try:
        url = f"{_VNDIRECT_BASE}/events"
        params = {
            "q": f"code:{ticker}~eventDate:gte:{today.isoformat()}~eventDate:lte:{future_cutoff}",
            "sort": "eventDate",
            "size": 10,
        }
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=8)
        resp.raise_for_status()
        for item in resp.json().get("data", []):
            ev_date_str = (item.get("eventDate") or "")[:10]
            ev_type = (item.get("eventType") or "CORPORATE_ACTION").upper()
            if not ev_date_str:
                continue
            try:
                ev_date = date.fromisoformat(ev_date_str)
                days_to = (ev_date - today).days
                if 0 <= days_to <= days_ahead:
                    events.append({
                        "type": ev_type,
                        "date": ev_date_str,
                        "days_to": days_to,
                        "detail": item.get("eventName") or ev_type,
                    })
            except ValueError:
                pass
    except Exception as e:
        logger.debug("earnings events fetch %s: %s", ticker, e)

    # Sort by days_to ascending
    events.sort(key=lambda e: e["days_to"])

    try:
        with open(cache_file, "w") as f:
            json.dump(events, f)
    except Exception:
        pass

    return events


def flag(ticker: str, warn_days: int = 7, ttl: int = 3600) -> dict:
    """
    Quick flag: does this ticker have a corporate event within warn_days?

    Returns:
        {
          flag: bool,
          message: str,
          days_to: int | None,
          events: [...]
        }
    """
    events = get_events(ticker, days_ahead=warn_days, ttl=ttl)
    if not events:
        return {"flag": False, "message": "", "days_to": None, "events": []}

    nearest = events[0]
    d = nearest["days_to"]
    ev_type = nearest["type"]
    msg = f"{ev_type} in {d} day{'s' if d != 1 else ''} — avoid new positions pre-event"

    return {
        "flag": True,
        "message": msg,
        "days_to": d,
        "events": events,
    }


def bulk_flags(tickers: List[str], warn_days: int = 7) -> Dict[str, dict]:
    """Check all tickers for upcoming events. Returns {ticker: flag_dict}."""
    result = {}
    for t in tickers:
        try:
            result[t] = flag(t, warn_days=warn_days)
        except Exception as e:
            logger.debug("earnings bulk %s: %s", t, e)
            result[t] = {"flag": False, "message": "", "days_to": None, "events": []}
        time.sleep(0.08)
    return result


def surprise_score(
    ticker: str,
    actual_eps: Optional[float],
    consensus_eps: Optional[float],
) -> dict:
    """
    Compute earnings surprise magnitude.
    Positive = beat, negative = miss. Returns dict with score and label.
    """
    if actual_eps is None or consensus_eps is None or consensus_eps == 0:
        return {"score": 0.0, "label": "NO_DATA", "beat": None}

    surprise_pct = (actual_eps - consensus_eps) / abs(consensus_eps) * 100
    if surprise_pct > 10:
        label = "BIG_BEAT"
    elif surprise_pct > 3:
        label = "BEAT"
    elif surprise_pct > -3:
        label = "IN_LINE"
    elif surprise_pct > -10:
        label = "MISS"
    else:
        label = "BIG_MISS"

    return {
        "score": round(surprise_pct, 1),
        "label": label,
        "beat": surprise_pct > 0,
        "score_delta": round(min(0.20, max(-0.20, surprise_pct / 100)), 3),
    }
