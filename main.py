#!/usr/bin/env python3
"""CLI entrypoint for analyst-stock-vn."""
import argparse
import sys
from pathlib import Path


def cmd_scan(args):
    import yaml
    from src.fetcher.yfinance_client import get_ohlcv_bulk
    from src.signals.multiday import build_multiday_scores, rank_by_conviction
    from src.report.formatter import print_table

    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    if args.min_streak is not None:
        cfg.setdefault("multiday", {})["min_streak"] = args.min_streak

    watchlist_key = getattr(args, "watchlist", "vn30")
    tickers = cfg["watchlist"].get(watchlist_key, cfg["watchlist"]["vn30"])
    top_n = cfg["output"]["top_n"]
    md_cfg = cfg.get("multiday", {})
    min_streak = md_cfg.get("min_streak", 2)
    max_workers = cfg.get("parallelism", {}).get("max_workers", 8)

    print(f"Fetching data for {len(tickers)} tickers ({watchlist_key.upper()})...")
    ohlcv_map = get_ohlcv_bulk(tickers, days=400)

    print(f"Running multi-day analysis (offsets={md_cfg.get('lookback_offsets', [0,1,3,5])}, min_streak={min_streak})...")
    analyses = build_multiday_scores(ohlcv_map, cfg, max_workers=max_workers)
    ranked = rank_by_conviction(analyses, top_n=top_n, min_streak=min_streak)

    print(f"\n{'='*60}")
    print(f"  Multi-Day Signal Results — {args.timeframe.upper()}")
    print(f"  Signals persisted ≥{min_streak} days  |  ranked by conviction_score")
    print(f"{'='*60}")
    print_table(ranked, args.timeframe)


def cmd_analyze(args):
    from agents.fundamental_analyst import analyze_ticker
    analyze_ticker(args.ticker)


def cmd_backtest(args):
    import yaml
    from src.fetcher.yfinance_client import get_ohlcv_bulk
    from src.backtesting.runner import run_backtest, print_backtest_report

    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    tickers = cfg["watchlist"]["vn30"]
    print(f"Fetching {args.days} days of data for {len(tickers)} tickers...")
    ohlcv_map = get_ohlcv_bulk(tickers, days=args.days)
    print(f"Running walk-forward backtest (hold={args.hold} days)...")
    df = run_backtest(ohlcv_map, cfg, hold_days=args.hold)
    print_backtest_report(df)


def cmd_advise(args):
    from agents.portfolio_advisor import advise
    advise(Path(args.portfolio))


def cmd_news(args):
    from agents.news_monitor import monitor, print_monitor_report
    tickers = [t.strip() for t in args.tickers.split(",")]
    headlines = None
    if args.headlines:
        headlines = Path(args.headlines).read_text().splitlines()
    result = monitor(tickers, headlines=headlines)
    print_monitor_report(result)


def cmd_update_outcomes(args):
    import yaml
    from src.fetcher.yfinance_client import get_ohlcv_bulk
    from src.trade_log.logger import load_history, update_pending_outcomes

    history = load_history()
    if history.empty:
        print("No signals logged yet (data/signals.jsonl is empty or missing).")
        return

    pending = history[history["outcome_result"] == "PENDING"]
    if pending.empty:
        print("No PENDING signals to update.")
        return

    tickers = pending["ticker"].unique().tolist()
    print(f"Fetching data for {len(tickers)} tickers with PENDING outcomes...")
    ohlcv_map = get_ohlcv_bulk(tickers, days=args.days + 20)
    n = update_pending_outcomes(ohlcv_map, hold_days=args.days)
    print(f"Updated {n} signal outcome(s).")


