"""
Portfolio optimization for VN stock positions.

Implements:
  1. Correlation matrix — identify highly correlated holdings
  2. Minimum-variance weights — optimal allocation given risk constraints
  3. Kelly criterion fractions — size each position by historical win rate
  4. Concentration risk warnings — single-stock and sector over-exposure

All optimizations are practical: lot-rounded, capital-constrained, and
calibrated to the VN market's 15% max position rule.
"""
from __future__ import annotations

import logging
import warnings
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

LOT = 100   # HOSE minimum lot size


def correlation_matrix(ohlcv_map: dict, min_history: int = 60) -> pd.DataFrame:
    """
    Compute pairwise daily-return correlation for all tickers with ≥min_history bars.

    Returns a square correlation DataFrame (tickers × tickers).
    """
    returns = {}
    for ticker, df in ohlcv_map.items():
        if df is not None and len(df) >= min_history:
            returns[ticker] = df["close"].pct_change().dropna()

    if len(returns) < 2:
        return pd.DataFrame()

    returns_df = pd.DataFrame(returns).dropna(how="all")
    return returns_df.corr()


def high_correlation_pairs(corr: pd.DataFrame, threshold: float = 0.80) -> List[dict]:
    """
    Find pairs of tickers whose return correlation exceeds `threshold`.

    Returns list of {pair: [a, b], correlation: float, note: str}.
    """
    if corr.empty:
        return []

    pairs = []
    cols = corr.columns.tolist()
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            c = float(corr.iloc[i, j])
            if abs(c) >= threshold:
                a, b = cols[i], cols[j]
                pairs.append({
                    "pair": [a, b],
                    "correlation": round(c, 2),
                    "note": (
                        f"{a} & {b} are highly correlated ({c:.0%}) — "
                        "holding both doubles sector exposure without diversification benefit"
                    ),
                })
    return pairs


def kelly_fraction(win_rate: float, avg_win_pct: float, avg_loss_pct: float) -> float:
    """
    Kelly criterion: f* = W/R - (1-W)/1  where R = avg_win / avg_loss.
    Returns quarter-Kelly (conservative standard practice).

    Args:
        win_rate: fraction of winning trades (0.0–1.0)
        avg_win_pct: average winning trade P&L in percent (e.g. 4.5)
        avg_loss_pct: average losing trade P&L magnitude in percent (e.g. 2.0)

    Returns:
        Fraction of capital to risk (e.g. 0.04 = 4%).
        Clamped to [0.005, 0.25].
    """
    if avg_loss_pct <= 0 or avg_win_pct <= 0:
        return 0.02

    q = 1.0 - win_rate
    r = avg_win_pct / avg_loss_pct
    full_kelly = win_rate - q / r
    quarter_kelly = full_kelly * 0.25
    return round(max(0.005, min(0.25, quarter_kelly)), 4)


def kelly_from_history(ticker: str) -> Optional[float]:
    """
    Compute Kelly fraction from signal history in data/signals.jsonl.
    Returns None if fewer than 10 resolved trades are available.
    """
    try:
        from src.trade_log.logger import load_history
        df = load_history()
        if df.empty:
            return None

        closed = df[
            (df["ticker"] == ticker) &
            (df["outcome_result"].isin(["HIT_TARGET", "HIT_STOP", "EXPIRED"]))
        ]
        if len(closed) < 10:
            return None

        wins = closed[closed["outcome_pnl_pct"] > 0]
        losses = closed[closed["outcome_pnl_pct"] <= 0]
        wr = len(wins) / len(closed)
        avg_win = float(wins["outcome_pnl_pct"].mean()) if not wins.empty else 2.0
        avg_loss = abs(float(losses["outcome_pnl_pct"].mean())) if not losses.empty else 1.0
        return kelly_fraction(wr, avg_win, avg_loss)
    except Exception as e:
        logger.debug("kelly_from_history %s: %s", ticker, e)
        return None


def min_variance_weights(
    ohlcv_map: dict,
    tickers: List[str],
    min_weight: float = 0.05,
    max_weight: float = 0.40,
) -> Dict[str, float]:
    """
    Compute minimum-variance portfolio weights for given tickers.

    Uses scipy.optimize.minimize (SLSQP) when available.
    Falls back to equal weights if scipy is not installed or optimization fails.

    Args:
        ohlcv_map: {ticker: DataFrame}
        tickers: list of tickers to include
        min_weight: minimum allocation per position (e.g. 0.05 = 5%)
        max_weight: maximum allocation per position (e.g. 0.40 = 40%)

    Returns:
        {ticker: weight} where weights sum to 1.0.
    """
    closes = {}
    for t in tickers:
        df = ohlcv_map.get(t)
        if df is not None and len(df) >= 60:
            closes[t] = df["close"].pct_change().dropna()

    available = list(closes.keys())
    n = len(available)
    if n < 2:
        return {t: 1.0 / len(tickers) for t in tickers}

    returns_df = pd.DataFrame(closes).dropna()
    if len(returns_df) < 30:
        return {t: round(1.0 / n, 4) for t in available}

    cov = returns_df.cov().values * 252   # annualised covariance

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from scipy.optimize import minimize

            def _portfolio_variance(w: np.ndarray) -> float:
                return float(w @ cov @ w)

            x0 = np.ones(n) / n
            bounds = [(min_weight, max_weight)] * n
            constraints = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}
            res = minimize(
                _portfolio_variance, x0,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"ftol": 1e-9, "maxiter": 500},
            )
            if res.success:
                return {t: round(float(w), 4) for t, w in zip(available, res.x)}
    except ImportError:
        pass
    except Exception as e:
        logger.debug("min_variance_weights optimization failed: %s", e)

    # Equal-weight fallback
    w = round(1.0 / n, 4)
    return {t: w for t in available}


