#!/usr/bin/env python3
"""
Market-open portfolio advisor.

Runs at 9:15 AM ICT (after ATO, before full morning session).
Fetches live prices for current portfolio positions via vnstock,
computes RSI / MACD / MA / ATR, decides BUY / SELL / HOLD, and
sends a decision email.

Cron (local):   15 9 * * 1-5  (ICT)
GitHub Actions: market_open_advisor.yml  →  02:15 UTC Mon–Fri
"""
import json
import os
import smtplib
import sys
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv(Path(__file__).parent.parent / ".env")

from src.fetcher.vnstock_client import get_ohlcv

PORTFOLIO_FILE = Path(__file__).parent.parent / "data" / "portfolio.json"
ICT = timezone(timedelta(hours=7))


# ── Data ──────────────────────────────────────────────────────────────────────

def _fetch_ohlcv(ticker: str, days: int = 90) -> pd.DataFrame:
    """Daily OHLCV in VND via the shared cached vnstock client."""
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=days)).isoformat()
    return get_ohlcv(ticker, start, end)


def _load_portfolio() -> list[dict]:
    if not PORTFOLIO_FILE.exists():
        return []
    data = json.loads(PORTFOLIO_FILE.read_text())
    # Deduplicate: one entry per ticker, skip placeholders (buy_price=0)
    seen: dict[str, dict] = {}
    for pos in data.get("positions", []):
        t = pos["ticker"]
        if pos.get("buy_price", 0) == 0:
            continue
        if t not in seen or pos["buy_price"] > 0:
            seen[t] = pos
    return list(seen.values())


# ── Indicators ────────────────────────────────────────────────────────────────

def _rsi(series: pd.Series, n: int = 14) -> float:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(n).mean()
    loss  = (-delta.clip(upper=0)).rolling(n).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - 100 / (1 + rs)
    return float(rsi.iloc[-1])


def _macd_hist(series: pd.Series) -> tuple[float, float]:
    """Returns (hist_today, hist_yesterday) for crossover detection."""
    ema12 = series.ewm(span=12).mean()
    ema26 = series.ewm(span=26).mean()
    line  = ema12 - ema26
    sig   = line.ewm(span=9).mean()
    hist  = line - sig
    return float(hist.iloc[-1]), float(hist.iloc[-2])


def _atr(df: pd.DataFrame, n: int = 14) -> float:
    hl   = df["high"] - df["low"]
    hpc  = (df["high"] - df["close"].shift()).abs()
    lpc  = (df["low"]  - df["close"].shift()).abs()
    tr   = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    return float(tr.rolling(n).mean().iloc[-1])


# ── Decision engine ────────────────────────────────────────────────────────────

