"""Daily buy/sell signals: RSI, MACD, Stochastic, ADX, volume spike, Bollinger, VWAP, divergence."""
import numpy as np
import pandas as pd

from src.indicators.technical import (
    adx, bollinger_bands, macd, macd_rsi_divergence, rsi, stochastic, vwap, volume_ratio
)
from src.signals.base import Signal


def analyze(ticker: str, df: pd.DataFrame, cfg: dict) -> Signal:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    rsi_val = float(rsi(close).iloc[-1])
    macd_line, signal_line, hist = macd(close)
    stoch_k, stoch_d = stochastic(high, low, close)
    adx_val = float(adx(high, low, close).iloc[-1])
    vol_r = float(volume_ratio(volume).iloc[-1])
    upper_bb, mid_bb, lower_bb = bollinger_bands(close)

    reasons = []
    score = 0.0
    trend_confirmed = adx_val > 25   # trending market — signals carry more weight

    # ── RSI ───────────────────────────────────────────────────────────────────
    if rsi_val < cfg["rsi_oversold"]:
        weight = 0.30 if not trend_confirmed else 0.25  # oversold in a trend = dangerous
        score += weight
        reasons.append(f"RSI oversold ({rsi_val:.1f})")
    elif rsi_val > cfg["rsi_overbought"]:
        weight = 0.30 if not trend_confirmed else 0.25
        score -= weight
        reasons.append(f"RSI overbought ({rsi_val:.1f})")

    # ── MACD crossover ────────────────────────────────────────────────────────
    prev_diff = float(macd_line.iloc[-2]) - float(signal_line.iloc[-2])
    curr_diff = float(macd_line.iloc[-1]) - float(signal_line.iloc[-1])
    if prev_diff < 0 < curr_diff:
        score += 0.25
        reasons.append("MACD bullish crossover")
    elif prev_diff > 0 > curr_diff:
        score -= 0.25
        reasons.append("MACD bearish crossover")

    # ── Stochastic ────────────────────────────────────────────────────────────
    stoch_k_val = float(stoch_k.iloc[-1])
    stoch_d_val = float(stoch_d.iloc[-1])
    prev_k = float(stoch_k.iloc[-2])
    prev_d = float(stoch_d.iloc[-2])
    if stoch_k_val < 20 and stoch_d_val < 20:
        score += 0.20
        reasons.append(f"Stochastic oversold (K={stoch_k_val:.0f})")
    elif stoch_k_val > 80 and stoch_d_val > 80:
        score -= 0.20
        reasons.append(f"Stochastic overbought (K={stoch_k_val:.0f})")
    # Stoch bullish crossover inside oversold zone
    if prev_k < prev_d and stoch_k_val > stoch_d_val and stoch_k_val < 30:
        score += 0.15
        reasons.append("Stochastic bullish cross (oversold)")
    elif prev_k > prev_d and stoch_k_val < stoch_d_val and stoch_k_val > 70:
        score -= 0.15
        reasons.append("Stochastic bearish cross (overbought)")

    # ── Bollinger Band touch ──────────────────────────────────────────────────
    price = float(close.iloc[-1])
    lb = float(lower_bb.iloc[-1])
    ub = float(upper_bb.iloc[-1])
    if price <= lb:
        score += 0.15
        reasons.append("Price at lower Bollinger Band")
    elif price >= ub:
        score -= 0.15
        reasons.append("Price at upper Bollinger Band")

    # ── Volume spike amplifier ────────────────────────────────────────────────
    if vol_r > cfg["volume_spike_ratio"]:
        score = score * 1.25 if score > 0 else score * 0.75
        reasons.append(f"Volume spike x{vol_r:.1f}")

    # ── ADX context note ─────────────────────────────────────────────────────
    if trend_confirmed and abs(score) > 0.20:
        score *= 1.10   # trending market boosts signal reliability
        reasons.append(f"ADX {adx_val:.0f} (trending)")

    # ── RSI-price divergence ──────────────────────────────────────────────────
    divergence = macd_rsi_divergence(close)
    if divergence["bullish_divergence"]:
        score += 0.20
        reasons.append("Bullish RSI divergence (potential reversal up)")
    elif divergence["bearish_divergence"]:
        score -= 0.20
        reasons.append("Bearish RSI divergence (potential reversal down)")

    # ── VWAP position ─────────────────────────────────────────────────────────
    vwap_val = float(vwap(high, low, close, volume).iloc[-1])
    if not np.isnan(vwap_val):
        if price > vwap_val:
            score += 0.05
            reasons.append(f"Price above VWAP ({vwap_val:,.0f})")
        elif price < vwap_val:
            score -= 0.05
            reasons.append(f"Price below VWAP ({vwap_val:,.0f})")

    action = "HOLD"
    confidence = min(abs(score), 1.0)
    if score > 0.25:
        action = "BUY"
    elif score < -0.25:
        action = "SELL"

    return Signal(
        ticker=ticker,
        timeframe="daily",
        action=action,
        confidence=confidence,
        reason="; ".join(reasons) or "No clear daily signal",
        price=price,
    )
