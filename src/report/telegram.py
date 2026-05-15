"""
Telegram notification support.
Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.

Supports both legacy ranked dict format and multiday conviction format.
"""
import os
import requests


def _token() -> str:
    t = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not t:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    return t


def _chat() -> str:
    c = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not c:
        raise RuntimeError("TELEGRAM_CHAT_ID not set")
    return c


def send(text: str) -> None:
    url = f"https://api.telegram.org/bot{_token()}/sendMessage"
    # Telegram limit is 4096 chars per message
    chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
    for chunk in chunks:
        resp = requests.post(
            url,
            json={"chat_id": _chat(), "text": chunk, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()


def _conviction_bar(label: str) -> str:
    return {"HIGH": "███", "MEDIUM": "██░", "LOW": "█░░"}.get(label, "░░░")


def format_scan(
    ranked: dict,
    timeframe: str,
    commentary: str = "",
    regime: dict = None,
    sector_rotation: dict = None,
    portfolio_statuses: list = None,
) -> str:
    """
    Format a full daily scan result for Telegram.

    Supports multiday conviction format (conviction_score, streak_days, etc.)
    with graceful fallback to legacy composite score format.
    """
    lines = [f"<b>VN Stock Scanner — {timeframe.upper()} — {_today()}</b>\n"]

    # ── Market regime ─────────────────────────────────────────────────────────
    if regime:
        r = regime.get("regime", "")
        icon = {"BULL": "🟢", "BEAR": "🔴", "SIDEWAYS": "🟡"}.get(r, "⚪")
        conf = regime.get("confidence", 0)
        r1m = regime.get("r1m_pct", 0)
        r3m = regime.get("r3m_pct", 0)
        lines.append(
            f"{icon} <b>Market: {r}</b> ({conf:.0%} conf)  "
            f"1M: {r1m:+.1f}%  3M: {r3m:+.1f}%\n"
        )

    # ── Sector rotation ───────────────────────────────────────────────────────
    if sector_rotation and sector_rotation.get("actionable"):
        into = ", ".join(sector_rotation.get("rotate_into", [])[:2])
        out_of = ", ".join(sector_rotation.get("rotate_out_of", [])[:2])
        lines.append(f"🔄 <b>Sector rotation:</b> INTO {into} | OUT {out_of}\n")

    # ── AI commentary ─────────────────────────────────────────────────────────
    if commentary:
        lines.append(f"<i>{commentary[:500]}</i>\n")

    # ── Buy/Sell signals ─────────────────────────────────────────────────────
    for action in ("buy", "sell"):
        items = ranked.get(action, [])
        if not items:
            continue
        emoji = "📈" if action == "buy" else "📉"
        lines.append(f"{emoji} <b>TOP {action.upper()}</b>")

        for item in items[:5]:
            price = f"{item.get('price', 0):,.0f}"
            ticker = item.get("ticker", "?")

            # Multiday format
            if "conviction_score" in item:
                score = item.get("conviction_score", 0)
                label = item.get("conviction_label", "?")
                streak = item.get("streak_days", 0)
                bar = _conviction_bar(label)
                lines.append(
                    f"  <b>{ticker}</b> {price}₫  [{score:+.3f}] {bar} {label}  {streak}d"
                )
            else:
                # Legacy composite format
                score = item.get("composite", 0)
                rec = item.get("recommendation", "")
                lines.append(f"  <b>{ticker}</b> {price}₫  [{score:+.2f}] {rec}")

            # Key reasons from signals
            if "signals" in item:
                reasons = "; ".join(
                    s["reason"] for s in item["signals"]
                    if s.get("action") not in ("HOLD", None)
                )[:120]
                if reasons:
                    lines.append(f"  <i>{reasons}</i>")

        lines.append("")

    # ── Portfolio alerts ──────────────────────────────────────────────────────
    if portfolio_statuses:
        action_needed = [s for s in portfolio_statuses if s.needs_action]
        if action_needed:
            lines.append("⚠ <b>PORTFOLIO ALERTS</b>")
            for s in action_needed:
                p = s.position
                icon = "🛑" if s.status == "SELL_STOP" else "✅"
                lines.append(
                    f"  {icon} <b>{p.ticker}</b> [{s.status}]  "
                    f"now={s.current_price:,.0f}  P&L={s.pnl_pct:+.1f}%"
                )
            lines.append("")

    return "\n".join(lines)


def format_portfolio_alert(portfolio_statuses: list) -> str:
    """Standalone portfolio alert message (for urgent send)."""
    lines = ["⚠ <b>PORTFOLIO ALERT</b>\n"]
    for s in portfolio_statuses:
        if not s.needs_action:
            continue
        p = s.position
        icon = "🛑" if s.status == "SELL_STOP" else "✅"
        lines.append(
            f"{icon} <b>{p.ticker}</b> — {s.status}\n"
            f"  Buy: {p.buy_price:,.0f}₫  Now: {s.current_price:,.0f}₫  P&L: {s.pnl_pct:+.1f}%\n"
            f"  {s.message}\n"
        )
    return "\n".join(lines)


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


# Legacy alias kept for backwards compatibility
def format_ranked(ranked: dict, timeframe: str, commentary: str) -> str:
    return format_scan(ranked, timeframe, commentary)
