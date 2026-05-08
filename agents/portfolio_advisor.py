"""
Claude-powered portfolio rebalancing advisor.
Reads a portfolio JSON file, computes P&L and allocations,
runs signals on held positions, then asks Claude for rebalancing recommendations.
"""
import json
from pathlib import Path

import anthropic
import yaml

from src.fetcher.yfinance_client import get_ohlcv_bulk
from src.indicators.technical import entry_exit_levels
from src.scoring.scorer import CompositeScore, rank
from src.signals import daily, monthly, yearly

from datetime import date, timedelta

DEFAULT_PORTFOLIO_PATH = Path(__file__).parent.parent / "data" / "portfolio.json"


def load_portfolio(path: Path) -> list[dict]:
    """Load portfolio from JSON: [{ticker, qty, avg_cost}, ...]"""
    if not path.exists():
        raise FileNotFoundError(
            f"Portfolio file not found: {path}\n"
            "Create data/portfolio.json with [{\"ticker\": \"VCB\", \"qty\": 100, \"avg_cost\": 85000}, ...]"
        )
    return json.loads(path.read_text())


def advise(portfolio_path: Path = DEFAULT_PORTFOLIO_PATH) -> None:
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    portfolio = load_portfolio(portfolio_path)
    tickers = [p["ticker"] for p in portfolio]
    print(f"Portfolio advisor: analyzing {len(tickers)} positions...")

    ohlcv_map = get_ohlcv_bulk(tickers, days=400)

    # Build current P&L and allocation
    total_value = 0.0
    positions = []
    for pos in portfolio:
        ticker = pos["ticker"]
        df = ohlcv_map.get(ticker)
        if df is None or df.empty:
            print(f"  [warn] No data for {ticker}, skipping")
            continue
        current_price = float(df["close"].iloc[-1])
        cost_basis = pos["avg_cost"] * pos["qty"]
        current_value = current_price * pos["qty"]
        pnl_pct = (current_price - pos["avg_cost"]) / pos["avg_cost"] * 100
        total_value += current_value
        positions.append({
            "ticker": ticker,
            "qty": pos["qty"],
            "avg_cost": pos["avg_cost"],
            "current_price": current_price,
            "current_value": current_value,
            "pnl_pct": round(pnl_pct, 1),
        })

    for pos in positions:
        pos["allocation_pct"] = round(pos["current_value"] / total_value * 100, 1) if total_value else 0.0

    # Run signals on held positions
    scores = []
    for ticker, df in ohlcv_map.items():
        try:
            price = float(df["close"].iloc[-1])
            sigs = [
                daily.analyze(ticker, df, cfg["thresholds"]["daily"]),
                monthly.analyze(ticker, df, cfg["thresholds"]["monthly"]),
                yearly.analyze(ticker, price, {}, cfg["thresholds"]["yearly"]),
            ]
            scores.append(CompositeScore(ticker=ticker, price=price, signals=sigs))
        except Exception as e:
            print(f"  [warn] {ticker}: {e}")

    ranked = rank(scores, top_n=len(tickers))
    levels = {t: entry_exit_levels(df) for t, df in ohlcv_map.items()}

    context = {
        "portfolio_date": date.today().isoformat(),
        "total_value_vnd": round(total_value),
        "positions": positions,
        "signals": ranked,
        "entry_exit_levels": levels,
        "portfolio_rules": {
            "max_position_pct": cfg.get("portfolio", {}).get("max_position_pct", 0.15) * 100,
            "risk_per_trade_pct": cfg.get("portfolio", {}).get("risk_per_trade_pct", 0.02) * 100,
        },
    }

    claude_cfg = cfg.get("claude", {})
    client = anthropic.Anthropic()
    prompt = f"""You are a portfolio advisor for a Vietnamese retail investor.
Analyze the current portfolio and today's signals, then recommend rebalancing actions.

Rules:
- Max {context['portfolio_rules']['max_position_pct']}% of capital in any one position
- Max 40% in one sector
- Stop-loss levels are in entry_exit_levels
- Prefer trimming positions with weak signals to fund stronger ones

Respond ONLY with valid JSON in this exact shape:
{{
  "trim": [{{"ticker": "XXX", "qty": 100, "reason": "..."}}],
  "add": [{{"ticker": "XXX", "qty": 100, "reason": "..."}}],
  "hold": [{{"ticker": "XXX", "note": "..."}}],
  "stop_updates": [{{"ticker": "XXX", "new_stop": 12000, "reason": "trail up"}}],
  "summary": "2-3 sentence portfolio overview"
}}

Portfolio data:
{json.dumps(context, indent=2, ensure_ascii=False)}
"""

    print("Asking Claude for rebalancing advice...")
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

    try:
        advice = json.loads(raw)
    except json.JSONDecodeError:
        print("Claude response (raw):")
        print(raw)
        return

    print("\n=== Portfolio Advisor ===")
    print(f"Total value: {total_value:,.0f} VND")
    print(f"\nSummary: {advice.get('summary', '')}")

    if advice.get("trim"):
        print("\nTRIM:")
        for a in advice["trim"]:
            print(f"  {a['ticker']}: sell {a.get('qty', '?')} shares — {a['reason']}")
    if advice.get("add"):
        print("\nADD:")
        for a in advice["add"]:
            lv = levels.get(a["ticker"], {})
            print(f"  {a['ticker']}: buy {a.get('qty', '?')} shares — {a['reason']}")
            if lv:
                print(f"    entry={lv['entry']:,.0f}  stop={lv['stop_loss']:,.0f}  target={lv['target']:,.0f}")
    if advice.get("hold"):
        print("\nHOLD:")
        for a in advice["hold"]:
            print(f"  {a['ticker']}: {a.get('note', '')}")
    if advice.get("stop_updates"):
        print("\nSTOP UPDATES:")
        for a in advice["stop_updates"]:
            print(f"  {a['ticker']}: move stop to {a.get('new_stop', 0):,.0f} — {a.get('reason', '')}")


if __name__ == "__main__":
    import sys
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORTFOLIO_PATH
    advise(path)
