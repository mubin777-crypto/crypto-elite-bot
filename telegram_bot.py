# telegram_bot.py - أوامر التليجرام (مع إضافة /add)
import logging
from telegram import Update
from telegram.ext import ContextTypes

from database import db
from config import config

logger = logging.getLogger(__name__)

class TelegramHandlers:
    def __init__(self):
        self.db = db

    # -------------------- الأوامر الأساسية --------------------
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

    # -------------------- الأمر الجديد /add (إضافة مباشرة) --------------------
    async def add_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """إضافة مستخدم مباشرة بدون انتظار (للمالك فقط)"""
        user_id = str(update.effective_user.id)
        logger.info(f"📩 أمر /add من {user_id}")
        try:
            if user_id != config.ADMIN_CHAT_ID:
                await update.message.reply_text("⛔ هذا الأمر متاح فقط للمالك.")
                return
            if not context.args:
                await update.message.reply_text("⚠️ استخدم: /add USER_ID [USER_ID2 ...]")
                return
            added = []
            for arg in context.args:
                target = arg.strip()
                if not target.isdigit():
                    continue
                # إضافة مباشرة دون انتظار
                await self.db.add_subscriber(target)
                added.append(target)
                # إرسال رسالة ترحيب للمستخدم المضاف
                try:
                    await context.bot.send_message(
                        chat_id=target,
                        text="🎉 *تمت إضافتك إلى البوت المحسن!* ستستلم إشارات التداول تلقائياً.",
                        parse_mode="Markdown"
                    )
                except:
                    pass
            if added:
                await update.message.reply_text(f"✅ تمت إضافة المستخدمين: `{', '.join(added)}`", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ لم يتم إضافة أي مستخدم (تأكد من الأرقام).")
            logger.info(f"✅ أضاف المالك المستخدمين: {added}")
        except Exception as e:
            logger.error(f"❌ خطأ في /add: {e}")
            await update.message.reply_text("⚠️ حدث خطأ أثناء إضافة المستخدمين.")

    # -------------------- أوامر إدارة المستخدمين --------------------
    async def adduser(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """إضافة مستخدم (قديم، نحتفظ به للتوافق)"""
        await self.add_user(update, context)

    async def removeuser(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """إزالة مستخدم (للمالك)"""
        user_id = str(update.effective_user.id)
        logger.info(f"📩 أمر /removeuser من {user_id}")
        try:
            if user_id != config.ADMIN_CHAT_ID:
                await update.message.reply_text("⛔ هذا الأمر متاح فقط للمالك.")
                return
            if not context.args:
                await update.message.reply_text("⚠️ استخدم: /removeuser USER_ID")
                return
            target = context.args[0].strip()
            if not target.isdigit():
                await update.message.reply_text("❌ المعرف يجب أن يكون أرقاماً.")
                return
            await self.db.remove_subscriber(target)
            await update.message.reply_text(f"✅ تمت إزالة المستخدم `{target}`.")
            logger.info(f"✅ تمت إزالة المستخدم {target}")
        except Exception as e:
            logger.error(f"❌ خطأ في /removeuser: {e}")
            await update.message.reply_text("⚠️ حدث خطأ أثناء معالجة طلبك.")

    # -------------------- أوامر الحالة والأداء --------------------
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
                result = await engine.evaluate()
                # حساب المخاطر حسب نوع الإشارة
                action = result.get('action', 'NEUTRAL')
                if action == 'NEUTRAL':
                    stop_loss = take_profit = pos_size = 0
                else:
                    stop_loss, take_profit, pos_size = engine.calculate_risk(result['price'], action)
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
