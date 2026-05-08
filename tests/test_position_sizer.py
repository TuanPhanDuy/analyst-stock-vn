"""Unit tests for risk-based position sizing."""
import pytest

from src.position_sizer import LOT, calculate, size_ranked


def test_basic_affordable():
    result = calculate(entry=50000, stop_loss=47500, capital=10_000_000)
    assert result["affordable"] is True
    assert result["qty"] >= LOT
    assert result["qty"] % LOT == 0


def test_lot_rounding():
    result = calculate(entry=50000, stop_loss=47500, capital=10_000_000)
    assert result["qty"] % LOT == 0


def test_max_position_cap():
    # Use large capital so 10% >> 1 lot, allowing the cap to be tested cleanly.
    # 10% of 500M capital = 50M; at 10,000 VND entry that's 5,000 shares = 50 lots.
    capital = 500_000_000
    entry = 10_000
    result = calculate(entry=entry, stop_loss=entry - 500, capital=capital,
                       max_position_pct=0.10)
    assert result["affordable"] is True
    assert result["total_cost"] <= capital * 0.10 + entry   # 1-lot rounding tolerance


def test_unaffordable_when_1_lot_exceeds_capital():
    result = calculate(entry=1_000_000, stop_loss=950_000, capital=50_000)
    assert result["affordable"] is False


def test_zero_capital_unaffordable():
    result = calculate(entry=50000, stop_loss=47500, capital=0)
    assert result["affordable"] is False


def test_invalid_inputs():
    assert calculate(entry=0, stop_loss=47500, capital=10_000_000)["affordable"] is False
    assert calculate(entry=50000, stop_loss=0, capital=10_000_000)["affordable"] is False


def test_size_ranked_splits_affordable():
    ranked = {
        "buy": [
            {"ticker": "AAAA", "price": 50000.0, "composite": 0.4, "confidence": 0.7,
             "reason": "", "conviction_label": "HIGH", "streak_days": 2, "snapshots": []},
        ],
        "sell": [],
    }
    entry_levels = {"AAAA": {"entry": 50000.0, "stop_loss": 47500.0, "target": 56000.0}}
    result = size_ranked(ranked, entry_levels, capital=10_000_000)
    assert "buy" in result
    assert "buy_unaffordable" in result


def test_size_ranked_unaffordable_stock():
    ranked = {
        "buy": [
            {"ticker": "AAAA", "price": 1_500_000.0, "composite": 0.5, "confidence": 0.8,
             "reason": "", "conviction_label": "HIGH", "streak_days": 2, "snapshots": []},
        ],
        "sell": [],
    }
    entry_levels = {"AAAA": {"entry": 1_500_000.0, "stop_loss": 1_400_000.0, "target": 1_700_000.0}}
    result = size_ranked(ranked, entry_levels, capital=100_000)
    assert len(result["buy_unaffordable"]) == 1
    assert len(result["buy"]) == 0


def test_size_ranked_portfolio_risk_cap():
    # Two cheap stocks but risk cap is very tight (0.1% total)
    ranked = {
        "buy": [
            {"ticker": "AAAA", "price": 10000.0, "composite": 0.5, "confidence": 0.7,
             "reason": "", "conviction_label": "HIGH", "streak_days": 2, "snapshots": []},
            {"ticker": "BBBB", "price": 10000.0, "composite": 0.4, "confidence": 0.6,
             "reason": "", "conviction_label": "MEDIUM", "streak_days": 2, "snapshots": []},
        ],
        "sell": [],
    }
    entry_levels = {
        "AAAA": {"entry": 10000.0, "stop_loss": 9500.0, "target": 11000.0},
        "BBBB": {"entry": 10000.0, "stop_loss": 9500.0, "target": 11000.0},
    }
    # With max_total_risk_pct=0.001 (0.1%), only one stock can fit
    result = size_ranked(ranked, entry_levels, capital=10_000_000,
                         risk_per_trade_pct=0.02, max_position_pct=0.15,
                         max_total_risk_pct=0.001)
    total = len(result["buy"]) + len(result["buy_unaffordable"])
    assert total == 2
    assert len(result["buy"]) <= 1   # at most 1 affordable given the tight cap


def test_sell_side_gets_sizing():
    ranked = {
        "buy": [],
        "sell": [
            {"ticker": "AAAA", "price": 50000.0, "composite": -0.4, "confidence": 0.7,
             "reason": "", "conviction_label": "HIGH", "streak_days": 2, "snapshots": []},
        ],
    }
    entry_levels = {"AAAA": {"entry": 50000.0, "stop_loss": 47500.0, "target": 44000.0}}
    result = size_ranked(ranked, entry_levels, capital=10_000_000)
    assert "sizing" in result["sell"][0]
