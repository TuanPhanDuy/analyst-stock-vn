"""
Open position monitor.

Loads positions from data/portfolio.json, checks each one against
current prices from the daily OHLCV map, and returns per-position
status (HOLD / WATCH / SELL_STOP / SELL_TARGET).

Portfolio JSON schema:
  {
    "positions": [
      {
        "ticker": "KDH",
        "buy_date": "2026-05-13",
        "buy_price": 23300,      # actual execution price in VND
        "qty": 100,              # shares held (0 = unknown)
        "stop_loss": 22480,      # exit if price closes at or below this
        "target": 24939,         # take-profit level
        "notes": ""
      }
    ]
  }

To add/remove a position: edit data/portfolio.json directly, or use
  add_position() / remove_position() helpers below.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

PORTFOLIO_PATH = Path(__file__).parent.parent / "data" / "portfolio.json"
WARN_STOP_PCT = 0.03   # within 3% of stop → WATCH


@dataclass
class Position:
    ticker: str
    buy_date: str
    buy_price: float
    stop_loss: float
    target: float
    qty: int = 0
    notes: str = ""


@dataclass
class PositionStatus:
    position: Position
    current_price: float
    pnl_pct: float          # vs buy_price
    pnl_vnd: float          # total VND P&L (0 if qty unknown)
    status: str             # HOLD | WATCH | SELL_STOP | SELL_TARGET
    message: str
    pct_from_stop: float    # positive = above stop
    pct_from_target: float  # positive = below target

    @property
    def needs_action(self) -> bool:
        return self.status in ("SELL_STOP", "SELL_TARGET")


# ── I/O ──────────────────────────────────────────────────────────────────────

def load_portfolio(path: Path = PORTFOLIO_PATH) -> List[Position]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    positions = []
    for p in data.get("positions", []):
        positions.append(Position(
            ticker=str(p.get("ticker", "")).upper(),
            buy_date=str(p.get("buy_date", "")),
            buy_price=float(p.get("buy_price") or 0),
            stop_loss=float(p.get("stop_loss") or 0),
            target=float(p.get("target") or 0),
            qty=int(p.get("qty") or 0),
            notes=str(p.get("notes", "")),
        ))
    return [p for p in positions if p.ticker]


def save_portfolio(positions: List[Position], path: Path = PORTFOLIO_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"positions": [
            {
                "ticker": p.ticker,
                "buy_date": p.buy_date,
                "buy_price": p.buy_price,
                "qty": p.qty,
                "stop_loss": p.stop_loss,
                "target": p.target,
                "notes": p.notes,
            }
            for p in positions
        ]},
        indent=2, ensure_ascii=False,
    ))


def add_position(ticker: str, buy_price: float, stop_loss: float, target: float,
                 buy_date: str = "", qty: int = 0, notes: str = "",
                 path: Path = PORTFOLIO_PATH) -> None:
    from datetime import date
    positions = load_portfolio(path)
    positions = [p for p in positions if p.ticker != ticker.upper()]   # replace if exists
    positions.append(Position(
        ticker=ticker.upper(),
        buy_date=buy_date or date.today().isoformat(),
        buy_price=buy_price,
        stop_loss=stop_loss,
        target=target,
        qty=qty,
        notes=notes,
    ))
    save_portfolio(positions, path)


def remove_position(ticker: str, path: Path = PORTFOLIO_PATH) -> bool:
    positions = load_portfolio(path)
    before = len(positions)
    positions = [p for p in positions if p.ticker != ticker.upper()]
    if len(positions) < before:
        save_portfolio(positions, path)
        return True
    return False


# ── Analysis ─────────────────────────────────────────────────────────────────

def analyze_positions(
    positions: List[Position],
    ohlcv_map: dict,
    entry_levels: dict,
) -> List[PositionStatus]:
    """
    Check each position against today's close. Returns PositionStatus list.
    Tickers not in ohlcv_map are skipped (data unavailable).
    """
    results = []
    for pos in positions:
        df = ohlcv_map.get(pos.ticker)
        if df is None or df.empty:
            continue

        current_price = float(df["close"].iloc[-1])

        # Use stored levels; recompute from entry_levels as fallback
        stop = pos.stop_loss
        target = pos.target
        if not stop or not target:
            el = entry_levels.get(pos.ticker, {})
            stop = stop or float(el.get("stop_loss", current_price * 0.95))
            target = target or float(el.get("target", current_price * 1.07))

        buy_price = pos.buy_price or current_price
        pnl_pct = (current_price - buy_price) / buy_price * 100
        pnl_vnd = (current_price - buy_price) * pos.qty if pos.qty else 0.0

        pct_from_stop = (current_price - stop) / current_price * 100
        pct_from_target = (target - current_price) / current_price * 100

        if current_price <= stop:
            status = "SELL_STOP"
            message = (f"Stop loss hit — price {current_price:,.0f} ≤ stop {stop:,.0f}. "
                       f"SELL immediately to limit further loss.")
        elif current_price >= target:
            status = "SELL_TARGET"
            message = (f"Target reached — price {current_price:,.0f} ≥ target {target:,.0f}. "
                       f"Consider taking profit.")
        elif pct_from_stop <= WARN_STOP_PCT * 100:
            status = "WATCH"
            message = (f"Only {pct_from_stop:.1f}% above stop {stop:,.0f}. "
                       f"Watch closely — be ready to sell.")
        else:
            status = "HOLD"
            message = (f"{pct_from_stop:.1f}% above stop · "
                       f"{pct_from_target:.1f}% from target {target:,.0f}")

        results.append(PositionStatus(
            position=pos,
            current_price=current_price,
            pnl_pct=round(pnl_pct, 2),
            pnl_vnd=round(pnl_vnd),
            status=status,
            message=message,
            pct_from_stop=round(pct_from_stop, 2),
            pct_from_target=round(pct_from_target, 2),
        ))

    return results
