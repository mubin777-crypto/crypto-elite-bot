# signals.py - معدل بالكامل مع إصلاح إدارة المخاطر
import logging
import asyncio
from typing import Dict, List, Optional, Tuple

from config import config
from indicators import Indicators
from database import db

logger = logging.getLogger(__name__)

class SignalEngine:
    def __init__(self, symbol: str, data_5m: Dict, data_1h: Dict, data_4h: Dict, stats: Dict):
        self.symbol = symbol
        self.data_5m = data_5m
        self.data_1h = data_1h
        self.data_4h = data_4h
        self.stats = stats
        self.action = "NEUTRAL"
        self._analyze()

    def _analyze(self):
        self.prices = self.data_5m['prices']
        self.highs = self.data_5m['highs']
        self.lows = self.data_5m['lows']
        self.volumes = self.data_5m['volumes']
        self.current_price = self.prices[-1] if self.prices else 0

        self.rsi = Indicators.calculate_rsi(self.prices, config.RSI_PERIOD)
        self.adx = Indicators.calculate_adx(self.highs, self.lows, self.prices, config.ADX_PERIOD)
        self.atr = Indicators.calculate_atr(self.highs, self.lows, self.prices, 14)
        self.macd = Indicators.calculate_macd(self.prices)
        self.bb = Indicators.calculate_bollinger(self.prices)

        self.trend_1h = self._get_trend(self.data_1h['prices'])
        self.trend_4h = self._get_trend(self.data_4h['prices'])
        self.trend_1d = self._get_trend(self.data_4h['prices'])

        if len(self.prices) >= 6 and self.prices[-6] > 0:
            self.change_1h = ((self.prices[-1] - self.prices[-6]) / self.prices[-6]) * 100
        else:
            self.change_1h = 0.0

        avg_volume = sum(self.volumes[-12:]) / 12 if len(self.volumes) >= 12 else 1
        self.volume_ratio = self.volumes[-1] / avg_volume if avg_volume > 0 else 0

        self.volume_spike = self.volume_ratio > 3.0

        price_range = (max(self.prices[-10:]) - min(self.prices[-10:])) / self.current_price if self.current_price > 0 else 0
        self.low_volatility_compression = price_range < 0.01

        self.pivot = Indicators.get_pivot_points(
            self.stats.get('high', self.current_price),
            self.stats.get('low', self.current_price),
            self.stats.get('last', self.current_price)
        )
        self.breakout = Indicators.detect_breakout(self.prices, self.highs, self.lows, self.atr, 20)

    def _get_trend(self, prices: List[float]) -> str:
        if len(prices) < 20:
            return 'neutral'
        sma20 = sum(prices[-20:]) / 20
        sma50 = sum(prices[-50:]) / 50 if len(prices) >= 50 else sma20
        current = prices[-1]
        if current > sma20 and sma20 > sma50:
            return 'bullish'
        elif current < sma20 and sma20 < sma50:
            return 'bearish'
        return 'neutral'

    async def evaluate(self) -> Dict:
        score = 0.0
        reasons = []
        weights = await db.get_factor_weights() if config.ADAPTIVE_THRESHOLD else {}

        # RSI
        if 45 <= self.rsi <= 55:
            score += 4.0
            reasons.append("RSI مثالي")
        elif 30 <= self.rsi < 45 or 55 < self.rsi <= 60:
            score += 3.0
            reasons.append("RSI جيد")
        elif 20 <= self.rsi < 30 or 60 < self.rsi <= 70:
            score += 1.5
            reasons.append("RSI حذر")
        else:
            score += 0.5
            reasons.append("RSI متطرف")
        score *= weights.get('rsi', 1.0)

        # الاتجاه (مع شرط ADX)
        if self.adx < 20:
            reasons.append(f"اتجاه ضعيف (ADX {self.adx:.1f})")
        elif self.trend_1d == 'bullish' and self.trend_4h == 'bullish':
            score += 3.5
            reasons.append("اتجاه صاعد قوي (4H+1D)")
        elif self.trend_4h == 'bullish':
            score += 2.5
            reasons.append("اتجاه صاعد (4H)")
        elif self.trend_1h == 'bullish':
            score += 1.5
            reasons.append("اتجاه صاعد (1H)")
        elif self.trend_1d == 'bearish' and self.trend_4h == 'bearish':
            score += 3.5
            reasons.append("اتجاه هابط قوي (4H+1D)")
        elif self.trend_4h == 'bearish':
            score += 2.5
            reasons.append("اتجاه هابط (4H)")
        elif self.trend_1h == 'bearish':
            score += 1.5
            reasons.append("اتجاه هابط (1H)")
        else:
            reasons.append("اتجاه جانبي")
        score *= weights.get('trend', 1.0)

        # الزخم
        if self.change_1h > 1.5:
            score += 1.5
            reasons.append(f"زخم قوي {self.change_1h:.1f}%")
        elif self.change_1h > 0.5:
            score += 0.8
            reasons.append(f"زخم معتدل {self.change_1h:.1f}%")
        else:
            score += 0.0
            reasons.append("زخم ضعيف")
        score *= weights.get('momentum', 1.0)

        # الحجم
        if self.volume_spike:
            score += 2.0
            reasons.append(f"🚀 انفجار حجم مفاجئ ({self.volume_ratio:.1f}x)")
        elif self.volume_ratio >= 2.0:
            score += 0.7
            reasons.append(f"حجم جيد {self.volume_ratio:.1f}x")
        elif self.volume_ratio >= 1.3:
            score += 0.4
            reasons.append(f"حجم معتدل {self.volume_ratio:.1f}x")
        score *= weights.get('volume', 1.0)

        # ADX (وزن إضافي)
        if self.adx > 30:
            score += 1.0
            reasons.append(f"اتجاه قوي (ADX {self.adx:.1f})")
        elif self.adx > 25:
            score += 0.5
            reasons.append(f"اتجاه متوسط (ADX {self.adx:.1f})")

        # بولينجر
        bb_width = (self.bb['upper'] - self.bb['lower']) / self.bb['middle'] * 100 if self.bb['middle'] > 0 else 0
        if bb_width < 2.0:
            score += 0.5
            reasons.append(f"انضغاط بولينجر ({bb_width:.1f}%)")

        if self.low_volatility_compression:
            score += 1.0
            reasons.append("📉 انضغاط سعري شديد - استعداد للانفجار")

        final_score = round(min(score, 10.0), 1)

        # تحديد الاتجاه والـ action
        if self.trend_1d == 'bullish' and self.trend_4h != 'bearish':
            self.action = "BUY"
        elif self.trend_1d == 'bearish' and self.trend_4h != 'bullish':
            self.action = "SELL"
        else:
            self.action = "NEUTRAL"

        # تحديد نوع الإشارة بناءً على النقاط والاتجاه
        if final_score >= 8.0 and self.action != "NEUTRAL":
            signal_type = "🟢 **شراء قوي**" if self.action == "BUY" else "🔴 **بيع قوي**"
        elif final_score >= 6.5 and self.action != "NEUTRAL":
            signal_type = "🟢 **شراء**" if self.action == "BUY" else "🔴 **بيع**"
        elif final_score >= 4.5:
            signal_type = "🟡 **مراقبة**"
        else:
            signal_type = "⚪ **حيادي**"

        # شروط صارمة للإشارات القابلة للتنفيذ
        is_actionable = (
            final_score >= config.SIGNAL_SCORE_THRESHOLD and 
            self.action != "NEUTRAL" and
            self.adx >= config.MIN_ADX_STRONG and
            abs(self.change_1h) >= config.MIN_CHANGE_1H
        )

        return {
            "symbol": self.symbol,
            "price": self.current_price,
            "rsi": round(self.rsi, 1),
            "adx": round(self.adx, 1),
            "change_1h": round(self.change_1h, 2),
            "volume_ratio": round(self.volume_ratio, 1),
            "score": final_score,
            "signal": signal_type,
            "reasons": reasons,
            "is_actionable": is_actionable,
            "trend_1h": self.trend_1h,
            "trend_4h": self.trend_4h,
            "trend_1d": self.trend_1d,
            "pivot": self.pivot,
            "breakout": self.breakout,
            "bb_width": round(bb_width, 2),
            "volume_spike": self.volume_spike,
            "low_volatility_compression": self.low_volatility_compression,
            "action": self.action
        }

    def calculate_risk(self, entry_price: float, action: str, stop_loss: float = None) -> Tuple[float, float, float]:
        """
        حساب وقف الخسارة، جني الأرباح، وحجم الصفقة
        🔥 تم إصلاح منطق SL/TP للبيع (Short)
        action: 'BUY' أو 'SELL'
        """
        atr_stop = self.atr * 2 if self.atr > 0 else entry_price * 0.015
        min_stop = entry_price * 0.01
        min_tp = entry_price * 0.025
        
        if action == 'BUY':
            # الشراء: SL أسفل، TP أعلى
            if stop_loss is None:
                stop_loss = entry_price - max(atr_stop, min_stop)
            take_profit = entry_price + max(atr_stop * 2, min_tp)
        else:  # SELL
            # 🔥 البيع: SL أعلى، TP أسفل (تم الإصلاح)
            if stop_loss is None:
                stop_loss = entry_price + max(atr_stop, min_stop)
            take_profit = entry_price - max(atr_stop * 2, min_tp)
        
        # حساب حجم الصفقة
        stop_distance = abs(entry_price - stop_loss) / entry_price
        if stop_distance == 0:
            position_size = 0.02
        else:
            risk_amount = config.RISK_PER_TRADE
            position_fraction = risk_amount / stop_distance
            position_size = round(min(position_fraction, config.MAX_POSITION_SIZE_PCT / 100), 4)
        
        return stop_loss, take_profit, position_size


