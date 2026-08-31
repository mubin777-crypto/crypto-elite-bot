"""
bot.py - الملف الرئيسي للبوت مع خادم Web مدمج.
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

    # ─── خادم HTTP ───
    async def start_web_server(self):
        app = web.Application()
        app.router.add_get("/", self._handle_health_check)
        app.router.add_get("/health", self._handle_health_check)
        
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.getenv("PORT", CFG.PORT))
        self.site = web.TCPSite(runner, "0.0.0.0", port)
        await self.site.start()
        logger.info(f"🌐 Web Server running on port {port}")

    async def _handle_health_check(self, request):
        return web.json_response({
            "status": "online",
            "bot_running": self.running,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    # ... باقي الدوال (scan_market_for_opportunities, scan_symbol, run_scan_cycle, ...) كما هي ...

    async def run(self):
        self.running = True
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown)

        try:
            await self.initialize()
            await self.start_web_server()  # 🔥 تشغيل الخادم
            
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
