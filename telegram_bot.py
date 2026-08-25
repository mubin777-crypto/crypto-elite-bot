# telegram_bot.py - مع إضافة سجلات Debug ومعالجة الأخطاء
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
        logger.info(f"📩 أمر /start من {user_id}")
        try:
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
            if config.ADMIN_CHAT_ID:
                await context.bot.send_message(
                    chat_id=config.ADMIN_CHAT_ID,
                    text=f"📩 طلب اشتراك جديد: `{user_id}`\n/approve {user_id}",
                    parse_mode="Markdown"
                )
            logger.info(f"✅ تم الرد على /start للمستخدم {user_id}")
        except Exception as e:
            logger.error(f"❌ خطأ في /start: {e}")
            await update.message.reply_text("⚠️ حدث خطأ أثناء معالجة طلبك.")

    async def approve(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        logger.info(f"📩 أمر /approve من {user_id}")
        try:
            if user_id != config.ADMIN_CHAT_ID:
                await update.message.reply_text("⛔ فقط للمالك.")
                return
            if not context.args:
                await update.message.reply_text("⚠️ استخدم: /approve USER_ID")
                return
            target_user = context.args[0].strip()
            pending = await self.db.get_pending()
            if target_user in pending:
                await self.db.remove_pending(target_user)
                await self.db.add_subscriber(target_user)
                await update.message.reply_text(f"✅ تمت الموافقة على `{target_user}`.")
                try:
                    await context.bot.send_message(chat_id=target_user, text="🎉 تمت الموافقة على اشتراكك!")
                except:
                    pass
                logger.info(f"✅ تمت الموافقة على المستخدم {target_user}")
            else:
                await update.message.reply_text("❌ غير موجود في قائمة الانتظار.")
        except Exception as e:
            logger.error(f"❌ خطأ في /approve: {e}")
            await update.message.reply_text("⚠️ حدث خطأ أثناء معالجة طلبك.")

    async def adduser(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        logger.info(f"📩 أمر /adduser من {user_id}")
        try:
            if user_id != config.ADMIN_CHAT_ID:
                await update.message.reply_text("⛔ هذا الأمر متاح فقط للمالك.")
                return
            if not context.args:
                await update.message.reply_text("⚠️ استخدم: /adduser USER_ID")
                return
            target_user = context.args[0].strip()
            if not target_user.isdigit():
                await update.message.reply_text("❌ المعرف يجب أن يكون أرقاماً فقط.")
                return
            subscribers = await self.db.get_subscribers()
            if target_user in subscribers:
                await update.message.reply_text(f"ℹ️ المستخدم `{target_user}` مشترك بالفعل.", parse_mode="Markdown")
                return
            await self.db.add_subscriber(target_user)
            try:
                await context.bot.send_message(chat_id=target_user, text="🎉 *تمت إضافتك إلى البوت المحسن!*", parse_mode="Markdown")
                await update.message.reply_text(f"✅ تمت إضافة المستخدم `{target_user}` بنجاح.")
            except Exception as e:
                await update.message.reply_text(f"✅ تمت إضافة المستخدم `{target_user}` ولكن لم نتمكن من إرسال رسالة ترحيب.")
            logger.info(f"✅ تمت إضافة المستخدم {target_user}")
        except Exception as e:
            logger.error(f"❌ خطأ في /adduser: {e}")
            await update.message.reply_text("⚠️ حدث خطأ أثناء معالجة طلبك.")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        logger.info(f"📩 أمر /status من {user_id}")
        try:
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
            logger.info(f"✅ تم الرد على /status للمستخدم {user_id}")
        except Exception as e:
            logger.error(f"❌ خطأ في /status: {e}")
            await update.message.reply_text("⚠️ حدث خطأ أثناء جلب حالة البوت.")

    async def performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        logger.info(f"📩 أمر /performance من {user_id}")
        try:
            perf = await self.db.get_performance()
            if not perf:
                await update.message.reply_text("📊 لا توجد بيانات أداء كافية حتى الآن.")
                return
            # perf: (id, date, total_trades, wins, losses, win_rate, profit_factor, avg_win, avg_loss, expectancy, max_drawdown, sharpe_ratio, consecutive_losses, total_return)
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
            logger.info(f"✅ تم الرد على /performance للمستخدم {user_id}")
        except Exception as e:
            logger.error(f"❌ خطأ في /performance: {e}")
            await update.message.reply_text("⚠️ حدث خطأ أثناء جلب بيانات الأداء.")

    async def signal_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        logger.info(f"📩 أمر /signal من {user_id}")
        if not context.args:
            await update.message.reply_text("⚠️ /signal SYMBOL")
            return
        sym = context.args[0].upper()
        try:
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
                logger.info(f"✅ تم الرد على /signal {sym} للمستخدم {user_id}")
        except Exception as e:
            logger.error(f"❌ خطأ في /signal {sym}: {e}")
            await update.message.reply_text(f"⚠️ حدث خطأ أثناء تحليل {sym}.")

handlers = TelegramHandlers()
