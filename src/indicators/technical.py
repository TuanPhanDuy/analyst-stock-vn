"""Core technical indicators operating on OHLCV DataFrames."""
import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(close: pd.Series, period: int = 20, std_dev: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return mid + std_dev * std, mid, mid - std_dev * std  # upper, mid, lower


def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """Current volume / rolling average — spike detection."""
    return volume / volume.rolling(period).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
               k_period: int = 14, d_period: int = 3):
    """Stochastic %K and %D. Both 0-100."""
    lowest_low = low.rolling(k_period).min()
    highest_high = high.rolling(k_period).max()
    k = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    d = k.rolling(d_period).mean()
    return k, d


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index — trend strength 0-100. >25 = trending."""
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr_val = atr(high, low, close, period)
    plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr_val.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr_val.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(span=period, adjust=False).mean()


def support_resistance(high: pd.Series, low: pd.Series, close: pd.Series,
                       lookback: int = 20) -> dict:
    """Recent support (swing low) and resistance (swing high) levels."""
    recent_high = high.iloc[-lookback:].max()
    recent_low = low.iloc[-lookback:].min()
    pivot = (recent_high + recent_low + close.iloc[-1]) / 3
    support1 = 2 * pivot - recent_high
    resistance1 = 2 * pivot - recent_low
    return {
        "support": round(float(support1), 0),
        "resistance": round(float(resistance1), 0),
        "pivot": round(float(pivot), 0),
        "recent_high": round(float(recent_high), 0),
        "recent_low": round(float(recent_low), 0),
    }


def vwap(high: pd.Series, low: pd.Series, close: pd.Series,
         volume: pd.Series, period: int = 20) -> pd.Series:
    """
    Rolling daily VWAP approximation using (H+L+C)/3 × volume weighted over `period` bars.
    NOTE: This is a daily-bar approximation, not intraday VWAP. Use for trend context only.
    """
    typical_price = (high + low + close) / 3
    tp_vol = typical_price * volume
    vol_sum = volume.rolling(period).sum().replace(0, float("nan"))
    return tp_vol.rolling(period).sum() / vol_sum


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume: cumulative buying/selling pressure indicator."""
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (direction * volume).cumsum()


def macd_rsi_divergence(close: pd.Series, lookback: int = 10) -> dict:
    """
    Detect bullish/bearish divergence between price and RSI.
    Bullish: price makes lower low but RSI makes higher low (potential reversal up).
    Bearish: price makes higher high but RSI makes lower high (potential reversal down).
    """
    if len(close) < lookback + 14:
        return {"bullish_divergence": False, "bearish_divergence": False}

    rsi_series = rsi(close)
    recent_close = close.iloc[-lookback:]
    recent_rsi = rsi_series.iloc[-lookback:]

    price_low_idx = recent_close.idxmin()
    price_high_idx = recent_close.idxmax()

    prev_close = close.iloc[-(lookback * 2):-lookback]
    prev_rsi = rsi_series.iloc[-(lookback * 2):-lookback]

    if prev_close.empty or prev_rsi.empty:
        return {"bullish_divergence": False, "bearish_divergence": False}

    prev_low = float(prev_close.min())
    prev_rsi_low = float(prev_rsi.min())
    curr_low = float(recent_close.min())
    curr_rsi_low = float(recent_rsi.min())

    prev_high = float(prev_close.max())
    prev_rsi_high = float(prev_rsi.max())
    curr_high = float(recent_close.max())
    curr_rsi_high = float(recent_rsi.max())

    bullish = curr_low < prev_low and curr_rsi_low > prev_rsi_low
    bearish = curr_high > prev_high and curr_rsi_high < prev_rsi_high

    return {"bullish_divergence": bullish, "bearish_divergence": bearish}


def entry_exit_levels(df: pd.DataFrame, stop_mult: float = 1.5, target_mult: float = 3.0) -> dict:
    """Suggest entry zone, stop loss, and target based on ATR and S/R."""
    close = df["close"]
    high = df["high"]
    low = df["low"]

    atr_val = float(atr(high, low, close).iloc[-1])
    price = float(close.iloc[-1])
    if not np.isfinite(atr_val) or atr_val == 0:
        atr_val = price * 0.02  # fallback: 2% of price
    sr = support_resistance(high, low, close)

    entry = price
    stop_loss = round(price - stop_mult * atr_val, 0)
    target = round(price + target_mult * atr_val, 0)
    risk_pct = round((price - stop_loss) / price * 100, 1)
    reward_pct = round((target - price) / price * 100, 1)

    return {
        "entry": entry,
        "stop_loss": stop_loss,
        "target": target,
        "atr": round(atr_val, 0),
        "risk_pct": risk_pct,
        "reward_pct": reward_pct,
        "support": sr["support"],
        "resistance": sr["resistance"],
    }
