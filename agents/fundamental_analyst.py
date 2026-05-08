"""
Claude-powered deep fundamental analysis for a single ticker.
"""
import json

import anthropic
import yaml

from src.fetcher.vnstock_client import get_company_overview, get_financials, get_ohlcv
from src.indicators.technical import macd, rsi
from src.scoring.scorer import CompositeScore
from src.signals import daily, monthly, yearly

from datetime import date, timedelta


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

    context = {
        "ticker": ticker,
        "price_vnd": price,
        "overview": overview,
        "fundamentals": fundamentals,
        "signals": [s.to_dict() for s in signals],
        "composite_score": composite.composite,
        "recommendation": composite.recommendation,
    }

    claude_cfg = cfg.get("claude", {})
    client = anthropic.Anthropic()
    prompt = f"""You are a senior Vietnamese equity analyst. Provide a structured investment thesis
for {ticker} based on the data below. Cover: business quality, valuation, technical setup,
key risks, and a 12-month price outlook. Write in clear English, ~400 words.

Data:
{json.dumps(context, indent=2, ensure_ascii=False)}
"""
    message = client.messages.create(
        model=claude_cfg.get("model", "claude-sonnet-4-6"),
        max_tokens=claude_cfg.get("max_tokens_ticker", 800),
        messages=[{"role": "user", "content": prompt}],
    )

    print(f"\n=== Fundamental Analysis: {ticker} ===")
    print(f"Price: {price:,.0f} VND | Recommendation: {composite.recommendation}\n")
    print(message.content[0].text)


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "VCB"
    analyze_ticker(ticker)
