# signals.py - النسخة المعدلة بالكامل
import logging
import asyncio
from datetime import datetime, timedelta, timezone
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

        self.pivot = Indicators.get_pivot_points(
            self.stats.get('high', self.current_price),
            self.stats.get('low', self.current_price),
            self.stats.get('last', self.current_price)
        )
        self.breakout = Indicators.detect_breakout(self.prices, self.highs, self.lows, 20)

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

    async def evaluate(self) -> Dict:  # 🔥 تم تعديلها إلى async
        score = 0.0
        reasons = []
        weights = await db.get_factor_weights() if config.ADAPTIVE_THRESHOLD else {}

        # RSI
        rsi_score = 0.0
        if 45 <= self.rsi <= 55:
            rsi_score = 4.0
            reasons.append("RSI مثالي")
        elif 30 <= self.rsi < 45 or 55 < self.rsi <= 60:
            rsi_score = 3.0
            reasons.append("RSI جيد")
        elif 20 <= self.rsi < 30 or 60 < self.rsi <= 70:
            rsi_score = 1.5
            reasons.append("RSI حذر")
        else:
            rsi_score = 0.5
            reasons.append("RSI متطرف")
        score += rsi_score * weights.get('rsi', 1.0)

        # الاتجاه
        trend_score = 0.0
        if self.trend_1d == 'bullish' and self.trend_4h == 'bullish':
            trend_score = 3.5
            reasons.append("اتجاه صاعد قوي (4H+1D)")
        elif self.trend_4h == 'bullish':
            trend_score = 2.5
            reasons.append("اتجاه صاعد (4H)")
        elif self.trend_1h == 'bullish':
            trend_score = 1.5
            reasons.append("اتجاه صاعد (1H)")
        elif self.trend_1d == 'bearish' and self.trend_4h == 'bearish':
            trend_score = -1.0
            reasons.append("اتجاه هابط قوي")
        else:
            reasons.append("اتجاه جانبي")
        score += trend_score * weights.get('trend', 1.0)

        # الزخم
        mom_score = 0.0
        if self.change_1h > 1.5:
            mom_score = 1.5
            reasons.append(f"زخم قوي {self.change_1h:.1f}%")
        elif self.change_1h > 0.5:
            mom_score = 0.8
            reasons.append(f"زخم معتدل {self.change_1h:.1f}%")
        else:
            reasons.append("زخم ضعيف")
        score += mom_score * weights.get('momentum', 1.0)

        # الحجم
        vol_score = 0.0
        if self.volume_ratio >= 3.0:
            vol_score = 1.0
            reasons.append(f"حجم ضخم {self.volume_ratio:.1f}x")
        elif self.volume_ratio >= 2.0:
            vol_score = 0.7
            reasons.append(f"حجم جيد {self.volume_ratio:.1f}x")
        elif self.volume_ratio >= 1.3:
            vol_score = 0.4
            reasons.append(f"حجم معتدل {self.volume_ratio:.1f}x")
        score += vol_score * weights.get('volume', 1.0)

        # ADX
        adx_score = 0.0
        if self.adx > 30:
            adx_score = 1.0
            reasons.append(f"اتجاه قوي (ADX {self.adx:.1f})")
        elif self.adx > 20:
            adx_score = 0.5
            reasons.append(f"اتجاه متوسط (ADX {self.adx:.1f})")
        score += adx_score * weights.get('adx', 1.0)

        # بولينجر
        bb_width = (self.bb['upper'] - self.bb['lower']) / self.bb['middle'] * 100 if self.bb['middle'] > 0 else 0
        if bb_width < 2.0:
            score += 0.5
            reasons.append(f"انضغاط بولينجر ({bb_width:.1f}%)")

        final_score = round(min(score, 10.0), 1)

        if final_score >= 7.0:
            signal_type = "🟢 **شراء قوي**" if self.change_1h > 0 else "🔴 **بيع قوي**"
        elif final_score >= 5.5:
            signal_type = "🟢 **شراء**" if self.change_1h > 0 else "🔴 **بيع**"
        elif final_score >= 4.5:
            signal_type = "🟡 **مراقبة**"
        else:
            signal_type = "⚪ **حيادي**"

        is_actionable = final_score >= config.SIGNAL_SCORE_THRESHOLD

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
            "bb_width": round(bb_width, 2)
        }

    def calculate_risk(self, entry_price: float, stop_loss: float = None) -> Tuple[float, float, float]:
        atr_stop = self.atr * 2 if self.atr > 0 else entry_price * 0.015
        if stop_loss is None:
            stop_loss = entry_price - max(atr_stop, entry_price * 0.01)
        take_profit = entry_price + max(atr_stop * 2, entry_price * 0.025)
        stop_loss_pct = abs(entry_price - stop_loss) / entry_price
        if stop_loss_pct == 0:
            position_size = 0.02
        else:
            risk_amount = config.RISK_PER_TRADE
            position_fraction = risk_amount / stop_loss_pct
            position_size = round(min(position_fraction, config.MAX_POSITION_SIZE_PCT / 100), 4)
        return stop_loss, take_profit, position_size

class ConfirmationEngine:
    def __init__(self, signal_engine: SignalEngine):
        self.initial = signal_engine
        self.confirmed = None
        self.wait_candles = config.CONFIRMATION_WAIT_CANDLES

    async def wait_and_confirm(self, session):
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
