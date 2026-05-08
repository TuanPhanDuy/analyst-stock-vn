"""
Claude-powered news and event monitor for held positions.
Accepts a list of tickers and optional headlines from CafeF/FireAnt scrape.
Flags material events (earnings, regulatory, legal) per ticker.
"""
import json

import anthropic
import yaml

from datetime import date


def monitor(tickers: list[str], headlines: list[str] = None,
            cfg: dict = None) -> dict:
    """
    Check for material events affecting the given tickers.

    Args:
        tickers: list of VN ticker symbols to monitor.
        headlines: optional list of recent news headlines to analyze.
                   If None, Claude uses its knowledge (date-limited).
        cfg: config dict (loaded from config.yaml). If None, loads from file.

    Returns:
        {
          "flagged": [{"ticker", "event", "severity", "recommendation"}],
          "all_clear": [ticker, ...]
        }
    """
    if cfg is None:
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)

    claude_cfg = cfg.get("claude", {})
    client = anthropic.Anthropic()

    headlines_block = ""
    if headlines:
        headlines_block = "\n=== PROVIDED HEADLINES ===\n" + "\n".join(
            f"- {h}" for h in headlines
        )
    else:
        headlines_block = (
            "\n[No headlines provided — use your knowledge up to today's date. "
            "Only flag events you are highly confident about. "
            "Prefer false negatives over false positives.]"
        )

    prompt = f"""You are a Vietnamese equity research analyst monitoring news for a retail investor's portfolio.
Today: {date.today().isoformat()}
Tickers to monitor: {', '.join(tickers)}
{headlines_block}

For each ticker, identify any recent MATERIAL events:
- Earnings releases (surprise beats/misses)
- Regulatory changes or government actions
- Legal/criminal investigations
- Major management changes (CEO/CFO departure)
- Mergers, acquisitions, major deals
- Significant credit rating changes

Be CONSERVATIVE — only flag events with HIGH confidence. If uncertain, do NOT flag.

Respond ONLY with valid JSON in this exact shape:
{{
  "flagged": [
    {{
      "ticker": "XXX",
      "event": "brief description of the event",
      "severity": "HIGH|MEDIUM|LOW",
      "recommendation": "REVIEW|HOLD|WATCH",
      "source_note": "where you know this from or 'from provided headlines'"
    }}
  ],
  "all_clear": ["list of tickers with no material events detected"],
  "analysis_date": "{date.today().isoformat()}"
}}

If you have no reliable information about a ticker, place it in all_clear rather than guessing."""

    message = client.messages.create(
        model=claude_cfg.get("model", "claude-sonnet-4-6"),
        max_tokens=claude_cfg.get("max_tokens_scan", 600),
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[:-3]

    return json.loads(raw)


def print_monitor_report(result: dict) -> None:
    flagged = result.get("flagged", [])
    all_clear = result.get("all_clear", [])

    print("\n=== News Monitor ===")
    if flagged:
        print(f"\nFLAGGED ({len(flagged)}):")
        for item in flagged:
            sev = item.get("severity", "?")
            marker = "🔴" if sev == "HIGH" else ("🟡" if sev == "MEDIUM" else "🟢")
            print(f"  {marker} {item['ticker']:5s} [{sev:6s}] {item['event']}")
            print(f"         → {item.get('recommendation', '')}  ({item.get('source_note', '')})")
    if all_clear:
        print(f"\nALL CLEAR: {', '.join(all_clear)}")
    if not flagged and not all_clear:
        print("  No results.")


if __name__ == "__main__":
    import sys
    tickers_arg = sys.argv[1].split(",") if len(sys.argv) > 1 else ["VCB", "FPT", "HPG"]
    tickers_arg = [t.strip() for t in tickers_arg]
    result = monitor(tickers_arg)
    print_monitor_report(result)