def cmd_regime(args):
    """Detect and print the current market regime."""
    import yaml
    from src.fetcher.yfinance_client import get_ohlcv_bulk
    from src.market_context import _synthetic_index
    from src import regime as regime_mod

    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    tickers = cfg["watchlist"]["vn30"]
    print(f"Fetching data for {len(tickers)} VN30 tickers...")
    ohlcv_map = get_ohlcv_bulk(tickers, days=400)
    idx_df = _synthetic_index(ohlcv_map)
    r = regime_mod.detect(idx_df, cfg.get("regime", {}))

    print(f"\n{'='*50}")
    print(f"  MARKET REGIME: {r['regime']}")
    print(f"{'='*50}")
    print(f"  Confidence:   {r['confidence']:.0%}")
    print(f"  MA50:         {r['ma50']:,.1f}")
    print(f"  MA200:        {r['ma200']:,.1f}")
    print(f"  ADX:          {r['adx']:.0f} {'(trending)' if r['is_trending'] else '(ranging)'}")
    print(f"  1M Return:    {r['r1m_pct']:+.1f}%")
    print(f"  3M Return:    {r['r3m_pct']:+.1f}%")
    print(f"  6M Return:    {r['r6m_pct']:+.1f}%")
    print(f"  52W Drawdown: {r['drawdown_from_52w_pct']:.1f}%")
    print(f"  Bull Score:   {r['bull_score']}")
    print(f"  Bear Score:   {r['bear_score']}")
    print(f"\n  {r['description']}")
    print(f"{'='*50}")


def cmd_sector(args):
    """Show sector rotation analysis."""
    import yaml
    from src.fetcher.yfinance_client import get_ohlcv_bulk
    from src import sector_rotation as sr_mod

    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    tickers = cfg["watchlist"]["vn30"]
    print(f"Fetching data for {len(tickers)} tickers...")
    ohlcv_map = get_ohlcv_bulk(tickers, days=400)
    result = sr_mod.analyze(ohlcv_map, cfg.get("sectors", {}), cfg)

    print(f"\n{'='*60}")
    print(f"  SECTOR ROTATION ANALYSIS")
    print(f"{'='*60}")
    print(f"\n  {result['summary']}\n")

    rankings = result.get("rankings", [])
    print(f"  {'Rank':<4} {'Sector':<14} {'Signal':<12} {'1M%':>6} {'3M%':>6} "
          f"{'Alpha':>6} {'AbvMA20':>8} {'Score':>6} {'Label'}")
    print(f"  {'─'*78}")
    for e in rankings:
        sig = {"ROTATE_IN": "▲ IN", "ROTATE_OUT": "▼ OUT", "HOLD": "─ HOLD"}.get(
            e.get("rotation_signal", ""), "─"
        )
        print(f"  {e['rank']:<4} {e['sector']:<14} {sig:<12} "
              f"{e.get('avg_return_1m_pct', 0):>+5.1f}% "
              f"{e.get('avg_return_3m_pct', 0):>+5.1f}% "
              f"{e.get('alpha_1m_pct', 0):>+5.1f}% "
              f"{e.get('above_ma20_pct', 0):>6.0f}%  "
              f"{e.get('score', 0):>+5.2f}  {e.get('label', '')}")

    print(f"\n  Spread: {result['spread_pct']:.1f}pts  |  Actionable: {result['actionable']}")


def cmd_optimize(args):
    """Run portfolio optimization on current buy signals."""
    import yaml
    from src.fetcher.yfinance_client import get_ohlcv_bulk
    from src.signals.multiday import build_multiday_scores, rank_by_conviction
    from src.portfolio_optimizer import optimize

    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    tickers = cfg["watchlist"]["vn30"]
    print(f"Fetching data for {len(tickers)} tickers...")
    ohlcv_map = get_ohlcv_bulk(tickers, days=400)

    analyses = build_multiday_scores(ohlcv_map, cfg, max_workers=8)
    ranked = rank_by_conviction(analyses, top_n=10, min_streak=2)

    if not ranked.get("buy"):
        print("No buy signals to optimize.")
        return

    result = optimize(ohlcv_map, ranked["buy"], cfg)

    print(f"\n{'='*60}")
    print(f"  PORTFOLIO OPTIMIZATION")
    print(f"{'='*60}")
    print(f"\n  Buy candidates: {[r['ticker'] for r in result['recommendations']]}")

    if result.get("high_correlation_pairs"):
        print("\n  ⚠ HIGH CORRELATION PAIRS:")
        for pair in result["high_correlation_pairs"]:
            print(f"    {pair['note']}")

    print("\n  RECOMMENDED WEIGHTS:")
    print(f"  {'Ticker':<8} {'MV Weight':>10} {'Kelly Risk':>10}  Note")
    print(f"  {'─'*50}")
    for rec in result["recommendations"]:
        print(f"  {rec['ticker']:<8} {rec['mv_weight_pct']:>9.1f}%  "
              f"{rec['kelly_risk_pct']:>9.1f}%  {rec.get('note', '')}")


