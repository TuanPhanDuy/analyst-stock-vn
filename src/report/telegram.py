"""Send scan results to Telegram. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env."""
import json
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
    resp = requests.post(url, json={"chat_id": _chat(), "text": text, "parse_mode": "HTML"}, timeout=10)
    resp.raise_for_status()


def format_ranked(ranked: dict, timeframe: str, commentary: str) -> str:
    lines = [f"<b>VN Stock Scanner — {timeframe.upper()}</b>\n"]

    if commentary:
        lines.append(f"<i>{commentary[:600]}</i>\n")

    for action in ("buy", "sell"):
        items = ranked.get(action, [])
        if not items:
            continue
        emoji = "📈" if action == "buy" else "📉"
        lines.append(f"{emoji} <b>TOP {action.upper()}</b>")
        for item in items[:5]:
            rec = item["recommendation"]
            price = f"{item['price']:,.0f}"
            score = f"{item['composite']:+.2f}"
            reasons = "; ".join(
                s["reason"] for s in item["signals"] if s["action"] != "HOLD"
            )[:100]
            lines.append(f"  <b>{item['ticker']}</b> {price} VND [{score}] {rec}")
            lines.append(f"  <i>{reasons}</i>")
        lines.append("")

    return "\n".join(lines)
