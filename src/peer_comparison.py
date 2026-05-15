"""
Peer comparison — rank a ticker vs sector peers on fundamentals and momentum.

Pulls fundamentals from VNDirect FINFO, price momentum from OHLCV.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Dict, List, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}
_FINFO = "https://finfo-api.vndirect.com.vn/v4"


def _get_fundamentals(ticker: str) -> dict:
    """Fetch latest fundamentals from VNDirect ratio endpoint."""
    try:
        url = f"{_FINFO}/ratios/latest"
        params = {"q": f"code:{ticker}", "fields": "code,pe,pb,roe,roa,eps,revenueGrowth,earningGrowth,debtToEquity"}
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=8)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if data:
            return data[0]
    except Exception as e:
        logger.debug("Fundamentals %s: %s", ticker, e)
    return {"code": ticker}


def _price_momentum(ohlcv: pd.DataFrame) -> dict:
    """Return 1M/3M returns and current price vs MA20/MA50."""
    if ohlcv is None or ohlcv.empty:
        return {}
    c = ohlcv["close"]
    ret_1m = float((c.iloc[-1] / c.iloc[-22] - 1) * 100) if len(c) >= 22 else None
    ret_3m = float((c.iloc[-1] / c.iloc[-66] - 1) * 100) if len(c) >= 66 else None
    ma20 = float(c.tail(20).mean())
    ma50 = float(c.tail(50).mean()) if len(c) >= 50 else None
    price = float(c.iloc[-1])
    return {
        "price": price,
        "ret_1m_pct": round(ret_1m, 2) if ret_1m is not None else None,
        "ret_3m_pct": round(ret_3m, 2) if ret_3m is not None else None,
        "vs_ma20_pct": round((price / ma20 - 1) * 100, 2),
        "vs_ma50_pct": round((price / ma50 - 1) * 100, 2) if ma50 else None,
    }


def _safe_float(val) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _rank_metric(values: List[Optional[float]], higher_is_better: bool = True) -> List[Optional[float]]:
    """Return percentile ranks (0-100) for a list of values. None → None."""
    valid = [(i, v) for i, v in enumerate(values) if v is not None]
    if not valid:
        return [None] * len(values)
    sorted_vals = sorted(valid, key=lambda x: x[1])
    rank_map = {}
    n = len(sorted_vals)
    for rank, (i, _) in enumerate(sorted_vals):
        pct = (rank / (n - 1)) * 100 if n > 1 else 50.0
        rank_map[i] = round(pct if higher_is_better else 100 - pct, 1)
    return [rank_map.get(i) for i in range(len(values))]


def compare(
    ticker: str,
    peers: List[str],
    ohlcv_map: Optional[Dict[str, pd.DataFrame]] = None,
) -> dict:
    """
    Compare ticker vs peers on P/E, P/B, ROE, momentum.

    Returns:
        {
          ticker, sector_peers, metrics (list per ticker),
          ticker_rank: {pe_rank, pb_rank, roe_rank, momentum_rank, overall_rank},
          summary: str
        }
    """
    all_tickers = [ticker] + [p for p in peers if p != ticker]
    fundamentals = {}
    momentum = {}

    for t in all_tickers:
        fundamentals[t] = _get_fundamentals(t)
        if ohlcv_map and t in ohlcv_map:
            momentum[t] = _price_momentum(ohlcv_map[t])
        else:
            momentum[t] = {}
        time.sleep(0.05)

    # Build metric table
    pe_vals = [_safe_float(fundamentals[t].get("pe")) for t in all_tickers]
    pb_vals = [_safe_float(fundamentals[t].get("pb")) for t in all_tickers]
    roe_vals = [_safe_float(fundamentals[t].get("roe")) for t in all_tickers]
    mom_vals = [momentum[t].get("ret_1m_pct") for t in all_tickers]

    # Lower P/E, P/B = better value (invert)
    pe_ranks = _rank_metric(pe_vals, higher_is_better=False)
    pb_ranks = _rank_metric(pb_vals, higher_is_better=False)
    roe_ranks = _rank_metric(roe_vals, higher_is_better=True)
    mom_ranks = _rank_metric(mom_vals, higher_is_better=True)

    metrics = []
    for i, t in enumerate(all_tickers):
        fund = fundamentals[t]
        mom = momentum[t]
        ranks = [pe_ranks[i], pb_ranks[i], roe_ranks[i], mom_ranks[i]]
        valid_ranks = [r for r in ranks if r is not None]
        overall = round(sum(valid_ranks) / len(valid_ranks), 1) if valid_ranks else None
        metrics.append({
            "ticker": t,
            "pe": pe_vals[i],
            "pb": pb_vals[i],
            "roe": roe_vals[i],
            "ret_1m_pct": mom_vals[i],
            "ret_3m_pct": mom.get("ret_3m_pct"),
            "vs_ma20_pct": mom.get("vs_ma20_pct"),
            "pe_rank": pe_ranks[i],
            "pb_rank": pb_ranks[i],
            "roe_rank": roe_ranks[i],
            "momentum_rank": mom_ranks[i],
            "overall_rank": overall,
            "is_target": t == ticker,
        })

    metrics.sort(key=lambda x: -(x["overall_rank"] or 0))

    target_metric = next((m for m in metrics if m["ticker"] == ticker), None)
    target_rank = next((i + 1 for i, m in enumerate(metrics) if m["ticker"] == ticker), None)

    # Summary sentence
    n_peers = len(all_tickers)
    if target_metric and target_rank:
        summary = (
            f"{ticker} ranks #{target_rank}/{n_peers} overall vs sector peers. "
        )
        if target_metric.get("pe_rank") is not None:
            val_desc = "cheap" if target_metric["pe_rank"] > 60 else ("expensive" if target_metric["pe_rank"] < 40 else "fair-valued")
            summary += f"Valuation is {val_desc} (P/E rank {target_metric['pe_rank']:.0f}th pct). "
        if target_metric.get("roe_rank") is not None:
            roe_desc = "high" if target_metric["roe_rank"] > 60 else ("low" if target_metric["roe_rank"] < 40 else "average")
            summary += f"ROE is {roe_desc}. "
    else:
        summary = f"No ranking data for {ticker}."

    return {
        "ticker": ticker,
        "sector_peers": peers,
        "metrics": metrics,
        "ticker_rank": {
            "overall": target_rank,
            "out_of": n_peers,
            "pe_pct": target_metric.get("pe_rank") if target_metric else None,
            "pb_pct": target_metric.get("pb_rank") if target_metric else None,
            "roe_pct": target_metric.get("roe_rank") if target_metric else None,
            "momentum_pct": target_metric.get("momentum_rank") if target_metric else None,
        },
        "summary": summary,
    }


def peers_from_sector(ticker: str, sector_map: dict, max_peers: int = 8) -> List[str]:
    """Return peers for a ticker from a sector map."""
    ticker_sector = next(
        (sector for sector, tickers in sector_map.items() if ticker in tickers),
        None,
    )
    if not ticker_sector:
        return []
    peers = [t for t in sector_map[ticker_sector] if t != ticker]
    return peers[:max_peers]
