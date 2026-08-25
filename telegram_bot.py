# telegram_bot.py
import logging
from telegram import Update
from telegram.ext import ContextTypes

from database import db
from config import config

logger = logging.getLogger(__name__)

class TelegramHandlers:
    def __init__(self):
        self.db = db

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        subscribers = await self.db.get_subscribers()
        pending = await self.db.get_pending()
        if user_id in subscribers:
            await update.message.reply_text("ℹ️ أنت مشترك بالفعل.")
            return
        if user_id in pending:
            await update.message.reply_text("⏳ طلبك قيد الانتظار.")
            return
        await self.db.add_pending(user_id)
        await update.message.reply_text("✅ تم استلام طلب الاشتراك.")
        await context.bot.send_message(
            chat_id=config.ADMIN_CHAT_ID,
            text=f"📩 طلب اشتراك جديد: `{user_id}`\n/approve {user_id}",
            parse_mode="Markdown"
        )

    async def approve(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_user.id) != config.ADMIN_CHAT_ID:
            await update.message.reply_text("⛔ فقط للمالك.")
            return
        if not context.args:
            await update.message.reply_text("⚠️ استخدم: /approve USER_ID")
            return
        user_id = context.args[0].strip()
        pending = await self.db.get_pending()
        if user_id in pending:
            await self.db.remove_pending(user_id)
            await self.db.add_subscriber(user_id)
            await update.message.reply_text(f"✅ تمت الموافقة على `{user_id}`.")
            try:
                await context.bot.send_message(chat_id=user_id, text="🎉 تمت الموافقة على اشتراكك!")
            except:
                pass
        else:
            await update.message.reply_text("❌ غير موجود في قائمة الانتظار.")

    async def adduser(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_user.id) != config.ADMIN_CHAT_ID:
            await update.message.reply_text("⛔ هذا الأمر متاح فقط للمالك.")
            return
        if not context.args:
            await update.message.reply_text("⚠️ استخدم: /adduser USER_ID")
            return
        user_id = context.args[0].strip()
        if not user_id.isdigit():
            await update.message.reply_text("❌ المعرف يجب أن يكون أرقاماً فقط.")
            return
        subscribers = await self.db.get_subscribers()
        if user_id in subscribers:
            await update.message.reply_text(f"ℹ️ المستخدم `{user_id}` مشترك بالفعل.", parse_mode="Markdown")
            return
        await self.db.add_subscriber(user_id)
        try:
            await context.bot.send_message(chat_id=user_id, text="🎉 *تمت إضافتك إلى البوت المحسن!*", parse_mode="Markdown")
            await update.message.reply_text(f"✅ تمت إضافة المستخدم `{user_id}` بنجاح.")
        except Exception as e:
            await update.message.reply_text(f"✅ تمت إضافة المستخدم `{user_id}` ولكن لم نتمكن من إرسال رسالة ترحيب.")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        subscribers = await self.db.get_subscribers()
        pending = await self.db.get_pending()
        await update.message.reply_text(
            f"📊 *حالة البوت المحسن*\n"
            f"👥 المشتركين: {len(subscribers)}\n"
            f"⏳ في الانتظار: {len(pending)}\n"
            f"💧 الحد الأدنى للسيولة: ${config.MIN_VOLUME_USD:,}\n"
            f"📊 عتبة الإشارة: {config.SIGNAL_SCORE_THRESHOLD}/10\n"
            f"⏱️ فترة التبريد: {config.COOLDOWN_MINUTES} دقيقة\n"
            f"📡 البيانات: Binance.US → Coinbase → CoinCap\n"
            f"🧪 المحاكاة: {'مفعلة' if config.PAPER_TRADING else 'معطلة'}",
            parse_mode="Markdown"
        )

    async def performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        perf = await self.db.get_performance()
        if not perf:
            await update.message.reply_text("📊 لا توجد بيانات أداء كافية حتى الآن.")
            return
        msg = (
            f"📈 *أداء البوت*\n\n"
            f"📊 إجمالي الصفقات: {perf[2]}\n"
            f"✅ الصفقات الرابحة: {perf[3]}\n"
            f"❌ الصفقات الخاسرة: {perf[4]}\n"
            f"📈 نسبة الربح: {perf[5]*100:.1f}%\n"
            f"💰 متوسط الربح: {perf[7]:.2f}%\n"
            f"📉 متوسط الخسارة: {perf[8]:.2f}%\n"
            f"📊 معامل الربح: {perf[6]:.2f}\n"
            f"📈 العائد المتوقع: {perf[9]:.2f}%\n"
            f"📈 العائد الكلي: {perf[13]:.2f}%\n"
            f"📉 أقصى انخفاض: {perf[10]*100:.1f}%\n"
            f"📊 نسبة شارب: {perf[11]:.2f}\n"
            f"📉 خسائر متتالية: {perf[12]}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def signal_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("⚠️ /signal SYMBOL")
            return
        sym = context.args[0].upper()
        from utils import fetch_klines, fetch_24hr_stats
        from signals import SignalEngine
        import aiohttp
        async with aiohttp.ClientSession() as session:
            data_5m = await fetch_klines(session, sym, '5m', 100)
            data_1h = await fetch_klines(session, sym, '1h', 30)
            data_4h = await fetch_klines(session, sym, '4h', 20)
            stats = await fetch_24hr_stats(session, sym)
            if not data_5m or not data_1h or not data_4h:
                await update.message.reply_text(f"❌ لا توجد بيانات كافية لـ {sym}")
                return
            engine = SignalEngine(sym, data_5m, data_1h, data_4h, stats)
            result = engine.evaluate()
            stop_loss, take_profit, pos_size = engine.calculate_risk(result['price'])
            msg = (
                f"📡 *تحليل فوري لـ {sym}*\n"
                f"🔹 النقاط: {result['score']}/10\n"
                f"🔹 الإشارة: {result['signal']}\n"
                f"💰 السعر: `{result['price']:.4f}`\n"
                f"📊 RSI(6): `{result['rsi']}`\n"
                f"📊 ADX: `{result['adx']}`\n"
                f"📈 تغير ساعة: `{result['change_1h']}%`\n"
                f"📊 الحجم النسبي: `{result['volume_ratio']}x`\n"
                f"📝 الأسباب: {', '.join(result['reasons'])}\n"
                f"🛡️ وقف الخسارة: `{stop_loss:.4f}`\n"
                f"🎯 جني الأرباح: `{take_profit:.4f}`\n"
                f"📊 حجم الصفقة: `{pos_size*100:.2f}%`"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")

handlers = TelegramHandlers()
