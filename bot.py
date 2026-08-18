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
    return "✅ Elite Bot is RUNNING! (Using Binance.US)"

# -------------------- المتغيرات البيئية --------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.environ.get("CHAT_ID")

# -------------------- دوال إدارة الملفات --------------------
SUBSCRIBERS_FILE = "subscribers.json"
PENDING_FILE = "pending.json"

def load_subscribers():
    try:
        with open(SUBSCRIBERS_FILE, 'r') as f:
            data = json.load(f)
            if ADMIN_CHAT_ID and ADMIN_CHAT_ID not in data:
                data.append(ADMIN_CHAT_ID)
                save_subscribers(data)
            return data
    except:
        default = [ADMIN_CHAT_ID] if ADMIN_CHAT_ID else []
        save_subscribers(default)
        return default

def save_subscribers(data):
    with open(SUBSCRIBERS_FILE, 'w') as f:
        json.dump(data, f)

def load_pending():
    try:
        with open(PENDING_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def save_pending(data):
    with open(PENDING_FILE, 'w') as f:
        json.dump(data, f)

SUBSCRIBERS = load_subscribers()
PENDING = load_pending()
logger.info(f"✅ المشتركين: {SUBSCRIBERS}")
logger.info(f"✅ قائمة الانتظار: {PENDING}")

# -------------------- دوال جلب البيانات (Binance.US) --------------------
def fetch_klines(symbol, interval='15m', limit=50, retries=3):
    """جلب بيانات الشموع من Binance.US مع إعادة المحاولة"""
    url = f"https://api.binance.us/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    logger.info(f"📡 محاولة جلب {symbol}...")
    
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=15)
            logger.info(f"📡 {symbol} - الرد: {resp.status_code}")
            
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 0:
                    logger.info(f"✅ تم جلب {len(data)} شمعة لـ {symbol}")
                    return [float(c[4]) for c in data]
                else:
                    logger.warning(f"⚠️ بيانات فارغة لـ {symbol}")
            else:
                logger.warning(f"⚠️ Binance.US رد بـ {resp.status_code} لـ {symbol}: {resp.text[:100]}")
        except requests.exceptions.Timeout:
            logger.warning(f"⏳ مهلة الاتصال لـ {symbol} (محاولة {attempt+1}/{retries})")
        except Exception as e:
            logger.error(f"❌ خطأ في جلب {symbol}: {type(e).__name__} - {str(e)}")
        
        if attempt < retries - 1:
            time.sleep(3)
    
    logger.error(f"❌ فشل جلب {symbol} بعد {retries} محاولات")
    return []

def fetch_24hr_stats(symbol):
    """جلب إحصائيات 24 ساعة من Binance.US"""
    try:
        url = f"https://api.binance.us/api/v3/ticker/24hr?symbol={symbol}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "volume": float(data.get('quoteVolume', 0)),
                "change_24h": float(data.get('priceChangePercent', 0))
            }
    except Exception as e:
        logger.error(f"خطأ في جلب إحصائيات {symbol}: {e}")
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
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            pairs = resp.json().get('pairs', [])
            symbols = []
            for p in pairs[:30]:
                if p.get('quoteToken', {}).get('symbol') == 'USDT' and float(p.get('volume', {}).get('h24', 0)) > 30000:
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
COOLDOWN_MINUTES = 30
SIGNAL_THRESHOLD = 1
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
    logger.info(f"🔍 بدء تحليل {symbol}...")
    prices = fetch_klines(symbol, interval='15m', limit=50)
    
    if not prices or len(prices) < 25:
        logger.warning(f"⚠️ بيانات غير كافية لـ {symbol}: {len(prices)} شمعة")
        return None
    
    logger.info(f"✅ تم جلب {len(prices)} شمعة لـ {symbol}")
    current_price = prices[-1]
    rsi = calculate_rsi(prices)
    ema = calculate_ema(prices, EMA_PERIOD)
    stats = fetch_24hr_stats(symbol)
    
    price_1h_ago = prices[-5] if len(prices) >= 5 else prices[0]
    change_1h = ((current_price - price_1h_ago) / price_1h_ago) * 100 if price_1h_ago != 0 else 0
    
    highest_50 = max(prices) if prices else current_price
    is_breakout = current_price >= highest_50 * 0.98
    
    score = 0
    reasons = []
    
    if change_1h > 1.0:
        score += 1
        reasons.append(f"زخم سعري ({change_1h:.1f}%)")
    elif change_1h < -1.0:
        score -= 1
        reasons.append(f"انهيار ({change_1h:.1f}%)")
    
    if rsi < 40 and current_price > ema:
        score += 1
        reasons.append(f"RSI منخفض ({rsi:.1f})")
    elif rsi > 60 and current_price < ema:
        score -= 1
        reasons.append(f"RSI مرتفع ({rsi:.1f})")
    
    if abs(stats.get('change_24h', 0)) > 3:
        score += 1
        reasons.append("نشاط حجم")
    
    if is_breakout and change_1h > 0:
        score += 1
        reasons.append("اختراق قمة")
    
    final_signal = "⏸ انتظار"
    if score >= 2:
        final_signal = "🔥 إشارة قوية"
    elif score >= 1:
        final_signal = "✅ إشارة معتدلة"
    elif score <= -2:
        final_signal = "🔻 بيع"
    
    logger.info(f"📊 {symbol} - النقاط: {score}, الإشارة: {final_signal}")
    
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
    if not TELEGRAM_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in SUBSCRIBERS:
        try:
            requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=5)
            logger.info(f"✅ تم الإرسال إلى {chat_id}")
        except Exception as e:
            logger.error(f"فشل الإرسال إلى {chat_id}: {e}")

