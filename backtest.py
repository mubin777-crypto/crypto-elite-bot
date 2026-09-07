# backtest.py
# Historical Backtesting with Fees & Slippage

import argparse
import asyncio
import json
import math
import statistics
from datetime import datetime, timezone
import config
from indicators import add_indicators
from signals import SignalEngine
from utils import DataFetcher, klines_to_dataframe

def calculate_trade_result(signal, future_candles, signal_time=None):
    direction = signal["direction"]
    entry = float(signal["entry"])
    sl = float(signal["sl"])
    tp = float(signal["tp"])
    risk = abs(entry - sl)

    fee_slippage = config.TRADING_FEE_PERCENT + config.SLIPPAGE_PERCENT

    for candle in future_candles:
        candle_time = datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc)
        if signal_time and candle_time <= signal_time:
            continue
        high = float(candle[2])
        low = float(candle[3])
        hit_sl = low <= sl if direction == "BUY" else high >= sl
        hit_tp = high >= tp if direction == "BUY" else low <= tp

        if hit_sl and hit_tp:
            return {"result_r": -1.0, "outcome": "LOSS", "reason": "SL_TP_SAME_CANDLE"}
        elif hit_sl:
            return {"result_r": -1.0, "outcome": "LOSS", "reason": "SL"}
        elif hit_tp:
            gross_reward = abs(tp - entry)
            net_reward = gross_reward * (1 - fee_slippage)
            r = net_reward / risk if risk > 0 else 0
            return {"result_r": r, "outcome": "WIN", "reason": "TP"}

    return {"result_r": 0.0, "outcome": "TIMEOUT", "reason": "TIMEOUT"}

async def backtest_symbol(fetcher, engine, symbol, limit=config.BACKTEST_LIMIT):
    raw = await fetcher.klines(symbol, config.ANALYSIS_INTERVAL, limit)
    if not raw or len(raw) < 200:
        return None

    results = []
    hold_candles = config.SIGNAL_MAX_HOLD_CANDLES

    for i in range(120, len(raw) - hold_candles):
        history = raw[:i]
        future = raw[i:i + hold_candles]
        signal_time = datetime.fromtimestamp(raw[i][0] / 1000, tz=timezone.utc)
        df = klines_to_dataframe(history)
        signal = engine.analyze(symbol, df, config.INITIAL_CAPITAL)
        if not signal:
            continue
        outcome = calculate_trade_result(signal, future, signal_time)
        results.append(outcome)

    if not results:
        return None

    wins = [x for x in results if x["outcome"] == "WIN"]
    losses = [x for x in results if x["outcome"] == "LOSS"]
    timeouts = [x for x in results if x["outcome"] == "TIMEOUT"]
    both_events = [x for x in results if x.get("reason") == "SL_TP_SAME_CANDLE"]

    win_rate = len(wins) / len(results) * 100
    gross_profit = sum(x["result_r"] for x in wins)
    gross_loss = abs(sum(x["result_r"] for x in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else math.inf
    returns = [x["result_r"] for x in results]
    average_r = statistics.mean(returns) if returns else 0
    sharpe = 0
    if len(returns) > 1:
        std_r = statistics.stdev(returns)
        sharpe = average_r / std_r if std_r > 0 else 0

    cumulative = []
    running = 0
    for r in returns:
        running += r
        cumulative.append(running)
    max_drawdown = 0
    peak = cumulative[0] if cumulative else 0
    for val in cumulative:
        if val > peak:
            peak = val
        dd = (peak - val)
        if dd > max_drawdown:
            max_drawdown = dd

    return {
        "symbol": symbol,
        "trades": len(results),
        "wins": len(wins),
        "losses": len(losses),
        "timeouts": len(timeouts),
        "both_events": len(both_events),
        "win_rate": round(win_rate, 2),
        "profit_factor": "INF" if math.isinf(profit_factor) else round(profit_factor, 3),
        "average_R": round(average_r, 4),
        "sharpe_R": round(sharpe, 4),
        "max_drawdown_R": round(max_drawdown, 2),
        "total_return_R": round(sum(returns), 2),
    }

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--limit", type=int, default=config.BACKTEST_LIMIT)
    args = parser.parse_args()

    fetcher = DataFetcher()
    await fetcher.start()
    engine = SignalEngine()

    try:
        result = await backtest_symbol(fetcher, engine, args.symbol.upper(), args.limit)
        if result:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print("No data or insufficient trades.")
    finally:
        await fetcher.close()

if __name__ == "__main__":
    asyncio.run(main())