class ConfirmationEngine:
    def __init__(self, signal_engine: SignalEngine):
        self.initial = signal_engine
        self.confirmed = None
        self.wait_candles = config.CONFIRMATION_WAIT_CANDLES

    async def wait_and_confirm(self, session):
        # إذا كان هناك انفجار حجم، تخطي التأكيد
        if self.initial.volume_spike:
            logger.info(f"⚡ انفجار حجم مفاجئ لـ {self.initial.symbol}، تخطي التأكيد")
            from utils import fetch_klines, fetch_24hr_stats
            data_5m = await fetch_klines(session, self.initial.symbol, '5m', 100)
            data_1h = await fetch_klines(session, self.initial.symbol, '1h', 30)
            data_4h = await fetch_klines(session, self.initial.symbol, '4h', 20)
            stats = await fetch_24hr_stats(session, self.initial.symbol)
            if not data_5m or not data_1h or not data_4h:
                return None
            new_engine = SignalEngine(self.initial.symbol, data_5m, data_1h, data_4h, stats)
            new_eval = await new_engine.evaluate()
            if new_eval['is_actionable']:
                self.confirmed = new_eval
                self.confirmed['signal'] = self.confirmed['signal'].replace("مراقبة", "تأكيد سريع")
                return self.confirmed
            else:
                logger.info(f"⚠️ انفجار حجم لـ {self.initial.symbol} لكن الإشارة غير قابلة للتنفيذ")
                return None

        # الانتظار العادي
        wait_seconds = 5 * 60 * self.wait_candles
        logger.info(f"⏳ انتظار {wait_seconds} ثانية لتأكيد إشارة {self.initial.symbol}")
        await asyncio.sleep(wait_seconds)

        from utils import fetch_klines, fetch_24hr_stats
        data_5m = await fetch_klines(session, self.initial.symbol, '5m', 100)
        data_1h = await fetch_klines(session, self.initial.symbol, '1h', 30)
        data_4h = await fetch_klines(session, self.initial.symbol, '4h', 20)
        stats = await fetch_24hr_stats(session, self.initial.symbol)
        if not data_5m or not data_1h or not data_4h:
            return None

        new_engine = SignalEngine(self.initial.symbol, data_5m, data_1h, data_4h, stats)
        new_eval = await new_engine.evaluate()
        if new_eval['score'] >= self.initial.score + config.CONFIRMATION_SCORE_BONUS:
            self.confirmed = new_eval
            self.confirmed['signal'] = self.confirmed['signal'].replace("مراقبة", "تأكيد")
            self.confirmed['signal'] = self.confirmed['signal'].replace("شراء", "تأكيد شراء")
            self.confirmed['signal'] = self.confirmed['signal'].replace("بيع", "تأكيد بيع")
            return self.confirmed
        else:
            logger.info(f"❌ لم يتم تأكيد {self.initial.symbol} (درجة: {new_eval['score']} < {self.initial.score + config.CONFIRMATION_SCORE_BONUS})")
            return None
