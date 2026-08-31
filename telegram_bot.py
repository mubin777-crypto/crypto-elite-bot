"""
telegram_bot.py - معالجة أوامر Telegram وإرسال الإشارات.
"""
import asyncio
import logging
import os
import time
from typing import Dict, Optional
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, Application
)
from config import CFG
from database import db
from backtest import backtester
from utils import logger

class TelegramManager:
    def __init__(self):
        self.app: Optional[Application] = None
        self.bot: Optional[Bot] = None
        self._token = None
        self._initialized = False

    def _ensure_initialized(self):
        """تأكد من إنشاء التطبيق فقط عند الحاجة مع إعادة محاولة قراءة التوكن."""
        if self._initialized:
            return
        
        # 🔥 محاولة قراءة التوكن مرة أخرى من البيئة (لضمان التحميل)
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if token:
            self._token = token
        else:
            self._token = CFG.TELEGRAM_BOT_TOKEN
        
        if not self._token or self._token == "":
            # إذا لم يوجد التوكن، نعطي تحذيراً ولكن لا نوقف البوت (سيتم إرسال الإشارات إلى السجلات فقط)
            logger.error("❌ TELEGRAM_BOT_TOKEN غير معرّف. سيتم تسجيل الإشارات في السجلات فقط دون إرسالها.")
            self._initialized = True  # نضعها True لتجنب تكرار التحذير
            return
        
        try:
            self.app = ApplicationBuilder().token(self._token).build()
            self.bot = self.app.bot
            self._setup_handlers()
            self._initialized = True
            logger.info("✅ Telegram Application initialized successfully")
        except Exception as e:
            logger.error(f"❌ فشل تهيئة تطبيق Telegram: {e}")
            self._initialized = True  # لمنع التكرار

    def _setup_handlers(self):
        if not self.app:
            return
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("prewatch", self.cmd_prewatch))
        self.app.add_handler(CommandHandler("performance", self.cmd_performance))
        self.app.add_handler(CommandHandler("signal", self.cmd_signal))
        self.app.add_handler(CommandHandler("reset_daily", self.cmd_reset_daily))

    def _is_admin(self, user_id: int) -> bool:
        return user_id == CFG.TELEGRAM_ADMIN_ID

    # ─── الأوامر ───
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        welcome = (
            "🤖 *بوت إشارات العملات الرقمية*\n\n"
            "الأوامر المتاحة:\n"
            "/status — حالة النظام\n"
            "/prewatch — قائمة المراقبة\n"
            "/performance — تقرير الأداء\n"
            "/signal SYMBOL — آخر إشارة لعملة\n"
            "/reset_daily — إعادة ضبط الإحصائيات (للمشرف فقط)"
        )
        await update.message.reply_text(welcome, parse_mode="Markdown")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        daily = db.get_daily_stats()
        status_msg = (
            "📡 *حالة النظام*\n"
            f"• الإشارات اليوم: {daily['total_signals']}\n"
            f"• الأرباح: {daily['wins']} | الخسائر: {daily['losses']}\n"
            f"• صافي PnL: ${daily['pnl']:.2f}\n"
            f"• رأس المال: ${daily['capital']:.2f}"
        )
        await update.message.reply_text(status_msg, parse_mode="Markdown")

    async def cmd_prewatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "👁️ *قائمة المراقبة:*\nيتم تحديثها تلقائياً بناءً على انضغاط البولينجر.",
            parse_mode="Markdown"
        )

    async def cmd_performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        signals = db.get_signals_for_backtest(days=7)
        if not signals:
            await update.message.reply_text("📊 لا توجد إشارات كافية في الأيام السبعة الماضية لإجراء التقرير.")
            return
        metrics = await backtester.run_on_history(signals, days_future=1)
        report = backtester.generate_weekly_report(metrics)
        await update.message.reply_text(report, parse_mode="Markdown")

    async def cmd_signal(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("⚠️ الاستخدام: /signal BTCUSDT")
            return
        symbol = context.args[0].upper()
        await update.message.reply_text(f"🔍 البحث عن آخر إشارة لـ {symbol}...")

    async def cmd_reset_daily(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ صلاحية المشرف مطلوبة.")
            return
        db.reset_daily_stats()
        await update.message.reply_text("✅ تم إعادة ضبط الإحصائيات اليومية.")

    # ─── إرسال الإشارات والتنبيهات ───
    async def send_signal(self, signal: Dict, chat_id: str = None):
        self._ensure_initialized()
        if not self.bot:
            logger.warning("⚠️ البوت غير مهيأ لإرسال الإشارات (لا يوجد توكن)، الإشارة مسجلة في السجلات فقط.")
            logger.info(f"📩 [محاكاة] إشارة: {signal['symbol']} | {signal['type']} | السعر: {signal['entry_price']}")
            return
        
        if chat_id is None:
            chat_id = CFG.TELEGRAM_CHANNEL_ID or str(CFG.TELEGRAM_ADMIN_ID)
        
        emoji = "🟢" if signal["type"] == "BUY" else "🔴"
        signal_display = signal.get("signal", signal["type"])
        
        reasons_text = " | ".join(signal.get("reasons", [])[:3])
        message = (
            f"{emoji} *{signal_display} — {signal['symbol']}*\n\n"
            f"💰 سعر الدخول: `{signal['entry_price']}`\n"
            f"🛑 وقف الخسارة: `{signal['stop_loss']}`\n"
            f"🎯 جني الأرباح: `{signal['take_profit']}`\n"
            f"📊 حجم الصفقة: `{signal['position_size']}`\n"
            f"🎚️ درجة الثقة: `{signal['confidence']}%`\n"
            f"⭐ النقاط: `{signal['score']}/10`\n"
            f"📈 ADX: `{signal['adx']}` | RSI: `{signal['rsi']}`\n"
            f"📦 حجم: `{signal['volume_ratio']}x`\n\n"
            f"📝 الأسباب: _{reasons_text}_\n"
            f"⏱ `{signal['timestamp']}`"
        )
        try:
            await self.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
            logger.info("✅ Signal sent via Telegram", extra={"symbol": signal["symbol"]})
        except Exception as e:
            logger.error("❌ Failed to send Telegram signal", extra={"error": str(e)})

    async def send_alert(self, message: str, to_admin: bool = True):
        self._ensure_initialized()
        if not self.bot:
            logger.warning(f"⚠️ لا يمكن إرسال التنبيه (لا يوجد توكن): {message}")
            return
        chat_id = str(CFG.TELEGRAM_ADMIN_ID) if to_admin else CFG.TELEGRAM_CHANNEL_ID
        try:
            await self.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
        except Exception as e:
            logger.error("❌ Failed to send alert", extra={"error": str(e)})

    async def start(self):
        """بدء تشغيل البوت مع محاولة قراءة التوكن."""
        # تأخير بسيط لضمان تحميل المتغيرات البيئية
        await asyncio.sleep(0.5)
        self._ensure_initialized()
        if not self.app:
            logger.warning("⚠️ لا يمكن بدء تطبيق Telegram بسبب نقص التوكن.")
            return
        await self.app.initialize()
        await self.app.start()
        logger.info("✅ Telegram bot started")

    async def stop(self):
        if self.app:
            await self.app.stop()
        self._initialized = False

# ─── كائن عالمي ───
telegram = TelegramManager()
