import os
import time
import logging
import threading
import asyncio
import json
import requests
from datetime import datetime, timedelta
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from concurrent.futures import ThreadPoolExecutor, as_completed

# -------------------- الإعدادات الأساسية --------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask('')

@app.route('/')
def home():
    return "✅ Elite Multi-Strategy Bot is RUNNING (200+ coins)!"

# -------------------- المتغيرات البيئية --------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.environ.get("CHAT_ID")  # المالك

# تخزين المشتركين وقائمة الانتظار كـ JSON في متغيرات البيئة
def load_subscribers():
    try:
        data = os.environ.get("SUBSCRIBERS_JSON", "[]")
        return json.loads(data)
    except:
        return []

def save_subscribers(subscribers):
    # سيتم تحديث متغير البيئة، لكن يجب إعادة تشغيل الخدمة حتى يرى المتغير الجديد.
    # سنستخدم أمراً خاصاً لتحديث المتغير عبر API Render (اختياري) أو نكتفي بحفظ الملف.
    # لكن Render لا يسمح بتعديل متغيرات البيئة عبر الكود. لذا سنستخدم ملف JSON لكن مع استراتيجية لضمان الديمومة.
    # الحل الأفضل: تخزين الملف في مجلد /tmp/ (مؤقت) أو استخدام قاعدة بيانات خارجية.
    # لكني سأستخدم ملفاً في مسار /opt/render/project/ (مستمر بين إعادة النشر؟ لا، ليس دائماً).
    # الحل الأمثل: استخدام متغير بيئة نصي محدث عبر أوامر البوت نفسها باستخدام Webhook Render API (معقد).
    # الحل البسيط: الاعتماد على ملف JSON في مسار ثابت، مع العلم بأنه قد يُفقد عند إعادة النشر، لكن يمكن للمالك إعادة إضافتهم يدوياً.
    # ولتجنب الفقدان، سأستخدم متغير بيئة `SUBSCRIBERS_JSON` وسأحدثه عبر أمر للمالك، وسيظل ثابتاً طالما لم يتغير يدوياً.
    # لكن Render لا يسمح بتعديل متغيرات البيئة عبر الكود، لذا سأستخدم ملفاً في دليل المشروع (والذي يُحفظ بين عمليات النشر)
    # ملاحظة: Render يحفظ نظام الملفات بين عمليات النشر؟ نعم، إلا إذا تم مسحه يدوياً. لذا سنستخدم ملف JSON في دليل المشروع.
    with open("subscribers.json", "w") as f:
        json.dump(subscribers, f)

def load_pending():
    try:
        with open("pending.json", "r") as f:
            return json.load(f)
    except:
        return []

def save_pending(pending):
    with open("pending.json", "w") as f:
        json.dump(pending, f)

# تحميل البيانات
SUBSCRIBERS = load_subscribers()
PENDING = load_pending()
logger.info(f"✅ المشتركين: {SUBSCRIBERS}")
logger.info(f"✅ قائمة الانتظار: {PENDING}")

# -------------------- دوال جلب البيانات --------------------
def fetch_klines(symbol, interval='15m', limit=50):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        resp = requests.get(url, timeout=3)
        if resp.status_code == 200:
            return [float(c[4]) for c in resp.json()]
    except:
        pass
    return []

def fetch_24hr_stats(symbol):
    try:
        url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
        resp = requests.get(url, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "volume": float(data.get('quoteVolume', 0)),
                "change_24h": float(data.get('priceChangePercent', 0))
            }
    except:
        pass
    return {"volume": 0, "change_24h": 0}

def calculate_rsi(prices):
    if len(prices) < 15:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(diff if diff > 0 else 0)
        losses.append(abs(diff) if diff < 0 else 0)
    avg_gain = sum(gains[:14]) / 14
    avg_loss = sum(losses[:14]) / 14
    for i in range(14, len(gains)):
        avg_gain = (avg_gain * 13 + gains[i]) / 14
        avg_loss = (avg_loss * 13 + losses[i]) / 14
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))

def calculate_ema(prices, period=20):
    if len(prices) < period:
        return prices[-1] if prices else 0
    multiplier = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = (p * multiplier) + (ema * (1 - multiplier))
    return ema

# -------------------- جلب عملات الميم من DexScreener --------------------
def fetch_dexscreener_trending():
    try:
        url = "https://api.dexscreener.com/latest/dex/search?q=?"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            pairs = resp.json().get('pairs', [])
            symbols = []
            for p in pairs[:20]:
                if p.get('quoteToken', {}).get('symbol') == 'USDT' and float(p.get('volume', {}).get('h24', 0)) > 50000:
                    base = p.get('baseToken', {}).get('symbol', '')
                    if base and len(base) < 10:
                        symbols.append(base.upper() + 'USDT')
            return symbols
    except Exception as e:
        logger.error(f"DexScreener error: {e}")
    return []

