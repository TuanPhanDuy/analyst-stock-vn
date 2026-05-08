"""Unit tests for multi-day signal analysis."""
import numpy as np
import pandas as pd
import pytest

from src.signals.multiday import (
    MultiDayAnalysis,
    analyze_ticker,
    build_multiday_scores,
    rank_by_conviction,
)

CFG = {
    "thresholds": {
        "daily":   {"rsi_oversold": 30, "rsi_overbought": 70, "volume_spike_ratio": 1.5},
        "monthly": {"ma_short": 20, "ma_long": 60},
        "yearly":  {"min_roe": 0.12},
    },
    "multiday": {
        "lookback_offsets": [0, 1, 3, 5],
        "consistency_bonus": 0.30,
        "min_streak": 2,
    },
}


def make_df(trend: float = 0, n: int = 150, seed: int = 0) -> pd.DataFrame:
    np.random.seed(seed)
    prices = 10000 + np.cumsum(np.random.randn(n) * 50 + trend)
    prices = prices.clip(min=100)
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "open":   prices * 0.99,
            "high":   prices * 1.01,
            "low":    prices * 0.98,
            "close":  prices,           # numpy array — aligns with idx automatically
            "volume": np.random.randint(500_000, 2_000_000, n).astype(float),
        },
        index=idx,
    )


def test_analyze_ticker_returns_analysis():
    df = make_df()
    result = analyze_ticker("TEST", df, CFG)
    assert result is not None
    assert isinstance(result, MultiDayAnalysis)
    assert result.ticker == "TEST"
    assert 0 < result.current_price


def test_analyze_ticker_snapshots_count():
    df = make_df(n=150)
    result = analyze_ticker("TEST", df, CFG, lookback_offsets=[0, 1, 3, 5])
    assert result is not None
    # Should produce up to 4 snapshots (one per offset)
    assert len(result.snapshots) >= 1
    assert len(result.snapshots) <= 4


def test_consistency_score_in_range():
    df = make_df()
    result = analyze_ticker("TEST", df, CFG)
    assert result is not None
    assert 0.0 <= result.consistency_score <= 1.0


def test_streak_days_non_negative():
    df = make_df()
    result = analyze_ticker("TEST", df, CFG)
    assert result is not None
    assert result.streak_days >= 0


def test_conviction_label_valid():
    df = make_df()
    result = analyze_ticker("TEST", df, CFG)
    assert result is not None
    assert result.conviction_label in ("HIGH", "MEDIUM", "LOW")


def test_insufficient_data_returns_none():
    df = make_df(n=30)   # too short for indicators
    result = analyze_ticker("TEST", df, CFG)
    assert result is None


def test_build_multiday_scores_returns_list():
    ohlcv_map = {
        "AAAA": make_df(trend=5, seed=1),
        "BBBB": make_df(trend=-5, seed=2),
    }
    analyses = build_multiday_scores(ohlcv_map, CFG, max_workers=2)
    assert isinstance(analyses, list)
    assert len(analyses) <= 2


def test_rank_by_conviction_structure():
    ohlcv_map = {
        "AAAA": make_df(trend=10, seed=1),
        "BBBB": make_df(trend=-10, seed=2),
        "CCCC": make_df(trend=0, seed=3),
    }
    analyses = build_multiday_scores(ohlcv_map, CFG, max_workers=2)
    ranked = rank_by_conviction(analyses, top_n=5, min_streak=1)
    assert "buy" in ranked
    assert "sell" in ranked
    assert isinstance(ranked["buy"], list)
    assert isinstance(ranked["sell"], list)


def test_rank_buy_items_have_conviction_fields():
    ohlcv_map = {"AAAA": make_df(trend=15, seed=1)}
    analyses = build_multiday_scores(ohlcv_map, CFG, max_workers=1)
    ranked = rank_by_conviction(analyses, top_n=5, min_streak=1)
    for item in ranked.get("buy", []):
        assert "conviction_score" in item
        assert "conviction_label" in item
        assert "consistency_score" in item
        assert "streak_days" in item
        assert "snapshots" in item


def test_min_streak_filters_short_signals():
    ohlcv_map = {"AAAA": make_df(trend=10, seed=1)}
    analyses = build_multiday_scores(ohlcv_map, CFG, max_workers=1)
    # With min_streak=99 no signal can have that many days
    ranked = rank_by_conviction(analyses, top_n=5, min_streak=99)
    assert ranked["buy"] == [] and ranked["sell"] == []


def test_to_dict_serializable():
    df = make_df()
    result = analyze_ticker("TEST", df, CFG)
    if result is None:
        pytest.skip("Insufficient data for this seed")
    d = result.to_dict()
    import json
    # Should not raise
    json.dumps(d)
