"""
config.py - ملف الإعدادات المركزي للبوت.
"""
import os
from dataclasses import dataclass, field
from typing import List, Dict

@dataclass
class Config:
    # ─── مفاتيح API ───
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_SECRET_KEY: str = os.getenv("BINANCE_SECRET_KEY", "")
    
    # ─── Telegram ───
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_ADMIN_ID: int = int(os.getenv("TELEGRAM_ADMIN_ID", "0"))
    TELEGRAM_CHANNEL_ID: str = os.getenv("TELEGRAM_CHANNEL_ID", "")
    
    # ─── إعدادات السوق ───
    QUOTE_ASSET: str = "USDT"
    TOP_N_COINS: int = 50
    TIMEFRAMES: List[str] = field(default_factory=lambda: ["5m", "1h", "4h"])
    SCAN_INTERVAL_SECONDS: int = 60
    
    # ─── إعدادات قاعدة البيانات ───
    DB_PATH: str = "crypto_signals.db"
    MAX_CANDLES_PER_SYMBOL: int = 250
    
    # ─── إعدادات المخاطر ───
    VIRTUAL_CAPITAL: float = 10_000.0
    RISK_PER_TRADE_PERCENT: float = 1.0
    MAX_DAILY_LOSS_PERCENT: float = 3.0
    COOLDOWN_MINUTES: int = 45
    OPPOSITE_SIGNAL_COOLDOWN: int = 240
    PRICE_TOLERANCE: float = 0.005
    MIN_RR_RATIO: float = 2.0
    SL_BUFFER_PERCENT: float = 0.003
    
    # ─── عتبات المؤشرات ───
    RSI_PERIOD: int = 6
    RSI_OVERSOLD: float = 30.0
    RSI_OVERBOUGHT: float = 70.0
    ADX_PERIOD: int = 14
    ADX_STRONG_TREND: float = 25.0
    ADX_WEAK_TREND: float = 20.0
    BB_PERIOD: int = 20
    BB_STD: float = 2.0
    BB_SQUEEZE_THRESHOLD: float = 0.05
    SMA_FAST: int = 20
    SMA_SLOW: int = 50
    MACD_FAST: int = 12
    MACD_SLOW: int = 26
    MACD_SIGNAL: int = 9
    MOMENTUM_PERIOD: int = 6
    VOLUME_AVG_PERIOD: int = 12
    VOLUME_SPIKE_RATIO: float = 1.5
    ATR_PERIOD: int = 14
    ATR_SL_MULTIPLIER: float = 1.5
    
    # ─── عتبات الإشارة ───
    MIN_CONFIDENCE: float = 75.0
    MIN_SCORE: float = 7.0
    MAX_SCORE_SIDEWAYS: float = 6.0
    
    # ─── أوزان المؤشرات ───
    WEIGHTS: Dict[str, float] = field(default_factory=lambda: {
        "trend": 0.20,
        "momentum": 0.15,
        "volume": 0.15,
        "volatility": 0.15,
        "rsi": 0.15,
        "macd": 0.10,
        "pivot": 0.10,
    })
    
    # ─── قائمة العملات الأساسية ───
    CORE_UNIVERSE: List[str] = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "SHIBUSDT",
        "ADAUSDT", "AVAXUSDT", "MATICUSDT", "DOTUSDT", "LINKUSDT", "UNIUSDT", "ATOMUSDT",
        "LTCUSDT", "BCHUSDT", "NEARUSDT", "FILUSDT", "ICPUSDT", "ETCUSDT", "XTZUSDT",
        "THETAUSDT", "XLMUSDT", "VETUSDT", "TRXUSDT", "EOSUSDT", "AAVEUSDT", "MKRUSDT",
        "SANDUSDT", "MANAUSDT", "AXSUSDT", "APEUSDT", "FTMUSDT", "ONEUSDT", "OCEANUSDT",
        "RNDRUSDT", "FETUSDT", "WIFUSDT", "BONKUSDT", "PEPEUSDT", "FLOKIUSDT", "BRETTUSDT",
        "ALGOUSDT", "ARBUSDT", "APTUSDT", "CAKEUSDT", "COMPUSDT",
        "EGLDUSDT", "ENJUSDT", "FLOWUSDT", "GALAUSDT", "GRTUSDT", "HBARUSDT",
        "IMXUSDT", "INJUSDT", "KAVAUSDT", "KSMUSDT", "LDOUSDT", "MASKUSDT",
        "NEOUSDT", "QNTUSDT", "RENUSDT", "ROSEUSDT", "RVNUSDT", "SUSHIUSDT",
        "UMAUSDT", "ZECUSDT", "TIAUSDT", "SEIUSDT", "SUIUSDT", "TONUSDT"
    ])
    
    # ─── إعدادات Pre-watch ───
    SCAN_UNLISTED_SYMBOLS: bool = True
    MAX_PREWATCH_TO_SCAN: int = 30
    MAX_CONCURRENT_REQUESTS: int = 10
    REQUEST_DELAY: float = 0.05
    REQUEST_TIMEOUT: int = 3  # 🔥 مهلة الطلب بالثواني
    # ─── إعدادات الأداء ───
    MAX_CONCURRENT_REQUESTS: int = 10
    REQUEST_DELAY: float = 0.05
    
    # ─── النشر ───
    RENDER_EXTERNAL_URL: str = os.getenv("RENDER_EXTERNAL_URL", "")
    SELF_PING_INTERVAL: int = 300
    PORT: int = int(os.getenv("PORT", "8080"))
    
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

CFG = Config()