def send_to_admin(message):
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
        if analysis['score'] >= SIGNAL_THRESHOLD:
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
        logger.error(f"خطأ في معالجة {symbol}: {e}")
    return None

# -------------------- حلقة المسح الذكية --------------------
def market_scanner_loop():
    logger.info("🚀 بدء تشغيل الماسح فائق السرعة (100+ عملة)...")
    while True:
        global dynamic_watch_list
        try:
            trending = fetch_dexscreener_trending()
            if trending:
                dynamic_watch_list = trending[:15]
                logger.info(f"🔥 تم جلب {len(dynamic_watch_list)} عملة رائجة من DexScreener")
        except Exception as e:
            logger.error(f"DexScreener update failed: {e}")
        
        all_symbols = list(set(BASE_WATCH_LIST + dynamic_watch_list))
        logger.info(f"🔄 بدء فحص {len(all_symbols)} عملة ...")
        
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(process_single_symbol, sym): sym for sym in all_symbols}
            for future in as_completed(futures):
                try:
                    future.result(timeout=5)
                except Exception as e:
                    logger.error(f"خطأ في الخيط: {e}")
        
        logger.info("✅ انتهت دورة المسح. الانتظار 5 دقائق...")
        time.sleep(300)

# -------------------- أوامر التليجرام --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id in SUBSCRIBERS:
        await update.message.reply_text("ℹ️ أنت مشترك بالفعل. ستصل إليك الإشارات.")
        return
    if user_id in PENDING:
        await update.message.reply_text("⏳ طلبك قيد الانتظار. سيتم إعلامك عند الموافقة.")
        return
    
    PENDING.append(user_id)
    save_pending(PENDING)
    await update.message.reply_text(
        "✅ تم استلام طلب الاشتراك.\n"
        "سيقوم المالك بمراجعته وقبولك قريباً.\n"
        "ستصلك رسالة عند الموافقة."
    )
    await send_to_admin(f"📩 *طلب اشتراك جديد*\nالمعرف: `{user_id}`\nللإضافة استخدم: /approve {user_id}")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ هذا الأمر متاح فقط للمالك.")
        return
    if not context.args:
        await update.message.reply_text("⚠️ استخدم: /approve USER_ID")
        return
    user_id = context.args[0].strip()
    if user_id in PENDING:
        PENDING.remove(user_id)
        save_pending(PENDING)
        if user_id not in SUBSCRIBERS:
            SUBSCRIBERS.append(user_id)
            save_subscribers(SUBSCRIBERS)
            await update.message.reply_text(f"✅ تمت الموافقة على المستخدم `{user_id}` وإضافته.")
            try:
                await context.bot.send_message(chat_id=user_id, text="🎉 تمت الموافقة على اشتراكك! ستصل إليك الإشارات.")
            except:
                pass
        else:
            await update.message.reply_text(f"ℹ️ المستخدم `{user_id}` مشترك بالفعل.")
    else:
        await update.message.reply_text(f"❌ المستخدم `{user_id}` ليس في قائمة الانتظار.")

async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ هذا الأمر متاح فقط للمالك.")
        return
    if not context.args:
        await update.message.reply_text("⚠️ استخدم: /reject USER_ID")
        return
    user_id = context.args[0].strip()
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
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ هذا الأمر متاح فقط للمالك.")
        return
    if not context.args:
        await update.message.reply_text("⚠️ استخدم: /remove USER_ID")
        return
    user_id = context.args[0].strip()
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
        await update.message.reply_text(f"❌ لا توجد بيانات كافية لـ {sym}. تحقق من السجلات.")
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

# -------------------- نظام الإبقاء على الحياة --------------------
def self_pinger():
    host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if not host:
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
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
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
    except RuntimeError as e:
        if "Event loop is closed" in str(e):
            logger.warning("الحلقة مغلقة، نعيد إنشاءها...")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(application.run_polling())
        else:
            raise

# -------------------- إرسال رسالة تأكيد عند بدء البوت --------------------
def send_startup_notification():
    all_syms = list(set(BASE_WATCH_LIST + dynamic_watch_list))
    msg = (
        "🤖 *تم إعادة تشغيل البوت بنجاح!*\n\n"
        f"📊 يفحص حالياً *{len(all_syms)}* عملة.\n"
        f"👥 المشتركين النشطين: {len(SUBSCRIBERS)}\n"
        f"⏳ طلبات الانتظار: {len(PENDING)}\n\n"
        "🔹 الآن يستخدم **Binance.US** (متوافق مع الولايات المتحدة).\n"
        "🔹 عتبة الإشارة: نقطة واحدة (أكثر حساسية).\n"
        "🔹 فترة المسح: 5 دقائق.\n\n"
        "✅ ستصلك الإشارات عند توفرها."
    )
    send_to_all_subscribers(msg)

# -------------------- نقطة الدخول --------------------
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=self_pinger, daemon=True).start()
    threading.Thread(target=market_scanner_loop, daemon=True).start()
    
    time.sleep(5)
    send_startup_notification()
    
    run_telegram_bot()
