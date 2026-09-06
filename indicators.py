# indicators.py
# Technical Indicators

import math
import numpy as np
import pandas as pd
import config

# ============================================================
# Helpers
# ============================================================
def _series(df, column):
    return pd.to_numeric(df[column], errors="coerce")

# ============================================================
# RSI
# ============================================================
def rsi(df, period=None):
    period = period or config.RSI_PERIOD
    close = _series(df, "close")
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - 100 / (1 + rs)
    result = result.where(result.notna(), np.where(avg_gain > 0, 100, 50))
    return result

# ============================================================
# True Range
# ============================================================
def true_range(df):
    high = _series(df, "high")
    low = _series(df, "low")
    close = _series(df, "close")
    previous_close = close.shift(1)
    return pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs(),
    ], axis=1).max(axis=1)

# ============================================================
# ATR
# ============================================================
def atr(df, period=None):
    period = period or config.ATR_PERIOD
    tr = true_range(df)
    return tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()

# ============================================================
# ADX
# ============================================================
def adx(df, period=None):
    period = period or config.ADX_PERIOD
    high = _series(df, "high")
    low = _series(df, "low")
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where(((up_move > down_move) & (up_move > 0)), up_move, 0.0),
        index=df.index
    )
    minus_dm = pd.Series(
        np.where(((down_move > up_move) & (down_move > 0)), down_move, 0.0),
        index=df.index
    )
    tr = true_range(df)
    atr_value = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/period, adjust=False, min_periods=period).mean() / atr_value.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/period, adjust=False, min_periods=period).mean() / atr_value.replace(0, np.nan)
    denominator = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / denominator
    return dx.ewm(alpha=1/period, adjust=False, min_periods=period).mean()

# ============================================================
# Bollinger Bands
# ============================================================
def bollinger_bands(df, period=None, std_multiplier=None):
    period = period or config.BB_PERIOD
    std_multiplier = std_multiplier or config.BB_STD
    close = _series(df, "close")
    middle = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = middle + std * std_multiplier
    lower = middle - std * std_multiplier
    width = (upper - lower) / middle.replace(0, np.nan)
    return middle, upper, lower, width

# ============================================================
# MACD
# ============================================================
def macd(df, fast=None, slow=None, signal=None):
    fast = fast or config.MACD_FAST
    slow = slow or config.MACD_SLOW
    signal = signal or config.MACD_SIGNAL
    close = _series(df, "close")
    fast_ema = close.ewm(span=fast, adjust=False).mean()
    slow_ema = close.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

# ============================================================
# Momentum
# ============================================================
def momentum(df, period=None):
    period = period or config.MOMENTUM_PERIOD
    close = _series(df, "close")
    return close.pct_change(periods=period) * 100

# ============================================================
# Volume Ratio
# ============================================================
def volume_ratio(df, period=None):
    period = period or config.VOLUME_AVG_PERIOD
    volume = _series(df, "volume")
    average = volume.rolling(period).mean()
    return volume / average.replace(0, np.nan)

# ============================================================
# Daily Pivot
# ============================================================
def pivot_points(df):
    if len(df) < 2:
        return {"pivot": np.nan, "r1": np.nan, "r2": np.nan, "s1": np.nan, "s2": np.nan}
    previous = df.iloc[-2]
    high = float(previous["high"])
    low = float(previous["low"])
    close = float(previous["close"])
    pivot = (high + low + close) / 3
    r1 = 2 * pivot - low
    s1 = 2 * pivot - high
    r2 = pivot + high - low
    s2 = pivot - high + low
    return {"pivot": pivot, "r1": r1, "r2": r2, "s1": s1, "s2": s2}

# ============================================================
# Add all indicators
# ============================================================
def add_indicators(df):
    result = df.copy()
    result["rsi"] = rsi(result)
    result["adx"] = adx(result)
    result["atr"] = atr(result)
    result["bb_middle"], result["bb_upper"], result["bb_lower"], result["bb_width"] = bollinger_bands(result)
    result["macd"], result["macd_signal"], result["macd_hist"] = macd(result)
    result["momentum"] = momentum(result)
    result["volume_ratio"] = volume_ratio(result)
    return result

# ============================================================
# Early Snipe
# ============================================================
def detect_early_snipe(df):
    if len(df) < 25:
        return {"active": False, "direction": None, "score": 0}
    latest = df.iloc[-1]
    close = float(latest["close"])
    bb_width = float(latest["bb_width"])
    vol_ratio = float(latest["volume_ratio"])
    adx_val = float(latest["adx"])
    recent_high = float(df["high"].iloc[-20:].max())
    recent_low = float(df["low"].iloc[-20:].min())
    resistance_distance = (recent_high - close) / close
    support_distance = (close - recent_low) / close
    squeeze = math.isfinite(bb_width) and bb_width <= config.SQUEEZE_BB_WIDTH
    silent_volume = math.isfinite(vol_ratio) and vol_ratio >= config.SILENT_VOLUME_MULTIPLIER
    near_resistance = 0 <= resistance_distance <= config.RESISTANCE_DISTANCE
    near_support = 0 <= support_distance <= config.RESISTANCE_DISTANCE

    previous_close = float(df["close"].iloc[-2])
    if near_resistance and close >= previous_close:
        direction = "BUY"
    elif near_support and close <= previous_close:
        direction = "SELL"
    else:
        return {"active": False, "direction": None, "score": 0}
    if not (squeeze and silent_volume and adx_val > 15.0):
        return {"active": False, "direction": None, "score": 0}
    return {
        "active": True,
        "direction": direction,
        "score": config.EARLY_SNIPE_SCORE,
        "squeeze": squeeze,
        "silent_volume": silent_volume,
        "near_resistance": near_resistance,
        "near_support": near_support,
    }
