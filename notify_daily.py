#!/usr/bin/env python3
"""
Daily scanner: real VN30 data → multi-day signal analysis → market context
              → Claude deep analysis → email + Telegram.

Multi-day analysis checks signals from today, yesterday, 3 days ago, and 5 days ago
using cached OHLCV data (no extra network calls). Only stocks with consistent signals
across multiple days surface as top picks — one-day blips are filtered out.

Cron: 30 8 * * 1-5  /Users/tuan/Projects/analyst-stock-vn/run_daily.sh
"""
import json
import os
import sys
import warnings
from datetime import date
from pathlib import Path

warnings.filterwarnings("ignore")

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))

import yaml

from src.fetcher.yfinance_client import get_data_freshness, get_ohlcv_bulk
from src.indicators.technical import entry_exit_levels
from src.market_context import build as build_market_context
from src.position_sizer import size_ranked
from src.signals.multiday import build_multiday_scores, rank_by_conviction
from src.verifier import verify
from src.report import email_notifier
from src.report.telegram import format_ranked, send as telegram_send
from src.trade_log.logger import append_signals


def _hr(title: str = "") -> None:
    print(f"\n{'─'*60}")
    if title:
        print(f"  {title}")
        print(f"{'─'*60}")


def _conviction_bar(label: str) -> str:
    return {"HIGH": "███", "MEDIUM": "██░", "LOW": "█░░"}.get(label, "░░░")


