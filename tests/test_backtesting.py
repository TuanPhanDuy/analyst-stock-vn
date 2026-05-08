"""Unit tests for walk-forward backtester and performance metrics."""
import numpy as np
import pandas as pd
import pytest

from src.backtesting.metrics import (
    max_drawdown, profit_factor, sharpe_ratio, summary_report, win_rate
)
from src.backtesting.runner import run_backtest

CFG = {
    "thresholds": {
        "daily":   {"rsi_oversold": 30, "rsi_overbought": 70, "volume_spike_ratio": 1.5},
        "monthly": {"ma_short": 20, "ma_long": 60},
        "yearly":  {"min_roe": 0.12},
    },
}


def make_df(trend: float = 0, n: int = 300, seed: int = 0) -> pd.DataFrame:
    np.random.seed(seed)
    prices = 10000 + np.cumsum(np.random.randn(n) * 50 + trend)
    prices = prices.clip(min=100)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "open":   prices * 0.99,
            "high":   prices * 1.01,
            "low":    prices * 0.98,
            "close":  prices,
            "volume": np.random.randint(500_000, 2_000_000, n).astype(float),
        },
        index=idx,
    )


# ── Runner tests ──────────────────────────────────────────────────────────────

def test_run_backtest_returns_dataframe():
    ohlcv_map = {"AAAA": make_df(seed=1), "BBBB": make_df(seed=2)}
    df = run_backtest(ohlcv_map, CFG, window_days=120, step_days=40, hold_days=5)
    assert isinstance(df, pd.DataFrame)


def test_run_backtest_empty_on_short_data():
    ohlcv_map = {"AAAA": make_df(n=50, seed=1)}
    df = run_backtest(ohlcv_map, CFG, window_days=120, hold_days=5)
    assert df.empty


def test_run_backtest_has_expected_columns():
    ohlcv_map = {"AAAA": make_df(seed=1)}
    df = run_backtest(ohlcv_map, CFG, window_days=120, step_days=40, hold_days=5)
    if df.empty:
        pytest.skip("No signals generated for this seed")
    for col in ("ticker", "action", "outcome_result", "outcome_pnl_pct", "eval_date"):
        assert col in df.columns, f"Missing column: {col}"


def test_run_backtest_outcomes_are_valid():
    ohlcv_map = {"AAAA": make_df(seed=3), "BBBB": make_df(seed=4)}
    df = run_backtest(ohlcv_map, CFG, window_days=120, step_days=40, hold_days=5)
    if df.empty:
        pytest.skip("No signals generated")
    valid = {"HIT_TARGET", "HIT_STOP", "EXPIRED"}
    assert set(df["outcome_result"].unique()).issubset(valid)


# ── Metrics tests ─────────────────────────────────────────────────────────────

def _make_results(pnls: list, result: str = "HIT_TARGET") -> pd.DataFrame:
    return pd.DataFrame([
        {"outcome_pnl_pct": p, "outcome_result": result if p >= 0 else "HIT_STOP", "action": "BUY"}
        for p in pnls
    ])


def test_win_rate_all_wins():
    df = _make_results([5.0, 3.0, 2.0])
    assert win_rate(df) == 1.0


def test_win_rate_all_losses():
    df = _make_results([-1.0, -2.0, -3.0])
    assert win_rate(df) == 0.0


def test_win_rate_mixed():
    df = _make_results([5.0, -2.0, 3.0, -1.0])
    assert win_rate(df) == pytest.approx(0.5)


def test_profit_factor_all_wins():
    df = _make_results([5.0, 3.0])
    pf = profit_factor(df)
    assert pf == float("inf")


def test_profit_factor_mixed():
    df = _make_results([6.0, -2.0])
    pf = profit_factor(df)
    assert pf == pytest.approx(3.0)


def test_sharpe_ratio_zero_volatility():
    returns = pd.Series([0.01] * 10)
    sr = sharpe_ratio(returns)
    assert isinstance(sr, float)


def test_max_drawdown_no_loss():
    equity = pd.Series([1.0, 1.1, 1.2, 1.3])
    assert max_drawdown(equity) == pytest.approx(0.0)


def test_max_drawdown_with_loss():
    equity = pd.Series([1.0, 1.2, 0.9, 1.1])
    dd = max_drawdown(equity)
    assert dd > 0.0
    assert dd < 1.0


def test_summary_report_structure():
    df = _make_results([5.0, -1.0, 3.0, -2.0, 4.0])
    report = summary_report(df)
    for key in ("total_signals", "win_rate_pct", "profit_factor", "sharpe_ratio", "max_drawdown_pct"):
        assert key in report


def test_summary_report_empty():
    report = summary_report(pd.DataFrame())
    assert "error" in report
