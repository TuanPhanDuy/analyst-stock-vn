"""
Market regime detection for the VN market.

Classifies the current environment as BULL, BEAR, or SIDEWAYS using:
  - Price vs MA50 / MA200 (trend direction)
  - MA50 vs MA200 (golden/death cross)
  - 1-month and 3-month momentum
  - ADX (trend strength)
  - 52-week drawdown

The regime multiplier adjusts individual stock signal weights:
  - BULL  → BUY signals boosted, SELL signals discounted
  - BEAR  → BUY signals discounted, SELL signals boosted
  - SIDEWAYS → all signals discounted (range-bound = more noise)
"""
import numpy as np
import pandas as pd

from src.indicators.technical import adx as calc_adx, sma


def detect(index_df: pd.DataFrame, cfg: dict = None) -> dict:
    """
    Classify market regime from an index price series.

    Args:
        index_df: DataFrame with at least a 'close' column (DatetimeIndex).
                  Optionally 'high' and 'low' for ADX.
        cfg: regime section from config.yaml (optional).

    Returns:
        {
          regime: "BULL" | "BEAR" | "SIDEWAYS" | "UNKNOWN",
          confidence: 0.0–1.0,
          price, ma50, ma200, adx,
          is_trending: bool,
          r1m_pct, r3m_pct, r6m_pct,
          drawdown_from_52w_pct,
          bull_score, bear_score,
          description: str
        }
    """
    if cfg is None:
        cfg = {}

    if index_df is None or index_df.empty or len(index_df) < 40:
        return {"regime": "UNKNOWN", "confidence": 0.0, "description": "Insufficient data"}

    close = index_df["close"].dropna()
    n = len(close)
    price = float(close.iloc[-1])

    # ── Moving averages ───────────────────────────────────────────────────────
    ma50_period = min(50, n // 2)
    ma200_period = min(200, n - 1)
    ma50 = float(sma(close, ma50_period).iloc[-1])
    ma200_series = sma(close, ma200_period)
    ma200 = float(ma200_series.iloc[-1]) if not np.isnan(float(ma200_series.iloc[-1])) else ma50

    # ── ADX (trend strength) ──────────────────────────────────────────────────
    adx_val = 0.0
    if "high" in index_df.columns and "low" in index_df.columns:
        try:
            adx_series = calc_adx(index_df["high"], index_df["low"], close)
            adx_val = float(adx_series.iloc[-1])
            if np.isnan(adx_val):
                adx_val = 0.0
        except Exception:
            adx_val = 0.0
    adx_threshold = cfg.get("adx_trend_threshold", 25)
    is_trending = adx_val > adx_threshold

    # ── Momentum ──────────────────────────────────────────────────────────────
    r1m = (price / float(close.iloc[-20]) - 1) if n >= 20 else 0.0
    r3m = (price / float(close.iloc[-60]) - 1) if n >= 60 else 0.0
    r6m = (price / float(close.iloc[-120]) - 1) if n >= 120 else 0.0

    # ── 52-week drawdown ─────────────────────────────────────────────────────
    lookback_52w = min(252, n)
    high_52w = float(close.tail(lookback_52w).max())
    drawdown = (price - high_52w) / high_52w

    # ── Scoring system ────────────────────────────────────────────────────────
    bull = 0
    bear = 0

    if price > ma50:
        bull += 1
    else:
        bear += 1

    if price > ma200:
        bull += 2   # MA200 is higher weight
    else:
        bear += 2

    if ma50 > ma200:   # golden cross condition
        bull += 1
    else:
        bear += 1

    if r1m > 0.02:
        bull += 1
    elif r1m < -0.02:
        bear += 1

    if r3m > 0.05:
        bull += 1
    elif r3m < -0.05:
        bear += 1

    if r6m > 0.10:
        bull += 1
    elif r6m < -0.10:
        bear += 1

    # Drawdown penalty for BULL classification
    if drawdown < -0.20:
        bear += 1   # >20% off high = likely bear
    elif drawdown > -0.05:
        bull += 1   # within 5% of high = healthy bull

    total = bull + bear

    # ── Classification ────────────────────────────────────────────────────────
    if total == 0:
        regime = "SIDEWAYS"
        confidence = 0.5
    else:
        bull_ratio = bull / total
        if bull_ratio >= 0.65:
            regime = "BULL"
            confidence = min(0.95, bull_ratio)
        elif bull_ratio <= 0.35:
            regime = "BEAR"
            confidence = min(0.95, 1.0 - bull_ratio)
        else:
            regime = "SIDEWAYS"
            # Higher confidence in SIDEWAYS when scores are balanced
            confidence = 1.0 - abs(bull - bear) / total

    # ADX boosts confidence if trend is strong
    if is_trending and regime != "SIDEWAYS":
        confidence = min(0.95, confidence + 0.05)

    descriptions = {
        "BULL": f"Bull market — price above MA{ma50_period} & MA{ma200_period}, momentum positive",
        "BEAR": f"Bear market — price below MA{ma50_period} & MA{ma200_period}, momentum negative",
        "SIDEWAYS": "Sideways market — mixed signals, reduced signal reliability",
        "UNKNOWN": "Insufficient data for regime classification",
    }

    return {
        "regime": regime,
        "confidence": round(confidence, 2),
        "price": round(price, 1),
        "ma50": round(ma50, 1),
        "ma200": round(ma200, 1),
        "adx": round(adx_val, 1),
        "is_trending": is_trending,
        "r1m_pct": round(r1m * 100, 1),
        "r3m_pct": round(r3m * 100, 1),
        "r6m_pct": round(r6m * 100, 1),
        "drawdown_from_52w_pct": round(drawdown * 100, 1),
        "bull_score": bull,
        "bear_score": bear,
        "description": descriptions[regime],
    }


def signal_multiplier(regime_result: dict, signal_action: str, cfg: dict = None) -> float:
    """
    Return a multiplier for a signal's conviction score based on regime.

    Args:
        regime_result: dict from detect()
        signal_action: "BUY" or "SELL"
        cfg: regime section from config.yaml

    Returns:
        float multiplier (e.g. 1.15 = +15% boost, 0.90 = -10% penalty)
    """
    if cfg is None:
        cfg = {}

    regime = regime_result.get("regime", "UNKNOWN")
    confidence = regime_result.get("confidence", 0.5)
    bull_boost = cfg.get("bull_signal_boost", 0.15)
    bear_penalty = cfg.get("bear_signal_penalty", 0.10)
    sideways_haircut = cfg.get("sideways_haircut", 0.10)

    if regime == "BULL":
        if signal_action == "BUY":
            return 1.0 + bull_boost * confidence
        else:  # SELL in bull market — less reliable
            return 1.0 - bear_penalty * 0.5 * confidence
    elif regime == "BEAR":
        if signal_action == "SELL":
            return 1.0 + bull_boost * confidence
        else:  # BUY in bear market — less reliable
            return 1.0 - bear_penalty * confidence
    elif regime == "SIDEWAYS":
        return 1.0 - sideways_haircut
    else:
        return 1.0   # UNKNOWN — no adjustment
