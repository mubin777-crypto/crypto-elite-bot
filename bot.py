# bot.py
# Main Application - مع خادم ويب متكامل

import asyncio
import json
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
# Web Handlers
# ============================================================

async def handle_health(request):
    """نقطة فحص السلامة (Health Check)"""
    return web.json_response({
        "status": "healthy",
        "service": "Quant Signal Engine",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }, status=200)

async def handle_telegram_webhook(request):
    """استقبال تحديثات Telegram Webhook"""
    try:
        data = await request.json()
        bot_instance = request.app['bot_instance']

        await bot_instance.telegram.process_update(data)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Error handling Telegram Webhook: {e}")
        return web.Response(status=500)

async def handle_status_api(request):
    """استرجاع حالة النظام عبر REST API"""
    bot_instance = request.app['bot_instance']
    try:
        open_signals = await bot_instance.db.get_open_signals()
        daily_stats = await bot_instance.db.get_daily_pnl()

        return web.json_response({
            "open_trades": len(open_signals),
            "daily_pnl": daily_stats.get("pnl", 0.0),
            "wins": daily_stats.get("wins", 0),
            "losses": daily_stats.get("losses", 0),
            "breakeven": daily_stats.get("breakeven", 0),
            "inconclusive": daily_stats.get("inconclusive", 0),
            "timeout": daily_stats.get("timeout", 0),
            "subscribers": len(await bot_instance.db.get_subscribers()),
            "known_symbols": len(bot_instance.known_symbols),
            "websocket_active": bot_instance.fetcher.websocket.running if hasattr(bot_instance.fetcher, 'websocket') else False,
        }, status=200)
    except Exception as e:
        logger.error(f"Error fetching status: {e}")
        return web.json_response({"error": str(e)}, status=500)

# ============================================================
# TradingBot Class
# ============================================================

