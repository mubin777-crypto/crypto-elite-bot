# config.py
# Quant Crypto Signal System v2026 - Strong Signals Edition

import os
from pathlib import Path

# ============================================================
# Base
# ============================================================
BASE_DIR = Path(__file__).resolve().parent

# ============================================================
# Database
# ============================================================
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "trading_bot.db"))
USE_POSTGRES = bool(DATABASE_URL) or os.getenv("USE_POSTGRES", "false").lower() == "true"

# ============================================================
# Render
# ============================================================
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", os.getenv("RENDER_EXTERNAL_URL", "")).rstrip("/")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
WEBHOOK_PATH = "/webhook"
HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "600"))
SELF_PING_INTERVAL = int(os.getenv("SELF_PING_INTERVAL", "300"))

# ============================================================
# Telegram
# ============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_ADMIN_ID = int(os.getenv("TELEGRAM_ADMIN_ID", "0"))
TELEGRAM_USE_WEBHOOK = os.getenv("TELEGRAM_USE_WEBHOOK", "false").lower() == "true"
TELEGRAM_API_TIMEOUT = int(os.getenv("TELEGRAM_API_TIMEOUT", "15"))
TELEGRAM_RETRY_BACKOFF_BASE = 1.0
TELEGRAM_MAX_RETRIES = 5

# ============================================================
# Binance
# ============================================================
BINANCE_ENDPOINTS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api.binance.us",
]

BINANCE_WS_ENDPOINTS = [
    "wss://data-stream.binance.vision/ws",
    "wss://stream.binance.com:9443/ws",
    "wss://stream.binance.com/ws",
]

BINANCE_TIMEOUT = 5
BINANCE_RETRIES = 2
MAX_CONCURRENT_REQUESTS = 10
REQUEST_DELAY = 0.05

# ============================================================
# Trading Fees & Slippage
# ============================================================
TRADING_FEE_PERCENT = 0.00075
SLIPPAGE_PERCENT = 0.0002

# ============================================================
# Core Universe
# ============================================================
CORE_UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "MATICUSDT", "UNIUSDT",
    "ATOMUSDT", "LTCUSDT", "BCHUSDT", "NEARUSDT", "FILUSDT", "ETCUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT", "TIAUSDT",
    "SEIUSDT", "RNDRUSDT", "FETUSDT", "STXUSDT", "TRXUSDT", "AAVEUSDT",
    "MKRUSDT", "GRTUSDT", "ALGOUSDT", "FTMUSDT", "SANDUSDT", "MANAUSDT",
    "AXSUSDT", "THETAUSDT", "EGLDUSDT", "ENJUSDT", "FLOWUSDT", "GALAUSDT",
]

EXCLUDED_SYMBOLS = ["USDCUSDT", "BUSDUSDT", "TUSDUSDT", "DAIUSDT", "USDPUSDT"]

# ============================================================
# Pre-watch
# ============================================================
PREWATCH_MIN_VOLUME_USDT = 5_000_000
PREWATCH_MIN_TRADES = 5000
PREWATCH_MAX_PRICE = 1000.0
PREWATCH_MIN_PRICE = 0.00001
PREWATCH_PRICE_CHANGE = 3.0
PREWATCH_VOLUME_USDT = 2_000_000

# ============================================================
# Timeframes
# ============================================================
ANALYSIS_INTERVAL = "5m"
TREND_INTERVAL = "15m"
DAILY_INTERVAL = "1d"
KLINE_LIMIT = 250
BACKTEST_LIMIT = 5000

# ============================================================
# Indicators
# ============================================================
RSI_PERIOD = 6
ADX_PERIOD = 14
ATR_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BB_PERIOD = 20
BB_STD = 2.0
MOMENTUM_PERIOD = 5
VOLUME_AVG_PERIOD = 20

# ============================================================
# 🔥 Signal scoring - عتبات مشددة لإشارات قوية فقط
# ============================================================
MIN_SCORE = float(os.getenv("MIN_SCORE", "7.5"))        # ✅ رُفع من 6.0 إلى 7.5
EARLY_SNIPE_SCORE = float(os.getenv("EARLY_SNIPE_SCORE", "7.0"))  # ✅ رُفع من 5.0 إلى 7.0
MIN_ADX = float(os.getenv("MIN_ADX", "20.0"))           # ✅ رُفع من 12 إلى 20 (اتجاه قوي)
MIN_FACTORS_ALIGNED = 5                                 # ✅ 5 من 7 عوامل على الأقل

# ============================================================
# RSI Filters - نطاقات مشددة
# ============================================================
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
RSI_BUY_ZONE_MIN = 40.0    # ✅ نطاق الشراء الأمثل
RSI_BUY_ZONE_MAX = 60.0
RSI_SELL_ZONE_MIN = 40.0
RSI_SELL_ZONE_MAX = 60.0
ENABLE_RSI_FILTER = True

