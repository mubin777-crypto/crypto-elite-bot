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

# -------------------- دوال جلب البيانات --------------------
def fetch_klines(symbol, interval='5m', limit=30, retries=2):
    url = f"https://api.binance.us/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 10:
                    return [float(c[4]) for c in data]
        except:
            pass
        if attempt < retries - 1:
            time.sleep(2)
    return []

def fetch_24hr_stats(symbol):
    try:
        url = f"https://api.binance.us/api/v3/ticker/24hr?symbol={symbol}"
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "volume": float(data.get('quoteVolume', 0)),
                "change_24h": float(data.get('priceChangePercent', 0)),
                "high": float(data.get('highPrice', 0)),
                "low": float(data.get('lowPrice', 0))
            }
    except:
        pass
    return {"volume": 0, "change_24h": 0, "high": 0, "low": 0}

def calculate_rsi(prices):
    if len(prices) < 14:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(diff if diff > 0 else 0)
        losses.append(abs(diff) if diff < 0 else 0)
    avg_gain = sum(gains[-14:]) / 14
    avg_loss = sum(losses[-14:]) / 14
    if avg_loss == 0:
        return 70.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return max(0, min(100, rsi))

def calculate_ema(prices, period=12):
    if len(prices) < period:
        return prices[-1] if prices else 0
    multiplier = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = (p * multiplier) + (ema * (1 - multiplier))
    return ema

# -------------------- قائمة العملات --------------------
BASE_WATCH_LIST = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "SHIBUSDT",
    "ADAUSDT", "AVAXUSDT", "MATICUSDT", "DOTUSDT", "LINKUSDT", "UNIUSDT", "ATOMUSDT",
    "LTCUSDT", "BCHUSDT", "NEARUSDT", "FILUSDT", "ICPUSDT", "ETCUSDT", "XTZUSDT",
    "THETAUSDT", "XLMUSDT", "VETUSDT", "TRXUSDT", "EOSUSDT", "AAVEUSDT", "MKRUSDT",
    "SANDUSDT", "MANAUSDT", "AXSUSDT", "APEUSDT", "FTMUSDT", "ONEUSDT", "OCEANUSDT",
    "RNDRUSDT", "FETUSDT", "WIFUSDT", "BONKUSDT", "PEPEUSDT", "FLOKIUSDT", "BRETTUSDT",
    "ALGOUSDT", "ARBUSDT", "APTUSDT", "BSVUSDT", "CAKEUSDT", "COMPUSDT", "CROUSDT",
    "EGLDUSDT", "ENJUSDT", "FLOWUSDT", "GALAUSDT", "GRTUSDT", "HBARUSDT",
    "IMXUSDT", "INJUSDT", "KAVAUSDT", "KSMUSDT", "LDOUSDT", "MASKUSDT",
    "NEOUSDT", "QNTUSDT", "RENUSDT", "ROSEUSDT", "RUNEUSDT", "RVNUSDT",
    "SUSHIUSDT", "UMAUSDT", "ZECUSDT"
]
dynamic_watch_list = []

COOLDOWN_MINUTES = 30
SIGNAL_THRESHOLD = 2
MIN_VOLUME_USD = 1000000
MIN_CHANGE_1H = 0.3
last_signal_time = {}