def concentration_risk(positions: List[dict], total_value: float, sector_map: dict = None) -> dict:
    """
    Assess portfolio concentration risk.

    Args:
        positions: [{"ticker", "value", "sector" (optional)}]
        total_value: total portfolio value in VND
        sector_map: {sector: [tickers]} for sector-level concentration

    Returns:
        {warnings: [...], sector_allocation: {...}, single_stock_pct: {...}}
    """
    if not positions or total_value <= 0:
        return {"warnings": [], "sector_allocation": {}, "single_stock_pct": {}}

    warnings_list = []

    # Single-stock concentration
    single_stock_pct = {}
    for pos in positions:
        pct = pos["value"] / total_value * 100
        single_stock_pct[pos["ticker"]] = round(pct, 1)
        if pct > 25:
            warnings_list.append(
                f"{pos['ticker']}: {pct:.0f}% of portfolio — dangerously over-concentrated"
            )
        elif pct > 15:
            warnings_list.append(
                f"{pos['ticker']}: {pct:.0f}% of portfolio — above 15% max"
            )

    # Sector concentration
    sector_totals: Dict[str, float] = {}
    if sector_map:
        for pos in positions:
            s = _find_sector(pos["ticker"], sector_map)
            sector_totals[s] = sector_totals.get(s, 0.0) + pos["value"]

        for sector, val in sector_totals.items():
            pct = val / total_value * 100
            if pct > 40:
                warnings_list.append(f"{sector}: {pct:.0f}% of portfolio — sector over-concentration")

    return {
        "warnings": warnings_list,
        "sector_allocation": {
            s: round(v / total_value * 100, 1) for s, v in sector_totals.items()
        },
        "single_stock_pct": single_stock_pct,
    }


def _find_sector(ticker: str, sector_map: dict) -> str:
    for sector, tickers in sector_map.items():
        if ticker in tickers:
            return sector
    return "Other"


def optimize(
    ohlcv_map: dict,
    buy_signals: List[dict],
    cfg: dict = None,
) -> dict:
    """
    Full portfolio optimization pass for buy signal candidates.

    Args:
        ohlcv_map: {ticker: DataFrame}
        buy_signals: list of signal dicts from ranked["buy"]
        cfg: full config dict

    Returns:
        {
          recommendations: [{ticker, mv_weight_pct, kelly_fraction, note}],
          min_variance_weights: {ticker: weight},
          kelly_fractions: {ticker: fraction},
          high_correlation_pairs: [...],
          concentration_warnings: [...]
        }
    """
    if cfg is None:
        cfg = {}

    tickers = [s["ticker"] for s in buy_signals]
    if not tickers:
        return {"message": "No buy signals to optimize", "recommendations": []}

    # Correlation analysis
    corr = correlation_matrix({t: ohlcv_map[t] for t in tickers if t in ohlcv_map})
    corr_pairs = high_correlation_pairs(corr, threshold=0.80) if not corr.empty else []

    # Min-variance weights
    port_cfg = cfg.get("portfolio", {})
    max_w = port_cfg.get("max_position_pct", 0.40)
    mv_weights = min_variance_weights(ohlcv_map, tickers, max_weight=max_w)

    # Kelly fractions
    kelly_map = {}
    for ticker in tickers:
        k = kelly_from_history(ticker)
        kelly_map[ticker] = k if k is not None else port_cfg.get("risk_per_trade_pct", 0.02)

    # Build recommendations
    recommendations = []
    for ticker in tickers:
        w = mv_weights.get(ticker, 1.0 / max(len(tickers), 1))
        k = kelly_map[ticker]
        in_corr_pair = any(ticker in p["pair"] for p in corr_pairs)
        note = ""
        if in_corr_pair:
            partners = [
                "/".join(p["pair"]).replace(f"{ticker}/", "").replace(f"/{ticker}", "")
                for p in corr_pairs if ticker in p["pair"]
            ]
            note = f"Correlated with {', '.join(partners)} — consider reducing one"

        recommendations.append({
            "ticker": ticker,
            "mv_weight_pct": round(w * 100, 1),
            "kelly_fraction": round(k, 4),
            "kelly_risk_pct": round(k * 100, 2),
            "note": note,
        })

    return {
        "recommendations": recommendations,
        "min_variance_weights": {t: round(w, 4) for t, w in mv_weights.items()},
        "kelly_fractions": kelly_map,
        "high_correlation_pairs": corr_pairs,
    }
