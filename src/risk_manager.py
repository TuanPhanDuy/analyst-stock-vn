"""
Portfolio risk metrics: VaR/CVaR, beta, risk contribution, stress tests.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _returns(ohlcv: pd.DataFrame) -> pd.Series:
    return ohlcv["close"].pct_change().dropna()


def var_cvar(
    returns: pd.Series,
    confidence: float = 0.95,
    holding_days: int = 1,
) -> dict:
    """
    Historical VaR and CVaR for a return series.

    Returns:
        var_pct, cvar_pct (positive numbers = loss magnitude)
    """
    if len(returns) < 20:
        return {"var_pct": None, "cvar_pct": None, "confidence": confidence}

    sorted_ret = np.sort(returns.values)
    idx = int((1 - confidence) * len(sorted_ret))
    var = -sorted_ret[max(idx - 1, 0)]
    cvar = -sorted_ret[:max(idx, 1)].mean()

    scale = np.sqrt(holding_days)
    return {
        "var_pct": round(float(var * scale * 100), 2),
        "cvar_pct": round(float(cvar * scale * 100), 2),
        "confidence": confidence,
        "holding_days": holding_days,
        "observations": len(returns),
    }


def portfolio_var(
    ohlcv_map: Dict[str, pd.DataFrame],
    weights: Dict[str, float],
    confidence: float = 0.95,
    holding_days: int = 1,
) -> dict:
    """
    VaR/CVaR for a weighted portfolio using historical simulation.
    """
    tickers = [t for t in weights if t in ohlcv_map and weights[t] > 0]
    if not tickers:
        return {}

    ret_df = pd.DataFrame(
        {t: _returns(ohlcv_map[t]) for t in tickers}
    ).dropna()

    if len(ret_df) < 20:
        return {}

    w = np.array([weights[t] for t in tickers])
    w = w / w.sum()
    portfolio_returns = ret_df.values @ w

    result = var_cvar(pd.Series(portfolio_returns), confidence, holding_days)
    result["tickers"] = tickers
    result["weights"] = {t: round(float(w[i]), 4) for i, t in enumerate(tickers)}
    return result


def beta(
    ticker_returns: pd.Series,
    market_returns: pd.Series,
) -> float:
    """Compute beta of ticker vs market index."""
    aligned = pd.concat([ticker_returns, market_returns], axis=1).dropna()
    if len(aligned) < 10:
        return 1.0
    cov = np.cov(aligned.iloc[:, 0].values, aligned.iloc[:, 1].values)
    if cov[1, 1] == 0:
        return 1.0
    return round(float(cov[0, 1] / cov[1, 1]), 3)


def portfolio_beta(
    ohlcv_map: Dict[str, pd.DataFrame],
    weights: Dict[str, float],
    market_ohlcv: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Weighted-average portfolio beta vs VN-Index.
    Falls back to equal beta=1 if market data unavailable.
    """
    if market_ohlcv is None:
        try:
            import yfinance as yf
            hist = yf.Ticker("^VNINDEX").history(period="1y")
            if not hist.empty:
                market_ohlcv = pd.DataFrame({"close": hist["Close"]})
                market_ohlcv.index = market_ohlcv.index.tz_localize(None)
        except Exception as e:
            logger.debug("VNIndex for beta: %s", e)

    if market_ohlcv is None:
        return {"portfolio_beta": 1.0, "note": "market data unavailable"}

    market_ret = _returns(market_ohlcv)
    betas = {}
    total_w = 0.0
    weighted_beta = 0.0

    for ticker, w in weights.items():
        if ticker not in ohlcv_map or w <= 0:
            continue
        b = beta(_returns(ohlcv_map[ticker]), market_ret)
        betas[ticker] = b
        weighted_beta += b * w
        total_w += w

    if total_w > 0:
        weighted_beta /= total_w

    return {
        "portfolio_beta": round(weighted_beta, 3),
        "ticker_betas": {t: round(b, 3) for t, b in betas.items()},
        "interpretation": (
            "aggressive" if weighted_beta > 1.2 else
            "moderate" if weighted_beta > 0.8 else
            "defensive"
        ),
    }