def advanced_analysis(symbol):
    prices = fetch_klines(symbol, interval='5m', limit=30)
    if not prices or len(prices) < 14:
        return None
    
    current_price = prices[-1]
    rsi = calculate_rsi(prices)
    ema = calculate_ema(prices, 12)
    stats = fetch_24hr_stats(symbol)
    
    if stats.get('volume', 0) < MIN_VOLUME_USD:
        return None
    if rsi >= 99 or rsi <= 1:
        return None
    
    change_1h = ((current_price - prices[-6]) / prices[-6]) * 100 if len(prices) >= 6 else 0
    if abs(change_1h) < MIN_CHANGE_1H and not (rsi < 30 or rsi > 70):
        return None
    
    avg_volume = stats.get('volume', 0) / 288
    current_volume = stats.get('volume', 0) / 288 * 1.5
    volume_spike = current_volume > avg_volume * 2.5
    
    score = 0
    reasons = []
    signal_type = "⏸ انتظار"
    
    if change_1h > 1.0:
        score += 1
        reasons.append(f"زخم ({change_1h:.1f}%)")
    elif change_1h < -1.0:
        score -= 1
        reasons.append(f"انهيار ({change_1h:.1f}%)")
    
    if rsi < 35 and current_price > ema:
        score += 1
        reasons.append(f"RSI مفرط بيع ({rsi:.1f})")
    elif rsi > 65 and current_price < ema:
        score -= 1
        reasons.append(f"RSI مفرط شراء ({rsi:.1f})")
    
    if abs(stats.get('change_24h', 0)) > 5:
        score += 1
        reasons.append(f"نشاط حجم 24h ({stats['change_24h']:.1f}%)")
    
    if volume_spike and change_1h > 0.5:
        score += 2
        reasons.append("🚀 انفجار حجم (سيولة عالية)")
    
    if current_price > stats.get('high', 0) * 0.99 and change_1h > 0:
        score += 1
        reasons.append("اختراق قمة 24h")
    
    if score >= 3 and change_1h > 0:
        signal_type = "🚀 **شراء انفجاري**"
    elif score >= 2 and rsi < 45:
        signal_type = "🟢 **شراء قوي**"
    elif score >= 2 and rsi > 55:
        signal_type = "🔴 **بيع / جني أرباح**"
    elif score >= 2:
        signal_type = "🟡 **مراقبة**"
    
    if signal_type == "🟡 **مراقبة**" or score < SIGNAL_THRESHOLD:
        return None
    
    return {
        "price": current_price,
        "rsi": rsi,
        "ema": ema,
        "change_1h": change_1h,
        "score": score,
        "reasons": reasons,
        "signal": signal_type,
        "volume_24h": stats.get('volume', 0),
        "high_24h": stats.get('high', 0),
        "volume_spike": volume_spike
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
        
        volume_str = f"${analysis['volume_24h']:,.0f}"
        if analysis['volume_24h'] >= 1_000_000_000:
            volume_str = f"${analysis['volume_24h']/1_000_000_000:.1f}B"
        elif analysis['volume_24h'] >= 1_000_000:
            volume_str = f"${analysis['volume_24h']/1_000_000:.1f}M"
        
        msg = (
            f"📊 *{symbol}* | النقاط: {analysis['score']}/5\n"
            f"🔔 {analysis['signal']}\n"
            f"💰 السعر: `{analysis['price']:.6f}`\n"
            f"📉 RSI: `{analysis['rsi']:.1f}` | EMA: `{analysis['ema']:.6f}`\n"
            f"📈 التغير (ساعة): `{analysis['change_1h']:.2f}%`\n"
            f"💧 السيولة 24h: `{volume_str}`\n"
            f"📝 الأسباب: {', '.join(analysis['reasons'])}"
        )
        
        send_to_all_subscribers(msg)
        last_signal_time[symbol] = now
        logger.info(f"✅ إشارة {symbol} أُرسلت")
        return symbol
    except Exception as e:
        logger.error(f"خطأ {symbol}: {e}")
    return None

# -------------------- حلقة المسح --------------------
def market_scanner_loop():
    logger.info("🚀 بدء الماسح الاحترافي...")
    while True:
        global dynamic_watch_list
        try:
            url = "https://api.dexscreener.com/latest/dex/search?q=?"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                pairs = resp.json().get('pairs', [])
                trending = []
                for p in pairs[:20]:
                    if p.get('quoteToken', {}).get('symbol') == 'USDT' and float(p.get('volume', {}).get('h24', 0)) > 500000:
                        base = p.get('baseToken', {}).get('symbol', '')
                        if base and len(base) < 10:
                            trending.append(base.upper() + 'USDT')
                if trending:
                    dynamic_watch_list = trending[:10]
                    logger.info(f"🔥 {len(dynamic_watch_list)} عملة ساخنة")
        except:
            pass
        
        all_symbols = list(set(BASE_WATCH_LIST + dynamic_watch_list))
        logger.info(f"🔄 فحص {len(all_symbols)} عملة ...")
        
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = {executor.submit(process_single_symbol, sym): sym for sym in all_symbols}
            for future in as_completed(futures):
                try:
                    future.result(timeout=5)
                except:
                    pass
        
        logger.info("✅ انتهت الدورة. انتظار 5 دقائق...")
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
    await update.message.reply_text("✅ تم استلام طلب الاشتراك. سيتم إعلامك عند الموافقة.")
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"📩 طلب اشتراك جديد: `{user_id}`\n/approve {user_id}", parse_mode="Markdown")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ فقط للمالك.")
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
            await update.message.reply_text(f"✅ تمت الموافقة على `{user_id}`.")
            try:
                await context.bot.send_message(chat_id=user_id, text="🎉 تمت الموافقة على اشتراكك!")
            except:
                pass
    else:
        await update.message.reply_text("❌ غير موجود في قائمة الانتظار.")

