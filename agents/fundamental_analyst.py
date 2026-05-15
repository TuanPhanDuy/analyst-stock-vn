"""
Claude-powered deep fundamental analysis for a single ticker.

Enhanced with: peer comparison, insider activity, earnings events,
market regime context, news sentiment, and chart patterns.
"""
import json
from datetime import date, timedelta

import anthropic
import yaml

from src.fetcher.vnstock_client import get_company_overview, get_financials, get_ohlcv
from src.indicators.technical import macd, rsi
from src.scoring.scorer import CompositeScore
from src.signals import daily, monthly, yearly


def _safe(fn, *args, default=None, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return default


def analyze_ticker(ticker: str) -> None:
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    end = date.today().isoformat()
    start = (date.today() - timedelta(days=365)).isoformat()

    df = get_ohlcv(ticker, start, end)
    fundamentals = get_financials(ticker)
    overview = get_company_overview(ticker)
    price = float(df["close"].iloc[-1])

    signals = [
        daily.analyze(ticker, df, cfg["thresholds"]["daily"]),
        monthly.analyze(ticker, df, cfg["thresholds"]["monthly"]),
        yearly.analyze(ticker, price, fundamentals, cfg["thresholds"]["yearly"]),
    ]
    composite = CompositeScore(ticker=ticker, price=price, signals=signals)

    # ── Enrichments ──────────────────────────────────────────────────────────
    sector_map = cfg.get("sector_map", {})

    # Market regime
    from src import regime as regime_mod
    regime_result = _safe(
        lambda: regime_mod.detect(df, cfg.get("regime", {})),
        default={"regime": "UNKNOWN"},
    )

    # Peer comparison
    from src.peer_comparison import compare, peers_from_sector
    peers = peers_from_sector(ticker, sector_map)
    ohlcv_map = {ticker: df}
    if peers:
        for peer in peers[:4]:
            try:
                peer_df = get_ohlcv(peer, start, end)
                ohlcv_map[peer] = peer_df
            except Exception:
                pass
    peer_result = _safe(
        lambda: compare(ticker, peers[:4], ohlcv_map),
        default={"summary": "peer data unavailable"},
    )

    # Insider transactions
    from src.fetcher.insider import signal as insider_signal, get_transactions
    insider_txns = _safe(lambda: get_transactions(ticker, days=90), default=[])
    insider = _safe(lambda: insider_signal(insider_txns), default={"label": "NO_DATA"})

    # Earnings events
    from src.earnings import flag as earnings_flag
    earnings = _safe(lambda: earnings_flag(ticker, warn_days=30), default={"flag": False})

    # Chart patterns
    from src.signals.patterns import detect_all as detect_patterns
    patterns = _safe(lambda: detect_all_patterns(df), default=[])

    # Intraday signals
    from src.signals.intraday import detect_all as detect_intraday
    intraday = _safe(lambda: detect_intraday(df), default=[])

    # News headlines
    from src.fetcher.news_scraper import fetch_all, format_for_claude
    headlines = _safe(lambda: fetch_all(ticker, days=14), default=[])
    news_text = format_for_claude(headlines, max_items=15)

    # Foreign flow
    from src.fetcher.foreign_flow import get_foreign_flow, foreign_signal
    ff_data = _safe(lambda: get_foreign_flow(ticker), default=[])
    ff = _safe(lambda: foreign_signal(ff_data), default={"label": "NEUTRAL", "score_delta": 0})

    # Risk metrics
    from src.risk_manager import var_cvar
    from src.fetcher.vnstock_client import get_ohlcv as get_ohlcv_short
    ret = df["close"].pct_change().dropna()
    risk = var_cvar(ret)

    # ── Build context for Claude ──────────────────────────────────────────────
    context = {
        "ticker": ticker,
        "price_vnd": price,
        "overview": overview,
        "fundamentals": fundamentals,
        "signals": [s.to_dict() for s in signals],
        "composite_score": composite.composite,
        "recommendation": composite.recommendation,
        "market_regime": regime_result,
        "peer_comparison": {
            "summary": peer_result.get("summary"),
            "rank": peer_result.get("ticker_rank"),
            "peers": [
                {k: v for k, v in m.items() if k in ("ticker", "pe", "pb", "roe", "ret_1m_pct", "overall_rank")}
                for m in peer_result.get("metrics", [])
            ],
        },
        "insider_activity": {
            "label": insider.get("label"),
            "net_qty": insider.get("net_qty"),
            "score_delta": insider.get("score_delta"),
        },
        "earnings_event": earnings,
        "foreign_flow": {
            "label": ff.get("label"),
            "net_5d": ff.get("net_5d"),
            "net_20d": ff.get("net_20d"),
            "score_delta": ff.get("score_delta"),
        },
        "chart_patterns": [
            {k: p[k] for k in ("pattern", "direction", "score_delta", "reason") if k in p}
            for p in patterns[:3]
        ],
        "intraday_signals": [
            {k: s[k] for k in ("pattern", "score_delta", "reason") if k in s}
            for s in intraday[:3]
        ],
        "risk": {
            "var_95_pct": risk.get("var_pct"),
            "cvar_95_pct": risk.get("cvar_pct"),
        },
        "recent_news": news_text,
    }

    # ── Claude analysis ───────────────────────────────────────────────────────
    claude_cfg = cfg.get("claude", {})
    client = anthropic.Anthropic()
    prompt = f"""You are a senior Vietnamese equity analyst. Provide a structured investment thesis
for {ticker} based on the data below. Cover:

1. **Business Quality** — competitive position, sector outlook
2. **Valuation** — vs peers, historical range, fair value estimate
3. **Technical Setup** — key levels, momentum signals, chart patterns
4. **Fundamental Catalysts** — earnings, corporate actions, insider activity
5. **Foreign & Institutional Flow** — smart money signals
6. **News Sentiment** — material recent events
7. **Key Risks** — downside scenarios, regulatory/sector risks
8. **Price Outlook** — 3-month and 12-month targets with confidence

Market regime: {regime_result.get('regime', 'UNKNOWN')} — adjust your conviction accordingly.
Write in clear English, ~500 words. Be specific with numbers.

Data:
{json.dumps(context, indent=2, ensure_ascii=False, default=str)}
"""
    message = client.messages.create(
        model=claude_cfg.get("model", "claude-sonnet-4-6"),
        max_tokens=claude_cfg.get("max_tokens_ticker", 1200),
        messages=[{"role": "user", "content": prompt}],
    )

    print(f"\n{'=' * 60}")
    print(f"FUNDAMENTAL ANALYSIS: {ticker}")
    print(f"{'=' * 60}")
    print(f"Price: {price:,.0f} VND  |  Recommendation: {composite.recommendation}")
    print(f"Regime: {regime_result.get('regime')}  |  Peer rank: {peer_result.get('ticker_rank', {}).get('overall', '?')}/{len(peers) + 1}")
    print(f"Insider: {insider.get('label')}  |  Foreign: {ff.get('label')}")
    if earnings.get("flag"):
        print(f"⚠ Earnings event: {earnings.get('message', '')}")
    print()
    print(message.content[0].text)
    print()


def detect_all_patterns(df):
    from src.signals.patterns import detect_all
    return detect_all(df)


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "VCB"
    analyze_ticker(ticker)
