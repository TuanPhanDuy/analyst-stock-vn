#!/usr/bin/env python3
"""
Record a BUY or SELL trade into data/portfolio.json and send a confirmation email.
Driven by env vars; called from the record_trade GitHub Actions workflow.

Price input is VCBS format (nghìn đồng): 26.5 → 26,500 VND.
Stop loss and target are computed automatically from 14-day ATR (2:1 R:R).
"""
import json
import os
import smtplib
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow imports from src/ when run from repo root or scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))

PORTFOLIO_FILE = Path(__file__).parent.parent / "data" / "portfolio.json"


# ---------------------------------------------------------------------------
# Portfolio I/O
# ---------------------------------------------------------------------------

def _load() -> dict:
    if PORTFOLIO_FILE.exists():
        return json.loads(PORTFOLIO_FILE.read_text())
    return {"positions": []}


def _save(data: dict) -> None:
    PORTFOLIO_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Price conversion
# ---------------------------------------------------------------------------

def vcbs_to_vnd(vcbs_str: str) -> int:
    """Convert VCBS app format to integer VND. '26.5' → 26500, '150' → 150000."""
    return round(float(vcbs_str) * 1000 / 100) * 100


def vnd_to_vcbs(vnd: int) -> str:
    """26500 → '26.5', 150000 → '150'"""
    v = vnd / 1000
    return f"{v:.2f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# ATR-based stop / target
# ---------------------------------------------------------------------------

def _compute_levels(ticker: str, entry: int) -> tuple[int, int]:
    """
    Fetch 60 days of OHLCV and compute:
      stop   = entry - 1.5 × ATR14   (volatility-adaptive)
      target = entry + 2 × (entry - stop)   (2:1 risk:reward)

    Falls back to entry −3% / +7% if vnstock is unavailable.
    """
    try:
        from src.fetcher.vnstock_client import get_ohlcv

        end = date.today().isoformat()
        start = (date.today() - timedelta(days=90)).isoformat()
        df = get_ohlcv(ticker, start, end, ttl=3600)

        if df is None or len(df) < 15:
            raise ValueError("not enough bars")

        df = df.tail(20).copy()
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values

        true_ranges = [
            max(h - l, abs(h - pc), abs(l - pc))
            for h, l, pc in zip(highs[1:], lows[1:], closes[:-1])
        ]
        atr14 = sum(true_ranges[-14:]) / 14

        stop_raw = entry - 1.5 * atr14
        stop = round(stop_raw / 100) * 100
        risk = entry - stop
        target = round((entry + 2 * risk) / 100) * 100

        print(f"ATR14={atr14:,.0f}  stop={stop:,}  target={target:,}")
        return stop, target

    except Exception as exc:
        print(f"ATR compute failed ({exc}) — using default −3% / +7%")
        stop = round(entry * 0.97 / 100) * 100
        target = round(entry * 1.07 / 100) * 100
        return stop, target


# ---------------------------------------------------------------------------
# Email confirmation
# ---------------------------------------------------------------------------

