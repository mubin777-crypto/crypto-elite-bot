"""
bot.py - المشغل الرئيسي لبوت Crypto Elite
"""
import asyncio
import logging
from datetime import datetime, timezone
from aiohttp import web

# استيراد الإعدادات والوحدات
from config import CFG
from database import db
from fetcher import fetcher
from engine import engine
from telegram_bot import telegram

# إعداد التسجيل (Logging)
logging.basicConfig(
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "module": "%(module)s", "message": "%(message)s"}',
    level=logging.INFO
)
logger = logging.getLogger("bot")

class CryptoEliteBot:
    def __init__(self):
        self.is_running = False
        self.last_scan = {}

    async def health_check_handler(self, request):
        """نقطة فحص الصحة لإبقاء البوت نشطاً عبر UptimeRobot"""
        return web.json_response({
            "status": "online",
            "bot_running": self.is_running,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    async def start_web_server(self):
        """تشغيل سيرفر الويب لربط المنفذ على Render"""
        app = web.Application()
        app.router.add_get("/", self.health_check_handler)
        app.router.add_get("/health", self.health_check_handler)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", CFG.PORT)
        await site.start()
        logger.info(f"🌐 Web Server successfully bound to port {CFG.PORT}")

    async def scan_symbol(self, symbol: str):
        """فحص عملة واحدة بشكل آمن دون الانهيار عند حدوث خطأ"""
        try:
            df_5m = await fetcher.fetch_klines(symbol, "5m", CFG.MAX_CANDLES_PER_SYMBOL)
            df_1h = await fetcher.fetch_klines(symbol, "1h", 100)
            df_4h = await fetcher.fetch_klines(symbol, "4h", 100)
            
            # فحص سلامة البيانات المرجعة لمنع أخطاء الـ None أو Dataframe الفارغ
            if df_5m is None or df_1h is None or df_4h is None:
                return
            if df_5m.empty or df_1h.empty or df_4h.empty:
                return

            db.save_candles(symbol, "5m", df_5m)
            db.save_candles(symbol, "1h", df_1h)
            db.save_candles(symbol, "4h", df_4h)
            self.last_scan[symbol] = datetime.now(timezone.utc)

            # تحليل البيانات واستخراج الإشارات
            signal_data = engine.analyze(symbol, df_5m, df_1h, df_4h)
            if not signal_data:
                return

            # التاكد من عدم تكرار الإشارة لنفس العملة والسعر
            last_signal = db.get_last_signal(symbol)
            if last_signal:
                price_diff = abs(last_signal['price'] - signal_data['entry_price']) / signal_data['entry_price'] if signal_data['entry_price'] > 0 else 1
                is_same = (last_signal['direction'] == signal_data['type'])
                if is_same and price_diff < CFG.PRICE_TOLERANCE:
                    return

            db.save_signal(signal_data)
            await telegram.send_signal(signal_data)

        except Exception as e:
            logger.error(f"❌ Error scanning {symbol}: {str(e)}")

    async def market_scanner_loop(self):
        """حلقة مسح السوق المستمرة"""
        logger.info("🚀 Initializing background services...")
        self.is_running = True
        
        # تحميل العملات المستهدفة
        symbols = await fetcher.get_safe_symbols()
        logger.info(f"✅ تم تحميل {len(symbols)} عملة آمنة.")

        while self.is_running:
            try:
                for symbol in symbols:
                    if not self.is_running:
                        break
                    await self.scan_symbol(symbol)
                    await asyncio.sleep(1) # تأخير قصير لتجنب تجاوز حد Binance API
                
                await asyncio.sleep(CFG.SCAN_INTERVAL) # الانتظار بين الدورات
            except Exception as e:
                logger.error(f"❌ خطأ غير متوقع في حلقة المسح: {str(e)}")
                await asyncio.sleep(5)

    async def run(self):
        """إدارة دورة حياة البوت والخدمات"""
        # 1. تشغيل سيرفر الويب
        await self.start_web_server()

        # 2. تشغيل خدمة تليجرام لاستقبال الأوامر وإرسال الإشارات
        await telegram.start()

        # 3. بدء حلقة المسح في الخلفية
        try:
            await self.market_scanner_loop()
        except asyncio.CancelledError:
            logger.info("🛑 Shutdown signal received")
        finally:
            self.is_running = False
            await telegram.stop()
            logger.info("✅ Bot shut down successfully")

if __name__ == "__main__":
    bot = CryptoEliteBot()
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(bot.run())
    except (KeyboardInterrupt, SystemExit):
        pass
