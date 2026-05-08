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

    tickers = cfg["watchlist"]["vn30"]
    top_n = cfg["output"]["top_n"]
    md_cfg = cfg.get("multiday", {})
    min_streak = md_cfg.get("min_streak", 2)
    max_workers = cfg.get("parallelism", {}).get("max_workers", 8)

    print(f"Fetching data for {len(tickers)} tickers...")
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


def main():
    parser = argparse.ArgumentParser(description="Vietnamese Stock Analyst")
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = sub.add_parser("scan", help="Scan watchlist and rank buy/sell signals")
    p_scan.add_argument("--timeframe", choices=["daily", "monthly", "yearly"], default="daily")
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

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
