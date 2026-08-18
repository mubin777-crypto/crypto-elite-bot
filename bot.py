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
    return "✅ Elite Pro Bot is RUNNING!"

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

# -------------------- دوال جلب البيانات (Binance.US) --------------------
def fetch_klines(symbol, interval='15m', limit=50, retries=3):
    url = f"https://api.binance.us/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 0:
                    return [float(c[4]) for c in data]
                else:
                    logger.warning(f"⚠️ بيانات فارغة لـ {symbol}")
            else:
                logger.warning(f"⚠️ رد {resp.status_code} لـ {symbol}")
        except Exception as e:
            logger.error(f"❌ خطأ {symbol}: {e}")
        if attempt < retries - 1:
            time.sleep(3)
    return []

def fetch_24hr_stats(symbol):
    try:
        url = f"https://api.binance.us/api/v3/ticker/24hr?symbol={symbol}"
        resp = requests.get(url, timeout=10)
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
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_ema(prices, period=20):
    if len(prices) < period:
        return prices[-1] if prices else 0
    multiplier = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = (p * multiplier) + (ema * (1 - multiplier))
    return ema

# -------------------- قائمة العملات الصالحة (تم تنظيفها) --------------------
BASE_WATCH_LIST = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "SHIBUSDT",
    "ADAUSDT", "AVAXUSDT", "MATICUSDT", "DOTUSDT", "LINKUSDT", "UNIUSDT", "ATOMUSDT",
    "LTCUSDT", "BCHUSDT", "NEARUSDT", "FILUSDT", "ICPUSDT", "ETCUSDT", "XTZUSDT",
    "THETAUSDT", "XLMUSDT", "VETUSDT", "TRXUSDT", "EOSUSDT", "AAVEUSDT", "MKRUSDT",
    "SANDUSDT", "MANAUSDT", "AXSUSDT", "APEUSDT", "FTMUSDT", "ONEUSDT",
    "OCEANUSDT", "RNDRUSDT", "FETUSDT", "WIFUSDT", "BONKUSDT", "PEPEUSDT",
    "FLOKIUSDT", "BRETTUSDT", "ALGOUSDT", "ARBUSDT", "APTUSDT", "BGBUSDT",
    "BSVUSDT", "CAKEUSDT", "CELOUSDT", "COMPUSDT", "CROUSDT", "DYDXUSDT",
    "EGLDUSDT", "ENJUSDT", "FLOWUSDT", "GALAUSDT", "GRTUSDT", "HBARUSDT",
    "HNTUSDT", "IMXUSDT", "INJUSDT", "KAVAUSDT", "KSMUSDT", "LDOUSDT",
    "LRCUSDT", "MASKUSDT", "MINAUSDT", "NEOUSDT", "OKBUSDT", "OMGUSDT",
    "QNTUSDT", "RENUSDT", "ROSEUSDT", "RUNEUSDT", "RVNUSDT", "SUSHIUSDT",
    "UMAUSDT", "UNFIUSDT", "WOOUSDT", "ZECUSDT", "VTHOUSDT", "STMXUSDT"
]
dynamic_watch_list = []

RSI_PERIOD = 14
EMA_PERIOD = 20
COOLDOWN_MINUTES = 60  # زيادة فترة التبريد إلى ساعة لتجنب الإشارات المتكررة
SIGNAL_THRESHOLD = 2   # رفع العتبة إلى نقطتين
MIN_CHANGE_1H = 0.5    # تجاهل العملات التي تغيرها أقل من 0.5%
last_signal_time = {}

