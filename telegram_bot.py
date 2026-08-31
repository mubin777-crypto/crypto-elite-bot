"""
telegram_bot.py - معالجة أوامر Telegram وإرسال الإشارات.
"""
import asyncio
from typing import Dict
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)
from config import CFG
from database import db
from backtest import backtester
from utils import logger

class TelegramManager:
    def __init__(self):
        self.app = ApplicationBuilder().token(CFG.TELEGRAM_BOT_TOKEN).build()
        self.bot: Bot = self.app.bot
        self._setup_handlers()

    def _setup_handlers(self):
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("prewatch", self.cmd_prewatch))
        self.app.add_handler(CommandHandler("performance", self.cmd_performance))
        self.app.add_handler(CommandHandler("signal", self.cmd_signal))
        self.app.add_handler(CommandHandler("reset_daily", self.cmd_reset_daily))

    def _is_admin(self, user_id: int) -> bool:
        return user_id == CFG.TELEGRAM_ADMIN_ID

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
        report = backtester.generate_weekly_report(backtester._calculate_metrics())
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

    async def send_signal(self, signal: Dict, chat_id: str = None):
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
            logger.info("Signal sent via Telegram", extra={"symbol": signal["symbol"]})
        except Exception as e:
            logger.error("Failed to send Telegram signal", extra={"error": str(e)})

    async def send_alert(self, message: str, to_admin: bool = True):
        chat_id = str(CFG.TELEGRAM_ADMIN_ID) if to_admin else CFG.TELEGRAM_CHANNEL_ID
        try:
            await self.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
        except Exception as e:
            logger.error("Failed to send alert", extra={"error": str(e)})

    async def start(self):
        await self.app.initialize()
        await self.app.start()
        logger.info("Telegram bot started")

    async def stop(self):
        await self.app.stop()

telegram = TelegramManager()
