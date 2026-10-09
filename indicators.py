# indicators.py
# Technical Indicators v2026.3 - With Kaufman ER + Market Regime

import math
import numpy as np
import pandas as pd
import config


def _series(df, column):
    return pd.to_numeric(df[column], errors="coerce")


# ============================================================
# Base Indicators
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
    return result.where(result.notna(), np.where(avg_gain > 0, 100, 50))


def true_range(df):
    high = _series(df, "high")
    low = _series(df, "low")
    close = _series(df, "close")
    prev_close = close.shift(1)
    return pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(df, period=None):
    period = period or config.ATR_PERIOD
    return true_range(df).ewm(alpha=1/period, adjust=False, min_periods=period).mean()


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


def keltner_channels(df, period=None, atr_mult=None):
    period = period or config.KELTNER_PERIOD
    atr_mult = atr_mult or config.KELTNER_ATR_MULT
    close = _series(df, "close")
    ema = close.ewm(span=period, adjust=False).mean()
    atr_value = atr(df, period)
    upper = ema + atr_value * atr_mult
    lower = ema - atr_value * atr_mult
    return ema, upper, lower


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


def momentum(df, period=None):
    period = period or config.MOMENTUM_PERIOD
    return _series(df, "close").pct_change(periods=period) * 100


def volume_ratio(df, period=None):
    period = period or config.VOLUME_AVG_PERIOD
    volume = _series(df, "volume")
    average = volume.rolling(period).mean()
    return volume / average.replace(0, np.nan)


def pivot_points(df):
    if len(df) < 2:
        return {"pivot": np.nan, "r1": np.nan, "r2": np.nan, "s1": np.nan, "s2": np.nan}
    prev = df.iloc[-2]
    high = float(prev["high"])
    low = float(prev["low"])
    close = float(prev["close"])
    pivot = (high + low + close) / 3
    r1 = 2 * pivot - low
    s1 = 2 * pivot - high
    r2 = pivot + high - low
    s2 = pivot - high + low
    return {"pivot": pivot, "r1": r1, "r2": r2, "s1": s1, "s2": s2}


# ============================================================
# 🆕 Kaufman Efficiency Ratio (من binance-futures-ai-bot)[reference:31]
# ============================================================
def efficiency_ratio(df, period=None):
    """
    Kaufman Efficiency Ratio:
    ER = |Close_n - Close_0| / Sum(|Close_i - Close_{i-1}|)
    ER > 0.35 = ترند قوي
    ER < 0.20 = سوق متذبذب (تجنبه)
    """
    period = period or config.ER_PERIOD
    close = _series(df, "close")
    change = close.diff().abs()
    volatility = change.rolling(period).sum()
    direction = (close - close.shift(period)).abs()
    er = direction / volatility.replace(0, np.nan)
    return er


# ============================================================
# 🆕 Market Regime Detection (من binance-futures-ai-bot)[reference:32]
# ============================================================
def detect_market_regime(df):
    """
    يحدد حالة السوق: TRENDING / RANGING / CHOPPY
    - TRENDING: ADX > 25 + ER > 0.35
    - RANGING: ADX < 20 أو ER < 0.20
    - NEUTRAL: ما بينهما
    """
    if len(df) < 50:
        return {"regime": "UNKNOWN", "adx": 0, "er": 0}

    latest = df.iloc[-1]
    adx_val = float(latest.get("adx", 0))
    er_val = float(latest.get("efficiency_ratio", 0))

    if adx_val >= config.REGIME_ADX_TRENDING and er_val >= config.REGIME_ER_TRENDING:
        regime = "TRENDING"
    elif adx_val < config.REGIME_ADX_RANGING or er_val < config.REGIME_ER_CHOPPY:
        regime = "RANGING"
    else:
        regime = "NEUTRAL"

    return {"regime": regime, "adx": adx_val, "er": er_val}


