"""
bot.py - الملف الرئيسي للمشروع (Crypto Signal Bot)
"""
import asyncio
import os
import signal
from datetime import datetime, timezone
from typing import List, Dict
from aiohttp import web

# استدعاء الإعدادات والأدوات وقواعد البيانات
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
        """تهيئة البوت، التلجرام، وقائمة العملات"""
        logger.info("🚀 Starting Signal Engine Initializer...")
        
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if token:
            CFG.TELEGRAM_BOT_TOKEN = token

        # بدء تشغيل التلجرام والمستمع (Polling)
        await telegram.start()

        # جلب قائمة أفضل العملات
        try:
            self.symbols = await fetcher.fetch_top_symbols(CFG.TOP_N_COINS)
        except Exception as e:
            logger.warning(f"⚠️ خطأ أثناء جلب قائمة العملات: {e}")
            self.symbols = []

        if not self.symbols:
            logger.warning("⚠️ تعذر جلب التوب عملات، استخدام قائمة Core الاحتياطية.")
            self.symbols = getattr(CFG, "CORE_UNIVERSE", [])[:50]

        logger.info(f"✅ Master Symbol Universe: {len(self.symbols)} active tickers.")

    async def start_web_server(self):
        """تشغيل سيرفر Web خفيف باستخدام aiohttp لإبقاء الخدمة أونلاين على Render"""
        app = web.Application()
        app.router.add_get("/", self._handle_health_check)
        app.router.add_get("/health", self._handle_health_check)

        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.getenv("PORT", getattr(CFG, "PORT", 10000)))
        
        self.site = web.TCPSite(runner, "0.0.0.0", port)
        await self.site.start()
        logger.info(f"🌐 Server actively listening on 0.0.0.0:{port}")

    async def _handle_health_check(self, request):
        return web.json_response({
            "status": "online",
            "bot_running": self.running,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    async def scan_market_for_opportunities(self):
        """فحص حركة 24 ساعة للبحث عن فرص للـ Prewatch"""
        try:
            tickers = await fetcher.fetch_24hr_tickers()
            if not tickers:
                return

            for item in tickers:
                symbol, change, volume = item["symbol"], item["change_24h"], item["volume_24h"]
                if abs(change) > 3.0 or volume > 2_000_000:
                    score = min(100, abs(change) * 8 + (volume / 100_000))
                    reason = f"تغير {change:.1f}% | حجم ${volume/1_000_000:.2f}M"
                    db.add_to_prewatch(symbol, score, change, volume, reason)
        except Exception as e:
            logger.error(f"Market prewatch scan issue: {e}")

    async def scan_symbol(self, symbol: str):
        """تحليل عملة واحدة على الأطر الزمنية المختلفة وإرسال الإشارة عند التحقق"""
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

            # معالجة الإشارة بواسطة Engine
            signal_obj = engine.analyze(symbol, df_5m, df_1h, df_4h)
            if not signal_obj:
                return

            # فحص التكرار وفترة التهدئة (Cooldown)
            last_signal = db.get_last_signal(symbol)
            if last_signal:
                price_diff = abs(last_signal['price'] - signal_obj['entry_price']) / signal_obj['entry_price'] if signal_obj['entry_price'] > 0 else 1
                if last_signal['direction'] == signal_obj['type'] and price_diff < CFG.PRICE_TOLERANCE:
                    return

                is_opp = (last_signal['direction'] == 'BUY' and signal_obj['type'] == 'SELL') or \
                         (last_signal['direction'] == 'SELL' and signal_obj['type'] == 'BUY')
                if is_opp:
                    t_diff = (datetime.now(timezone.utc) - datetime.fromisoformat(last_signal['timestamp'])).total_seconds() / 60
                    if t_diff < CFG.OPPOSITE_SIGNAL_COOLDOWN:
                        return

            # حفظ وإرسال الإشارة
            db.save_signal(signal_obj)
            await telegram.send_signal(signal_obj)
            db.set_cooldown(symbol, datetime.now(timezone.utc).isoformat())
            db.set_last_signal(symbol, signal_obj['type'], signal_obj['entry_price'], signal_obj['type'], signal_obj['type'])
            db.update_daily_stats(0, False)
            logger.info("⚡ Signal Dispatched", extra={"symbol": symbol, "type": signal_obj["type"]})

        except Exception as e:
            logger.error(f"Execution error on {symbol}: {e}")

    async def run_scan_cycle(self):
        """دورة فحص السوق الشاملة"""
        try:
            if datetime.now(timezone.utc).minute % 3 == 0:
                await self.scan_market_for_opportunities()
        except Exception as e:
            logger.error(f"Prewatch cycle failure: {e}")

        pre_watch_symbols = db.get_active_prewatch(CFG.MAX_PREWATCH_TO_SCAN) if getattr(CFG, "SCAN_UNLISTED_SYMBOLS", False) else []
        core_universe = getattr(CFG, "CORE_UNIVERSE", [])
        all_symbols = list(set(core_universe + self.symbols + pre_watch_symbols))

        for symbol in all_symbols:
            if not self.running:
                break
            await self.scan_symbol(symbol)
            await asyncio.sleep(getattr(CFG, "REQUEST_DELAY", 0.5))

        db.set_scan_state("symbols", ",".join(all_symbols))
        db.set_scan_state("last_scan", datetime.now(timezone.utc).isoformat())

    async def self_ping(self):
        """منع خادم Render من الدخول في وضع النوم (Idle)"""
        url = getattr(CFG, "RENDER_EXTERNAL_URL", None)
        if not url:
            port = os.environ.get("PORT", getattr(CFG, "PORT", 10000))
            url = f"http://127.0.0.1:{port}/health"

        interval = getattr(CFG, "SELF_PING_INTERVAL", 300)

        import aiohttp
        while self.running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=10) as resp:
                        logger.info(f"Keep-Alive ping response: {resp.status}")
            except Exception as e:
                logger.warning(f"Self-ping issue: {e}")
            await asyncio.sleep(interval)

    def _shutdown(self):
        logger.info("🛑 Shutting down signal engine...")
        self.running = False

    async def run(self):
        self.running = True
        loop = asyncio.get_running_loop()
        
        # التقاط إشارات الإغلاق
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._shutdown)
            except NotImplementedError:
                pass

        try:
            await self.initialize()
            await self.start_web_server()
            asyncio.create_task(self.self_ping())

            while self.running:
                await self.run_scan_cycle()
                await asyncio.sleep(getattr(CFG, "SCAN_INTERVAL_SECONDS", 60))
        finally:
            await self.shutdown()

    async def shutdown(self):
        self.running = False
        if self.site:
            await self.site.stop()
        if hasattr(fetcher, 'close'):
            await fetcher.close()
        await telegram.stop()
        logger.info("👋 Bot shut down cleanly.")


if __name__ == "__main__":
    bot = CryptoSignalBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Stopped manually.")
