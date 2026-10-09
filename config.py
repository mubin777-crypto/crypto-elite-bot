# config.py
# Quant Crypto Signal System v2026.3 - Rebuilt with Global Best Practices

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ============================================================
# Database
# ============================================================
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "trading_bot.db"))
if os.getenv("RENDER"):
    USE_POSTGRES = True
else:
    USE_POSTGRES = bool(DATABASE_URL) or os.getenv("USE_POSTGRES", "false").lower() == "true"

# ============================================================
# Render
# ============================================================
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", os.getenv("RENDER_EXTERNAL_URL", "")).rstrip("/")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
WEBHOOK_PATH = "/webhook"
HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "600"))
SELF_PING_INTERVAL = int(os.getenv("SELF_PING_INTERVAL", "240"))

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
MAX_CONCURRENT_REQUESTS = 5          # Render 512MB
REQUEST_DELAY = 0.08

# ============================================================
# Trading Fees
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

EXCLUDED_SYMBOLS = [
    "USDCUSDT", "BUSDUSDT", "TUSDUSDT", "DAIUSDT", "USDPUSDT",
    "FDUSDUSDT", "PYUSDUSDT", "USDDUSDT", "RLUSDUSDT", "USTCUSDT",
    "EURUSDT", "TRYUSDT", "BRLUSDT", "ARSUSDT", "BIDRUSDT",
    "AEURUSDT", "EURIUSDT", "USD1USDT",
    "TRUMPUSDT", "WLFIUSDT", "MELANIAUSDT", "BIDENUSDT",
]
EXCLUDED_SUFFIXES = ["BUSD", "UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"]
EXCLUDE_TOKENIZED_STOCKS = True

# ============================================================
# Pre-watch
# ============================================================
PREWATCH_MIN_VOLUME_USDT = 5_000_000
PREWATCH_MIN_TRADES = 15000
PREWATCH_MAX_PRICE = 1000.0
PREWATCH_MIN_PRICE = 0.00001
PREWATCH_PRICE_CHANGE = 4.5
PREWATCH_VOLUME_USDT = 6_000_000

# ============================================================
# Timeframes
# ============================================================
ANALYSIS_INTERVAL = "5m"
TREND_INTERVAL = "15m"
HIGHER_TF_INTERVAL = "1h"      # 🆕 من tokenometry: Multi-Timeframe Analysis[reference:24]
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
ER_PERIOD = 10                  # 🆕 Kaufman Efficiency Ratio (من binance-futures-ai-bot)[reference:25]

# ============================================================
# Signal Scoring
# ============================================================
MIN_SCORE = float(os.getenv("MIN_SCORE", "8.0"))
EARLY_SNIPE_SCORE = float(os.getenv("EARLY_SNIPE_SCORE", "8.5"))
MIN_ADX = float(os.getenv("MIN_ADX", "25.0"))
MIN_FACTORS_ALIGNED = 5
EXPLOSION_RSI_OVERRIDE = False

# 🆕 Signal Types (من nsydat/binance_bot + OpenClaw Apex)
EXPLOSION_MIN_ADX = 22.0
BREAKOUT_MIN_ADX = 20.0
BREAKOUT_MIN_VOLUME = 1.5
BREAKOUT_MIN_FACTORS = 3

# 🆕 Market Regime (من binance-futures-ai-bot)[reference:26]
REGIME_ADX_TRENDING = 25.0
REGIME_ADX_RANGING = 20.0
REGIME_ER_CHOPPY = 0.20
REGIME_ER_TRENDING = 0.35
ENABLE_REGIME_FILTER = True

# ============================================================
# RSI Filters
# ============================================================
RSI_OVERBOUGHT = 72.0
RSI_OVERSOLD = 28.0
RSI_BUY_ZONE_MIN = 45.0
RSI_BUY_ZONE_MAX = 62.0
RSI_SELL_ZONE_MIN = 38.0
RSI_SELL_ZONE_MAX = 55.0
ENABLE_RSI_FILTER = True

