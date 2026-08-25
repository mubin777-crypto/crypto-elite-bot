# config.py - الإعدادات العامة (بعد إزالة التوكن الثابت)
import os

class Config:
    # -------------------- متغيرات البيئة --------------------
    # 🔒 التوكن يُقرأ فقط من متغيرات البيئة (غير موجود في الكود)
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
    ADMIN_CHAT_ID = os.environ.get("CHAT_ID", "")
    DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///crypto_bot.db")
    PORT = int(os.environ.get("PORT", 10000))
    RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "localhost")

    # -------------------- مصادر البيانات --------------------
    BINANCE_US_BASE = "https://api.binance.us"
    COINBASE_BASE = "https://api.exchange.coinbase.com"
    COINCAP_BASE = "https://api.coincap.io/v2"
    CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1"

    # -------------------- إعدادات البوت الأساسية --------------------
    DB_PATH = "crypto_bot.db"
    RATE_LIMIT_DELAY = 0.1
    SEMAPHORE_LIMIT = 5
    COOLDOWN_MINUTES = 45
    MIN_VOLUME_USD = 200_000
    MIN_VOLATILITY_DAILY = 0.3
    SIGNAL_SCORE_THRESHOLD = 4.0
    CONFIRMATION_SCORE_BONUS = 0.5
    CONFIRMATION_WAIT_CANDLES = 2
    RISK_PER_TRADE = 0.01
    MAX_POSITION_SIZE_PCT = 2.0
    MIN_CHANGE_1H = 0.3
    RSI_PERIOD = 6
    ADX_PERIOD = 14
    MIN_ADX_STRONG = 20
    DAILY_LOSS_LIMIT_PCT = 3.0
    PAPER_TRADING = True
    INITIAL_CAPITAL = 10000.0
    MAX_OPEN_TRADES = 3
    DYNAMIC_SYMBOLS_LIMIT = 70
    DYNAMIC_UPDATE_INTERVAL = 1800
    ADAPTIVE_THRESHOLD = True

    # -------------------- قائمة العملات الأساسية --------------------
    CORE_UNIVERSE = [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "SHIBUSDT",
        "ADAUSDT", "AVAXUSDT", "MATICUSDT", "DOTUSDT", "LINKUSDT", "UNIUSDT", "ATOMUSDT",
        "LTCUSDT", "BCHUSDT", "NEARUSDT", "FILUSDT", "ICPUSDT", "ETCUSDT", "XTZUSDT",
        "THETAUSDT", "XLMUSDT", "VETUSDT", "TRXUSDT", "EOSUSDT", "AAVEUSDT", "MKRUSDT",
        "SANDUSDT", "MANAUSDT", "AXSUSDT", "APEUSDT", "FTMUSDT", "ONEUSDT", "OCEANUSDT",
        "RNDRUSDT", "FETUSDT", "WIFUSDT", "BONKUSDT", "PEPEUSDT", "FLOKIUSDT", "BRETTUSDT",
        "ALGOUSDT", "ARBUSDT", "APTUSDT", "CAKEUSDT", "COMPUSDT", "CROUSDT",
        "EGLDUSDT", "ENJUSDT", "FLOWUSDT", "GALAUSDT", "GRTUSDT", "HBARUSDT",
        "IMXUSDT", "INJUSDT", "KAVAUSDT", "KSMUSDT", "LDOUSDT", "MASKUSDT",
        "NEOUSDT", "QNTUSDT", "RENUSDT", "ROSEUSDT", "RVNUSDT", "SUSHIUSDT",
        "UMAUSDT", "ZECUSDT", "TIAUSDT", "SEIUSDT", "SUIUSDT", "TONUSDT"
    ]

    # -------------------- الأطر الزمنية --------------------
    TIMEFRAMES = {
        "5m": {"limit": 100, "weight": 1.0},
        "1h": {"limit": 30, "weight": 1.5},
        "4h": {"limit": 20, "weight": 2.0},
        "1d": {"limit": 10, "weight": 2.5}
    }

config = Config()
