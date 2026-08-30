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

    # -------------------- إعدادات البوت (عتبات وسطية للصيد الذكي) --------------------
    DB_PATH = DATABASE_URL.replace("sqlite:///", "")
    RATE_LIMIT_DELAY = 0.1
    SEMAPHORE_LIMIT = 5
    COOLDOWN_MINUTES = 45
    MIN_VOLUME_USD = 100_000
    MIN_VOLATILITY_DAILY = 0.1

    # 🔥 عتبات وسطية (توازن بين الوهم والحقيقة)
    SIGNAL_SCORE_THRESHOLD = 4.5          # عتبة ذهبية
    CONFIRMATION_SCORE_BONUS = 0.5
    CONFIRMATION_WAIT_CANDLES = 0         # تخطي التأكيد في السوق الهادئ
    RISK_PER_TRADE = 0.01
    MAX_POSITION_SIZE_PCT = 2.0
    MIN_CHANGE_1H = 0.1                   # تغير بسيط مقبول
    RSI_PERIOD = 6
    ADX_PERIOD = 14
    MIN_ADX_STRONG = 12                   # وسط (8 ضعيف، 25 صارم)
    DAILY_LOSS_LIMIT_PCT = 3.0
    PAPER_TRADING = True
    INITIAL_CAPITAL = 10000.0
    MAX_OPEN_TRADES = 3
    DYNAMIC_SYMBOLS_LIMIT = 100
    DYNAMIC_UPDATE_INTERVAL = 900
    ADAPTIVE_THRESHOLD = True

    # -------------------- ccxt (مفعل لسرعة ودقة البيانات) --------------------
    USE_CCXT = True
    CCXT_EXCHANGE = "binance"
    CCXT_RATE_LIMIT = 1200
    CCXT_MAX_SYMBOLS = 200

    # -------------------- ADX --------------------
    ENABLE_ADX_FILTER = True
    MIN_ADX_STRONG = 12

    # -------------------- المراقبة الاستباقية (لصيد العملات الصامتة) --------------------
    PRE_WATCH_ENABLED = True
    PRE_WATCH_SCAN_INTERVAL = 300
    PRE_WATCH_MIN_VOLUME = 75_000        # وسطية
    PRE_WATCH_MIN_CHANGE = 0.4           # وسطية
    PRE_WATCH_MAX_SUPPLY = 1_000_000_000
    PRE_WATCH_ALERT_THRESHOLD = 55       # وسطية
    PRE_WATCH_MAX_SYMBOLS = 20

    # -------------------- ضوابط مسح العملات غير المدرجة --------------------
    MAX_PREWATCH_TO_SCAN = 30            # أقصى عدد من عملات Pre-watch لتحليلها في كل دورة
    SCAN_UNLISTED_SYMBOLS = True         # تفعيل فحص العملات غير المدرجة

    # -------------------- قواعد الانفجار المبكر --------------------
    BB_SQUEEZE_THRESHOLD = 2.0           # انضغاط بولينجر أقل من 2%
    EARLY_VOLUME_RATIO = 1.8             # حجم يتدفق بصمت (أموال ذكية)

    # -------------------- قائمة العملات الأساسية (موسعة) --------------------
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
        "UMAUSDT", "ZECUSDT", "TIAUSDT", "SEIUSDT", "SUIUSDT", "TONUSDT"
    ]

    TIMEFRAMES = {
        "5m": {"limit": 100, "weight": 1.0},
        "1h": {"limit": 30, "weight": 1.5},
        "4h": {"limit": 20, "weight": 2.0},
    }

config = Config()
