import os
import time
import logging
import threading
import asyncio
import json
import math
import aiohttp
import aiosqlite
from datetime import datetime, timedelta
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# -------------------- الإعدادات الأساسية --------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask('')

@app.route('/')
def home():
    return "✅ Elite Pro Bot v7.8 - High Quality Signals"

# -------------------- المتغيرات البيئية --------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.environ.get("CHAT_ID")

# -------------------- إعدادات القوة العالية --------------------
DB_PATH = "crypto_bot.db"
RATE_LIMIT_DELAY = 0.1
SEMAPHORE_LIMIT = 5
COOLDOWN_MINUTES = 30                      # ↑ زيادة من 20
MIN_VOLUME_USD = 300_000                   # ↑ زيادة من 200K
SIGNAL_SCORE_THRESHOLD = 5.5               # ↑ زيادة من 4.5
RISK_PER_TRADE = 0.01
MAX_POSITION_SIZE_PCT = 2.0

# -------------------- دوال قاعدة البيانات غير المتزامنة --------------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('PRAGMA journal_mode=WAL')
        await db.execute('''CREATE TABLE IF NOT EXISTS subscribers (user_id TEXT PRIMARY KEY)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS pending (user_id TEXT PRIMARY KEY)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS signal_cooldown (symbol TEXT PRIMARY KEY, last_signal_time TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS signals_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            timestamp TEXT,
            signal_type TEXT,
            price REAL,
            stop_loss REAL,
            take_profit REAL,
            result TEXT,
            profit_loss REAL
        )''')
        if ADMIN_CHAT_ID:
            await db.execute("INSERT OR IGNORE INTO subscribers (user_id) VALUES (?)", (ADMIN_CHAT_ID,))
        await db.commit()
    logger.info("✅ قاعدة البيانات مهيأة (WAL mode)")

async def get_subscribers():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM subscribers") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def add_subscriber(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO subscribers (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def remove_subscriber(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM subscribers WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_pending():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM pending") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def add_pending(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO pending (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def remove_pending(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pending WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_cooldown(symbol):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_signal_time FROM signal_cooldown WHERE symbol = ?", (symbol,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def set_cooldown(symbol, timestamp):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO signal_cooldown (symbol, last_signal_time) VALUES (?, ?)", (symbol, timestamp))
        await db.commit()

async def save_signal_history(symbol, signal_type, price, stop_loss, take_profit):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO signals_history (symbol, timestamp, signal_type, price, stop_loss, take_profit) VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, datetime.now().isoformat(), signal_type, price, stop_loss, take_profit)
        )
        await db.commit()

# -------------------- دوال جلب البيانات (Binance أولاً) --------------------
async def fetch_binance_klines(session, symbol, interval='5m', limit=50):
    try:
        url = f"https://api.binance.us/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data and len(data) > 0:
                    return {
                        "prices": [float(c[4]) for c in data],
                        "highs": [float(c[2]) for c in data],
                        "lows": [float(c[3]) for c in data],
                        "volumes": [float(c[5]) for c in data],
                        "opens": [float(c[1]) for c in data]
                    }
    except Exception as e:
        logger.debug(f"Binance error: {e}")
    return None

async def fetch_coinbase_klines(session, symbol, interval='5m', limit=50):
    try:
        symbol_cb = symbol.replace('USDT', '-USD')
        url = f"https://api.exchange.coinbase.com/products/{symbol_cb}/candles"
        granularity = 300 if interval == '5m' else 900
        params = {'granularity': granularity, 'limit': limit}
        async with session.get(url, params=params, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
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

async def fetch_klines(session, symbol, interval='5m', limit=50, retries=3):
    await asyncio.sleep(RATE_LIMIT_DELAY)
    for attempt in range(retries):
        data = await fetch_binance_klines(session, symbol, interval, limit)
        if data and len(data['prices']) > 10:
            return data
        data = await fetch_coinbase_klines(session, symbol, interval, limit)
        if data and len(data['prices']) > 10:
            return data
        if attempt < retries - 1:
            wait_time = 2 ** attempt
            await asyncio.sleep(wait_time)
    return None

async def fetch_binance_24hr_stats(session, symbol):
    try:
        url = f"https://api.binance.us/api/v3/ticker/24hr?symbol={symbol}"
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "volume": float(data.get('quoteVolume', 0)),
                    "change_24h": float(data.get('priceChangePercent', 0)),
                    "high": float(data.get('highPrice', 0)),
                    "low": float(data.get('lowPrice', 0)),
                    "open": float(data.get('openPrice', 0)),
                    "last": float(data.get('lastPrice', 0))
                }
    except Exception as e:
        logger.debug(f"Binance stats error: {e}")
    return None

