"""
Price alert system — set configurable price levels, persist in data/alerts.json.

Alerts fire once when the condition is met, then move to 'triggered' state.
Supported conditions: above, below, pct_change_from_cost
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

ALERTS_FILE = Path(__file__).parent.parent / "data" / "alerts.json"
ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)

VALID_CONDITIONS = {"above", "below", "pct_change"}


def _load() -> List[dict]:
    if ALERTS_FILE.exists():
        try:
            return json.loads(ALERTS_FILE.read_text())
        except Exception:
            pass
    return []


def _save(alerts: List[dict]) -> None:
    ALERTS_FILE.write_text(json.dumps(alerts, indent=2, ensure_ascii=False))


def add_alert(
    ticker: str,
    condition: str,
    threshold: float,
    note: str = "",
    reference_price: Optional[float] = None,
) -> dict:
    """
    Add a new price alert.

    condition: 'above' | 'below' | 'pct_change'
    threshold: price level (above/below) or pct (pct_change, e.g. 5.0 = +5%)
    reference_price: used only for pct_change condition (typically avg_cost)
    """
    if condition not in VALID_CONDITIONS:
        raise ValueError(f"condition must be one of {VALID_CONDITIONS}")

    alerts = _load()
    alert_id = max((a["id"] for a in alerts), default=0) + 1

    alert = {
        "id": alert_id,
        "ticker": ticker.upper(),
        "condition": condition,
        "threshold": threshold,
        "reference_price": reference_price,
        "note": note,
        "created_at": date.today().isoformat(),
        "status": "active",
        "triggered_at": None,
        "triggered_price": None,
    }
    alerts.append(alert)
    _save(alerts)
    logger.info("Alert %d added: %s %s %.2f", alert_id, ticker, condition, threshold)
    return alert


def remove_alert(alert_id: int) -> bool:
    """Remove an alert by ID. Returns True if found."""
    alerts = _load()
    before = len(alerts)
    alerts = [a for a in alerts if a["id"] != alert_id]
    _save(alerts)
    return len(alerts) < before


def list_alerts(ticker: Optional[str] = None, status: str = "active") -> List[dict]:
    """List alerts, optionally filtered by ticker and/or status."""
    alerts = _load()
    if status != "all":
        alerts = [a for a in alerts if a["status"] == status]
    if ticker:
        alerts = [a for a in alerts if a["ticker"] == ticker.upper()]
    return alerts


def _check_condition(alert: dict, current_price: float) -> bool:
    cond = alert["condition"]
    threshold = alert["threshold"]
    ref = alert.get("reference_price")

    if cond == "above":
        return current_price >= threshold
    if cond == "below":
        return current_price <= threshold
    if cond == "pct_change" and ref and ref > 0:
        pct = (current_price / ref - 1) * 100
        return pct >= threshold if threshold >= 0 else pct <= threshold
    return False


def check_alerts(prices: Dict[str, float]) -> List[dict]:
    """
    Check all active alerts against current prices.

    prices: {ticker: current_price}
    Returns list of triggered alert dicts.
    """
    alerts = _load()
    triggered = []
    changed = False

    for alert in alerts:
        if alert["status"] != "active":
            continue
        ticker = alert["ticker"]
        price = prices.get(ticker)
        if price is None:
            continue

        if _check_condition(alert, price):
            alert["status"] = "triggered"
            alert["triggered_at"] = datetime.now().isoformat()[:16]
            alert["triggered_price"] = price
            triggered.append(alert)
            changed = True
            logger.info(
                "Alert triggered: %s %s %.2f (price=%.2f)",
                ticker, alert["condition"], alert["threshold"], price,
            )

    if changed:
        _save(alerts)

    return triggered


def bulk_check(prices: Dict[str, float]) -> List[dict]:
    """Alias for check_alerts for clarity in callers."""
    return check_alerts(prices)


def format_triggered(triggered: List[dict]) -> str:
    """Format triggered alerts for notification messages."""
    if not triggered:
        return ""
    lines = ["🔔 PRICE ALERTS TRIGGERED:"]
    for a in triggered:
        cond_str = {
            "above": f"≥ {a['threshold']:,.0f}",
            "below": f"≤ {a['threshold']:,.0f}",
            "pct_change": f"{a['threshold']:+.1f}% from cost",
        }.get(a["condition"], str(a["threshold"]))
        lines.append(
            f"  {a['ticker']} {cond_str} — hit {a.get('triggered_price', 0):,.0f}"
            + (f" ({a['note']})" if a.get("note") else "")
        )
    return "\n".join(lines)


def reset_triggered(alert_id: Optional[int] = None) -> int:
    """Re-activate triggered alerts. Pass id to reset one, None to reset all."""
    alerts = _load()
    count = 0
    for a in alerts:
        if a["status"] == "triggered":
            if alert_id is None or a["id"] == alert_id:
                a["status"] = "active"
                a["triggered_at"] = None
                a["triggered_price"] = None
                count += 1
    _save(alerts)
    return count