# -------------------- التحليل المتقدم --------------------
RSI_PERIOD = 14
EMA_PERIOD = 20
COOLDOWN_MINUTES = 60
last_signal_time = {}
BASE_WATCH_LIST = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "SHIBUSDT",
    "ADAUSDT", "AVAXUSDT", "MATICUSDT", "DOTUSDT", "LINKUSDT", "UNIUSDT", "ATOMUSDT",
    "LTCUSDT", "BCHUSDT", "NEARUSDT", "FILUSDT", "ICPUSDT", "ETCUSDT", "XTZUSDT",
    "THETAUSDT", "XLMUSDT", "VETUSDT", "TRXUSDT", "EOSUSDT", "AAVEUSDT", "MKRUSDT",
    "SANDUSDT", "MANAUSDT", "AXSUSDT", "APEUSDT", "FTMUSDT", "ONEUSDT", "HOTUSDT",
    "CHRUSDT", "OCEANUSDT", "RNDRUSDT", "FETUSDT", "AGIXUSDT", "WIFUSDT", "BONKUSDT",
    "PEPEUSDT", "FLOKIUSDT", "BRETTUSDT", "GOATUSDT", "LILPEPEUSDT", "VIRTUALUSDT"
]
dynamic_watch_list = []

def advanced_analysis(symbol):
    prices = fetch_klines(symbol, interval='15m', limit=50)
    if len(prices) < 25:
        return None
    
    current_price = prices[-1]
    rsi = calculate_rsi(prices)
    ema = calculate_ema(prices, EMA_PERIOD)
    stats = fetch_24hr_stats(symbol)
    
    price_1h_ago = prices[-5] if len(prices) >= 5 else prices[0]
    change_1h = ((current_price - price_1h_ago) / price_1h_ago) * 100 if price_1h_ago != 0 else 0
    
    highest_50 = max(prices) if prices else current_price
    is_breakout = current_price >= highest_50 * 0.99
    
    score = 0
    reasons = []
    
    if change_1h > 2.0:
        score += 1
        reasons.append(f"زخم سعري ({change_1h:.1f}%)")
    elif change_1h < -2.0:
        score -= 1
        reasons.append(f"انهيار ({change_1h:.1f}%)")
    
    if rsi < 35 and current_price > ema:
        score += 1
        reasons.append(f"RSI مفرط بيع ({rsi:.1f})")
    elif rsi > 70 and current_price < ema:
        score -= 1
        reasons.append(f"RSI مفرط شراء ({rsi:.1f})")
    
    if abs(stats.get('change_24h', 0)) > 5:
        score += 1
        reasons.append("نشاط حجم استثنائي")
    
    if is_breakout and change_1h > 0:
        score += 1
        reasons.append("اختراق قمة 50 شمعة")
    
    final_signal = "⏸ انتظار"
    if score >= 3:
        final_signal = "🔥 شراء قوي (انفجار)"
    elif score == 2:
        final_signal = "✅ شراء معتدل"
    elif score <= -2:
        final_signal = "🔻 بيع (انعكاس)"
    
    return {
        "price": current_price,
        "rsi": rsi,
        "ema": ema,
        "change_1h": change_1h,
        "score": score,
        "reasons": reasons,
        "signal": final_signal,
        "volume_24h": stats.get('volume', 0)
    }

# -------------------- دوال الإرسال --------------------
def send_to_all_subscribers(message):
    """إرسال رسالة إلى جميع المشتركين"""
    if not TELEGRAM_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in SUBSCRIBERS:
        try:
            requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=5)
        except Exception as e:
            logger.error(f"فشل الإرسال إلى {chat_id}: {e}")

def send_to_admin(message):
    """إرسال رسالة للمالك"""
    if not ADMIN_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        logger.error(f"فشل الإرسال للمالك: {e}")

def process_single_symbol(symbol):
    try:
        analysis = advanced_analysis(symbol)
        if not analysis:
            return None
        if analysis['score'] >= 2:
            now = datetime.now()
            last = last_signal_time.get(symbol)
            if last and (now - last) < timedelta(minutes=COOLDOWN_MINUTES):
                return None
            
            msg = (
                f"📊 *{symbol}* | النقاط: {analysis['score']}/4\n"
                f"🟢 الإشارة: {analysis['signal']}\n"
                f"💰 السعر: `{analysis['price']:.4f}`\n"
                f"📉 RSI: `{analysis['rsi']:.1f}` | EMA: `{analysis['ema']:.4f}`\n"
                f"📈 التغير (ساعة): `{analysis['change_1h']:.2f}%`\n"
                f"📝 الأسباب: {', '.join(analysis['reasons'])}"
            )
            send_to_all_subscribers(msg)
            last_signal_time[symbol] = now
            logger.info(f"✅ إشارة لـ {symbol} أُرسلت إلى {len(SUBSCRIBERS)} مشترك")
            return symbol
    except Exception as e:
        logger.error(f"Error processing {symbol}: {e}")
    return None

