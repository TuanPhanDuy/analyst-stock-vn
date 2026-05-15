"""
Claude-powered news and event monitor for held positions.

Auto-scrapes real headlines from CafeF, FireAnt, and VNDirect announcements
using news_scraper.py. Flags material events per ticker.
"""
import json
import logging
from datetime import date
from typing import List, Optional

import anthropic
import yaml

logger = logging.getLogger(__name__)


def _scrape_headlines(tickers: List[str], days: int = 7) -> tuple[dict, str]:
    """
    Fetch real headlines for the given tickers using news_scraper.
    Returns (headlines_by_ticker, formatted_text).
    """
    try:
        from src.fetcher.news_scraper import fetch_bulk, format_for_claude
        headlines_map = fetch_bulk(tickers, days=days)
        # Build a combined formatted block
        all_items = []
        for ticker, items in headlines_map.items():
            all_items.extend(items)
        # Sort by date desc
        all_items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        formatted = format_for_claude(all_items, max_items=40)
        return headlines_map, formatted
    except Exception as e:
        logger.warning("News scraper failed: %s", e)
        return {}, ""


def _earnings_flags(tickers: List[str]) -> dict:
    """Get earnings/event warnings for all tickers."""
    try:
        from src.earnings import bulk_flags
        return bulk_flags(tickers, warn_days=14)
    except Exception:
        return {}


def monitor(
    tickers: List[str],
    headlines: Optional[List[str]] = None,
    cfg: Optional[dict] = None,
    days: int = 7,
    auto_scrape: bool = True,
) -> dict:
    """
    Check for material events affecting the given tickers.

    Args:
        tickers: VN ticker symbols to monitor.
        headlines: optional list of pre-fetched headline strings (overrides auto_scrape).
        cfg: config dict. If None, loads from config.yaml.
        days: how many days back to look for news.
        auto_scrape: whether to auto-fetch from CafeF/FireAnt/VNDirect.

    Returns:
        {
          "flagged": [{"ticker", "event", "severity", "recommendation"}],
          "all_clear": [ticker, ...],
          "scraped_headlines": {ticker: count},
          "earnings_warnings": {...},
        }
    """
    if cfg is None:
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)

    # Auto-scrape real headlines
    headlines_map: dict = {}
    headlines_text = ""

    if headlines is not None:
        # Caller provided headlines directly
        headlines_text = "\n".join(f"- {h}" for h in headlines)
    elif auto_scrape:
        headlines_map, headlines_text = _scrape_headlines(tickers, days=days)

    # Earnings/event warnings (non-Claude, deterministic)
    earnings_flags = _earnings_flags(tickers)
    earnings_block = ""
    urgent_earnings = {t: v for t, v in earnings_flags.items() if v.get("flag")}
    if urgent_earnings:
        lines = [f"  {t}: {v.get('message', '')}" for t, v in urgent_earnings.items()]
        earnings_block = "\n=== UPCOMING CORPORATE EVENTS (within 14 days) ===\n" + "\n".join(lines)

    headlines_block = ""
    if headlines_text:
        headlines_block = f"\n=== SCRAPED HEADLINES (last {days} days) ===\n{headlines_text}"
    else:
        headlines_block = (
            "\n[No headlines available — use your knowledge up to today's date. "
            "Only flag events you are highly confident about.]"
        )

    claude_cfg = cfg.get("claude", {})
    client = anthropic.Anthropic()

    prompt = f"""You are a Vietnamese equity research analyst monitoring news for a retail investor's portfolio.
Today: {date.today().isoformat()}
Tickers to monitor: {', '.join(tickers)}
{headlines_block}
{earnings_block}

For each ticker, identify any recent MATERIAL events:
- Earnings releases (beats/misses vs expectations)
- Regulatory changes, government investigations, or policy announcements
- Legal/criminal investigations or major lawsuits
- Management changes (CEO/CFO departure or appointment)
- Mergers, acquisitions, strategic partnerships
- Significant credit rating changes or debt restructuring
- Major insider buying/selling announcements
- Dividend announcements or changes

Be CONSERVATIVE — only flag events with HIGH confidence from the provided headlines.
If a headline is ambiguous, assess severity as LOW not HIGH.

Respond ONLY with valid JSON:
{{
  "flagged": [
    {{
      "ticker": "XXX",
      "event": "brief description",
      "severity": "HIGH|MEDIUM|LOW",
      "recommendation": "REVIEW_POSITION|HOLD|WATCH_CLOSELY",
      "headline_source": "exact or paraphrased headline that triggered this flag"
    }}
  ],
  "all_clear": ["tickers with no material events"],
  "market_themes": ["any broad VN market themes from headlines"],
  "analysis_date": "{date.today().isoformat()}"
}}"""

    message = client.messages.create(
        model=claude_cfg.get("model", "claude-sonnet-4-6"),
        max_tokens=cfg.get("claude", {}).get("max_tokens_scan", 800),
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:])
    if raw.endswith("```"):
        raw = raw[:-3].rstrip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"flagged": [], "all_clear": tickers, "error": "JSON parse failed"}

    # Attach metadata
    result["scraped_headlines"] = {t: len(v) for t, v in headlines_map.items()}
    result["earnings_warnings"] = earnings_flags

    return result


def print_monitor_report(result: dict) -> None:
    flagged = result.get("flagged", [])
    all_clear = result.get("all_clear", [])
    scraped = result.get("scraped_headlines", {})
    earnings = result.get("earnings_warnings", {})
    themes = result.get("market_themes", [])

    print("\n=== News Monitor Report ===")

    # Scraped counts
    if scraped:
        total = sum(scraped.values())
        print(f"Headlines scraped: {total} across {len(scraped)} tickers")

    # Earnings warnings
    urgent = {t: v for t, v in earnings.items() if v.get("flag")}
    if urgent:
        print("\n⚡ UPCOMING EVENTS:")
        for t, v in urgent.items():
            print(f"  {t}: {v.get('message', '')}")

    # Market themes
    if themes:
        print("\n📰 Market themes: " + " | ".join(themes))

    if flagged:
        print(f"\nFLAGGED ({len(flagged)}):")
        for item in flagged:
            sev = item.get("severity", "?")
            marker = "🔴" if sev == "HIGH" else ("🟡" if sev == "MEDIUM" else "🟢")
            print(f"  {marker} {item['ticker']:5s} [{sev:6s}] {item['event']}")
            rec = item.get("recommendation", "")
            src = item.get("headline_source", "")
            if rec:
                print(f"         → {rec}")
            if src:
                print(f"         Source: {src[:100]}")
    if all_clear:
        print(f"\nALL CLEAR: {', '.join(all_clear)}")
    if not flagged and not all_clear:
        print("  No results.")


if __name__ == "__main__":
    import sys
    tickers_arg = sys.argv[1].split(",") if len(sys.argv) > 1 else ["VCB", "FPT", "HPG"]
    tickers_arg = [t.strip().upper() for t in tickers_arg]
    result = monitor(tickers_arg)
    print_monitor_report(result)
