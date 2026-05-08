"""Unit tests for signal modules using synthetic OHLCV data."""
import numpy as np
import pandas as pd
import pytest

from src.signals import daily, monthly, yearly
from src.signals.base import Signal

DAILY_CFG = {"rsi_oversold": 30, "rsi_overbought": 70, "volume_spike_ratio": 1.5}
MONTHLY_CFG = {"ma_short": 20, "ma_long": 60}
YEARLY_CFG = {"min_roe": 0.12}


def make_df(trend: float = 0, n: int = 120) -> pd.DataFrame:
    """Synthetic OHLCV with controllable trend."""
    np.random.seed(0)
    prices = 10000 + np.cumsum(np.random.randn(n) * 50 + trend)
    prices = prices.clip(min=100)
    return pd.DataFrame({
        "open": prices * 0.99,
        "high": prices * 1.01,
        "low": prices * 0.98,
        "close": pd.Series(prices),
        "volume": np.random.randint(500_000, 2_000_000, n),
    })


def test_daily_returns_signal():
    df = make_df()
    sig = daily.analyze("TEST", df, DAILY_CFG)
    assert isinstance(sig, Signal)
    assert sig.timeframe == "daily"
    assert sig.action in ("BUY", "SELL", "HOLD")
    assert 0 <= sig.confidence <= 1


def test_monthly_returns_signal():
    df = make_df()
    sig = monthly.analyze("TEST", df, MONTHLY_CFG)
    assert isinstance(sig, Signal)
    assert sig.timeframe == "monthly"


def test_yearly_cheap_stock_buys():
    sig = yearly.analyze("TEST", 10000, {"pe": 8, "pb": 0.9, "roe": 0.18}, YEARLY_CFG)
    assert sig.action == "BUY"
    assert sig.confidence >= 0.60


def test_yearly_expensive_stock_sells():
    sig = yearly.analyze("TEST", 50000, {"pe": 35, "pb": 6.0, "roe": 0.05}, YEARLY_CFG)
    assert sig.action == "SELL"


def test_signal_is_actionable():
    sig = Signal("X", "daily", "BUY", 0.75, "test", 10000)
    assert sig.is_actionable(min_confidence=0.60)
    assert not sig.is_actionable(min_confidence=0.80)


def test_signal_hold_never_actionable():
    sig = Signal("X", "daily", "HOLD", 0.90, "hold", 10000)
    assert not sig.is_actionable()
