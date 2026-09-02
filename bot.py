"""
bot.py - الملف الرئيسي مع خادم Webhook مدمج وتحسينات السرعة والمراقبة.
"""
import asyncio
import os
import signal
import json
from datetime import datetime, timezone
from typing import List, Dict
from aiohttp import web
from telegram import Update
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
        self._last_alert_sent = datetime.now(timezone.utc)

    async def initialize(self):
        logger.info("🚀 Initializing bot...")
        
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if token:
            logger.info(f"✅ TELEGRAM_BOT_TOKEN موجود في البيئة (الطول: {len(token)} حرف)")
            CFG.TELEGRAM_BOT_TOKEN = token
        else:
            if CFG.TELEGRAM_BOT_TOKEN:
                logger.info("ℹ️ تم العثور على التوكن في CFG.")
            else:
                logger.warning("⚠️ TELEGRAM_BOT_TOKEN غير موجود في البيئة ولا في CFG.")
        
        self.symbols = await fetcher.fetch_top_symbols(CFG.TOP_N_COINS)
        if not self.symbols:
            logger.warning("⚠️ لم يتم جلب أي عملات، سيتم استخدام القائمة الأساسية.")
            self.symbols = CFG.CORE_UNIVERSE[:50]
        logger.info(f"✅ تم تحميل {len(self.symbols)} عملة.")

    # ─── خادم HTTP ───
    async def start_web_server(self):
        app = web.Application()
        app.router.add_get("/", self._handle_health_check)
        app.router.add_get("/health", self._handle_health_check)
        app.router.add_get("/webhook", self._handle_webhook_get)
        app.router.add_post("/webhook", self._handle_webhook)
        
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

    async def _handle_webhook_get(self, request):
        return web.json_response({"status": "ok", "message": "Webhook endpoint is reachable"})

    # 🔥 معالج Webhook المحسّن مع سجلات إضافية
    async def _handle_webhook(self, request):
        try:
            data = await request.json()
            update_id = data.get('update_id', 'unknown')
            logger.info(f"📩 Webhook received: {update_id}")
            
            # تسجيل محتوى التحديث للمساعدة في التصحيح
            logger.info(f"📦 Full update: {json.dumps(data, ensure_ascii=False)[:500]}")
            
            if telegram.bot is None:
                logger.error("❌ Telegram bot not initialized")
                return web.json_response({"status": "error", "message": "Bot not ready"}, status=500)
            
            # إنشاء كائن التحديث
            update = Update.de_json(data, telegram.bot)
            
            # تسجيل معلومات الرسالة
            if update.message:
                user_id = update.effective_user.id if update.effective_user else "unknown"
                text = update.message.text if update.message.text else "[no text]"
                logger.info(f"📩 Message from {user_id}: {text}")
            
            # معالجة التحديث عبر التطبيق
            if telegram.app:
                # 🔥 التأكد من أن التطبيق يحتوي على المعالجات
                logger.info(f"🔍 Processing update with app handlers: {len(telegram.app.handlers) if telegram.app.handlers else 0} handlers")
                await telegram.app.process_update(update)
                logger.info("✅ Update processed successfully")
                return web.json_response({"status": "ok"})
            else:
                logger.error("❌ Telegram app not initialized")
                return web.json_response({"status": "error", "message": "App not ready"}, status=500)
                
        except Exception as e:
            logger.error(f"❌ Webhook error: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    # ─── ماسح الفرص ───
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

    # ─── مسح عملة واحدة ───
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
            logger.debug(f"⚠️ خطأ أثناء مسح {symbol}: {e}")

    # ─── دورة المسح ───
    async def run_scan_cycle(self):
        logger.info("🔄 Starting scan cycle...")
        try:
            if int(datetime.now(timezone.utc).minute) % 3 == 0:
                await self.scan_market_for_opportunities()
        except Exception as e:
            logger.error(f"خطأ في ماسح الفرص: {e}")
        
        pre_watch_symbols = db.get_active_prewatch(CFG.MAX_PREWATCH_TO_SCAN) if CFG.SCAN_UNLISTED_SYMBOLS else []
        all_symbols = list(set(CFG.CORE_UNIVERSE + self.symbols + pre_watch_symbols))
        logger.info(f"🔄 فحص {len(all_symbols)} عملة (Pre-watch: {len(pre_watch_symbols)})...")
        
        for symbol in all_symbols:
            if not self.running: break
            await self.scan_symbol(symbol)
            await asyncio.sleep(CFG.REQUEST_DELAY)
            
        db.set_scan_state("symbols", ",".join(all_symbols))
        db.set_scan_state("last_scan", datetime.now(timezone.utc).isoformat())

    # ─── فحص الصحة (محسّن) ───
    async def health_check(self):
        while self.running:
            await asyncio.sleep(CFG.HEALTH_CHECK_INTERVAL)
            now = datetime.now(timezone.utc)
            stale_symbols = []
            for symbol, last_time in self.last_scan.items():
                if (now - last_time).total_seconds() > 300:
                    stale_symbols.append(symbol)
            if stale_symbols and len(stale_symbols) >= CFG.MIN_STALE_SYMBOLS_FOR_ALERT:
                if (now - self._last_alert_sent).total_seconds() > 600:
                    limited = stale_symbols[:CFG.MAX_STALE_SYMBOLS_TO_REPORT]
                    msg = f"⚠️ توقف تحديث {len(stale_symbols)} عملة: {', '.join(limited)}..."
                    await telegram.send_alert(msg)
                    self._last_alert_sent = now
                    logger.warning("Stale data", extra={"symbols": stale_symbols})
            else:
                self._last_alert_sent = now

    # ─── Self Ping ───
    async def self_ping(self):
        if not CFG.RENDER_EXTERNAL_URL:
            port = int(os.getenv("PORT", CFG.PORT))
            url = f"http://127.0.0.1:{port}/health"
        else:
            url = CFG.RENDER_EXTERNAL_URL
        while self.running:
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=10) as resp:
                        logger.info("✅ Self-ping OK", extra={"status": resp.status})
            except Exception as e:
                logger.warning("⚠️ Self-ping failed", extra={"error": str(e)})
            await asyncio.sleep(CFG.SELF_PING_INTERVAL)

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
            await self.initialize()
            await self.start_web_server()
            await telegram.start_webhook()
            
            asyncio.create_task(self.health_check())
            asyncio.create_task(self.self_ping())
            
            while self.running:
                try:
                    await self.run_scan_cycle()
                except Exception as e:
                    logger.error(f"❌ خطأ في دورة المسح: {e}")
                await asyncio.sleep(CFG.SCAN_INTERVAL_SECONDS)
                
                if datetime.now(timezone.utc).minute == 0:
                    try:
                        self.symbols = await fetcher.fetch_top_symbols(CFG.TOP_N_COINS)
                        if not self.symbols:
                            self.symbols = CFG.CORE_UNIVERSE[:50]
                    except Exception as e:
                        logger.error(f"خطأ في تحديث القائمة: {e}")
                        
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
