"""
bot.py - النسخة المعالجة جذرياً لضمان Port Binding الفوري على Render
"""
import asyncio
import os
import signal
from datetime import datetime, timezone
from typing import List, Dict
from aiohttp import web
from config import CFG
from utils import fetcher, logger
from database import db
from signals import engine
from telegram_bot import telegram

class CryptoSignalBot:
    def __init__(self):
        self.running = False
        self.symbols: List[str] = []
        self.last_scan: Dict[str, datetime] = {}
        self.runner = None

    async def _handle_health_check(self, request):
        return web.json_response({
            "status": "online",
            "bot_running": self.running,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }, status=200)

    async def start_web_server(self):
        """ربط المنفذ فوراً لمنع Render من إنهاء الحاوية"""
        app = web.Application()
        app.router.add_get("/", self._handle_health_check)
        app.router.add_get("/health", self._handle_health_check)
        
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        port = int(os.getenv("PORT", "10000"))
        site = web.TCPSite(self.runner, "0.0.0.0", port)
        await site.start()
        logger.info(f"🌐 Web Server successfully bound to port {port}")

    async def initialize_async_services(self):
        """تهيئة الخدمات الخارجية بعد ضمان فتح المنفذ"""
        logger.info("🚀 Initializing background services...")
        token = CFG.TELEGRAM_BOT_TOKEN
        if token:
            logger.info(f"✅ TELEGRAM_TOKEN جاهز (الطول: {len(token)} حرف)")
        
        await telegram.start()
        
        self.symbols = await fetcher.fetch_top_symbols(CFG.TOP_N_COINS)
        if not self.symbols:
            logger.warning("⚠️ لم يتم جلب أي عملات، سيتم استخدام القائمة الأساسية.")
            self.symbols = [s for s in CFG.CORE_UNIVERSE if s not in CFG.EXCLUDED_SYMBOLS][:50]
        logger.info(f"✅ تم تحميل {len(self.symbols)} عملة آمنة.")

    async def scan_market_for_opportunities(self):
        try:
            tickers = await fetcher.fetch_24hr_tickers()
            if not tickers:
                return

            for item in tickers:
                symbol = item["symbol"]
                change = item["change_24h"]
                volume = item["volume_24h"]

                if volume >= 1_000_000 and (3.0 <= abs(change) <= 50.0):
                    score = min(100, abs(change) * 8 + (volume / 500_000))
                    reason = f"تغير {change:.1f}% | حجم ${volume/1_000_000:.2f}M"
                    db.add_to_prewatch(symbol, score, change, volume, reason)

        except Exception as e:
            logger.error(f"خطأ في ماسح الفرص: {e}")

    async def scan_symbol(self, symbol: str):
        try:
            df_5m = await fetcher.fetch_klines(symbol, "5m", CFG.MAX_CANDLES_PER_SYMBOL)
            df_1h = await fetcher.fetch_klines(symbol, "1h", 100)
            df_4h = await fetcher.fetch_klines(symbol, "4h", 100)
            if df_5m.empty or df_1h.empty or df_4h.empty:
                return

            db.save_candles(symbol, "5m", df_5m)
            db.save_candles(symbol, "1h", df_1h)
            db.save_candles(symbol, "4h", df_4h)
            self.last_scan[symbol] = datetime.now(timezone.utc)

            signal_data = engine.analyze(symbol, df_5m, df_1h, df_4h)
            if not signal_data:
                return

            last_signal = db.get_last_signal(symbol)
            if last_signal:
                price_diff = abs(last_signal['price'] - signal_data['entry_price']) / signal_data['entry_price'] if signal_data['entry_price'] > 0 else 1
                is_same = (last_signal['direction'] == signal_data['type'])
                if is_same and price_diff < CFG.PRICE_TOLERANCE:
                    return
                is_opp = (last_signal['direction'] == 'BUY' and signal_data['type'] == 'SELL') or \
                         (last_signal['direction'] == 'SELL' and signal_data['type'] == 'BUY')
                if is_opp:
                    t_diff = (datetime.now(timezone.utc) - datetime.fromisoformat(last_signal['timestamp'])).total_seconds() / 60
                    if t_diff < CFG.OPPOSITE_SIGNAL_COOLDOWN:
                        return

            db.save_signal(signal_data)
            await telegram.send_signal(signal_data)
            await db.set_cooldown(symbol, datetime.now(timezone.utc).isoformat())
            db.set_last_signal(symbol, signal_data['type'], signal_data['entry_price'], signal_data['type'], signal_data['type'])
            db.update_daily_stats(0, False)

        except Exception as e:
            logger.error(f"❌ Error scanning {symbol}", extra={"error": str(e)})

    async def run_scan_loop(self):
        while self.running:
            try:
                if int(datetime.now(timezone.utc).minute) % 3 == 0:
                    await self.scan_market_for_opportunities()

                pre_watch_symbols = db.get_active_prewatch(CFG.MAX_PREWATCH_TO_SCAN) if CFG.SCAN_UNLISTED_SYMBOLS else []
                all_symbols = list(set([s for s in (CFG.CORE_UNIVERSE + self.symbols + pre_watch_symbols) if s not in CFG.EXCLUDED_SYMBOLS]))
                
                for symbol in all_symbols:
                    if not self.running: 
                        break
                    await self.scan_symbol(symbol)
                    await asyncio.sleep(CFG.REQUEST_DELAY)

            except Exception as e:
                logger.error(f"❌ خطأ في دورة المسح: {e}")
                
            await asyncio.sleep(CFG.SCAN_INTERVAL_SECONDS)

    def _shutdown(self):
        logger.info("🛑 Shutdown signal received")
        self.running = False

    async def run(self):
        self.running = True
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._shutdown)
            except NotImplementedError:
                pass

        try:
            # Step 1: فتح خادم الويب فوراً لاستجابة Render
            await self.start_web_server()
            
            # Step 2: تهيئة الخدمات في الخلفية
            await self.initialize_async_services()
            
            # Step 3: بدء حلقة المسح
            asyncio.create_task(self.run_scan_loop())
            
            while self.running:
                await asyncio.sleep(1)
                        
        except Exception as e:
            logger.critical("💥 Bot crashed", extra={"error": str(e)})
            raise
        finally:
            await self.shutdown()

    async def shutdown(self):
        self.running = False
        if self.runner:
            await self.runner.cleanup()
        await fetcher.close()
        await telegram.stop()
        logger.info("✅ Bot shut down successfully")

if __name__ == "__main__":
    bot = CryptoSignalBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
