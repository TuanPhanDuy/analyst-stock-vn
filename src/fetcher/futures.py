"""
VN30F futures premium/discount as a pre-market directional signal.

The spread between VN30F (nearest contract) and VN30 spot gives an
early read on where institutional money expects the market to open.

Sources tried in order:
  1. SSI iBoard snapshot (best real-time, requires auth)
  2. VNDirect derivatives endpoint
  3. CafeF HTML scrape (fallback)
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/html, */*",
}

CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"futures_{key}.json"


def _load_cache(path: Path, ttl: int = 300) -> Optional[dict]:
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return None


def _save_cache(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def _from_vndirect() -> Optional[dict]:
    """VNDirect derivatives snapshot."""
    try:
        url = "https://finfo-api.vndirect.com.vn/v4/derivatives"
        params = {"q": "code:VN30F1M", "sort": "tradingDate:desc", "size": 1}
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=8)
        resp.raise_for_status()
        items = resp.json().get("data", [])
        if not items:
            return None
        d = items[0]
        return {
            "contract": d.get("code", "VN30F"),
            "last_price": float(d.get("lastPrice") or d.get("close") or 0),
            "open_interest": int(d.get("oi") or d.get("openInterest") or 0),
            "volume": int(d.get("tradingVolume") or d.get("volume") or 0),
            "source": "VNDirect",
        }
    except Exception as e:
        logger.debug("VNDirect futures: %s", e)
    return None


def _from_cafef() -> Optional[dict]:
    """Scrape CafeF derivatives page for VN30F nearest contract."""
    try:
        url = "https://cafef.vn/du-lieu/phai-sinh.chn"
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        # Look for VN30F price pattern in HTML
        m = re.search(r'VN30F\w*[^>]*>\s*([\d,]+\.?\d*)', resp.text)
        if m:
            price_str = m.group(1).replace(",", "")
            return {
                "contract": "VN30F",
                "last_price": float(price_str),
                "open_interest": 0,
                "volume": 0,
                "source": "CafeF",
            }
    except Exception as e:
        logger.debug("CafeF futures: %s", e)
    return None


def get_vn30_spot() -> Optional[float]:
    """Fetch VN30 index spot level from yfinance."""
    try:
        import yfinance as yf
        hist = yf.Ticker("^VN30").history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.debug("VN30 spot: %s", e)
    return None


def get_futures_snapshot(ttl: int = 300) -> dict:
    """
    Get VN30F nearest contract snapshot and compute basis.

    Returns:
        {
          contract, futures_price, spot_price,
          basis_pts, basis_pct,
          signal: CONTANGO | BACKWARDATION | NEUTRAL,
          score_delta: float (-0.10 to +0.10),
          source, cached
        }
    """
    cache_file = _cache_path(date.today().isoformat())
    cached = _load_cache(cache_file, ttl)
    if cached:
        cached["cached"] = True
        return cached

    futures_data = _from_vndirect() or _from_cafef()
    spot = get_vn30_spot()

    if not futures_data:
        result = {
            "contract": "VN30F",
            "futures_price": None,
            "spot_price": spot,
            "basis_pts": None,
            "basis_pct": None,
            "signal": "UNAVAILABLE",
            "score_delta": 0.0,
            "source": "none",
            "cached": False,
        }
        return result

    fp = futures_data["last_price"]
    basis_pts = None
    basis_pct = None
    signal = "NEUTRAL"
    score_delta = 0.0

    if spot and spot > 0 and fp > 0:
        basis_pts = round(fp - spot, 2)
        basis_pct = round((fp / spot - 1) * 100, 3)

        if basis_pct > 0.5:
            signal = "STRONG_CONTANGO"
            score_delta = 0.08
        elif basis_pct > 0.2:
            signal = "CONTANGO"
            score_delta = 0.04
        elif basis_pct < -0.5:
            signal = "STRONG_BACKWARDATION"
            score_delta = -0.08
        elif basis_pct < -0.2:
            signal = "BACKWARDATION"
            score_delta = -0.04
        else:
            signal = "NEUTRAL"
            score_delta = 0.0

    result = {
        "contract": futures_data["contract"],
        "futures_price": fp,
        "spot_price": spot,
        "basis_pts": basis_pts,
        "basis_pct": basis_pct,
        "open_interest": futures_data.get("open_interest", 0),
        "volume": futures_data.get("volume", 0),
        "signal": signal,
        "score_delta": score_delta,
        "source": futures_data["source"],
        "cached": False,
    }

    _save_cache(cache_file, result)
    return result


def futures_signal_summary(snapshot: Optional[dict] = None) -> str:
    """Format a one-line summary for reports."""
    if snapshot is None:
        snapshot = get_futures_snapshot()

    sig = snapshot.get("signal", "UNAVAILABLE")
    fp = snapshot.get("futures_price")
    bp = snapshot.get("basis_pct")

    if fp is None:
        return "VN30F: data unavailable"

    basis_str = f"basis {bp:+.2f}%" if bp is not None else ""
    emoji = {
        "STRONG_CONTANGO": "↑↑",
        "CONTANGO": "↑",
        "NEUTRAL": "→",
        "BACKWARDATION": "↓",
        "STRONG_BACKWARDATION": "↓↓",
    }.get(sig, "?")

    return f"VN30F {emoji} {fp:,.0f} {basis_str} [{sig}]"
