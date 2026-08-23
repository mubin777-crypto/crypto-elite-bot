# bot.py - بوت التليجرام مع تشغيل آمن (يعمل على Render)

import sys
import time
import asyncio
import threading
import signal
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from core import config, logger, Database, Repository, DataProvider, Scanner, Tracker

# ===================================================================
# أوامر التليجرام
# ===================================================================

class CommandHandlers:
    def __init__(self, repo):
        self.repo = repo

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        subscribers = await self.repo.get_subscribers()
        pending = await self.repo.get_pending()
        if user_id in subscribers:
            await update.message.reply_text("ℹ️ أنت مشترك بالفعل.")
            return
        if user_id in pending:
            await update.message.reply_text("⏳ طلبك قيد الانتظار.")
            return
        await self.repo.add_pending(user_id)
        await update.message.reply_text("✅ تم استلام طلب الاشتراك.")
        if config.ADMIN_CHAT_ID:
            await context.bot.send_message(
                chat_id=config.ADMIN_CHAT_ID,
                text=f"📩 طلب اشتراك جديد: <code>{user_id}</code>\n/approve {user_id}",
                parse_mode="HTML"
            )

    async def approve(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_user.id) != config.ADMIN_CHAT_ID:
            await update.message.reply_text("⛔ فقط للمالك.")
            return
        if not context.args:
            await update.message.reply_text("⚠️ استخدم: /approve USER_ID")
            return
        user_id = context.args[0].strip()
        pending = await self.repo.get_pending()
        if user_id in pending:
            await self.repo.remove_pending(user_id)
            await self.repo.add_subscriber(user_id)
            await update.message.reply_text(f"✅ تمت الموافقة على <code>{user_id}</code>.", parse_mode="HTML")
            try:
                await context.bot.send_message(chat_id=user_id, text="🎉 تمت الموافقة على اشتراكك!")
            except:
                pass
        else:
            await update.message.reply_text("❌ غير موجود في قائمة الانتظار.")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        subscribers = await self.repo.get_subscribers()
        pending = await self.repo.get_pending()
        await update.message.reply_text(
            f"📊 <b>حالة البوت</b>\n"
            f"👥 المشتركين: {len(subscribers)}\n"
            f"⏳ في الانتظار: {len(pending)}\n"
            f"⏱️ فترة التبريد: {config.COOLDOWN_MINUTES} دقيقة\n"
            f"🔄 قاعدة البيانات: SQLite",
            parse_mode="HTML"
        )

    async def performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        gross_profit = await self.repo.db.fetchrow("SELECT SUM(profit_loss) FROM signals_history WHERE status = 'WIN'")
        gross_loss = await self.repo.db.fetchrow("SELECT SUM(profit_loss) FROM signals_history WHERE status = 'LOSS'")
        gross_profit = gross_profit[0] if gross_profit else 0.0
        gross_loss = abs(gross_loss[0]) if gross_loss else 0.0

        stats = await self.repo.db.fetchrow("SELECT COUNT(*), SUM(win), AVG(profit_loss) FROM signals_history WHERE status IN ('WIN', 'LOSS')")
        if not stats or stats[0] == 0:
            await update.message.reply_text("📊 لا توجد بيانات أداء كافية حتى الآن.")
            return
        total, wins, avg_profit = stats
        wins = wins or 0
        losses = total - wins
        win_rate = wins / total if total > 0 else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
        avg_win = 0.0
        avg_loss = 0.0
        if wins > 0:
            avg_win_row = await self.repo.db.fetchrow("SELECT AVG(profit_loss) FROM signals_history WHERE status = 'WIN'")
            avg_win = avg_win_row[0] if avg_win_row else 0.0
        if losses > 0:
            avg_loss_row = await self.repo.db.fetchrow("SELECT AVG(profit_loss) FROM signals_history WHERE status = 'LOSS'")
            avg_loss = avg_loss_row[0] if avg_loss_row else 0.0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * abs(avg_loss)) if total > 0 else 0.0
        await update.message.reply_text(
            f"📈 <b>أداء البوت</b>\n\n"
            f"📊 إجمالي الصفقات: {total}\n"
            f"✅ الصفقات الرابحة: {wins}\n"
            f"❌ الصفقات الخاسرة: {losses}\n"
            f"📈 نسبة الربح: {win_rate*100:.1f}%\n"
            f"💰 متوسط الربح: {avg_win:.2f}%\n"
            f"📉 متوسط الخسارة: {avg_loss:.2f}%\n"
            f"📊 معامل الربح: {profit_factor:.2f}\n"
            f"📈 العائد المتوقع: {expectancy:.2f}%",
            parse_mode="HTML"
        )

# ===================================================================
# بوت التليجرام
# ===================================================================

class SignalBot:
    def __init__(self, repo):
        self.repo = repo
        self.handlers = CommandHandlers(repo)
        self.application = None

    def build(self):
        self.application = Application.builder().token(config.TELEGRAM_TOKEN).build()
        self.application.add_handler(CommandHandler("start", self.handlers.start))
        self.application.add_handler(CommandHandler("approve", self.handlers.approve))
        self.application.add_handler(CommandHandler("status", self.handlers.status))
        self.application.add_handler(CommandHandler("performance", self.handlers.performance))
        logger.info("✅ Telegram bot built")
        return self.application

    async def start_polling(self):
        if not self.application:
            self.build()
        await self.application.bot.delete_webhook()
        logger.info("✅ Webhook deleted, starting polling...")
        await self.application.run_polling(allowed_updates=["message", "callback_query"])

# ===================================================================
# التشغيل الآمن - باستخدام خيوط منفصلة
# ===================================================================

def run_flask():
    """تشغيل Flask في خيط منفصل"""
    from flask import Flask
    flask_app = Flask(__name__)
    @flask_app.route('/')
    @flask_app.route('/healthcheck')
    def healthcheck():
        return "✅ Elite Signal Bot v14 - Running"
    flask_app.run(host='0.0.0.0', port=config.PORT, debug=False)

def run_scanner(provider, repo):
    """تشغيل الماسح في خيط منفصل"""
    scanner = Scanner(provider, repo)
    asyncio.run(scanner.start())

def run_tracker(provider, repo):
    """تشغيل المتتبع في خيط منفصل"""
    tracker = Tracker(provider, repo)
    asyncio.run(tracker.start())

def main():
    # 1. تهيئة قاعدة البيانات والخدمات
    db = Database()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    if not loop.run_until_complete(db.connect()):
        logger.error("❌ Failed to connect to database")
        return
    loop.close()

    repo = Repository(db)
    provider = DataProvider()

    # 2. تشغيل Flask في خيط منفصل
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask server started")

    # 3. تشغيل الماسح والمتتبع في خيوط منفصلة
    scanner_thread = threading.Thread(target=run_scanner, args=(provider, repo), daemon=True)
    tracker_thread = threading.Thread(target=run_tracker, args=(provider, repo), daemon=True)
    scanner_thread.start()
    tracker_thread.start()
    logger.info("✅ Scanner and Tracker started")

    # 4. تشغيل بوت التليجرام في الخيط الرئيسي
    bot = SignalBot(repo)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(bot.start_polling())
    except KeyboardInterrupt:
        logger.info("🛑 Interrupted by user")
    except Exception as e:
        logger.error(f"❌ Bot error: {e}")
    finally:
        loop.close()

def signal_handler(sig, frame):
    logger.info(f"Received signal {sig}")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    main()
