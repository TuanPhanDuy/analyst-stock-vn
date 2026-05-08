"""
Walk-forward backtester.
Replays the signal pipeline over a sliding window of historical OHLCV data.
Works entirely on cached data — no network calls.
"""
import logging
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)

from src.backtesting.metrics import summary_report
from src.indicators.technical import entry_exit_levels
from src.scoring.scorer import CompositeScore, rank
from src.signals import daily, monthly, yearly


def _run_signals(window_map: dict, cfg: dict) -> list[dict]:
    """Run all signal modules on a slice of OHLCV data. Returns list of signal dicts."""
    records = []
    thresholds = cfg.get("thresholds", {})
    for ticker, df in window_map.items():
        try:
            price = float(df["close"].iloc[-1])
            sigs = [
                daily.analyze(ticker, df, thresholds.get("daily", {})),
                monthly.analyze(ticker, df, thresholds.get("monthly", {})),
                yearly.analyze(ticker, price, {}, thresholds.get("yearly", {})),
            ]
            composite = CompositeScore(ticker=ticker, price=price, signals=sigs)
            levels = entry_exit_levels(df)
            ranked = rank([composite], top_n=1)
            for action in ("buy", "sell"):
                for item in ranked.get(action, []):
                    records.append({
                        "ticker": ticker,
                        "price": price,
                        "action": action.upper(),
                        "composite_score": composite.composite,
                        "confidence": item.get("confidence", 0.0),
                        "reason": item.get("reason", ""),
                        "entry": levels["entry"],
                        "stop_loss": levels["stop_loss"],
                        "target": levels["target"],
                    })
        except Exception as e:
            logger.warning("signal failed %s: %s", ticker, e)
    return records


def _evaluate_outcome(record: dict, future_df) -> dict:
    """Check whether entry/stop/target was hit in the forward window."""
    if future_df is None or future_df.empty:
        return {"outcome_result": "EXPIRED", "outcome_pnl_pct": 0.0}

    entry = record["entry"]
    target = record["target"]
    stop = record["stop_loss"]
    is_buy = record["action"] == "BUY"

    for _, row in future_df.iterrows():
        high = float(row["high"])
        low = float(row["low"])

        if is_buy:
            if target and high >= target:
                return {"outcome_result": "HIT_TARGET",
                        "outcome_pnl_pct": round((target - entry) / entry * 100, 2)}
            if stop and low <= stop:
                return {"outcome_result": "HIT_STOP",
                        "outcome_pnl_pct": round((stop - entry) / entry * 100, 2)}
        else:
            if stop and high >= stop:
                return {"outcome_result": "HIT_STOP",
                        "outcome_pnl_pct": round((entry - stop) / entry * 100, 2)}
            if target and low <= target:
                return {"outcome_result": "HIT_TARGET",
                        "outcome_pnl_pct": round((entry - target) / entry * 100, 2)}

    final_price = float(future_df["close"].iloc[-1])
    if is_buy:
        pnl = (final_price - entry) / entry * 100
    else:
        pnl = (entry - final_price) / entry * 100
    return {"outcome_result": "EXPIRED", "outcome_pnl_pct": round(pnl, 2)}


def run_backtest(
    ohlcv_map: dict,
    cfg: dict,
    window_days: int = 120,
    step_days: int = 20,
    hold_days: int = 5,
) -> pd.DataFrame:
    """
    Walk-forward backtest over historical OHLCV data.

    Args:
        ohlcv_map: {ticker: df} covering the full history period.
        cfg: config dict (same as loaded from config.yaml).
        window_days: number of days of history used for each signal evaluation.
        step_days: how far to advance the window each iteration.
        hold_days: number of days to hold before evaluating the outcome.

    Returns:
        DataFrame of signal records with outcomes filled in.
    """
    all_dates = sorted(set().union(*[set(df.index) for df in ohlcv_map.values()]))
    if len(all_dates) < window_days + hold_days:
        return pd.DataFrame()

    all_records = []
    for start_idx in range(window_days, len(all_dates) - hold_days, step_days):
        eval_date = all_dates[start_idx]

        window_map = {}
        for t, df in ohlcv_map.items():
            slice_df = df[df.index <= eval_date].tail(window_days)
            if len(slice_df) >= 60:
                window_map[t] = slice_df

        if not window_map:
            continue

        signals = _run_signals(window_map, cfg)

        future_map = {}
        for t, df in ohlcv_map.items():
            fwd = df[df.index > eval_date].head(hold_days)
            if not fwd.empty:
                future_map[t] = fwd

        for sig in signals:
            outcome = _evaluate_outcome(sig, future_map.get(sig["ticker"]))
            all_records.append({
                **sig,
                **outcome,
                "eval_date": eval_date.date().isoformat() if hasattr(eval_date, "date") else str(eval_date),
            })

    return pd.DataFrame(all_records)


def print_backtest_report(df: pd.DataFrame) -> None:
    """Print a formatted summary of backtest results."""
    report = summary_report(df)
    print("\n" + "=" * 50)
    print("  BACKTEST RESULTS")
    print("=" * 50)
    if "error" in report:
        print(f"  {report['error']}")
        return
    print(f"  Total signals:   {report['total_signals']}")
    print(f"  Closed trades:   {report['total_closed']}")
    print(f"  Win rate:        {report['win_rate_pct']}%")
    print(f"  Avg P&L:         {report['avg_pnl_pct']:+.2f}%")
    print(f"  Profit factor:   {report['profit_factor']:.2f}")
    print(f"  Sharpe ratio:    {report['sharpe_ratio']:.2f}")
    print(f"  Max drawdown:    {report['max_drawdown_pct']:.1f}%")
    print()
    for action, stats in report.get("by_action", {}).items():
        print(f"  {action.upper():4s}: {stats['count']} trades, "
              f"win rate {stats['win_rate_pct']}%")
    print("=" * 50)
