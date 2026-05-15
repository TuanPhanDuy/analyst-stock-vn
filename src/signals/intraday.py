"""
Intraday pattern signals: opening gap, open-to-close direction, day streak,
VWAP deviation, and session momentum.

Input: OHLCV DataFrame with DatetimeIndex, columns open/high/low/close/volume.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def _empty(name: str) -> dict:
    return {
        "pattern": name,
        "detected": False,
        "direction": 0,
        "score_delta": 0.0,
        "reason": "",
    }


def opening_gap(df: pd.DataFrame, min_gap_pct: float = 1.0) -> dict:
    """
    Detect a significant opening gap vs prior close.

    A gap-up ≥ min_gap_pct% on rising volume is bullish (+0.08 to +0.12).
    A gap-down is bearish.
    """
    if len(df) < 2:
        return _empty("opening_gap")

    prev_close = float(df["close"].iloc[-2])
    today_open = float(df["open"].iloc[-1])
    if prev_close <= 0:
        return _empty("opening_gap")

    gap_pct = (today_open / prev_close - 1) * 100

    if abs(gap_pct) < min_gap_pct:
        return _empty("opening_gap")

    direction = 1 if gap_pct > 0 else -1
    gap_abs = abs(gap_pct)

    # Volume confirmation
    avg_vol = float(df["volume"].iloc[-11:-1].mean()) if len(df) >= 11 else float(df["volume"].mean())
    today_vol = float(df["volume"].iloc[-1])
    vol_ratio = today_vol / avg_vol if avg_vol > 0 else 1.0

    score_delta = min(0.12, 0.06 + gap_abs * 0.01)
    if vol_ratio > 1.5:
        score_delta = min(0.15, score_delta * 1.2)

    score_delta *= direction

    return {
        "pattern": "opening_gap",
        "detected": True,
        "direction": direction,
        "gap_pct": round(gap_pct, 2),
        "vol_ratio": round(vol_ratio, 2),
        "score_delta": round(score_delta, 3),
        "reason": f"Gap {'up' if direction > 0 else 'down'} {gap_abs:.1f}% at open"
                  + (f" (vol {vol_ratio:.1f}x)" if vol_ratio > 1.3 else ""),
    }


def open_to_close_strength(df: pd.DataFrame, lookback: int = 5) -> dict:
    """
    Measure open-to-close directional consistency over recent sessions.

    If 4+/5 sessions close above open → bullish momentum.
    """
    if len(df) < lookback:
        return _empty("open_to_close")

    recent = df.tail(lookback)
    bull_days = int((recent["close"] > recent["open"]).sum())
    bear_days = lookback - bull_days
    consistency = max(bull_days, bear_days) / lookback

    if consistency < 0.7:
        return _empty("open_to_close")

    direction = 1 if bull_days > bear_days else -1
    score_delta = round((consistency - 0.5) * 0.2 * direction, 3)

    return {
        "pattern": "open_to_close",
        "detected": True,
        "direction": direction,
        "bull_days": bull_days,
        "bear_days": bear_days,
        "consistency": round(consistency, 2),
        "score_delta": score_delta,
        "reason": f"{bull_days if direction > 0 else bear_days}/{lookback} sessions "
                  f"close {'above' if direction > 0 else 'below'} open",
    }


def day_streak(df: pd.DataFrame) -> dict:
    """
    Count consecutive up/down days by close-to-close.

    3+ consecutive days in one direction → potential exhaustion or momentum.
    """
    if len(df) < 2:
        return _empty("day_streak")

    closes = df["close"].values
    changes = np.diff(closes)

    streak = 1
    direction = int(np.sign(changes[-1])) if len(changes) > 0 else 0
    if direction == 0:
        return _empty("day_streak")

    for chg in reversed(changes[:-1]):
        if int(np.sign(chg)) == direction:
            streak += 1
        else:
            break

    if streak < 3:
        return _empty("day_streak")

    # 3-4 day streak → momentum signal; 5+ day → exhaustion warning
    if streak >= 5:
        # Potential reversal — signal against the streak direction
        score_delta = round(-0.05 * direction, 3)
        reason = f"{streak}-day {'up' if direction > 0 else 'down'} streak — exhaustion risk"
    else:
        score_delta = round(0.04 * direction, 3)
        reason = f"{streak} consecutive {'up' if direction > 0 else 'down'} days — momentum"

    return {
        "pattern": "day_streak",
        "detected": True,
        "direction": direction,
        "streak_days": streak,
        "score_delta": score_delta,
        "reason": reason,
    }


def vwap_deviation(df: pd.DataFrame, threshold_pct: float = 2.0) -> dict:
    """
    Compare current close to estimated VWAP (typical price × volume / cum_volume).

    Strong positive deviation = overbought intraday; negative = oversold.
    """
    if len(df) < 5:
        return _empty("vwap_deviation")

    recent = df.tail(20)
    typical = (recent["high"] + recent["low"] + recent["close"]) / 3
    total_vol = float(recent["volume"].sum())
    if total_vol <= 0:
        return _empty("vwap_deviation")

    vwap = float((typical * recent["volume"]).sum() / total_vol)
    current = float(df["close"].iloc[-1])
    dev_pct = (current / vwap - 1) * 100

    if abs(dev_pct) < threshold_pct:
        return _empty("vwap_deviation")

    direction = 1 if dev_pct > 0 else -1
    # Strong deviation above VWAP on rising price can be bullish OR mean-reversion risk
    # We use it as a mild momentum confirmation (not reversal)
    score_delta = round(min(0.06, abs(dev_pct) * 0.01) * direction, 3)

    return {
        "pattern": "vwap_deviation",
        "detected": True,
        "direction": direction,
        "vwap": round(vwap, 2),
        "current": round(current, 2),
        "dev_pct": round(dev_pct, 2),
        "score_delta": score_delta,
        "reason": f"Price {dev_pct:+.1f}% vs VWAP ({vwap:,.0f})",
    }


def high_low_range_compression(df: pd.DataFrame, lookback: int = 10, threshold: float = 0.5) -> dict:
    """
    Detect range compression (narrow candles = coiling before breakout).

    Compares recent N-day average range to prior N-day average range.
    Compression ≥ threshold (50% narrower) = SQUEEZE signal.
    """
    if len(df) < lookback * 2:
        return _empty("range_compression")

    def avg_range(window: pd.DataFrame) -> float:
        return float(((window["high"] - window["low"]) / window["close"]).mean() * 100)

    recent_range = avg_range(df.tail(lookback))
    prior_range = avg_range(df.iloc[-lookback * 2:-lookback])

    if prior_range <= 0:
        return _empty("range_compression")

    compression_ratio = recent_range / prior_range
    if compression_ratio > (1 - threshold):
        return _empty("range_compression")

    compression_pct = (1 - compression_ratio) * 100
    score_delta = round(min(0.08, compression_pct * 0.002), 3)

    return {
        "pattern": "range_compression",
        "detected": True,
        "direction": 0,  # neutral — direction unknown before breakout
        "compression_pct": round(compression_pct, 1),
        "recent_range_pct": round(recent_range, 3),
        "prior_range_pct": round(prior_range, 3),
        "score_delta": score_delta,
        "reason": f"Range compressed {compression_pct:.0f}% vs prior period — squeeze",
    }


def detect_all(df: pd.DataFrame) -> list:
    """Run all intraday pattern detectors and return detected signals."""
    detectors = [
        opening_gap,
        open_to_close_strength,
        day_streak,
        vwap_deviation,
        high_low_range_compression,
    ]
    results = []
    for fn in detectors:
        try:
            r = fn(df)
            if r.get("detected"):
                results.append(r)
        except Exception:
            pass
    return sorted(results, key=lambda x: abs(x.get("score_delta", 0)), reverse=True)


def intraday_score_delta(df: pd.DataFrame) -> tuple:
    """
    Quick summary: (net_delta, reasons_list).
    """
    signals = detect_all(df)
    net = sum(s.get("score_delta", 0) for s in signals)
    reasons = [s["reason"] for s in signals if s.get("reason")]
    return round(net, 3), reasons
