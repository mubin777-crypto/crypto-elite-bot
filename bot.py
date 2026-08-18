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
    return "✅ Elite Bot is RUNNING! (100+ coins)"

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
COOLDOWN_MINUTES = 30  # خفضنا إلى 30 دقيقة
SIGNAL_THRESHOLD = 1   # خفضنا إلى نقطة واحدة
last_signal_time = {}

# زيادة قائمة العملات إلى 100+
BASE_WATCH_LIST = [
    # العملات الكبرى
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "SHIBUSDT",
    "ADAUSDT", "AVAXUSDT", "MATICUSDT", "DOTUSDT", "LINKUSDT", "UNIUSDT", "ATOMUSDT",
    "LTCUSDT", "BCHUSDT", "NEARUSDT", "FILUSDT", "ICPUSDT", "ETCUSDT", "XTZUSDT",
    "THETAUSDT", "XLMUSDT", "VETUSDT", "TRXUSDT", "EOSUSDT", "AAVEUSDT", "MKRUSDT",
    "SANDUSDT", "MANAUSDT", "AXSUSDT", "APEUSDT", "FTMUSDT", "ONEUSDT", "HOTUSDT",
    "CHRUSDT", "OCEANUSDT", "RNDRUSDT", "FETUSDT", "AGIXUSDT", "WIFUSDT", "BONKUSDT",
    "PEPEUSDT", "FLOKIUSDT", "BRETTUSDT", "GOATUSDT", "LILPEPEUSDT", "VIRTUALUSDT",
    # عملات إضافية
    "ALGOUSDT", "ARBUSDT", "APTUSDT", "BGBUSDT", "BSVUSDT", "CAKEUSDT", "CELOUSDT",
    "COMPUSDT", "CROUSDT", "DYDXUSDT", "EGLDUSDT", "ENJUSDT", "EOSUSDT", "FLOWUSDT",
    "GALAUSDT", "GRTUSDT", "HBARUSDT", "HNTUSDT", "ICPUSDT", "IMXUSDT", "INJUSDT",
    "KAVAUSDT", "KSMUSDT", "LDOUSDT", "LEOUSD", "LRCUSDT", "MASKUSDT", "MINAUSDT",
    "NEOUSDT", "OKBUSDT", "OMGUSDT", "PAXGUSDT", "QNTUSDT", "RENUSDT", "ROSEUSDT",
    "RUNEUSDT", "RVNUSDT", "SUSHIUSDT", "UMAUSDT", "UNFIUSDT", "WOOUSDT", "ZECUSDT"
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
    is_breakout = current_price >= highest_50 * 0.98  # خفضنا العتبة إلى 98% بدلاً من 99%
    
    score = 0
    reasons = []
    
    # استراتيجية الزخم (خففنا الشرط إلى 1% بدلاً من 2%)
    if change_1h > 1.0:
        score += 1
        reasons.append(f"زخم سعري ({change_1h:.1f}%)")
    elif change_1h < -1.0:
        score -= 1
        reasons.append(f"انهيار ({change_1h:.1f}%)")
    
    # استراتيجية RSI (خففنا العتبة إلى 40 و 60 بدلاً من 35 و 70)
    if rsi < 40 and current_price > ema:
        score += 1
        reasons.append(f"RSI منخفض ({rsi:.1f})")
    elif rsi > 60 and current_price < ema:
        score -= 1
        reasons.append(f"RSI مرتفع ({rsi:.1f})")
    
    # استراتيجية الحجم (خففنا إلى 3% بدلاً من 5%)
    if abs(stats.get('change_24h', 0)) > 3:
        score += 1
        reasons.append("نشاط حجم")
    
    # استراتيجية اختراق القمم
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

def process_single_symbol(symbol):
    try:
        analysis = advanced_analysis(symbol)
        if not analysis:
            return None
        # الآن نرسل الإشارة إذا كانت النقاط >= 1 (بدلاً من 2)
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
        logger.error(f"Error processing {symbol}: {e}")
    return None

# -------------------- حلقة المسح الذكية (تقليل المدة إلى 5 دقائق) --------------------
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
                    future.result(timeout=3)
                except Exception as e:
                    logger.error(f"Thread error: {e}")
        
        logger.info("✅ انتهت دورة المسح. الانتظار 5 دقائق...")
        time.sleep(300)  # 5 دقائق بدلاً من 10

# -------------------- بقية الكود (الأوامر، Flask، التشغيل) --------------------
# ... (نفس الكود السابق مع تحديث رسائل التأكيد)

# -------------------- إرسال رسالة تأكيد عند بدء البوت --------------------
def send_startup_notification():
    all_syms = list(set(BASE_WATCH_LIST + dynamic_watch_list))
    msg = (
        "🤖 *تم إعادة تشغيل البوت بنجاح!*\n\n"
        f"📊 يفحص حالياً *{len(all_syms)}* عملة.\n"
        f"👥 المشتركين النشطين: {len(SUBSCRIBERS)}\n"
        f"⏳ طلبات الانتظار: {len(PENDING)}\n\n"
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
