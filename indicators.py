# indicators.py - معدل مع ATR الديناميكي
import math
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

class Indicators:
    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(prices)):
            diff = prices[i] - prices[i-1]
            gains.append(diff if diff >= 0 else 0.0)
            losses.append(abs(diff) if diff < 0 else 0.0)
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 0.0
        tr_list = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            tr_list.append(tr)
        if len(tr_list) < period:
            return 0.0
        return sum(tr_list[-period:]) / period

    @staticmethod
    def calculate_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        if len(closes) < period * 2 + 1:
            return 0.0
        plus_dm, minus_dm, tr_list = [], [], []
        for i in range(1, len(closes)):
            up = highs[i] - highs[i-1]
            down = lows[i-1] - lows[i]
            if up > down and up > 0:
                plus_dm.append(up); minus_dm.append(0.0)
            elif down > up and down > 0:
                plus_dm.append(0.0); minus_dm.append(down)
            else:
                plus_dm.append(0.0); minus_dm.append(0.0)
            tr_list.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])))
        atr = sum(tr_list[:period]) / period
        plus_smooth = sum(plus_dm[:period]) / period
        minus_smooth = sum(minus_dm[:period]) / period
        dx_values = []
        plus_di = (plus_smooth / atr) * 100 if atr > 0 else 0
        minus_di = (minus_smooth / atr) * 100 if atr > 0 else 0
        dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
        dx_values.append(dx)
        for i in range(period, len(tr_list)):
            atr = (atr * (period - 1) + tr_list[i]) / period
            plus_smooth = (plus_smooth * (period - 1) + plus_dm[i]) / period
            minus_smooth = (minus_smooth * (period - 1) + minus_dm[i]) / period
            plus_di = (plus_smooth / atr) * 100 if atr > 0 else 0
            minus_di = (minus_smooth / atr) * 100 if atr > 0 else 0
            dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
            dx_values.append(dx)
        if len(dx_values) < period:
            return dx_values[-1] if dx_values else 0.0
        return sum(dx_values[-period:]) / period

    @staticmethod
    def calculate_sma(prices: List[float], period: int = 20) -> float:
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        return sum(prices[-period:]) / period

    @staticmethod
    def calculate_ema(prices: List[float], period: int) -> float:
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        multiplier = 2 / (period + 1)
        ema = prices[0]
        for price in prices[1:]:
            ema = (price - ema) * multiplier + ema
        return ema

    @staticmethod
    def calculate_macd(prices: List[float], short=12, long=26, signal=9) -> Dict:
        if len(prices) < long:
            return {"histogram": 0.0}
        ema_short = Indicators.calculate_ema(prices, short)
        ema_long = Indicators.calculate_ema(prices, long)
        return {"histogram": ema_short - ema_long}

    @staticmethod
    def calculate_bollinger(prices: List[float], period=20, std=2) -> Dict:
        if len(prices) < period:
            return {"upper": 0.0, "middle": 0.0, "lower": 0.0}
        sma = sum(prices[-period:]) / period
        variance = sum((p - sma) ** 2 for p in prices[-period:]) / period
        std_dev = math.sqrt(variance)
        return {"upper": sma + std_dev * std, "middle": sma, "lower": sma - std_dev * std}

    @staticmethod
    def get_pivot_points(high: float, low: float, close: float) -> Dict:
        pivot = (high + low + close) / 3
        r1 = 2 * pivot - low
        r2 = pivot + (high - low)
        s1 = 2 * pivot - high
        s2 = pivot - (high - low)
        return {"pivot": pivot, "resistance": [r1, r2], "support": [s1, s2]}

    @staticmethod
    def detect_breakout(prices: List[float], highs: List[float], lows: List[float], atr: float, lookback: int = 20) -> Optional[str]:
        """تحسين عتبة الاختراق باستخدام ATR بدلاً من النسبة الثابتة"""
        if len(prices) < lookback + 1 or atr <= 0:
            return None
        recent_high = max(highs[-lookback:-1])
        recent_low = min(lows[-lookback:-1])
        current = prices[-1]
        # عتبة ديناميكية تعتمد على ATR
        threshold = max(atr / current, 0.005)  # على الأقل 0.5%
        if current > recent_high * (1 + threshold):
            return "BREAKOUT"
        elif current < recent_low * (1 - threshold):
            return "BREAKDOWN"
        return None