async def fetch_coinbase_24hr_stats(session, symbol):
    try:
        symbol_cb = symbol.replace('USDT', '-USD')
        url = f"https://api.exchange.coinbase.com/products/{symbol_cb}/stats"
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                last = float(data.get('last', 0))
                open_price = float(data.get('open', 0))
                change_24h = ((last - open_price) / open_price * 100) if open_price != 0 else 0
                volume = float(data.get('volume', 0)) * last
                return {
                    "volume": volume,
                    "change_24h": change_24h,
                    "high": float(data.get('high', 0)),
                    "low": float(data.get('low', 0)),
                    "open": open_price,
                    "last": last
                }
    except Exception as e:
        logger.debug(f"Coinbase stats error: {e}")
    return None

async def fetch_24hr_stats(session, symbol):
    stats = await fetch_binance_24hr_stats(session, symbol)
    if stats and stats.get('volume', 0) > 1000:
        return stats
    stats = await fetch_coinbase_24hr_stats(session, symbol)
    if stats and stats.get('volume', 0) > 1000:
        return stats
    return {"volume": 0, "change_24h": 0, "high": 0, "low": 0, "open": 0, "last": 0}

# -------------------- المؤشرات الفنية المحسنة --------------------
def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return max(0, min(100, rsi))

def calculate_sma(prices, period=20):
    if len(prices) < period:
        return prices[-1] if prices else 0
    return sum(prices[-period:]) / period

def calculate_ema_series(prices, period):
    if len(prices) < period:
        return [prices[-1]] * len(prices) if prices else []
    multiplier = 2 / (period + 1)
    ema_series = [prices[0]]
    for price in prices[1:]:
        ema = (price - ema_series[-1]) * multiplier + ema_series[-1]
        ema_series.append(ema)
    return ema_series

def calculate_macd(prices, short=12, long=26, signal=9):
    if len(prices) < long:
        return {"histogram": 0}
    ema_short = calculate_ema_series(prices, short)
    ema_long = calculate_ema_series(prices, long)
    macd_line = [s - l for s, l in zip(ema_short, ema_long)]
    signal_line = calculate_ema_series(macd_line, signal)
    histogram = macd_line[-1] - signal_line[-1]
    return {"histogram": histogram}

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

# -------------------- منطق تحديد الإشارة المعدل (قوة أعلى) --------------------
def determine_signal_type(rsi, change_1h, score):
    if rsi > 75:
        return "🔴 **بيع / جني أرباح** (تشبع شرائي مفرط)"
    elif rsi > 70 and change_1h > 0:
        return "🔴 **بيع / جني أرباح** (اقتراب من القمة)"
    elif rsi < 25:
        return "🟢 **شراء قوي** (تشبع بيعي مفرط - فرصة ارتداد)"
    elif rsi < 30 and change_1h < 0:
        return "🟢 **شراء** (منطقة تشبع بيعي)"
    # نطاق RSI الآمن مضيق (45-55 بدلاً من 40-60)
    elif 45 <= rsi <= 55:
        if score >= 7.0 and change_1h > 0:  # رفع العتبة من 6.0 إلى 7.0
            return "🟢 **شراء قوي** (زخم إيجابي في نطاق محايد)"
        elif score >= 7.0 and change_1h < 0:
            return "🔴 **بيع** (زخم سلبي في نطاق محايد)"
        else:
            return "🟡 **مراقبة** (زخم متوازن)"
    elif 55 < rsi <= 65:
        return "🟡 **مراقبة** (زخم مرتفع مع الحذر)"
    else:
        return "⚪ **حيادي** (لا توجد إشارة واضحة)"

