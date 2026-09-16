# signals.py
# Signal Engine - Strong Signals Only + Fixed Position Size

import math
import json
import logging
from datetime import datetime, timezone
import config
from indicators import add_indicators, detect_early_snipe, detect_explosion_setup, pivot_points

logger = logging.getLogger("quant_bot.signals")


class SignalEngine:
    def __init__(self, adaptive_weights=None):
        self.adaptive_weights = adaptive_weights

    # ============================================================
    # Factor Scoring
    # ============================================================
    def score_factors(self, df):
        latest = df.iloc[-1]
        rsi_value = float(latest["rsi"])
        adx_value = float(latest["adx"])
        momentum_value = float(latest["momentum"])
        volume_value = float(latest["volume_ratio"])
        close = float(latest["close"])
        bb_middle = float(latest["bb_middle"])
        macd_hist = float(latest["macd_hist"])

        scores = {"BUY": {}, "SELL": {}}

        # RSI
        if config.RSI_BUY_ZONE_MIN <= rsi_value <= config.RSI_BUY_ZONE_MAX:
            rsi_buy = 1.0
        elif rsi_value > config.RSI_OVERBOUGHT:
            rsi_buy = -1.0
        else:
            rsi_buy = 0.0

        if config.RSI_SELL_ZONE_MIN <= rsi_value <= config.RSI_SELL_ZONE_MAX:
            rsi_sell = 1.0
        elif rsi_value < config.RSI_OVERSOLD:
            rsi_sell = -1.0
        else:
            rsi_sell = 0.0

        scores["BUY"]["rsi"] = rsi_buy
        scores["SELL"]["rsi"] = rsi_sell

        # ADX
        scores["BUY"]["adx"] = 1.0 if (adx_value > config.MIN_ADX and momentum_value > 0) else 0.0
        scores["SELL"]["adx"] = 1.0 if (adx_value > config.MIN_ADX and momentum_value < 0) else 0.0

        # Momentum
        if config.MOMENTUM_MIN <= momentum_value <= config.MOMENTUM_MAX:
            scores["BUY"]["momentum"] = 1.0
        elif -config.MOMENTUM_MAX <= momentum_value <= -config.MOMENTUM_MIN:
            scores["SELL"]["momentum"] = 1.0
        else:
            scores["BUY"]["momentum"] = 0.0
            scores["SELL"]["momentum"] = 0.0

        # Volume
        scores["BUY"]["volume"] = 1.0 if volume_value >= 1.2 else 0.0
        scores["SELL"]["volume"] = 1.0 if volume_value >= 1.2 else 0.0

        # Bollinger
        scores["BUY"]["bollinger"] = 1.0 if close > bb_middle else 0.0
        scores["SELL"]["bollinger"] = 1.0 if close < bb_middle else 0.0

        # MACD
        scores["BUY"]["macd"] = 1.0 if macd_hist > 0 else 0.0
        scores["SELL"]["macd"] = 1.0 if macd_hist < 0 else 0.0

        # Pivot
        pivots = pivot_points(df)
        scores["BUY"]["pivot"] = 0.0
        scores["SELL"]["pivot"] = 0.0
        if math.isfinite(pivots["pivot"]):
            if close > pivots["pivot"]:
                scores["BUY"]["pivot"] = 1.0
            elif close < pivots["pivot"]:
                scores["SELL"]["pivot"] = 1.0

        return scores, pivots

    # ============================================================
    # Weighted Score
    # ============================================================
    def weighted_score(self, factors, return_contributions=False):
        total = 0.0
        contributions = {}
        weights = {}
        positives = 0

        for name, value in factors.items():
            weight = 1.0 if not self.adaptive_weights else self.adaptive_weights.get(name, 1.0)
            weights[name] = weight
            contrib = value * weight
            contributions[name] = contrib
            total += contrib
            if value > 0:
                positives += 1

        if return_contributions:
            if total > 0:
                normalized = {k: v / total for k, v in contributions.items()}
            else:
                normalized = {k: 0 for k in contributions}
            return total, normalized, weights, positives
        return total

    # ============================================================
    # Risk Levels
    # ============================================================
    def calculate_risk_levels(self, direction, entry, atr_value, pivots):
        if not math.isfinite(atr_value) or atr_value <= 0:
            return None
        if direction not in ["BUY", "SELL"]:
            return None

        atr_distance = atr_value * config.ATR_SL_MULTIPLIER
        buffer = entry * config.SL_BUFFER_PERCENT

        if direction == "BUY":
            sl_atr = entry - atr_distance - buffer
            if math.isfinite(pivots["s1"]) and pivots["s1"] < entry:
                sl_pivot = pivots["s1"] - buffer
                sl = max(sl_atr, sl_pivot) if sl_pivot > sl_atr else sl_atr
            else:
                sl = sl_atr
            sl = min(sl, entry - buffer * 0.5)
            risk = entry - sl
            if risk <= 0:
                return None
            tp = entry + risk * config.MIN_RR
            if math.isfinite(pivots["r1"]) and pivots["r1"] > entry:
                tp = max(tp, pivots["r1"] - buffer)
        else:
            sl_atr = entry + atr_distance + buffer
            if math.isfinite(pivots["r1"]) and pivots["r1"] > entry:
                sl_pivot = pivots["r1"] + buffer
                sl = min(sl_atr, sl_pivot) if sl_pivot < sl_atr else sl_atr
            else:
                sl = sl_atr
            sl = max(sl, entry + buffer * 0.5)
            risk = sl - entry
            if risk <= 0:
                return None
            tp = entry - risk * config.MIN_RR
            if math.isfinite(pivots["s1"]) and pivots["s1"] < entry:
                tp = min(tp, pivots["s1"] + buffer)

        if direction == "BUY":
            if not (sl < entry and tp > entry):
                return None
        else:
            if not (sl > entry and tp < entry):
                return None

        rr = abs((tp - entry) / risk)
        if rr < config.MIN_RR or not math.isfinite(rr):
            return None

        return {
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "risk_distance": risk,
            "rr": rr,
        }

    # ============================================================
    # Position Size - الإصلاح الحرج
    # ============================================================
    def position_size(self, capital, entry, sl):
        """
        ✅ الإصلاح: نأخذ الأدنى بين الحجم المطلوب والحد الأقصى
        بدلاً من رفض الإشارة بالكامل.
        """
        if capital <= 0 or entry <= 0:
            return None, None

        risk_amount = capital * config.RISK_PER_TRADE
        distance = abs(entry - sl)
        if distance <= 0:
            return None, None

        raw_quantity = risk_amount / distance
        max_notional = capital * config.MAX_POSITION_PERCENT
        max_quantity = max_notional / entry

        # ✅ نأخذ الأدنى (لا رفض)
        final_quantity = min(raw_quantity, max_quantity)

        # ✅ التحقق من المخاطرة النهائية
        actual_risk = final_quantity * distance
        max_acceptable_risk = capital * 0.05
        if actual_risk > max_acceptable_risk:
            logger.debug(
                f"Signal rejected: actual_risk {actual_risk:.2f} > "
                f"max {max_acceptable_risk:.2f}"
            )
            return None, None

        actual_risk_percent = actual_risk / capital
        return final_quantity, actual_risk_percent

    # ============================================================
    # Quality Gates
    # ============================================================
    def check_trend_alignment(self, direction, df_15m):
        if df_15m is None or len(df_15m) < 50:
            return True, 0.0
        trend_close = float(df_15m["close"].iloc[-1])
        trend_ema50 = df_15m["close"].ewm(span=50).mean().iloc[-1]
        trend_slope_pct = (trend_close - trend_ema50) / trend_ema50 * 100
        if direction == "BUY":
            aligned = trend_slope_pct > 0.1
        else:
            aligned = trend_slope_pct < -0.1
        return aligned, trend_slope_pct

    def check_volume_confirmation(self, df):
        latest = df.iloc[-1]
        vol_ratio = float(latest["volume_ratio"])
        return vol_ratio >= 1.2

    def check_momentum_alignment(self, direction, df):
        latest = df.iloc[-1]
        momentum_value = float(latest["momentum"])
        macd_hist = float(latest["macd_hist"])
        if direction == "BUY":
            return momentum_value > 0 and macd_hist > 0
        else:
            return momentum_value < 0 and macd_hist < 0

    # ============================================================
    # Main Analysis
    # ============================================================
    def analyze(self, symbol, df, capital=None, df_15m=None):
        capital = capital or config.INITIAL_CAPITAL
        if df is None or len(df) < 60:
            return None

        df = add_indicators(df)
        latest = df.iloc[-1]

        required = ["rsi", "adx", "atr", "bb_width", "macd_hist", "momentum", "volume_ratio"]
        for name in required:
            if not math.isfinite(float(latest[name])):
                return None

        factors, pivots = self.score_factors(df)
        buy_score, buy_contrib, buy_weights, buy_positives = self.weighted_score(
            factors["BUY"], return_contributions=True
        )
        sell_score, sell_contrib, sell_weights, sell_positives = self.weighted_score(
            factors["SELL"], return_contributions=True
        )

        explosion = detect_explosion_setup(df)

        direction = None
        score = 0.0
        is_explosion = False
        used_factors = {}
        factor_weights = {}
        factor_contributions = {}
        factor_count = 0

        # ✅ أولوية للانفجارات (3/4 شروط = 7.5)
        if explosion["active"] and explosion["score"] >= config.EARLY_SNIPE_SCORE:
            direction = explosion["direction"]
            score = explosion["score"]
            is_explosion = True
            used_factors = {"explosion": 1.0}
            factor_weights = {"explosion": 1.0}
            factor_contributions = {"explosion": 1.0}
            factor_count = explosion["conditions_met"]
            logger.info(
                f"💥 Explosion setup detected: {symbol} {direction} "
                f"({explosion['conditions_met']}/4 conditions) → processing"
            )

        elif buy_score >= config.MIN_SCORE and buy_positives >= config.MIN_FACTORS_ALIGNED:
            direction = "BUY"
            score = buy_score
            used_factors = {k: v for k, v in factors["BUY"].items() if v > 0}
            factor_weights = buy_weights
            factor_contributions = buy_contrib
            factor_count = buy_positives

        elif sell_score >= config.MIN_SCORE and sell_positives >= config.MIN_FACTORS_ALIGNED:
            direction = "SELL"
            score = sell_score
            used_factors = {k: v for k, v in factors["SELL"].items() if v > 0}
            factor_weights = sell_weights
            factor_contributions = sell_contrib
            factor_count = sell_positives

        if direction is None:
            return None

        # ✅ RSI filter (مع تجاوز للانفجارات)
        rsi_value = float(latest["rsi"])
        if config.ENABLE_RSI_FILTER:
            if direction == "BUY" and rsi_value > config.RSI_OVERBOUGHT:
                if not (is_explosion and config.EXPLOSION_RSI_OVERRIDE):
                    logger.debug(f"Rejected {symbol}: RSI too high ({rsi_value:.1f})")
                    return None
            if direction == "SELL" and rsi_value < config.RSI_OVERSOLD:
                if not (is_explosion and config.EXPLOSION_RSI_OVERRIDE):
                    logger.debug(f"Rejected {symbol}: RSI too low ({rsi_value:.1f})")
                    return None

        # ✅ ADX filter (إلا للانفجارات)
        adx_value = float(latest["adx"])
        if not is_explosion and adx_value <= config.MIN_ADX:
            logger.debug(f"Rejected {symbol}: ADX too low ({adx_value:.1f})")
            return None

        # ✅ بوابة الجودة (مخففة للانفجارات)
        if config.REQUIRE_TREND_ALIGNMENT:
            trend_aligned, trend_slope = self.check_trend_alignment(direction, df_15m)
            if not trend_aligned and not is_explosion:
                logger.debug(f"Rejected {symbol}: trend not aligned")
                return None

        if config.REQUIRE_VOLUME_CONFIRMATION:
            if not self.check_volume_confirmation(df) and not is_explosion:
                logger.debug(f"Rejected {symbol}: volume not confirmed")
                return None

        if config.REQUIRE_MOMENTUM_ALIGNMENT:
            if not self.check_momentum_alignment(direction, df) and not is_explosion:
                logger.debug(f"Rejected {symbol}: momentum not aligned")
                return None

        entry = float(latest["close"])
        atr_value = float(latest["atr"])

        risk = self.calculate_risk_levels(direction, entry, atr_value, pivots)
        if risk is None:
            logger.debug(f"Rejected {symbol}: risk levels invalid")
            return None

        # ✅ الإصلاح: position_size الآن يأخذ الأدنى
        quantity, actual_risk_percent = self.position_size(
            capital, risk["entry"], risk["sl"]
        )
        if quantity is None or quantity <= 0:
            logger.warning(
                f"⚠️ {symbol} {direction} rejected at position_size "
                f"(entry={entry}, sl={risk['sl']})"
            )
            return None

        # حساب الترند
        if df_15m is not None and len(df_15m) > 0:
            trend_close = float(df_15m["close"].iloc[-1])
            trend_ema50 = (
                df_15m["close"].ewm(span=50).mean().iloc[-1]
                if len(df_15m) >= 50
                else trend_close
            )
            trend_slope = trend_close - trend_ema50
        else:
            trend_slope = 0

        if direction == "BUY" and trend_slope > 0:
            score += 1.0
        elif direction == "SELL" and trend_slope < 0:
            score += 1.0

        max_possible = 7.0
        if is_explosion:
            max_possible += 3.0
        quality = min(score / max_possible * 100, 100)

        if quality < config.MIN_QUALITY_PERCENT and not is_explosion:
            logger.debug(f"Rejected {symbol}: quality {quality:.1f}% < {config.MIN_QUALITY_PERCENT}%")
            return None

        logger.info(
            f"✅ Signal approved: {symbol} {direction} "
            f"score={score:.2f} quality={quality:.1f}%"
        )

        return {
            "symbol": symbol.upper(),
            "direction": direction,
            "score": round(score, 2),
            "quality": round(quality, 1),
            "entry": round(risk["entry"], 8),
            "sl": round(risk["sl"], 8),
            "tp": round(risk["tp"], 8),
            "rr": round(risk["rr"], 2),
            "position_size": round(quantity, 8),
            "actual_risk_percent": round(actual_risk_percent * 100, 2),
            "rsi": round(rsi_value, 2),
            "adx": round(adx_value, 2),
            "atr": round(atr_value, 8),
            "early_snipe": is_explosion,
            "factor_count": factor_count,
            "factor_contributions": factor_contributions,
            "factor_weights": factor_weights,
            "used_factors": used_factors,
            "explosion_details": explosion.get("details", {}) if is_explosion else {},
            "explosion_conditions": explosion.get("conditions_met", 0) if is_explosion else 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
