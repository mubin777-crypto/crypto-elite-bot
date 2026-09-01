"""
telegram_bot.py - معالجة أوامر Telegram وإرسال الإشارات.
"""
import os
from typing import Dict, Optional
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Application
from config import CFG
from database import db
from backtest import backtester
from utils import logger

class TelegramManager:
    def __init__(self):
        self.app: Optional[Application] = None
        self.bot: Optional[Bot] = None
        self._token = None

    def _ensure_initialized(self):
        if self.bot is not None:
            return
        
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or CFG.TELEGRAM_BOT_TOKEN.strip()
        if token:
            self._token = token
            logger.info(f"✅ TELEGRAM_BOT_TOKEN جاهز للاستخدام (الطول: {len(token)})")
        else:
            logger.error("❌ TELEGRAM_BOT_TOKEN مفقود في متغيرات البيئة و config!")
            return
        
        try:
            self.app = ApplicationBuilder().token(self._token).build()
            self.bot = self.app.bot
            self._setup_handlers()
            logger.info("✅ تم إعداد تطبيق التليجرام بنجاح")
        except Exception as e:
            logger.error(f"❌ خطأ أثناء تهيئة Telegram: {e}")
            self.bot = None
            self.app = None

    def _setup_handlers(self):
        if not self.app: return
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("prewatch", self.cmd_prewatch))
        self.app.add_handler(CommandHandler("performance", self.cmd_performance))
        self.app.add_handler(CommandHandler("reset_daily", self.cmd_reset_daily))
        self.app.add_handler(CommandHandler("adduser", self.cmd_adduser))
        self.app.add_handler(CommandHandler("removeuser", self.cmd_removeuser))

    def _is_admin(self, user_id: int) -> bool:
        return user_id == CFG.TELEGRAM_ADMIN_ID

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        welcome = (
            "🤖 *بوت إشارات العملات الرقمية*\n\n"
            "الأوامر المتاحة:\n"
            "/status — حالة النظام\n"
            "/prewatch — قائمة المراقبة\n"
            "/performance — تقرير الأداء\n"
            "/reset_daily — إعادة ضبط الإحصائيات"
        )
        await update.message.reply_text(welcome, parse_mode="Markdown")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        daily = db.get_daily_stats()
        pre_watch = db.get_active_prewatch(10)
        status_msg = (
            "📡 *حالة النظام*\n"
            f"• الإشارات اليوم: {daily['total_signals']}\n"
            f"• الأرباح: {daily['wins']} | الخسائر: {daily['losses']}\n"
            f"• صافي PnL: ${daily['pnl']:.2f}\n"
            f"• رأس المال: ${daily['capital']:.2f}\n"
            f"• عملات تحت المراقبة: {len(pre_watch)}"
        )
        await update.message.reply_text(status_msg, parse_mode="Markdown")

    async def cmd_prewatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pre_watch = db.get_active_prewatch(20)
        if not pre_watch:
            await update.message.reply_text("🔭 لا توجد عملات تحت المراقبة حالياً.")
            return
        msg = "🔭 *قائمة المراقبة*\n\n" + "\n".join([f"{i}. `{s}`" for i, s in enumerate(pre_watch[:10], 1)])
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def cmd_performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        signals = db.get_signals_for_backtest(days=7)
        if not signals:
            await update.message.reply_text("📊 لا توجد إشارات كافية في الأيام السبعة الماضية.")
            return
        metrics = await backtester.run_on_history(signals, days_future=1)
        report = backtester.generate_weekly_report(metrics)
        await update.message.reply_text(report, parse_mode="Markdown")

    async def cmd_reset_daily(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ صلاحية المشرف مطلوبة.")
            return
        db.reset_daily_stats()
        await update.message.reply_text("✅ تم إعادة ضبط الإحصائيات اليومية.")

    async def cmd_adduser(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ صلاحية المشرف مطلوبة.")
            return
        if not context.args:
            await update.message.reply_text("⚠️ الاستخدام: /adduser USER_ID")
            return
        for user_id in context.args: db.add_subscriber(user_id)
        await update.message.reply_text(f"✅ تمت إضافة المستخدمين: {', '.join(context.args)}")

    async def cmd_removeuser(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ صلاحية المشرف مطلوبة.")
            return
        if not context.args:
            await update.message.reply_text("⚠️ الاستخدام: /removeuser USER_ID")
            return
        for user_id in context.args: db.remove_subscriber(user_id)
        await update.message.reply_text(f"✅ تمت إزالة المستخدمين: {', '.join(context.args)}")

    async def send_signal(self, signal: Dict, chat_id: str = None):
        self._ensure_initialized()
        if not self.bot:
            logger.info(f"📩 [محاكاة إشارة]: {signal['symbol']} | {signal['type']} @ {signal['entry_price']}")
            return
        
        target_chat = chat_id or CFG.TELEGRAM_CHANNEL_ID or str(CFG.TELEGRAM_ADMIN_ID)
        if not target_chat or target_chat == "0":
            logger.warning("⚠️ لا يوجد معرف قناة أو مشرف إرسال محدد.")
            return

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
            await self.bot.send_message(chat_id=target_chat, text=message, parse_mode="Markdown")
            logger.info("✅ Telegram signal dispatched", extra={"symbol": signal["symbol"]})
        except Exception as e:
            logger.error(f"❌ Failed Telegram dispatch: {e}")

    async def send_alert(self, message: str, to_admin: bool = True):
        self._ensure_initialized()
        if not self.bot: return
        target_chat = str(CFG.TELEGRAM_ADMIN_ID) if to_admin else CFG.TELEGRAM_CHANNEL_ID
        try:
            await self.bot.send_message(chat_id=target_chat, text=message, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"❌ Failed alert send: {e}")

    async def start(self):
        self._ensure_initialized()
        if not self.app: return
        await self.app.initialize()
        await self.app.start()
        logger.info("✅ Telegram listener online")

    async def stop(self):
        if self.app:
            await self.app.stop()

telegram = TelegramManager()
