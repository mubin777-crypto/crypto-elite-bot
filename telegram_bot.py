"""
telegram_bot.py - معالجة استقبال الأوامر وإرسال التوصيات بالتوازي
"""
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import CFG
from database import db

logger = logging.getLogger("telegram_bot")

class TelegramManager:
    def __init__(self):
        self.app = None

    async def start(self):
        """تهيئة وتكليف البوت بالاستماع للأوامر في الخلفية"""
        if not CFG.TELEGRAM_BOT_TOKEN:
            logger.warning("⚠️ لم يتم توفير TELEGRAM_BOT_TOKEN")
            return

        # 1. بناء التطبيق
        self.app = Application.builder().token(CFG.TELEGRAM_BOT_TOKEN).build()

        # 2. تسجيل المعالجات (Command Handlers)
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("help", self._cmd_help))

        # 3. بدء التشغيل التزامني
        await self.app.initialize()
        await self.app.start()
        
        # 4. تفعيل Polling الاستقبال في الخلفية (Non-blocking)
        if self.app.updater:
            await self.app.updater.start_polling(drop_pending_updates=True)
            logger.info("📡 Telegram Polling started successfully (جاهز لاستقبال الأوامر)")

    async def stop(self):
        """إيقاف البوت بنظافة عند إغلاق الخادم"""
        if self.app:
            if self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            logger.info("🛑 Telegram Bot stopped")

    # --- الأوامر المتاحة ---

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "👋 أهلاً بك في بوت Crypto Elite!\n"
            "البوت يعمل حالياً على فحص الأسواق وإرسال التوصيات تلقائياً.\n\n"
            "الأوامر المتاحة:\n"
            "/status - فحص حالة البوت والعملات"
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        stats = db.get_daily_stats() if hasattr(db, 'get_daily_stats') else {}
        msg = (
            "📊 **حالة النظام الحالية:**\n\n"
            "✅ البوت: متصل ويعمل\n"
            "🌐 سيرفر الويب: Port 10000 (Active)\n"
            "🔍 مسح العملات: شغال تلقائياً"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("لأي استفسار، استخدم الأمر /status لتفقد حالة النظام.")

    async def send_signal(self, signal_data: dict):
        """دالة إرسال التوصيات إلى القناة/المجموعة"""
        if not self.app or not CFG.TELEGRAM_CHAT_ID:
            return

        msg = (
            f"🚨 **إشارة جديدة: {signal_data.get('symbol')}**\n"
            f"النوع: {signal_data.get('type')}\n"
            f"سعر الدخول: {signal_data.get('entry_price')}\n"
        )
        try:
            await self.app.bot.send_message(
                chat_id=CFG.TELEGRAM_CHAT_ID,
                text=msg,
                parse_mode="Markdown"
            )
            logger.info(f"✅ Signal sent via Telegram for {signal_data.get('symbol')}")
        except Exception as e:
            logger.error(f"❌ فشل إرسال التوصية عبر تليجرام: {e}")

# كائن عالمي موحد
telegram = TelegramManager()
