import os
import time
import logging
import threading
import asyncio
import json
import math
import sqlite3
import requests
from datetime import datetime, timedelta
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from concurrent.futures import ThreadPoolExecutor, as_completed

# -------------------- الإعدادات الأساسية --------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask('')

@app.route('/')
def home():
    return "✅ Elite Pro Bot v3.0 with Backtesting is RUNNING!"

# -------------------- المتغيرات البيئية --------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.environ.get("CHAT_ID")

# -------------------- قاعدة البيانات (SQLite) --------------------
DB_PATH = "signals.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS signals
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  symbol TEXT,
                  timestamp TEXT,
                  signal_type TEXT,
                  price REAL,
                  stop_loss REAL,
                  take_profit REAL,
                  result TEXT,
                  profit_loss REAL)''')
    conn.commit()
    conn.close()

init_db()

def save_signal(symbol, signal_type, price, stop_loss, take_profit):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO signals (symbol, timestamp, signal_type, price, stop_loss, take_profit) VALUES (?, ?, ?, ?, ?)",
              (symbol, datetime.now().isoformat(), signal_type, price, stop_loss, take_profit))
    conn.commit()
    conn.close()

def get_performance_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM signals")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM signals WHERE result='win'")
    wins = c.fetchone()[0]
    c.execute("SELECT AVG(profit_loss) FROM signals WHERE result='win'")
    avg_win = c.fetchone()[0] or 0
    c.execute("SELECT AVG(profit_loss) FROM signals WHERE result='loss'")
    avg_loss = c.fetchone()[0] or 0
    conn.close()
    return {"total": total, "wins": wins, "avg_win": avg_win, "avg_loss": avg_loss}

# -------------------- دوال جلب البيانات المحسنة --------------------
def fetch_klines(symbol, interval='5m', limit=100, retries=2):
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
                        "volumes": [float(c[5]) for c in data],
                        "opens": [float(c[1]) for c in data]
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

def fetch_news_sentiment(symbol):
    """جلب تحليل الأخبار من CryptoPanic (محاكاة)"""
    # في الإصدار الحقيقي، استخدم API حقيقي
    return {"sentiment": "neutral", "impact": 0}

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

def calculate_ichimoku(prices, highs, lows):
    """حساب Ichimoku Cloud (محاكاة مبسطة)"""
    if len(prices) < 52:
        return {"tenkan": 0, "kijun": 0, "senkou_a": 0, "senkou_b": 0}
    tenkan = (max(prices[-9:]) + min(prices[-9:])) / 2
    kijun = (max(prices[-26:]) + min(prices[-26:])) / 2
    senkou_a = (tenkan + kijun) / 2
    senkou_b = (max(prices[-52:]) + min(prices[-52:])) / 2
    return {"tenkan": tenkan, "kijun": kijun, "senkou_a": senkou_a, "senkou_b": senkou_b}

def calculate_fibonacci(high, low):
    """حساب مستويات فيبوناتشي"""
    diff = high - low
    return {
        "0": high,
        "0.236": high - diff * 0.236,
        "0.382": high - diff * 0.382,
        "0.5": high - diff * 0.5,
        "0.618": high - diff * 0.618,
        "1": low
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
SIGNAL_THRESHOLD = 2.5  # رفع العتبة قليلاً للحصول على إشارات أقوى
MIN_VOLUME_USD = 500_000
MIN_CHANGE_1H = 0.2
MAX_POSITION_SIZE = 0.02  # 2% من المحفظة
RISK_PER_TRADE = 0.01  # 1% مخاطرة لكل صفقة
last_signal_time = {}

def advanced_analysis(symbol):
    """تحليل متكامل مع مؤشرات متقدمة واختبار تاريخي"""
    data_5m = fetch_klines(symbol, '5m', 100)
    data_1h = fetch_klines(symbol, '1h', 30)
    data_4h = fetch_klines(symbol, '4h', 20)
    
    if not data_5m or not data_1h or not data_4h:
        return None
    
    prices_5m = data_5m['prices']
    highs_5m = data_5m['highs']
    lows_5m = data_5m['lows']
    volumes_5m = data_5m['volumes']
    opens_5m = data_5m['opens']
    
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
    ichimoku = calculate_ichimoku(prices_5m, highs_5m, lows_5m)
    fib = calculate_fibonacci(stats.get('high', current_price), stats.get('low', current_price))
    
    change_1h = ((prices_5m[-1] - prices_5m[-6]) / prices_5m[-6]) * 100 if len(prices_5m) >= 6 else 0
    if abs(change_1h) < MIN_CHANGE_1H and not (rsi < 30 or rsi > 70):
        return None
    
    avg_volume = stats.get('volume', 0) / 288
    current_volume = volumes_5m[-1] if volumes_5m else 0
    volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
    
    # تحليل الأخبار (محاكاة)
    news = fetch_news_sentiment(symbol)
    
    # نظام النقاط المحسن (0-10)
    score = 0
    reasons = []
    
    # 1. الزخم السعري (0-2)
    if change_1h > 2.0:
        score += 2
        reasons.append(f"زخم قوي جداً ({change_1h:.1f}%)")
    elif change_1h > 1.0:
        score += 1.5
        reasons.append(f"زخم قوي ({change_1h:.1f}%)")
    elif change_1h > 0.5:
        score += 1
        reasons.append(f"زخم معتدل ({change_1h:.1f}%)")
    elif change_1h < -2.0:
        score -= 2
        reasons.append(f"انهيار حاد ({change_1h:.1f}%)")
    
    # 2. RSI (0-2)
    if rsi < 30 and current_price > ema12:
        score += 2
        reasons.append(f"RSI مفرط بيع ({rsi:.1f})")
    elif rsi < 40 and current_price > ema12:
        score += 1
        reasons.append(f"RSI منخفض ({rsi:.1f})")
    elif rsi > 70 and current_price < ema12:
        score -= 2
        reasons.append(f"RSI مفرط شراء ({rsi:.1f})")
    elif rsi > 60 and current_price < ema12:
        score -= 1
        reasons.append(f"RSI مرتفع ({rsi:.1f})")
    
    # 3. MACD (0-2)
    if macd['histogram'] > 0 and macd['histogram'] > macd['histogram'] * 1.1:
        score += 1.5
        reasons.append("تقاطع MACD إيجابي قوي")
    elif macd['histogram'] > 0:
        score += 1
        reasons.append("تقاطع MACD إيجابي")
    elif macd['histogram'] < 0 and macd['histogram'] < macd['histogram'] * 1.1:
        score -= 1.5
        reasons.append("تقاطع MACD سلبي قوي")
    
    # 4. بولينجر باند (0-1)
    if current_price < bb['lower'] and change_1h > 0:
        score += 1
        reasons.append("اختراق دعم بولينجر")
    elif current_price > bb['upper'] and change_1h < 0:
        score -= 1
        reasons.append("اختراق مقاومة بولينجر")
    
    # 5. الحجم والانفجار (0-2)
    if volume_ratio > 3.0:
        score += 2
        reasons.append(f"🚀 انفجار حجم كبير ({volume_ratio:.1f}x)")
    elif volume_ratio > 2.0:
        score += 1.5
        reasons.append(f"انفجار حجم ({volume_ratio:.1f}x)")
    elif volume_ratio > 1.5:
        score += 1
        reasons.append(f"نشاط حجم ({volume_ratio:.1f}x)")
    
    # 6. إيشيموكو (0-1)
    if current_price > ichimoku['senkou_a'] and current_price > ichimoku['senkou_b']:
        score += 1
        reasons.append("فوق سحابة إيشيموكو")
    elif current_price < ichimoku['senkou_a'] and current_price < ichimoku['senkou_b']:
        score -= 1
        reasons.append("تحت سحابة إيشيموكو")
    
    # 7. فيبوناتشي (0-1)
    if current_price > fib['0.618'] and change_1h > 0:
        score += 1
        reasons.append("اختراق مستوى 61.8% فيبوناتشي")
    elif current_price < fib['0.382'] and change_1h < 0:
        score -= 1
        reasons.append("كسر مستوى 38.2% فيبوناتشي")
    
    # 8. تحليل الأخبار (0-0.5)
    if news['sentiment'] == 'positive':
        score += 0.5
        reasons.append("أخبار إيجابية")
    elif news['sentiment'] == 'negative':
        score -= 0.5
        reasons.append("أخبار سلبية")
    
    # إدارة المخاطر الديناميكية
    risk_amount = RISK_PER_TRADE
    atr_stop = atr * 2 if atr > 0 else current_price * 0.02
    stop_loss = current_price - atr_stop if change_1h > 0 else current_price + atr_stop
    take_profit = current_price + atr_stop * 2 if change_1h > 0 else current_price - atr_stop * 2
    position_size = min(MAX_POSITION_SIZE, risk_amount * 100 / (abs(current_price - stop_loss) / current_price * 100))
    
    # تصنيف الإشارة
    signal_type = "⏸ انتظار"
    if score >= 4 and change_1h > 0:
        signal_type = "🚀 **شراء انفجاري**"
    elif score >= 3 and change_1h > 0.5:
        signal_type = "🟢 **شراء قوي**"
    elif score >= 3 and change_1h < -0.5:
        signal_type = "🔴 **بيع / جني أرباح**"
    elif score >= 2.5:
        signal_type = "🟡 **مراقبة**"
    
    if signal_type in ["🟡 **مراقبة**", "⏸ انتظار"] or score < SIGNAL_THRESHOLD:
        return None
    
    # حفظ الإشارة في قاعدة البيانات
    save_signal(symbol, signal_type, current_price, stop_loss, take_profit)
    
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
        "ichimoku": ichimoku,
        "fib": fib,
        "change_1h": change_1h,
        "volume_ratio": volume_ratio,
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
            f"📊 *{analysis['symbol']}* | النقاط: {analysis['score']}/10\n"
            f"🔔 {analysis['signal']}\n\n"
            f"💰 السعر: `{analysis['price']:.6f}`\n"
            f"📉 RSI: `{analysis['rsi']:.1f}` | MACD: `{analysis['macd']['histogram']:.4f}`\n"
            f"📊 بولينجر: الأعلى `{analysis['bb']['upper']:.6f}` | الأدنى `{analysis['bb']['lower']:.6f}`\n"
            f"📈 إيشيموكو: السحابة العليا `{analysis['ichimoku']['senkou_a']:.6f}` | السفلى `{analysis['ichimoku']['senkou_b']:.6f}`\n"
            f"📈 فيبوناتشي: 61.8% `{analysis['fib']['0.618']:.6f}` | 38.2% `{analysis['fib']['0.382']:.6f}`\n"
            f"📈 التغير (ساعة): `{analysis['change_1h']:.2f}%`\n"
            f"📊 الحجم النسبي: `{analysis['volume_ratio']:.1f}x`\n"
            f"💧 السيولة 24h: `{volume_str}`\n"
            f"📝 الأسباب: {', '.join(analysis['reasons'])}\n\n"
            f"🛡️ **إدارة المخاطر:**\n"
            f"• وقف الخسارة: `{analysis['stop_loss']:.6f}`\n"
            f"• جني الأرباح: `{analysis['take_profit']:.6f}`\n"
            f"• حجم الصفقة: `{analysis['position_size']:.2f}%` من المحفظة"
        )
        
        send_to_all_subscribers(msg)
        last_signal_time[symbol] = now
        logger.info(f"✅ إشارة {symbol} أُرسلت")
        return symbol
    except Exception as e:
        logger.error(f"خطأ {symbol}: {e}")
    return None

# -------------------- اختبار تاريخي (Backtesting) --------------------
def backtest_strategy(symbol, days=30):
    """اختبار الاستراتيجية على بيانات تاريخية"""
    logger.info(f"🔍 بدء الاختبار التاريخي لـ {symbol} لآخر {days} يوم")
    
    # جلب بيانات تاريخية (شموع ساعة)
    data = fetch_klines(symbol, '1h', days * 24)
    if not data or len(data['prices']) < 50:
        return None
    
    prices = data['prices']
    highs = data['highs']
    lows = data['lows']
    volumes = data['volumes']
    
    # محاكاة الصفقات
    trades = []
    in_position = False
    entry_price = 0
    stop_loss = 0
    take_profit = 0
    
    for i in range(50, len(prices)):
        # تحليل كل نقطة كما لو كانت لحظية
        current_price = prices[i]
        # محاكاة مبسطة للإشارات (نستخدم نفس المنطق ولكن بشكل مبسط)
        rsi = calculate_rsi(prices[:i+1], 14)
        ema12 = calculate_ema(prices[:i+1], 12)
        bb = calculate_bollinger(prices[:i+1], 20, 2)
        atr = calculate_atr(highs[:i+1], lows[:i+1], prices[:i+1], 14)
        
        score = 0
        if rsi < 30 and current_price > ema12:
            score += 1
        if current_price < bb['lower']:
            score += 1
        if len(prices[:i+1]) > 5:
            change = ((prices[i] - prices[i-5]) / prices[i-5]) * 100
            if change > 1.0:
                score += 1
        
        # إشارة شراء
        if score >= 2 and not in_position:
            in_position = True
            entry_price = current_price
            stop_loss = current_price - atr * 2 if atr > 0 else current_price * 0.98
            take_profit = current_price + atr * 3 if atr > 0 else current_price * 1.04
        
        # إشارة بيع أو خروج
        elif in_position:
            if current_price <= stop_loss:
                trades.append({"entry": entry_price, "exit": current_price, "profit": (current_price - entry_price) / entry_price * 100, "result": "loss"})
                in_position = False
            elif current_price >= take_profit:
                trades.append({"entry": entry_price, "exit": current_price, "profit": (current_price - entry_price) / entry_price * 100, "result": "win"})
                in_position = False
    
    # حساب الإحصائيات
    if not trades:
        return {"total_trades": 0, "win_rate": 0, "avg_profit": 0, "total_profit": 0}
    
    wins = [t for t in trades if t['result'] == 'win']
    losses = [t for t in trades if t['result'] == 'loss']
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_profit = sum(t['profit'] for t in trades) / len(trades) if trades else 0
    total_profit = sum(t['profit'] for t in trades)
    
    return {
        "total_trades": len(trades),
        "win_rate": win_rate,
        "avg_profit": avg_profit,
        "total_profit": total_profit,
        "wins": len(wins),
        "losses": len(losses)
    }

# -------------------- أوامر التليجرام --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id in SUBSCRIBERS:
        await update.message.reply_text("ℹ️ أنت مشترك بالفعل. ستصل إليك الإشارات مع تحليل متكامل.")
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
                await context.bot.send_message(chat_id=user_id, text="🎉 تمت الموافقة على اشتراكك! ستصل إليك الإشارات مع تحليل متكامل.")
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
            text="🎉 *تمت إضافتك إلى بوت الإشارات الاحترافي v3.0!*\n\nستصلك الإشارات مع تحليل متكامل وإدارة مخاطر.",
            parse_mode="Markdown"
        )
        await update.message.reply_text(f"✅ تمت إضافة المستخدم `{user_id}` بنجاح.")
    except Exception as e:
        await update.message.reply_text(f"✅ تمت إضافة المستخدم `{user_id}` ولكن لم نتمكن من إرسال رسالة ترحيب له.")
        logger.warning(f"لم نتمكن من إرسال رسالة للمستخدم {user_id}: {e}")
    logger.info(f"➕ المالك أضاف مستخدم يدوياً: {user_id}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_syms = list(set(BASE_WATCH_LIST + dynamic_watch_list))
    stats = get_performance_stats()
    await update.message.reply_text(
        f"📊 *حالة البوت v3.0*\n"
        f"📌 العملات: {len(all_syms)}\n"
        f"👥 المشتركين: {len(SUBSCRIBERS)}\n"
        f"⏳ في الانتظار: {len(PENDING)}\n"
        f"💧 الحد الأدنى للسيولة: $500K\n"
        f"📊 المؤشرات: RSI, MACD, بولينجر, إيشيموكو, فيبوناتشي\n"
        f"🛡️ إدارة المخاطر: ديناميكية (ATR + نسبة مخاطرة ثابتة)\n"
        f"📈 أداء الإشارات السابقة:\n"
        f"• إجمالي الصفقات: {stats['total']}\n"
        f"• الصفقات الرابحة: {stats['wins']}\n"
        f"• متوسط الربح: {stats['avg_win']:.2f}%\n"
        f"• متوسط الخسارة: {stats['avg_loss']:.2f}%",
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
        f"💰 السعر: `{analysis['price']:.6f}`\n"
        f"📊 RSI: `{analysis['rsi']:.1f}`\n"
        f"📈 MACD: `{analysis['macd']['histogram']:.4f}`\n"
        f"📊 بولينجر: الأعلى `{analysis['bb']['upper']:.6f}` | الأدنى `{analysis['bb']['lower']:.6f}`\n"
        f"📈 إيشيموكو: سحابة عليا `{analysis['ichimoku']['senkou_a']:.6f}` | سفلى `{analysis['ichimoku']['senkou_b']:.6f}`\n"
        f"📈 فيبوناتشي: 61.8% `{analysis['fib']['0.618']:.6f}` | 38.2% `{analysis['fib']['0.382']:.6f}`\n"
        f"📈 تغير ساعة: `{analysis['change_1h']:.2f}%`\n"
        f"📊 الحجم النسبي: `{analysis['volume_ratio']:.1f}x`\n"
        f"💧 السيولة 24h: `{volume_str}`\n"
        f"📝 الأسباب: {', '.join(analysis['reasons'])}\n\n"
        f"🛡️ وقف الخسارة: `{analysis['stop_loss']:.6f}`\n"
        f"🎯 جني الأرباح: `{analysis['take_profit']:.6f}`\n"
        f"📊 حجم الصفقة: `{analysis['position_size']:.2f}%` من المحفظة"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def backtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختبار تاريخي للاستراتيجية على عملة معينة"""
    if not context.args:
        await update.message.reply_text("⚠️ /backtest SYMBOL [days]\nمثال: /backtest BTCUSDT 30")
        return
    symbol = context.args[0].upper()
    days = 30
    if len(context.args) > 1 and context.args[1].isdigit():
        days = int(context.args[1])
    
    await update.message.reply_text(f"⏳ جاري اختبار {symbol} لآخر {days} يوم...")
    result = backtest_strategy(symbol, days)
    
    if not result:
        await update.message.reply_text(f"❌ لا توجد بيانات كافية لاختبار {symbol}")
        return
    
    msg = (
        f"📊 *نتائج الاختبار التاريخي لـ {symbol}*\n"
        f"📅 الفترة: آخر {days} يوم\n"
        f"📈 إجمالي الصفقات: {result['total_trades']}\n"
        f"✅ الصفقات الرابحة: {result['wins']}\n"
        f"❌ الصفقات الخاسرة: {result['losses']}\n"
        f"📊 نسبة الربح: {result['win_rate']:.1f}%\n"
        f"💰 متوسط الربح لكل صفقة: {result['avg_profit']:.2f}%\n"
        f"💵 إجمالي الربح: {result['total_profit']:.2f}%"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def performance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض أداء الإشارات السابقة"""
    stats = get_performance_stats()
    msg = (
        f"📈 *أداء الإشارات السابقة*\n"
        f"📊 إجمالي الصفقات: {stats['total']}\n"
        f"✅ الصفقات الرابحة: {stats['wins']}\n"
        f"💰 متوسط الربح: {stats['avg_win']:.2f}%\n"
        f"📉 متوسط الخسارة: {stats['avg_loss']:.2f}%\n"
        f"📊 نسبة الربح للخسارة: {stats['avg_win']/stats['avg_loss'] if stats['avg_loss'] != 0 else 0:.2f}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

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

def market_scanner_loop():
    logger.info("🚀 بدء الماسح الاحترافي v3.0...")
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
    application.add_handler(CommandHandler("backtest", backtest_command))
    application.add_handler(CommandHandler("performance", performance))
    
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
