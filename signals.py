# signals.py
# Signal Engine v2026.3 - Rebuilt with Market Regime + 3 Signal Types

import math
import logging
from datetime import datetime, timezone
import config
from indicators import (
    add_indicators, detect_early_snipe, detect_explosion_setup,
    pivot_points, detect_market_regime, efficiency_ratio,
)

logger = logging.getLogger("quant_bot.signals")


class SignalEngine:
    def __init__(self, adaptive_weights=None):
        self.adaptive_weights = adaptive_weights
        self._near_miss_log = []

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
            scores["BUY"]["rsi"] = 1.0
        elif rsi_value > config.RSI_OVERBOUGHT:
            scores["BUY"]["rsi"] = -1.0
        else:
            scores["BUY"]["rsi"] = 0.0

        if config.RSI_SELL_ZONE_MIN <= rsi_value <= config.RSI_SELL_ZONE_MAX:
            scores["SELL"]["rsi"] = 1.0
        elif rsi_value < config.RSI_OVERSOLD:
            scores["SELL"]["rsi"] = -1.0
        else:
            scores["SELL"]["rsi"] = 0.0

        scores["BUY"]["adx"] = 1.0 if (adx_value > config.MIN_ADX and momentum_value > 0) else 0.0
        scores["SELL"]["adx"] = 1.0 if (adx_value > config.MIN_ADX and momentum_value < 0) else 0.0

        if config.MOMENTUM_MIN <= momentum_value <= config.MOMENTUM_MAX:
            scores["BUY"]["momentum"] = 1.0
        elif -config.MOMENTUM_MAX <= momentum_value <= -config.MOMENTUM_MIN:
            scores["SELL"]["momentum"] = 1.0
        else:
            scores["BUY"]["momentum"] = 0.0
            scores["SELL"]["momentum"] = 0.0

        scores["BUY"]["volume"] = 1.0 if volume_value >= 1.2 else 0.0
        scores["SELL"]["volume"] = 1.0 if volume_value >= 1.2 else 0.0

        scores["BUY"]["bollinger"] = 1.0 if close > bb_middle else 0.0
        scores["SELL"]["bollinger"] = 1.0 if close < bb_middle else 0.0

        scores["BUY"]["macd"] = 1.0 if macd_hist > 0 else 0.0
        scores["SELL"]["macd"] = 1.0 if macd_hist < 0 else 0.0

        pivots = pivot_points(df)
        scores["BUY"]["pivot"] = 0.0
        scores["SELL"]["pivot"] = 0.0
        if math.isfinite(pivots["pivot"]):
            if close > pivots["pivot"]:
                scores["BUY"]["pivot"] = 1.0
            elif close < pivots["pivot"]:
                scores["SELL"]["pivot"] = 1.0

        return scores, pivots

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

        return {"entry": entry, "sl": sl, "tp": tp, "risk_distance": risk, "rr": rr}

    # ============================================================
    # Position Size (من kadmos-risk: risk_budget ÷ stop_distance)[reference:33]
    # ============================================================
    def position_size(self, capital, entry, sl, atr_value=None):
        """
        ✅ الإصلاح الجذري: position_size = risk_budget ÷ real_stop_distance
        مع Volatility Targeting (من binance-futures-ai-bot)
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
        final_quantity = min(raw_quantity, max_quantity)

        actual_risk = final_quantity * distance
        if actual_risk > capital * 0.05:
            return None, None

        return final_quantity, actual_risk / capital

    # ============================================================
    # Quality Gates
    # ============================================================
    def check_trend_alignment(self, direction, df_15m):
        if df_15m is None or len(df_15m) < 50:
            return True, 0.0
        trend_close = float(df_15m["close"].iloc[-1])
        trend_ema50 = df_15m["close"].ewm(span=50).mean().iloc[-1]
        slope_pct = (trend_close - trend_ema50) / trend_ema50 * 100
        if direction == "BUY":
            return slope_pct > 0.1, slope_pct
        return slope_pct < -0.1, slope_pct

    def check_volume_confirmation(self, df):
        return float(df.iloc[-1]["volume_ratio"]) >= 1.2

    def check_momentum_alignment(self, direction, df):
        m = float(df.iloc[-1]["momentum"])
        h = float(df.iloc[-1]["macd_hist"])
        if direction == "BUY":
            return m > 0 and h > 0
        return m < 0 and h < 0

    # ============================================================
    # Signal Type Classifier
    # ============================================================
    def _classify_signal(
        self, is_explosion_4of4, breakout_confirmed,
        adx_value, rsi_value, direction,
        buy_positives, sell_positives, er_value
    ):
        # 🆕 Market Regime Check (من binance-futures-ai-bot)[reference:34]
        if config.ENABLE_REGIME_FILTER:
            if adx_value < config.REGIME_ADX_RANGING and er_value < config.REGIME_ER_CHOPPY:
                return None  # سوق متذبذب - لا إشارات

        # 1. EXPLOSION
        if is_explosion_4of4:
            if adx_value < config.EXPLOSION_MIN_ADX:
                return None
            if direction == "BUY" and rsi_value > config.RSI_OVERBOUGHT:
                return None
            if direction == "SELL" and rsi_value < config.RSI_OVERSOLD:
                return None
            return "EXPLOSION"

        # 2. BREAKOUT (مع حد أدنى للعوامل)
        if breakout_confirmed:
            if adx_value < config.BREAKOUT_MIN_ADX:
                return None
            if direction == "BUY" and buy_positives < config.BREAKOUT_MIN_FACTORS:
                return None
            if direction == "SELL" and sell_positives < config.BREAKOUT_MIN_FACTORS:
                return None
            if direction == "BUY" and rsi_value > config.RSI_OVERBOUGHT:
                return None
            if direction == "SELL" and rsi_value < config.RSI_OVERSOLD:
                return None
            return "BREAKOUT"

        # 3. TREND
        if direction == "BUY" and buy_positives >= config.MIN_FACTORS_ALIGNED:
            if adx_value >= config.MIN_ADX:
                return "TREND"
        if direction == "SELL" and sell_positives >= config.MIN_FACTORS_ALIGNED:
            if adx_value >= config.MIN_ADX:
                return "TREND"

        return None

    def _calculate_score(self, signal_type, factor_count, adx_value,
                         volume_ratio, trend_aligned, er_value=0):
        base = {"EXPLOSION": 7.5, "BREAKOUT": 6.5, "TREND": 5.5}.get(signal_type, 5.0)
        factor_bonus = min(factor_count / 7.0 * 1.5, 1.5)
        adx_bonus = min((adx_value - 20) / 30.0 * 1.0, 1.0) if adx_value > 20 else 0.0
        volume_bonus = min((volume_ratio - 1.0) / 2.0 * 0.5, 0.5) if volume_ratio > 1.0 else 0.0
        trend_bonus = 0.5 if trend_aligned else 0.0
        er_bonus = min(er_value * 0.5, 0.5) if er_value > 0 else 0.0  # 🆕

        total = base + factor_bonus + adx_bonus + volume_bonus + trend_bonus + er_bonus
        return round(min(total, 10.0), 2)

    # ============================================================
    # 🆕 Near-Miss Logging (من OpenClaw Apex)[reference:35]
    # ============================================================
    def _log_near_miss(self, symbol, reason, details):
        if not config.ENABLE_NEAR_MISS_LOGGING:
            return
        entry = {
            "symbol": symbol,
            "reason": reason,
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._near_miss_log.append(entry)
        if len(self._near_miss_log) > config.NEAR_MISS_MAX_LOGS:
            self._near_miss_log = self._near_miss_log[-config.NEAR_MISS_MAX_LOGS:]
        logger.debug(f"Near-miss: {symbol} | {reason} | {details}")

    # ============================================================
    # Main Analysis
    # ============================================================
    def analyze(self, symbol, df, capital=None, df_15m=None):
        capital = capital or config.INITIAL_CAPITAL
        if df is None or len(df) < 60:
            return None

        df = add_indicators(df)
        latest = df.iloc[-1]

        required = ["rsi", "adx", "atr", "bb_width", "macd_hist",
                    "momentum", "volume_ratio", "efficiency_ratio"]
        for name in required:
            if not math.isfinite(float(latest[name])):
                self._log_near_miss(symbol, f"invalid_{name}", {})
                return None

        factors, pivots = self.score_factors(df)
        buy_score, buy_contrib, buy_weights, buy_positives = self.weighted_score(
            factors["BUY"], return_contributions=True
        )
        sell_score, sell_contrib, sell_weights, sell_positives = self.weighted_score(
            factors["SELL"], return_contributions=True
        )

        rsi_value = float(latest["rsi"])
        adx_value = float(latest["adx"])
        volume_ratio = float(latest["volume_ratio"])
        entry = float(latest["close"])
        atr_value = float(latest["atr"])
        er_value = float(latest["efficiency_ratio"])

        # Market Regime
        regime_data = detect_market_regime(df)
        if regime_data["regime"] == "RANGING" and config.ENABLE_REGIME_FILTER:
            self._log_near_miss(symbol, "ranging_market", regime_data)
            return None

        # Explosion Detection
        explosion = detect_explosion_setup(df)
        is_explosion_4of4 = (
            explosion.get("active", False)
            and explosion.get("conditions_met", 0) >= 4
        )

        # Breakout
        breakout_data = explosion.get("breakout_data", {})
        breakout_confirmed = (
            (breakout_data.get("breakout_up", False)
             or breakout_data.get("breakout_down", False))
            and volume_ratio >= config.BREAKOUT_MIN_VOLUME
        )

        # Direction
        if buy_score > sell_score:
            candidate_direction = "BUY"
            candidate_positives = buy_positives
        elif sell_score > buy_score:
            candidate_direction = "SELL"
            candidate_positives = sell_positives
        else:
            self._log_near_miss(symbol, "score_tie", {"buy": buy_score, "sell": sell_score})
            return None

        trend_aligned, _ = self.check_trend_alignment(candidate_direction, df_15m)

        signal_type = self._classify_signal(
            is_explosion_4of4=is_explosion_4of4,
            breakout_confirmed=breakout_confirmed,
            adx_value=adx_value,
            rsi_value=rsi_value,
            direction=candidate_direction,
            buy_positives=buy_positives,
            sell_positives=sell_positives,
            er_value=er_value,
        )

        if signal_type is None:
            self._log_near_miss(symbol, "classify_rejected", {
                "adx": round(adx_value, 1),
                "er": round(er_value, 3),
                "buy": round(buy_score, 1),
                "sell": round(sell_score, 1),
            })
            return None

        direction = candidate_direction

        if config.REQUIRE_TREND_ALIGNMENT and not trend_aligned:
            self._log_near_miss(symbol, "trend_not_aligned", {})
            return None
        if config.REQUIRE_VOLUME_CONFIRMATION and not self.check_volume_confirmation(df):
            self._log_near_miss(symbol, "volume_not_confirmed", {})
            return None
        if config.REQUIRE_MOMENTUM_ALIGNMENT and not self.check_momentum_alignment(direction, df):
            self._log_near_miss(symbol, "momentum_not_aligned", {})
            return None

        score = self._calculate_score(
            signal_type=signal_type,
            factor_count=candidate_positives,
            adx_value=adx_value,
            volume_ratio=volume_ratio,
            trend_aligned=trend_aligned,
            er_value=er_value,
        )

        if score < config.MIN_SCORE:
            self._log_near_miss(symbol, "score_too_low", {"score": score})
            return None

        risk = self.calculate_risk_levels(direction, entry, atr_value, pivots)
        if risk is None:
            self._log_near_miss(symbol, "risk_invalid", {})
            return None

        quantity, actual_risk_percent = self.position_size(
            capital, risk["entry"], risk["sl"], atr_value
        )
        if quantity is None or quantity <= 0:
            self._log_near_miss(symbol, "position_size_rejected", {})
            return None

        quality = round(score / 10.0 * 100, 1)
        if quality < config.MIN_QUALITY_PERCENT:
            self._log_near_miss(symbol, "quality_too_low", {"quality": quality})
            return None

        if direction == "BUY":
            used_factors = {k: v for k, v in factors["BUY"].items() if v > 0}
            factor_weights = buy_weights
            factor_contributions = buy_contrib
        else:
            used_factors = {k: v for k, v in factors["SELL"].items() if v > 0}
            factor_weights = sell_weights
            factor_contributions = sell_contrib

        signal_type_ar = {
            "EXPLOSION": "💥 تنبؤ بانفجار",
            "BREAKOUT": "⚡ اختراق مؤكد",
            "TREND": "📈 اتجاه قوي",
        }.get(signal_type, "📊 إشارة")

        logger.info(
            f"✅ Signal: {symbol} {direction} type={signal_type} "
            f"score={score} quality={quality}% regime={regime_data['regime']}"
        )

        return {
            "symbol": symbol.upper(),
            "direction": direction,
            "signal_type": signal_type,
            "signal_type_ar": signal_type_ar,
            "score": score,
            "quality": quality,
            "entry": round(risk["entry"], 8),
            "sl": round(risk["sl"], 8),
            "tp": round(risk["tp"], 8),
            "rr": round(risk["rr"], 2),
            "position_size": round(quantity, 8),
            "actual_risk_percent": round(actual_risk_percent * 100, 2),
            "rsi": round(rsi_value, 2),
            "adx": round(adx_value, 2),
            "atr": round(atr_value, 8),
            "volume_ratio": round(volume_ratio, 2),
            "efficiency_ratio": round(er_value, 3),
            "market_regime": regime_data["regime"],
            "factor_count": candidate_positives,
            "factor_contributions": factor_contributions,
            "factor_weights": factor_weights,
            "used_factors": used_factors,
            "explosion_details": explosion.get("details", {}) if signal_type == "EXPLOSION" else {},
            "explosion_conditions": explosion.get("conditions_met", 0) if signal_type == "EXPLOSION" else 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
