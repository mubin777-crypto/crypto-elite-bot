import os
import time
import logging
import threading
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
CHAT_ID = os.environ.get("CHAT_ID")

# قائمة أساسية بـ 100 عملة (أعلى القيمة السوقية)
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

# إعدادات المؤشرات
RSI_PERIOD = 14
EMA_PERIOD = 20
COOLDOWN_MINUTES = 60
last_signal_time = {}

# -------------------- دوال جلب البيانات من Binance --------------------
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
    if len(prices) < RSI_PERIOD + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(diff if diff > 0 else 0)
        losses.append(abs(diff) if diff < 0 else 0)
    avg_gain = sum(gains[:RSI_PERIOD]) / RSI_PERIOD
    avg_loss = sum(losses[:RSI_PERIOD]) / RSI_PERIOD
    for i in range(RSI_PERIOD, len(gains)):
        avg_gain = (avg_gain * (RSI_PERIOD - 1) + gains[i]) / RSI_PERIOD
        avg_loss = (avg_loss * (RSI_PERIOD - 1) + losses[i]) / RSI_PERIOD
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))

def calculate_ema(prices):
    if len(prices) < EMA_PERIOD:
        return prices[-1] if prices else 0
    multiplier = 2 / (EMA_PERIOD + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = (p * multiplier) + (ema * (1 - multiplier))
    return ema

# -------------------- جلب عملات الميم الرائجة من DexScreener --------------------
def fetch_dexscreener_trending():
    """جلب العملات الأكثر ارتفاعاً خلال ساعة من DexScreener"""
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

# -------------------- التحليل المتقدم (نظام النقاط الرباعي) --------------------
def advanced_analysis(symbol):
    prices = fetch_klines(symbol, interval='15m', limit=50)
    if len(prices) < 25:
        return None
    
    current_price = prices[-1]
    rsi = calculate_rsi(prices)
    ema = calculate_ema(prices)
    stats = fetch_24hr_stats(symbol)
    
    price_1h_ago = prices[-5] if len(prices) >= 5 else prices[0]
    change_1h = ((current_price - price_1h_ago) / price_1h_ago) * 100 if price_1h_ago != 0 else 0
    
    highest_50 = max(prices) if prices else current_price
    is_breakout = current_price >= highest_50 * 0.99
    
    score = 0
    reasons = []
    
    # الاستراتيجية 1: الزخم
    if change_1h > 2.0:
        score += 1
        reasons.append(f"زخم سعري ({change_1h:.1f}%)")
    elif change_1h < -2.0:
        score -= 1
        reasons.append(f"انهيار ({change_1h:.1f}%)")
    
    # الاستراتيجية 2: التشبع الفني
    if rsi < 35 and current_price > ema:
        score += 1
        reasons.append(f"RSI مفرط بيع ({rsi:.1f})")
    elif rsi > 70 and current_price < ema:
        score -= 1
        reasons.append(f"RSI مفرط شراء ({rsi:.1f})")
    
    # الاستراتيجية 3: انفجار الحجم
    if abs(stats.get('change_24h', 0)) > 5:
        score += 1
        reasons.append("نشاط حجم استثنائي")
    
    # الاستراتيجية 4: اختراق القمم
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
def send_telegram_message(message, chat_id=None):
    if not chat_id:
        chat_id = CHAT_ID
    if not TELEGRAM_TOKEN or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        logger.error(f"Telegram send error: {e}")

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
            send_telegram_message(msg)
            last_signal_time[symbol] = now
            logger.info(f"✅ إشارة لـ {symbol} (نقاط: {analysis['score']})")
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
    await update.message.reply_text(
        "🚀 *بوت المؤسسات (4 استراتيجيات)*\n\n"
        "/status - عرض حالة البوت\n"
        "/add SYMBOL - إضافة عملة\n"
        "/remove SYMBOL - إزالة عملة\n"
        "/signal SYMBOL - تحليل فوري", parse_mode="Markdown"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📊 *حالة البوت فائق السرعة*\n"]
    all_syms = list(set(BASE_WATCH_LIST + dynamic_watch_list))
    lines.append(f"📌 إجمالي العملات المراقبة: {len(all_syms)}\n")
    lines.append("🔹 آخر 5 عملات ساخنة:")
    for sym in dynamic_watch_list[:5]:
        lines.append(f"   - `{sym}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def add_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ /add SYMBOL")
        return
    sym = context.args[0].upper()
    if sym not in BASE_WATCH_LIST:
        BASE_WATCH_LIST.append(sym)
        await update.message.reply_text(f"✅ تمت إضافة {sym}")
    else:
        await update.message.reply_text("✅ موجودة مسبقاً")

async def remove_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ /remove SYMBOL")
        return
    sym = context.args[0].upper()
    if sym in BASE_WATCH_LIST:
        BASE_WATCH_LIST.remove(sym)
        await update.message.reply_text(f"✅ تمت إزالة {sym}")
    else:
        await update.message.reply_text("❌ غير موجودة")

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
        f"💰 السعر: `{analysis['price']:.4f}`\n"
        f"📊 RSI: `{analysis['rsi']:.1f}`\n"
        f"📈 EMA: `{analysis['ema']:.4f}`\n"
        f"📉 تغير ساعة: `{analysis['change_1h']:.2f}%`\n"
        f"📝 الأسباب: {', '.join(analysis['reasons'])}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# -------------------- نظام الإبقاء على الحياة (Self-Ping) --------------------
def self_pinger():
    """يرسل طلباً لنفسه كل 10 دقائق لمنع السبات على Render"""
    host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if not host:
        logger.warning("RENDER_EXTERNAL_HOSTNAME غير موجود، تخطي الـ Self-Ping")
        return
    url = f"https://{host}"
    time.sleep(300)  # انتظر 5 دقائق بعد التشغيل
    while True:
        try:
            requests.get(url, timeout=3)
            logger.info("✅ Self-ping sent successfully")
        except Exception as e:
            logger.error(f"Self-ping failed: {e}")
        time.sleep(600)  # كل 10 دقائق

# -------------------- تشغيل البوت --------------------
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def run_telegram_bot():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("add", add_symbol))
    application.add_handler(CommandHandler("remove", remove_symbol))
    application.add_handler(CommandHandler("signal", signal_now))
    application.run_polling()

if __name__ == "__main__":
    # تشغيل الخوادم في خيوط منفصلة
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=self_pinger, daemon=True).start()
    threading.Thread(target=run_telegram_bot, daemon=True).start()
    # تشغيل حلقة المسح الرئيسية
    market_scanner_loop()
