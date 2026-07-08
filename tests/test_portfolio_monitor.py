"""Tests for portfolio position monitoring, incl. zero-level fallback handling."""
import pandas as pd

from src.portfolio_monitor import Position, analyze_positions


def _ohlcv(close: float) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=3, freq="D")
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1000},
        index=idx,
    )


def test_stored_levels_used_when_present():
    pos = Position(ticker="SHB", buy_date="2026-05-13", buy_price=14000,
                   stop_loss=13609, target=14782, qty=200)
    statuses = analyze_positions([pos], {"SHB": _ohlcv(14200)}, entry_levels={})
    s = statuses[0]
    assert s.effective_stop == 13609
    assert s.effective_target == 14782
    assert s.levels_are_fallback is False
    assert s.status == "HOLD"


def test_zero_levels_fall_back_to_entry_levels():
    pos = Position(ticker="HPG", buy_date="2026-05-20", buy_price=26175,
                   stop_loss=0, target=0, qty=300)
    entry_levels = {"HPG": {"stop_loss": 25390, "target": 28008}}
    statuses = analyze_positions([pos], {"HPG": _ohlcv(26500)}, entry_levels)
    s = statuses[0]
    # Effective levels come from entry_levels, and are flagged as fallback.
    assert s.effective_stop == 25390
    assert s.effective_target == 28008
    assert s.levels_are_fallback is True


def test_zero_levels_do_not_falsely_trigger_sell():
    # With stop_loss=0/target=0, the raw 0 must never be used in the comparison
    # (price >= 0 would falsely fire SELL_TARGET). Fallback levels prevent that.
    pos = Position(ticker="HPG", buy_date="2026-05-20", buy_price=26175,
                   stop_loss=0, target=0, qty=300)
    statuses = analyze_positions([pos], {"HPG": _ohlcv(26500)}, entry_levels={})
    s = statuses[0]
    assert s.status != "SELL_TARGET"  # would be the bug if raw target=0 were used
    assert s.effective_target > s.current_price
    assert s.effective_stop < s.current_price


def test_stop_hit_triggers_sell():
    pos = Position(ticker="HCM", buy_date="2026-05-18", buy_price=26500,
                   stop_loss=25700, target=28400, qty=100)
    statuses = analyze_positions([pos], {"HCM": _ohlcv(25600)}, entry_levels={})
    assert statuses[0].status == "SELL_STOP"
    assert statuses[0].needs_action is True
