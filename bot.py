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
    return "✅ Elite Pro Bot v5.0 (Coinbase + Binance) with Enhanced Signal Evaluation is RUNNING!"

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

# -------------------- دوال جلب البيانات من Coinbase --------------------
def fetch_coinbase_klines(symbol, interval='5m', limit=50):
    try:
        symbol_cb = symbol.replace('USDT', '-USD')
        url = f"https://api.exchange.coinbase.com/products/{symbol_cb}/candles"
        granularity = 300 if interval == '5m' else 900
        params = {'granularity': granularity, 'limit': limit}
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data and len(data) > 0:
                return {
                    "prices": [c[4] for c in data],
                    "highs": [c[2] for c in data],
                    "lows": [c[1] for c in data],
                    "volumes": [c[5] for c in data],
                    "opens": [c[3] for c in data]
                }
    except Exception as e:
        logger.debug(f"Coinbase error: {e}")
    return None

def fetch_coinbase_24hr_stats(symbol):
    try:
        symbol_cb = symbol.replace('USDT', '-USD')
        url = f"https://api.exchange.coinbase.com/products/{symbol_cb}/stats"
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            # Coinbase لا تقدم التغير المئوي مباشرة، نحسبه من open و last
            last = float(data.get('last', 0))
            open_price = float(data.get('open', 0))
            change_24h = ((last - open_price) / open_price * 100) if open_price != 0 else 0
            return {
                "volume": float(data.get('volume', 0)) * last,  # تحويل حجم العملة إلى دولار
                "change_24h": change_24h,
                "high": float(data.get('high', 0)),
                "low": float(data.get('low', 0)),
                "open": open_price,
                "last": last
            }
    except:
        pass
    return None

# -------------------- دوال جلب البيانات من Binance.US --------------------
def fetch_binance_klines(symbol, interval='5m', limit=50):
    try:
        url = f"https://api.binance.us/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data and len(data) > 0:
                return {
                    "prices": [float(c[4]) for c in data],
                    "highs": [float(c[2]) for c in data],
                    "lows": [float(c[3]) for c in data],
                    "volumes": [float(c[5]) for c in data],
                    "opens": [float(c[1]) for c in data]
                }
    except:
        pass
    return None

def fetch_binance_24hr_stats(symbol):
    try:
        url = f"https://api.binance.us/api/v3/ticker/24hr?symbol={symbol}"
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "volume": float(data.get('quoteVolume', 0)),  # حجم الدولار
                "change_24h": float(data.get('priceChangePercent', 0)),
                "high": float(data.get('highPrice', 0)),
                "low": float(data.get('lowPrice', 0)),
                "open": float(data.get('openPrice', 0)),
                "last": float(data.get('lastPrice', 0))
            }
    except:
        pass
    return None

# -------------------- دوال جلب البيانات الرئيسية --------------------
def fetch_klines(symbol, interval='5m', limit=50, retries=2):
    for attempt in range(retries):
        data = fetch_coinbase_klines(symbol, interval, limit)
        if data and len(data['prices']) > 10:
            logger.info(f"✅ Coinbase: {symbol} - {len(data['prices'])} شمعة")
            return data
        data = fetch_binance_klines(symbol, interval, limit)
        if data and len(data['prices']) > 10:
            logger.info(f"✅ Binance.US: {symbol} - {len(data['prices'])} شمعة")
            return data
        if attempt < retries - 1:
            time.sleep(2)
    logger.warning(f"⚠️ فشل جلب {symbol} من جميع المصادر")
    return None

def fetch_24hr_stats(symbol):
    stats = fetch_coinbase_24hr_stats(symbol)
    if stats and stats.get('volume', 0) > 0:
        return stats
    stats = fetch_binance_24hr_stats(symbol)
    if stats and stats.get('volume', 0) > 0:
        return stats
    return {"volume": 0, "change_24h": 0, "high": 0, "low": 0, "open": 0, "last": 0}

# -------------------- المؤشرات الفنية --------------------
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
    signal = calculate_ema([macd] * 9, 9) if len([macd] * 9) >= 9 else macd
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

