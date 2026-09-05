# bot.py
# Main Application

import asyncio
import logging
import signal
import time
from datetime import datetime, timezone
import aiohttp
from aiohttp import web
import config
from database import Database
from utils import (
    DataFetcher, AdaptiveWeights, klines_to_dataframe, logger,
)
from signals import SignalEngine
from telegram_bot import TelegramBot

# ============================================================
# Trading Bot
# ============================================================
class TradingBot:
    def __init__(self):
        self.running = True
        self.fetcher = DataFetcher()
        self.db = Database(config.DB_PATH)
        self.weights = AdaptiveWeights()
        self.engine = SignalEngine(self.weights)
        self.telegram = TelegramBot(self.db, self.engine, self.fetcher)
        self.last_data_update = {}
        self.last_scan = 0
        self.scan_counter = 0
        self.daily_capital = config.INITIAL_CAPITAL
        self.last_health_alert = 0
        self.tasks = []

        self.app = web.Application()
        self.app.router.add_get("/", self.root)
        self.app.router.add_get("/health", self.health)
        self.app.router.add_post(config.WEBHOOK_PATH, self.telegram_webhook)

    # ========================================================
    # Load weights
    # ========================================================
    async def load_weights(self):
        saved = await self.db.get_weights()
        if saved:
            self.weights = AdaptiveWeights(saved)
            self.engine = SignalEngine(self.weights)
            self.telegram.signal_engine = self.engine
            logger.info(f"Adaptive weights loaded: {self.weights.to_dict()}")

    # ========================================================
    # HTTP handlers
    # ========================================================
    async def root(self, request):
        return web.json_response({
            "status": "online",
            "service": "Quant Crypto Signal System",
            "version": "2026",
        })

    async def health(self, request):
        now = time.time()
        stale = [sym for sym, ts in self.last_data_update.items() if now - ts > 900]
        return web.json_response({
            "status": "degraded" if stale else "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stale_count": len(stale),
            "stale_symbols": stale[:5],
        })

    async def telegram_webhook(self, request):
        try:
            update = await request.json()
            await self.telegram.handle_update(update)
            return web.json_response({"ok": True})
        except Exception as exc:
            logger.exception(f"Webhook error: {exc}")
            return web.json_response({"ok": False}, status=500)

    # ========================================================
    # Cooldown
    # ========================================================
    async def cooldown_allowed(self, symbol, direction):
        record = await self.db.get_cooldown(symbol)
        if not record:
            return True
        try:
            created = datetime.fromisoformat(record["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            elapsed = (now - created).total_seconds()
        except Exception:
            return True
        same_direction_limit = config.COOLDOWN_MINUTES * 60
        opposite_limit = config.OPPOSITE_COOLDOWN_HOURS * 3600
        if record["direction"] == direction:
            return elapsed >= same_direction_limit
        return elapsed >= opposite_limit

    # ========================================================
    # Daily loss
    # ========================================================
    async def daily_loss_exceeded(self):
        pnl = await self.db.get_daily_pnl()
        limit = -self.daily_capital * config.DAILY_MAX_LOSS_PERCENT
        return pnl <= limit

    # ========================================================
    # Scan symbol (exclude stablecoins)
    # ========================================================
    async def scan_symbol(self, symbol):
        if symbol in config.EXCLUDED_SYMBOLS:
            return
        if await self.daily_loss_exceeded():
            return
        klines = await self.fetcher.klines(symbol, config.ANALYSIS_INTERVAL, config.KLINE_LIMIT)
        if not klines:
            return
        self.last_data_update[symbol] = time.time()
        df = klines_to_dataframe(klines)
        if len(df) < 60:
            return
        # جلب 15m للترند
        klines_15m = await self.fetcher.klines(symbol, config.TREND_INTERVAL, 50)
        df_15m = klines_to_dataframe(klines_15m) if klines_15m else None
        result = self.engine.analyze(symbol, df, self.daily_capital, df_15m)
        if not result:
            return
        if not await self.cooldown_allowed(symbol, result["direction"]):
            return
        signal_id = await self.db.add_signal(result)
        await self.db.set_cooldown(symbol, result["direction"])
        result["signal_id"] = signal_id
        await self.telegram.broadcast(self.telegram.format_signal(result))
        logger.info(f"Signal generated: {symbol} {result['direction']} score={result['score']}")

    # ========================================================
    # Market scan
    # ========================================================
    async def scan_market(self):
        prewatch = await self.db.get_prewatch(config.MAX_PREWATCH_TO_SCAN)
        prewatch_symbols = [item["symbol"] for item in prewatch]
        symbols = list(dict.fromkeys(config.CORE_UNIVERSE + prewatch_symbols))
        tasks = [self.scan_symbol(symbol) for symbol in symbols]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Scan task failed: {result}")

    # ========================================================
    # Prewatch
    # ========================================================
    async def scan_prewatch(self):
        data = await self.fetcher.ticker_24h()
        if not isinstance(data, list):
            return
        for item in data:
            symbol = item.get("symbol", "").upper()
            if not symbol.endswith("USDT"):
                continue
            if symbol in config.CORE_UNIVERSE or symbol in config.EXCLUDED_SYMBOLS:
                continue
            try:
                change = float(item.get("priceChangePercent", 0))
                volume = float(item.get("quoteVolume", 0))
            except (ValueError, TypeError):
                continue
            if abs(change) > config.PREWATCH_PRICE_CHANGE or volume > config.PREWATCH_VOLUME_USDT:
                reasons = []
                if abs(change) > config.PREWATCH_PRICE_CHANGE:
                    reasons.append("price_move")
                if volume > config.PREWATCH_VOLUME_USDT:
                    reasons.append("high_volume")
                await self.db.add_prewatch(symbol, ",".join(reasons), change, volume)

    # ========================================================
    # Health monitor
    # ========================================================
    async def health_monitor(self):
        while self.running:
            try:
                await asyncio.sleep(config.HEALTH_CHECK_INTERVAL)
                now = time.time()
                stale = [sym for sym, ts in self.last_data_update.items() if now - ts > 900]
                if len(stale) > 3 and now - self.last_health_alert > config.HEALTH_CHECK_INTERVAL:
                    await self.telegram.broadcast(
                        f"⚠️ <b>Data Health Alert</b>\n\nStale symbols: {len(stale)}\n" + "\n".join(stale[:5])
                    )
                    self.last_health_alert = now
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"Health monitor error: {exc}")

    # ========================================================
    # Self ping
    # ========================================================
    async def self_ping(self):
        url = config.RENDER_EXTERNAL_URL or f"http://127.0.0.1:{config.PORT}/health"
        timeout = aiohttp.ClientTimeout(total=10)
        while self.running:
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as response:
                        logger.info(f"Self-ping: {response.status}")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"Self-ping failed: {exc}")
            await asyncio.sleep(config.SELF_PING_INTERVAL)

    # ========================================================
    # Evaluate open signals
    # ========================================================
    async def evaluate_open_signals(self):
        open_signals = await self.db.get_open_signals()
        if not open_signals:
            return
        for signal_row in open_signals:
            try:
                klines = await self.fetcher.klines(
                    signal_row["symbol"],
                    config.ANALYSIS_INTERVAL,
                    config.SIGNAL_MAX_HOLD_CANDLES + 1
                )
                if not klines:
                    continue
                entry = float(signal_row["entry"])
                sl = float(signal_row["sl"])
                tp = float(signal_row["tp"])
                direction = signal_row["direction"]
                outcome = None
                for candle in klines:
                    high = float(candle[2])
                    low = float(candle[3])
                    if direction == "BUY":
                        hit_sl = low <= sl
                        hit_tp = high >= tp
                    else:
                        hit_sl = high >= sl
                        hit_tp = low <= tp
                    if hit_sl:
                        outcome = -1.0
                        break
                    if hit_tp:
                        outcome = 2.0
                        break
                if outcome is None:
                    continue
                result_amount = outcome * self.daily_capital * config.RISK_PER_TRADE
                await self.db.close_signal(signal_row["id"], result_amount, outcome)
                await self.db.add_daily_pnl(self.daily_capital, result_amount)
                success = outcome > 0
                for factor in config.FACTORS:
                    self.weights.update(factor, success)
                    await self.db.save_weight(factor, self.weights.weights[factor])
                logger.info(f"Signal evaluated: id={signal_row['id']} result={outcome}")
            except Exception as exc:
                logger.exception(f"Signal evaluation error: {exc}")

    # ========================================================
    # Scanner loop
    # ========================================================
    async def scanner_loop(self):
        while self.running:
            started = time.monotonic()
            try:
                self.scan_counter += 1
                if self.scan_counter % config.PREWATCH_SCAN_EVERY == 0:
                    await self.scan_prewatch()
                await self.scan_market()
                await self.evaluate_open_signals()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"Scanner error: {exc}")
            elapsed = time.monotonic() - started
            wait = max(1, config.SCAN_INTERVAL - elapsed)
            await asyncio.sleep(wait)

    # ========================================================
    # Start (add admin as subscriber)
    # ========================================================
    async def start(self):
        config.validate_config()
        await self.db.init()
        await self.load_weights()
        await self.fetcher.start()
        await self.telegram.start()

        # Add admin as subscriber automatically
        admin_id = config.TELEGRAM_ADMIN_ID
        if admin_id:
            await self.db.add_subscriber(admin_id)
            logger.info(f"Admin {admin_id} added as subscriber")

        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", config.PORT)
        await site.start()
        logger.info(f"HTTP server listening on 0.0.0.0:{config.PORT}")

        self.tasks = [
            asyncio.create_task(self.scanner_loop()),
            asyncio.create_task(self.health_monitor()),
            asyncio.create_task(self.self_ping()),
        ]
        logger.info("🚀 Quant Crypto Signal System started successfully")

        try:
            while self.running:
                await asyncio.sleep(1)
        finally:
            self.running = False
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            await runner.cleanup()
            await self.telegram.close()
            await self.fetcher.close()
            await self.db.close()
            logger.info("System shutdown complete")

    # ========================================================
    # Stop
    # ========================================================
    def stop(self):
        self.running = False

# ============================================================
# Main
# ============================================================
async def main():
    bot = TradingBot()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, bot.stop)
        except NotImplementedError:
            pass
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())
