"""
indicators.py - محرك المؤشرات الفنية (نسخة أصلية متكاملة).
"""
import numpy as np
import pandas as pd
from typing import Dict, Tuple
from config import CFG

class TechnicalIndicators:
    @staticmethod
    def rsi(df: pd.DataFrame, period: int = 6) -> pd.Series:
        close = df["close"]
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50)

    @staticmethod
    def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period, min_periods=period).mean()
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr.replace(0, np.nan))
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr.replace(0, np.nan))
        dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0)
        adx = dx.rolling(window=period, min_periods=period).mean()
        return adx.fillna(0)

    @staticmethod
    def bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> Dict[str, pd.Series]:
        close = df["close"]
        sma = close.rolling(window=period, min_periods=period).mean()
        std = close.rolling(window=period, min_periods=period).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        bandwidth = (upper - lower) / sma.replace(0, np.nan)
        squeeze = bandwidth < CFG.BB_SQUEEZE_THRESHOLD
        return {
            "middle": sma,
            "upper": upper,
            "lower": lower,
            "bandwidth": bandwidth,
            "squeeze": squeeze,
        }

    @staticmethod
    def sma(df: pd.DataFrame, period: int) -> pd.Series:
        return df["close"].rolling(window=period, min_periods=period).mean()

    @staticmethod
    def macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, pd.Series]:
        close = df["close"]
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": histogram,
        }

    @staticmethod
    def momentum(df: pd.DataFrame, period: int = 6) -> pd.Series:
        close = df["close"]
        momentum = ((close - close.shift(period)) / close.shift(period).replace(0, np.nan)) * 100
        return momentum.fillna(0)

    @staticmethod
    def volume_ratio(df: pd.DataFrame, period: int = 12) -> pd.Series:
        volume = df["volume"]
        avg_volume = volume.rolling(window=period, min_periods=period).mean()
        ratio = volume / avg_volume.replace(0, np.nan)
        return ratio.fillna(1.0)

    @staticmethod
    def pivot_points(df: pd.DataFrame) -> Dict[str, float]:
        if len(df) < 2:
            return {"pivot": 0, "r1": 0, "r2": 0, "s1": 0, "s2": 0}
        last = df.iloc[-2]
        high = last["high"]
        low = last["low"]
        close = last["close"]
        pivot = (high + low + close) / 3
        r1 = (2 * pivot) - low
        r2 = pivot + (high - low)
        s1 = (2 * pivot) - high
        s2 = pivot - (high - low)
        return {"pivot": pivot, "r1": r1, "r2": r2, "s1": s1, "s2": s2}

    @classmethod
    def compute_all(cls, df: pd.DataFrame) -> Dict[str, any]:
        if len(df) < CFG.SMA_SLOW + 10:
            return {}
        bb = cls.bollinger_bands(df, CFG.BB_PERIOD, CFG.BB_STD)
        macd_data = cls.macd(df, CFG.MACD_FAST, CFG.MACD_SLOW, CFG.MACD_SIGNAL)
        return {
            "rsi": cls.rsi(df, CFG.RSI_PERIOD),
            "adx": cls.adx(df, CFG.ADX_PERIOD),
            "bb": bb,
            "sma20": cls.sma(df, CFG.SMA_FAST),
            "sma50": cls.sma(df, CFG.SMA_SLOW),
            "macd": macd_data,
            "momentum": cls.momentum(df, CFG.MOMENTUM_PERIOD),
            "volume_ratio": cls.volume_ratio(df, CFG.VOLUME_AVG_PERIOD),
            "pivots": cls.pivot_points(df),
        }