def cmd_accuracy(args):
    """Show signal accuracy report from trade history."""
    import json
    from src.ml_signal import accuracy_report

    report = accuracy_report()

    if "message" in report and "total_closed" not in report:
        print(f"\n  {report['message']}")
        return

    print(f"\n{'='*50}")
    print(f"  SIGNAL ACCURACY REPORT")
    print(f"{'='*50}")
    print(f"  Closed trades:  {report.get('total_closed', 0)}")
    print(f"  Pending trades: {report.get('total_pending', 0)}")
    print(f"  Win rate:       {report.get('overall_win_rate_pct', 0):.1f}%")
    print(f"  Avg P&L:        {report.get('avg_pnl_pct', 0):+.2f}%")
    print(f"  Avg win:        {report.get('avg_win_pct', 0):+.2f}%")
    print(f"  Avg loss:       {report.get('avg_loss_pct', 0):+.2f}%")
    print(f"  ML calibrator:  {report.get('ml_calibrator', 'unknown')}")

    if report.get("by_action"):
        print("\n  By action:")
        for action, stats in report["by_action"].items():
            print(f"    {action:<4}: {stats['count']:3d} trades  "
                  f"win={stats['win_rate_pct']:.1f}%  avg={stats['avg_pnl_pct']:+.2f}%")

    if report.get("top_tickers"):
        print("\n  Top performers:")
        for t in report["top_tickers"]:
            print(f"    {t['ticker']:<6} avg={t['avg_pnl_pct']:+.2f}%  trades={t['trades']}")

    if report.get("worst_tickers"):
        print("\n  Worst performers:")
        for t in report["worst_tickers"]:
            print(f"    {t['ticker']:<6} avg={t['avg_pnl_pct']:+.2f}%  trades={t['trades']}")

    if report.get("kelly_recommended_risk_pct"):
        print(f"\n  Kelly-recommended risk/trade: {report['kelly_recommended_risk_pct']:.2f}%")

    print(f"{'='*50}")


def cmd_foreign(args):
    """Show foreign investor flow for watchlist tickers."""
    import yaml
    from src.fetcher.yfinance_client import get_ohlcv_bulk
    from src.fetcher.foreign_flow import get_foreign_flow_bulk, foreign_signal

    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else cfg["watchlist"]["vn30"]
    print(f"Fetching foreign flow for {len(tickers)} tickers...")

    raw = get_foreign_flow_bulk(tickers, days=60)
    ff_cfg = cfg.get("foreign_flow", {})
    strong_thr = ff_cfg.get("strong_buying_threshold", 100_000)
    signals = {t: foreign_signal(f, strong_threshold=strong_thr) for t, f in raw.items()}

    # Sort: strong buyers first, then strong sellers
    buying = sorted(
        [(t, s) for t, s in signals.items() if s["direction"] > 0],
        key=lambda x: x[1]["net_5d"], reverse=True
    )
    selling = sorted(
        [(t, s) for t, s in signals.items() if s["direction"] < 0],
        key=lambda x: x[1]["net_5d"]
    )

    print(f"\n{'='*60}")
    print(f"  FOREIGN INVESTOR FLOW")
    print(f"{'='*60}")
    if buying:
        print(f"\n  FOREIGN BUYING:")
        print(f"  {'Ticker':<8} {'Label':<15} {'Net 5D':>12} {'Net 20D':>12}")
        print(f"  {'─'*50}")
        for t, s in buying[:10]:
            print(f"  {t:<8} {s['label']:<15} {s['net_5d']:>+12,}  {s['net_20d']:>+12,}")
    if selling:
        print(f"\n  FOREIGN SELLING:")
        print(f"  {'Ticker':<8} {'Label':<15} {'Net 5D':>12} {'Net 20D':>12}")
        print(f"  {'─'*50}")
        for t, s in selling[:10]:
            print(f"  {t:<8} {s['label']:<15} {s['net_5d']:>+12,}  {s['net_20d']:>+12,}")
    if not buying and not selling:
        print("  No significant foreign flow data available.")


def cmd_dashboard(_args):
    """Launch the Streamlit dashboard."""
    import subprocess
    print("Launching Streamlit dashboard at http://localhost:8501")
    subprocess.run(["streamlit", "run", "dashboard.py"], check=True)


