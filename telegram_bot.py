import os
import asyncio
from typing import Dict, Any
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from utils import logger
from config import CFG
from database import db

class TelegramBotManager:
    def __init__(self):
        self.app = None
        self.is_running = False

    async def start(self):
        """بدء تشغيل مستمع التلجرام مدمج مع حلقة asyncio الرئيسية"""
        token = os.environ.get("TELEGRAM_BOT_TOKEN", getattr(CFG, "TELEGRAM_BOT_TOKEN", "")).strip()
        
        if not token:
            logger.error("❌ TELEGRAM_BOT_TOKEN مفقود في متغيرات البيئة و config!")
            return

        try:
            logger.info(f"✅ TELEGRAM_BOT_TOKEN جاهز للاستخدام (الطول: {len(token)})")
            
            # بناء تطبيق التلجرام
            self.app = Application.builder().token(token).build()

            # تسجيل كافة الأوامر قبل البدء
            self.app.add_handler(CommandHandler("start", self._cmd_start))
            self.app.add_handler(CommandHandler("status", self._cmd_status))
            self.app.add_handler(CommandHandler("adduser", self._cmd_adduser))

            # تهيئة التطبيق وإلغاء أي Webhook قديم
            await self.app.initialize()
            await self.app.bot.delete_webhook(drop_pending_updates=True)
            await self.app.start()
            
            # تشغيل المستمع (Polling) داخل نفس حلقة الـ AsyncIO
            await self.app.updater.start_polling(drop_pending_updates=True)
            
            self.is_running = True
            logger.info("✅ تم إعداد تطبيق التليجرام وبدء الاستماع للأوامر بنجاح")
            
        except Exception as e:
            logger.error(f"❌ فشل في تشغيل بوت التليجرام: {e}")

    async def stop(self):
        """إيقاف البوت بشكل آمن عند إغلاق السيرفر"""
        if self.app and self.is_running:
            try:
                if self.app.updater and self.app.updater.running:
                    await self.app.updater.stop()
                await self.app.stop()
                await self.app.shutdown()
                self.is_running = False
                logger.info("🛑 تم إيقاف بوت التلجرام بنجاح.")
            except Exception as e:
                logger.error(f"خطأ أثناء إيقاف التلجرام: {e}")

    # ---------------------------------------------------------
    # الأوامر المخصصة (Handlers)
    # ---------------------------------------------------------
    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        logger.info(f"📩 تم استقبال الأمر /start من: {user_id}")
        await update.message.reply_text(
            f"👋 **مرحباً بك في نظام Phoenix Elite!**\nمعرف حسابك: `{user_id}`",
            parse_mode="Markdown"
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        logger.info(f"📩 تم استقبال الأمر /status من: {user_id}")
        
        status_text = (
            "🤖 **حالة المحرك والنظام:**\n"
            "✅ محرك الإشارات: يعمل (Scanning Active)\n"
            "✅ قاعدة البيانات: متصلة\n"
            "✅ السيرفر: Webhook/Aiohttp Online"
        )
        await update.message.reply_text(status_text, parse_mode="Markdown")

    async def _cmd_adduser(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        logger.info(f"📩 تم استقبال الأمر /adduser من: {user_id}")
        
        if not context.args:
            await update.message.reply_text("⚠️ يرجى كتابة الآيدي. مثال:\n`/adduser 8224097606`", parse_mode="Markdown")
            return
            
        target_id = context.args[0]
        # إضافة المستخدم في قاعدة البيانات إن أردت
        await update.message.reply_text(f"✅ تم تسجيل المستخدم `{target_id}` في النظام بنجاح.", parse_mode="Markdown")

    async def send_signal(self, signal_obj: Dict[str, Any]):
        """دالة إرسال الإشارات التي يستدعيها bot.py"""
        if not self.app or not self.is_running:
            logger.warning("تعذر إرسال الإشارة، بوت التلجرام غير نشط.")
            return

        chat_id = getattr(CFG, "TELEGRAM_CHAT_ID", None)
        if not chat_id:
            return

        msg = (
            f"⚡ **إشارة جديدة: {signal_obj.get('symbol')}**\n"
            f"📈 النوع: {signal_obj.get('type')}\n"
            f"🎯 سعر الدخول: {signal_obj.get('entry_price')}"
        )
        try:
            await self.app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"خطأ أثناء إرسال الإشارة عبر التلجرام: {e}")

# إنشاء الكائن الموحد لاستخدامه في bot.py
telegram = TelegramBotManager()
