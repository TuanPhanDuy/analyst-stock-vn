"""
Primary data source using yfinance (.VN suffix for HOSE tickers).
Returns clean DataFrames with DatetimeIndex, columns: open high low close volume.
"""
import time
import warnings
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _retry(fn, attempts: int = 3, backoff: float = 2.0):
    last_exc = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if i < attempts - 1:
                time.sleep(backoff ** i)
    raise last_exc


def _sym(ticker: str) -> str:
    return ticker if ticker.endswith(".VN") else f"{ticker}.VN"


def _cache_path(key: str) -> Path:
    safe = key.replace(":", "_").replace("/", "_")
    return CACHE_DIR / f"{safe}.pkl"


def _load_cache(path: Path, ttl: int):
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        try:
            return pd.read_pickle(path)
        except Exception:
            pass
    return None


def get_ohlcv(ticker: str, days: int = 400, ttl: int = 3600) -> pd.DataFrame:
    """
    Fetch daily OHLCV for a VN ticker.
    Returns DataFrame with DatetimeIndex (tz-naive), columns: open high low close volume.
    Falls back to stale cache (up to 7 days) if the live fetch fails.
    Raises ValueError if fewer than 60 rows returned.
    """
    path = _cache_path(f"yf_{ticker}_{days}")
    cached = _load_cache(path, ttl)
    if cached is not None:
        return cached

    sym = _sym(ticker)
    start = (date.today() - timedelta(days=days)).isoformat()
    try:
        df = _retry(lambda: yf.download(sym, start=start, auto_adjust=True, progress=False))
    except Exception:
        df = pd.DataFrame()

    if df.empty:
        # Try serving stale cache before giving up (tolerates transient API failures)
        stale = _load_cache(path, ttl=7 * 86400)
        if stale is not None:
            return stale
        raise ValueError(f"No data returned for {ticker}")

    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df[["open", "high", "low", "close", "volume"]].dropna()

    if len(df) < 60:
        raise ValueError(f"Only {len(df)} rows for {ticker}, need ≥60")

    df.to_pickle(path)
    return df


def get_ohlcv_bulk(tickers: list, days: int = 400) -> dict:
    """
    Fetch all tickers via one bulk API call, then fall back to per-ticker
    fetching for any that are missing or empty in the bulk result.
    Returns {ticker: df} for successful ones.
    """
    syms = [_sym(t) for t in tickers]
    start = (date.today() - timedelta(days=days)).isoformat()
    result = {}

    # ── 1. Bulk fetch ─────────────────────────────────────────────────────────
    try:
        raw = _retry(
            lambda: yf.download(
                syms, start=start, auto_adjust=True,
                group_by="ticker", progress=False,
            )
        )
        for ticker, sym in zip(tickers, syms):
            try:
                df = raw.copy() if len(tickers) == 1 else raw[sym].copy()
                df.columns = [c.lower() for c in df.columns]
                df.index = pd.to_datetime(df.index).tz_localize(None)
                df = df[["open", "high", "low", "close", "volume"]].dropna()
                if len(df) >= 60:
                    result[ticker] = df
            except Exception:
                pass
    except Exception:
        pass  # bulk totally failed — will retry per-ticker below

    # ── 2. Per-ticker fallback for anything missing ───────────────────────────
    missing = [t for t in tickers if t not in result]
    if missing:
        print(f"  [fallback] Fetching {len(missing)} tickers individually…")
        for ticker in missing:
            try:
                result[ticker] = get_ohlcv(ticker, days=days)
            except Exception as e:
                print(f"  [warn] {ticker}: {e}")
            time.sleep(0.3)   # be polite to Yahoo Finance

    return result


def get_data_freshness(df: pd.DataFrame) -> dict:
    """Return metadata about how current the data is."""
    last_date = df.index[-1].date()
    today = date.today()
    days_old = (today - last_date).days
    # VN market closed weekends — 1 day old on Monday is fine
    is_fresh = days_old <= 3
    return {
        "last_date": last_date.isoformat(),
        "days_old": days_old,
        "is_fresh": is_fresh,
        "rows": len(df),
    }