# ============================================================
# 🔥 Explosion Detection (كشف الانفجارات)
# ============================================================
# شروط الانضغاط
SQUEEZE_BB_WIDTH = 0.015              # ✅ أكثر تشدداً (1.5% بدلاً من 2%)
SQUEEZE_MIN_CANDLES = 5               # ✅ الانضغاط يجب أن يستمر 5 شموع على الأقل
SQUEEZE_WIDTH_TREND_CANDLES = 3       # ✅ عرض البولينجر يجب أن يكون في تضييق
KELTNER_PERIOD = 20                   # ✅ TTM Squeeze - Keltner
KELTNER_ATR_MULT = 1.5

# شروط الحجم
SILENT_VOLUME_MULTIPLIER = 1.5        # ✅ الحجم الحالي مقارنة بالمتوسط
VOLUME_TREND_MULTIPLIER = 1.2         # ✅ متوسط 3 شموع أعلى من 20 شمعة بـ 20%
VOLUME_MIN_CONSECUTIVE = 3            # ✅ 3 شموع متتالية بحجم مرتفع

# شروط السعر
RESISTANCE_DISTANCE = 0.008           # ✅ 0.8% من القمة (أقرب من السابق)
CONSOLIDATION_RANGE_MAX = 0.025       # ✅ نطاق التذبذب أقل من 2.5%
HIGHER_LOWS_COUNT = 3                 # ✅ 3 قيعان صاعدة
BREAKOUT_CONFIRMATION_PCT = 0.003     # ✅ اختراق بـ 0.3% على الأقل

# شروط الزخم
MOMENTUM_MIN = 0.3                    # ✅ زخم 5 شموع > 0.3%
MOMENTUM_MAX = 5.0                    # ✅ حد أقصى لمنع الشراء في القمم

# ============================================================
# 🔥 Quality Gate (بوابة الجودة النهائية)
# ============================================================
# يجب أن تجتاز الإشارة كل هذه الشروط قبل الإرسال
MIN_QUALITY_PERCENT = 75.0            # ✅ الحد الأدنى للجودة 75%
REQUIRE_TREND_ALIGNMENT = True        # ✅ الترند 15m يجب أن يكون متوافقاً
REQUIRE_VOLUME_CONFIRMATION = True    # ✅ الحجم يجب أن يؤكد
REQUIRE_MOMENTUM_ALIGNMENT = True     # ✅ الزخم يجب أن يكون متوافقاً

# ============================================================
# Risk
# ============================================================
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "10000"))
RISK_PER_TRADE = 0.01
MAX_POSITION_PERCENT = 0.50
ATR_SL_MULTIPLIER = 1.5
SL_BUFFER_PERCENT = 0.003
MIN_RR = 2.0                          # ✅ رُفع من 1.5 إلى 2.0 (عائد أعلى)
COOLDOWN_MINUTES = 60                 # ✅ رُفع من 45 دقيقة (إشارات أقوى)
OPPOSITE_COOLDOWN_HOURS = 6           # ✅ رُفع من 4 ساعات
DAILY_MAX_LOSS_PERCENT = 0.03

SIGNAL_MAX_HOLD_CANDLES = 6           # ✅ رُفع من 3 إلى 6 (30 دقيقة)
SIGNAL_EVALUATION_INTERVAL = 60

# ============================================================
# Scanner
# ============================================================
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "45"))  # ✅ رُفع من 30 لتقليل الضغط
PREWATCH_SCAN_EVERY = 3
MAX_PREWATCH_TO_SCAN = 20              # ✅ قللنا من 30 لتحسين الجودة
MAX_LAST_DATA_UPDATE = 100

# ============================================================
# Adaptive weights
# ============================================================
FACTORS = [
    "rsi", "adx", "momentum", "volume",
    "bollinger", "macd", "pivot",
]

# ============================================================
# Logging
# ============================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ============================================================
# Validation
# ============================================================
def validate_config():
    errors = []
    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN is required")
    if not TELEGRAM_ADMIN_ID:
        errors.append("TELEGRAM_ADMIN_ID is required")
    if TELEGRAM_USE_WEBHOOK and not WEBHOOK_URL:
        errors.append("WEBHOOK_URL is required in webhook mode")
    if not 0 < RISK_PER_TRADE <= 0.05:
        errors.append("RISK_PER_TRADE must be between 0 and 0.05")
    if not 0 < MAX_POSITION_PERCENT <= 1:
        errors.append("MAX_POSITION_PERCENT must be between 0 and 1")
    if MIN_RR < 2.0:
        errors.append("MIN_RR must be at least 2.0")
    if BINANCE_TIMEOUT > 5:
        errors.append("BINANCE_TIMEOUT must not exceed 5 seconds")
    if MIN_SCORE < 7.0:
        errors.append("MIN_SCORE must be at least 7.0 for strong signals")
    if errors:
        raise ValueError(" | ".join(errors))
