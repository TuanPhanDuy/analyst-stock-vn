"""Tests for the realized-P&L ledger."""
from src.realized_pnl import load_history, record_sale, summary


def test_record_sale_computes_pnl(tmp_path):
    ledger = tmp_path / "realized.jsonl"
    rec = record_sale("HPG", sell_price=28000, qty=300, buy_price=26175,
                      buy_date="2026-05-20", sell_date="2026-07-08", ledger=ledger)
    assert rec["realized_pnl_vnd"] == (28000 - 26175) * 300
    assert rec["pnl_pct"] == round((28000 / 26175 - 1) * 100, 2)
    assert rec["ticker"] == "HPG"


def test_loss_is_negative(tmp_path):
    ledger = tmp_path / "realized.jsonl"
    rec = record_sale("SHB", sell_price=13000, qty=200, buy_price=14000, ledger=ledger)
    assert rec["realized_pnl_vnd"] == (13000 - 14000) * 200
    assert rec["realized_pnl_vnd"] < 0


def test_summary_empty(tmp_path):
    assert "message" in summary(ledger=tmp_path / "none.jsonl")


def test_summary_aggregates(tmp_path):
    ledger = tmp_path / "realized.jsonl"
    record_sale("HPG", 28000, 300, 26175, ledger=ledger)   # win
    record_sale("SHB", 13000, 200, 14000, ledger=ledger)   # loss
    record_sale("HPG", 27000, 100, 26175, ledger=ledger)   # win
    s = summary(ledger=ledger)
    assert s["closed_lots"] == 3
    assert s["win_rate_pct"] == round(2 / 3 * 100, 1)
    assert s["by_ticker"]["HPG"]["lots"] == 2
    assert s["best"]["ticker"] == "HPG"
    assert s["worst"]["ticker"] == "SHB"
    assert len(load_history(ledger)) == 3


def test_zero_buy_price_is_safe(tmp_path):
    ledger = tmp_path / "realized.jsonl"
    rec = record_sale("X", 10000, 100, 0, ledger=ledger)
    assert rec["pnl_pct"] == 0.0
