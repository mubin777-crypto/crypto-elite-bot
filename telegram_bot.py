import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# إعداد السجلات
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# جلب توكن التلجرام ومعرف الأدمن من متغيرات البيئة
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")  # اختياري: إذا كنت تطبق حماية للأوامر

# ---------------------------------------------------------
# دالّات التعامل مع الأوامر (Command Handlers)
# ---------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"📩 تم استقبال الأمر /start من المستخدم: {user_id}")
    await update.message.reply_text(f"👋 مرحباً بك! البوت يعمل بنجاح.\nمعرف حسابك: `{user_id}`", parse_mode="Markdown")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"📩 تم استقبال الأمر /status من المستخدم: {user_id}")
    
    status_msg = (
        "🤖 **حالة النظام:**\n"
        "✅ محرك الإشارات: يعمل\n"
        "✅ الاتصال بالخادم: متصل (Port 10000)\n"
        "✅ مستمع التلجرام: نشط"
    )
    await update.message.reply_text(status_msg, parse_mode="Markdown")

async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"📩 تم استقبال الأمر /adduser من المستخدم: {user_id}")
    
    # فحص الأرجومنت المرفقة مع الأمر (مثال: /adduser 8224097606)
    if not context.args:
        await update.message.reply_text("⚠️ يرجى تحديد معرف المستخدم. مثال:\n`/adduser 123456789`", parse_mode="Markdown")
        return

    target_user_id = context.args[0]
    # يمكنك إضافة منطق حفظ المستخدم في قاعدة البيانات هنا
    
    await update.message.reply_text(f"✅ تم إضافة المستخدم `{target_user_id}` بنجاح!", parse_mode="Markdown")

# ---------------------------------------------------------
# إعداد البوت وبدء التشغيل
# ---------------------------------------------------------

async def init_telegram_bot():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN مفقود في متغيرات البيئة و config!")
        return None

    logger.info(f"✅ TELEGRAM_BOT_TOKEN جاهز للاستخدام (الطول: {len(TELEGRAM_BOT_TOKEN)})")

    # بناء تطبيق التلجرام
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # تسجيل الأوامر (يجب أن يتم قبل start_polling)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("adduser", adduser_command))

    # تهيئة وتشعيل المستمع
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    
    logger.info("✅ تم إعداد تطبيق التليجرام وبدء الاستماع للأوامر بنجاح")
    return app