def cmd_risk(args):
    """Portfolio risk report: VaR, CVaR, beta, risk contribution."""
    import yaml
    from src.fetcher.yfinance_client import get_ohlcv_bulk
    from src.risk_manager import risk_report

    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else cfg["watchlist"]["vn30"]
    weights = {}
    if args.weights:
        for pair in args.weights.split(","):
            t, w = pair.strip().split(":")
            weights[t.strip().upper()] = float(w)
    else:
        # Equal weight
        for t in tickers:
            weights[t] = 1.0 / len(tickers)

    print(f"Fetching data for {len(tickers)} tickers...")
    ohlcv_map = get_ohlcv_bulk(tickers, days=400)
    nav = float(args.nav) if args.nav else 100_000_000.0
    report = risk_report(ohlcv_map, weights, nav=nav)

    print(f"\n{'='*55}")
    print(f"  PORTFOLIO RISK REPORT")
    print(f"{'='*55}")
    pvar = report.get("portfolio_var_95", {})
    pbeta = report.get("portfolio_beta", {})
    if pvar.get("var_pct") is not None:
        print(f"  VaR 95% (1-day):  {pvar['var_pct']:.2f}%")
        print(f"  CVaR 95% (1-day): {pvar.get('cvar_pct', 0):.2f}%")
        if report.get("daily_var_vnd"):
            print(f"  VaR in VND:       {report['daily_var_vnd']:,.0f} ₫")
    if pbeta.get("portfolio_beta") is not None:
        print(f"  Portfolio Beta:   {pbeta['portfolio_beta']:.2f} ({pbeta.get('interpretation','')})")

    rc = report.get("risk_contribution", [])
    if rc:
        print(f"\n  Risk Contribution:")
        print(f"  {'Ticker':<8} {'Weight':>8} {'Risk %':>8} {'MargVar':>10}")
        print(f"  {'─'*38}")
        for r in rc:
            print(f"  {r['ticker']:<8} {r['weight_pct']:>7.1f}%  {r['risk_contribution_pct']:>7.1f}%  {r['marginal_var']:>10.4f}")

    stress = report.get("stress_tests", [])
    if stress:
        print(f"\n  Stress Tests:")
        for s in stress:
            color = "↓" if s["portfolio_impact_pct"] < 0 else "↑"
            print(f"    {color} {s['scenario']}: {s['portfolio_impact_pct']:+.2f}%")

    flags = report.get("flags", [])
    if flags:
        print(f"\n  ⚠ Risk Flags:")
        for f in flags:
            print(f"    {f}")


def cmd_alerts(args):
    """Manage price alerts."""
    from src.alerts import add_alert, remove_alert, list_alerts, reset_triggered

    sub = args.alert_command

    if sub == "add":
        ticker = args.ticker.upper()
        alert = add_alert(ticker, args.condition, float(args.threshold),
                          note=args.note or "", reference_price=float(args.ref) if args.ref else None)
        print(f"Alert #{alert['id']} added: {ticker} {args.condition} {args.threshold}"
              + (f" ref={args.ref}" if args.ref else ""))

    elif sub == "list":
        ticker = args.ticker.upper() if args.ticker else None
        alerts = list_alerts(ticker=ticker, status=args.status or "active")
        if not alerts:
            print("No alerts found.")
            return
        print(f"\n{'ID':>4}  {'Ticker':<8} {'Condition':<12} {'Threshold':>12}  {'Status':<10} {'Note'}")
        print("─" * 65)
        for a in alerts:
            print(f"{a['id']:>4}  {a['ticker']:<8} {a['condition']:<12} {a['threshold']:>12}  "
                  f"{a['status']:<10} {a.get('note', '')}")

    elif sub == "remove":
        removed = remove_alert(int(args.id))
        print(f"Alert #{args.id} {'removed' if removed else 'not found'}.")

    elif sub == "reset":
        count = reset_triggered(int(args.id) if args.id else None)
        print(f"Reset {count} alert(s) to active.")

    elif sub == "check":
        import yaml
        from src.fetcher.yfinance_client import get_ohlcv_bulk
        from src.alerts import check_alerts, format_triggered

        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)

        tickers_to_check = {a["ticker"] for a in list_alerts(status="active")}
        if not tickers_to_check:
            print("No active alerts to check.")
            return

        ohlcv_map = get_ohlcv_bulk(list(tickers_to_check), days=5)
        prices = {t: float(df["close"].iloc[-1]) for t, df in ohlcv_map.items()}
        triggered = check_alerts(prices)
        if triggered:
            print(format_triggered(triggered))
        else:
            print(f"No alerts triggered (checked {len(prices)} tickers).")


