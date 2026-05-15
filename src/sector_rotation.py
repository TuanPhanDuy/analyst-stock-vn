"""
Sector rotation analysis for the VN market.

Identifies leading and lagging sectors using:
  - 1-month and 3-month relative return vs VN30 average
  - % of sector stocks above MA20 (breadth)
  - Momentum acceleration (1M vs 3M rank change)

Usage:
    from src.sector_rotation import analyze, rotation_report
    report = analyze(ohlcv_map, sector_map, cfg)
"""
from __future__ import annotations

from typing import Dict, List


def sector_momentum(ohlcv_map: dict, sector_map: dict) -> dict:
    """
    Compute momentum score for each sector.

    Returns:
        {sector: {score, label, avg_return_1m_pct, avg_return_3m_pct,
                  above_ma20_pct, ticker_count, tickers}}
    """
    results = {}

    for sector, tickers in sector_map.items():
        returns_1m, returns_3m = [], []
        above_ma20 = 0
        total = 0

        for t in tickers:
            df = ohlcv_map.get(t)
            if df is None or len(df) < 20:
                continue
            total += 1
            close = df["close"]

            r1m = (float(close.iloc[-1]) / float(close.iloc[-20]) - 1) * 100
            r3m = (float(close.iloc[-1]) / float(close.iloc[max(0, len(close) - 60)]) - 1) * 100

            returns_1m.append(r1m)
            returns_3m.append(r3m)

            ma20 = float(close.rolling(20).mean().iloc[-1])
            if float(close.iloc[-1]) > ma20:
                above_ma20 += 1

        if not returns_1m:
            continue

        avg_1m = sum(returns_1m) / len(returns_1m)
        avg_3m = sum(returns_3m) / len(returns_3m) if returns_3m else 0.0
        above_pct = above_ma20 / total * 100 if total else 0

        # Composite score: blend return (60%) + breadth vs neutral (40%)
        score = avg_1m * 0.6 + (above_pct - 50.0) * 0.4

        if score > 8:
            label = "STRONG"
        elif score > 2:
            label = "IMPROVING"
        elif score > -2:
            label = "NEUTRAL"
        elif score > -8:
            label = "WEAK"
        else:
            label = "LAGGING"

        results[sector] = {
            "score": round(score, 2),
            "label": label,
            "avg_return_1m_pct": round(avg_1m, 1),
            "avg_return_3m_pct": round(avg_3m, 1),
            "above_ma20_pct": round(above_pct, 0),
            "ticker_count": total,
            "tickers": tickers,
        }

    return results


