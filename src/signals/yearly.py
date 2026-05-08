"""Yearly signals: value investing screen using fundamental ratios."""
from src.signals.base import Signal


def analyze(ticker: str, price: float, fundamentals: dict, cfg: dict) -> Signal:
    pe = fundamentals.get("pe")
    pb = fundamentals.get("pb")
    roe = fundamentals.get("roe")
    debt_equity = fundamentals.get("debt_equity")
    eps_growth = fundamentals.get("eps_growth")      # YoY EPS growth as decimal (0.15 = 15%)
    dividend_yield = fundamentals.get("dividend_yield")  # as decimal (0.03 = 3%)
    revenue_cagr = fundamentals.get("revenue_cagr")  # multi-year revenue CAGR as decimal

    reasons = []
    score = 0.0

    # ROE quality screen
    if roe is not None:
        if roe >= cfg.get("min_roe", 0.12):
            score += 0.30
            reasons.append(f"ROE {roe*100:.1f}% (quality)")
        else:
            score -= 0.20
            reasons.append(f"ROE {roe*100:.1f}% below threshold")

    # Valuation: low P/E is attractive
    pe_cheap = cfg.get("pe_cheap", 12)
    pe_expensive = cfg.get("pe_expensive", 25)
    sector_pe_avg = fundamentals.get("sector_pe_avg")   # optionally injected by caller
    if pe is not None and pe > 0:
        if pe < pe_cheap:
            score += 0.35
            reasons.append(f"P/E {pe:.1f} (cheap)")
        elif pe > pe_expensive:
            score -= 0.25
            reasons.append(f"P/E {pe:.1f} (expensive)")
        # Sector-relative: more precise than absolute threshold
        if sector_pe_avg is not None and sector_pe_avg > 0:
            discount = cfg.get("pe_discount_to_sector", 0.20)
            if pe < sector_pe_avg * (1 - discount):
                score += 0.20
                reasons.append(f"P/E {pe:.1f} cheap vs sector avg {sector_pe_avg:.1f}")
            elif pe > sector_pe_avg * 1.20:
                score -= 0.10
                reasons.append(f"P/E {pe:.1f} expensive vs sector avg {sector_pe_avg:.1f}")

    # P/B
    pb_cheap = cfg.get("pb_cheap", 1.5)
    pb_expensive = cfg.get("pb_expensive", 4.0)
    if pb is not None and pb > 0:
        if pb < pb_cheap:
            score += 0.20
            reasons.append(f"P/B {pb:.2f} (below book)")
        elif pb > pb_expensive:
            score -= 0.15
            reasons.append(f"P/B {pb:.2f} (premium)")

    # Debt-to-equity leverage check
    if debt_equity is not None:
        max_de = cfg.get("max_debt_equity", 1.5)
        if debt_equity < max_de:
            score += 0.15
            reasons.append(f"D/E {debt_equity:.2f} (low leverage)")
        else:
            score -= 0.10
            reasons.append(f"D/E {debt_equity:.2f} (high leverage)")

    # EPS growth — quality earnings trend
    if eps_growth is not None:
        min_eps = cfg.get("min_eps_growth", 0.10)
        if eps_growth >= min_eps:
            score += 0.20
            reasons.append(f"EPS growth +{eps_growth*100:.1f}% YoY")
        elif eps_growth < 0:
            score -= 0.10
            reasons.append(f"EPS declining {eps_growth*100:.1f}% YoY")

    # PEG ratio: P/E relative to EPS growth (< 1.0 = GARP)
    if pe is not None and pe > 0 and eps_growth is not None and eps_growth > 0:
        peg = pe / (eps_growth * 100)
        if peg < 1.0:
            score += 0.15
            reasons.append(f"PEG {peg:.2f} (growth at reasonable price)")
        elif peg > 2.0:
            score -= 0.10
            reasons.append(f"PEG {peg:.2f} (expensive for growth)")

    # Dividend yield — income bonus (no penalty when absent)
    if dividend_yield is not None and dividend_yield > 0:
        min_div = cfg.get("min_dividend_yield", 0.02)
        if dividend_yield >= min_div:
            score += 0.10
            reasons.append(f"Dividend yield {dividend_yield*100:.1f}%")

    # Revenue CAGR — top-line growth quality
    if revenue_cagr is not None:
        min_cagr = cfg.get("min_revenue_cagr", 0.08)
        if revenue_cagr >= min_cagr:
            score += 0.15
            reasons.append(f"Revenue CAGR +{revenue_cagr*100:.1f}%")
        elif revenue_cagr < 0:
            score -= 0.10
            reasons.append(f"Revenue declining {revenue_cagr*100:.1f}% CAGR")

    action = "HOLD"
    confidence = abs(score)
    if score > 0.40:
        action = "BUY"
    elif score < -0.30:
        action = "SELL"

    return Signal(
        ticker=ticker,
        timeframe="yearly",
        action=action,
        confidence=min(confidence, 1.0),
        reason="; ".join(reasons) or "Insufficient fundamental data",
        price=price,
    )
