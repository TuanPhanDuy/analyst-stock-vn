"""
Data access layer. Tries vnstock3 first, falls back to vnstock (legacy).
All methods return pandas DataFrames with DatetimeIndex.
Responses are cached to data/cache/ to avoid rate-limiting.
"""
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd

CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_pkl(key: str, ttl: int, fetch_fn) -> pd.DataFrame:
    path = CACHE_DIR / (hashlib.sha256(key.encode()).hexdigest()[:16] + ".pkl")
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        return pd.read_pickle(path)
    df = fetch_fn()
    df.to_pickle(path)
    return df


def _cache_json(key: str, ttl: int, fetch_fn) -> Any:
    path = CACHE_DIR / (hashlib.sha256(key.encode()).hexdigest()[:16] + ".json")
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        with open(path) as f:
            return json.load(f)
    data = fetch_fn()
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def _make_stock(ticker: str):
    """Return a vnstock stock object, trying vnstock3 then legacy vnstock."""
    try:
        from vnstock3 import Vnstock  # type: ignore
        return Vnstock().stock(symbol=ticker, source="VCI")
    except ImportError:
        pass
    try:
        from vnstock import Vnstock  # type: ignore
        return Vnstock().stock(symbol=ticker, source="VCI")
    except Exception:
        pass
    raise ImportError("Install vnstock3: pip install vnstock3")


def get_ohlcv(ticker: str, start: str, end: str, ttl: int = 3600) -> pd.DataFrame:
    """Daily OHLCV. Columns: open high low close volume. Index: DatetimeIndex."""
    def fetch():
        stock = _make_stock(ticker)
        df = stock.quote.history(start=start, end=end, interval="1D")
        df.index = pd.to_datetime(df.index)
        df.columns = [c.lower() for c in df.columns]
        return df[["open", "high", "low", "close", "volume"]]
    return _cache_pkl(f"ohlcv:{ticker}:{start}:{end}", ttl, fetch)


def get_financials(ticker: str, ttl: int = 86400) -> dict:
    """Latest fundamental ratios: pe, pb, roe, eps."""
    def fetch():
        stock = _make_stock(ticker)
        try:
            ratio = stock.finance.ratio(period="year", lang="en")
            if ratio.empty:
                return {}
            latest = ratio.iloc[-1].to_dict()
            return {
                "pe": latest.get("P/E") or latest.get("pe"),
                "pb": latest.get("P/B") or latest.get("pb"),
                "roe": latest.get("ROE") or latest.get("roe"),
                "eps": latest.get("EPS") or latest.get("eps"),
            }
        except Exception:
            return {}
    return _cache_json(f"fin:{ticker}", ttl, fetch)


def get_company_overview(ticker: str) -> dict:
    """Sector, industry, market cap."""
    try:
        stock = _make_stock(ticker)
        info = stock.company.overview()
        return info.to_dict(orient="records")[0] if not info.empty else {}
    except Exception:
        return {}
