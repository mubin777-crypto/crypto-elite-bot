"""
signals.py - محرك توليد الإشارات مع تحسينات الاتجاه وإصلاح SL/TP.
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
from config import CFG
from indicators import TechnicalIndicators
from utils import calculate_atr, safe_divide, AdaptiveWeights, logger
from database import db

class SignalEngine:
    def __init__(self):
        self.indicators = TechnicalIndicators()
        self.adaptive = AdaptiveWeights(CFG.WEIGHTS)
        saved = db.get_adaptive_weights()
        if saved:
            self.adaptive.weights = saved

    def analyze(self, symbol: str, df_5m: pd.DataFrame, df_1h: pd.DataFrame, df_4h: pd.DataFrame) -> Optional[Dict]:
        if len(df_5m) < CFG.SMA_SLOW + 10:
            return None

        ind_5m = self.indicators.compute_all(df_5m)
        if not ind_5m:
            return None

        # 🔥 حساب ADX أولاً لنمرره لدالة الاتجاه
        adx_val = ind_5m["adx"].iloc[-1]
        trend_4h = self._get_trend_direction(df_4h, adx_val)
        
        last_idx = len(df_5m) - 1
        prev_idx = last_idx - 1
        close = df_5m["close"].iloc[last_idx]
        rsi_val = ind_5m["rsi"].iloc[last_idx]
        sma20 = ind_5m["sma20"].iloc[last_idx]
        sma50 = ind_5m["sma50"].iloc[last_idx]
        momentum = ind_5m["momentum"].iloc[last_idx]
        vol_ratio = ind_5m["volume_ratio"].iloc[last_idx]
        bb = ind_5m["bb"]
        bb_upper = bb["upper"].iloc[last_idx]
        bb_lower = bb["lower"].iloc[last_idx]
        is_squeeze = bb["squeeze"].iloc[last_idx]
        macd_hist = ind_5m["macd"]["histogram"].iloc[last_idx]
        macd_hist_prev = ind_5m["macd"]["histogram"].iloc[prev_idx]
        pivots = ind_5m["pivots"]
        atr = calculate_atr(df_5m, CFG.ATR_PERIOD).iloc[last_idx]

        # Cooldown
        if db.get_last_signal_time(symbol, CFG.COOLDOWN_MINUTES):
            return None

        # Daily Loss
        daily_stats = db.get_daily_stats()
        if daily_stats.get("pnl", 0) <= -(CFG.VIRTUAL_CAPITAL * CFG.MAX_DAILY_LOSS_PERCENT / 100):
            logger.warning("Daily loss limit reached", extra={"symbol": symbol})
            return None

        scores = {}
        reasons = []

        # ─── 1. الاتجاه (محسّن باستخدام ADX) ───
        trend_score = 0
        if trend_4h == "UP":
            trend_score = 1.0
            reasons.append(f"اتجاه صاعد (ADX {adx_val:.1f})")
        elif trend_4h == "DOWN":
            trend_score = 1.0
            reasons.append(f"اتجاه هابط (ADX {adx_val:.1f})")
        else:
            reasons.append(f"اتجاه جانبي/ضعيف (ADX {adx_val:.1f})")
        scores["trend"] = trend_score

        # ─── 2. الزخم ───
        mom_score = 0
        if trend_4h == "UP" and momentum > 0.5:
            mom_score = 1.0
            reasons.append(f"زخم إيجابي ({momentum:.2f}%)")
        elif trend_4h == "DOWN" and momentum < -0.5:
            mom_score = 1.0
            reasons.append(f"زخم سلبي ({momentum:.2f}%)")
        scores["momentum"] = mom_score

        # ─── 3. الحجم ───
        vol_score = 0
        if vol_ratio >= CFG.VOLUME_SPIKE_RATIO:
            vol_score = 1.0
            reasons.append(f"ارتفاع الحجم ({vol_ratio:.1f}x)")
        elif vol_ratio >= 1.2:
            vol_score = 0.5
        scores["volume"] = vol_score

        # ─── 4. التقلب ───
        vol_score_bb = 0
        if is_squeeze:
            vol_score_bb = 0.8
            reasons.append("انضغاط بولينجر")
        if close > bb_upper and trend_4h == "UP":
            vol_score_bb = max(vol_score_bb, 0.6)
            reasons.append("اختراق الحد العلوي")
        elif close < bb_lower and trend_4h == "DOWN":
            vol_score_bb = max(vol_score_bb, 0.6)
            reasons.append("اختراق الحد السفلي")
        scores["volatility"] = vol_score_bb

        # ─── 5. RSI ───
        rsi_score = 0
        if trend_4h == "UP" and 30 <= rsi_val <= 50:
            rsi_score = 1.0
            reasons.append(f"RSI في منطقة الشراء ({rsi_val:.1f})")
        elif trend_4h == "DOWN" and 50 <= rsi_val <= 70:
            rsi_score = 1.0
            reasons.append(f"RSI في منطقة البيع ({rsi_val:.1f})")
        scores["rsi"] = rsi_score

        # ─── 6. MACD ───
        macd_score = 0
        if macd_hist > 0 and macd_hist_prev <= 0 and trend_4h == "UP":
            macd_score = 1.0
            reasons.append("تقاطع MACD إيجابي")
        elif macd_hist < 0 and macd_hist_prev >= 0 and trend_4h == "DOWN":
            macd_score = 1.0
            reasons.append("تقاطع MACD سلبي")
        scores["macd"] = macd_score

        # ─── 7. Pivot ───
        pivot_score = 0
        if trend_4h == "UP" and abs(close - pivots["r1"]) / close < 0.005:
            pivot_score = 0.8
            reasons.append("اقتراب من مقاومة يومية")
        elif trend_4h == "DOWN" and abs(close - pivots["s1"]) / close < 0.005:
            pivot_score = 0.8
            reasons.append("اقتراب من دعم يومي")
        scores["pivot"] = pivot_score

        # ─── Early Snipe ───
        if adx_val < CFG.ADX_WEAK_TREND and not self._early_snipe_check(df_5m, ind_5m):
            return None

        # ─── النتيجة الإجمالية ───
        total_score = sum(score * self.adaptive.weights.get(factor, 0.1) * 10 for factor, score in scores.items())
        if trend_4h == "SIDEWAYS" and adx_val < 20:
            total_score = min(total_score, CFG.MAX_SCORE_SIDEWAYS)

        if total_score < CFG.MIN_SCORE:
            return None

        signal_type = "BUY" if trend_4h == "UP" else "SELL" if trend_4h == "DOWN" else "NEUTRAL"
        if signal_type == "NEUTRAL":
            return None

        # 🔥 حساب SL/TP مع التحقق من الصحة
        sl, tp, risk = self._calculate_risk_levels(close, atr, pivots, signal_type)
        if sl is None or tp is None:
            return None

        # 🔥 التحقق من صحة SL/TP
        if signal_type == "BUY":
            if sl >= close or tp <= close:
                logger.warning(f"⚠️ SL/TP غير صحيح لـ {symbol}: SL={sl}, TP={tp}, Entry={close}")
                return None
        else:  # SELL
            if sl <= close or tp >= close:
                logger.warning(f"⚠️ SL/TP غير صحيح لـ {symbol}: SL={sl}, TP={tp}, Entry={close}")
                return None

        rr_ratio = abs(tp - close) / risk if risk > 0 else 0
        if rr_ratio < CFG.MIN_RR_RATIO:
            return None

        risk_amount_usdt = CFG.VIRTUAL_CAPITAL * CFG.RISK_PER_TRADE_PERCENT / 100
        units = safe_divide(risk_amount_usdt, risk, 0)
        max_units = (CFG.VIRTUAL_CAPITAL * 0.5) / close if close > 0 else 0
        position_size = min(units, max_units)

        confidence = min(100, total_score * 10)
        if confidence < CFG.MIN_CONFIDENCE:
            return None

        # بناء الإشارة
        signal = {
            "symbol": symbol,
            "timeframe": "5m",
            "type": signal_type,
            "entry_price": round(close, 8),
            "stop_loss": round(sl, 8),
            "take_profit": round(tp, 8),
            "position_size": round(position_size, 4),
            "confidence": round(confidence, 1),
            "score": round(total_score, 2),
            "reasons": reasons,
            "adx": round(adx_val, 2),
            "rsi": round(rsi_val, 2),
            "volume_ratio": round(vol_ratio, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # 🔥 تحديد نوع الإشارة النصي
        if signal["score"] >= 8.0:
            signal["signal"] = "🟢 **شراء قوي**" if signal_type == "BUY" else "🔴 **بيع قوي**"
        elif signal["score"] >= 6.0:
            signal["signal"] = "🟢 **شراء**" if signal_type == "BUY" else "🔴 **بيع**"
        elif signal["score"] >= 4.0:
            signal["signal"] = "🟡 **مراقبة**"
        else:
            signal["signal"] = "⚪ **حيادي**"

        return signal

    def _get_trend_direction(self, df: pd.DataFrame, adx_val: float = None) -> str:
        """تحديد اتجاه السوق باستخدام SMA و ADX."""
        if len(df) < CFG.SMA_SLOW:
            return "SIDEWAYS"
        sma20 = df["close"].rolling(window=CFG.SMA_FAST).mean().iloc[-1]
        sma50 = df["close"].rolling(window=CFG.SMA_SLOW).mean().iloc[-1]
        close = df["close"].iloc[-1]
        
        # إذا كان ADX مرتفعاً (> 25)، نأخذ اتجاه SMA
        if adx_val is not None and adx_val > CFG.ADX_WEAK_TREND:
            if close > sma20 > sma50:
                return "UP"
            elif close < sma20 < sma50:
                return "DOWN"
            else:
                return "SIDEWAYS"
        else:
            # ADX منخفض: سوق جانبي
            return "SIDEWAYS"

    def _early_snipe_check(self, df: pd.DataFrame, ind: Dict) -> bool:
        bb = ind["bb"]
        vol = ind["volume_ratio"]
        squeeze_3 = bb["squeeze"].iloc[-3:].all() if len(bb["squeeze"]) >= 3 else False
        vol_spike = vol.iloc[-1] >= 1.8
        high_20 = df["high"].iloc[-20:].max()
        close = df["close"].iloc[-1]
        near_resistance = abs(close - high_20) / high_20 < 0.01 if high_20 > 0 else False
        return squeeze_3 and vol_spike and near_resistance

    def _calculate_risk_levels(self, entry: float, atr: float, pivots: Dict[str, float],
                               signal_type: str) -> Tuple[Optional[float], Optional[float], float]:
        if entry <= 0:
            return None, None, 0.0

        buffer = 1.0 - CFG.SL_BUFFER_PERCENT

        # 🔥 معالجة العملات منخفضة السعر (أقل من 0.01)
        if entry < 0.01:
            if signal_type == "BUY":
                sl = entry * 0.97
                tp = entry * 1.06
            else:
                sl = entry * 1.03
                tp = entry * 0.94
            # 🔥 التأكد من صحة SL/TP
            if signal_type == "BUY":
                if sl >= entry:
                    sl = entry * 0.99
                if tp <= entry:
                    tp = entry * 1.02
            else:
                if sl <= entry:
                    sl = entry * 1.01
                if tp >= entry:
                    tp = entry * 0.98
            risk = abs(entry - sl)
            return sl, tp, risk

        atr_sl = atr * CFG.ATR_SL_MULTIPLIER

        if signal_type == "BUY":
            sl_atr = entry - atr_sl
            s1_val = pivots.get("s1", sl_atr)
            if pd.isna(s1_val) or s1_val is None or s1_val == 0:
                sl_support = sl_atr
            else:
                sl_support = s1_val * buffer if s1_val < entry else sl_atr
            sl = max(sl_atr, sl_support) if sl_support < entry else sl_atr
            if sl >= entry:
                sl = entry - atr_sl
            risk = entry - sl
            tp = entry + (risk * CFG.MIN_RR_RATIO)
        else:
            sl_atr = entry + atr_sl
            r1_val = pivots.get("r1", sl_atr)
            if pd.isna(r1_val) or r1_val is None or r1_val == 0:
                sl_resist = sl_atr
            else:
                sl_resist = r1_val / buffer if r1_val > entry else sl_atr
            sl = min(sl_atr, sl_resist) if sl_resist > entry else sl_atr
            if sl <= entry:
                sl = entry + atr_sl
            risk = sl - entry
            tp = entry - (risk * CFG.MIN_RR_RATIO)

        return sl, tp, risk

    def update_weights_from_results(self, results: List[Dict]):
        for res in results:
            for factor in res.get("factors", []):
                self.adaptive.update(factor, res.get("pnl", 0))
        self.adaptive.recalculate()
        db.save_adaptive_weights(self.adaptive.weights)

engine = SignalEngine()