def evaluate_signal(rsi, volume_ratio, liquidity_usd, price_near_upper_bollinger, change_1h, price_above_ema, trend_1h=None, trend_4h=None):
    if rsi > 75:
        return {
            "status": "مرفوض",
            "score": 0.0,
            "signal": "🔴 تجنب الدخول (تشبع شرائي)",
            "reasons": ["RSI مرتفع جداً (> 75)"]
        }

    score = 0.0
    reasons = []

    # 1. RSI - نقاط أكثر صرامة
    if 45 <= rsi <= 55:
        score += 3.5  # زيادة من 3.0
        reasons.append("زخم RSI في النطاق الآمن المثالي")
    elif 40 <= rsi < 45:
        score += 2.5
        reasons.append("RSI منخفض - فرصة شراء محتملة")
    elif 55 < rsi <= 65:
        score += 1.5
        reasons.append("RSI مرتفع - حذر")
    elif 25 <= rsi < 40:
        score += 2.0
        reasons.append("منطقة تشبع بيعي (فرصة)")
    else:
        score += 0.5
        reasons.append("زخم ضعيف")

    # 2. الحجم - عتبات أعلى
    if volume_ratio >= 2.5:
        score += 3.0
        reasons.append(f"🚀 انفجار حجم كبير ({volume_ratio:.1f}x)")
    elif volume_ratio >= 1.8:
        score += 2.0
        reasons.append(f"نشاط حجم قوي ({volume_ratio:.1f}x)")
    elif volume_ratio >= 1.3:
        score += 1.0
        reasons.append(f"نشاط حجم معتدل ({volume_ratio:.1f}x)")
    else:
        score += 0.3
        reasons.append("حجم ضعيف")

    # 3. السيولة - عتبات أعلى
    if liquidity_usd > 2_000_000:
        score += 2.0
        reasons.append("سيولة عالية جداً (> $2M)")
    elif liquidity_usd > 1_000_000:
        score += 1.5
        reasons.append("سيولة عالية (> $1M)")
    elif liquidity_usd > 500_000:
        score += 0.5
        reasons.append("سيولة جيدة (> $500K)")
    else:
        score += 0.2
        reasons.append("سيولة منخفضة")

    # 4. البولينجر
    if not price_near_upper_bollinger:
        score += 2.0
        reasons.append("مساحة للصعود (بعيد عن الحد العلوي)")
    else:
        score += 0.5
        reasons.append("السعر قريب من الحد العلوي")

    # 5. الزخم السعري
    if change_1h > 1.5:
        score += 1.0
        reasons.append(f"زخم سعري قوي ({change_1h:.1f}%)")
    elif change_1h > 0.5:
        score += 0.5
        reasons.append(f"زخم سعري معتدل ({change_1h:.1f}%)")
    elif change_1h < -1.5:
        reasons.append(f"انهيار سعري ({change_1h:.1f}%)")

    # 6. الاتجاه (EMA)
    if price_above_ema:
        score += 1.0
        reasons.append("السعر فوق EMA12 (اتجاه صاعد)")
    else:
        reasons.append("السعر تحت EMA12 (اتجاه هابط محتمل)")

    # 7. اتجاهات 1h و 4h
    if trend_1h:
        score += 0.5
        reasons.append("اتجاه 1h صاعد")
    if trend_4h:
        score += 0.5
        reasons.append("اتجاه 4h صاعد")

    final_score = round(score, 1)
    signal_label = determine_signal_type(rsi, change_1h, final_score)

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
    "ALGOUSDT", "ARBUSDT", "APTUSDT", "CAKEUSDT", "COMPUSDT", "CROUSDT",
    "EGLDUSDT", "ENJUSDT", "FLOWUSDT", "GALAUSDT", "GRTUSDT", "HBARUSDT",
    "IMXUSDT", "INJUSDT", "KAVAUSDT", "KSMUSDT", "LDOUSDT", "MASKUSDT",
    "NEOUSDT", "QNTUSDT", "RENUSDT", "ROSEUSDT", "RVNUSDT",
    "SUSHIUSDT", "UMAUSDT", "ZECUSDT"
]
dynamic_watch_list = []

