"""
Institutional Trading Desk — Multi-Agent Daily Brief Generator.

Simulates 10 trading desk roles (Macro, Research, Quant, Portfolio Manager,
Risk, Compliance, Trader, Liquidity, Ops, Head of Trading) via a single
Claude call with structured data context. Returns a decision-first brief
optimized for pre-market reading.
"""
import json
import logging
from datetime import date
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)


def run_session(
    regime_result: dict,
    vnindex: dict,
    ranked: dict,
    portfolio_statuses: list,
    risk_report: dict,
    sector_rotation: dict,
    foreign_flows: dict,
    insider_signals: dict,
    earnings_flags: dict,
    futures_snapshot: dict,
    pattern_enrichments: dict,
    capital: float,
    cfg: dict,
) -> dict:
    """
    Run the full institutional trading desk session.

    Returns:
        {
          "head_directive": str,           # e.g. "RISK-ON · Buy SHB, Hold KDH, Build cash"
          "decisions": [                   # ordered action list
            {"action": "BUY|SELL|HOLD|TRIM|WATCH", "ticker": str,
             "entry": float, "stop": float, "target": float,
             "conviction": "HIGH|MEDIUM|LOW", "rationale": str}
          ],
          "agent_summaries": {            # one-liner per agent
            "macro": str, "research": str, "quant": str,
            "portfolio": str, "risk": str, "compliance": str
          },
          "risk_alerts": [str],           # urgent flags only
          "market_stance": str,           # BULL/BEAR/SIDEWAYS + note
          "key_themes": [str],            # 2-3 market themes for today
          "execution_note": str,          # timing / liquidity note
        }
    """
    client = anthropic.Anthropic()
    claude_cfg = cfg.get("claude", {})

    # ── Build context payload ─────────────────────────────────────────────────
    regime = regime_result.get("regime", "UNKNOWN")
    conf = regime_result.get("confidence", 0)
    r1m = regime_result.get("r1m_pct", 0)
    adx = regime_result.get("adx", 0)

    vni_price = vnindex.get("price", 0)
    vni_trend = vnindex.get("trend", "?")
    vni_1m = vnindex.get("m1_return_pct", 0)

    buy_signals = [
        {
            "ticker": s["ticker"],
            "price": s.get("price", 0),
            "conviction_score": round(s.get("conviction_score", 0), 3),
            "conviction_label": s.get("conviction_label", "LOW"),
            "streak_days": s.get("streak_days", 0),
            "consistency_pct": round(s.get("consistency_score", 0) * 100, 0),
            "entry": s.get("sizing", {}).get("entry", s.get("price", 0)),
            "stop": s.get("sizing", {}).get("stop_loss", 0),
            "target": s.get("sizing", {}).get("target", 0),
            "qty": s.get("sizing", {}).get("qty", 0),
            "total_cost": s.get("sizing", {}).get("total_cost", 0),
        }
        for s in ranked.get("buy", [])[:5]
    ]

    sell_signals = [
        {
            "ticker": s["ticker"],
            "price": s.get("price", 0),
            "conviction_score": round(s.get("conviction_score", 0), 3),
            "conviction_label": s.get("conviction_label", "LOW"),
        }
        for s in ranked.get("sell", [])[:3]
    ]

    portfolio_ctx = []
    for s in portfolio_statuses:
        p = s.position
        portfolio_ctx.append({
            "ticker": p.ticker,
            "buy_price": p.buy_price,
            "current_price": s.current_price,
            "pnl_pct": round(s.pnl_pct, 2),
            "status": s.status,
            "stop_loss": p.stop_loss,
            "target": p.target,
            "pct_from_stop": round(s.pct_from_stop, 1),
            "pct_from_target": round(s.pct_from_target, 1),
            "needs_action": s.needs_action,
        })

    risk_ctx = {}
    if risk_report:
        risk_ctx = {
            "var_95_pct": risk_report.get("portfolio_var_95", {}).get("var_pct"),
            "cvar_95_pct": risk_report.get("portfolio_var_95", {}).get("cvar_pct"),
            "beta": risk_report.get("portfolio_beta", {}).get("portfolio_beta"),
            "flags": risk_report.get("flags", []),
        }

    rotation_ctx = {}
    if sector_rotation:
        rotation_ctx = {
            "rotate_into": sector_rotation.get("rotate_into", [])[:3],
            "rotate_out_of": sector_rotation.get("rotate_out_of", [])[:3],
        }

    futures_ctx = {}
    if futures_snapshot:
        futures_ctx = {
            "signal": futures_snapshot.get("signal", ""),
            "basis_pct": futures_snapshot.get("basis_pct", 0),
            "score_delta": futures_snapshot.get("score_delta", 0),
        }

    strong_foreign_buy = [t for t, s in foreign_flows.items()
                          if "STRONG" in s.get("label", "") and s.get("direction", 0) > 0]
    strong_foreign_sell = [t for t, s in foreign_flows.items()
                           if "STRONG" in s.get("label", "") and s.get("direction", 0) < 0]
    insider_buy = [t for t, s in insider_signals.items() if s.get("direction", 0) > 0]
    earnings_warn = [t for t, f in earnings_flags.items() if f.get("flag")]
    pattern_bullish = [t for t, v in pattern_enrichments.items() if v.get("pattern_delta", 0) > 0.05]
    pattern_bearish = [t for t, v in pattern_enrichments.items() if v.get("pattern_delta", 0) < -0.05]

    context_json = json.dumps({
        "date": date.today().isoformat(),
        "market": {
            "regime": regime,
            "regime_confidence_pct": round(conf * 100),
            "vn_index_price": vni_price,
            "vn_index_trend": vni_trend,
            "vn_index_1m_pct": vni_1m,
            "adx": adx,
            "index_1m_pct": r1m,
            "futures": futures_ctx,
            "sector_rotation": rotation_ctx,
        },
        "portfolio": portfolio_ctx,
        "portfolio_risk": risk_ctx,
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
        "foreign_flow": {
            "strong_buying": strong_foreign_buy,
            "strong_selling": strong_foreign_sell,
        },
        "insider": {"buying": insider_buy},
        "earnings_warnings": earnings_warn,
        "chart_patterns": {
            "bullish": pattern_bullish,
            "bearish": pattern_bearish,
        },
        "capital_vnd": capital,
    }, ensure_ascii=False)

    prompt = f"""You are the Head of an institutional trading desk covering Vietnamese equities (HOSE/HNX).
Today: {date.today().isoformat()} | Pre-market brief (market opens 9:00 AM ICT).

You have 10 specialist agents: Macro Strategist, Equity Research, Quant, Portfolio Manager,
Risk Management, Compliance, Trader, Liquidity, Operations, and Head of Trading.

Here is today's market data (JSON):
{context_json}

Run each agent's analysis and produce the DAILY TRADING BRIEF.

Rules:
- Capital preservation first. Never trade without defined stop.
- Vietnam market specifics: T+2 settlement, lot size 100 shares, HOSE transaction cost ~0.70% round-trip.
- Stop losses already defined in portfolio context — respect them.
- Positions with needs_action=true MUST be addressed first.
- Max position = 50% of capital. Min cash buffer = 15%.
- Only recommend NEW entries if they pass Risk Management (VaR stays under 3.5%).

Respond ONLY with valid JSON:
{{
  "head_directive": "one-line overall stance for today, e.g. RISK-ON · Add SHB, Hold KDH, target 15% cash",
  "decisions": [
    {{
      "action": "BUY|SELL|HOLD|TRIM|WATCH|EXIT",
      "ticker": "XXX",
      "entry": 14200,
      "stop": 13800,
      "target": 15100,
      "qty": 100,
      "conviction": "HIGH|MEDIUM|LOW",
      "rationale": "short reason (max 15 words)",
      "agent": "which agent is driving this decision"
    }}
  ],
  "agent_summaries": {{
    "macro": "one sentence on macro / regime",
    "research": "one sentence on best fundamental opportunity",
    "quant": "one sentence on strongest quant signal today",
    "portfolio": "one sentence on portfolio allocation status",
    "risk": "one sentence on key risk metric or alert",
    "compliance": "CLEAR or specific warning"
  }},
  "risk_alerts": ["list of urgent risk flags, empty if none"],
  "market_stance": "BULL|BEAR|SIDEWAYS + one-line note",
  "key_themes": ["theme 1", "theme 2"],
  "execution_note": "one sentence on best execution timing or liquidity note"
}}"""

    try:
        message = client.messages.create(
            model=claude_cfg.get("model", "claude-sonnet-4-6"),
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[:-3].rstrip()
        return json.loads(raw)
    except Exception as e:
        logger.error("Trading desk brief failed: %s", e)
        return {
            "head_directive": "Analysis unavailable — hold all positions",
            "decisions": [],
            "agent_summaries": {},
            "risk_alerts": [f"Brief generation failed: {e}"],
            "market_stance": regime,
            "key_themes": [],
            "execution_note": "",
        }
