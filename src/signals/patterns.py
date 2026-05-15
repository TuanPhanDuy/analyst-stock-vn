"""
Chart pattern detection for VN stocks.

Detects classic technical chart formations from OHLCV data:
  - Double Bottom / Double Top
  - Head and Shoulders / Inverse H&S
  - Ascending / Descending / Symmetric Triangle
  - Bull Flag / Bear Flag
  - Cup and Handle (simplified)

Each detector returns a pattern dict:
    {
      pattern: str,
      detected: bool,
      confidence: float (0–1),
      description: str,
      score_delta: float,   # positive = bullish, negative = bearish
    }
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _swing_highs_lows(close: pd.Series, window: int = 5) -> tuple:
    """
    Identify local swing highs and lows using a rolling window.
    Returns (highs_idx, lows_idx) — lists of integer positions.
    """
    highs, lows = [], []
    n = len(close)
    for i in range(window, n - window):
        seg = close.iloc[i - window: i + window + 1]
        if close.iloc[i] == seg.max():
            highs.append(i)
        if close.iloc[i] == seg.min():
            lows.append(i)
    return highs, lows


def double_bottom(df: pd.DataFrame, tolerance: float = 0.03) -> dict:
    """
    Detect a double bottom (W pattern) — bullish reversal.

    Two troughs at approximately the same price level with a peak in between.
    Confirmed when price closes above the intervening peak (neckline).

    Args:
        tolerance: max relative difference between the two troughs (0.03 = 3%).
    """
    if df is None or len(df) < 40:
        return _empty("DOUBLE_BOTTOM")

    close = df["close"]
    _, lows_idx = _swing_highs_lows(close, window=5)

    if len(lows_idx) < 2:
        return _empty("DOUBLE_BOTTOM")

    # Check the last two significant lows
    l1_idx, l2_idx = lows_idx[-2], lows_idx[-1]
    l1, l2 = float(close.iloc[l1_idx]), float(close.iloc[l2_idx])
    price_now = float(close.iloc[-1])

    # Troughs at similar levels
    trough_diff = abs(l1 - l2) / max(l1, l2)
    if trough_diff > tolerance:
        return _empty("DOUBLE_BOTTOM")

    # Peak (neckline) between the two troughs
    between = close.iloc[l1_idx:l2_idx]
    if between.empty:
        return _empty("DOUBLE_BOTTOM")
    neckline = float(between.max())
    avg_trough = (l1 + l2) / 2

    # Pattern height: neckline should be at least 3% above troughs
    height_pct = (neckline - avg_trough) / avg_trough
    if height_pct < 0.03:
        return _empty("DOUBLE_BOTTOM")

    # Confirmation: price breaking above neckline
    confirmed = price_now > neckline
    confidence = min(0.90, 0.55 + (1 - trough_diff / tolerance) * 0.20 + (0.15 if confirmed else 0))
    target = neckline + (neckline - avg_trough)   # equal-measure projection

    return {
        "pattern": "DOUBLE_BOTTOM",
        "detected": True,
        "confirmed": confirmed,
        "confidence": round(confidence, 2),
        "description": (
            f"Double bottom at {avg_trough:,.0f} · neckline {neckline:,.0f} · "
            f"target {target:,.0f} · {'CONFIRMED ✓' if confirmed else 'watching for breakout'}"
        ),
        "score_delta": round(confidence * 0.20 if confirmed else confidence * 0.10, 3),
        "neckline": neckline,
        "target": target,
    }


def double_top(df: pd.DataFrame, tolerance: float = 0.03) -> dict:
    """
    Detect a double top (M pattern) — bearish reversal.
    """
    if df is None or len(df) < 40:
        return _empty("DOUBLE_TOP")

    close = df["close"]
    highs_idx, _ = _swing_highs_lows(close, window=5)

    if len(highs_idx) < 2:
        return _empty("DOUBLE_TOP")

    h1_idx, h2_idx = highs_idx[-2], highs_idx[-1]
    h1, h2 = float(close.iloc[h1_idx]), float(close.iloc[h2_idx])
    price_now = float(close.iloc[-1])

    peak_diff = abs(h1 - h2) / max(h1, h2)
    if peak_diff > tolerance:
        return _empty("DOUBLE_TOP")

    between = close.iloc[h1_idx:h2_idx]
    if between.empty:
        return _empty("DOUBLE_TOP")
    neckline = float(between.min())
    avg_peak = (h1 + h2) / 2

    height_pct = (avg_peak - neckline) / neckline
    if height_pct < 0.03:
        return _empty("DOUBLE_TOP")

    confirmed = price_now < neckline
    confidence = min(0.90, 0.55 + (1 - peak_diff / tolerance) * 0.20 + (0.15 if confirmed else 0))
    target = neckline - (avg_peak - neckline)

    return {
        "pattern": "DOUBLE_TOP",
        "detected": True,
        "confirmed": confirmed,
        "confidence": round(confidence, 2),
        "description": (
            f"Double top at {avg_peak:,.0f} · neckline {neckline:,.0f} · "
            f"target {target:,.0f} · {'CONFIRMED ✓' if confirmed else 'watching for breakdown'}"
        ),
        "score_delta": round(-confidence * 0.20 if confirmed else -confidence * 0.10, 3),
        "neckline": neckline,
        "target": target,
    }


def head_and_shoulders(df: pd.DataFrame) -> dict:
    """
    Detect a head and shoulders pattern — bearish reversal.

    Three peaks where the middle (head) is the highest,
    with two lower shoulders on either side.
    """
    if df is None or len(df) < 60:
        return _empty("HEAD_AND_SHOULDERS")

    close = df["close"]
    highs_idx, _ = _swing_highs_lows(close, window=5)

    if len(highs_idx) < 3:
        return _empty("HEAD_AND_SHOULDERS")

    # Check last three swing highs as L shoulder, head, R shoulder
    ls_idx, h_idx, rs_idx = highs_idx[-3], highs_idx[-2], highs_idx[-1]
    ls = float(close.iloc[ls_idx])
    head = float(close.iloc[h_idx])
    rs = float(close.iloc[rs_idx])
    price_now = float(close.iloc[-1])

    # Head must be higher than both shoulders
    if not (head > ls and head > rs):
        return _empty("HEAD_AND_SHOULDERS")

    # Shoulders should be at similar levels (within 5%)
    shoulder_diff = abs(ls - rs) / max(ls, rs)
    if shoulder_diff > 0.05:
        return _empty("HEAD_AND_SHOULDERS")

    # Neckline = avg of troughs between shoulder/head/shoulder
    t1 = float(close.iloc[ls_idx:h_idx].min())
    t2 = float(close.iloc[h_idx:rs_idx].min())
    neckline = (t1 + t2) / 2

    confirmed = price_now < neckline
    confidence = min(0.85, 0.50 + (1 - shoulder_diff / 0.05) * 0.20 + (0.15 if confirmed else 0))
    target = neckline - (head - neckline)

    return {
        "pattern": "HEAD_AND_SHOULDERS",
        "detected": True,
        "confirmed": confirmed,
        "confidence": round(confidence, 2),
        "description": (
            f"H&S: LS={ls:,.0f} Head={head:,.0f} RS={rs:,.0f} · "
            f"neckline={neckline:,.0f} · target={target:,.0f} · "
            f"{'CONFIRMED ✓' if confirmed else 'watching neckline'}"
        ),
        "score_delta": round(-confidence * 0.22 if confirmed else -confidence * 0.08, 3),
        "neckline": neckline,
        "target": target,
    }


def inverse_head_and_shoulders(df: pd.DataFrame) -> dict:
    """
    Detect an inverse head and shoulders — bullish reversal.
    """
    if df is None or len(df) < 60:
        return _empty("INVERSE_H&S")

    close = df["close"]
    _, lows_idx = _swing_highs_lows(close, window=5)

    if len(lows_idx) < 3:
        return _empty("INVERSE_H&S")

    ls_idx, h_idx, rs_idx = lows_idx[-3], lows_idx[-2], lows_idx[-1]
    ls = float(close.iloc[ls_idx])
    head = float(close.iloc[h_idx])
    rs = float(close.iloc[rs_idx])
    price_now = float(close.iloc[-1])

    if not (head < ls and head < rs):
        return _empty("INVERSE_H&S")

    shoulder_diff = abs(ls - rs) / max(ls, rs)
    if shoulder_diff > 0.05:
        return _empty("INVERSE_H&S")

    p1 = float(close.iloc[ls_idx:h_idx].max())
    p2 = float(close.iloc[h_idx:rs_idx].max())
    neckline = (p1 + p2) / 2

    confirmed = price_now > neckline
    confidence = min(0.85, 0.50 + (1 - shoulder_diff / 0.05) * 0.20 + (0.15 if confirmed else 0))
    target = neckline + (neckline - head)

    return {
        "pattern": "INVERSE_H&S",
        "detected": True,
        "confirmed": confirmed,
        "confidence": round(confidence, 2),
        "description": (
            f"Inv H&S: LS={ls:,.0f} Head={head:,.0f} RS={rs:,.0f} · "
            f"neckline={neckline:,.0f} · target={target:,.0f} · "
            f"{'CONFIRMED ✓' if confirmed else 'watching for breakout'}"
        ),
        "score_delta": round(confidence * 0.22 if confirmed else confidence * 0.08, 3),
        "neckline": neckline,
        "target": target,
    }


def triangle(df: pd.DataFrame, lookback: int = 30) -> dict:
    """
    Detect triangle patterns (ascending, descending, symmetric).

    Ascending triangle: flat resistance + rising support → bullish breakout.
    Descending triangle: flat support + falling resistance → bearish breakdown.
    Symmetric triangle: converging highs and lows → breakout in dominant direction.
    """
    if df is None or len(df) < lookback + 10:
        return _empty("TRIANGLE")

    recent = df.tail(lookback)
    highs = recent["high"].values
    lows = recent["low"].values
    xs = np.arange(len(highs), dtype=float)

    # Fit trend lines to highs and lows
    high_slope = float(np.polyfit(xs, highs, 1)[0])
    low_slope = float(np.polyfit(xs, lows, 1)[0])
    high_std = float(np.std(highs - np.polyfit(xs, highs, 1)[0] * xs))
    low_std = float(np.std(lows - np.polyfit(xs, lows, 1)[0] * xs))

    price_now = float(df["close"].iloc[-1])
    resistance = float(np.polyfit(xs, highs, 1)[0] * len(highs) + np.polyfit(xs, highs, 1)[1])
    support = float(np.polyfit(xs, lows, 1)[0] * len(lows) + np.polyfit(xs, lows, 1)[1])

    flat_threshold = abs(resistance - support) * 0.05

    if high_slope < -flat_threshold and abs(low_slope) < flat_threshold:
        pat = "DESCENDING_TRIANGLE"
        confirmed = price_now < support
        score_delta = -0.15 if confirmed else -0.06
        desc = f"Descending triangle: resistance falling, support flat at {support:,.0f}"
    elif low_slope > flat_threshold and abs(high_slope) < flat_threshold:
        pat = "ASCENDING_TRIANGLE"
        confirmed = price_now > resistance
        score_delta = 0.15 if confirmed else 0.06
        desc = f"Ascending triangle: support rising, resistance flat at {resistance:,.0f}"
    elif high_slope < -flat_threshold and low_slope > flat_threshold:
        pat = "SYMMETRIC_TRIANGLE"
        breakout_up = price_now > resistance
        breakout_down = price_now < support
        confirmed = breakout_up or breakout_down
        score_delta = (0.12 if breakout_up else -0.12) if confirmed else 0.0
        desc = f"Symmetric triangle: converging to {(resistance + support) / 2:,.0f}"
    else:
        return _empty("TRIANGLE")

    height = abs(recent["high"].max() - recent["low"].min())
    confidence = min(0.80, 0.45 + (0.20 if confirmed else 0))

    return {
        "pattern": pat,
        "detected": True,
        "confirmed": confirmed,
        "confidence": round(confidence, 2),
        "description": desc + (" · CONFIRMED ✓" if confirmed else ""),
        "score_delta": round(score_delta, 3),
        "resistance": round(resistance, 0),
        "support": round(support, 0),
        "target": round((resistance + height if score_delta > 0 else support - height), 0),
    }


def flag_pattern(df: pd.DataFrame, pole_min_pct: float = 0.08) -> dict:
    """
    Detect bull flag (strong up-move + sideways/slight pullback) or
    bear flag (strong down-move + slight relief rally).

    The flag is a brief consolidation after a sharp directional move (pole).
    Breakout from the flag continues in the direction of the pole.
    """
    if df is None or len(df) < 25:
        return _empty("FLAG")

    close = df["close"]
    n = len(close)

    # Measure the "pole": sharp directional move in bars 10-20 ago
    pole_window = min(15, n // 3)
    flag_window = min(10, n // 4)

    pole_end_idx = n - flag_window - 1
    pole_start_idx = max(0, pole_end_idx - pole_window)

    pole_start = float(close.iloc[pole_start_idx])
    pole_end = float(close.iloc[pole_end_idx])
    pole_return = (pole_end - pole_start) / pole_start

    if abs(pole_return) < pole_min_pct:
        return _empty("FLAG")

    # Flag: consolidation in the last flag_window bars
    flag_section = close.iloc[pole_end_idx:]
    flag_high = float(flag_section.max())
    flag_low = float(flag_section.min())
    flag_range = (flag_high - flag_low) / pole_end if pole_end else 0
    price_now = float(close.iloc[-1])

    # Flag should be tight relative to the pole
    if flag_range > abs(pole_return) * 0.5:
        return _empty("FLAG")

    if pole_return > 0:
        pat = "BULL_FLAG"
        # Breakout = price moves above flag high
        confirmed = price_now > flag_high
        score_delta = 0.18 if confirmed else 0.08
        desc = f"Bull flag: pole +{pole_return*100:.1f}% · flag range {flag_range*100:.1f}%"
    else:
        pat = "BEAR_FLAG"
        confirmed = price_now < flag_low
        score_delta = -0.18 if confirmed else -0.08
        desc = f"Bear flag: pole {pole_return*100:.1f}% · flag range {flag_range*100:.1f}%"

    confidence = min(0.80, 0.50 + min(abs(pole_return) / 0.20, 0.20) + (0.10 if confirmed else 0))

    return {
        "pattern": pat,
        "detected": True,
        "confirmed": confirmed,
        "confidence": round(confidence, 2),
        "description": desc + (" · CONFIRMED ✓" if confirmed else ""),
        "score_delta": round(score_delta, 3),
    }


# ── Master scanner ────────────────────────────────────────────────────────────

def detect_all(df: pd.DataFrame) -> list:
    """
    Run all pattern detectors on a single ticker's OHLCV data.

    Returns a list of detected patterns (only those with detected=True),
    sorted by |score_delta| descending.
    """
    if df is None or len(df) < 40:
        return []

    detectors = [
        double_bottom,
        double_top,
        head_and_shoulders,
        inverse_head_and_shoulders,
        triangle,
        flag_pattern,
    ]

    detected = []
    for fn in detectors:
        try:
            result = fn(df)
            if result.get("detected"):
                detected.append(result)
        except Exception:
            pass

    detected.sort(key=lambda x: abs(x.get("score_delta", 0)), reverse=True)
    return detected


def pattern_score_delta(df: pd.DataFrame) -> tuple:
    """
    Quick summary: net score_delta from all detected patterns + description string.

    Returns (net_delta: float, reasons: str)
    """
    patterns = detect_all(df)
    if not patterns:
        return 0.0, ""

    net = sum(p["score_delta"] for p in patterns)
    reasons = "; ".join(
        f"{p['pattern']}({p['score_delta']:+.2f})" for p in patterns[:2]
    )
    return round(net, 3), reasons


def _empty(name: str) -> dict:
    return {
        "pattern": name,
        "detected": False,
        "confirmed": False,
        "confidence": 0.0,
        "description": "",
        "score_delta": 0.0,
    }
