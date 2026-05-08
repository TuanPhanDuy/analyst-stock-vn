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
