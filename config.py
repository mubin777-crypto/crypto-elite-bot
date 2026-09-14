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
TELEGRAM_API_TIMEOUT = int(os.getenv("TELEGRAM_API_TIMEOUT", "45"))
TELEGRAM_LONG_POLL_TIMEOUT = int(os.getenv("TELEGRAM_LONG_POLL_TIMEOUT", "25"))
TELEGRAM_RETRY_BACKOFF_BASE = 1.0
TELEGRAM_MAX_RETRIES = 3

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

# ============================================================
# Excluded Symbols (stablecoins + political + leveraged)
# ============================================================
EXCLUDED_SYMBOLS = [
    # عملات مستقرة
    "USDCUSDT", "BUSDUSDT", "TUSDUSDT", "DAIUSDT", "USDPUSDT",
    "FDUSDUSDT", "PYUSDUSDT", "USDDUSDT", "RLUSDUSDT", "USTCUSDT",
    "EURUSDT", "TRYUSDT", "BRLUSDT", "ARSUSDT", "BIDRUSDT",
    "AEURUSDT", "EURIUSDT", "USD1USDT",
    # عملات سياسية / meme
    "TRUMPUSDT", "WLFIUSDT", "MELANIAUSDT", "BIDENUSDT",
]

EXCLUDED_SUFFIXES = [
    "BUSD",
    "UPUSDT",
    "DOWNUSDT",
    "BULLUSDT",
    "BEARUSDT",
]

EXCLUDE_TOKENIZED_STOCKS = True

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
# Signal scoring
# ============================================================
MIN_SCORE = float(os.getenv("MIN_SCORE", "7.5"))
EARLY_SNIPE_SCORE = float(os.getenv("EARLY_SNIPE_SCORE", "10.0"))  # ✅ رُفع إلى 10.0 (انفجارات 4/4 فقط)
MIN_ADX = float(os.getenv("MIN_ADX", "20.0"))
MIN_FACTORS_ALIGNED = 5

# ============================================================
# ✅ تجاوز فلتر RSI للانفجارات
# ============================================================
EXPLOSION_RSI_OVERRIDE = True  # ✅ السماح للانفجارات بتجاوز فلتر RSI

# ============================================================
# RSI Filters
# ============================================================
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
RSI_BUY_ZONE_MIN = 40.0
RSI_BUY_ZONE_MAX = 60.0
RSI_SELL_ZONE_MIN = 40.0
RSI_SELL_ZONE_MAX = 60.0
ENABLE_RSI_FILTER = True

# ============================================================
# Explosion Detection
# ============================================================
SQUEEZE_BB_WIDTH = 0.015
SQUEEZE_MIN_CANDLES = 5
SQUEEZE_WIDTH_TREND_CANDLES = 3
KELTNER_PERIOD = 20
KELTNER_ATR_MULT = 1.5

SILENT_VOLUME_MULTIPLIER = 1.5
VOLUME_TREND_MULTIPLIER = 1.2
VOLUME_MIN_CONSECUTIVE = 3

RESISTANCE_DISTANCE = 0.008
CONSOLIDATION_RANGE_MAX = 0.025
HIGHER_LOWS_COUNT = 3
BREAKOUT_CONFIRMATION_PCT = 0.003

MOMENTUM_MIN = 0.3
MOMENTUM_MAX = 5.0

# ============================================================
# Quality Gate
# ============================================================
MIN_QUALITY_PERCENT = 75.0
REQUIRE_TREND_ALIGNMENT = True
REQUIRE_VOLUME_CONFIRMATION = True
REQUIRE_MOMENTUM_ALIGNMENT = True

# ============================================================
# Risk
# ============================================================
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "10000"))
RISK_PER_TRADE = 0.01
MAX_POSITION_PERCENT = 0.50
ATR_SL_MULTIPLIER = 1.5
SL_BUFFER_PERCENT = 0.003
MIN_RR = 2.0
COOLDOWN_MINUTES = 60
OPPOSITE_COOLDOWN_HOURS = 6
DAILY_MAX_LOSS_PERCENT = 0.03

SIGNAL_MAX_HOLD_CANDLES = 6
SIGNAL_EVALUATION_INTERVAL = 60

# ============================================================
# Scanner
# ============================================================
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "45"))
PREWATCH_SCAN_EVERY = 3
MAX_PREWATCH_TO_SCAN = 20
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
    if TELEGRAM_API_TIMEOUT <= TELEGRAM_LONG_POLL_TIMEOUT:
        errors.append(
            f"TELEGRAM_API_TIMEOUT ({TELEGRAM_API_TIMEOUT}) must be > "
            f"TELEGRAM_LONG_POLL_TIMEOUT ({TELEGRAM_LONG_POLL_TIMEOUT})"
        )
    if errors:
        raise ValueError(" | ".join(errors))