class TradingBot:
    def __init__(self):
        self.running = True
        self.fetcher = DataFetcher()
        self.db = Database()
        self.weights = AdaptiveWeights()
        self.engine = SignalEngine(self.weights)
        self.telegram = TelegramBot(self.db, self.engine, self.fetcher)
        self.last_data_update = {}
        self.last_data_update_max = config.MAX_LAST_DATA_UPDATE
        self.scan_counter = 0
        self.daily_capital = config.INITIAL_CAPITAL
        self.last_health_alert = 0
        self.tasks = []
        self.known_symbols = set(config.CORE_UNIVERSE)
        self.web_runner = None

    # ============================================================
    # Web Server
    # ============================================================
    async def init_web_server(self):
        app = web.Application()
        app['bot_instance'] = self

        app.router.add_get("/", handle_health)
        app.router.add_get("/health", handle_health)
        app.router.add_post(config.WEBHOOK_PATH, handle_telegram_webhook)
        app.router.add_get("/api/status", handle_status_api)

        self.web_runner = web.AppRunner(app)
        await self.web_runner.setup()
        site = web.TCPSite(self.web_runner, "0.0.0.0", config.PORT)
        await site.start()
        logger.info(f"🌐 Web Server running on port {config.PORT}")
        logger.info(f"📍 Health: http://0.0.0.0:{config.PORT}/health")
        logger.info(f"📍 Webhook: {config.WEBHOOK_URL}{config.WEBHOOK_PATH}")
        logger.info(f"📍 Status API: http://0.0.0.0:{config.PORT}/api/status")
        return self.web_runner

    async def setup_telegram_webhook(self):
        if config.TELEGRAM_USE_WEBHOOK and config.WEBHOOK_URL:
            full_webhook_url = f"{config.WEBHOOK_URL.rstrip('/')}{config.WEBHOOK_PATH}"
            success = await self.telegram.set_webhook(full_webhook_url)
            if success:
                logger.info(f"✅ Telegram Webhook set to: {full_webhook_url}")
            else:
                logger.error("❌ Failed to set Telegram Webhook")
        else:
            logger.info("ℹ️ Telegram Webhook disabled, using Polling mode")

    # ============================================================
    # Load weights
    # ============================================================
    async def load_weights(self):
        saved = await self.db.get_weights()
        if saved:
            self.weights = AdaptiveWeights(saved)
            self.engine = SignalEngine(self.weights)
            self.telegram.signal_engine = self.engine
            logger.info(f"Adaptive weights loaded: {self.weights.to_dict()}")

    # ============================================================
    # Cooldown & Daily Loss
    # ============================================================
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

    async def daily_loss_exceeded(self):
        stats = await self.db.get_daily_pnl()
        limit = -self.daily_capital * config.DAILY_MAX_LOSS_PERCENT
        return stats["pnl"] <= limit

    # ============================================================
    # Scan Symbol
    # ============================================================
    async def scan_symbol(self, symbol):
        if symbol in config.EXCLUDED_SYMBOLS:
            return
        if await self.daily_loss_exceeded():
            return
        klines = await self.fetcher.klines(symbol, config.ANALYSIS_INTERVAL, config.KLINE_LIMIT)
        if not klines:
            return

        self.last_data_update[symbol] = time.time()
        if len(self.last_data_update) > self.last_data_update_max:
            oldest = min(self.last_data_update, key=self.last_data_update.get)
            del self.last_data_update[oldest]

        df = klines_to_dataframe(klines)
        if len(df) < 60:
            return

        klines_15m = await self.fetcher.klines(symbol, config.TREND_INTERVAL, 50)
        df_15m = klines_to_dataframe(klines_15m) if klines_15m else None
        result = self.engine.analyze(symbol, df, self.daily_capital, df_15m)
        if not result:
            return
        if not await self.cooldown_allowed(symbol, result["direction"]):
            return

        signal_id = await self.db.add_signal(result)
        result["signal_id"] = signal_id

        formatted = self.telegram.format_signal(result)
        await self.telegram.broadcast(formatted)

        await self.db.set_cooldown(symbol, result["direction"])
        logger.info(f"📊 Signal generated: {symbol} {result['direction']} score={result['score']} (id={signal_id})")

    # ============================================================
    # Scan Market
    # ============================================================
    async def scan_market(self):
        prewatch = await self.db.get_prewatch(config.MAX_PREWATCH_TO_SCAN)
        prewatch_symbols = [item["symbol"] for item in prewatch if item["symbol"] not in self.known_symbols]
        symbols = list(dict.fromkeys(list(self.known_symbols) + prewatch_symbols))
        tasks = [self.scan_symbol(symbol) for symbol in symbols]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Scan task failed: {result}")

    # ============================================================
    # Scan Pre-watch
    # ============================================================
    async def scan_prewatch(self):
        data = await self.fetcher.ticker_24h()
        if not isinstance(data, list):
            return
        for item in data:
            symbol = item.get("symbol", "").upper()
            if not symbol.endswith("USDT"):
                continue
            if symbol in self.known_symbols or symbol in config.EXCLUDED_SYMBOLS:
                continue
            try:
                change = float(item.get("priceChangePercent", 0))
                volume = float(item.get("quoteVolume", 0))
                trades = int(item.get("count", 0))
                price = float(item.get("lastPrice", 0))
            except (ValueError, TypeError):
                continue
            if volume < config.PREWATCH_MIN_VOLUME_USDT:
                continue
            if trades < config.PREWATCH_MIN_TRADES:
                continue
            if price > config.PREWATCH_MAX_PRICE or price < config.PREWATCH_MIN_PRICE:
                continue
            if abs(change) > config.PREWATCH_PRICE_CHANGE or volume > config.PREWATCH_VOLUME_USDT:
                reasons = []
                if abs(change) > config.PREWATCH_PRICE_CHANGE:
                    reasons.append("price_move")
                if volume > config.PREWATCH_VOLUME_USDT:
                    reasons.append("high_volume")
                await self.db.add_prewatch(symbol, ",".join(reasons), change, volume, trades, price)
                self.known_symbols.add(symbol)

    # ============================================================
    # Health Monitor
    # ============================================================
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

    # ============================================================
    # Self Ping (Keep-Alive)
    # ============================================================
    async def self_ping(self):
        url = config.RENDER_EXTERNAL_URL or f"http://127.0.0.1:{config.PORT}/health"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while self.running:
                try:
                    async with session.get(url) as response:
                        logger.debug(f"Self-ping: {response.status}")
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.warning(f"Self-ping failed: {exc}")
                await asyncio.sleep(config.SELF_PING_INTERVAL)

    # ============================================================
    # Evaluate Open Signals
    # ============================================================
    async def evaluate_open_signals(self):
        open_signals = await self.db.get_open_signals()
        if not open_signals:
            return
        for signal_row in open_signals:
            try:
                signal_time = datetime.fromisoformat(signal_row["created_at"])
                klines = await self.fetcher.klines(
                    signal_row["symbol"],
                    config.ANALYSIS_INTERVAL,
                    config.SIGNAL_MAX_HOLD_CANDLES + 5
                )
                if not klines:
                    continue
                entry = float(signal_row["entry"])
                sl = float(signal_row["sl"])
                tp = float(signal_row["tp"])
                direction = signal_row["direction"]
                outcome = None
                exit_reason = None

                for candle in klines:
                    candle_time = datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc)
                    if candle_time <= signal_time:
                        continue
                    high = float(candle[2])
                    low = float(candle[3])
                    hit_sl = low <= sl if direction == "BUY" else high >= sl
                    hit_tp = high >= tp if direction == "BUY" else low <= tp

                    if hit_sl and hit_tp:
                        outcome = -1.0
                        exit_reason = "SL_TP_SAME_CANDLE"
                        break
                    elif hit_sl:
                        outcome = -1.0
                        exit_reason = "SL"
                        break
                    elif hit_tp:
                        outcome = 2.0
                        exit_reason = "TP"
                        break

                if outcome is None:
                    outcome = 0.0
                    exit_reason = "TIMEOUT"

                result_amount = outcome * self.daily_capital * config.RISK_PER_TRADE
                await self.db.close_signal(signal_row["id"], result_amount, outcome, exit_reason)

                if outcome > 0:
                    category = "win"
                elif outcome < 0:
                    category = "loss"
                elif exit_reason == "TIMEOUT":
                    category = "timeout"
                else:
                    category = "breakeven"

                await self.db.add_daily_result(self.daily_capital, result_amount, category)

                signal_data = await self.db.get_signal(signal_row["id"])
                if signal_data and signal_data.get("factor_contributions"):
                    try:
                        if isinstance(signal_data["factor_contributions"], str):
                            factor_contributions = json.loads(signal_data["factor_contributions"])
                        else:
                            factor_contributions = signal_data["factor_contributions"]
                    except:
                        factor_contributions = {}

                    success = outcome > 0
                    for factor, contribution in factor_contributions.items():
                        if factor in config.FACTORS:
                            self.weights.update(factor, success, contribution=contribution)
                            await self.db.save_weight(factor, self.weights.weights[factor])
                else:
                    success = outcome > 0
                    for factor in config.FACTORS:
                        self.weights.update(factor, success, contribution=0.5)
                        await self.db.save_weight(factor, self.weights.weights[factor])

                logger.info(f"📈 Signal evaluated: id={signal_row['id']} result={outcome} reason={exit_reason}")
            except Exception as exc:
                logger.exception(f"Signal evaluation error: {exc}")

    # ============================================================
    # Scanner Loop
    # ============================================================
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

    # ============================================================
    # Start
    # ============================================================
    async def start(self):
        config.validate_config()

        logger.info(f"🔧 BINANCE_ENDPOINTS: {config.BINANCE_ENDPOINTS}")
        logger.info(f"🔧 DATABASE: {'PostgreSQL' if config.USE_POSTGRES else 'SQLite'}")
        logger.info(f"🔧 TELEGRAM_MODE: {'Webhook' if config.TELEGRAM_USE_WEBHOOK else 'Polling'}")

        # 1️⃣ تهيئة قاعدة البيانات
        await self.db.init()

        # 2️⃣ تحميل الأوزان
        await self.load_weights()

        # 3️⃣ بدء جلب البيانات (WebSocket + REST)
        await self.fetcher.start()

        # 4️⃣ بدء خادم الويب
        await self.init_web_server()

        # 5️⃣ تفعيل Webhook (إذا كان مفعلاً)
        await self.setup_telegram_webhook()

        # 6️⃣ بدء Telegram Bot (Polling إذا لزم الأمر)
        await self.telegram.start()

        # 7️⃣ إضافة الأدمن كمشترك
        admin_id = config.TELEGRAM_ADMIN_ID
        if admin_id:
            success = await self.db.add_subscriber(admin_id)
            if success:
                logger.info(f"✅ Admin {admin_id} added as subscriber")
                await self.telegram.send_message(admin_id, "✅ Bot started successfully!")
            else:
                logger.error(f"❌ Failed to add admin {admin_id} as subscriber")

        count = await self.db.get_subscribers()
        logger.info(f"📊 Total active subscribers: {len(count)}")

        # 8️⃣ بدء المهام الخلفية
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
            await self.shutdown()

    # ============================================================
    # Shutdown
    # ============================================================
    async def shutdown(self):
        logger.info("🛑 Shutting down...")

        if config.TELEGRAM_USE_WEBHOOK:
            logger.info("🗑️ Deleting Telegram webhook...")
            await self.telegram.delete_webhook()

        self.running = False
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

        if self.web_runner:
            await self.web_runner.cleanup()

        await self.telegram.close()
        await self.fetcher.close()
        await self.db.close()
        logger.info("✅ System shutdown complete")

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
