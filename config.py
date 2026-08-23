# config.py - إعدادات البوت
import os

class Config:
    # Telegram
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
    ADMIN_CHAT_ID = os.environ.get("CHAT_ID", "")
    
    # Database
    DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://user:pass@localhost:5432/elite_signal_bot")
    
    # Binance API
    BINANCE_BASE_URL = os.environ.get("BINANCE_BASE_URL", "https://api.binance.com")
    BINANCE_TIMEOUT = 10
    BINANCE_RETRIES = 3
    
    # Scanning
    MIN_VOLUME_USD = 300_000
    MIN_VOLATILITY = 0.5
    TOP_SYMBOLS_COUNT = 25
    COOLDOWN_MINUTES = 45
    SCAN_INTERVAL_SECONDS = 300  # 5 minutes
    
    # Indicators
    RSI_PERIOD = 6
    ADX_PERIOD = 14
    MIN_ADX_STRONG = 25
    MIN_CHANGE_1H = 0.3
    
    # Risk Management
    MAX_POSITION_SIZE_PCT = 2.0
    MAX_TRADE_DURATION_HOURS = 48
    RISK_PER_TRADE_PCT = 1.0
    MIN_RISK_REWARD_RATIO = 1.5
    
    # Signal
    SIGNAL_VALIDITY_MINUTES = 15
    
    # Performance
    PERFORMANCE_UPDATE_INTERVAL_SECONDS = 60
    
    # Flask
    PORT = int(os.environ.get("PORT", 10000))