def run(timeframe: str = "daily") -> None:
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    tickers = cfg["watchlist"]["vn30"]
    top_n = cfg["output"]["top_n"]
    md_cfg = cfg.get("multiday", {})
    offsets = md_cfg.get("lookback_offsets", [0, 1, 3, 5])
    min_streak = md_cfg.get("min_streak", 2)
    max_workers = cfg.get("parallelism", {}).get("max_workers", 8)

    _hr(f"VN Stock Scanner — {date.today()} — {timeframe.upper()}")
    print(f"Fetching real market data for {len(tickers)} VN30 tickers...")

    # ── 1. Fetch ──────────────────────────────────────────────────────────────
    ohlcv_map = get_ohlcv_bulk(tickers, days=400)
    print(f"  Got data: {len(ohlcv_map)}/{len(tickers)} tickers")
    if len(ohlcv_map) < len(tickers) * 0.7:
        print("ERROR: <70% tickers returned data. Aborting.")
        sys.exit(1)

    # ── 2. Market context ─────────────────────────────────────────────────────
    print("Building market context (VN-Index, sectors, breadth)...")
    market_ctx = build_market_context(ohlcv_map, sector_map=cfg.get("sectors"))
    vni = market_ctx.get("vnindex", {})
    breadth = market_ctx.get("breadth", {})
    if vni.get("price"):
        print(f"  VN-Index: {vni['price']:,.1f}  trend={vni['trend']}  "
              f"1M={vni['m1_return_pct']:+.1f}%  3M={vni['m3_return_pct']:+.1f}%")
    if breadth:
        print(f"  Breadth: {breadth['breadth']}  "
              f"above MA20={breadth['pct_above_ma20']}%  "
              f"advancing={breadth['pct_advancing']}%")

    # ── 3. Multi-day signal analysis ──────────────────────────────────────────
    print(f"Running multi-day analysis (offsets={offsets}, min_streak={min_streak})...")
    analyses = build_multiday_scores(
        ohlcv_map, cfg, max_workers=max_workers
    )
    print(f"  Analyzed {len(analyses)} tickers across {len(offsets)} time points each")

    # Also collect entry/exit levels and freshness per ticker
    entry_levels = {}
    freshness_map = {}
    for ticker, df in ohlcv_map.items():
        try:
            entry_levels[ticker] = entry_exit_levels(df)
            freshness_map[ticker] = get_data_freshness(df)
        except Exception:
            pass

    ranked = rank_by_conviction(analyses, top_n=top_n, min_streak=min_streak)

    # ── 4. Position sizing ────────────────────────────────────────────────────
    port = cfg.get("portfolio", {})
    capital = port.get("capital", 100_000_000)
    risk_pct = port.get("risk_per_trade_pct", 0.02)
    max_pos = port.get("max_position_pct", 0.15)
    max_total_risk = port.get("max_total_risk_pct", 0.06)
    ranked = size_ranked(ranked, entry_levels, capital, risk_pct, max_pos, max_total_risk)
    market_ctx["capital"] = capital
    n_afford = len(ranked.get("buy", []))
    n_skip = len(ranked.get("buy_unaffordable", []))
    print(f"Position sizing: capital={capital:,.0f} ₫  "
          f"risk/trade={risk_pct*100:.0f}%  max/pos={max_pos*100:.0f}%  "
          f"affordable={n_afford}  skipped={n_skip}")

    # ── 5. Print multi-day signals ────────────────────────────────────────────
    _hr("Multi-Day Signal Analysis")
    for action in ("buy", "sell"):
        items = ranked.get(action, [])
        if items:
            print(f"\n  {action.upper()} — ranked by conviction (streak ≥ {min_streak} days):")
            print(f"  {'Ticker':<6} {'Price':>10}  {'Conv':>6}  {'Label':>6}  "
                  f"{'Streak':>6}  {'Consist':>7}  {'Trend':>7}")
            print(f"  {'─'*62}")
            for item in items[:5]:
                el = entry_levels.get(item["ticker"], {})
                sz = item.get("sizing", {})
                bar = _conviction_bar(item.get("conviction_label", "LOW"))
                trend_arrow = "↑" if item.get("confidence_trend", 0) > 0 else "↓"
                print(f"  {item['ticker']:<6} {item['price']:>10,.0f} ₫  "
                      f"{item['conviction_score']:>+6.3f}  "
                      f"{bar} {item.get('conviction_label','?'):>6}  "
                      f"{item.get('streak_days',0):>4}d  "
                      f"{item.get('consistency_score',0)*100:>5.0f}%  "
                      f"{trend_arrow} {abs(item.get('confidence_trend',0)):.3f}")
                if el:
                    print(f"         entry={el['entry']:,.0f}  "
                          f"stop={el['stop_loss']:,.0f} (-{el['risk_pct']}%)  "
                          f"target={el['target']:,.0f} (+{el['reward_pct']}%)")
                if sz and sz.get("qty"):
                    print(f"         qty={sz['qty']:,} shares  "
                          f"cost={sz['total_cost']:,.0f} ₫  "
                          f"max_loss={sz['risk_vnd']:,.0f} ₫ ({sz['risk_pct']}%)")
                # Show snapshot history
                snaps = item.get("snapshots", [])
                if snaps:
                    snap_summary = "  ".join(
                        f"{s['date'][5:]}:{s['action'][0]}({s['composite']:+.2f})"
                        for s in snaps
                    )
                    print(f"         history: {snap_summary}")

    # One-day-only signals (streak < min_streak) shown as WATCH
    one_day_buys = [a for a in analyses
                    if a.conviction_score > 0 and a.streak_days < min_streak]
    one_day_sells = [a for a in analyses
                     if a.conviction_score < 0 and a.streak_days < min_streak]
    if one_day_buys or one_day_sells:
        print(f"\n  WATCH (new signal today, no prior confirmation):")
        for a in sorted(one_day_buys, key=lambda x: x.conviction_score, reverse=True)[:3]:
            print(f"    {a.ticker:<6} BUY  conviction={a.conviction_score:+.3f}  "
                  f"(first day — monitor tomorrow)")
        for a in sorted(one_day_sells, key=lambda x: x.conviction_score)[:3]:
            print(f"    {a.ticker:<6} SELL conviction={a.conviction_score:+.3f}  "
                  f"(first day — monitor tomorrow)")

    skipped = ranked.get("buy_unaffordable", [])
    if skipped:
        print(f"\n  BUY (need more capital — 1 lot > {capital:,.0f} ₫):")
        for item in skipped:
            sz = item.get("sizing", {})
            print(f"    {item['ticker']:5s}  {item['price']:>10,.0f} ₫  "
                  f"1 lot={item['price']*100:,.0f} ₫  — {sz.get('sizing_note','')[:60]}")

    # ── 6. Verify + Claude deep analysis ──────────────────────────────────────
    _hr("Verification & Claude Analysis")
    print("  Running checks and Claude deep analysis...")

    # Attach conviction context to market_ctx so Claude can use it
    market_ctx["multiday_context"] = {
        "offsets_analyzed": offsets,
        "min_streak_required": min_streak,
        "high_conviction_buys": [
            {"ticker": a.ticker, "streak": a.streak_days,
             "consistency": a.consistency_score, "trend": a.confidence_trend}
            for a in sorted(analyses, key=lambda x: x.conviction_score, reverse=True)[:5]
            if a.conviction_score > 0
        ],
        "high_conviction_sells": [
            {"ticker": a.ticker, "streak": a.streak_days,
             "consistency": a.consistency_score, "trend": a.confidence_trend}
            for a in sorted(analyses, key=lambda x: x.conviction_score)[:5]
            if a.conviction_score < 0
        ],
    }

    result = verify(ranked, timeframe, freshness_map, market_ctx, entry_levels, cfg=cfg)

    if result.issues:
        print(f"\n  FAILED — {len(result.issues)} issue(s):")
        for issue in result.issues:
            print(f"    ✗ {issue}")
        print("\n  Email NOT sent.")
        sys.exit(1)

    review = result.claude_review
    final = result.adjusted_ranked

    n_logged = append_signals(final, entry_levels, date.today().isoformat())
    if n_logged:
        print(f"  ✓ Logged {n_logged} signals to data/signals.jsonl")

    if isinstance(review, dict):
        print(f"\n  Market: {review.get('market_summary','')}")
        print(f"  Sectors: {review.get('sectors_to_watch','')}")
        print(f"  Confidence: {review.get('confidence','')}")
        if review.get("flagged_tickers"):
            print(f"  Flagged: {review['flagged_tickers']}")
        print(f"\n  Top Picks:")
        for p in review.get("top_picks", []):
            print(f"    {p['action']:4s} {p['ticker']:5s} [{p['conviction']:6s}] "
                  f"entry={p.get('entry',0):,.0f}  stop={p.get('stop_loss',0):,.0f}  "
                  f"target={p.get('target',0):,.0f}")
            print(f"          {p.get('thesis','')}")
    elif review is None:
        print("  Claude analysis skipped (no API credits — signals sent without AI commentary)")

    # ── 7. Send ───────────────────────────────────────────────────────────────
    _hr("Sending Notifications")

    if os.environ.get("GMAIL_APP_PASSWORD"):
        try:
            email_notifier.send(final, timeframe, review, market_ctx)
            print(f"  ✓ Email → {os.environ.get('NOTIFY_EMAIL')}")
        except Exception as e:
            print(f"  ✗ Email failed: {e}")
    else:
        print("  [skip] Email: set GMAIL_APP_PASSWORD in .env")

    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        try:
            summary = review.get("market_summary", str(review)) if isinstance(review, dict) else str(review)
            telegram_send(format_ranked(final, timeframe, summary))
            print("  ✓ Telegram sent")
        except Exception as e:
            print(f"  ✗ Telegram failed: {e}")

    print(f"\nDone. {date.today()}")


if __name__ == "__main__":
    tf = sys.argv[1] if len(sys.argv) > 1 else "daily"
    run(tf)