# ==================== الأمر الجديد: إضافة مستخدم يدوياً ====================
async def add_user_manually(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة مستخدم يدوياً عن طريق المعرف (للمالك فقط)"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ هذا الأمر متاح فقط للمالك.")
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ استخدم: /adduser USER_ID\nمثال: /adduser 123456789")
        return
    
    user_id = context.args[0].strip()
    
    if not user_id.isdigit():
        await update.message.reply_text("❌ المعرف يجب أن يكون أرقاماً فقط.")
        return
    
    if user_id == ADMIN_CHAT_ID:
        await update.message.reply_text("ℹ️ أنت المالك، مشترك بالفعل.")
        return
    
    if user_id in SUBSCRIBERS:
        await update.message.reply_text(f"ℹ️ المستخدم `{user_id}` مشترك بالفعل.", parse_mode="Markdown")
        return
    
    SUBSCRIBERS.append(user_id)
    save_subscribers(SUBSCRIBERS)
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="🎉 *تمت إضافتك إلى بوت الإشارات!*\n\nستصلك الإشارات التلقائية عند توفرها.",
            parse_mode="Markdown"
        )
        await update.message.reply_text(f"✅ تمت إضافة المستخدم `{user_id}` بنجاح، وتم إرسال رسالة ترحيب له.")
    except Exception as e:
        await update.message.reply_text(
            f"✅ تمت إضافة المستخدم `{user_id}` ولكن لم نتمكن من إرسال رسالة ترحيب له (قد يكون لم يبدأ البوت بعد).",
            parse_mode="Markdown"
        )
        logger.warning(f"لم نتمكن من إرسال رسالة للمستخدم {user_id}: {e}")
    
    logger.info(f"➕ المالك أضاف مستخدم يدوياً: {user_id}")

# ====================================================================

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_syms = list(set(BASE_WATCH_LIST + dynamic_watch_list))
    await update.message.reply_text(
        f"📊 *حالة البوت*\n"
        f"📌 العملات: {len(all_syms)}\n"
        f"👥 المشتركين: {len(SUBSCRIBERS)}\n"
        f"⏳ في الانتظار: {len(PENDING)}\n"
        f"💧 الحد الأدنى للسيولة: $1M",
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
    volume_str = f"${analysis['volume_24h']:,.0f}"
    msg = (
        f"📡 *تحليل فوري لـ {sym}*\n"
        f"🔹 النقاط: {analysis['score']}/5\n"
        f"🔹 الإشارة: {analysis['signal']}\n"
        f"💰 السعر: `{analysis['price']:.6f}`\n"
        f"📊 RSI: `{analysis['rsi']:.1f}`\n"
        f"📈 EMA: `{analysis['ema']:.6f}`\n"
        f"📉 تغير ساعة: `{analysis['change_1h']:.2f}%`\n"
        f"💧 السيولة 24h: `{volume_str}`\n"
        f"📝 الأسباب: {', '.join(analysis['reasons'])}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# -------------------- تشغيل البوت --------------------
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=False)

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
    application.add_handler(CommandHandler("adduser", add_user_manually))  # الأمر الجديد
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