def advanced_analysis(symbol):
    logger.info(f"🔍 تحليل {symbol}...")
    prices = fetch_klines(symbol, interval='15m', limit=50)
    if not prices or len(prices) < 25:
        return None
    
    current_price = prices[-1]
    rsi = calculate_rsi(prices)
    ema = calculate_ema(prices, EMA_PERIOD)
    stats = fetch_24hr_stats(symbol)
    
    # التحقق من صحة RSI (استبعاد القيم غير الواقعية)
    if rsi >= 99 or rsi <= 1:
        logger.warning(f"⚠️ RSI غير طبيعي لـ {symbol}: {rsi}")
        return None
    
    price_1h_ago = prices[-5] if len(prices) >= 5 else prices[0]
    if price_1h_ago == 0:
        return None
    change_1h = ((current_price - price_1h_ago) / price_1h_ago) * 100
    
    # تجاهل العملات الراكدة (ما لم تكن مفرطة البيع/الشراء بشكل كبير)
    if abs(change_1h) < MIN_CHANGE_1H and (rsi < 25 or rsi > 75) is False:
        logger.info(f"⏸ {symbol} راكدة (تغير {change_1h:.2f}%)")
        return None
    
    highest_50 = max(prices) if prices else current_price
    is_breakout = current_price >= highest_50 * 0.98
    
    volume_24h = stats.get('volume', 0)
    avg_volume = volume_24h / 96 if volume_24h > 0 else 1
    high_volume = volume_24h > avg_volume * 2  # حجم ضعف المتوسط
    
    score = 0
    reasons = []
    signal_type = "⏸ انتظار"
    
    # 1. الزخم
    if change_1h > 1.0:
        score += 1
        reasons.append(f"زخم ({change_1h:.1f}%)")
    elif change_1h < -1.0:
        score -= 1
        reasons.append(f"انهيار ({change_1h:.1f}%)")
    
    # 2. RSI + EMA (مع شروط أكثر صرامة)
    if rsi < 35 and current_price > ema:
        score += 1
        reasons.append(f"RSI مفرط بيع ({rsi:.1f})")
    elif rsi > 65 and current_price < ema:
        score -= 1
        reasons.append(f"RSI مفرط شراء ({rsi:.1f})")
    
    # 3. الحجم (نشاط استثنائي)
    if abs(stats.get('change_24h', 0)) > 4:
        score += 1
        reasons.append("نشاط حجم")
    
    # 4. اختراق القمم + حجم قوي
    if is_breakout and change_1h > 0:
        if high_volume:
            score += 2  # اختراق بحجم قوي يعطي نقطتين
            reasons.append("اختراق بحجم قوي")
        else:
            score += 1
            reasons.append("اختراق قمة")
    
    # تصنيف الإشارة النهائية (شراء / بيع)
    if score >= 2 and rsi < 45:
        signal_type = "🟢 **شراء قوي**"
    elif score >= 2 and rsi > 55:
        signal_type = "🔴 **بيع / جني أرباح**"
    elif score >= 3:
        signal_type = "🟢 **شراء قوي جداً**"
    elif score <= -2:
        signal_type = "🔴 **بيع**"
    elif score >= 2:
        signal_type = "🟡 **مراقبة** (بدون اتجاه واضح)"
    
    # نقطة إضافية: إذا كانت الإشارة مجرد "مراقبة" ولا تستحق الإرسال
    if signal_type == "🟡 **مراقبة**" or score < SIGNAL_THRESHOLD:
        return None
    
    logger.info(f"📊 {symbol} - النقاط: {score}, الإشارة: {signal_type}")
    
    return {
        "price": current_price,
        "rsi": rsi,
        "ema": ema,
        "change_1h": change_1h,
        "score": score,
        "reasons": reasons,
        "signal": signal_type,
        "volume": stats.get('volume', 0)
    }

# -------------------- دوال الإرسال --------------------
def send_to_all_subscribers(message):
    if not TELEGRAM_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in SUBSCRIBERS:
        try:
            requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=5)
        except Exception as e:
            logger.error(f"فشل الإرسال إلى {chat_id}: {e}")

def process_single_symbol(symbol):
    try:
        analysis = advanced_analysis(symbol)
        if not analysis:
            return None
        
        now = datetime.now()
        last = last_signal_time.get(symbol)
        if last and (now - last) < timedelta(minutes=COOLDOWN_MINUTES):
            return None
        
        # بناء رسالة الإشارة
        msg = (
            f"📊 *{symbol}* | النقاط: {analysis['score']}/4\n"
            f"🔔 الإشارة: {analysis['signal']}\n"
            f"💰 السعر: `{analysis['price']:.6f}`\n"
            f"📉 RSI: `{analysis['rsi']:.1f}` | EMA: `{analysis['ema']:.6f}`\n"
            f"📈 التغير (ساعة): `{analysis['change_1h']:.2f}%`\n"
            f"📝 الأسباب: {', '.join(analysis['reasons'])}"
        )
        
        send_to_all_subscribers(msg)
        last_signal_time[symbol] = now
        logger.info(f"✅ إشارة لـ {symbol} أُرسلت إلى {len(SUBSCRIBERS)} مشترك")
        return symbol
    except Exception as e:
        logger.error(f"خطأ في {symbol}: {e}")
    return None

