"""
Signal persistence and outcome tracking.
Appends actionable signals to data/signals.jsonl (JSON-Lines format).
Each line is one signal record; outcomes can be updated later.
"""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

LOG_PATH = Path(__file__).parent.parent.parent / "data" / "signals.jsonl"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


@dataclass
class SignalRecord:
    scan_date: str
    ticker: str
    timeframe: str
    action: str
    confidence: float
    price: float
    entry: float
    stop_loss: float
    target: float
    composite_score: float
    reason: str
    outcome_date: str = ""
    outcome_price: float = 0.0
    outcome_pnl_pct: float = 0.0
    outcome_result: str = ""  # HIT_TARGET | HIT_STOP | EXPIRED | PENDING


def append_signals(ranked: dict, entry_levels: dict, scan_date: str,
                   log_path: Path = LOG_PATH) -> int:
    """Write actionable signals (BUY/SELL) from ranked dict to the log file.
    Returns the number of records written."""
    records = []
    for action in ("buy", "sell"):
        for item in ranked.get(action, []):
            ticker = item["ticker"]
            levels = entry_levels.get(ticker, {})
            records.append(SignalRecord(
                scan_date=scan_date,
                ticker=ticker,
                timeframe=item.get("timeframe", ""),
                action=action.upper(),
                confidence=float(item.get("confidence", 0.0)),
                price=float(item.get("price", 0.0)),
                entry=float(levels.get("entry", item.get("price", 0.0))),
                stop_loss=float(levels.get("stop_loss", 0.0)),
                target=float(levels.get("target", 0.0)),
                composite_score=float(item.get("composite", 0.0)),
                reason=str(item.get("reason", "")),
                outcome_result="PENDING",
            ))

    if not records:
        return 0

    with open(log_path, "a") as f:
        for rec in records:
            f.write(json.dumps(asdict(rec)) + "\n")

    return len(records)


def update_outcome(ticker: str, scan_date: str, outcome_price: float,
                   outcome_date: str, log_path: Path = LOG_PATH) -> bool:
    """Find a PENDING record for (ticker, scan_date) and fill in the outcome.
    Returns True if a matching record was updated."""
    if not log_path.exists():
        return False

    lines = log_path.read_text().splitlines()
    updated = False
    new_lines = []

    for line in lines:
        if not line.strip():
            new_lines.append(line)
            continue
        rec = json.loads(line)
        if (rec["ticker"] == ticker and rec["scan_date"] == scan_date
                and rec["outcome_result"] == "PENDING"):
            entry = rec.get("entry", rec["price"])
            target = rec.get("target", 0.0)
            stop = rec.get("stop_loss", 0.0)

            if rec["action"] == "BUY":
                pnl = (outcome_price - entry) / entry if entry else 0.0
            else:
                pnl = (entry - outcome_price) / entry if entry else 0.0

            if target and outcome_price >= target:
                result = "HIT_TARGET"
            elif stop and outcome_price <= stop:
                result = "HIT_STOP"
            else:
                result = "EXPIRED"

            rec["outcome_date"] = outcome_date
            rec["outcome_price"] = outcome_price
            rec["outcome_pnl_pct"] = round(pnl * 100, 2)
            rec["outcome_result"] = result
            updated = True

        new_lines.append(json.dumps(rec))

    log_path.write_text("\n".join(new_lines) + "\n")
    return updated


def update_pending_outcomes(ohlcv_map: dict, hold_days: int = 5,
                            as_of_date: str = None, log_path: Path = LOG_PATH) -> int:
    """
    Resolve PENDING signals using provided OHLCV data.
    For each PENDING record older than hold_days from as_of_date, evaluate
    whether the target or stop was hit in the forward window and update the record.
    Returns the number of records updated.
    """
    from datetime import date as date_cls, timedelta
    if not log_path.exists() or log_path.stat().st_size == 0:
        return 0
    cutoff = as_of_date or date_cls.today().isoformat()

    lines = log_path.read_text().splitlines()
    updated_count = 0
    new_lines = []

    for line in lines:
        if not line.strip():
            new_lines.append(line)
            continue
        rec = json.loads(line)
        if rec.get("outcome_result") != "PENDING":
            new_lines.append(json.dumps(rec))
            continue

        scan_date = rec["scan_date"]
        days_elapsed = (date_cls.fromisoformat(cutoff) - date_cls.fromisoformat(scan_date)).days
        if days_elapsed < hold_days:
            new_lines.append(json.dumps(rec))
            continue

        ticker = rec["ticker"]
        df = ohlcv_map.get(ticker)
        if df is None or df.empty:
            new_lines.append(json.dumps(rec))
            continue

        fwd = df[df.index > scan_date].head(hold_days)
        entry = rec.get("entry") or rec["price"]
        target = rec.get("target", 0.0)
        stop = rec.get("stop_loss", 0.0)
        is_buy = rec["action"] == "BUY"
        result = "EXPIRED"
        outcome_price = float(df["close"].iloc[-1])

        for _, row in fwd.iterrows():
            h, lo = float(row["high"]), float(row["low"])
            if is_buy:
                if target and h >= target:
                    result, outcome_price = "HIT_TARGET", target
                    break
                if stop and lo <= stop:
                    result, outcome_price = "HIT_STOP", stop
                    break
            else:
                if stop and h >= stop:
                    result, outcome_price = "HIT_STOP", stop
                    break
                if target and lo <= target:
                    result, outcome_price = "HIT_TARGET", target
                    break

        pnl = ((outcome_price - entry) / entry if is_buy else (entry - outcome_price) / entry) if entry else 0.0
        rec["outcome_date"] = cutoff
        rec["outcome_price"] = outcome_price
        rec["outcome_pnl_pct"] = round(pnl * 100, 2)
        rec["outcome_result"] = result
        new_lines.append(json.dumps(rec))
        updated_count += 1

    log_path.write_text("\n".join(new_lines) + "\n")
    return updated_count


def load_history(log_path: Path = LOG_PATH) -> pd.DataFrame:
    """Load all signal records into a DataFrame."""
    if not log_path.exists() or log_path.stat().st_size == 0:
        return pd.DataFrame()
    records = [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]
    return pd.DataFrame(records)


def win_rate(timeframe: str = None, log_path: Path = LOG_PATH) -> dict:
    """Compute win rate and average P&L from closed records."""
    df = load_history(log_path)
    if df.empty:
        return {"win_rate": None, "avg_pnl_pct": None, "total_closed": 0}

    closed = df[df["outcome_result"].isin(["HIT_TARGET", "HIT_STOP", "EXPIRED"])]
    if timeframe:
        closed = closed[closed["timeframe"] == timeframe]
    if closed.empty:
        return {"win_rate": None, "avg_pnl_pct": None, "total_closed": 0}

    wins = closed[closed["outcome_pnl_pct"] > 0]
    return {
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "avg_pnl_pct": round(float(closed["outcome_pnl_pct"].mean()), 2),
        "total_closed": len(closed),
    }
