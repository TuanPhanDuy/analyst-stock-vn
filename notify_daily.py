#!/usr/bin/env python3
"""
Daily scanner: real VN30 data → multi-day signal analysis → market context
              → regime detection → sector rotation → VN30F futures
              → foreign flow → earnings flags → insider signals
              → price alerts → news scrape → chart patterns → intraday signals
              → risk report → ML calibration → signal dedup → Claude analysis
              → email + Telegram → NAV record.

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
from src.market_context import build as build_market_context, _synthetic_index
from src.portfolio_monitor import analyze_positions, load_portfolio
from src.position_sizer import size_ranked
from src.signals.multiday import build_multiday_scores, rank_by_conviction
from src.verifier import verify
from src.report import email_notifier
from src.report.telegram import format_scan, send as telegram_send
from src.trade_log.logger import append_signals


def _hr(title: str = "") -> None:
    print(f"\n{'─'*60}")
    if title:
        print(f"  {title}")
        print(f"{'─'*60}")


def _conviction_bar(label: str) -> str:
    return {"HIGH": "███", "MEDIUM": "██░", "LOW": "█░░"}.get(label, "░░░")


def run(timeframe: str = "daily") -> None:
    with open(Path(__file__).parent / "config.yaml") as f:
        cfg = yaml.safe_load(f)

    tickers = cfg["watchlist"]["vn30"]
    top_n = cfg["output"]["top_n"]
    md_cfg = cfg.get("multiday", {})
    offsets = md_cfg.get("lookback_offsets", [0, 1, 3, 5])
    min_streak = md_cfg.get("min_streak", 2)
    max_workers = cfg.get("parallelism", {}).get("max_workers", 8)

    _hr(f"VN Stock Scanner — {date.today()} — {timeframe.upper()}")
    print(f"Fetching real market data for {len(tickers)} VN30 tickers...")

    # ── 1. Fetch OHLCV ────────────────────────────────────────────────────────
    ohlcv_map = get_ohlcv_bulk(tickers, days=400)
    print(f"  Got data: {len(ohlcv_map)}/{len(tickers)} tickers")
    if len(ohlcv_map) < len(tickers) * 0.7:
        print("ERROR: <70% tickers returned data. Aborting.")
        sys.exit(1)

    # ── 1b. Resolve past signal outcomes ──────────────────────────────────────
    # Replays each PENDING signal against the fresh OHLCV window to mark
    # HIT_TARGET / HIT_STOP / EXPIRED. Without this the trade log never closes a
    # trade, so the ML calibrator and accuracy report stay permanently empty.
    try:
        from src.ml_signal import reload as ml_reload
        from src.trade_log.logger import update_pending_outcomes
        hold_days = cfg.get("trade_log", {}).get("hold_days", 5)
        n_resolved = update_pending_outcomes(ohlcv_map, hold_days=hold_days)
        if n_resolved:
            print(f"  Resolved {n_resolved} past signal outcome(s) → data/signals.jsonl")
            ml_reload()   # retrain/re-validate calibrator on the newly closed trades
    except Exception as e:
        print(f"  [warn] Outcome resolution failed: {e}")

    # ── 2. Market regime ──────────────────────────────────────────────────────
    print("Detecting market regime...")
    regime_result = {"regime": "UNKNOWN", "confidence": 0.0}
    try:
        from src import regime as regime_mod
        idx_df = _synthetic_index(ohlcv_map)
        regime_result = regime_mod.detect(idx_df, cfg.get("regime", {}))
        r = regime_result["regime"]
        conf = regime_result["confidence"]
        r1m = regime_result.get("r1m_pct", 0)
        r3m = regime_result.get("r3m_pct", 0)
        adx_val = regime_result.get("adx", 0)
        print(f"  Regime: {r} ({conf:.0%} conf)  1M={r1m:+.1f}%  3M={r3m:+.1f}%  ADX={adx_val:.0f}")
    except Exception as e:
        print(f"  [warn] Regime detection failed: {e}")

    # ── 3. Market context (breadth, sectors) ──────────────────────────────────
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

    # ── 4. Sector rotation ────────────────────────────────────────────────────
    print("Analyzing sector rotation...")
    sector_rotation_result = {}
    try:
        from src import sector_rotation as sr_mod
        sector_rotation_result = sr_mod.analyze(ohlcv_map, cfg.get("sectors", {}), cfg)
        print(f"  {sector_rotation_result.get('summary', 'No rotation signal')}")
    except Exception as e:
        print(f"  [warn] Sector rotation failed: {e}")

    # ── 5. Multi-day signal analysis ──────────────────────────────────────────
    print(f"Running multi-day analysis (offsets={offsets}, min_streak={min_streak})...")
    analyses = build_multiday_scores(ohlcv_map, cfg, max_workers=max_workers)
    print(f"  Analyzed {len(analyses)} tickers across {len(offsets)} time points each")

    entry_levels = {}
    freshness_map = {}
    for ticker, df in ohlcv_map.items():
        try:
            entry_levels[ticker] = entry_exit_levels(df)
            freshness_map[ticker] = get_data_freshness(df)
        except Exception:
            pass

    # Ranking is deferred until after foreign flow + insider signals are fetched
    # (§7, §9) so their score_delta and the regime multiplier can be folded into
    # each ticker's conviction_score before we rank. See §9c.

    # ── 6. Market rules signals (ceiling/floor) ───────────────────────────────
    ceiling_floor_alerts = {}
    try:
        from src.market_rules import market_rules_signal
        for ticker, df in ohlcv_map.items():
            sig = market_rules_signal(df)
            if sig["score_delta"] != 0:
                ceiling_floor_alerts[ticker] = sig
        if ceiling_floor_alerts:
            print(f"  VN Market Rules: {len(ceiling_floor_alerts)} ceiling/floor signals")
            for t, sig in list(ceiling_floor_alerts.items())[:3]:
                print(f"    {t}: {sig['reason']}")
    except Exception as e:
        print(f"  [warn] Market rules signal failed: {e}")

    # ── 7. Foreign investor flow ──────────────────────────────────────────────
    foreign_flows = {}
    if cfg.get("foreign_flow", {}).get("enabled", True):
        print("Fetching foreign investor flow...")
        try:
            from src.fetcher.foreign_flow import get_foreign_flow_bulk, foreign_signal
            ff_cfg = cfg.get("foreign_flow", {})
            raw_flows = get_foreign_flow_bulk(
                tickers,
                days=ff_cfg.get("lookback_days", 60),
                ttl=ff_cfg.get("cache_ttl", 3600),
            )
            strong_thr = ff_cfg.get("strong_buying_threshold", 100_000)
            foreign_flows = {t: foreign_signal(f, strong_threshold=strong_thr)
                             for t, f in raw_flows.items()}
            buying = [t for t, s in foreign_flows.items() if s["direction"] > 0]
            selling = [t for t, s in foreign_flows.items() if s["direction"] < 0]
            print(f"  Foreign: {len(buying)} buying, {len(selling)} selling")
            strong_buy = [t for t, s in foreign_flows.items() if "STRONG" in s["label"] and s["direction"] > 0]
            if strong_buy:
                print(f"    Strong foreign buying: {', '.join(strong_buy[:5])}")
        except Exception as e:
            print(f"  [warn] Foreign flow fetch failed: {e}")

    # ── 8. Earnings / event flags ─────────────────────────────────────────────
    earnings_flags = {}
    warn_days = cfg.get("earnings", {}).get("warn_days_ahead", 7)
    print(f"Checking earnings/event calendar ({warn_days}-day window)...")
    try:
        from src.earnings import bulk_flags
        earnings_flags = bulk_flags(tickers, warn_days=warn_days)
        flagged = [t for t, f in earnings_flags.items() if f["flag"]]
        if flagged:
            print(f"  Events in {warn_days}d: {', '.join(flagged)}")
        else:
            print(f"  No corporate events in {warn_days}d")
    except Exception as e:
        print(f"  [warn] Earnings calendar failed: {e}")

    # ── 9. Insider transactions ───────────────────────────────────────────────
    insider_signals = {}
    if cfg.get("insider", {}).get("enabled", True):
        print("Checking insider transactions...")
        try:
            from src.fetcher.insider import bulk_signals as insider_bulk
            ins_cfg = cfg.get("insider", {})
            insider_signals = insider_bulk(
                tickers,
                days=ins_cfg.get("lookback_days", 90),
                ttl=ins_cfg.get("cache_ttl", 21600),
            )
            buying = [t for t, s in insider_signals.items() if s["direction"] > 0]
            selling = [t for t, s in insider_signals.items() if s["direction"] < 0]
            if buying:
                print(f"  Insider buying: {', '.join(buying[:5])}")
            if selling:
                print(f"  Insider selling: {', '.join(selling[:5])}")
        except Exception as e:
            print(f"  [warn] Insider tracker failed: {e}")

    # ── 9c. Fold regime + foreign flow + insider into conviction, then rank ───
    # These three signals were previously display-only. apply_to_analyses folds
    # them into each ticker's conviction_score (regime multiplies, foreign/insider
    # add) so the ranking and downstream sizing reflect them.
    try:
        from src.scoring.adjustments import apply_to_analyses
        # Supply the calibrator only when ML is enabled; it self-gates on OOS
        # validation, so this is a no-op until enough closed trades prove it helps.
        ml_calibrate = None
        if cfg.get("ml_signal", {}).get("enabled", True):
            from src.ml_signal import calibrate_confidence as ml_calibrate
        apply_to_analyses(analyses, foreign_flows, insider_signals, regime_result,
                          cfg, ml_calibrate=ml_calibrate)
        import math
        n_adj = sum(
            1 for a in analyses
            if not math.isclose(a.regime_mult, 1.0)
            or not math.isclose(a.ml_factor, 1.0)
            or a.foreign_delta or a.insider_delta
        )
        print(f"  Conviction adjusted by regime/foreign/insider/ML on {n_adj} tickers")
    except Exception as e:
        print(f"  [warn] Conviction adjustment failed: {e}")
    ranked = rank_by_conviction(analyses, top_n=top_n, min_streak=min_streak)

    # ── 9b. VN30F futures snapshot (pre-market leading indicator) ─────────────
    futures_snapshot = {}
    try:
        from src.fetcher.futures import get_futures_snapshot, futures_signal_summary
        futures_snapshot = get_futures_snapshot(ttl=300)
        print(f"  {futures_signal_summary(futures_snapshot)}")
    except Exception as e:
        print(f"  [warn] Futures snapshot failed: {e}")

    # ── 10. Portfolio monitoring ───────────────────────────────────────────────
    portfolio = load_portfolio()
    portfolio_statuses = []
    if portfolio:
        portfolio_statuses = analyze_positions(portfolio, ohlcv_map, entry_levels)
        _hr("My Portfolio")
        for s in portfolio_statuses:
            p = s.position
            flag = " ← ACTION REQUIRED" if s.needs_action else ""
            print(f"  {p.ticker:<6} buy={p.buy_price:,.0f}  now={s.current_price:,.0f}  "
                  f"P&L={s.pnl_pct:+.2f}%  status={s.status}{flag}")
            print(f"         {s.message}")

    # ── 10b. Price alerts ─────────────────────────────────────────────────────
    triggered_alerts = []
    try:
        from src.alerts import check_alerts, format_triggered
        current_prices = {t: float(df["close"].iloc[-1]) for t, df in ohlcv_map.items()}
        triggered_alerts = check_alerts(current_prices)
        if triggered_alerts:
            print(f"\n  {format_triggered(triggered_alerts)}")
    except Exception as e:
        print(f"  [warn] Price alerts check failed: {e}")

    # ── 11. Position sizing ───────────────────────────────────────────────────
    port = cfg.get("portfolio", {})
    capital = port.get("capital", 100_000_000)
    risk_pct = port.get("risk_per_trade_pct", 0.02)
    max_pos = port.get("max_position_pct", 0.15)
    max_total_risk = port.get("max_total_risk_pct", 0.06)
    ranked = size_ranked(ranked, entry_levels, capital, risk_pct, max_pos, max_total_risk)
    market_ctx["capital"] = capital

    # ── 11b. News scrape for top signal context ───────────────────────────────
    top_tickers_for_news = [
        item["ticker"] for item in (ranked.get("buy", [])[:5] + ranked.get("sell", [])[:3])
    ]
    news_map: dict = {}
    if top_tickers_for_news:
        print(f"Scraping news for: {', '.join(top_tickers_for_news)}...")
        try:
            from src.fetcher.news_scraper import fetch_bulk
            news_map = fetch_bulk(top_tickers_for_news, days=7)
            total_news = sum(len(v) for v in news_map.values())
            print(f"  Got {total_news} headlines across {len(news_map)} tickers")
        except Exception as e:
            print(f"  [warn] News scrape failed: {e}")

    # ── 11c. Chart pattern enrichment for top buy signals ─────────────────────
    pattern_enrichments: dict = {}
    try:
        from src.signals.patterns import pattern_score_delta
        from src.signals.intraday import intraday_score_delta
        for item in ranked.get("buy", [])[:8] + ranked.get("sell", [])[:4]:
            t = item["ticker"]
            df = ohlcv_map.get(t)
            if df is None:
                continue
            pat_delta, pat_reasons = pattern_score_delta(df)
            int_delta, int_reasons = intraday_score_delta(df)
            pattern_enrichments[t] = {
                "pattern_delta": pat_delta,
                "intraday_delta": int_delta,
                "reasons": pat_reasons + int_reasons,
            }
        if any(v["pattern_delta"] != 0 for v in pattern_enrichments.values()):
            bullish_patterns = [t for t, v in pattern_enrichments.items() if v["pattern_delta"] > 0.05]
            bearish_patterns = [t for t, v in pattern_enrichments.items() if v["pattern_delta"] < -0.05]
            if bullish_patterns:
                print(f"  Bullish patterns: {', '.join(bullish_patterns)}")
            if bearish_patterns:
                print(f"  Bearish patterns: {', '.join(bearish_patterns)}")
    except Exception as e:
        print(f"  [warn] Pattern enrichment failed: {e}")

    # ── 12. ML signal calibration ─────────────────────────────────────────────
    ml_cfg = cfg.get("ml_signal", {})
    if ml_cfg.get("enabled", True):
        try:
            from src.ml_signal import calibrate_confidence, accuracy_report
            ml_blend = ml_cfg.get("confidence_blend", 0.50)
            min_samples = ml_cfg.get("min_samples", 20)
            acc = accuracy_report()
            if acc.get("total_closed", 0) >= min_samples:
                print(f"ML calibrator active ({acc['total_closed']} closed trades, "
                      f"win rate={acc.get('overall_win_rate_pct', 0):.1f}%)")
            else:
                n_closed = acc.get("total_closed", 0)
                print(f"ML calibrator: accumulating data ({n_closed}/{min_samples} trades needed)")
        except Exception as e:
            print(f"  [warn] ML calibrator failed: {e}")

    # ── 13. Print enriched signals ────────────────────────────────────────────
    _hr("Multi-Day Signal Analysis")
    for action in ("buy", "sell"):
        items = ranked.get(action, [])
        if items:
            print(f"\n  {action.upper()} — ranked by conviction (streak ≥ {min_streak} days):")
            print(f"  {'Ticker':<6} {'Price':>10}  {'Conv':>6}  {'Label':>6}  "
                  f"{'Streak':>6}  {'Consist':>7}  {'Trend':>7}  {'FF':>8}  {'Ins':>6}")
            print(f"  {'─'*78}")
            for item in items[:5]:
                el = entry_levels.get(item["ticker"], {})
                sz = item.get("sizing", {})
                bar = _conviction_bar(item.get("conviction_label", "LOW"))
                trend_arrow = "↑" if item.get("confidence_trend", 0) > 0 else "↓"
                t = item["ticker"]
                ff = foreign_flows.get(t, {})
                ins = insider_signals.get(t, {})
                ff_label = ff.get("label", "")[:8] if ff else ""
                ins_label = "BUY" if (ins.get("direction", 0) > 0) else ("SELL" if (ins.get("direction", 0) < 0) else "")

                print(f"  {t:<6} {item['price']:>10,.0f} ₫  "
                      f"{item['conviction_score']:>+6.3f}  "
                      f"{bar} {item.get('conviction_label','?'):>6}  "
                      f"{item.get('streak_days',0):>4}d  "
                      f"{item.get('consistency_score',0)*100:>5.0f}%  "
                      f"{trend_arrow} {abs(item.get('confidence_trend',0)):.3f}  "
                      f"{ff_label:>8}  {ins_label:>6}")

                if el:
                    print(f"         entry={el['entry']:,.0f}  "
                          f"stop={el['stop_loss']:,.0f} (-{el['risk_pct']}%)  "
                          f"target={el['target']:,.0f} (+{el['reward_pct']}%)")
                if sz and sz.get("qty"):
                    print(f"         qty={sz['qty']:,} shares  "
                          f"cost={sz['total_cost']:,.0f} ₫  "
                          f"max_loss={sz['risk_vnd']:,.0f} ₫ ({sz['risk_pct']}%)")

                # Earnings flag warning
                ef = earnings_flags.get(t, {})
                if ef.get("flag"):
                    print(f"         ⚠ EARNINGS: {ef['message']}")

                # Market rules signal
                mr = ceiling_floor_alerts.get(t)
                if mr:
                    print(f"         VN-RULES: {mr['reason']}")

                snaps = item.get("snapshots", [])
                if snaps:
                    snap_summary = "  ".join(
                        f"{s['date'][5:]}:{s['action'][0]}({s['composite']:+.2f})"
                        for s in snaps
                    )
                    print(f"         history: {snap_summary}")

    # One-day-only signals
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

    # ── 14. Portfolio optimizer ───────────────────────────────────────────────
    if ranked.get("buy"):
        try:
            from src.portfolio_optimizer import optimize
            opt = optimize(ohlcv_map, ranked["buy"], cfg)
            if opt.get("high_correlation_pairs"):
                _hr("Portfolio Optimizer")
                for pair in opt["high_correlation_pairs"][:3]:
                    print(f"  ⚠ {pair['note']}")
            if opt.get("recommendations"):
                print("  Suggested weights (min-variance):")
                for rec in opt["recommendations"][:5]:
                    kelly_pct = rec["kelly_risk_pct"]
                    print(f"    {rec['ticker']:<6} MV={rec['mv_weight_pct']:.0f}%  "
                          f"Kelly risk={kelly_pct:.1f}%"
                          + (f"  ← {rec['note']}" if rec.get("note") else ""))
        except Exception as e:
            print(f"  [warn] Portfolio optimizer failed: {e}")

    # ── 15. Sector rotation summary ───────────────────────────────────────────
    if sector_rotation_result.get("actionable"):
        _hr("Sector Rotation")
        rankings = sector_rotation_result.get("rankings", [])
        print(f"  {sector_rotation_result['summary']}")
        for e in rankings[:5]:
            sig = e.get("rotation_signal", "HOLD")
            arrow = {"ROTATE_IN": "▲", "ROTATE_OUT": "▼", "HOLD": "─"}.get(sig, "─")
            print(f"    {arrow} {e['sector']:<14} {e['avg_return_1m_pct']:>+5.1f}% 1M  "
                  f"score={e['score']:>+5.1f}  {e['label']}")

    # ── 16. Verify + Claude deep analysis ─────────────────────────────────────
    _hr("Verification & Claude Analysis")
    print("  Running checks and Claude deep analysis...")

    market_ctx["regime"] = regime_result
    market_ctx["sector_rotation"] = sector_rotation_result
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

    if result.warnings:
        for w in result.warnings:
            print(f"  ⚠ {w}")

    review = result.claude_review
    final = result.adjusted_ranked

    try:
        n_logged = append_signals(final, entry_levels, date.today().isoformat())
        if n_logged:
            print(f"  ✓ Logged {n_logged} signals to data/signals.jsonl")
    except Exception as e:
        print(f"  [warn] Trade log failed (non-fatal): {e}")

    if isinstance(review, dict):
        print(f"\n  Market: {review.get('market_summary','')}")
        print(f"  Regime note: {regime_result.get('description', '')}")
        print(f"  Sectors: {review.get('sectors_to_watch','')}")
        print(f"  Confidence: {review.get('confidence','')}")
        if review.get("flagged_tickers"):
            print(f"  Flagged: {review['flagged_tickers']}")
        print(f"\n  Top Picks:")
        for p in review.get("top_picks", []):
            print(f"    {p.get('action','?'):4s} {p.get('ticker','?'):5s} [{p.get('conviction','?'):6s}] "
                  f"entry={p.get('entry',0):,.0f}  stop={p.get('stop_loss',0):,.0f}  "
                  f"target={p.get('target',0):,.0f}")
            print(f"          {p.get('thesis','')}")
    elif review is None:
        print("  Claude analysis skipped (no API credits — signals sent without AI commentary)")

    # ── 16b. Risk report ──────────────────────────────────────────────────────
    portfolio_risk_report: dict = {}
    portfolio_weights: dict = {}
    if portfolio:
        try:
            from src.risk_manager import risk_report as compute_risk
            total_val = sum(
                p.qty * float(ohlcv_map[p.ticker]["close"].iloc[-1])
                for p in portfolio
                if p.ticker in ohlcv_map and getattr(p, "qty", 0)
            ) or capital
            portfolio_weights = {
                p.ticker: (p.qty * float(ohlcv_map[p.ticker]["close"].iloc[-1])) / total_val
                for p in portfolio
                if p.ticker in ohlcv_map and getattr(p, "qty", 0)
            }
            if portfolio_weights:
                portfolio_risk_report = compute_risk(ohlcv_map, portfolio_weights, nav=total_val)
                var_pct = portfolio_risk_report.get("portfolio_var_95", {}).get("var_pct")
                beta_val = portfolio_risk_report.get("portfolio_beta", {}).get("portfolio_beta")
                if var_pct:
                    print(f"  Portfolio risk — VaR 95%: {var_pct:.2f}%  Beta: {beta_val:.2f}")
                for flag in portfolio_risk_report.get("flags", []):
                    print(f"  ⚠ RISK: {flag}")
        except Exception as e:
            print(f"  [warn] Risk report failed: {e}")

    # ── 16c. Signal deduplication ─────────────────────────────────────────────
    signals_before_dedup = len(final.get("buy", [])) + len(final.get("sell", []))
    try:
        from src.dedup import filter_new
        all_signals_flat = [
            {"ticker": item["ticker"], "action": "BUY", "timeframe": timeframe,
             "confidence": item.get("conviction_score", 0)}
            for item in final.get("buy", [])
        ] + [
            {"ticker": item["ticker"], "action": "SELL", "timeframe": timeframe,
             "confidence": item.get("conviction_score", 0)}
            for item in final.get("sell", [])
        ]
        new_signals = filter_new(all_signals_flat)
        new_tickers = {s["ticker"] for s in new_signals}
        signals_suppressed = signals_before_dedup - len(new_signals)
        if signals_suppressed > 0:
            print(f"  Signal dedup: {signals_suppressed} unchanged signal(s) suppressed "
                  f"(use --no-dedup to override)")
    except Exception as e:
        print(f"  [warn] Signal dedup failed: {e}")
        new_tickers = set()

    # ── 16d. Institutional trading desk brief ─────────────────────────────────
    institutional_brief: dict = {}
    _hr("Institutional Trading Desk — Running Agent Team")
    try:
        from agents.trading_desk import run_session as desk_session
        institutional_brief = desk_session(
            regime_result=regime_result,
            vnindex=market_ctx.get("vnindex", {}),
            ranked=final,
            portfolio_statuses=portfolio_statuses,
            risk_report=portfolio_risk_report,
            sector_rotation=sector_rotation_result,
            foreign_flows=foreign_flows,
            insider_signals=insider_signals,
            earnings_flags=earnings_flags,
            futures_snapshot=futures_snapshot,
            pattern_enrichments=pattern_enrichments,
            capital=capital,
            cfg=cfg,
        )
        print(f"  HEAD OF TRADING: {institutional_brief.get('head_directive', '?')}")
        for d in institutional_brief.get("decisions", [])[:5]:
            print(f"    {d['action']:5s} {d['ticker']:6s} [{d['conviction']:6s}] "
                  f"entry={d.get('entry',0):,.0f}  stop={d.get('stop',0):,.0f}  "
                  f"target={d.get('target',0):,.0f}  — {d['rationale']}")
        for alert in institutional_brief.get("risk_alerts", []):
            print(f"  ⚠ RISK ALERT: {alert}")
    except Exception as e:
        print(f"  [warn] Trading desk brief failed: {e}")

    # ── 17. Send notifications ────────────────────────────────────────────────
    _hr("Sending Notifications")

    if os.environ.get("GMAIL_APP_PASSWORD"):
        try:
            email_notifier.send(
                final, timeframe, review, market_ctx,
                portfolio_statuses=portfolio_statuses,
                regime_result=regime_result,
                sector_rotation=sector_rotation_result,
                risk_report=portfolio_risk_report,
                institutional_brief=institutional_brief,
            )
            print(f"  ✓ Email → {os.environ.get('NOTIFY_EMAIL')}")
        except Exception as e:
            print(f"  ✗ Email failed: {e}")
            sys.exit(1)
    else:
        print("  [skip] Email: set GMAIL_APP_PASSWORD in .env")

    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        try:
            summary = review.get("market_summary", str(review)) if isinstance(review, dict) else str(review)
            msg = format_scan(
                final, timeframe,
                commentary=summary,
                regime=regime_result,
                sector_rotation=sector_rotation_result,
                portfolio_statuses=portfolio_statuses,
            )
            telegram_send(msg)
            print("  ✓ Telegram sent")
        except Exception as e:
            print(f"  ✗ Telegram failed: {e}")

    # ── 18. Record portfolio NAV ──────────────────────────────────────────────
    if portfolio:
        try:
            from src.portfolio_tracker import record_nav
            positions_dict = {}
            for p in portfolio:
                if p.ticker in ohlcv_map and getattr(p, "qty", 0):
                    positions_dict[p.ticker] = {
                        "quantity": p.qty,
                        "avg_cost": p.buy_price,
                        "current_price": float(ohlcv_map[p.ticker]["close"].iloc[-1]),
                    }
            cash_est = capital - sum(
                p.qty * p.buy_price for p in portfolio if getattr(p, "qty", 0)
            )
            record_nav(date.today(), positions_dict, max(cash_est, 0))
            print(f"  ✓ NAV snapshot recorded")
        except Exception as e:
            print(f"  [warn] NAV record failed (non-fatal): {e}")

    print(f"\nDone. {date.today()}")


if __name__ == "__main__":
    tf = sys.argv[1] if len(sys.argv) > 1 else "daily"
    run(tf)
