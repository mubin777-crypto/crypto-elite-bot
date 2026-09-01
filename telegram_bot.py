"""
telegram_bot.py - إدارة استقبال الأوامر وإرسال الإشارات
"""
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import CFG

logger = logging.getLogger("telegram_bot")

class TelegramManager:
    def __init__(self):
        self.app = None

    async def start(self):
        """تهيئة البوت وتشغيل Polling غير معطل للمهام الأخرى"""
        if not CFG.TELEGRAM_BOT_TOKEN:
            logger.warning("⚠️ لم يتم توفير TELEGRAM_BOT_TOKEN")
            return

        # بناء التطبيق
        self.app = Application.builder().token(CFG.TELEGRAM_BOT_TOKEN).build()

        # إضافة معالجات الأوامر
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("status", self._cmd_status))

        # بدء التشغيل وفتح الاستماع للأوامر
        await self.app.initialize()
        await self.app.start()
        
        if self.app.updater:
            await self.app.updater.start_polling(drop_pending_updates=True)
            logger.info("📡 Telegram Polling started successfully (جاهز لاستقبال الأوامر)")

    async def stop(self):
        """إيقاف البوت بنظافة"""
        if self.app:
            if self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            logger.info("🛑 Telegram Bot stopped successfully")

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "👋 أهلاً بك في بوت Crypto Elite!\n\n"
            "البوت يعمل حالياً على فحص الأسواق وإرسال التوصيات تلقائياً.\n"
            "استخدم /status لمعرفة حالة البوت."
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = (
            "📊 **حالة النظام:**\n\n"
            "✅ البوت: متصل ويعمل\n"
            "🌐 Web Server: Port 10000 (Active)\n"
            "🔍 مسح العملات: شغال في الخلفية"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def send_signal(self, signal_data: dict):
        """إرسال الإشعارات للقناة أو المجموعة"""
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

telegram = TelegramManager()