def _decide(pos: dict, df: pd.DataFrame) -> dict:
    close   = float(df["close"].iloc[-1])
    buy     = pos["buy_price"]
    stop    = pos.get("stop_loss", 0)
    target  = pos.get("target", 0)
    qty     = pos.get("qty", 0)

    rsi_val              = _rsi(df["close"])
    hist_now, hist_prev  = _macd_hist(df["close"])
    ma20  = float(df["close"].rolling(20).mean().iloc[-1])
    ma50  = float(df["close"].rolling(50).mean().iloc[-1])
    atr14 = _atr(df)
    mom5  = (close / float(df["close"].iloc[-6]) - 1) * 100

    pnl_pct = (close / buy - 1) * 100 if buy else 0
    pnl_vnd = (close - buy) * qty if buy and qty else 0

    # Trend
    above_ma20 = close > ma20
    above_ma50 = close > ma50

    # MACD crossover
    macd_bull = hist_prev < 0 < hist_now
    macd_bear = hist_prev > 0 > hist_now

    reasons: list[str] = []
    action = "HOLD"
    urgency = "normal"   # normal | urgent

    # ── SELL conditions ───────────────────────────────────────────
    sell_signals = 0

    if target and close >= target:
        reasons.append(f"Target hit ({target:,} VND)")
        sell_signals += 2
    if stop and close <= stop:
        reasons.append(f"Stop triggered ({stop:,} VND)")
        sell_signals += 3
        urgency = "urgent"
    if stop and (close - stop) < atr14:
        reasons.append(f"Stop within 1 ATR — {close - stop:,.0f} VND cushion")
        sell_signals += 1
        urgency = "urgent"
    if rsi_val >= 75:
        reasons.append(f"RSI overbought ({rsi_val:.1f})")
        sell_signals += 1
    if macd_bear:
        reasons.append("MACD bearish crossover")
        sell_signals += 1
    if not above_ma20 and not above_ma50:
        reasons.append("Below MA20 and MA50 — downtrend")
        sell_signals += 1

    # ── BUY / ADD conditions ──────────────────────────────────────
    buy_signals = 0
    if rsi_val <= 30 and above_ma50:
        reasons.append(f"RSI oversold ({rsi_val:.1f}) with MA50 support — bounce setup")
        buy_signals += 2
    if macd_bull and above_ma20:
        reasons.append("MACD bullish crossover above MA20")
        buy_signals += 2
    if mom5 >= 5 and above_ma20 and above_ma50:
        reasons.append(f"Strong 5-day momentum ({mom5:+.1f}%) in uptrend")
        buy_signals += 1

    # Final decision
    if sell_signals >= 2:
        action = "SELL"
    elif sell_signals == 1 and buy_signals == 0:
        action = "WATCH — possible sell"
    elif buy_signals >= 2:
        action = "BUY / ADD"
    elif buy_signals == 1:
        action = "WATCH — possible buy"
    else:
        if not reasons:
            reasons.append("No strong signal — momentum neutral")

    return {
        "ticker":   pos["ticker"],
        "action":   action,
        "urgency":  urgency,
        "close":    close,
        "buy":      buy,
        "stop":     stop,
        "target":   target,
        "qty":      qty,
        "pnl_pct":  pnl_pct,
        "pnl_vnd":  pnl_vnd,
        "rsi":      rsi_val,
        "macd_hist": hist_now,
        "ma20":     ma20,
        "ma50":     ma50,
        "atr14":    atr14,
        "mom5":     mom5,
        "reasons":  reasons,
    }


# ── Email ─────────────────────────────────────────────────────────────────────

def _action_color(action: str, urgency: str) -> str:
    if action.startswith("SELL"):   return "#c0392b"
    if action.startswith("BUY"):    return "#1a7f4b"
    if urgency == "urgent":         return "#e67e22"
    return "#2c3e50"


def _row(label: str, value: str) -> str:
    return (
        f'<tr>'
        f'<td style="padding:6px 10px;color:#666;white-space:nowrap;font-size:13px">{label}</td>'
        f'<td style="padding:6px 10px;font-size:13px">{value}</td>'
        f'</tr>'
    )


def _position_card(d: dict) -> str:
    color  = _action_color(d["action"], d["urgency"])
    pnl_s  = f"{d['pnl_pct']:+.1f}%"
    if d["qty"] and d["pnl_vnd"]:
        pnl_s += f" ({d['pnl_vnd']:+,.0f} VND on {d['qty']} shares)"

    rows  = _row("Current price", f"<b>{d['close']:,.0f} VND</b>")
    rows += _row("Buy price",     f"{d['buy']:,.0f} VND")
    rows += _row("P/L",           f"<b style='color:{color}'>{pnl_s}</b>")
    if d["stop"]:
        rows += _row("Stop / Target", f"{d['stop']:,.0f} → {d['target']:,.0f} VND")
    rows += _row("RSI(14)",       f"{d['rsi']:.1f}")
    rows += _row("MACD hist",     f"{d['macd_hist']:+,.0f}")
    rows += _row("MA20 / MA50",   f"{d['ma20']:,.0f} / {d['ma50']:,.0f}")
    rows += _row("ATR14",         f"{d['atr14']:,.0f}")
    rows += _row("5-day move",    f"{d['mom5']:+.1f}%")

    reasons_html = "".join(f"<li>{r}</li>" for r in d["reasons"])

    return f"""
    <div style="border:2px solid {color};border-radius:8px;margin-bottom:18px;overflow:hidden">
      <div style="background:{color};color:white;padding:10px 14px;font-size:16px;font-weight:bold">
        {d['ticker']} — {d['action']}
        {'&nbsp;&nbsp;⚠️ URGENT' if d['urgency'] == 'urgent' else ''}
      </div>
      <table style="border-collapse:collapse;width:100%;background:white">
        {rows}
      </table>
      <div style="background:#f8f9fa;padding:10px 14px;font-size:13px">
        <b>Signals:</b>
        <ul style="margin:4px 0 0 16px;padding:0">{reasons_html}</ul>
      </div>
    </div>"""