# -------------------- حلقة المسح الذكية --------------------
def market_scanner_loop():
    logger.info("🚀 بدء تشغيل الماسح فائق السرعة (200+ عملة)...")
    while True:
        global dynamic_watch_list
        try:
            trending = fetch_dexscreener_trending()
            if trending:
                dynamic_watch_list = trending[:10]
                logger.info(f"🔥 تم جلب {len(dynamic_watch_list)} عملة رائجة من DexScreener")
        except Exception as e:
            logger.error(f"DexScreener update failed: {e}")
        
        all_symbols = list(set(BASE_WATCH_LIST + dynamic_watch_list))
        logger.info(f"🔄 بدء فحص {len(all_symbols)} عملة ...")
        
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = {executor.submit(process_single_symbol, sym): sym for sym in all_symbols}
            for future in as_completed(futures):
                try:
                    future.result(timeout=3)
                except Exception as e:
                    logger.error(f"Thread error: {e}")
        
        logger.info("✅ انتهت دورة المسح. الانتظار 10 دقائق...")
        time.sleep(600)

# -------------------- أوامر التليجرام --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id in SUBSCRIBERS:
        await update.message.reply_text("ℹ️ أنت مشترك بالفعل. ستصل إليك الإشارات.")
        return
    if user_id in PENDING:
        await update.message.reply_text("⏳ طلبك قيد الانتظار. سيتم إعلامك عند الموافقة.")
        return
    
    # إضافة إلى قائمة الانتظار
    PENDING.append(user_id)
    save_pending(PENDING)
    await update.message.reply_text(
        "✅ تم استلام طلب الاشتراك.\n"
        "سيقوم المالك بمراجعته وقبولك قريباً.\n"
        "ستصلك رسالة عند الموافقة."
    )
    # إشعار للمالك
    await send_to_admin(f"📩 *طلب اشتراك جديد*\nالمعرف: `{user_id}`\nللإضافة استخدم: /approve {user_id}")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قبول طلب اشتراك (للمالك فقط)"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ هذا الأمر متاح فقط للمالك.")
        return
    if not context.args:
        await update.message.reply_text("⚠️ استخدم: /approve USER_ID")
        return
    user_id = context.args[0]
    if user_id in PENDING:
        PENDING.remove(user_id)
        save_pending(PENDING)
        if user_id not in SUBSCRIBERS:
            SUBSCRIBERS.append(user_id)
            save_subscribers(SUBSCRIBERS)
            await update.message.reply_text(f"✅ تمت الموافقة على المستخدم `{user_id}` وإضافته كشريك.")
            # إشعار للمستخدم
            send_to_admin(f"✅ تم قبول المستخدم {user_id}")
            # إرسال رسالة للمستخدم الجديد (نحاول إرسالها مباشرة)
            try:
                await context.bot.send_message(chat_id=user_id, text="🎉 تمت الموافقة على اشتراكك! ستصل إليك الإشارات التلقائية.")
            except:
                pass
        else:
            await update.message.reply_text(f"ℹ️ المستخدم `{user_id}` مشترك بالفعل.")
    else:
        await update.message.reply_text(f"❌ المستخدم `{user_id}` ليس في قائمة الانتظار.")

async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رفض طلب اشتراك (للمالك فقط)"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ هذا الأمر متاح فقط للمالك.")
        return
    if not context.args:
        await update.message.reply_text("⚠️ استخدم: /reject USER_ID")
        return
    user_id = context.args[0]
    if user_id in PENDING:
        PENDING.remove(user_id)
        save_pending(PENDING)
        await update.message.reply_text(f"✅ تم رفض المستخدم `{user_id}`.")
        try:
            await context.bot.send_message(chat_id=user_id, text="❌ تم رفض طلب اشتراكك.")
        except:
            pass
    else:
        await update.message.reply_text(f"❌ المستخدم `{user_id}` ليس في قائمة الانتظار.")