def relative_strength_rank(sector_momentum_data: dict) -> List[dict]:
    """
    Rank sectors by relative strength (momentum score).

    Returns sorted list of dicts with rank and rotation_signal added.
    """
    entries = [
        {"sector": s, **v}
        for s, v in sector_momentum_data.items()
    ]
    entries.sort(key=lambda x: x["score"], reverse=True)
    n = len(entries)

    for i, e in enumerate(entries):
        e["rank"] = i + 1
        top_third = max(1, n // 3)
        bottom_third = max(1, 2 * n // 3)
        if i < top_third:
            e["rotation_signal"] = "ROTATE_IN"
        elif i >= bottom_third:
            e["rotation_signal"] = "ROTATE_OUT"
        else:
            e["rotation_signal"] = "HOLD"

    return entries


def _market_avg_return(ohlcv_map: dict, period: int = 20) -> float:
    """Equal-weighted market average return over `period` bars."""
    rets = []
    for df in ohlcv_map.values():
        if df is not None and len(df) >= period:
            r = float(df["close"].iloc[-1]) / float(df["close"].iloc[-period]) - 1
            rets.append(r * 100)
    return sum(rets) / len(rets) if rets else 0.0


def sector_vs_market(sector_data: dict, ohlcv_map: dict) -> dict:
    """Add relative-to-market return to each sector entry."""
    mkt_1m = _market_avg_return(ohlcv_map, 20)
    mkt_3m = _market_avg_return(ohlcv_map, 60)

    enriched = {}
    for sector, data in sector_data.items():
        enriched[sector] = {
            **data,
            "alpha_1m_pct": round(data["avg_return_1m_pct"] - mkt_1m, 1),
            "alpha_3m_pct": round(data["avg_return_3m_pct"] - mkt_3m, 1),
            "market_1m_pct": round(mkt_1m, 1),
        }

    return enriched


def analyze(ohlcv_map: dict, sector_map: dict, cfg: dict = None) -> dict:
    """
    Full sector rotation analysis.

    Returns:
        {
          rankings: [...],           sorted by score
          rotate_into: [sector],     top performers
          rotate_out_of: [sector],   laggards
          watch: [sector],
          spread_pct: float,         return spread between top and bottom sector
          actionable: bool,          True if spread > min_return_spread threshold
          summary: str
        }
    """
    if cfg is None:
        cfg = {}
    sr_cfg = cfg.get("sector_rotation", {})
    min_spread = sr_cfg.get("min_return_spread", 5.0)

    mom = sector_momentum(ohlcv_map, sector_map)
    mom_vs_market = sector_vs_market(mom, ohlcv_map)
    rankings = relative_strength_rank(mom_vs_market)

    if not rankings:
        return {
            "rankings": [],
            "rotate_into": [],
            "rotate_out_of": [],
            "watch": [],
            "spread_pct": 0.0,
            "actionable": False,
            "summary": "Insufficient data",
        }

    rotate_into = [e["sector"] for e in rankings if e["rotation_signal"] == "ROTATE_IN"]
    rotate_out = [e["sector"] for e in rankings if e["rotation_signal"] == "ROTATE_OUT"]
    watch = [e["sector"] for e in rankings if e["rotation_signal"] == "HOLD"]

    top_score = rankings[0]["score"]
    bottom_score = rankings[-1]["score"]
    spread = round(abs(top_score - bottom_score), 1)
    actionable = spread >= min_spread

    if actionable:
        summary = (
            f"Rotate INTO {', '.join(rotate_into[:2])} "
            f"({rankings[0]['avg_return_1m_pct']:+.1f}% 1M). "
            f"Reduce {', '.join(rotate_out[:2])} "
            f"({rankings[-1]['avg_return_1m_pct']:+.1f}% 1M). "
            f"Spread: {spread:.1f}pts."
        )
    else:
        summary = f"No clear rotation opportunity (spread {spread:.1f}pts < {min_spread}pts threshold)."

    return {
        "rankings": rankings,
        "rotate_into": rotate_into,
        "rotate_out_of": rotate_out,
        "watch": watch,
        "spread_pct": spread,
        "actionable": actionable,
        "summary": summary,
    }


def ticker_sector(ticker: str, sector_map: dict) -> str | None:
    """Look up which sector a ticker belongs to."""
    for sector, tickers in sector_map.items():
        if ticker in tickers:
            return sector
    return None


def sector_bias(ticker: str, rotation_result: dict, sector_map: dict) -> dict:
    """
    Given a ticker, return a directional bias based on its sector's rotation signal.

    Returns:
        {sector, signal: "ROTATE_IN"|"ROTATE_OUT"|"HOLD"|"UNKNOWN", score_delta: float}
    """
    sector = ticker_sector(ticker, sector_map)
    if sector is None:
        return {"sector": None, "signal": "UNKNOWN", "score_delta": 0.0}

    for entry in rotation_result.get("rankings", []):
        if entry["sector"] == sector:
            sig = entry.get("rotation_signal", "HOLD")
            delta = 0.10 if sig == "ROTATE_IN" else (-0.10 if sig == "ROTATE_OUT" else 0.0)
            return {"sector": sector, "signal": sig, "score_delta": delta}

    return {"sector": sector, "signal": "HOLD", "score_delta": 0.0}
