"""
bot.py - الملف الرئيسي للبوت مع خادم Web مدمج لـ Render Web Service.
"""
import asyncio
import os
import signal
from datetime import datetime, timezone
from typing import List, Dict
from aiohttp import web  # 🔥 مكتبة خادم الـ Web
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
        self.site = None

    async def initialize(self):
        logger.info("🚀 Initializing bot...")
        if not CFG.TELEGRAM_BOT_TOKEN:
            logger.warning("⚠️ TELEGRAM_BOT_TOKEN غير معرّف. سيتم تسجيل الإشارات في السجلات فقط.")
        else:
            logger.info("✅ TELEGRAM_BOT_TOKEN موجود.")
        await telegram.start()
        self.symbols = await fetcher.fetch_top_symbols(CFG.TOP_N_COINS)
        logger.info(f"✅ تم تحميل {len(self.symbols)} عملة.")

    # ─── 🔥 خادم HTTP بسيط لاستجابة Render ───
    async def start_web_server(self):
        """تشغيل خادم ويب مصغر على المنفذ المطلوب بواسطة Render."""
        app = web.Application()
        app.router.add_get("/", self._handle_health_check)
        app.router.add_get("/health", self._handle_health_check)
        
        runner = web.AppRunner(app)
        await runner.setup()
        port = CFG.PORT
        self.site = web.TCPSite(runner, "0.0.0.0", port)
        await self.site.start()
        logger.info(f"🌐 Web Server running on port {port}")

    async def _handle_health_check(self, request):
        """نقطة الفحص الروتينية (Health Check Endpoint)."""
        return web.json_response({
            "status": "online",
            "bot_running": self.running,
            "last_scan_time": db.get_scan_state("last_scan"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    async def scan_market_for_opportunities(self):
        try:
            logger.info("🔍 مسح سريع للفرص (دفعة واحدة)...")
            tickers = await fetcher.fetch_24hr_tickers()
            if not tickers:
                return

            for item in tickers:
                symbol = item["symbol"]
                change = item["change_24h"]
                volume = item["volume_24h"]

                if abs(change) > 3.0 or volume > 2_000_000:
                    score = min(100, abs(change) * 8 + (volume / 100_000))
                    reason = f"تغير {change:.1f}% | حجم ${volume/1_000_000:.2f}M"
                    db.add_to_prewatch(symbol, score, change, volume, reason)
                    logger.info(f"🔭 تمت إضافة {symbol} إلى Pre-watch: {reason}")

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

            signal = engine.analyze(symbol, df_5m, df_1h, df_4h)
            if not signal:
                return

            last_signal = db.get_last_signal(symbol)
            if last_signal:
                price_diff = abs(last_signal['price'] - signal['entry_price']) / signal['entry_price'] if signal['entry_price'] > 0 else 1
                is_same = (last_signal['direction'] == signal['type'])
                if is_same and price_diff < CFG.PRICE_TOLERANCE:
                    logger.info(f"⏭️ تخطي {symbol}: مكرر")
                    return
                is_opp = (last_signal['direction'] == 'BUY' and signal['type'] == 'SELL') or \
                         (last_signal['direction'] == 'SELL' and signal['type'] == 'BUY')
                if is_opp:
                    t_diff = (datetime.now(timezone.utc) - datetime.fromisoformat(last_signal['timestamp'])).total_seconds() / 60
                    if t_diff < CFG.OPPOSITE_SIGNAL_COOLDOWN:
                        logger.info(f"⏭️ تخطي {symbol}: معاكس")
                        return

            db.save_signal(signal)
            await telegram.send_signal(signal)
            await db.set_cooldown(symbol, datetime.now(timezone.utc).isoformat())
            db.set_last_signal(symbol, signal['type'], signal['entry_price'], signal['type'], signal['type'])
            db.update_daily_stats(0, False)
            logger.info("✅ Signal generated", extra={"symbol": symbol, "type": signal["type"]})

        except Exception as e:
            logger.error(f"❌ Error scanning {symbol}", extra={"error": str(e)})

    async def run_scan_cycle(self):
        logger.info("🔄 Starting scan cycle...")
        
        if int(datetime.now(timezone.utc).minute) % 3 == 0:
            await self.scan_market_for_opportunities()
        
        pre_watch_symbols = db.get_active_prewatch(CFG.MAX_PREWATCH_TO_SCAN) if CFG.SCAN_UNLISTED_SYMBOLS else []
        all_symbols = list(set(CFG.CORE_UNIVERSE + self.symbols + pre_watch_symbols))
        logger.info(f"🔄 فحص {len(all_symbols)} عملة (Pre-watch: {len(pre_watch_symbols)})...")
        
        for symbol in all_symbols:
            if not self.running: break
            await self.scan_symbol(symbol)
            await asyncio.sleep(CFG.REQUEST_DELAY)
            
        db.set_scan_state("symbols", ",".join(all_symbols))
        db.set_scan_state("last_scan", datetime.now(timezone.utc).isoformat())

    async def health_check(self):
        while self.running:
            await asyncio.sleep(300)
            stale = [s for s, t in self.last_scan.items() if (datetime.now(timezone.utc) - t).total_seconds() > 300]
            if stale:
                await telegram.send_alert(f"⚠️ توقف تحديث {len(stale)} عملة.")
                logger.warning("Stale data", extra={"symbols": stale})

    async def self_ping(self):
        if not CFG.RENDER_EXTERNAL_URL: return
        while self.running:
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(CFG.RENDER_EXTERNAL_URL, timeout=10) as resp:
                        logger.info("✅ Self-ping OK", extra={"status": resp.status})
            except Exception as e:
                logger.warning("⚠️ Self-ping failed", extra={"error": str(e)})
            await asyncio.sleep(CFG.SELF_PING_INTERVAL)

    async def run(self):
        self.running = True
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown)

        try:
            await self.initialize()
            # 🔥 تشغيل خادم Web في نفس الـ Event Loop
            await self.start_web_server()
            
            asyncio.create_task(self.health_check())
            asyncio.create_task(self.self_ping())
            
            while self.running:
                await self.run_scan_cycle()
                await asyncio.sleep(CFG.SCAN_INTERVAL_SECONDS)
                if datetime.now(timezone.utc).minute == 0:
                    self.symbols = await fetcher.fetch_top_symbols(CFG.TOP_N_COINS)
        except Exception as e:
            logger.critical("💥 Bot crashed", extra={"error": str(e)})
            raise
        finally:
            await self.shutdown()

    def _shutdown(self):
        logger.info("🛑 Shutdown signal received")
        self.running = False

    async def shutdown(self):
        self.running = False
        if self.site:
            await self.site.stop()
        await fetcher.close()
        await telegram.stop()
        logger.info("✅ Bot shut down")

if __name__ == "__main__":
    bot = CryptoSignalBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
