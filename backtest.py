"""
backtest.py - نظام الاختبار الخلفي لتقييم الأداء (نسخة أصلية متكاملة).
"""
import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from datetime import datetime, timezone
from config import CFG
from utils import logger, fetcher
from database import db

class Backtester:
    def __init__(self):
        self.trades: List[Dict] = []
        self._price_cache: Dict[str, pd.DataFrame] = {}

    async def _fetch_historical_data(self, symbol: str, days: int = 7) -> Optional[pd.DataFrame]:
        if symbol in self._price_cache:
            return self._price_cache[symbol]
        try:
            limit = days * 24 * 12
            df = await fetcher.fetch_klines(symbol, "5m", limit=min(limit, 500))
            if not df.empty:
                self._price_cache[symbol] = df
                return df
        except Exception as e:
            logger.warning(f"Failed to fetch history for {symbol}", extra={"error": str(e)})
        return None

    async def simulate_signal(self, signal: Dict, df_future: pd.DataFrame) -> Dict:
        entry = signal["entry_price"]
        sl = signal["stop_loss"]
        tp = signal["take_profit"]
        signal_type = signal["type"]
        position_size = signal.get("position_size", 1.0)

        result = {
            "signal_id": signal.get("id"),
            "symbol": signal["symbol"],
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "type": signal_type,
            "position_size": position_size,
            "exit_price": None,
            "pnl": 0.0,
            "pnl_percent": 0.0,
            "status": "OPEN",
            "duration_bars": 0,
            "exit_reason": "PENDING",
        }

        if df_future is None or len(df_future) < 2:
            result["status"] = "NO_DATA"
            return result

        max_bars = 20
        for i, (_, row) in enumerate(df_future.head(max_bars).iterrows()):
            high = row["high"]
            low = row["low"]
            close = row["close"]
            result["duration_bars"] = i + 1

            if signal_type == "BUY":
                if low <= sl:
                    result["exit_price"] = sl
                    result["pnl"] = (sl - entry) * position_size
                    result["pnl_percent"] = (sl / entry - 1) * 100
                    result["status"] = "HIT_SL"
                    result["exit_reason"] = "Stop Loss"
                    break
                elif high >= tp:
                    result["exit_price"] = tp
                    result["pnl"] = (tp - entry) * position_size
                    result["pnl_percent"] = (tp / entry - 1) * 100
                    result["status"] = "HIT_TP"
                    result["exit_reason"] = "Take Profit"
                    break
            else:
                if high >= sl:
                    result["exit_price"] = sl
                    result["pnl"] = (entry - sl) * position_size
                    result["pnl_percent"] = (entry / sl - 1) * 100
                    result["status"] = "HIT_SL"
                    result["exit_reason"] = "Stop Loss"
                    break
                elif low <= tp:
                    result["exit_price"] = tp
                    result["pnl"] = (entry - tp) * position_size
                    result["pnl_percent"] = (entry / tp - 1) * 100
                    result["status"] = "HIT_TP"
                    result["exit_reason"] = "Take Profit"
                    break

            if i == max_bars - 1 or i == len(df_future) - 1:
                result["exit_price"] = close
                if signal_type == "BUY":
                    result["pnl"] = (close - entry) * position_size
                    result["pnl_percent"] = (close / entry - 1) * 100
                else:
                    result["pnl"] = (entry - close) * position_size
                    result["pnl_percent"] = (entry / close - 1) * 100
                result["status"] = "EXPIRED"
                result["exit_reason"] = "Time Expired / Market Close"
                break

        return result

    async def run_on_history(self, signals: List[Dict], days_future: int = 1) -> Dict:
        self.trades = []
        total = len(signals)

        for idx, signal in enumerate(signals):
            symbol = signal["symbol"]
            logger.info(f"Backtesting {idx+1}/{total}: {symbol}")

            df_history = await self._fetch_historical_data(symbol, days=days_future + 1)
            if df_history is None or df_history.empty:
                continue

            signal_time = pd.to_datetime(signal.get("created_at") or signal.get("timestamp"))
            if pd.isna(signal_time):
                continue

            future_mask = df_history["open_time"] > signal_time
            df_future = df_history[future_mask]

            if df_future.empty:
                continue

            trade = await self.simulate_signal(signal, df_future)
            self.trades.append(trade)

        return self._calculate_metrics()

    def _calculate_metrics(self) -> Dict:
        if not self.trades:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "avg_profit": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "max_consecutive_losses": 0,
                "sharpe_monthly": 0.0,
                "net_pnl": 0.0,
                "total_win_trades": 0,
                "total_loss_trades": 0,
            }

        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]
        total_trades = len(self.trades)

        win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0
        avg_profit = np.mean([t["pnl"] for t in wins]) if wins else 0.0
        avg_loss = np.mean([abs(t["pnl"]) for t in losses]) if losses else 0.0
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = sum(abs(t["pnl"]) for t in losses)
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        max_consecutive_losses = 0
        current_streak = 0
        for t in self.trades:
            if t["pnl"] <= 0:
                current_streak += 1
                max_consecutive_losses = max(max_consecutive_losses, current_streak)
            else:
                current_streak = 0

        returns = [t["pnl"] for t in self.trades]
        if len(returns) > 1 and np.std(returns) > 0:
            sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(30)
        else:
            sharpe = 0.0

        metrics = {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 2),
            "avg_profit": round(avg_profit, 4),
            "avg_loss": round(avg_loss, 4),
            "profit_factor": round(profit_factor, 2),
            "max_consecutive_losses": max_consecutive_losses,
            "sharpe_monthly": round(sharpe, 3),
            "net_pnl": round(sum(returns), 4),
            "total_win_trades": len(wins),
            "total_loss_trades": len(losses),
        }

        logger.info("Backtest completed", extra=metrics)
        return metrics

    def generate_weekly_report(self, metrics: Dict) -> str:
        report = (
            "📊 *تقرير الأداء الأسبوعي (Backtest)*\n\n"
            f"📈 إجمالي الصفقات: {metrics.get('total_trades', 0)}\n"
            f"✅ الصفقات الرابحة: {metrics.get('total_win_trades', 0)}\n"
            f"❌ الصفقات الخاسرة: {metrics.get('total_loss_trades', 0)}\n"
            f"🎯 نسبة النجاح: {metrics.get('win_rate', 0)}%\n"
            f"💰 متوسط الربح: ${metrics.get('avg_profit', 0):.2f}\n"
            f"📉 متوسط الخسارة: ${metrics.get('avg_loss', 0):.2f}\n"
            f"⚖️ معامل الربح: {metrics.get('profit_factor', 0)}\n"
            f"📉 أقصى خسارة متتالية: {metrics.get('max_consecutive_losses', 0)}\n"
            f"📊 نسبة شارب (شهري): {metrics.get('sharpe_monthly', 0)}\n"
            f"💵 صافي الربح: ${metrics.get('net_pnl', 0):.2f}\n"
        )
        return report

backtester = Backtester()
