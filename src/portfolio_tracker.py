"""
Daily NAV tracker — equity curve, drawdown, CAGR vs VN-Index benchmark.

Usage:
    from src.portfolio_tracker import PortfolioTracker
    tracker = PortfolioTracker()
    tracker.record_nav(date.today(), positions, cash)
    summary = tracker.summary()
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

NAV_FILE = Path(__file__).parent.parent / "data" / "portfolio_nav.json"
NAV_FILE.parent.mkdir(parents=True, exist_ok=True)


def _load_nav_history() -> List[dict]:
    if NAV_FILE.exists():
        try:
            return json.loads(NAV_FILE.read_text())
        except Exception:
            pass
    return []


def _save_nav_history(history: List[dict]) -> None:
    NAV_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2))


def record_nav(nav_date: date, positions: Dict[str, dict], cash: float) -> dict:
    """
    Record a daily NAV snapshot.

    positions: {ticker: {quantity, avg_cost, current_price}}
    Returns the snapshot dict.
    """
    equity = sum(
        p["quantity"] * p.get("current_price", p.get("avg_cost", 0))
        for p in positions.values()
    )
    total_nav = equity + cash
    cost_basis = sum(p["quantity"] * p["avg_cost"] for p in positions.values())
    unrealized_pnl = equity - cost_basis

    snapshot = {
        "date": nav_date.isoformat(),
        "nav": round(total_nav, 2),
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "cost_basis": round(cost_basis, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "positions": {
            t: {
                "qty": p["quantity"],
                "cost": p["avg_cost"],
                "price": p.get("current_price", p.get("avg_cost", 0)),
            }
            for t, p in positions.items()
        },
    }

    history = _load_nav_history()
    # Replace existing entry for today
    history = [h for h in history if h["date"] != snapshot["date"]]
    history.append(snapshot)
    history.sort(key=lambda x: x["date"])
    _save_nav_history(history)
    return snapshot


def nav_series() -> pd.Series:
    """Return NAV history as a pandas Series indexed by date."""
    history = _load_nav_history()
    if not history:
        return pd.Series(dtype=float)
    dates = pd.to_datetime([h["date"] for h in history])
    values = [h["nav"] for h in history]
    return pd.Series(values, index=dates, name="NAV")


def equity_curve() -> pd.DataFrame:
    """Return full equity curve with returns and drawdown columns."""
    nav = nav_series()
    if nav.empty:
        return pd.DataFrame()

    df = pd.DataFrame({"nav": nav})
    df["daily_return"] = df["nav"].pct_change()
    df["cumulative_return"] = df["nav"] / df["nav"].iloc[0] - 1
    rolling_max = df["nav"].cummax()
    df["drawdown"] = (df["nav"] - rolling_max) / rolling_max
    return df


def _fetch_vnindex_returns(start: date, end: date) -> pd.Series:
    """Fetch VN-Index (^VNINDEX) returns via yfinance."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("^VNINDEX")
        hist = ticker.history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat())
        if hist.empty:
            return pd.Series(dtype=float)
        closes = hist["Close"]
        closes.index = closes.index.tz_localize(None)
        return closes
    except Exception as e:
        logger.debug("VNIndex fetch: %s", e)
        return pd.Series(dtype=float)


def summary(benchmark: bool = True) -> dict:
    """
    Compute portfolio performance summary.

    Returns dict with keys:
        nav_current, nav_start, total_return_pct, cagr_pct,
        max_drawdown_pct, sharpe, benchmark_return_pct (if available),
        alpha_pct, trading_days, nav_series (list of {date, nav})
    """
    df = equity_curve()
    if df.empty:
        return {"error": "no NAV history recorded yet"}

    nav_start = df["nav"].iloc[0]
    nav_current = df["nav"].iloc[-1]
    total_return = nav_current / nav_start - 1
    trading_days = len(df)
    years = trading_days / 252

    cagr = (1 + total_return) ** (1 / max(years, 1 / 252)) - 1 if years > 0 else 0.0
    max_drawdown = df["drawdown"].min()

    daily_ret = df["daily_return"].dropna()
    sharpe = 0.0
    if len(daily_ret) > 1 and daily_ret.std() > 0:
        sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252)

    result: dict = {
        "nav_current": round(nav_current, 2),
        "nav_start": round(nav_start, 2),
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "sharpe": round(sharpe, 2),
        "trading_days": trading_days,
        "benchmark_return_pct": None,
        "alpha_pct": None,
        "nav_series": [
            {"date": str(d.date()), "nav": v}
            for d, v in zip(df.index, df["nav"])
        ],
    }

    if benchmark:
        start_dt = df.index[0].date()
        end_dt = df.index[-1].date()
        vni = _fetch_vnindex_returns(start_dt, end_dt)
        if not vni.empty and len(vni) >= 2:
            bench_return = vni.iloc[-1] / vni.iloc[0] - 1
            result["benchmark_return_pct"] = round(bench_return * 100, 2)
            result["alpha_pct"] = round((total_return - bench_return) * 100, 2)

    return result


def drawdown_periods(threshold: float = -0.05) -> List[dict]:
    """Return drawdown periods where drawdown exceeded threshold."""
    df = equity_curve()
    if df.empty:
        return []

    in_dd = False
    dd_start = None
    periods = []
    peak = df["nav"].iloc[0]

    for dt, row in df.iterrows():
        nav = row["nav"]
        if nav > peak:
            if in_dd:
                periods.append({
                    "start": str(dd_start.date()),
                    "end": str(dt.date()),
                    "max_drawdown_pct": round(row["drawdown"] * 100, 2),
                    "recovery_days": (dt - dd_start).days,
                })
                in_dd = False
            peak = nav
        if row["drawdown"] < threshold and not in_dd:
            in_dd = True
            dd_start = dt

    return periods


class PortfolioTracker:
    """Stateful wrapper for convenience."""

    def record(self, positions: Dict[str, dict], cash: float, nav_date: Optional[date] = None) -> dict:
        return record_nav(nav_date or date.today(), positions, cash)

    def summary(self, benchmark: bool = True) -> dict:
        return summary(benchmark)

    def equity_curve(self) -> pd.DataFrame:
        return equity_curve()

    def drawdown_periods(self, threshold: float = -0.05) -> List[dict]:
        return drawdown_periods(threshold)