# ============================================================
# Add All Indicators
# ============================================================
def add_indicators(df):
    result = df.copy()
    result["rsi"] = rsi(result)
    result["adx"] = adx(result)
    result["atr"] = atr(result)
    result["bb_middle"], result["bb_upper"], result["bb_lower"], result["bb_width"] = bollinger_bands(result)
    result["kc_middle"], result["kc_upper"], result["kc_lower"] = keltner_channels(result)
    result["macd"], result["macd_signal"], result["macd_hist"] = macd(result)
    result["momentum"] = momentum(result)
    result["volume_ratio"] = volume_ratio(result)
    result["efficiency_ratio"] = efficiency_ratio(result)   # 🆕
    return result


# ============================================================
# Explosion Detection (Strict)
# ============================================================
def detect_squeeze(df) -> dict:
    min_required = max(config.BB_PERIOD, config.KELTNER_PERIOD) + 30
    if len(df) < min_required:
        return {"active": False, "reason": "insufficient_data"}

    latest = df.iloc[-1]
    bb_upper = float(latest["bb_upper"])
    bb_lower = float(latest["bb_lower"])
    kc_upper = float(latest["kc_upper"])
    kc_lower = float(latest["kc_lower"])

    if not all(np.isfinite([bb_upper, bb_lower, kc_upper, kc_lower])):
        return {"active": False, "reason": "nan_values"}

    squeeze_on = bb_upper < kc_upper and bb_lower > kc_lower

    squeeze_count = 0
    check_range = min(config.SQUEEZE_MIN_CANDLES + 2, len(df))
    for i in range(1, check_range):
        row = df.iloc[-i]
        if (float(row["bb_upper"]) < float(row["kc_upper"]) and
                float(row["bb_lower"]) > float(row["kc_lower"])):
            squeeze_count += 1

    bb_width_series = df["bb_width"].dropna()
    if len(bb_width_series) < 30:
        return {"active": False, "reason": "insufficient_width_data"}

    bb_width_current = float(bb_width_series.iloc[-1])
    lookback = min(100, len(bb_width_series))
    # ✅ إصلاح Bug #3: استثنِ القيمة الحالية من العتبة
    threshold = float(bb_width_series.iloc[-(lookback + 1):-1].quantile(0.20))
    bb_width_ok = bb_width_current <= threshold

    is_squeeze = (
        squeeze_on
        and squeeze_count >= config.SQUEEZE_MIN_CANDLES
        and bb_width_ok
    )

    return {
        "active": is_squeeze,
        "squeeze_on": squeeze_on,
        "squeeze_count": squeeze_count,
        "bb_width": bb_width_current,
        "bb_width_threshold": threshold,
        "bb_width_ok": bb_width_ok,
    }


def detect_consolidation(df) -> dict:
    lookback = 20
    if len(df) < lookback + 5:
        return {"active": False, "reason": "insufficient_data"}

    recent = df.iloc[-lookback:]
    high_max = float(recent["high"].max())
    low_min = float(recent["low"].min())

    if low_min <= 0:
        return {"active": False, "reason": "invalid_low"}

    range_pct = (high_max - low_min) / low_min
    lows = recent["low"].values
    higher_lows = 0
    for i in range(2, len(lows)):
        if lows[i] > lows[i-1] and lows[i-1] > lows[i-2]:
            higher_lows += 1

    return {
        "active": range_pct <= config.CONSOLIDATION_RANGE_MAX,
        "range_pct": range_pct,
        "higher_lows": higher_lows,
        "higher_lows_ok": higher_lows >= config.HIGHER_LOWS_COUNT - 1,
    }


