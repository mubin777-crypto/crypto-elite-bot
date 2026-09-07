# signals.py
# Signal Engine - Production Ready

import math
import json
import logging
from datetime import datetime, timezone
import config
from indicators import add_indicators, detect_early_snipe, pivot_points

logger = logging.getLogger("quant_bot.signals")

class SignalEngine:
    def __init__(self, adaptive_weights=None):
        self.adaptive_weights = adaptive_weights

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

        rsi_buy = 1.0 if 45 <= rsi_value <= 65 else 0.0
        rsi_sell = 1.0 if 35 <= rsi_value <= 55 else 0.0

        if config.ENABLE_RSI_FILTER:
            if rsi_value > config.RSI_OVERBOUGHT:
                rsi_buy = -1.0
            if rsi_value < config.RSI_OVERSOLD:
                rsi_sell = -1.0

        scores["BUY"]["rsi"] = rsi_buy
        scores["SELL"]["rsi"] = rsi_sell

        scores["BUY"]["adx"] = 1.0 if (adx_value > config.MIN_ADX and momentum_value > 0) else 0.0
        scores["SELL"]["adx"] = 1.0 if (adx_value > config.MIN_ADX and momentum_value < 0) else 0.0

        scores["BUY"]["momentum"] = 1.0 if momentum_value > 0 else 0.0
        scores["SELL"]["momentum"] = 1.0 if momentum_value < 0 else 0.0

        scores["BUY"]["volume"] = 1.0 if volume_value >= 1.0 else 0.0
        scores["SELL"]["volume"] = 1.0 if volume_value >= 1.0 else 0.0

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

        for name, value in factors.items():
            weight = 1.0 if not self.adaptive_weights else self.adaptive_weights.get(name, 1.0)
            weights[name] = weight
            contrib = value * weight
            contributions[name] = contrib
            total += contrib

        if return_contributions:
            if total > 0:
                normalized = {k: v / total for k, v in contributions.items()}
            else:
                normalized = {k: 0 for k in contributions}
            return total, normalized, weights
        return total

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

    def position_size(self, capital, entry, sl):
        if capital <= 0 or entry <= 0:
            return None, None

        risk_amount = capital * config.RISK_PER_TRADE
        distance = abs(entry - sl)
        if distance <= 0:
            return None, None

        raw_quantity = risk_amount / distance
        max_notional = capital * config.MAX_POSITION_PERCENT
        max_quantity = max_notional / entry

        if raw_quantity > max_quantity:
            logger.debug(f"Signal rejected: position size exceeds max {config.MAX_POSITION_PERCENT*100}%")
            return None, None

        actual_risk_amount = raw_quantity * distance
        actual_risk_percent = actual_risk_amount / capital

        return raw_quantity, actual_risk_percent

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
        buy_score, buy_contrib, buy_weights = self.weighted_score(factors["BUY"], return_contributions=True)
        sell_score, sell_contrib, sell_weights = self.weighted_score(factors["SELL"], return_contributions=True)

        early = detect_early_snipe(df)
        direction = None
        score = 0.0
        early_snipe = False
        used_factors = {}
        factor_weights = {}
        factor_contributions = {}

        if early["active"]:
            direction = early["direction"]
            score = early["score"]
            early_snipe = True
            used_factors = {"early_snipe": 1.0}
            factor_weights = {"early_snipe": 1.0}
            factor_contributions = {"early_snipe": 1.0}
        elif buy_score >= config.MIN_SCORE:
            direction = "BUY"
            score = buy_score
            used_factors = {k: v for k, v in factors["BUY"].items() if v > 0}
            factor_weights = buy_weights
            factor_contributions = buy_contrib
        elif sell_score >= config.MIN_SCORE:
            direction = "SELL"
            score = sell_score
            used_factors = {k: v for k, v in factors["SELL"].items() if v > 0}
            factor_weights = sell_weights
            factor_contributions = sell_contrib

        if direction is None:
            return None

        rsi_value = float(latest["rsi"])
        if config.ENABLE_RSI_FILTER:
            if direction == "BUY" and rsi_value > config.RSI_OVERBOUGHT:
                return None
            if direction == "SELL" and rsi_value < config.RSI_OVERSOLD:
                return None

        adx_value = float(latest["adx"])
        if not early_snipe and adx_value <= config.MIN_ADX:
            return None

        entry = float(latest["close"])
        atr_value = float(latest["atr"])

        risk = self.calculate_risk_levels(direction, entry, atr_value, pivots)
        if risk is None:
            return None

        quantity, actual_risk_percent = self.position_size(capital, risk["entry"], risk["sl"])
        if quantity is None or quantity <= 0:
            return None

        if df_15m is not None and len(df_15m) > 0:
            df_15m_indicators = add_indicators(df_15m)
            trend_close = float(df_15m["close"].iloc[-1])
            trend_ema50 = df_15m["close"].ewm(span=50).mean().iloc[-1] if len(df_15m) >= 50 else trend_close
            trend_slope = trend_close - trend_ema50
        else:
            trend_slope = 0

        if direction == "BUY" and trend_slope > 0:
            score += 1.0
        elif direction == "SELL" and trend_slope < 0:
            score += 1.0

        max_possible = 7.0
        if early_snipe:
            max_possible += 1.0
        quality = min(score / max_possible * 100, 100)

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
            "early_snipe": early_snipe,
            "factor_contributions": factor_contributions,
            "factor_weights": factor_weights,
            "used_factors": used_factors,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
