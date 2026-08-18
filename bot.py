import os
import time
import logging
import threading
import asyncio
import json
import math
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
    return "✅ Elite Pro Bot v2.0 is RUNNING!"

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

# -------------------- دوال جلب البيانات المحسنة --------------------
def fetch_klines(symbol, interval='5m', limit=50, retries=2):
    """جلب بيانات الشموع مع إعادة المحاولة"""
    url = f"https://api.binance.us/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 20:
                    return {
                        "prices": [float(c[4]) for c in data],
                        "highs": [float(c[2]) for c in data],
                        "lows": [float(c[3]) for c in data],
                        "volumes": [float(c[5]) for c in data]
                    }
        except:
            pass
        if attempt < retries - 1:
            time.sleep(2)
    return None

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
                "low": float(data.get('lowPrice', 0)),
                "open": float(data.get('openPrice', 0))
            }
    except:
        pass
    return {"volume": 0, "change_24h": 0, "high": 0, "low": 0, "open": 0}

def fetch_order_book(symbol, limit=10):
    """جلب عمق السوق (السيولة الحقيقية)"""
    try:
        url = f"https://api.binance.us/api/v3/depth?symbol={symbol}&limit={limit}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            bids = sum(float(b[1]) for b in data['bids'][:5])
            asks = sum(float(a[1]) for a in data['asks'][:5])
            return {"bids": bids, "asks": asks, "spread": (float(data['asks'][0][0]) / float(data['bids'][0][0]) - 1) * 100}
    except:
        pass
    return {"bids": 0, "asks": 0, "spread": 0}

# -------------------- المؤشرات الفنية المتقدمة --------------------
def calculate_rsi(prices, period=14):
    if len(prices) < period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(diff if diff > 0 else 0)
        losses.append(abs(diff) if diff < 0 else 0)
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
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

def calculate_sma(prices, period=20):
    if len(prices) < period:
        return prices[-1] if prices else 0
    return sum(prices[-period:]) / period

def calculate_macd(prices):
    if len(prices) < 26:
        return {"macd": 0, "signal": 0, "histogram": 0}
    ema12 = calculate_ema(prices, 12)
    ema26 = calculate_ema(prices, 26)
    macd = ema12 - ema26
    signal = macd * 0.9
    histogram = macd - signal
    return {"macd": macd, "signal": signal, "histogram": histogram}

def calculate_bollinger(prices, period=20, std=2):
    if len(prices) < period:
        return {"upper": 0, "middle": 0, "lower": 0}
    sma = sum(prices[-period:]) / period
    variance = sum((p - sma) ** 2 for p in prices[-period:]) / period
    std_dev = math.sqrt(variance)
    return {
        "upper": sma + (std_dev * std),
        "middle": sma,
        "lower": sma - (std_dev * std)
    }

def calculate_atr(highs, lows, closes, period=14):
    if len(closes) < period:
        return 0
    tr_list = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_list.append(tr)
    return sum(tr_list[-period:]) / period

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

COOLDOWN_MINUTES = 20
SIGNAL_THRESHOLD = 2
MIN_VOLUME_USD = 500000
MIN_CHANGE_1H = 0.2
last_signal_time = {}