def detect_volume_buildup(df) -> dict:
    period = config.VOLUME_AVG_PERIOD
    if len(df) < period + 10:
        return {"active": False, "reason": "insufficient_data"}

    volume = _series(df, "volume")
    avg_20 = volume.rolling(period).mean()
    latest_avg = float(avg_20.iloc[-1])
    if latest_avg <= 0:
        return {"active": False, "reason": "invalid_avg"}

    current_ratio = float(df["volume_ratio"].iloc[-1])
    volume_high = current_ratio >= config.SILENT_VOLUME_MULTIPLIER

    recent_avg_3 = float(volume.iloc[-3:].mean())
    recent_ratio = recent_avg_3 / latest_avg
    trend_up = recent_ratio >= config.VOLUME_TREND_MULTIPLIER

    consecutive_high = 0
    ratios = df["volume_ratio"].iloc[-config.VOLUME_MIN_CONSECUTIVE:]
    for r in ratios:
        if np.isfinite(r) and float(r) >= 1.0:
            consecutive_high += 1

    return {
        "active": volume_high,
        "current_ratio": current_ratio,
        "recent_ratio": recent_ratio,
        "trend_up": trend_up,
        "consecutive_high": consecutive_high,
        "volume_confirmed": volume_high and (
            trend_up or consecutive_high >= config.VOLUME_MIN_CONSECUTIVE
        ),
    }


def detect_breakout_proximity(df) -> dict:
    lookback = 20
    if len(df) < lookback + 2:
        return {"active": False, "reason": "insufficient_data"}

    recent = df.iloc[-lookback:-1]
    recent_high = float(recent["high"].max())
    recent_low = float(recent["low"].min())
    close = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])

    if recent_high <= 0 or recent_low <= 0:
        return {"active": False, "reason": "invalid_values"}

    resistance_dist = (recent_high - close) / close
    support_dist = (close - recent_low) / close

    near_resistance = 0 <= resistance_dist <= config.RESISTANCE_DISTANCE
    near_support = 0 <= support_dist <= config.RESISTANCE_DISTANCE

    breakout_up = (
        close > recent_high * (1 + config.BREAKOUT_CONFIRMATION_PCT)
        and close > prev_close
    )
    breakout_down = (
        close < recent_low * (1 - config.BREAKOUT_CONFIRMATION_PCT)
        and close < prev_close
    )

    direction = None
    if near_resistance or breakout_up:
        direction = "BUY"
    elif near_support or breakout_down:
        direction = "SELL"

    return {
        "active": near_resistance or near_support or breakout_up or breakout_down,
        "direction": direction,
        "near_resistance": near_resistance,
        "near_support": near_support,
        "breakout_up": breakout_up,
        "breakout_down": breakout_down,
        "resistance_dist": resistance_dist,
        "support_dist": support_dist,
    }


def detect_explosion_setup(df) -> dict:
    if len(df) < 60:
        return {"active": False, "reason": "insufficient_data"}

    squeeze = detect_squeeze(df)
    consolidation = detect_consolidation(df)
    volume = detect_volume_buildup(df)
    breakout = detect_breakout_proximity(df)

    conditions_met = 0
    details = {}

    if squeeze.get("active"):
        conditions_met += 1
        details["squeeze"] = True
    if consolidation.get("active"):
        conditions_met += 1
        details["consolidation"] = True
    if volume.get("volume_confirmed"):
        conditions_met += 1
        details["volume_buildup"] = True
    if breakout.get("active"):
        conditions_met += 1
        details["breakout_proximity"] = True

    direction = breakout.get("direction")
    is_explosion = conditions_met >= 4 and direction is not None

    if is_explosion:
        base = 7.0
        if squeeze.get("squeeze_count", 0) >= config.SQUEEZE_MIN_CANDLES + 1:
            base += 0.5
        vol_ratio = volume.get("current_ratio", 1.0)
        if vol_ratio >= 2.5:
            base += 0.5
        elif vol_ratio >= 2.0:
            base += 0.25
        explosion_score = round(min(base, 8.5), 2)
    else:
        explosion_score = 0.0

    return {
        "active": is_explosion,
        "direction": direction,
        "score": explosion_score,
        "conditions_met": conditions_met,
        "details": details,
        "squeeze_data": squeeze,
        "consolidation_data": consolidation,
        "volume_data": volume,
        "breakout_data": breakout,
    }


def detect_early_snipe(df):
    explosion = detect_explosion_setup(df)
    if not explosion["active"]:
        return {"active": False, "direction": None, "score": 0, "details": {}}
    return {
        "active": True,
        "direction": explosion["direction"],
        "score": explosion["score"],
        "details": explosion["details"],
        "explosion_data": explosion,
    }