def cmd_nav(args):
    """Portfolio NAV history and performance summary."""
    from src.portfolio_tracker import summary, equity_curve, drawdown_periods

    result = summary(benchmark=not args.no_benchmark)

    if "error" in result:
        print(f"\n  {result['error']}")
        print("  Run daily scans to start recording NAV history.")
        return

    print(f"\n{'='*50}")
    print(f"  PORTFOLIO PERFORMANCE")
    print(f"{'='*50}")
    print(f"  NAV (current):     {result['nav_current']:>15,.2f} ₫")
    print(f"  NAV (start):       {result['nav_start']:>15,.2f} ₫")
    print(f"  Total return:      {result['total_return_pct']:>+14.2f}%")
    print(f"  CAGR:              {result['cagr_pct']:>+14.2f}%")
    print(f"  Max drawdown:      {result['max_drawdown_pct']:>+14.2f}%")
    print(f"  Sharpe ratio:      {result['sharpe']:>14.2f}")
    print(f"  Trading days:      {result['trading_days']:>15}")
    if result.get("benchmark_return_pct") is not None:
        print(f"  VN-Index return:   {result['benchmark_return_pct']:>+14.2f}%")
        print(f"  Alpha vs VN-Index: {result.get('alpha_pct', 0):>+14.2f}%")

    dd_periods = drawdown_periods(threshold=-0.05)
    if dd_periods:
        print(f"\n  Drawdown periods (> 5%):")
        for d in dd_periods:
            print(f"    {d['start']} → {d['end']}  max={d['max_drawdown_pct']:.1f}%  "
                  f"recovery={d['recovery_days']}d")


def cmd_patterns(args):
    """Detect chart patterns for a single ticker."""
    import yaml
    from src.fetcher.yfinance_client import get_ohlcv_bulk
    from src.signals.patterns import detect_all as detect_patterns
    from src.signals.intraday import detect_all as detect_intraday

    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    ticker = args.ticker.upper()
    print(f"Fetching data for {ticker}...")
    ohlcv_map = get_ohlcv_bulk([ticker], days=200)

    df = ohlcv_map.get(ticker)
    if df is None:
        print(f"No data for {ticker}")
        return

    price = float(df["close"].iloc[-1])
    patterns = detect_patterns(df)
    intraday = detect_intraday(df)

    print(f"\n{'='*55}")
    print(f"  PATTERN ANALYSIS: {ticker}  ({price:,.0f} ₫)")
    print(f"{'='*55}")

    if patterns:
        print(f"\n  Chart Patterns:")
        for p in patterns:
            arrow = "↑" if p["direction"] > 0 else ("↓" if p["direction"] < 0 else "→")
            print(f"  {arrow} {p['pattern']:<25} delta={p['score_delta']:>+.3f}  {p.get('reason','')}")
    else:
        print("  No chart patterns detected.")

    if intraday:
        print(f"\n  Intraday Signals:")
        for s in intraday:
            arrow = "↑" if s["direction"] > 0 else ("↓" if s["direction"] < 0 else "→")
            print(f"  {arrow} {s['pattern']:<25} delta={s['score_delta']:>+.3f}  {s.get('reason','')}")
    else:
        print("\n  No intraday signals.")


def cmd_futures(_args):
    """Show VN30F futures snapshot and basis signal."""
    from src.fetcher.futures import get_futures_snapshot, futures_signal_summary

    print("Fetching VN30F futures data...")
    snap = get_futures_snapshot(ttl=60)

    print(f"\n{'='*50}")
    print(f"  VN30F FUTURES SNAPSHOT")
    print(f"{'='*50}")
    print(f"  {futures_signal_summary(snap)}")
    if snap.get("futures_price") is not None:
        print(f"\n  Contract:       {snap.get('contract', 'VN30F')}")
        print(f"  Futures price:  {snap['futures_price']:,.1f}")
        if snap.get("spot_price"):
            print(f"  Spot (VN30):    {snap['spot_price']:,.1f}")
        if snap.get("basis_pct") is not None:
            print(f"  Basis:          {snap['basis_pct']:+.3f}%  ({snap.get('basis_pts', 0):+.1f} pts)")
        print(f"  Signal:         {snap.get('signal', '?')}")
        print(f"  Score delta:    {snap.get('score_delta', 0):+.2f}")
        print(f"  OI:             {snap.get('open_interest', 0):,}")
        print(f"  Source:         {snap.get('source', '?')}")


