# config.py
# Quant Crypto Signal System v2026

import os
from pathlib import Path

# ============================================================
# Base
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "trading_bot.db"))

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
TELEGRAM_USE_WEBHOOK = os.getenv("TELEGRAM_USE_WEBHOOK", "true").lower() == "true"
TELEGRAM_FALLBACK_POLLING = os.getenv("TELEGRAM_FALLBACK_POLLING", "false").lower() == "true"

# ============================================================
# Binance
# ============================================================
BINANCE_ENDPOINTS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api.binance.us",
    "https://data-api.binance.vision",
]
BINANCE_TIMEOUT = 5
BINANCE_RETRIES = 2
MAX_CONCURRENT_REQUESTS = 10
REQUEST_DELAY = 0.05

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
# Excluded stablecoins (added)
# ============================================================
EXCLUDED_SYMBOLS = ["USDCUSDT", "BUSDUSDT", "TUSDUSDT", "DAIUSDT", "USDPUSDT"]

# ============================================================
# Timeframes
# ============================================================
ANALYSIS_INTERVAL = "5m"
TREND_INTERVAL = "15m"
DAILY_INTERVAL = "1d"
KLINE_LIMIT = 250

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
MIN_SCORE = float(os.getenv("MIN_SCORE", "6.0"))
EARLY_SNIPE_SCORE = float(os.getenv("EARLY_SNIPE_SCORE", "3.8"))
MIN_ADX = 12.0
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
ENABLE_RSI_FILTER = True

# ============================================================
# Early breakout
# ============================================================
SQUEEZE_BB_WIDTH = 0.02
SILENT_VOLUME_MULTIPLIER = 1.8
RESISTANCE_DISTANCE = 0.015

# ============================================================
# Risk
# ============================================================
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "10000"))
RISK_PER_TRADE = 0.01
MAX_POSITION_PERCENT = 0.50
ATR_SL_MULTIPLIER = 1.5
SL_BUFFER_PERCENT = 0.003
MIN_RR = 2.0
COOLDOWN_MINUTES = 45
OPPOSITE_COOLDOWN_HOURS = 4
DAILY_MAX_LOSS_PERCENT = 0.03

# ============================================================
# Scanner
# ============================================================
PREWATCH_PRICE_CHANGE = 3.0
PREWATCH_VOLUME_USDT = 2_000_000
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "30"))
PREWATCH_SCAN_EVERY = 3
MAX_PREWATCH_TO_SCAN = 30

# ============================================================
# Signal evaluation
# ============================================================
SIGNAL_MAX_HOLD_CANDLES = 1
SIGNAL_EVALUATION_INTERVAL = 60

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
        errors.append("WEBHOOK_URL or RENDER_EXTERNAL_URL is required in webhook mode")
    if not 0 < RISK_PER_TRADE <= 0.05:
        errors.append("RISK_PER_TRADE must be between 0 and 0.05")
    if not 0 < MAX_POSITION_PERCENT <= 1:
        errors.append("MAX_POSITION_PERCENT must be between 0 and 1")
    if MIN_RR < 2:
        errors.append("MIN_RR must be at least 2")
    if BINANCE_TIMEOUT > 5:
        errors.append("BINANCE_TIMEOUT must not exceed 5 seconds")
    if errors:
        raise ValueError(" | ".join(errors))