def advanced_analysis(symbol):
    data_5m = fetch_klines(symbol, '5m', 50)
    data_1h = fetch_klines(symbol, '1h', 30)
    data_4h = fetch_klines(symbol, '4h', 20)
    
    if not data_5m or not data_1h or not data_4h:
        return None
    
    prices_5m = data_5m['prices']
    highs_5m = data_5m['highs']
    lows_5m = data_5m['lows']
    volumes_5m = data_5m['volumes']
    
    stats = fetch_24hr_stats(symbol)
    if stats.get('volume', 0) < MIN_VOLUME_USD:
        return None
    
    current_price = prices_5m[-1]
    rsi = calculate_rsi(prices_5m, 14)
    if rsi >= 99 or rsi <= 1:
        return None
    
    ema12 = calculate_ema(prices_5m, 12)
    ema26 = calculate_ema(prices_5m, 26)
    sma20 = calculate_sma(prices_5m, 20)
    macd = calculate_macd(prices_5m)
    bb = calculate_bollinger(prices_5m, 20, 2)
    atr = calculate_atr(highs_5m, lows_5m, prices_5m, 14)
    
    change_1h = ((prices_5m[-1] - prices_5m[-6]) / prices_5m[-6]) * 100 if len(prices_5m) >= 6 else 0
    if abs(change_1h) < MIN_CHANGE_1H and not (rsi < 30 or rsi > 70):
        return None
    
    avg_volume = stats.get('volume', 0) / 288
    current_volume = volumes_5m[-1] if volumes_5m else 0
    volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
    
    order_book = fetch_order_book(symbol)
    liquidity = order_book['bids'] + order_book['asks']
    
    # نظام النقاط
    score = 0
    reasons = []
    
    # 1. الزخم
    if change_1h > 1.5:
        score += 1
        reasons.append(f"زخم قوي ({change_1h:.1f}%)")
    elif change_1h > 0.5:
        score += 0.5
        reasons.append(f"زخم معتدل ({change_1h:.1f}%)")
    elif change_1h < -1.5:
        score -= 1
        reasons.append(f"انهيار ({change_1h:.1f}%)")
    
    # 2. RSI
    if rsi < 35 and current_price > ema12:
        score += 1
        reasons.append(f"RSI مفرط بيع ({rsi:.1f})")
    elif rsi > 65 and current_price < ema12:
        score -= 1
        reasons.append(f"RSI مفرط شراء ({rsi:.1f})")
    
    # 3. MACD
    if macd['histogram'] > 0 and macd['histogram'] > macd['histogram'] * 1.1:
        score += 1
        reasons.append("تقاطع MACD إيجابي")
    elif macd['histogram'] < 0 and macd['histogram'] < macd['histogram'] * 1.1:
        score -= 1
        reasons.append("تقاطع MACD سلبي")
    
    # 4. بولينجر
    if current_price < bb['lower'] and change_1h > 0:
        score += 1
        reasons.append("اختراق دعم بولينجر")
    elif current_price > bb['upper'] and change_1h < 0:
        score -= 1
        reasons.append("اختراق مقاومة بولينجر")
    
    # 5. حجم
    if volume_ratio > 2.5:
        score += 1.5
        reasons.append(f"🚀 انفجار حجم ({volume_ratio:.1f}x)")
    elif volume_ratio > 1.5:
        score += 1
        reasons.append(f"نشاط حجم ({volume_ratio:.1f}x)")
    
    # 6. سيولة
    if liquidity > 100000:
        score += 0.5
        reasons.append("سيولة عالية")
    
    # 7. الاتجاه
    trend_1h = prices_1h[-1] > calculate_sma(prices_1h, 20) if len(prices_1h) >= 20 else False
    trend_4h = prices_4h[-1] > calculate_sma(prices_4h, 20) if len(prices_4h) >= 20 else False
    if trend_1h and trend_4h:
        score += 1
        reasons.append("اتجاه صاعد (1h+4h)")
    elif not trend_1h and not trend_4h:
        score -= 0.5
        reasons.append("اتجاه هابط (1h+4h)")
    
    # إشارة
    signal_type = "⏸ انتظار"
    stop_loss = current_price * 0.97
    take_profit = current_price * 1.06
    position_size = 0
    
    if score >= 3 and change_1h > 0:
        signal_type = "🚀 **شراء انفجاري**"
        stop_loss = current_price * 0.97
        take_profit = current_price * 1.08
        position_size = 0.5
    elif score >= 2.5 and change_1h > 0.5:
        signal_type = "🟢 **شراء قوي**"
        stop_loss = current_price * 0.975
        take_profit = current_price * 1.06
        position_size = 1.0
    elif score >= 2.5 and change_1h < -0.5:
        signal_type = "🔴 **بيع / جني أرباح**"
        stop_loss = current_price * 1.025
        take_profit = current_price * 0.95
        position_size = 1.0
    elif score >= 2:
        signal_type = "🟡 **مراقبة**"
    
    if signal_type in ["🟡 **مراقبة**", "⏸ انتظار"] or score < 2:
        return None
    
    # ATR
    atr_stop = atr * 2 if atr > 0 else current_price * 0.02
    if "شراء" in signal_type:
        stop_loss = max(stop_loss, current_price - atr_stop)
        take_profit = max(take_profit, current_price + atr_stop * 2)
    elif "بيع" in signal_type:
        stop_loss = min(stop_loss, current_price + atr_stop)
        take_profit = min(take_profit, current_price - atr_stop * 2)
    
    return {
        "symbol": symbol,
        "price": current_price,
        "rsi": rsi,
        "ema12": ema12,
        "ema26": ema26,
        "sma20": sma20,
        "macd": macd,
        "bb": bb,
        "atr": atr,
        "change_1h": change_1h,
        "volume_ratio": volume_ratio,
        "liquidity": liquidity,
        "score": round(score, 1),
        "reasons": reasons,
        "signal": signal_type,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "position_size": position_size,
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
            f"📊 *{analysis['symbol']}* | النقاط: {analysis['score']}/7\n"
            f"🔔 {analysis['signal']}\n\n"
            f"💰 السعر: `{analysis['price']:.6f}`\n"
            f"📉 RSI: `{analysis['rsi']:.1f}` | MACD: `{analysis['macd']['histogram']:.4f}`\n"
            f"📊 بولينجر: الأعلى `{analysis['bb']['upper']:.6f}` | الأدنى `{analysis['bb']['lower']:.6f}`\n"
            f"📈 التغير (ساعة): `{analysis['change_1h']:.2f}%`\n"
            f"📊 الحجم النسبي: `{analysis['volume_ratio']:.1f}x`\n"
            f"💧 السيولة 24h: `{volume_str}`\n"
            f"📊 عمق السوق: `{analysis['liquidity']:,.0f}`\n"
            f"📝 الأسباب: {', '.join(analysis['reasons'])}\n\n"
            f"🛡️ **إدارة المخاطر:**\n"
            f"• وقف الخسارة: `{analysis['stop_loss']:.6f}`\n"
            f"• جني الأرباح: `{analysis['take_profit']:.6f}`\n"
            f"• حجم الصفقة: `{analysis['position_size']:.1f}%` من المحفظة"
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
    logger.info("🚀 بدء الماسح الاحترافي v2.0...")
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
        await update.message.reply_text("ℹ️ أنت مشترك بالفعل. ستصل إليك الإشارات مع إدارة المخاطر.")
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
                await context.bot.send_message(chat_id=user_id, text="🎉 تمت الموافقة على اشتراكك! ستصل إليك الإشارات مع إدارة المخاطر.")
            except:
                pass
    else:
        await update.message.reply_text("❌ غير موجود في قائمة الانتظار.")

async def add_user_manually(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            text="🎉 *تمت إضافتك إلى بوت الإشارات الاحترافي!*\n\nستصلك الإشارات مع تحليل متكامل وإدارة مخاطر.",
            parse_mode="Markdown"
        )
        await update.message.reply_text(f"✅ تمت إضافة المستخدم `{user_id}` بنجاح.")
    except Exception as e:
        await update.message.reply_text(f"✅ تمت إضافة المستخدم `{user_id}` ولكن لم نتمكن من إرسال رسالة ترحيب له.")
        logger.warning(f"لم نتمكن من إرسال رسالة للمستخدم {user_id}: {e}")
    logger.info(f"➕ المالك أضاف مستخدم يدوياً: {user_id}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_syms = list(set(BASE_WATCH_LIST + dynamic_watch_list))
    await update.message.reply_text(
        f"📊 *حالة البوت v2.0*\n"
        f"📌 العملات: {len(all_syms)}\n"
        f"👥 المشتركين: {len(SUBSCRIBERS)}\n"
        f"⏳ في الانتظار: {len(PENDING)}\n"
        f"💧 الحد الأدنى للسيولة: $500K\n"
        f"📊 المؤشرات: RSI, MACD, بولينجر, متوسطات متحركة\n"
        f"🛡️ إدارة المخاطر: مدمجة (وقف الخسارة + جني الأرباح)",
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
        f"🔹 النقاط: {analysis['score']}/7\n"
        f"🔹 الإشارة: {analysis['signal']}\n"
        f"💰 السعر: `{analysis['price']:.6f}`\n"
        f"📊 RSI: `{analysis['rsi']:.1f}`\n"
        f"📈 MACD: `{analysis['macd']['histogram']:.4f}`\n"
        f"📊 بولينجر: الأعلى `{analysis['bb']['upper']:.6f}` | الأدنى `{analysis['bb']['lower']:.6f}`\n"
        f"📈 تغير ساعة: `{analysis['change_1h']:.2f}%`\n"
        f"📊 الحجم النسبي: `{analysis['volume_ratio']:.1f}x`\n"
        f"💧 السيولة 24h: `{volume_str}`\n"
        f"📊 عمق السوق: `{analysis['liquidity']:,.0f}`\n"
        f"📝 الأسباب: {', '.join(analysis['reasons'])}\n\n"
        f"🛡️ وقف الخسارة: `{analysis['stop_loss']:.6f}`\n"
        f"🎯 جني الأرباح: `{analysis['take_profit']:.6f}`\n"
        f"📊 حجم الصفقة: `{analysis['position_size']:.1f}%` من المحفظة"
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
    application.add_handler(CommandHandler("adduser", add_user_manually))
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
