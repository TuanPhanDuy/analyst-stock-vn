"""
HOSE/HNX/UPCOM market microstructure rules.

Tick sizes (bước giá) — HOSE & HNX:
  price < 10,000           → tick = 10 VND
  10,000 ≤ price < 50,000  → tick = 50 VND
  price ≥ 50,000           → tick = 100 VND

Daily price bands:
  HOSE:  ±7%  (LO orders only; ATO/ATC exempt)
  HNX:   ±10% (also supports MP orders)
  UPCOM: ±15%
"""
import math

_MARKET_PARAMS = {
    "HOSE":  {"band": 0.07, "lot": 100, "supports_mp": False},
    "HNX":   {"band": 0.10, "lot": 100, "supports_mp": True},
    "UPCOM": {"band": 0.15, "lot": 100, "supports_mp": True},
}


def get_rules(market: str = "HOSE") -> dict:
    """Return market-specific trading parameters."""
    return _MARKET_PARAMS.get(market.upper(), _MARKET_PARAMS["HOSE"])


def tick_size(price: float) -> int:
    """Return the minimum price increment (bước giá) for a given price level."""
    if price < 10_000:
        return 10
    if price < 50_000:
        return 50
    return 100


def round_to_tick(price: float, direction: str = "nearest") -> int:
    """
    Round a price to the nearest valid tick.
    direction: 'nearest' | 'up' | 'down'
    """
    t = tick_size(price)
    if direction == "up":
        return int(math.ceil(price / t) * t)
    if direction == "down":
        return int(math.floor(price / t) * t)
    return int(round(price / t) * t)


def ceiling_floor_hnx(reference_price: float) -> dict:
    """Ceiling/floor for HNX (±10% band)."""
    return ceiling_floor(reference_price, band=0.10)


def ceiling_floor_upcom(reference_price: float) -> dict:
    """Ceiling/floor for UPCOM (±15% band)."""
    return ceiling_floor(reference_price, band=0.15)


def ceiling_floor(reference_price: float, band: float = 0.07) -> dict:
    """
    Calculate ceiling (giá trần) and floor (giá sàn) from reference price.
    HOSE band = 7%. Ceiling rounds DOWN, floor rounds UP (exchange convention).
    """
    raw_ceil = reference_price * (1 + band)
    raw_floor = reference_price * (1 - band)
    ceil_price = round_to_tick(raw_ceil, "down")
    floor_price = round_to_tick(raw_floor, "up")
    return {
        "reference": round_to_tick(reference_price, "nearest"),
        "ceiling": ceil_price,
        "floor": floor_price,
        "band_pct": band * 100,
    }


def detect_ceiling_run(df, market: str = "HOSE", consecutive_days: int = 2) -> dict:
    """
    Detect stocks hitting the daily price ceiling for N consecutive days.

    A stock closing at the ceiling (giá trần) for 2+ days signals a strong
    demand/supply imbalance — buyers are willing to pay any price. This is a
    breakout signal unique to VN market microstructure.

    Args:
        df: OHLCV DataFrame (DatetimeIndex, columns: open high low close volume).
        market: "HOSE" | "HNX" | "UPCOM"
        consecutive_days: minimum ceiling hits to fire the signal.

    Returns:
        {
          signal: bool,
          days_at_ceiling: int,
          ceiling_price: float,
          label: "CEILING_RUN" | "CEILING_WATCH" | "NONE",
          score_delta: float   (positive = buy signal boost)
        }
    """
    params = _MARKET_PARAMS.get(market.upper(), _MARKET_PARAMS["HOSE"])
    band = params["band"]

    if df is None or len(df) < consecutive_days + 1:
        return {"signal": False, "days_at_ceiling": 0, "label": "NONE", "score_delta": 0.0}

    days_at_ceiling = 0
    for i in range(1, consecutive_days + 2):  # check last N+1 bars
        try:
            ref = float(df["close"].iloc[-(i + 1)])
            close = float(df["close"].iloc[-i])
            cf = ceiling_floor(ref, band)
            if close >= cf["ceiling"] * 0.9995:   # within 1 tick of ceiling
                days_at_ceiling += 1
            else:
                break
        except (IndexError, KeyError):
            break

    ceiling_now = ceiling_floor(float(df["close"].iloc[-2]), band)["ceiling"]
    score_delta = 0.0
    label = "NONE"

    if days_at_ceiling >= consecutive_days + 1:
        label = "CEILING_RUN"
        score_delta = 0.25   # strong breakout signal
    elif days_at_ceiling >= consecutive_days:
        label = "CEILING_WATCH"
        score_delta = 0.12

    return {
        "signal": days_at_ceiling >= consecutive_days,
        "days_at_ceiling": days_at_ceiling,
        "ceiling_price": ceiling_now,
        "label": label,
        "score_delta": score_delta,
    }


