"""
backtest.py - نظام الاختبار الخلفي لتقييم الأداء.
"""
import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from utils import logger, fetcher

class Backtester:
    def __init__(self):
        self.trades: List[Dict] = []
        self._price_cache: Dict[str, pd.DataFrame] = {}

    async def _fetch_historical_data(self, symbol: str, days: int = 7) -> Optional[pd.DataFrame]:
        if symbol in self._price_cache:
            return self._price_cache[symbol]
        try:
            limit = min(days * 24 * 12, 500)
            df = await fetcher.fetch_klines(symbol, "5m", limit=limit)
            if not df.empty:
                self._price_cache[symbol] = df
                return df
        except Exception as e:
            logger.warning(f"Failed to fetch history for {symbol}: {e}")
        return None

    async def simulate_signal(self, signal: Dict, df_future: pd.DataFrame) -> Dict:
        entry = signal["entry_price"]
        sl = signal["stop_loss"]
        tp = signal["take_profit"]
        signal_type = signal["type"]
        position_size = signal.get("position_size", 1.0)

        result = {
            "symbol": signal["symbol"],
            "entry": entry, "sl": sl, "tp": tp, "type": signal_type,
            "position_size": position_size, "exit_price": None,
            "pnl": 0.0, "pnl_percent": 0.0, "status": "OPEN", "duration_bars": 0,
        }

        if df_future is None or len(df_future) < 2:
            result["status"] = "NO_DATA"
            return result

        for i, (_, row) in enumerate(df_future.head(20).iterrows()):
            high, low, close = row["high"], row["low"], row["close"]
            result["duration_bars"] = i + 1

            if signal_type == "BUY":
                if low <= sl:
                    result.update({"exit_price": sl, "pnl": (sl - entry) * position_size, "pnl_percent": (sl / entry - 1) * 100, "status": "HIT_SL"})
                    break
                elif high >= tp:
                    result.update({"exit_price": tp, "pnl": (tp - entry) * position_size, "pnl_percent": (tp / entry - 1) * 100, "status": "HIT_TP"})
                    break
            else:
                if high >= sl:
                    result.update({"exit_price": sl, "pnl": (entry - sl) * position_size, "pnl_percent": (entry / sl - 1) * 100, "status": "HIT_SL"})
                    break
                elif low <= tp:
                    result.update({"exit_price": tp, "pnl": (entry - tp) * position_size, "pnl_percent": (entry / tp - 1) * 100, "status": "HIT_TP"})
                    break

            result.update({"exit_price": close, "status": "EXPIRED", "pnl": (close - entry) * position_size if signal_type == "BUY" else (entry - close) * position_size})

        return result

    async def run_on_history(self, signals: List[Dict], days_future: int = 1) -> Dict:
        self.trades = []
        for signal in signals:
            symbol = signal["symbol"]
            df_history = await self._fetch_historical_data(symbol, days=days_future + 1)
            if df_history is None or df_history.empty: continue

            signal_time = pd.to_datetime(signal.get("created_at") or signal.get("timestamp"))
            if pd.isna(signal_time): continue

            df_future = df_history[df_history["open_time"] > signal_time]
            if df_future.empty: continue

            trade = await self.simulate_signal(signal, df_future)
            self.trades.append(trade)

        return self._calculate_metrics()

    def _calculate_metrics(self) -> Dict:
        if not self.trades:
            return {"total_trades": 0, "win_rate": 0.0, "avg_profit": 0.0, "avg_loss": 0.0, "profit_factor": 0.0, "net_pnl": 0.0}

        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]
        total_trades = len(self.trades)

        win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0
        avg_profit = np.mean([t["pnl"] for t in wins]) if wins else 0.0
        avg_loss = np.mean([abs(t["pnl"]) for t in losses]) if losses else 0.0
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = sum(abs(t["pnl"]) for t in losses)
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

        return {
            "total_trades": total_trades, "win_rate": round(win_rate, 2),
            "avg_profit": round(avg_profit, 4), "avg_loss": round(avg_loss, 4),
            "profit_factor": round(profit_factor, 2), "net_pnl": round(sum(t["pnl"] for t in self.trades), 4),
            "total_win_trades": len(wins), "total_loss_trades": len(losses),
        }

    def generate_weekly_report(self, metrics: Dict) -> str:
        return (
            "📊 *تقرير الأداء الأسبوعي (Backtest)*\n\n"
            f"📈 إجمالي الصفقات: {metrics.get('total_trades', 0)}\n"
            f"✅ الصفقات الرابحة: {metrics.get('total_win_trades', 0)}\n"
            f"❌ الصفقات الخاسرة: {metrics.get('total_loss_trades', 0)}\n"
            f"🎯 نسبة النجاح: {metrics.get('win_rate', 0)}%\n"
            f"💵 صافي الربح: ${metrics.get('net_pnl', 0):.2f}\n"
        )

backtester = Backtester()