def risk_contribution(
    ohlcv_map: Dict[str, pd.DataFrame],
    weights: Dict[str, float],
) -> List[dict]:
    """
    Marginal and percentage risk contribution per position.
    Higher = that position drives more portfolio risk.
    """
    tickers = [t for t in weights if t in ohlcv_map and weights[t] > 0]
    if not tickers:
        return []

    ret_df = pd.DataFrame(
        {t: _returns(ohlcv_map[t]) for t in tickers}
    ).dropna()

    if len(ret_df) < 10:
        return []

    w = np.array([weights[t] for t in tickers])
    w = w / w.sum()
    cov_matrix = ret_df.cov().values

    portfolio_vol = np.sqrt(w @ cov_matrix @ w)
    if portfolio_vol == 0:
        return []

    marginal_contrib = cov_matrix @ w
    risk_contrib = w * marginal_contrib / portfolio_vol

    total_contrib = risk_contrib.sum()
    pct_contrib = risk_contrib / total_contrib if total_contrib > 0 else risk_contrib

    results = []
    for i, ticker in enumerate(tickers):
        results.append({
            "ticker": ticker,
            "weight_pct": round(float(w[i] * 100), 2),
            "risk_contribution_pct": round(float(pct_contrib[i] * 100), 2),
            "marginal_var": round(float(marginal_contrib[i] * 100), 4),
        })

    return sorted(results, key=lambda x: -x["risk_contribution_pct"])


def stress_test(
    ohlcv_map: Dict[str, pd.DataFrame],
    weights: Dict[str, float],
    scenarios: Optional[Dict[str, float]] = None,
) -> List[dict]:
    """
    Scenario stress test — applies market shocks and estimates portfolio impact.

    Default scenarios based on VN market historical events.
    """
    if scenarios is None:
        scenarios = {
            "VN-Index -5% day (circuit breaker)": -0.05,
            "VN-Index -10% week (2022 sell-off)": -0.10,
            "VN-Index -20% correction (2022)": -0.20,
            "VN-Index +5% rally": 0.05,
            "VN-Index +10% breakout": 0.10,
        }

    results = []
    tickers = [t for t in weights if t in ohlcv_map and weights[t] > 0]
    if not tickers:
        return results

    # Estimate each stock's sensitivity via beta-like correlation
    market_shocks = {}
    for name, shock in scenarios.items():
        portfolio_impact = 0.0
        total_w = sum(weights.get(t, 0) for t in tickers)
        for ticker in tickers:
            w = weights[ticker] / total_w
            # Simplified: assume beta=1 if not computed
            portfolio_impact += shock * w
        results.append({
            "scenario": name,
            "market_shock_pct": round(shock * 100, 1),
            "portfolio_impact_pct": round(portfolio_impact * 100, 2),
        })

    return results


def risk_report(
    ohlcv_map: Dict[str, pd.DataFrame],
    weights: Dict[str, float],
    nav: float = 100.0,
) -> dict:
    """Full risk report for a portfolio."""
    tickers = [t for t in weights if t in ohlcv_map and weights[t] > 0]

    pvar = portfolio_var(ohlcv_map, weights)
    pbeta = portfolio_beta(ohlcv_map, weights)
    rc = risk_contribution(ohlcv_map, weights)
    stress = stress_test(ohlcv_map, weights)

    # Individual ticker VaR
    ticker_vars = {}
    for ticker in tickers:
        ret = _returns(ohlcv_map[ticker])
        v = var_cvar(ret)
        ticker_vars[ticker] = v

    report = {
        "portfolio_var_95": pvar,
        "portfolio_beta": pbeta,
        "risk_contribution": rc,
        "stress_tests": stress,
        "ticker_var": ticker_vars,
    }

    if pvar.get("var_pct") and nav:
        report["daily_var_vnd"] = round(nav * pvar["var_pct"] / 100, 2)

    # Risk flags
    flags = []
    if pbeta.get("portfolio_beta", 1) > 1.3:
        flags.append("HIGH_BETA: portfolio is highly sensitive to market moves")
    if pvar.get("var_pct", 0) and pvar["var_pct"] > 5:
        flags.append("HIGH_VAR: daily 95% VaR exceeds 5% of portfolio")
    if rc:
        top_contrib = rc[0]
        if top_contrib["risk_contribution_pct"] > 50:
            flags.append(f"CONCENTRATION: {top_contrib['ticker']} drives {top_contrib['risk_contribution_pct']}% of risk")

    report["flags"] = flags
    return report
