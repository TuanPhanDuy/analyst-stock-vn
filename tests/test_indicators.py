"""Unit tests for technical indicators — no network calls required."""
import numpy as np
import pandas as pd
import pytest

from src.indicators.technical import atr, bollinger_bands, macd, rsi, sma, volume_ratio


@pytest.fixture
def close():
    np.random.seed(42)
    prices = 10000 + np.cumsum(np.random.randn(100) * 100)
    return pd.Series(prices.clip(min=1))


@pytest.fixture
def ohlcv(close):
    n = len(close)
    return pd.DataFrame({
        "open": close * 0.99,
        "high": close * 1.01,
        "low": close * 0.98,
        "close": close,
        "volume": np.random.randint(1_000_000, 5_000_000, n),
    })


def test_rsi_bounds(close):
    r = rsi(close)
    valid = r.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_rsi_length(close):
    r = rsi(close, period=14)
    assert len(r) == len(close)


def test_macd_returns_three_series(close):
    m, s, h = macd(close)
    assert len(m) == len(s) == len(h) == len(close)


def test_bollinger_upper_gt_lower(close):
    upper, mid, lower = bollinger_bands(close)
    valid = ~upper.isna()
    assert (upper[valid] > lower[valid]).all()


def test_sma_length(close):
    assert len(sma(close, 20)) == len(close)


def test_volume_ratio_positive(ohlcv):
    vr = volume_ratio(ohlcv["volume"])
    assert (vr.dropna() > 0).all()


def test_atr_positive(ohlcv):
    a = atr(ohlcv["high"], ohlcv["low"], ohlcv["close"])
    assert (a.dropna() > 0).all()
