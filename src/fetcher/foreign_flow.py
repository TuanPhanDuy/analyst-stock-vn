"""
Foreign investor net buy/sell data for HOSE/HNX tickers.

Fetches from VNDirect FINFO API (public, no auth required).
Returns net foreign volume over 5-day and 20-day windows.
Signal direction: sustained foreign buying is bullish confirmation in VN market.
"""
import hashlib
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List

import requests

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://www.vndirect.com.vn",
    "Referer": "https://www.vndirect.com.vn/",
}
_VNDIRECT_BASE = "https://finfo-api.vndirect.com.vn/v4"


def _cache_path(key: str) -> Path:
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    return CACHE_DIR / f"ff_{h}.json"


def _load_cache(path: Path, ttl: int = 3600):
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _save_cache(path: Path, data) -> None:
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def get_foreign_flow(ticker: str, days: int = 60, ttl: int = 3600) -> dict:
    """
    Fetch foreign net buy/sell volume for one ticker from VNDirect.

    Returns:
        {
          ticker, net_volume_5d, net_volume_20d, latest_net,
          buy_volume_5d, sell_volume_5d, source, records
        }
    """
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=days)).isoformat()
    cache_key = f"ff_{ticker}_{start}_{end}"
    cache_file = _cache_path(cache_key)

    cached = _load_cache(cache_file, ttl)
    if cached is not None:
        return cached

    empty = {
        "ticker": ticker,
        "net_volume_5d": 0,
        "net_volume_20d": 0,
        "latest_net": 0,
        "buy_volume_5d": 0,
        "sell_volume_5d": 0,
        "source": "vndirect",
        "records": [],
        "error": None,
    }

    try:
        url = f"{_VNDIRECT_BASE}/stock_prices"
        params = {
            "sort": "date:desc",
            "size": days,
            "page": 1,
            "q": f"code:{ticker}~date:gte:{start}~date:lte:{end}",
            "fields": "code,date,foreignBuyVolume,foreignSellVolume,foreignNetVolume",
        }
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        records = resp.json().get("data", [])

        if not records:
            _save_cache(cache_file, empty)
            return empty

        # Sort ascending so index -1 = most recent
        records.sort(key=lambda r: r.get("date", ""))

        def _net(r):
            n = r.get("foreignNetVolume") or r.get("foreign_net_volume") or 0
            if n is None:
                buy = r.get("foreignBuyVolume") or 0
                sell = r.get("foreignSellVolume") or 0
                n = (buy or 0) - (sell or 0)
            return int(n)

        nets = [_net(r) for r in records]
        buys = [int(r.get("foreignBuyVolume") or 0) for r in records]
        sells = [int(r.get("foreignSellVolume") or 0) for r in records]

        result = {
            "ticker": ticker,
            "net_volume_5d": sum(nets[-5:]) if len(nets) >= 5 else sum(nets),
            "net_volume_20d": sum(nets[-20:]) if len(nets) >= 20 else sum(nets),
            "latest_net": nets[-1] if nets else 0,
            "buy_volume_5d": sum(buys[-5:]) if len(buys) >= 5 else sum(buys),
            "sell_volume_5d": sum(sells[-5:]) if len(sells) >= 5 else sum(sells),
            "source": "vndirect",
            "records": records[-5:],   # keep only latest 5 for cache size
            "error": None,
        }
        _save_cache(cache_file, result)
        return result

    except Exception as e:
        logger.debug("foreign_flow fetch failed %s: %s", ticker, e)
        err = {**empty, "error": str(e)}
        _save_cache(cache_file, err)   # cache failures briefly to avoid hammering API
        return err


def get_foreign_flow_bulk(tickers: List[str], days: int = 60, ttl: int = 3600) -> Dict[str, dict]:
    """Fetch foreign flow for all tickers. Returns {ticker: flow_dict}."""
    result = {}
    for ticker in tickers:
        try:
            result[ticker] = get_foreign_flow(ticker, days=days, ttl=ttl)
        except Exception as e:
            logger.warning("foreign_flow_bulk %s: %s", ticker, e)
            result[ticker] = {
                "ticker": ticker, "net_volume_5d": 0, "net_volume_20d": 0,
                "latest_net": 0, "buy_volume_5d": 0, "sell_volume_5d": 0,
                "source": "error", "records": [], "error": str(e),
            }
        time.sleep(0.08)   # polite pacing
    return result


def foreign_signal(flow: dict, strong_threshold: int = 100_000) -> dict:
    """
    Convert foreign flow data into a signal modifier.

    Args:
        flow: dict from get_foreign_flow()
        strong_threshold: net volume above which buying/selling is "strong"

    Returns:
        {
          direction: 1 (buying) | -1 (selling) | 0 (neutral),
          strength: 0.0–1.0,
          label: "STRONG_BUYING" | "BUYING" | "NEUTRAL" | "SELLING" | "STRONG_SELLING",
          net_5d, net_20d,
          score_delta: float to add to composite score (positive = bullish boost)
        }
    """
    net_5d = flow.get("net_volume_5d") or 0
    net_20d = flow.get("net_volume_20d") or 0

    if net_5d == 0 and net_20d == 0:
        return {
            "direction": 0, "strength": 0.0, "label": "NEUTRAL",
            "net_5d": 0, "net_20d": 0, "score_delta": 0.0,
        }

    # Both 5d and 20d agreeing is high-conviction
    both_agree = (net_5d > 0 and net_20d > 0) or (net_5d < 0 and net_20d < 0)

    if net_5d > 0:
        direction = 1
        is_strong = net_5d > strong_threshold
        label = "STRONG_BUYING" if is_strong else "BUYING"
        strength = min(1.0, 0.5 + min(net_5d / (strong_threshold * 2), 0.5))
        if not both_agree:
            strength *= 0.6  # discount if 20d disagrees
    elif net_5d < 0:
        direction = -1
        is_strong = abs(net_5d) > strong_threshold
        label = "STRONG_SELLING" if is_strong else "SELLING"
        strength = min(1.0, 0.5 + min(abs(net_5d) / (strong_threshold * 2), 0.5))
        if not both_agree:
            strength *= 0.6
    else:
        direction = 0
        label = "NEUTRAL"
        strength = 0.0

    # Score delta: maximum ±0.15 addition to composite score
    score_delta = direction * strength * 0.15

    return {
        "direction": direction,
        "strength": round(strength, 2),
        "label": label,
        "net_5d": net_5d,
        "net_20d": net_20d,
        "score_delta": round(score_delta, 3),
    }
