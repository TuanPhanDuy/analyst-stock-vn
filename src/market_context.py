"""
Market-wide context: VN-Index trend, sector performance, breadth indicators.
Provides the macro backdrop for individual stock signals.
"""
import warnings
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

# VN30 sector mapping
SECTORS = {
    "Banking":     ["VCB", "BID", "CTG", "TCB", "VPB", "MBB", "ACB", "STB", "HDB", "SHB", "EIB"],
    "Real Estate": ["VHM", "VIC", "KDH", "TCH"],
    "Securities":  ["SSI", "VCI", "HCM", "VND"],
    "Consumer":    ["VNM", "MSN", "MWG", "SAB", "PNJ"],
    "Industrial":  ["HPG", "REE"],
    "Energy":      ["GAS", "PLX", "POW"],
    "Technology":  ["FPT"],
}


def _synthetic_index(ohlcv_map: dict) -> pd.DataFrame:
    """Equal-weighted VN30 price index from our own data."""
    closes = {}
    for ticker, df in ohlcv_map.items():
        if df is not None and len(df) >= 20:
            closes[ticker] = df["close"]
    if not closes:
        return pd.DataFrame()
    combined = pd.DataFrame(closes).dropna(how="all")
    # Normalise each series to 100 at start, then average
    normed = combined.apply(lambda s: s / s.iloc[0] * 100, axis=0)
    combined["index"] = normed.mean(axis=1)
    return combined[["index"]].rename(columns={"index": "close"})


def index_trend(df: pd.DataFrame) -> dict:
    """Summarise VN30 composite index trend."""
    if df.empty or len(df) < 20:
        return {"status": "unavailable"}

    close = df["close"]
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean() if len(close) >= 60 else close.rolling(len(close)).mean()
    price = float(close.iloc[-1])
    m1_return = (price / float(close.iloc[-20]) - 1) * 100
    m3_return = (price / float(close.iloc[max(0, len(close)-60)]) - 1) * 100

    above_ma20 = price > float(ma20.iloc[-1])
    above_ma60 = price > float(ma60.iloc[-1])

    if above_ma20 and above_ma60 and m1_return > 0:
        trend = "BULLISH"
    elif not above_ma20 and not above_ma60 and m1_return < 0:
        trend = "BEARISH"
    else:
        trend = "MIXED"

    return {
        "price": round(price, 1),
        "trend": trend,
        "ma20": round(float(ma20.iloc[-1]), 1),
        "ma60": round(float(ma60.iloc[-1]), 1),
        "above_ma20": above_ma20,
        "above_ma60": above_ma60,
        "m1_return_pct": round(m1_return, 1),
        "m3_return_pct": round(m3_return, 1),
    }


def sector_performance(ohlcv_map: dict, sector_map: dict = None) -> dict:
    """Average 1M and 3M return per sector."""
    mapping = sector_map if sector_map is not None else SECTORS
    results = {}
    for sector, tickers in mapping.items():
        returns_1m, returns_3m = [], []
        for t in tickers:
            df = ohlcv_map.get(t)
            if df is None or len(df) < 20:
                continue
            close = df["close"]
            r1m = (float(close.iloc[-1]) / float(close.iloc[-20]) - 1) * 100
            r3m = (float(close.iloc[-1]) / float(close.iloc[max(0, len(close)-60)]) - 1) * 100
            returns_1m.append(r1m)
            returns_3m.append(r3m)
        if returns_1m:
            results[sector] = {
                "return_1m": round(sum(returns_1m) / len(returns_1m), 1),
                "return_3m": round(sum(returns_3m) / len(returns_3m), 1),
            }
    return results


def market_breadth(ohlcv_map: dict) -> dict:
    """% of VN30 stocks above MA20 and MA60, and advancing today."""
    above_ma20, above_ma60, advancing = 0, 0, 0
    total = 0
    for ticker, df in ohlcv_map.items():
        if df is None or len(df) < 20:
            continue
        total += 1
        close = df["close"]
        price = float(close.iloc[-1])
        if price > float(close.rolling(20).mean().iloc[-1]):
            above_ma20 += 1
        if len(close) >= 60 and price > float(close.rolling(60).mean().iloc[-1]):
            above_ma60 += 1
        if len(close) >= 2 and float(close.iloc[-1]) > float(close.iloc[-2]):
            advancing += 1

    if total == 0:
        return {}
    return {
        "total": total,
        "pct_above_ma20": round(above_ma20 / total * 100),
        "pct_above_ma60": round(above_ma60 / total * 100),
        "pct_advancing": round(advancing / total * 100),
        "breadth": (
            "STRONG" if above_ma20 / total > 0.65
            else "WEAK" if above_ma20 / total < 0.35
            else "NEUTRAL"
        ),
    }


def build(ohlcv_map: dict, sector_map: dict = None) -> dict:
    """Return full market context dict."""
    vnindex_df = _synthetic_index(ohlcv_map)
    return {
        "vnindex": index_trend(vnindex_df),
        "sectors": sector_performance(ohlcv_map, sector_map=sector_map),
        "breadth": market_breadth(ohlcv_map),
    }
