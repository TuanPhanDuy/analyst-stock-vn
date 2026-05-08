"""Backtest performance metrics."""
import numpy as np
import pandas as pd


def sharpe_ratio(returns: pd.Series, risk_free: float = 0.045) -> float:
    """Annualized Sharpe ratio. risk_free is annual (e.g. 0.045 = 4.5%)."""
    if returns.empty or returns.std() == 0:
        return 0.0
    daily_rf = risk_free / 252
    excess = returns - daily_rf
    return float((excess.mean() / excess.std()) * np.sqrt(252))


def max_drawdown(equity_curve: pd.Series) -> float:
    """Maximum peak-to-trough drawdown as a positive fraction (e.g. 0.15 = 15%)."""
    if equity_curve.empty:
        return 0.0
    peak = equity_curve.cummax()
    drawdown = (equity_curve - peak) / peak
    return float(abs(drawdown.min()))


def win_rate(df: pd.DataFrame) -> float:
    """Fraction of closed trades that were profitable."""
    closed = df[df["outcome_result"].isin(["HIT_TARGET", "HIT_STOP", "EXPIRED"])]
    if closed.empty:
        return 0.0
    return float((closed["outcome_pnl_pct"] > 0).mean())


def profit_factor(df: pd.DataFrame) -> float:
    """Gross profit divided by gross loss. >1 means the system is profitable."""
    closed = df[df["outcome_result"].isin(["HIT_TARGET", "HIT_STOP", "EXPIRED"])]
    if closed.empty:
        return 0.0
    gross_profit = closed.loc[closed["outcome_pnl_pct"] > 0, "outcome_pnl_pct"].sum()
    gross_loss = abs(closed.loc[closed["outcome_pnl_pct"] < 0, "outcome_pnl_pct"].sum())
    return float(gross_profit / gross_loss) if gross_loss > 0 else float("inf")


def summary_report(df: pd.DataFrame) -> dict:
    """All key metrics in one dict, ready to print."""
    if df.empty:
        return {"error": "No records"}

    closed = df[df["outcome_result"].isin(["HIT_TARGET", "HIT_STOP", "EXPIRED"])]
    pnl = closed["outcome_pnl_pct"] / 100  # convert % to fraction

    equity = (1 + pnl).cumprod() if not pnl.empty else pd.Series([1.0])

    return {
        "total_signals": len(df),
        "total_closed": len(closed),
        "win_rate_pct": round(win_rate(df) * 100, 1),
        "avg_pnl_pct": round(float(pnl.mean() * 100), 2) if not pnl.empty else 0.0,
        "profit_factor": round(profit_factor(df), 2),
        "sharpe_ratio": round(sharpe_ratio(pnl), 2),
        "max_drawdown_pct": round(max_drawdown(equity) * 100, 1),
        "by_action": {
            action: {
                "count": len(closed[closed["action"] == action.upper()]),
                "win_rate_pct": round(
                    win_rate(closed[closed["action"] == action.upper()]) * 100, 1
                ),
            }
            for action in ("buy", "sell")
        },
    }
