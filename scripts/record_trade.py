#!/usr/bin/env python3
"""
Record a BUY or SELL trade into data/portfolio.json and send a confirmation email.
Driven by env vars; intended to be called from the record_trade GitHub Actions workflow.
"""
import json
import os
import smtplib
import sys
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

PORTFOLIO_FILE = Path(__file__).parent.parent / "data" / "portfolio.json"


def _load() -> dict:
    if PORTFOLIO_FILE.exists():
        return json.loads(PORTFOLIO_FILE.read_text())
    return {"positions": []}


def _save(data: dict) -> None:
    PORTFOLIO_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _auto_round(price: int, pct: float) -> int:
    """Apply pct to price and round to nearest 100 VND."""
    return round(price * (1 + pct) / 100) * 100


def _send_email(ticker: str, action: str, qty: int, price: int,
                stop_loss: int, target: int, notes: str) -> None:
    gmail_user = os.environ.get("GMAIL_USER", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    to_addr = os.environ.get("NOTIFY_EMAIL", "")

    if not all([gmail_user, app_password, to_addr]):
        print("Email env vars not set — skipping notification")
        return

    today = date.today().isoformat()
    color = "#1a7f4b" if action == "BUY" else "#c0392b"
    emoji = "🟢" if action == "BUY" else "🔴"
    total = qty * price

    def _row(label: str, value: str) -> str:
        return (f'<tr><td style="padding:8px 12px;border-bottom:1px solid #eee;'
                f'color:#666;white-space:nowrap">{label}</td>'
                f'<td style="padding:8px 12px;border-bottom:1px solid #eee">'
                f'<b>{value}</b></td></tr>')

    rows = (
        _row("Date", today)
        + _row("Ticker", ticker)
        + _row("Action", f'<span style="color:{color}">{action}</span>')
        + _row("Quantity", f"{qty:,} shares")
        + _row("Price", f"{price:,} VND")
        + _row("Total value", f"{total:,} VND")
    )
    if action == "BUY":
        sl_pct = (stop_loss / price - 1) * 100
        tgt_pct = (target / price - 1) * 100
        rows += _row("Stop loss", f"{stop_loss:,} VND  ({sl_pct:+.1f}%)")
        rows += _row("Target", f"{target:,} VND  ({tgt_pct:+.1f}%)")
    if notes:
        rows += _row("Notes", notes)

    html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:20px">
  <h2 style="color:{color};margin-bottom:4px">{emoji} Trade Recorded: {action} {ticker}</h2>
  <p style="color:#888;margin-top:0;font-size:13px">portfolio.json updated and committed</p>
  <table style="border-collapse:collapse;width:100%;font-size:14px">
    {rows}
  </table>
  <p style="color:#aaa;font-size:11px;margin-top:20px">
    Sent by VN Trading Desk · analyst-stock-vn
  </p>
</body></html>"""

    subject = f"{emoji} {action} {qty} {ticker} @ {price:,} VND — {today}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"VN Trading Desk <{gmail_user}>"
    msg["To"] = to_addr
    msg.attach(MIMEText(subject, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
        srv.login(gmail_user, app_password)
        srv.sendmail(gmail_user, to_addr, msg.as_string())
    print(f"Confirmation email sent to {to_addr}")


def main() -> None:
    ticker = os.environ.get("TRADE_TICKER", "").strip().upper()
    action = os.environ.get("TRADE_ACTION", "BUY").strip().upper()
    qty = int(os.environ.get("TRADE_QTY", "0") or 0)
    price = int(os.environ.get("TRADE_PRICE", "0") or 0)
    stop_loss = int(os.environ.get("TRADE_STOP_LOSS", "0") or 0)
    target = int(os.environ.get("TRADE_TARGET", "0") or 0)
    notes = os.environ.get("TRADE_NOTES", "").strip()

    if not ticker:
        print("ERROR: TRADE_TICKER is required", file=sys.stderr)
        sys.exit(1)
    if qty <= 0:
        print("ERROR: TRADE_QTY must be > 0", file=sys.stderr)
        sys.exit(1)
    if price <= 0:
        print("ERROR: TRADE_PRICE must be > 0", file=sys.stderr)
        sys.exit(1)
    if action not in ("BUY", "SELL"):
        print(f"ERROR: TRADE_ACTION must be BUY or SELL, got {action!r}", file=sys.stderr)
        sys.exit(1)

    today = date.today().isoformat()
    portfolio = _load()

    if action == "BUY":
        if not stop_loss:
            stop_loss = _auto_round(price, -0.03)
        if not target:
            target = _auto_round(price, 0.07)

        position = {
            "ticker": ticker,
            "buy_date": today,
            "buy_price": price,
            "qty": qty,
            "stop_loss": stop_loss,
            "target": target,
            "notes": notes or f"Bought {today} via iPhone",
        }
        portfolio["positions"].append(position)
        print(f"BUY recorded: {qty} {ticker} @ {price:,} VND  "
              f"(stop={stop_loss:,}  target={target:,})")

    else:  # SELL
        remaining = qty
        new_positions = []
        for pos in portfolio["positions"]:
            if pos["ticker"] == ticker and remaining > 0:
                held = pos.get("qty", 0)
                if held <= remaining:
                    remaining -= held
                    # position fully closed — drop it
                else:
                    pos["qty"] = held - remaining
                    remaining = 0
                    new_positions.append(pos)
            else:
                new_positions.append(pos)

        sold = qty - remaining
        portfolio["positions"] = new_positions
        print(f"SELL recorded: {sold} {ticker} @ {price:,} VND")
        if remaining > 0:
            print(f"WARNING: {remaining} shares not found in portfolio "
                  f"(sold more than held — check portfolio.json)")

    _save(portfolio)
    _send_email(ticker, action, qty, price, stop_loss, target, notes)


if __name__ == "__main__":
    main()
