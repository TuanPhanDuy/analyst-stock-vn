"""
Data access layer using vnstock (legacy 0.2.x ohlc_data API).
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


# Columns the legacy vnstock.ohlc_data endpoint is expected to return.
_OHLCV_REQUIRED = ("time", "open", "high", "low", "close", "volume")


def _normalize_ohlcv(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """
    Validate and normalize a raw vnstock.ohlc_data frame into the project's
    canonical OHLCV shape: DatetimeIndex + open/high/low/close/volume in VND.

    Raises ValueError with an actionable message if the upstream schema changed
    (missing columns / empty payload) rather than letting a bare KeyError leak.
    """
    if df is None or len(df) == 0:
        raise ValueError(f"vnstock returned no OHLCV rows for {ticker}")
    df = df.rename(columns={c: c.lower() for c in df.columns})
    missing = [c for c in _OHLCV_REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(
            f"vnstock OHLCV schema for {ticker} missing columns {missing}; "
            f"got {list(df.columns)}. The vnstock API may have changed — "
            f"update src/fetcher/vnstock_client._normalize_ohlcv."
        )
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time").sort_index()
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float) * 1000  # nghìn VND → VND
    df["volume"] = df["volume"].astype(float)
    return df[["open", "high", "low", "close", "volume"]]


def get_ohlcv(ticker: str, start: str, end: str, ttl: int = 3600) -> pd.DataFrame:
    """Daily OHLCV. Columns: open high low close volume. Index: DatetimeIndex."""
    def fetch():
        import warnings
        import vnstock  # type: ignore
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = vnstock.ohlc_data(ticker, start_date=start, end_date=end)
        return _normalize_ohlcv(df, ticker)
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
