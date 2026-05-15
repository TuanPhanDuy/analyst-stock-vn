"""
Backtest performance metrics.

Vietnamese market transaction costs (HOSE):
  - Brokerage commission: 0.15% per side (buy + sell)
  - Sell-side securities tax: 0.1% (fixed by law)
  - Market impact / slippage estimate: 0.1% per side
  Total round-trip cost: ~0.70% (buy 0.25% + sell 0.35%)
"""
import numpy as np
import pandas as pd

# HOSE cost constants
COMMISSION_PCT = 0.0015   # 0.15% per side
SECURITIES_TAX_PCT = 0.001  # 0.1% on sell side only (Thuế TNCN)
SLIPPAGE_PCT = 0.001        # 0.1% per side (market impact estimate)

ROUND_TRIP_COST_PCT = (
    (COMMISSION_PCT + SLIPPAGE_PCT)           # buy side
    + (COMMISSION_PCT + SECURITIES_TAX_PCT + SLIPPAGE_PCT)  # sell side
) * 100  # expressed as percentage


def apply_transaction_costs(
    pnl_pct: float,
    commission_pct: float = COMMISSION_PCT,
    slippage_pct: float = SLIPPAGE_PCT,
) -> float:
    """
    Deduct round-trip transaction costs from a trade's gross P&L.

    For VN market:
      - Buy cost: commission + slippage
      - Sell cost: commission + 0.1% securities tax + slippage
    """
    buy_cost = (commission_pct + slippage_pct) * 100
    sell_cost = (commission_pct + SECURITIES_TAX_PCT + slippage_pct) * 100
    return pnl_pct - buy_cost - sell_cost


def apply_costs_series(pnl_series: pd.Series) -> pd.Series:
    """Apply transaction costs to a Series of trade P&L percentages."""
    return pnl_series.apply(apply_transaction_costs)


def sharpe_ratio(returns: pd.Series, risk_free: float = 0.045) -> float:
    """Annualized Sharpe ratio. risk_free is annual (e.g. 0.045 = 4.5%)."""
    if returns.empty or returns.std() == 0:
        return 0.0
    daily_rf = risk_free / 252
    excess = returns - daily_rf
    return float((excess.mean() / excess.std()) * np.sqrt(252))


def sortino_ratio(returns: pd.Series, risk_free: float = 0.045) -> float:
    """Sortino ratio — penalizes only downside volatility."""
    if returns.empty:
        return 0.0
    daily_rf = risk_free / 252
    excess = returns - daily_rf
    downside = excess[excess < 0]
    if downside.empty or downside.std() == 0:
        return float("inf")
    return float((excess.mean() / downside.std()) * np.sqrt(252))


def max_drawdown(equity_curve: pd.Series) -> float:
    """Maximum peak-to-trough drawdown as a positive fraction (e.g. 0.15 = 15%)."""
    if equity_curve.empty:
        return 0.0
    peak = equity_curve.cummax()
    drawdown = (equity_curve - peak) / peak
    return float(abs(drawdown.min()))


def calmar_ratio(equity_curve: pd.Series, annualized_return: float) -> float:
    """Calmar = annualized return / max drawdown. Higher is better."""
    mdd = max_drawdown(equity_curve)
    return float(annualized_return / mdd) if mdd > 0 else float("inf")


def win_rate(df: pd.DataFrame) -> float:
    """Fraction of closed trades that were profitable (after costs)."""
    closed = df[df["outcome_result"].isin(["HIT_TARGET", "HIT_STOP", "EXPIRED"])]
    if closed.empty:
        return 0.0
    net_pnl = apply_costs_series(closed["outcome_pnl_pct"])
    return float((net_pnl > 0).mean())


def profit_factor(df: pd.DataFrame) -> float:
    """Gross profit divided by gross loss (after costs). >1 = profitable."""
    closed = df[df["outcome_result"].isin(["HIT_TARGET", "HIT_STOP", "EXPIRED"])]
    if closed.empty:
        return 0.0
    net_pnl = apply_costs_series(closed["outcome_pnl_pct"])
    gross_profit = net_pnl[net_pnl > 0].sum()
    gross_loss = abs(net_pnl[net_pnl < 0].sum())
    return float(gross_profit / gross_loss) if gross_loss > 0 else float("inf")


def expectancy(df: pd.DataFrame) -> float:
    """Average expected P&L per trade after costs (in %)."""
    closed = df[df["outcome_result"].isin(["HIT_TARGET", "HIT_STOP", "EXPIRED"])]
    if closed.empty:
        return 0.0
    net_pnl = apply_costs_series(closed["outcome_pnl_pct"])
    return float(net_pnl.mean())


def summary_report(df: pd.DataFrame) -> dict:
    """All key metrics in one dict, ready to print. Costs applied per VN market rules."""
    if df.empty:
        return {"error": "No records"}

    closed = df[df["outcome_result"].isin(["HIT_TARGET", "HIT_STOP", "EXPIRED"])]

    # Gross P&L
    gross_pnl_pct = closed["outcome_pnl_pct"]
    # Net P&L after VN transaction costs
    net_pnl_pct = apply_costs_series(gross_pnl_pct)

    net_frac = net_pnl_pct / 100  # convert % to fraction
    equity = (1 + net_frac).cumprod() if not net_frac.empty else pd.Series([1.0])

    # Annualized return estimate
    n_trades = len(closed)
    avg_hold_days = float(closed.get("hold_days", pd.Series([5] * n_trades)).mean()) if "hold_days" in closed.columns else 5
    trades_per_year = 252 / max(avg_hold_days, 1)
    ann_return = float((1 + net_frac.mean()) ** trades_per_year - 1) if not net_frac.empty else 0.0

    return {
        "total_signals": len(df),
        "total_closed": n_trades,
        "round_trip_cost_pct": round(ROUND_TRIP_COST_PCT, 3),
        "win_rate_pct": round(win_rate(df) * 100, 1),
        "avg_gross_pnl_pct": round(float(gross_pnl_pct.mean()), 2) if not gross_pnl_pct.empty else 0.0,
        "avg_net_pnl_pct": round(float(net_pnl_pct.mean()), 2) if not net_pnl_pct.empty else 0.0,
        "expectancy_pct": round(expectancy(df), 2),
        "profit_factor": round(profit_factor(df), 2),
        "sharpe_ratio": round(sharpe_ratio(net_frac), 2),
        "sortino_ratio": round(sortino_ratio(net_frac), 2),
        "max_drawdown_pct": round(max_drawdown(equity) * 100, 1),
        "calmar_ratio": round(calmar_ratio(equity, ann_return), 2),
        "annualized_return_pct": round(ann_return * 100, 1),
        "by_action": {
            action: {
                "count": len(closed[closed["action"] == action.upper()]),
                "win_rate_pct": round(
                    float(
                        (apply_costs_series(
                            closed.loc[closed["action"] == action.upper(), "outcome_pnl_pct"]
                        ) > 0).mean() * 100
                    ) if len(closed[closed["action"] == action.upper()]) > 0 else 0.0,
                    1,
                ),
                "avg_net_pnl_pct": round(
                    float(apply_costs_series(
                        closed.loc[closed["action"] == action.upper(), "outcome_pnl_pct"]
                    ).mean())
                    if len(closed[closed["action"] == action.upper()]) > 0 else 0.0,
                    2,
                ),
            }
            for action in ("buy", "sell")
        },
    }
