"""
Claude-powered market scanner agent.
Uses multi-day analysis to surface only signals that have been
consistent across multiple recent days — not just today's snapshot.
"""
import json
import os
from datetime import date, timedelta

import anthropic
import yaml

from src.fetcher.yfinance_client import get_ohlcv_bulk
from src.signals.multiday import build_multiday_scores, rank_by_conviction


def scan(timeframe: str = "daily") -> None:
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    tickers = cfg["watchlist"]["vn30"]
    top_n = cfg["output"]["top_n"]
    md_cfg = cfg.get("multiday", {})
    min_streak = md_cfg.get("min_streak", 2)
    max_workers = cfg.get("parallelism", {}).get("max_workers", 8)

    print(f"Fetching data for {len(tickers)} tickers...")
    ohlcv_map = get_ohlcv_bulk(tickers, days=400)

    print(f"Running multi-day signal analysis (min_streak={min_streak})...")
    analyses = build_multiday_scores(ohlcv_map, cfg, max_workers=max_workers)
    ranked = rank_by_conviction(analyses, top_n=top_n, min_streak=min_streak)

    # Claude narrates the results
    claude_cfg = cfg.get("claude", {})
    client = anthropic.Anthropic()
    prompt = f"""You are a Vietnamese stock market analyst.
Based on the multi-day signal scan below, write a concise market commentary (in English)
explaining the top buy and sell signals for {timeframe} trading.

Key: these signals have persisted for {min_streak}+ consecutive trading days.
conviction_label HIGH = signal strengthening, LOW = weakening.
streak_days = how many consecutive days the same direction was signalled.
consistency_score = fraction of lookback days with same direction (1.0 = all days).

Highlight which signals show the most conviction (streak + consistency), any sector
patterns, and caution flags. Under 300 words.

Scan results:
{json.dumps(ranked, indent=2, ensure_ascii=False)}
"""
    message = client.messages.create(
        model=claude_cfg.get("model", "claude-sonnet-4-6"),
        max_tokens=claude_cfg.get("max_tokens_scan", 600),
        messages=[{"role": "user", "content": prompt}],
    )
    commentary = message.content[0].text

    print("\n--- AI Market Commentary ---")
    print(commentary)
    print("\n--- Ranked Signals (by conviction) ---")

    from src.report.formatter import print_table
    print_table(ranked, timeframe)


if __name__ == "__main__":
    import sys
    tf = sys.argv[1] if len(sys.argv) > 1 else "daily"
    scan(tf)
