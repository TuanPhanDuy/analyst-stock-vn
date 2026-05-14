"""
Risk-based position sizing for HOSE stocks.

Formula (fixed fractional risk):
  risk_vnd       = capital × risk_per_trade_pct
  risk_per_share = entry_price - stop_loss_price
  raw_qty        = risk_vnd / risk_per_share
  qty            = floor(raw_qty / 100) × 100   # round DOWN to HOSE lot of 100

Hard caps:
  max by value   = capital × max_position_pct
  min qty        = 100 shares (1 lot)
"""
import math


LOT = 100   # HOSE minimum tradeable lot


def _floor_lot(qty: float) -> int:
    """Round DOWN to the nearest HOSE lot (100 shares)."""
    if not math.isfinite(qty) or qty <= 0:
        return LOT
    return max(LOT, int(math.floor(qty / LOT)) * LOT)


def calculate(
    entry: float,
    stop_loss: float,
    capital: float,
    risk_per_trade_pct: float = 0.02,
    max_position_pct: float = 0.15,
) -> dict:
    """
    Return sizing recommendation for one trade.
    Returns {"affordable": False} if even 1 lot exceeds capital.
    """
    if entry <= 0 or stop_loss <= 0 or capital <= 0:
        return {"affordable": False, "qty": 0, "error": "Invalid inputs"}

    # Hard gate: if 1 lot costs more than total capital, skip this stock
    min_cost = entry * LOT
    if min_cost > capital:
        return {
            "affordable": False,
            "qty": 0,
            "min_cost": round(min_cost),
            "capital": round(capital),
            "need_more": round(min_cost - capital),
            "sizing_note": (
                f"1 lot = {min_cost:,.0f} ₫ — exceeds your capital of {capital:,.0f} ₫. "
                f"Need {min_cost - capital:,.0f} ₫ more to buy this stock."
            ),
        }

    risk_per_share = entry - stop_loss
    if risk_per_share <= 0:
        risk_per_share = entry * 0.05   # fallback: assume 5% stop

    # Risk-based quantity
    risk_vnd = capital * risk_per_trade_pct
    qty_by_risk = _floor_lot(risk_vnd / risk_per_share)

    # Cap by max position size
    max_value = capital * max_position_pct
    qty_by_cap = _floor_lot(max_value / entry)

    # Also cap by total available capital (can't spend more than you have)
    qty_by_capital = _floor_lot(capital / entry)

    qty = min(qty_by_risk, qty_by_cap, qty_by_capital)
    qty = max(qty, LOT)

    total_cost = qty * entry
    actual_risk = qty * risk_per_share
    actual_risk_pct = actual_risk / capital * 100
    position_pct = total_cost / capital * 100

    if qty_by_capital <= LOT and total_cost > capital * 0.80:
        sizing_note = "Using minimum 1 lot — this takes most of your capital. Consider waiting for more funds."
    elif qty_by_risk > qty_by_cap:
        sizing_note = f"Capped at {max_position_pct*100:.0f}% max position per trade"
    else:
        sizing_note = f"Risk-based: {actual_risk_pct:.1f}% of capital at risk if stop hits"

    return {
        "affordable": True,
        "qty": qty,
        "total_cost": round(total_cost),
        "risk_vnd": round(actual_risk),
        "risk_pct": round(actual_risk_pct, 2),
        "position_pct": round(position_pct, 1),
        "sizing_note": sizing_note,
    }


def size_ranked(ranked: dict, entry_levels: dict, capital: float,
                risk_per_trade_pct: float = 0.02,
                max_position_pct: float = 0.15,
                max_total_risk_pct: float = 0.06) -> dict:
    """
    Attach sizing to each item. Splits buy list into 'buy' (affordable)
    and 'buy_unaffordable' (1 lot > capital or portfolio risk cap hit).
    Does not mutate original.

    max_total_risk_pct: maximum fraction of capital at risk across all open BUY trades.
    Once accumulated risk reaches this threshold, further BUY trades are marked unaffordable.
    """
    import copy
    result = copy.deepcopy(ranked)

    accumulated_risk_pct = 0.0
    affordable, unaffordable = [], []
    for item in result.get("buy", []):
        el = entry_levels.get(item["ticker"], {})
        entry = el.get("entry", item["price"])
        stop = el.get("stop_loss", entry * 0.95)
        sizing = calculate(entry, stop, capital, risk_per_trade_pct, max_position_pct)
        item["sizing"] = sizing

        if not sizing.get("affordable", True):
            unaffordable.append(item)
            continue

        trade_risk_pct = sizing.get("risk_pct", 0.0) / 100
        if accumulated_risk_pct + trade_risk_pct > max_total_risk_pct:
            sizing["affordable"] = False
            sizing["sizing_note"] = (
                f"Portfolio risk cap hit ({accumulated_risk_pct*100:.1f}% of {max_total_risk_pct*100:.0f}% used). "
                "Reduce existing positions before adding more."
            )
            unaffordable.append(item)
        else:
            accumulated_risk_pct += trade_risk_pct
            affordable.append(item)

    result["buy"] = affordable
    result["buy_unaffordable"] = unaffordable

    for item in result.get("sell", []):
        el = entry_levels.get(item["ticker"], {})
        entry = el.get("entry", item["price"])
        stop = el.get("stop_loss", entry * 0.95)
        item["sizing"] = calculate(entry, stop, capital, risk_per_trade_pct, max_position_pct)

    return result
