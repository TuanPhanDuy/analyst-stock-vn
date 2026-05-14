"""Monthly signals: MA trend, golden/death cross, momentum."""
import pandas as pd

from src.indicators.technical import ema, sma, support_resistance, volume_ratio
from src.signals.base import Signal


def analyze(ticker: str, df: pd.DataFrame, cfg: dict) -> Signal:
    close = df["close"]
    volume = df["volume"]
    high = df["high"]
    low = df["low"]
    short = cfg.get("ma_short", 20)
    long = cfg.get("ma_long", 60)
    vol_confirm_ratio = cfg.get("volume_confirmation_ratio", 1.2)
    sr_proximity = cfg.get("sr_proximity_pct", 0.02)

    ma_s = sma(close, short)
    ma_l = sma(close, long)
    ema_short = ema(close, 20)
    ema_long = ema(close, 50)

    reasons = []
    score = 0.0

    # EMA trend confirmation: EMA-20 vs EMA-50 as a score multiplier
    ema_bullish = float(ema_short.iloc[-1]) > float(ema_long.iloc[-1])
    ema_multiplier = 1.10 if ema_bullish else 0.90

    # Volume confirmation for crossovers
    current_vol_ratio = float(volume_ratio(volume).iloc[-1])
    cross_weight = 1.0 if current_vol_ratio >= vol_confirm_ratio else 0.60

    # Golden / death cross (volume-confirmed)
    if ma_s.iloc[-2] < ma_l.iloc[-2] and ma_s.iloc[-1] > ma_l.iloc[-1]:
        cross_score = 0.45 * cross_weight
        score += cross_score
        vol_note = "" if current_vol_ratio >= vol_confirm_ratio else " (weak volume)"
        reasons.append(f"Golden cross MA{short}/MA{long}{vol_note}")
    elif ma_s.iloc[-2] > ma_l.iloc[-2] and ma_s.iloc[-1] < ma_l.iloc[-1]:
        cross_score = -0.45 * cross_weight
        score += cross_score
        vol_note = "" if current_vol_ratio >= vol_confirm_ratio else " (weak volume)"
        reasons.append(f"Death cross MA{short}/MA{long}{vol_note}")

    # Apply EMA trend multiplier to accumulated cross score
    score *= ema_multiplier
    if ema_bullish:
        reasons.append("EMA-20 above EMA-50 (uptrend)")

    # Price above/below long MA
    price_vs_ma = (close.iloc[-1] - ma_l.iloc[-1]) / ma_l.iloc[-1]
    if price_vs_ma > 0.05:
        score += 0.20
        reasons.append(f"Price {price_vs_ma*100:.1f}% above MA{long}")
    elif price_vs_ma < -0.05:
        score -= 0.20
        reasons.append(f"Price {abs(price_vs_ma)*100:.1f}% below MA{long}")

    # 3-month momentum
    if len(close) >= 60:
        momentum = (close.iloc[-1] / close.iloc[-60] - 1)
        if momentum > 0.10:
            score += 0.15
            reasons.append(f"3M momentum +{momentum*100:.1f}%")
        elif momentum < -0.10:
            score -= 0.15
            reasons.append(f"3M momentum {momentum*100:.1f}%")

    # Support / resistance proximity
    if len(close) >= 20:
        sr = support_resistance(high, low, close)
        price = float(close.iloc[-1])
        support = sr["support"]
        resistance = sr["resistance"]
        near_support = abs(price - support) / price <= sr_proximity
        near_resistance = abs(price - resistance) / price <= sr_proximity
        if near_support and score > 0:
            score += 0.10
            reasons.append(f"Near support {support:,.0f}")
        elif near_resistance and score <= 0:
            score -= 0.10
            reasons.append(f"Near resistance {resistance:,.0f}")

    action = "HOLD"
    confidence = abs(score)
    if score > 0.30:
        action = "BUY"
    elif score < -0.30:
        action = "SELL"

    return Signal(
        ticker=ticker,
        timeframe="monthly",
        action=action,
        confidence=min(confidence, 1.0),
        reason="; ".join(reasons) or "No clear signal",
        price=float(close.iloc[-1]),
    )