def cmd_peers(args):
    """Compare a ticker vs sector peers on fundamentals and momentum."""
    import yaml
    from src.fetcher.yfinance_client import get_ohlcv_bulk
    from src.peer_comparison import compare, peers_from_sector

    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    ticker = args.ticker.upper()
    sector_map = cfg.get("sector_map", {})

    if args.peers:
        peers = [p.strip().upper() for p in args.peers.split(",")]
    else:
        peers = peers_from_sector(ticker, sector_map, max_peers=6)
        if not peers:
            print(f"No sector peers found for {ticker}. Use --peers PEER1,PEER2 to specify.")
            return

    all_tickers = [ticker] + peers
    print(f"Fetching data for {', '.join(all_tickers)}...")
    ohlcv_map = get_ohlcv_bulk(all_tickers, days=200)

    result = compare(ticker, peers, ohlcv_map)

    print(f"\n{'='*70}")
    print(f"  PEER COMPARISON: {ticker}")
    print(f"{'='*70}")
    print(f"\n  {result['summary']}\n")

    metrics = result.get("metrics", [])
    print(f"  {'Ticker':<8} {'P/E':>6} {'P/B':>6} {'ROE%':>6} {'1M%':>6}  "
          f"{'P/E rank':>8} {'ROE rank':>8} {'Overall':>8}")
    print(f"  {'─'*68}")
    for m in metrics:
        marker = "►" if m.get("is_target") else " "
        pe = f"{m['pe']:.1f}" if m.get("pe") else "  —"
        pb = f"{m['pb']:.1f}" if m.get("pb") else "  —"
        roe = f"{m['roe']:.1f}" if m.get("roe") else "  —"
        ret = f"{m['ret_1m_pct']:+.1f}" if m.get("ret_1m_pct") is not None else "  —"
        per = f"{m['pe_rank']:.0f}" if m.get("pe_rank") is not None else "  —"
        rr = f"{m['roe_rank']:.0f}" if m.get("roe_rank") is not None else "  —"
        ov = f"{m['overall_rank']:.0f}" if m.get("overall_rank") is not None else "  —"
        print(f"  {marker}{m['ticker']:<7} {pe:>6} {pb:>6} {roe:>6} {ret:>6}  "
              f"{per:>8} {rr:>8} {ov:>8}")


