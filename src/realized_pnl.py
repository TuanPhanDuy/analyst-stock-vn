"""
Realized-P&L ledger for closed lots.

The portfolio (data/portfolio.json) only tracks *open* positions, and
portfolio_tracker only computes *unrealized* (mark-to-market) P&L. When a lot is
sold it simply disappears from portfolio.json — the realized gain/loss is lost.

This module records each closed lot to data/realized_pnl.jsonl so realized
performance (total booked P&L, win rate on completed trades) can be reported.
One line per closed lot; append-only.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import List, Optional

LEDGER_PATH = Path(__file__).parent.parent / "data" / "realized_pnl.jsonl"
LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)


def record_sale(
    ticker: str,
    sell_price: float,
    qty: int,
    buy_price: float,
    buy_date: str = "",
    sell_date: Optional[str] = None,
    notes: str = "",
    ledger: Path = LEDGER_PATH,
) -> dict:
    """
    Append one closed-lot record and return it.

    realized_pnl_vnd = (sell_price − buy_price) × qty
    """
    sell_date = sell_date or date.today().isoformat()
    realized_vnd = round((sell_price - buy_price) * qty)
    pnl_pct = round((sell_price / buy_price - 1) * 100, 2) if buy_price else 0.0
    rec = {
        "sell_date": sell_date,
        "ticker": ticker.upper(),
        "qty": int(qty),
        "buy_price": round(buy_price),
        "sell_price": round(sell_price),
        "realized_pnl_vnd": realized_vnd,
        "pnl_pct": pnl_pct,
        "buy_date": buy_date,
        "notes": notes,
    }
    with open(ledger, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load_history(ledger: Path = LEDGER_PATH) -> List[dict]:
    if not ledger.exists() or ledger.stat().st_size == 0:
        return []
    out = []
    for line in ledger.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def summary(ledger: Path = LEDGER_PATH) -> dict:
    """
    Aggregate realized performance across all closed lots.

    Returns:
        {
          total_realized_vnd, closed_lots, win_rate_pct, avg_pnl_pct,
          best, worst, by_ticker: {ticker: {realized_vnd, lots}}
        }
        or {"message": ...} if the ledger is empty.
    """
    recs = load_history(ledger)
    if not recs:
        return {"message": "No realized trades yet (data/realized_pnl.jsonl is empty)."}

    total = sum(r["realized_pnl_vnd"] for r in recs)
    wins = [r for r in recs if r["realized_pnl_vnd"] > 0]
    win_rate = round(len(wins) / len(recs) * 100, 1)
    avg_pnl = round(sum(r["pnl_pct"] for r in recs) / len(recs), 2)

    by_ticker: dict = {}
    for r in recs:
        t = r["ticker"]
        agg = by_ticker.setdefault(t, {"realized_vnd": 0, "lots": 0})
        agg["realized_vnd"] += r["realized_pnl_vnd"]
        agg["lots"] += 1

    best = max(recs, key=lambda r: r["pnl_pct"])
    worst = min(recs, key=lambda r: r["pnl_pct"])

    return {
        "total_realized_vnd": total,
        "closed_lots": len(recs),
        "win_rate_pct": win_rate,
        "avg_pnl_pct": avg_pnl,
        "best": {"ticker": best["ticker"], "pnl_pct": best["pnl_pct"]},
        "worst": {"ticker": worst["ticker"], "pnl_pct": worst["pnl_pct"]},
        "by_ticker": by_ticker,
    }