# -------------------- دالة تقييم الإشارة المحسنة --------------------
def evaluate_signal(rsi, volume_multiplier, liquidity_ok, price_near_upper_bollinger, change_1h):
    """
    تقييم الإشارة بناءً على معايير متعددة، تعيد نقاط وتصنيف وأسباب.
    """
    # فلترة RSI الصارمة
    if rsi > 75:
        return {
            "status": "مرفوض",
            "score": 0.0,
            "signal": "🔴 تجنب الدخول",
            "reasons": ["RSI مرتفع جداً (> 75) - مخاطر تصحيح عالية"]
        }
    if rsi < 25:
        # في حالة انخفاض RSI قد تكون فرصة شراء لكن نفضل الحذر
        pass

    score = 0.0
    reasons = []

    # 1. تقييم RSI
    if 40 <= rsi <= 60:
        score += 3.0
        reasons.append("زخم RSI في النطاق الآمن")
    elif 60 < rsi <= 70:
        score += 1.5
        reasons.append("زخم مرتفع مع الحذر")
    elif 25 <= rsi < 40:
        score += 2.0
        reasons.append("منطقة تشبع بيعي (فرصة)")
    else:
        score += 0.5
        reasons.append("زخم ضعيف")

    # 2. تقييم الحجم
    if volume_multiplier >= 2.0:
        score += 3.0
        reasons.append(f"🚀 انفجار حجم ({volume_multiplier:.1f}x)")
    elif volume_multiplier >= 1.3:
        score += 2.0
        reasons.append(f"نشاط حجم جيد ({volume_multiplier:.1f}x)")
    else:
        score += 0.5
        reasons.append("حجم ضعيف")

    # 3. تقييم السيولة
    if liquidity_ok:
        score += 2.0
        reasons.append("سيولة عالية")
    else:
        score += 0.5
        reasons.append("سيولة منخفضة")

    # 4. موقع السعر من البولينجر
    if not price_near_upper_bollinger:
        score += 2.0
        reasons.append("مساحة للصعود (بعيد عن الحد العلوي)")
    else:
        score += 0.5
        reasons.append("السعر قريب من الحد العلوي")

    # 5. الزخم السعري (إضافة جديدة)
    if change_1h > 1.5:
        score += 1.0
        reasons.append(f"زخم سعري قوي ({change_1h:.1f}%)")
    elif change_1h > 0.5:
        score += 0.5
        reasons.append(f"زخم سعري معتدل ({change_1h:.1f}%)")

    final_score = round(score, 1)

    if final_score >= 8.0:
        signal_label = "🟢 شراء قوي"
    elif final_score >= 5.0:
        signal_label = "🟡 شراء معتدل / مراقبة"
    else:
        signal_label = "⚪ حيادي / تجنب"

    return {
        "status": "مقبول",
        "score": final_score,
        "signal": signal_label,
        "reasons": reasons
    }

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
MIN_VOLUME_USD = 200_000
MIN_CHANGE_1H = 0.2
MAX_POSITION_SIZE = 0.02
RISK_PER_TRADE = 0.01
last_signal_time = {}

def advanced_analysis(symbol):
    """تحليل متكامل مع تقييم محسن للإشارة"""
    data_5m = fetch_klines(symbol, '5m', 100)
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
    macd = calculate_macd(prices_5m)
    bb = calculate_bollinger(prices_5m, 20, 2)
    atr = calculate_atr(highs_5m, lows_5m, prices_5m, 14)
    
    change_1h = ((prices_5m[-1] - prices_5m[-6]) / prices_5m[-6]) * 100 if len(prices_5m) >= 6 else 0
    if abs(change_1h) < MIN_CHANGE_1H and not (rsi < 30 or rsi > 70):
        return None
    
    avg_volume = stats.get('volume', 0) / 288
    current_volume = volumes_5m[-1] if volumes_5m else 0
    volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
    
    # تحديد موقع السعر من البولينجر
    price_near_upper = current_price > bb['upper'] * 0.98 if bb['upper'] > 0 else False
    
    # تقييم السيولة
    liquidity_ok = stats.get('volume', 0) > 1_000_000  # دولار
    
    # استدعاء دالة التقييم المحسنة
    eval_result = evaluate_signal(rsi, volume_ratio, liquidity_ok, price_near_upper, change_1h)
    
    # إذا كانت الإشارة مرفوضة أو ضعيفة، نلغيها
    if eval_result['status'] == 'مرفوض' or eval_result['score'] < 5.0:
        return None
    
    # حساب إدارة المخاطر الديناميكية
    min_stop_pct = 0.01  # 1% كحد أدنى
    atr_stop = atr * 2 if atr > 0 else current_price * 0.015
    stop_loss = current_price - max(atr_stop, current_price * min_stop_pct)
    take_profit = current_price + max(atr_stop * 2, current_price * 0.02)
    
    # معالجة الأسعار الصغيرة (دقة 8 أو 6)
    price_precision = 8 if symbol in ["PEPEUSDT", "SHIBUSDT", "BONKUSDT"] else 6
    stop_loss = round(stop_loss, price_precision)
    take_profit = round(take_profit, price_precision)
    
    position_size = min(MAX_POSITION_SIZE, RISK_PER_TRADE * 100 / (abs(current_price - stop_loss) / current_price * 100))
    position_size = round(position_size, 2)
    
    return {
        "symbol": symbol,
        "price": round(current_price, price_precision),
        "rsi": round(rsi, 1),
        "ema12": round(ema12, price_precision),
        "ema26": round(ema26, price_precision),
        "macd": {"histogram": round(macd['histogram'], 6)},
        "bb": {
            "upper": round(bb['upper'], price_precision),
            "lower": round(bb['lower'], price_precision)
        },
        "change_1h": round(change_1h, 2),
        "volume_ratio": round(volume_ratio, 1),
        "score": eval_result['score'],
        "reasons": eval_result['reasons'],
        "signal": eval_result['signal'],
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "position_size": position_size,
        "volume_24h": stats.get('volume', 0)
    }