# -------------------- التحليل المتقدم --------------------
async def advanced_analysis(session, symbol):
    data_5m = await fetch_klines(session, symbol, '5m', 100)
    data_1h = await fetch_klines(session, symbol, '1h', 30)
    data_4h = await fetch_klines(session, symbol, '4h', 20)
    
    if not data_5m or not data_1h or not data_4h:
        return None
    
    prices_5m = data_5m['prices']
    highs_5m = data_5m['highs']
    lows_5m = data_5m['lows']
    volumes_5m = data_5m['volumes']
    
    trend_1h = data_1h['prices'][-1] > calculate_sma(data_1h['prices'], 20) if len(data_1h['prices']) >= 20 else False
    trend_4h = data_4h['prices'][-1] > calculate_sma(data_4h['prices'], 20) if len(data_4h['prices']) >= 20 else False
    
    stats = await fetch_24hr_stats(session, symbol)
    if stats.get('volume', 0) < MIN_VOLUME_USD:
        return None
    
    current_price = prices_5m[-1]
    rsi = calculate_rsi(prices_5m, 14)
    if rsi >= 99 or rsi <= 1:
        return None
    
    ema12 = calculate_ema_series(prices_5m, 12)[-1]
    macd = calculate_macd(prices_5m)
    bb = calculate_bollinger(prices_5m, 20, 2)
    atr = calculate_atr(highs_5m, lows_5m, prices_5m, 14)
    
    if len(prices_5m) >= 6 and prices_5m[-6] > 0:
        change_1h = ((prices_5m[-1] - prices_5m[-6]) / prices_5m[-6]) * 100
    else:
        change_1h = 0.0
    
    if abs(change_1h) < 0.2 and not (rsi < 30 or rsi > 70):
        return None
    
    avg_volume_12 = sum(volumes_5m[-12:]) / 12 if len(volumes_5m) >= 12 else 1
    current_volume = volumes_5m[-1] if volumes_5m else 0
    volume_ratio = current_volume / avg_volume_12 if avg_volume_12 > 0 else 0
    
    price_near_upper = current_price > bb['upper'] * 0.98 if bb['upper'] > 0 else False
    price_above_ema = current_price > ema12
    liquidity_usd = stats.get('volume', 0)
    
    eval_result = evaluate_signal(rsi, volume_ratio, liquidity_usd, price_near_upper, change_1h, price_above_ema, trend_1h, trend_4h)
    
    if eval_result['status'] == 'مرفوض' or eval_result['score'] < SIGNAL_SCORE_THRESHOLD:
        return None
    
    min_stop_pct = 0.01
    atr_stop = atr * 2 if atr > 0 else current_price * 0.015
    stop_loss = current_price - max(atr_stop, current_price * min_stop_pct)
    take_profit = current_price + max(atr_stop * 2, current_price * 0.02)
    
    price_precision = 8 if symbol in ["PEPEUSDT", "SHIBUSDT", "BONKUSDT"] else 6
    stop_loss = round(stop_loss, price_precision)
    take_profit = round(take_profit, price_precision)
    
    position_size = calculate_position_size(current_price, stop_loss)
    
    await save_signal_history(symbol, eval_result['signal'], current_price, stop_loss, take_profit)
    
    return {
        "symbol": symbol,
        "price": round(current_price, price_precision),
        "rsi": round(rsi, 1),
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

def calculate_position_size(current_price, stop_loss):
    if current_price <= 0 or stop_loss <= 0 or current_price == stop_loss:
        return 0.5
    stop_loss_pct = abs(current_price - stop_loss) / current_price
    if stop_loss_pct == 0:
        return 0.5
    position_size = (RISK_PER_TRADE / stop_loss_pct) * 100
    return round(min(MAX_POSITION_SIZE_PCT, max(0.1, position_size)), 2)

# -------------------- دوال الإرسال غير المتزامنة --------------------
async def send_message_async(session, chat_id, message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for attempt in range(3):
        try:
            async with session.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=5) as resp:
                if resp.status == 200:
                    return
                elif resp.status == 429:
                    data = await resp.json()
                    retry_after = data.get('retry_after', 5)
                    await asyncio.sleep(retry_after)
                else:
                    logger.warning(f"Telegram error {resp.status} for {chat_id}")
        except Exception as e:
            logger.error(f"Error sending to {chat_id}: {e}")
            await asyncio.sleep(2 ** attempt)

async def send_to_all_subscribers_async(session, message):
    subscribers = await get_subscribers()
    if not subscribers:
        return
    tasks = [send_message_async(session, chat_id, message) for chat_id in subscribers]
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info(f"✅ تم إرسال الرسالة لـ {len(subscribers)} مشترك")

# -------------------- أوامر التليجرام --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    subscribers = await get_subscribers()
    pending = await get_pending()
    if user_id in subscribers:
        await update.message.reply_text("ℹ️ أنت مشترك بالفعل.")
        return
    if user_id in pending:
        await update.message.reply_text("⏳ طلبك قيد الانتظار.")
        return
    await add_pending(user_id)
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
    pending = await get_pending()
    if user_id in pending:
        await remove_pending(user_id)
        await add_subscriber(user_id)
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
    subscribers = await get_subscribers()
    if user_id in subscribers:
        await update.message.reply_text(f"ℹ️ المستخدم `{user_id}` مشترك بالفعل.", parse_mode="Markdown")
        return
    await add_subscriber(user_id)
    try:
        await context.bot.send_message(chat_id=user_id, text="🎉 *تمت إضافتك إلى البوت الاحترافي v7.8!*", parse_mode="Markdown")
        await update.message.reply_text(f"✅ تمت إضافة المستخدم `{user_id}` بنجاح.")
    except Exception as e:
        await update.message.reply_text(f"✅ تمت إضافة المستخدم `{user_id}` ولكن لم نتمكن من إرسال رسالة ترحيب.")
    logger.info(f"➕ المالك أضاف مستخدم: {user_id}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribers = await get_subscribers()
    pending = await get_pending()
    all_syms = list(set(BASE_WATCH_LIST + dynamic_watch_list))
    await update.message.reply_text(
        f"📊 *حالة البوت v7.8 - جودة عالية*\n"
        f"📌 العملات: {len(all_syms)}\n"
        f"👥 المشتركين: {len(subscribers)}\n"
        f"⏳ في الانتظار: {len(pending)}\n"
        f"💧 الحد الأدنى للسيولة: ${MIN_VOLUME_USD:,}\n"
        f"📊 نظام التقييم: RSI (45-55 مثالي) + حجم (≥2.5x للانفجار)\n"
        f"🛡️ إدارة المخاطر: ديناميكية (وقف خسارة ≥1%، حجم صفقة محسوب)\n"
        f"🔹 عتبة النقاط: {SIGNAL_SCORE_THRESHOLD}/10 (إشارات قوية فقط)",
        parse_mode="Markdown"
    )

async def signal_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ /signal SYMBOL")
        return
    sym = context.args[0].upper()
    async with aiohttp.ClientSession() as session:
        analysis = await advanced_analysis(session, sym)
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

# -------------------- حلقة المسح غير المتزامنة --------------------
async def process_single_symbol(session, symbol, semaphore, send_session):
    async with semaphore:
        cooldown_time = await get_cooldown(symbol)
        if cooldown_time:
            last_time = datetime.fromisoformat(cooldown_time)
            if (datetime.now() - last_time) < timedelta(minutes=COOLDOWN_MINUTES):
                return None
        
        analysis = await advanced_analysis(session, symbol)
        if not analysis:
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
        
        await send_to_all_subscribers_async(send_session, msg)
        await set_cooldown(symbol, datetime.now().isoformat())
        logger.info(f"✅ إشارة {symbol} أُرسلت")
        return analysis

async def market_scanner_loop():
    logger.info("🚀 بدء الماسح الاحترافي v7.8 (جودة عالية)...")
    semaphore = asyncio.Semaphore(SEMAPHORE_LIMIT)
    
    async with aiohttp.ClientSession() as session:
        async with aiohttp.ClientSession() as send_session:
            while True:
                global dynamic_watch_list
                try:
                    trending = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
                    valid_trending = []
                    for sym in trending[:5]:
                        test_data = await fetch_klines(session, sym, '5m', 5)
                        if test_data:
                            valid_trending.append(sym)
                    if valid_trending:
                        dynamic_watch_list = valid_trending
                        logger.info(f"🔥 {len(dynamic_watch_list)} عملة ساخنة مدعومة")
                except Exception as e:
                    logger.error(f"خطأ في جلب العملات الساخنة: {e}")
                
                all_symbols = list(set(BASE_WATCH_LIST + dynamic_watch_list))
                logger.info(f"🔄 فحص {len(all_symbols)} عملة ...")
                
                tasks = [
                    process_single_symbol(session, symbol, semaphore, send_session)
                    for symbol in all_symbols
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"خطأ في المعالجة: {result}")
                
                logger.info("✅ انتهت الدورة. انتظار 5 دقائق...")
                await asyncio.sleep(300)

# -------------------- تشغيل البوت --------------------
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=False)

async def main():
    await init_db()
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask Server Started")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("approve", approve))
    application.add_handler(CommandHandler("adduser", add_user_manually))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("signal", signal_now))
    
    # تشغيل الماسح في الخلفية
    asyncio.create_task(market_scanner_loop())
    logger.info("✅ Scanner started as background task")
    
    # تشغيل البوت باستخدام run_polling (الطريقة الصحيحة)
    logger.info("✅ Starting Telegram Bot...")
    await application.run_polling()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 تم إيقاف البوت يدوياً (KeyboardInterrupt)")
    except Exception as e:
        logger.error(f"⚠️ توقف غير متوقع: {e}")
