"""Tests for ML calibration: walk-forward validation gate + safe application."""
import json

import pytest

import src.ml_signal as ml


def _write_log(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _rec(scan_date, composite, win, streak=3, consistency=0.8):
    """A closed signal whose outcome (win/loss) is driven by `win`."""
    return {
        "scan_date": scan_date,
        "ticker": "TST",
        "timeframe": "daily",
        "action": "BUY",
        "confidence": abs(composite),
        "price": 10000,
        "entry": 10000,
        "stop_loss": 9500,
        "target": 11000,
        "composite_score": composite,
        "streak_days": streak,
        "consistency_score": consistency,
        "conviction_score": composite,
        "outcome_pnl_pct": 5.0 if win else -5.0,
        "outcome_result": "HIT_TARGET" if win else "HIT_STOP",
    }


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    # Point the log at a temp file and reset caches for every test.
    monkeypatch.setattr(ml, "LOG_PATH", tmp_path / "signals.jsonl")
    ml.reload()
    yield
    ml.reload()


def test_validate_insufficient_data(tmp_path):
    _write_log(ml.LOG_PATH, [_rec(f"2026-01-{i:02d}", 0.5, True) for i in range(1, 6)])
    v = ml.validate(min_samples=20)
    assert v["reliable"] is False
    assert "insufficient" in v["reason"]


def test_validate_learnable_signal_is_reliable(tmp_path):
    # composite_score perfectly separates win/loss → model should show OOS skill.
    rows = []
    for i in range(1, 61):
        day = f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
        win = i % 2 == 0
        composite = 0.8 if win else -0.8
        rows.append(_rec(day, composite, win))
    _write_log(ml.LOG_PATH, rows)
    v = ml.validate(min_samples=20)
    assert v["n_samples"] == 60
    assert v["oos_auc"] is not None
    assert v["reliable"] is True
    assert v["oos_auc"] >= 0.55


def test_validate_noise_is_not_reliable(tmp_path):
    # Outcome unrelated to features → no OOS edge → must not be trusted.
    rows = []
    for i in range(1, 61):
        day = f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
        win = (i * 7) % 3 == 0            # pseudo-random, independent of composite
        composite = 0.5                  # constant feature carries no information
        rows.append(_rec(day, composite, win, streak=1, consistency=0.5))
    _write_log(ml.LOG_PATH, rows)
    v = ml.validate(min_samples=20)
    assert v["reliable"] is False


def test_calibrate_passes_through_when_not_validated(tmp_path):
    rows = [_rec(f"2026-01-{i:02d}", 0.5, i % 2 == 0) for i in range(1, 6)]
    _write_log(ml.LOG_PATH, rows)
    # Not enough data → no validated model → confidence unchanged.
    out = ml.calibrate_confidence(0.7, 0.5, "BUY", streak_days=3, consistency_score=0.8)
    assert out == 0.7


def test_calibrate_applies_when_validated(tmp_path):
    rows = []
    for i in range(1, 61):
        day = f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
        win = i % 2 == 0
        rows.append(_rec(day, 0.8 if win else -0.8, win))
    _write_log(ml.LOG_PATH, rows)
    assert ml.get_validation(min_samples=20)["reliable"] is True
    out = ml.calibrate_confidence(0.5, 0.8, "BUY", streak_days=3, consistency_score=0.8)
    assert 0.0 <= out <= 1.0