# -------------------- دوال الإرسال والمعالجة --------------------
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
            f"📊 *{analysis['symbol']}* | النقاط: {analysis['score']}/10\n"
            f"🔔 {analysis['signal']}\n\n"
            f"💰 السعر: `{analysis['price']}`\n"
            f"📉 RSI: `{analysis['rsi']}` | MACD: `{analysis['macd']['histogram']}`\n"
            f"📊 بولينجر: الأعلى `{analysis['bb']['upper']}` | الأدنى `{analysis['bb']['lower']}`\n"
            f"📈 التغير (ساعة): `{analysis['change_1h']}%`\n"
            f"📊 الحجم النسبي: `{analysis['volume_ratio']}x`\n"
            f"💧 السيولة 24h: `{volume_str}`\n"
            f"📝 الأسباب: {', '.join(analysis['reasons'])}\n\n"
            f"🛡️ **إدارة المخاطر:**\n"
            f"• وقف الخسارة: `{analysis['stop_loss']}`\n"
            f"• جني الأرباح: `{analysis['take_profit']}`\n"
            f"• حجم الصفقة: `{analysis['position_size']}%` من المحفظة"
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
    logger.info("🚀 بدء الماسح الاحترافي v5.0 ...")
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
                    valid_trending = []
                    for sym in trending[:10]:
                        test_data = fetch_klines(sym, '5m', 5)
                        if test_data:
                            valid_trending.append(sym)
                    if valid_trending:
                        dynamic_watch_list = valid_trending
                        logger.info(f"🔥 {len(dynamic_watch_list)} عملة ساخنة مدعومة")
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
        await update.message.reply_text("ℹ️ أنت مشترك بالفعل.")
        return
    if user_id in PENDING:
        await update.message.reply_text("⏳ طلبك قيد الانتظار.")
        return
    PENDING.append(user_id)
    save_pending(PENDING)
    await update.message.reply_text("✅ تم استلام طلب الاشتراك.")
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

async def add_user_manually(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ هذا الأمر متاح فقط للمالك.")
        return
    if not context.args:
        await update.message.reply_text("⚠️ استخدم: /adduser USER_ID")
        return
    user_id = context.args[0].strip()
    if not user_id.isdigit():
        await update.message.reply_text("❌ المعرف يجب أن يكون أرقاماً فقط.")
        return
    if user_id in SUBSCRIBERS:
        await update.message.reply_text(f"ℹ️ المستخدم `{user_id}` مشترك بالفعل.", parse_mode="Markdown")
        return
    SUBSCRIBERS.append(user_id)
    save_subscribers(SUBSCRIBERS)
    try:
        await context.bot.send_message(chat_id=user_id, text="🎉 *تمت إضافتك إلى البوت الاحترافي v5.0!*", parse_mode="Markdown")
        await update.message.reply_text(f"✅ تمت إضافة المستخدم `{user_id}` بنجاح.")
    except Exception as e:
        await update.message.reply_text(f"✅ تمت إضافة المستخدم `{user_id}` ولكن لم نتمكن من إرسال رسالة ترحيب.")
    logger.info(f"➕ المالك أضاف مستخدم: {user_id}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_syms = list(set(BASE_WATCH_LIST + dynamic_watch_list))
    await update.message.reply_text(
        f"📊 *حالة البوت v5.0*\n"
        f"📌 العملات: {len(all_syms)}\n"
        f"👥 المشتركين: {len(SUBSCRIBERS)}\n"
        f"⏳ في الانتظار: {len(PENDING)}\n"
        f"💧 الحد الأدنى للسيولة: $200K\n"
        f"📊 نظام التقييم: RSI + الحجم + البولينجر + السيولة\n"
        f"🛡️ إدارة المخاطر: ديناميكية (وقف خسارة ≥1%)",
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
        f"🔹 النقاط: {analysis['score']}/10\n"
        f"🔹 الإشارة: {analysis['signal']}\n"
        f"💰 السعر: `{analysis['price']}`\n"
        f"📊 RSI: `{analysis['rsi']}`\n"
        f"📈 MACD: `{analysis['macd']['histogram']}`\n"
        f"📊 بولينجر: الأعلى `{analysis['bb']['upper']}` | الأدنى `{analysis['bb']['lower']}`\n"
        f"📈 تغير ساعة: `{analysis['change_1h']}%`\n"
        f"📊 الحجم النسبي: `{analysis['volume_ratio']}x`\n"
        f"💧 السيولة 24h: `{volume_str}`\n"
        f"📝 الأسباب: {', '.join(analysis['reasons'])}\n\n"
        f"🛡️ وقف الخسارة: `{analysis['stop_loss']}`\n"
        f"🎯 جني الأرباح: `{analysis['take_profit']}`\n"
        f"📊 حجم الصفقة: `{analysis['position_size']}%` من المحفظة"
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