def _send_email(decisions: list[dict]) -> None:
    gmail_user   = os.environ.get("GMAIL_USER", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    to_addr      = os.environ.get("NOTIFY_EMAIL", "")

    if not all([gmail_user, app_password, to_addr]):
        print("Email env vars not set — printing decisions instead:\n")
        for d in decisions:
            print(f"  {d['ticker']:6s} {d['action']:20s}  close={d['close']:,.0f}  "
                  f"P/L={d['pnl_pct']:+.1f}%  RSI={d['rsi']:.1f}")
            for r in d["reasons"]:
                print(f"           → {r}")
        return

    now     = datetime.now(ICT)
    today   = date.today().isoformat()
    urgent  = [d for d in decisions if d["urgency"] == "urgent"]
    actions = [d for d in decisions if not d["action"].startswith("HOLD") and not d["action"].startswith("WATCH")]

    if urgent:
        subject = f"⚠️ URGENT ACTION: {', '.join(d['ticker'] for d in urgent)} — {today}"
    elif actions:
        subject = f"📊 Market Open Advisor: {len(actions)} action(s) — {today}"
    else:
        subject = f"📊 Market Open Advisor: HOLD all — {today}"

    cards_html = "".join(_position_card(d) for d in decisions)

    html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:560px;margin:0 auto;padding:20px;color:#1a1a1a">
  <h2 style="margin-bottom:4px">📊 Market Open Advisor</h2>
  <p style="color:#888;margin-top:0;font-size:13px">
    {now.strftime('%A %d %b %Y · %H:%M ICT')} &nbsp;·&nbsp;
    Data: vnstock (VCI) · Signals: RSI / MACD / MA / ATR
  </p>
  {cards_html}
  <p style="color:#aaa;font-size:11px;margin-top:20px">
    VN Trading Desk · analyst-stock-vn<br>
    This is an automated signal — always verify before executing.
  </p>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"VN Trading Desk <{gmail_user}>"
    msg["To"]      = to_addr
    msg.attach(MIMEText(subject, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
        srv.login(gmail_user, app_password)
        srv.sendmail(gmail_user, to_addr, msg.as_string())
    print(f"Email sent → {to_addr}  [{subject}]")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    positions = _load_portfolio()
    if not positions:
        print("No positions in portfolio — nothing to advise.")
        return

    decisions: list[dict] = []
    for pos in positions:
        ticker = pos["ticker"]
        print(f"  Fetching {ticker}…", end=" ", flush=True)
        try:
            df = _fetch_ohlcv(ticker, days=120)
            if df is None or len(df) < 30:
                print("not enough data — skipped")
                continue
            d = _decide(pos, df)
            decisions.append(d)
            print(f"close={d['close']:,.0f}  RSI={d['rsi']:.1f}  → {d['action']}")
        except Exception as e:
            print(f"ERROR: {e}")

    if not decisions:
        # We had positions but produced zero decisions — every fetch failed or
        # returned too little data. That is a failure, not a quiet "all clear":
        # exit non-zero so the workflow's failure alert fires instead of the run
        # ending green with no email sent.
        print("No decisions generated — all price fetches failed or returned too little data.")
        sys.exit(1)

    _send_email(decisions)


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  Market Open Advisor — {date.today()}")
    print(f"{'='*60}\n")
    main()
    print("\nDone.")
