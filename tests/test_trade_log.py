"""Unit tests for trade log persistence and outcome tracking."""
import json
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from src.trade_log.logger import append_signals, load_history, update_outcome, win_rate


def _tmp_log() -> Path:
    """Return a fresh temp file path for an isolated test log."""
    f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    f.close()
    p = Path(f.name)
    p.unlink()   # start empty
    return p


RANKED = {
    "buy": [
        {
            "ticker": "VCB",
            "price": 85000.0,
            "composite": 0.42,
            "confidence": 0.72,
            "reason": "RSI oversold",
            "conviction_label": "HIGH",
            "streak_days": 3,
            "snapshots": [],
        }
    ],
    "sell": [],
}

ENTRY_LEVELS = {
    "VCB": {"entry": 85000.0, "stop_loss": 82000.0, "target": 91000.0}
}


def test_append_signals_creates_file():
    log = _tmp_log()
    n = append_signals(RANKED, ENTRY_LEVELS, "2025-05-01", log_path=log)
    assert n == 1
    assert log.exists()


def test_append_signals_record_content():
    log = _tmp_log()
    append_signals(RANKED, ENTRY_LEVELS, "2025-05-01", log_path=log)
    lines = log.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["ticker"] == "VCB"
    assert rec["action"] == "BUY"
    assert rec["outcome_result"] == "PENDING"


def test_append_signals_accumulates():
    log = _tmp_log()
    append_signals(RANKED, ENTRY_LEVELS, "2025-05-01", log_path=log)
    append_signals(RANKED, ENTRY_LEVELS, "2025-05-02", log_path=log)
    lines = [l for l in log.read_text().splitlines() if l.strip()]
    assert len(lines) == 2


def test_append_signals_empty_ranked():
    log = _tmp_log()
    n = append_signals({"buy": [], "sell": []}, {}, "2025-05-01", log_path=log)
    assert n == 0
    assert not log.exists()


def test_load_history_returns_dataframe():
    log = _tmp_log()
    append_signals(RANKED, ENTRY_LEVELS, "2025-05-01", log_path=log)
    df = load_history(log_path=log)
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert "ticker" in df.columns
    assert "outcome_result" in df.columns


def test_load_history_missing_file():
    df = load_history(log_path=Path("/nonexistent/signals.jsonl"))
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_update_outcome_changes_pending():
    log = _tmp_log()
    append_signals(RANKED, ENTRY_LEVELS, "2025-05-01", log_path=log)
    updated = update_outcome("VCB", "2025-05-01", 91000.0, "2025-05-06", log_path=log)
    assert updated
    df = load_history(log_path=log)
    assert df.iloc[0]["outcome_result"] != "PENDING"


def test_win_rate_no_records():
    log = _tmp_log()
    result = win_rate(log_path=log)
    assert result["total_closed"] == 0
    assert result["win_rate"] is None


def test_win_rate_after_resolution():
    log = _tmp_log()
    append_signals(RANKED, ENTRY_LEVELS, "2025-05-01", log_path=log)
    update_outcome("VCB", "2025-05-01", 91000.0, "2025-05-06", log_path=log)
    result = win_rate(log_path=log)
    assert result["total_closed"] == 1
    assert isinstance(result["win_rate"], float)
    assert 0.0 <= result["win_rate"] <= 100.0