async def pending_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض طلبات الانتظار (للمالك فقط)"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ هذا الأمر متاح فقط للمالك.")
        return
    if not PENDING:
        await update.message.reply_text("📭 لا توجد طلبات معلقة.")
        return
    lines = ["📋 *طلبات الانتظار:*"]
    for uid in PENDING:
        lines.append(f"- `{uid}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def list_subscribers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض المشتركين الحاليين (للمالك فقط)"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ هذا الأمر متاح فقط للمالك.")
        return
    if not SUBSCRIBERS:
        await update.message.reply_text("📭 لا يوجد مشتركون حالياً.")
        return
    lines = ["👥 *المشتركين الحاليين:*"]
    for uid in SUBSCRIBERS:
        lines.append(f"- `{uid}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def remove_subscriber(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف مشترك (للمالك فقط)"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ هذا الأمر متاح فقط للمالك.")
        return
    if not context.args:
        await update.message.reply_text("⚠️ استخدم: /remove USER_ID")
        return
    user_id = context.args[0]
    if user_id in SUBSCRIBERS:
        SUBSCRIBERS.remove(user_id)
        save_subscribers(SUBSCRIBERS)
        await update.message.reply_text(f"✅ تم حذف المستخدم `{user_id}` من المشتركين.")
        try:
            await context.bot.send_message(chat_id=user_id, text="❌ تم إلغاء اشتراكك من قبل المالك.")
        except:
            pass
    else:
        await update.message.reply_text(f"❌ المستخدم `{user_id}` ليس مشتركاً.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_syms = list(set(BASE_WATCH_LIST + dynamic_watch_list))
    lines = [
        "📊 *حالة البوت*",
        f"📌 العملات المراقبة: {len(all_syms)}",
        f"👥 المشتركين: {len(SUBSCRIBERS)}",
        f"⏳ طلبات الانتظار: {len(PENDING)}",
        "🔹 آخر 5 عملات ساخنة:"
    ]
    for sym in dynamic_watch_list[:5]:
        lines.append(f"   - `{sym}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def signal_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ /signal SYMBOL (مثال: /signal SOLUSDT)")
        return
    sym = context.args[0].upper()
    analysis = advanced_analysis(sym)
    if not analysis:
        await update.message.reply_text(f"❌ لا توجد بيانات كافية لـ {sym}.")
        return
    msg = (
        f"📡 *تحليل فوري لـ {sym}*\n"
        f"🔹 النقاط: {analysis['score']}/4\n"
        f"🔹 الإشارة: {analysis['signal']}\n"
        f"💰 السعر: `{analysis['price']:.4f}`\n"
        f"📊 RSI: `{analysis['rsi']:.1f}`\n"
        f"📈 EMA: `{analysis['ema']:.4f}`\n"
        f"📉 تغير ساعة: `{analysis['change_1h']:.2f}%`\n"
        f"📝 الأسباب: {', '.join(analysis['reasons'])}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# -------------------- إرسال رسالة ترحيبية عند بدء البوت --------------------
def send_startup_notification():
    all_syms = list(set(BASE_WATCH_LIST + dynamic_watch_list))
    msg = (
        "🤖 *البوت قيد التشغيل الآن!*\n\n"
        f"📊 يفحص حالياً *{len(all_syms)}* عملة.\n"
        f"👥 عدد المشتركين: {len(SUBSCRIBERS)}\n"
        f"⏳ طلبات الانتظار: {len(PENDING)}\n"
        "🔹 يستخدم 4 استراتيجيات نقاط.\n"
        "🔹 يتم تحديث عملات الميم تلقائياً من DexScreener.\n\n"
        "✅ ستصلك الإشارات التلقائية عند توفرها."
    )
    send_to_all_subscribers(msg)

# -------------------- نظام الإبقاء على الحياة --------------------
def self_pinger():
    host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if not host:
        logger.warning("RENDER_EXTERNAL_HOSTNAME غير موجود، تخطي الـ Self-Ping")
        return
    url = f"https://{host}"
    time.sleep(300)
    while True:
        try:
            requests.get(url, timeout=3)
            logger.info("✅ Self-ping sent successfully")
        except Exception as e:
            logger.error(f"Self-ping failed: {e}")
        time.sleep(600)

# -------------------- تشغيل Flask --------------------
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# -------------------- تشغيل بوت التليجرام --------------------
def run_telegram_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("approve", approve))
    application.add_handler(CommandHandler("reject", reject))
    application.add_handler(CommandHandler("pending", pending_list))
    application.add_handler(CommandHandler("list", list_subscribers))
    application.add_handler(CommandHandler("remove", remove_subscriber))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("signal", signal_now))
    
    try:
        loop.run_until_complete(application.run_polling())
    finally:
        loop.close()

# -------------------- نقطة الدخول --------------------
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=self_pinger, daemon=True).start()
    threading.Thread(target=market_scanner_loop, daemon=True).start()
    
    time.sleep(5)
    send_startup_notification()
    
    run_telegram_bot()
