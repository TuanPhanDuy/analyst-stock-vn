"""
Signal deduplication — suppress repeated signals that haven't changed.

Persists signal state to data/signal_state.json. A signal is considered
"new" if: action changed, confidence moved by ≥ CONFIDENCE_DELTA, or
it hasn't been seen in more than STALE_AFTER_DAYS days.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent.parent / "data" / "signal_state.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

CONFIDENCE_DELTA = 0.08   # min change to treat as new
STALE_AFTER_DAYS = 3      # re-surface even if unchanged after N days


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _sig_key(signal: dict) -> str:
    return f"{signal.get('ticker', '')}_{signal.get('timeframe', '')}"


def is_new(signal: dict, state: Optional[dict] = None) -> bool:
    """Return True if this signal is meaningfully new vs last seen."""
    if state is None:
        state = _load_state()

    key = _sig_key(signal)
    prev = state.get(key)
    if prev is None:
        return True

    if signal.get("action") != prev.get("action"):
        return True

    conf_diff = abs(signal.get("confidence", 0) - prev.get("confidence", 0))
    if conf_diff >= CONFIDENCE_DELTA:
        return True

    last_seen = prev.get("date", "")
    if last_seen:
        try:
            delta = (date.today() - date.fromisoformat(last_seen)).days
            if delta >= STALE_AFTER_DAYS:
                return True
        except Exception:
            return True

    return False


def filter_new(signals: List[dict]) -> List[dict]:
    """
    Filter a list of signals, returning only those that are new/changed.
    Also updates the state file for seen signals.
    """
    state = _load_state()
    new_signals = []

    for sig in signals:
        if is_new(sig, state):
            new_signals.append(sig)

    # Update state for ALL signals (not just new ones) so staleness tracking works
    for sig in signals:
        key = _sig_key(sig)
        state[key] = {
            "action": sig.get("action"),
            "confidence": sig.get("confidence", 0),
            "date": date.today().isoformat(),
        }

    _save_state(state)
    return new_signals


def mark_seen(signals: List[dict]) -> None:
    """Mark signals as seen without filtering."""
    state = _load_state()
    for sig in signals:
        key = _sig_key(sig)
        state[key] = {
            "action": sig.get("action"),
            "confidence": sig.get("confidence", 0),
            "date": date.today().isoformat(),
        }
    _save_state(state)


def clear_stale(older_than_days: int = 30) -> int:
    """Remove state entries older than N days. Returns count removed."""
    state = _load_state()
    cutoff = (date.today() - timedelta(days=older_than_days)).isoformat()
    before = len(state)
    state = {k: v for k, v in state.items() if v.get("date", "") >= cutoff}
    _save_state(state)
    return before - len(state)


def state_summary() -> dict:
    """Return counts of tracked signal states."""
    state = _load_state()
    action_counts: Dict[str, int] = {}
    for v in state.values():
        a = v.get("action", "UNKNOWN")
        action_counts[a] = action_counts.get(a, 0) + 1
    return {
        "total_tracked": len(state),
        "by_action": action_counts,
    }