# -------------------- حلقة المسح الذكية --------------------
def market_scanner_loop():
    logger.info("🚀 بدء تشغيل الماسح الاحترافي...")
    while True:
        global dynamic_watch_list
        try:
            # جلب عملات ساخنة من DexScreener
            url = "https://api.dexscreener.com/latest/dex/search?q=?"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                pairs = resp.json().get('pairs', [])
                trending = []
                for p in pairs[:20]:
                    if p.get('quoteToken', {}).get('symbol') == 'USDT':
                        base = p.get('baseToken', {}).get('symbol', '')
                        if base and len(base) < 10:
                            trending.append(base.upper() + 'USDT')
                if trending:
                    dynamic_watch_list = trending[:10]
                    logger.info(f"🔥 عملات ساخنة: {len(dynamic_watch_list)}")
        except Exception as e:
            logger.error(f"DexScreener error: {e}")
        
        all_symbols = list(set(BASE_WATCH_LIST + dynamic_watch_list))
        logger.info(f"🔄 فحص {len(all_symbols)} عملة ...")
        
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(process_single_symbol, sym): sym for sym in all_symbols}
            for future in as_completed(futures):
                try:
                    future.result(timeout=5)
                except Exception as e:
                    logger.error(f"خطأ في الخيط: {e}")
        
        logger.info("✅ انتهت الدورة. الانتظار 5 دقائق...")
        time.sleep(300)

# -------------------- أوامر التليجرام --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id in SUBSCRIBERS:
        await update.message.reply_text("ℹ️ أنت مشترك بالفعل.")
        return
    if user_id in PENDING:
        await update.message.reply_text("⏳ طلبك قيد الانتظار.")
        return
    PENDING.append(user_id)
    save_pending(PENDING)
    await update.message.reply_text("✅ تم استلام طلب الاشتراك. سيتم إعلامك عند الموافقة.")
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"📩 طلب اشتراك جديد: `{user_id}`\n/approve {user_id}", parse_mode="Markdown")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ فقط للمالك.")
        return
    if not context.args:
        await update.message.reply_text("⚠️ /approve USER_ID")
        return
    user_id = context.args[0].strip()
    if user_id in PENDING:
        PENDING.remove(user_id)
        save_pending(PENDING)
        if user_id not in SUBSCRIBERS:
            SUBSCRIBERS.append(user_id)
            save_subscribers(SUBSCRIBERS)
            await update.message.reply_text(f"✅ تمت الموافقة على `{user_id}`.")
            try:
                await context.bot.send_message(chat_id=user_id, text="🎉 تمت الموافقة على اشتراكك!")
            except:
                pass
    else:
        await update.message.reply_text("❌ غير موجود في قائمة الانتظار.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_syms = list(set(BASE_WATCH_LIST + dynamic_watch_list))
    await update.message.reply_text(
        f"📊 *حالة البوت*\n"
        f"📌 العملات: {len(all_syms)}\n"
        f"👥 المشتركين: {len(SUBSCRIBERS)}\n"
        f"⏳ في الانتظار: {len(PENDING)}",
        parse_mode="Markdown"
    )

async def signal_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ /signal SYMBOL")
        return
    sym = context.args[0].upper()
    analysis = advanced_analysis(sym)
    if not analysis:
        await update.message.reply_text(f"❌ لا توجد بيانات كافية لـ {sym}")
        return
    msg = (
        f"📡 *تحليل فوري لـ {sym}*\n"
        f"🔹 النقاط: {analysis['score']}/4\n"
        f"🔹 الإشارة: {analysis['signal']}\n"
        f"💰 السعر: `{analysis['price']:.6f}`\n"
        f"📊 RSI: `{analysis['rsi']:.1f}`\n"
        f"📈 EMA: `{analysis['ema']:.6f}`\n"
        f"📉 تغير ساعة: `{analysis['change_1h']:.2f}%`\n"
        f"📝 الأسباب: {', '.join(analysis['reasons'])}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# -------------------- تشغيل البوت --------------------
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def self_pinger():
    host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if not host:
        return
    url = f"https://{host}"
    time.sleep(300)
    while True:
        try:
            requests.get(url, timeout=3)
        except:
            pass
        time.sleep(600)

def run_telegram_bot():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("approve", approve))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("signal", signal_now))
    
    try:
        loop.run_until_complete(application.run_polling())
    except RuntimeError as e:
        if "Event loop is closed" in str(e):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(application.run_polling())
        else:
            raise

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=self_pinger, daemon=True).start()
    threading.Thread(target=market_scanner_loop, daemon=True).start()
    time.sleep(5)
    run_telegram_bot()