def main():
    parser = argparse.ArgumentParser(description="Vietnamese Stock Analyst")
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = sub.add_parser("scan", help="Scan watchlist and rank buy/sell signals")
    p_scan.add_argument("--timeframe", choices=["daily", "monthly", "yearly"], default="daily")
    p_scan.add_argument("--watchlist", choices=["vn30", "vn100", "hnx30"], default="vn30")
    p_scan.add_argument("--min-streak", type=int, default=None,
                        help="Override min consecutive days required (default: from config.yaml)")
    p_scan.set_defaults(func=cmd_scan)

    # analyze
    p_analyze = sub.add_parser("analyze", help="Deep analysis on a single ticker")
    p_analyze.add_argument("--ticker", required=True, help="e.g. VCB")
    p_analyze.add_argument("--timeframe", choices=["daily", "monthly", "yearly"], default="monthly")
    p_analyze.set_defaults(func=cmd_analyze)

    # backtest
    p_bt = sub.add_parser("backtest", help="Walk-forward backtest on cached OHLCV data")
    p_bt.add_argument("--days", type=int, default=400, help="Historical lookback days")
    p_bt.add_argument("--hold", type=int, default=5, help="Days to hold each signal")
    p_bt.set_defaults(func=cmd_backtest)

    # advise
    p_adv = sub.add_parser("advise", help="Portfolio rebalancing advice")
    p_adv.add_argument("--portfolio", default="data/portfolio.json",
                       help="Path to portfolio JSON file")
    p_adv.set_defaults(func=cmd_advise)

    # news
    p_news = sub.add_parser("news", help="Monitor news/events for tickers")
    p_news.add_argument("--tickers", required=True, help="Comma-separated tickers, e.g. VCB,FPT")
    p_news.add_argument("--headlines", default=None,
                        help="Path to a text file with one headline per line")
    p_news.set_defaults(func=cmd_news)

    # update-outcomes
    p_uo = sub.add_parser("update-outcomes", help="Resolve PENDING signal outcomes from trade log")
    p_uo.add_argument("--days", type=int, default=5, help="Hold period in days (default: 5)")
    p_uo.set_defaults(func=cmd_update_outcomes)

    # regime  [NEW]
    p_regime = sub.add_parser("regime", help="Detect current market regime (BULL/BEAR/SIDEWAYS)")
    p_regime.set_defaults(func=cmd_regime)

    # sector  [NEW]
    p_sector = sub.add_parser("sector", help="Sector rotation analysis")
    p_sector.set_defaults(func=cmd_sector)

    # optimize  [NEW]
    p_opt = sub.add_parser("optimize", help="Portfolio optimization (min-variance + Kelly)")
    p_opt.set_defaults(func=cmd_optimize)

    # accuracy  [NEW]
    p_acc = sub.add_parser("accuracy", help="Signal accuracy report from trade history")
    p_acc.set_defaults(func=cmd_accuracy)

    # foreign  [NEW]
    p_ff = sub.add_parser("foreign", help="Foreign investor flow for watchlist tickers")
    p_ff.add_argument("--tickers", default=None, help="Comma-separated tickers (default: VN30)")
    p_ff.set_defaults(func=cmd_foreign)

    # dashboard
    p_dash = sub.add_parser("dashboard", help="Launch the Streamlit visual dashboard")
    p_dash.set_defaults(func=cmd_dashboard)

    # risk
    p_risk = sub.add_parser("risk", help="Portfolio risk report: VaR, beta, stress test")
    p_risk.add_argument("--tickers", default=None, help="Comma-separated tickers (default: VN30)")
    p_risk.add_argument("--weights", default=None,
                        help="Comma-separated TICKER:WEIGHT pairs, e.g. VCB:0.4,FPT:0.3")
    p_risk.add_argument("--nav", default=None, help="Portfolio NAV in VND for absolute VaR (default: 100M)")
    p_risk.set_defaults(func=cmd_risk)

    # alerts
    p_alerts = sub.add_parser("alerts", help="Manage price alerts")
    alerts_sub = p_alerts.add_subparsers(dest="alert_command", required=True)

    p_alert_add = alerts_sub.add_parser("add", help="Add a new price alert")
    p_alert_add.add_argument("--ticker", required=True)
    p_alert_add.add_argument("--condition", required=True, choices=["above", "below", "pct_change"])
    p_alert_add.add_argument("--threshold", required=True, help="Price level or pct change")
    p_alert_add.add_argument("--ref", default=None, help="Reference price (for pct_change)")
    p_alert_add.add_argument("--note", default=None)

    p_alert_list = alerts_sub.add_parser("list", help="List alerts")
    p_alert_list.add_argument("--ticker", default=None)
    p_alert_list.add_argument("--status", default="active", choices=["active", "triggered", "all"])

    p_alert_rm = alerts_sub.add_parser("remove", help="Remove an alert by ID")
    p_alert_rm.add_argument("--id", required=True)

    p_alert_reset = alerts_sub.add_parser("reset", help="Re-activate triggered alerts")
    p_alert_reset.add_argument("--id", default=None, help="Alert ID (omit to reset all)")

    p_alert_check = alerts_sub.add_parser("check", help="Check all active alerts against current prices")

    p_alerts.set_defaults(func=cmd_alerts)

    # nav
    p_nav = sub.add_parser("nav", help="Show portfolio NAV history and performance vs VN-Index")
    p_nav.add_argument("--no-benchmark", action="store_true", help="Skip VN-Index benchmark fetch")
    p_nav.set_defaults(func=cmd_nav)

    # patterns
    p_pat = sub.add_parser("patterns", help="Detect chart patterns and intraday signals for a ticker")
    p_pat.add_argument("--ticker", required=True, help="e.g. VCB")
    p_pat.set_defaults(func=cmd_patterns)

    # futures
    p_fut = sub.add_parser("futures", help="Show VN30F futures snapshot and basis signal")
    p_fut.set_defaults(func=cmd_futures)

    # peers
    p_peers = sub.add_parser("peers", help="Peer comparison: rank ticker vs sector peers")
    p_peers.add_argument("--ticker", required=True, help="e.g. VCB")
    p_peers.add_argument("--peers", default=None, help="Comma-separated peer tickers (default: sector peers)")
    p_peers.set_defaults(func=cmd_peers)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