def detect_floor_bounce(df, market: str = "HOSE") -> dict:
    """
    Detect a floor-price bounce: stock touched floor (giá sàn) and recovered.

    A close at the floor means sellers have given up at the lowest allowed price.
    If the next session opens higher it signals exhaustion of sellers — a
    contrarian buy signal (applies in context of high volume).

    Returns:
        {
          signal: bool,
          touched_floor: bool,
          bounced: bool,
          floor_price: float,
          label: "FLOOR_BOUNCE" | "FLOOR_TOUCH" | "NONE",
          score_delta: float
        }
    """
    params = _MARKET_PARAMS.get(market.upper(), _MARKET_PARAMS["HOSE"])
    band = params["band"]

    if df is None or len(df) < 3:
        return {"signal": False, "touched_floor": False, "bounced": False,
                "floor_price": 0.0, "label": "NONE", "score_delta": 0.0}

    try:
        ref_prev = float(df["close"].iloc[-3])
        close_prev = float(df["close"].iloc[-2])
        close_now = float(df["close"].iloc[-1])
        floor = ceiling_floor(ref_prev, band)["floor"]

        touched = close_prev <= floor * 1.0005
        bounced = touched and close_now > close_prev * 1.005   # >0.5% recovery

        label = "NONE"
        score_delta = 0.0
        if bounced:
            label = "FLOOR_BOUNCE"
            score_delta = 0.18
        elif touched:
            label = "FLOOR_TOUCH"
            score_delta = 0.08

        return {
            "signal": bounced,
            "touched_floor": touched,
            "bounced": bounced,
            "floor_price": floor,
            "label": label,
            "score_delta": score_delta,
        }
    except (IndexError, KeyError):
        return {"signal": False, "touched_floor": False, "bounced": False,
                "floor_price": 0.0, "label": "NONE", "score_delta": 0.0}


def foreign_ownership_headroom(foreign_held_pct: float, fol_pct: float = 49.0) -> dict:
    """
    Check foreign ownership limit (FOL) headroom.

    Stocks near FOL cap (e.g. banks capped at 30%) behave differently:
    - Near cap: foreigners cannot buy more → price premium may compress
    - Well below cap: foreigners can accumulate freely → buying is more impactful

    Args:
        foreign_held_pct: current foreign ownership as % (e.g. 28.5)
        fol_pct: FOL ceiling for this stock (30.0 for banks, 49.0 default)

    Returns:
        {headroom_pct, is_near_cap, note}
    """
    headroom = fol_pct - foreign_held_pct
    is_near_cap = headroom < 3.0
    note = (
        f"Near FOL cap ({foreign_held_pct:.1f}% / {fol_pct:.0f}%) — "
        "limited foreign buying capacity"
        if is_near_cap
        else f"FOL headroom: {headroom:.1f}% remaining"
    )
    return {"headroom_pct": round(headroom, 1), "is_near_cap": is_near_cap, "note": note}


def market_rules_signal(df, market: str = "HOSE") -> dict:
    """
    Composite VN market microstructure signal for a ticker.

    Runs ceiling run and floor bounce detection and returns a combined
    score_delta and human-readable reason string.

    Returns:
        {score_delta: float, reason: str, ceiling: dict, floor: dict}
    """
    ceiling = detect_ceiling_run(df, market=market)
    floor = detect_floor_bounce(df, market=market)

    score_delta = 0.0
    reasons = []

    if ceiling["signal"]:
        score_delta += ceiling["score_delta"]
        reasons.append(
            f"{ceiling['label']}: {ceiling['days_at_ceiling']}d at ceiling "
            f"{ceiling['ceiling_price']:,.0f}"
        )

    if floor["signal"]:
        score_delta += floor["score_delta"]
        reasons.append(f"{floor['label']}: bounced from floor {floor['floor_price']:,.0f}")

    return {
        "score_delta": round(score_delta, 3),
        "reason": "; ".join(reasons),
        "ceiling": ceiling,
        "floor": floor,
    }


def valid_lo_price(desired_price: float, reference_price: float,
                   band: float = 0.07) -> dict:
    """
    Clamp desired_price to a valid LO price within the ceiling/floor,
    then snap to the nearest tick.

    Returns dict with: price, ceiling, floor, reference, is_clamped, tick
    """
    cf = ceiling_floor(reference_price, band)
    ceil_p = cf["ceiling"]
    floor_p = cf["floor"]

    # Snap to valid tick first
    snapped = round_to_tick(desired_price, "nearest")

    # Clamp to band
    clamped = max(floor_p, min(ceil_p, snapped))
    is_clamped = clamped != snapped

    return {
        "price": clamped,
        "ceiling": ceil_p,
        "floor": floor_p,
        "reference": cf["reference"],
        "tick": tick_size(reference_price),
        "is_clamped": is_clamped,
        "band_pct": band * 100,
    }