# ============================================================
# Explosion Detection
# ============================================================
SQUEEZE_MIN_CANDLES = 5
SQUEEZE_WIDTH_TREND_CANDLES = 4
KELTNER_PERIOD = 20
KELTNER_ATR_MULT = 1.5
SILENT_VOLUME_MULTIPLIER = 2.0
VOLUME_TREND_MULTIPLIER = 1.5
VOLUME_MIN_CONSECUTIVE = 3
RESISTANCE_DISTANCE = 0.005
CONSOLIDATION_RANGE_MAX = 0.020
HIGHER_LOWS_COUNT = 3
BREAKOUT_CONFIRMATION_PCT = 0.006
MOMENTUM_MIN = 0.6
MOMENTUM_MAX = 5.0

# ============================================================
# Quality Gate
# ============================================================
MIN_QUALITY_PERCENT = 80.0
REQUIRE_TREND_ALIGNMENT = True
REQUIRE_VOLUME_CONFIRMATION = True
REQUIRE_MOMENTUM_ALIGNMENT = True

# ============================================================
# Risk Management (من kadmos-risk + binance-futures-ai-bot)
# ============================================================
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "10000"))
RISK_PER_TRADE = 0.0075
MAX_POSITION_PERCENT = 0.30
ATR_SL_MULTIPLIER = 1.8
SL_BUFFER_PERCENT = 0.004
MIN_RR = 2.8
COOLDOWN_MINUTES = 240
OPPOSITE_COOLDOWN_HOURS = 12
DAILY_MAX_LOSS_PERCENT = 0.02

# 🆕 من multi-market-trading-bot: Drawdown Circuit Breaker[reference:27]
MAX_WEEKLY_DRAWDOWN_PERCENT = 0.06
MAX_MONTHLY_DRAWDOWN_PERCENT = 0.15
MAX_CONSECUTIVE_LOSSES = 5          # من kadmos-risk[reference:28]
ENABLE_CIRCUIT_BREAKER = True

SIGNAL_MAX_HOLD_CANDLES = 6
SIGNAL_EVALUATION_INTERVAL = 60

# ============================================================
# 🆕 Anti-Spam (من nsydat/binance_bot + bearish-alpha-bot)
# ============================================================
MAX_SIGNALS_PER_SYMBOL_PER_DAY = 2
DUPLICATE_WINDOW_HOURS = 4
MAX_SIGNALS_PER_DAY = 15
MAX_SIGNALS_PER_HOUR = 3
MIN_SIGNAL_GAP_MINUTES = 10         # من nsydat/binance_bot[reference:29]

# ============================================================
# 🆕 Signal Outcome Tracker
# ============================================================
TRACKER_INTERVAL = 60
OUTCOME_CHECK_CANDLES = 6
DAILY_SUMMARY_HOUR_UTC = 0

# ============================================================
# 🆕 Near-Miss Monitoring (من OpenClaw Apex)[reference:30]
# ============================================================
ENABLE_NEAR_MISS_LOGGING = True
NEAR_MISS_MAX_LOGS = 500

# ============================================================
# Scanner
# ============================================================
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
PREWATCH_SCAN_EVERY = 6
MAX_PREWATCH_TO_SCAN = 20
MAX_LAST_DATA_UPDATE = 100

# ============================================================
# Adaptive Weights
# ============================================================
FACTORS = ["rsi", "adx", "momentum", "volume", "bollinger", "macd", "pivot"]
ADAPTIVE_WEIGHTS_MIN_SAMPLES = 20

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
        errors.append("WEBHOOK_URL required in webhook mode")
    if not 0 < RISK_PER_TRADE <= 0.05:
        errors.append("RISK_PER_TRADE must be 0-0.05")
    if MIN_RR < 2.0:
        errors.append("MIN_RR must be >= 2.0")
    if BINANCE_TIMEOUT > 5:
        errors.append("BINANCE_TIMEOUT must be <= 5")
    if MIN_SCORE < 7.5:
        errors.append("MIN_SCORE must be >= 7.5")
    if TELEGRAM_API_TIMEOUT <= TELEGRAM_LONG_POLL_TIMEOUT:
        errors.append("TELEGRAM_API_TIMEOUT must be > LONG_POLL_TIMEOUT")
    if os.getenv("RENDER") and not DATABASE_URL:
        errors.append("DATABASE_URL required on Render")
    if errors:
        raise ValueError(" | ".join(errors))
