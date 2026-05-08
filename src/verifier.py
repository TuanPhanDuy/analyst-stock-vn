"""
Pre-send verification + Claude deep analysis.
Checks data quality, signal sanity, then uses Claude to produce
an actionable investment brief with market context.
"""
import json
import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import List

import anthropic


def _retry(fn, attempts: int = 3, backoff: float = 2.0):
    last_exc = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if i < attempts - 1:
                time.sleep(backoff ** i)
    raise last_exc


@dataclass
class VerificationResult:
    passed: bool
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    claude_review: str = ""
    adjusted_ranked: dict = field(default_factory=dict)


# ── Data quality checks ──────────────────────────────────────────────────────

def check_data_quality(freshness_map: dict) -> List[str]:
    issues = []
    stale = [t for t, f in freshness_map.items() if not f["is_fresh"]]
    if len(stale) > len(freshness_map) * 0.3:
        issues.append(
            f">30% of tickers stale (>3 days old): {', '.join(stale[:5])}..."
        )
    return issues


def check_signal_sanity(ranked: dict) -> List[str]:
    issues = []
    buys = ranked.get("buy", [])
    sells = ranked.get("sell", [])
    if not buys and not sells:
        issues.append("No actionable signals generated.")
    return issues


def check_price_sanity(ranked: dict) -> List[str]:
    issues = []
    for action in ("buy", "sell"):
        for item in ranked.get(action, []):
            p = item.get("price", 0)
            if p <= 0 or p < 1000 or p > 5_000_000:
                issues.append(f"{item['ticker']} price {p:,.0f} VND looks implausible")
    return issues


# ── Claude deep analysis ──────────────────────────────────────────────────────

def claude_deep_analysis(ranked: dict, market_ctx: dict,
                         entry_levels: dict, timeframe: str,
                         model: str = "claude-sonnet-4-6",
                         max_tokens: int = 1500,
                         retry_attempts: int = 3,
                         retry_backoff: float = 2.0) -> tuple:
    """
    Full investment brief: validate signals, add market context,
    produce entry/stop/target recommendations.
    Returns (commentary_str, pruned_ranked).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "Claude analysis skipped (no API key).", ranked

    client = anthropic.Anthropic(api_key=api_key)

    system = """You are a senior Vietnamese equity analyst providing a daily investment brief.
You receive quantitative signals, market context, and entry/exit levels.
Your job:
1. Validate which signals are most reliable given the market context
2. Flag any signals that contradict the macro backdrop
3. Produce a clear, actionable brief for a retail investor

Respond ONLY with valid JSON in this exact shape (no markdown, no explanation outside JSON):
{
  "market_summary": "2-3 sentences on today's VN market backdrop",
  "top_picks": [
    {
      "ticker": "XXX",
      "action": "BUY|SELL|AVOID",
      "conviction": "HIGH|MEDIUM|LOW",
      "thesis": "1-2 sentence reason",
      "entry": 12345,
      "stop_loss": 11000,
      "target": 14000,
      "risk_note": "key risk in one sentence"
    }
  ],
  "sectors_to_watch": "one sentence on which sectors look best/worst",
  "flagged_tickers": ["TICKER"],
  "flag_reasons": {"TICKER": "reason"},
  "confidence": "HIGH|MEDIUM|LOW"
}"""

    multiday_ctx = market_ctx.get("multiday_context", {})
    prompt = f"""Today: {date.today()}. Timeframe: {timeframe}.

=== VN-INDEX CONTEXT ===
{json.dumps(market_ctx.get('vnindex', {}), indent=2)}

=== SECTOR PERFORMANCE (1M / 3M avg return) ===
{json.dumps(market_ctx.get('sectors', {}), indent=2)}

=== MARKET BREADTH (VN30) ===
{json.dumps(market_ctx.get('breadth', {}), indent=2)}

=== MULTI-DAY CONVICTION (signals checked over {multiday_ctx.get('offsets_analyzed', [0,1,3,5])} trading-day offsets) ===
Signals required to persist for at least {multiday_ctx.get('min_streak_required', 2)} consecutive days to qualify.
consistency_score: fraction of lookback days where the same direction was signalled (1.0 = perfect).
confidence_trend: positive = signal strengthening over days, negative = weakening.

High-conviction BUY candidates:
{json.dumps(multiday_ctx.get('high_conviction_buys', []), indent=2)}

High-conviction SELL candidates:
{json.dumps(multiday_ctx.get('high_conviction_sells', []), indent=2)}

=== QUANTITATIVE SIGNALS (today's snapshot, ranked by conviction_score) ===
{json.dumps(ranked, indent=2, ensure_ascii=False)}

=== ENTRY / STOP / TARGET LEVELS (ATR-based) ===
{json.dumps(entry_levels, indent=2, ensure_ascii=False)}

Instructions:
- top_picks should include the best 3 BUY and top 2 SELL candidates
- STRONGLY prefer picks with HIGH conviction_label and streak_days >= 3 — these are signals
  that have been building over multiple days, not one-day blips
- Downgrade any signal where confidence_trend is negative (weakening) even if today's score is high
- Use the entry/stop/target from entry_levels as starting points, adjust if S/R suggests better levels
- If market breadth is WEAK or VN-Index is BEARISH, be more cautious on BUY signals
- If a stock's signal contradicts its sector trend, lower conviction to LOW and note it
- Flag any ticker where the signal looks like a data artifact (score suspiciously round, etc.)"""

    try:
        msg = _retry(
            lambda: client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            ),
            attempts=retry_attempts,
            backoff=retry_backoff,
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
            if raw.endswith("```"):
                raw = raw[:-3]
        review = json.loads(raw)
    except anthropic.BadRequestError as e:
        if "credit balance" in str(e).lower() or "billing" in str(e).lower():
            return None, ranked   # no credits — send without commentary, no error shown
        return None, ranked
    except Exception:
        return None, ranked

    flagged = set(review.get("flagged_tickers", []))
    pruned = {
        "buy": [i for i in ranked.get("buy", []) if i["ticker"] not in flagged],
        "sell": [i for i in ranked.get("sell", []) if i["ticker"] not in flagged],
    }
    if not pruned["buy"] and not pruned["sell"]:
        pruned = ranked

    return review, pruned


# ── Main entry point ──────────────────────────────────────────────────────────

def verify(ranked: dict, timeframe: str, freshness_map: dict,
           market_ctx: dict = None, entry_levels: dict = None,
           cfg: dict = None) -> VerificationResult:
    issues = []
    issues += check_data_quality(freshness_map)
    issues += check_signal_sanity(ranked)
    issues += check_price_sanity(ranked)

    if issues:
        return VerificationResult(
            passed=False, issues=issues,
            claude_review="Skipped — data quality issues.",
            adjusted_ranked=ranked,
        )

    claude_cfg = (cfg or {}).get("claude", {})
    retry_cfg = (cfg or {}).get("retry", {})
    review, pruned = claude_deep_analysis(
        ranked, market_ctx or {}, entry_levels or {}, timeframe,
        model=claude_cfg.get("model", "claude-sonnet-4-6"),
        max_tokens=claude_cfg.get("max_tokens_analysis", 1500),
        retry_attempts=retry_cfg.get("attempts", 3),
        retry_backoff=retry_cfg.get("backoff", 2.0),
    )

    return VerificationResult(
        passed=True,
        claude_review=review,          # None when no credits — callers must handle
        adjusted_ranked=pruned,
    )
