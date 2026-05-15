"""
Insider and major shareholder transaction tracker for HOSE/HNX tickers.

Data source: VNDirect FINFO API insider_dealings endpoint.
HOSE/SSC regulations require major shareholders (≥5%) and insiders
to disclose trades within 3 business days.

Net insider buying is a high-conviction fundamental signal:
insiders know their company better than any analyst.
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
}
_VNDIRECT_BASE = "https://finfo-api.vndirect.com.vn/v4"

# Vietnamese terms for buy/sell in disclosure filings
_BUY_KEYWORDS = {"BUY", "MUA", "ĐĂNG KÝ MUA", "MUA THÊM", "NHẬN CHUYỂN NHƯỢNG"}
_SELL_KEYWORDS = {"SELL", "BÁN", "ĐĂNG KÝ BÁN", "BÁN BỚT", "CHUYỂN NHƯỢNG"}


def _cache_path(key: str) -> Path:
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    return CACHE_DIR / f"ins_{h}.json"


def _load_cache(path: Path, ttl: int):
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def get_transactions(ticker: str, days: int = 90, ttl: int = 21_600) -> List[dict]:
    """
    Fetch insider/major shareholder transactions from VNDirect.

    Returns list of transaction dicts:
        [{date, name, position, type, qty, price, value_vnd}]
    """
    cache_key = f"ins_{ticker}_{date.today().isoformat()}_{days}"
    cache_file = _cache_path(cache_key)
    cached = _load_cache(cache_file, ttl)
    if cached is not None:
        return cached

    transactions = []
    start = (date.today() - timedelta(days=days)).isoformat()

    try:
        url = f"{_VNDIRECT_BASE}/insider_dealings"
        params = {
            "q": f"code:{ticker}~dealingDate:gte:{start}",
            "sort": "dealingDate:desc",
            "size": 100,
        }
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        for item in resp.json().get("data", []):
            qty = int(item.get("quantity") or 0)
            price = float(item.get("price") or 0)
            transactions.append({
                "date": (item.get("dealingDate") or "")[:10],
                "name": item.get("dealerName") or "",
                "position": item.get("dealerPosition") or "",
                "type": (item.get("dealingType") or "").upper().strip(),
                "qty": qty,
                "price": price,
                "value_vnd": round(qty * price),
            })
    except Exception as e:
        logger.debug("insider fetch %s: %s", ticker, e)

    try:
        with open(cache_file, "w") as f:
            json.dump(transactions, f)
    except Exception:
        pass

    return transactions


def signal(transactions: List[dict], recent_days: int = 30) -> dict:
    """
    Convert transaction list to an insider signal.

    Considers only transactions in the last recent_days.
    Net buying (buy_qty - sell_qty > 0) = bullish insider signal.

    Returns:
        {
          direction: 1 (buying) | -1 (selling) | 0 (neutral),
          strength: 0.0–1.0,
          label: "INSIDER_BUYING" | "INSIDER_SELLING" | "NEUTRAL" | "NO_DATA",
          net_qty, buy_qty, sell_qty,
          transaction_count,
          score_delta: float
        }
    """
    if not transactions:
        return {
            "direction": 0, "strength": 0.0, "label": "NO_DATA",
            "net_qty": 0, "buy_qty": 0, "sell_qty": 0,
            "transaction_count": 0, "score_delta": 0.0,
        }

    cutoff = (date.today() - timedelta(days=recent_days)).isoformat()
    recent = [t for t in transactions if t.get("date", "") >= cutoff]

    if not recent:
        return {
            "direction": 0, "strength": 0.0, "label": "NO_DATA",
            "net_qty": 0, "buy_qty": 0, "sell_qty": 0,
            "transaction_count": 0, "score_delta": 0.0,
        }

    buy_qty = sum(
        t["qty"] for t in recent
        if any(kw in t.get("type", "") for kw in _BUY_KEYWORDS)
    )
    sell_qty = sum(
        t["qty"] for t in recent
        if any(kw in t.get("type", "") for kw in _SELL_KEYWORDS)
    )
    net_qty = buy_qty - sell_qty

    # Strength scales with volume — 500k shares = max strength
    ref_volume = 500_000
    if net_qty > 0:
        direction = 1
        label = "INSIDER_BUYING"
        strength = min(0.85, 0.40 + min(net_qty / ref_volume, 1.0) * 0.45)
    elif net_qty < 0:
        direction = -1
        label = "INSIDER_SELLING"
        strength = min(0.85, 0.40 + min(abs(net_qty) / ref_volume, 1.0) * 0.45)
    else:
        direction = 0
        label = "NEUTRAL"
        strength = 0.0

    # Score delta: max ±0.20 addition to composite score
    score_delta = direction * strength * 0.20

    return {
        "direction": direction,
        "strength": round(strength, 2),
        "label": label,
        "net_qty": net_qty,
        "buy_qty": buy_qty,
        "sell_qty": sell_qty,
        "transaction_count": len(recent),
        "score_delta": round(score_delta, 3),
    }


def bulk_signals(tickers: List[str], days: int = 90, ttl: int = 21_600) -> Dict[str, dict]:
    """
    Fetch and compute insider signals for all tickers.
    Returns {ticker: signal_dict}.
    """
    result = {}
    for t in tickers:
        try:
            txns = get_transactions(t, days=days, ttl=ttl)
            result[t] = signal(txns)
        except Exception as e:
            logger.warning("insider bulk %s: %s", t, e)
            result[t] = {
                "direction": 0, "strength": 0.0, "label": "ERROR",
                "net_qty": 0, "buy_qty": 0, "sell_qty": 0,
                "transaction_count": 0, "score_delta": 0.0,
            }
        time.sleep(0.05)
    return result
