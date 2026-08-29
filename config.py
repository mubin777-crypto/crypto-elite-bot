# config.py - الإعدادات العامة مع دعم ccxt و ADX
import os

class Config:
    # -------------------- متغيرات البيئة --------------------
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
    ADMIN_CHAT_ID = os.environ.get("CHAT_ID", "")
    DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///crypto_bot.db")
    PORT = int(os.environ.get("PORT", 10000))
    RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "localhost")

    # -------------------- مصادر البيانات --------------------
    BINANCE_US_BASE = "https://api.binance.us"
    BINANCE_COM_BASE = "https://api.binance.com"
    COINBASE_BASE = "https://api.exchange.coinbase.com"
    COINCAP_BASE = "https://api.coincap.io/v2"
    CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1"
    COINGECKO_BASE = "https://api.coingecko.com/api/v3"

    # -------------------- إعدادات البوت الأساسية --------------------
    DB_PATH = DATABASE_URL.replace("sqlite:///", "")
    RATE_LIMIT_DELAY = 0.1
    SEMAPHORE_LIMIT = 5
    COOLDOWN_MINUTES = 45
    MIN_VOLUME_USD = 200_000
    MIN_VOLATILITY_DAILY = 0.3
    SIGNAL_SCORE_THRESHOLD = 6.5
    CONFIRMATION_SCORE_BONUS = 0.5
    CONFIRMATION_WAIT_CANDLES = 2
    RISK_PER_TRADE = 0.01
    MAX_POSITION_SIZE_PCT = 2.0
    MIN_CHANGE_1H = 0.25
    RSI_PERIOD = 6
    ADX_PERIOD = 14
    MIN_ADX_STRONG = 25
    DAILY_LOSS_LIMIT_PCT = 3.0
    PAPER_TRADING = True
    INITIAL_CAPITAL = 10000.0
    MAX_OPEN_TRADES = 3
    DYNAMIC_SYMBOLS_LIMIT = 100
    DYNAMIC_UPDATE_INTERVAL = 900
    ADAPTIVE_THRESHOLD = True

    # -------------------- إعدادات ccxt (جديد) --------------------
    USE_CCXT = True                      # تفعيل استخدام ccxt
    CCXT_EXCHANGE = "binance"            # أو "binanceus"
    CCXT_RATE_LIMIT = 1200
    CCXT_MAX_SYMBOLS = 200               # حد أقصى للعملات في كل مسح

    # -------------------- إعدادات ADX (جديد) --------------------
    ENABLE_ADX_FILTER = True
    MIN_ADX_STRONG = 25

    # -------------------- إعدادات المراقبة الاستباقية --------------------
    PRE_WATCH_ENABLED = True
    PRE_WATCH_SCAN_INTERVAL = 300
    PRE_WATCH_MIN_VOLUME = 1_000_000
    PRE_WATCH_MIN_CHANGE = 2.0
    PRE_WATCH_MAX_SUPPLY = 1_000_000_000
    PRE_WATCH_ALERT_THRESHOLD = 70
    PRE_WATCH_MAX_SYMBOLS = 20

    # -------------------- قائمة العملات الأساسية --------------------
    CORE_UNIVERSE = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "SHIBUSDT",
        "ADAUSDT", "AVAXUSDT", "MATICUSDT", "DOTUSDT", "LINKUSDT", "UNIUSDT", "ATOMUSDT",
        "LTCUSDT", "BCHUSDT", "NEARUSDT", "FILUSDT", "ICPUSDT", "ETCUSDT", "XTZUSDT",
        "THETAUSDT", "XLMUSDT", "VETUSDT", "TRXUSDT", "EOSUSDT", "AAVEUSDT", "MKRUSDT",
        "SANDUSDT", "MANAUSDT", "AXSUSDT", "APEUSDT", "FTMUSDT", "ONEUSDT", "OCEANUSDT",
        "RNDRUSDT", "FETUSDT", "WIFUSDT", "BONKUSDT", "PEPEUSDT", "FLOKIUSDT", "BRETTUSDT",
        "ALGOUSDT", "ARBUSDT", "APTUSDT", "CAKEUSDT", "COMPUSDT", "CROUSDT",
        "EGLDUSDT", "ENJUSDT", "FLOWUSDT", "GALAUSDT", "GRTUSDT", "HBARUSDT",
        "IMXUSDT", "INJUSDT", "KAVAUSDT", "KSMUSDT", "LDOUSDT", "MASKUSDT",
        "NEOUSDT", "QNTUSDT", "RENUSDT", "ROSEUSDT", "RVNUSDT", "SUSHIUSDT",
        "UMAUSDT", "ZECUSDT", "TIAUSDT", "SEIUSDT", "SUIUSDT", "TONUSDT",
        "HNTUSDT", "PONSUSDT", "GIGGLEUSDT", "MAGMAUSDT", "LIGHTUSDT", "EDENUSDT"
    ]

    # -------------------- الأطر الزمنية --------------------
    TIMEFRAMES = {
        "5m": {"limit": 100, "weight": 1.0},
        "1h": {"limit": 30, "weight": 1.5},
        "4h": {"limit": 20, "weight": 2.0},
        "1d": {"limit": 10, "weight": 2.5}
    }

config = Config()