def _send_email(ticker: str, action: str, qty: int, entry: int,
                stop: int, target: int, notes: str) -> None:
    gmail_user = os.environ.get("GMAIL_USER", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    to_addr = os.environ.get("NOTIFY_EMAIL", "")

    if not all([gmail_user, app_password, to_addr]):
        print("Email env vars not set — skipping notification")
        return

    today = date.today().isoformat()
    color = "#1a7f4b" if action == "BUY" else "#c0392b"
    emoji = "🟢" if action == "BUY" else "🔴"
    total = qty * entry

    def row(label: str, value: str) -> str:
        return (
            f'<tr>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #eee;color:#666;white-space:nowrap">{label}</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #eee"><b>{value}</b></td>'
            f'</tr>'
        )

    sl_pct = (stop / entry - 1) * 100
    tgt_pct = (target / entry - 1) * 100

    rows = (
        row("Date", today)
        + row("Ticker", ticker)
        + row("Action", f'<span style="color:{color}">{action}</span>')
        + row("Quantity", f"{qty:,} shares")
        + row("Price", f"{vnd_to_vcbs(entry)} ({entry:,} VND)")
        + row("Total value", f"{total:,} VND")
    )
    if action == "BUY":
        rows += row("Stop loss", f"{vnd_to_vcbs(stop)} ({stop:,} VND &nbsp; {sl_pct:+.1f}%)")
        rows += row("Target", f"{vnd_to_vcbs(target)} ({target:,} VND &nbsp; {tgt_pct:+.1f}%)")
    if notes:
        rows += row("Notes", notes)

    html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:20px">
  <h2 style="color:{color};margin-bottom:4px">{emoji} Trade Recorded: {action} {ticker}</h2>
  <p style="color:#888;margin-top:0;font-size:13px">
    Stop &amp; target computed from 14-day ATR (2:1 R:R) · portfolio.json updated
  </p>
  <table style="border-collapse:collapse;width:100%;font-size:14px">
    {rows}
  </table>
  <p style="color:#aaa;font-size:11px;margin-top:20px">VN Trading Desk · analyst-stock-vn</p>
</body></html>"""

    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    import smtplib

    subject = f"{emoji} {action} {qty} {ticker} @ {vnd_to_vcbs(entry)} — {today}"
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ticker = os.environ.get("TRADE_TICKER", "").strip().upper()
    action = os.environ.get("TRADE_ACTION", "BUY").strip().upper()
    qty = int(os.environ.get("TRADE_QTY", "0") or 0)
    price_raw = os.environ.get("TRADE_PRICE", "").strip()
    notes = os.environ.get("TRADE_NOTES", "").strip()

    if not ticker:
        print("ERROR: TRADE_TICKER is required", file=sys.stderr)
        sys.exit(1)
    if qty <= 0:
        print("ERROR: TRADE_QTY must be > 0", file=sys.stderr)
        sys.exit(1)
    if not price_raw:
        print("ERROR: TRADE_PRICE is required", file=sys.stderr)
        sys.exit(1)
    if action not in ("BUY", "SELL"):
        print(f"ERROR: TRADE_ACTION must be BUY or SELL, got {action!r}", file=sys.stderr)
        sys.exit(1)

    try:
        entry = vcbs_to_vnd(price_raw)
    except ValueError:
        print(f"ERROR: invalid price {price_raw!r} — use VCBS format e.g. 26.5", file=sys.stderr)
        sys.exit(1)

    if entry <= 0:
        print("ERROR: price must be > 0", file=sys.stderr)
        sys.exit(1)

    today = date.today().isoformat()
    portfolio = _load()

    if action == "BUY":
        stop, target = _compute_levels(ticker, entry)
        position = {
            "ticker": ticker,
            "buy_date": today,
            "buy_price": entry,
            "qty": qty,
            "stop_loss": stop,
            "target": target,
            "notes": notes or f"Bought {today} via iPhone",
        }
        portfolio["positions"].append(position)
        print(f"BUY recorded: {qty} {ticker} @ {vnd_to_vcbs(entry)} "
              f"(stop={vnd_to_vcbs(stop)}, target={vnd_to_vcbs(target)})")

    else:  # SELL
        remaining = qty
        new_positions = []
        for pos in portfolio["positions"]:
            if pos["ticker"] == ticker and remaining > 0:
                held = pos.get("qty", 0)
                if held <= remaining:
                    remaining -= held
                else:
                    pos["qty"] = held - remaining
                    remaining = 0
                    new_positions.append(pos)
            else:
                new_positions.append(pos)

        sold = qty - remaining
        portfolio["positions"] = new_positions
        print(f"SELL recorded: {sold} {ticker} @ {vnd_to_vcbs(entry)}")
        if remaining > 0:
            print(f"WARNING: {remaining} shares not found in portfolio")
        stop = target = 0

    _save(portfolio)
    _send_email(ticker, action, qty, entry, stop, target, notes)


if __name__ == "__main__":
    main()
