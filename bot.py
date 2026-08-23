#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Elite Signal Bot V23 - الإصدار النهائي مع جميع التعديلات
- INCONCLUSIVE منفصلة عن الإحصائيات
- Atomic Commit مع BEGIN IMMEDIATE
- Tracker مع فحص فجوات البيانات
- Supervisor مع backoff
- Transactional Outbox للإرسال
"""

import os
import sys
import time
import math
import logging
import asyncio
import threading
import aiosqlite
import aiohttp
import html
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any, NamedTuple
from datetime import datetime, timedelta, timezone
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ===================================================================
# 1. الإعدادات (Config)
# ===================================================================

class Config:
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
    ADMIN_CHAT_ID = os.environ.get("CHAT_ID", "")
    DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///elite_signal_bot.db")
    BINANCE_BASE_URL = os.environ.get("BINANCE_BASE_URL", "https://api.binance.com")
    BINANCE_TIMEOUT = 10
    BINANCE_RETRIES = 3
    PORT = int(os.environ.get("PORT", 10000))
    
    # Universe
    CORE_UNIVERSE = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
        "SUIUSDT", "TONUSDT", "LTCUSDT", "BCHUSDT", "NEARUSDT",
        "APTUSDT", "ARBUSDT", "OPUSDT", "ATOMUSDT", "FILUSDT",
        "INJUSDT", "TIAUSDT", "SEIUSDT", "WLDUSDT", "HBARUSDT"
    ]
    MAX_UNIVERSE_SIZE = 70
    CORE_SIZE = 25
    DYNAMIC_SIZE = 45
    
    # Filters
    MIN_VOLUME_USD = 1_000_000
    MIN_TRADES_24H = 500
    MIN_VOLATILITY_DAILY = 1.0
    MAX_STABLE_COINS = ["USDC", "FDUSD", "TUSD", "BUSD", "DAI"]
    EXCLUDED_SYMBOLS = ["UP", "DOWN", "BULL", "BEAR", "HALF"]
    
    # Risk
    INITIAL_CAPITAL = 10000.0
    MAX_POSITION_PCT = 2.0
    RISK_PER_TRADE_PCT = 1.0
    MIN_RISK_REWARD_RATIO = 1.5
    MAX_TRADE_DURATION_HOURS = 48
    MAX_OPEN_TRADES = 5
    MAX_SECTOR_EXPOSURE = 2
    DAILY_LOSS_LIMIT_PCT = 3.0
    MAX_CONSECUTIVE_LOSSES = 3
    CORRELATION_THRESHOLD = 0.80
    
    # Scanner
    SCAN_INTERVAL_SECONDS = 300
    COOLDOWN_MINUTES = 45
    
    # Indicators
    RSI_PERIOD = 6
    ADX_PERIOD = 14
    MIN_ADX_STRONG = 25
    MIN_CHANGE_1H = 0.3
    
    # Rate Limiting
    MAX_REQUESTS_PER_MINUTE = 1200
    REQUEST_BURST = 5
    
    # Scoring
    SCORE_ELITE = 90
    SCORE_STRONG = 80
    SCORE_GOOD = 70
    MIN_SIGNAL_SCORE = 70
    
    # Sectors
    SECTORS = {
        "BTC_ECO": ["BTCUSDT", "STXUSDT", "ORDIUSDT"],
        "ETH_ECO": ["ETHUSDT", "LDOUSDT", "ARBUSDT", "OPUSDT"],
        "SOL_ECO": ["SOLUSDT", "JUPUSDT", "PYTHUSDT", "JTOUSDT"],
        "AI": ["FETUSDT", "RENDERUSDT", "TAOUSDT", "WLDUSDT"],
        "L1": ["BNBUSDT", "ADAUSDT", "AVAXUSDT", "NEARUSDT", "APTUSDT", "SUIUSDT", "TONUSDT"],
        "DeFi": ["UNIUSDT", "AAVEUSDT", "MKRUSDT", "CRVUSDT", "PENDLEUSDT"],
        "Meme": ["DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT", "FLOKIUSDT"],
        "RWA": ["ONDOUSDT", "ENAUSDT"],
        "Gaming": ["IMXUSDT", "GALAUSDT", "SANDUSDT", "MANAUSDT"],
        "Infra": ["LINKUSDT", "DOTUSDT", "ATOMUSDT", "FILUSDT", "GRTUSDT"]
    }

config = Config()

# ===================================================================
# 2. إعدادات التسجيل
# ===================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ===================================================================
# 3. نماذج البيانات (Data Models)
# ===================================================================

@dataclass
class CandleData:
    prices: List[float]
    highs: List[float]
    lows: List[float]
    volumes: List[float]
    opens: List[float]
    timestamps: List[datetime] = None

    def __post_init__(self):
        if not (len(self.prices) == len(self.highs) == len(self.lows) == len(self.volumes) == len(self.opens)):
            raise ValueError("All candle lists must have the same length")
        if self.timestamps is None:
            self.timestamps = [datetime.now(timezone.utc) - timedelta(minutes=i) for i in range(len(self.prices)-1, -1, -1)]

    @property
    def length(self) -> int:
        return len(self.prices)

    def closed_prices(self) -> List[float]:
        return self.prices[:-1] if len(self.prices) > 1 else []

    def closed_highs(self) -> List[float]:
        return self.highs[:-1] if len(self.highs) > 1 else []

    def closed_lows(self) -> List[float]:
        return self.lows[:-1] if len(self.lows) > 1 else []

    def closed_volumes(self) -> List[float]:
        return self.volumes[:-1] if len(self.volumes) > 1 else []

    def get_current_price(self) -> float:
        return self.prices[-1] if self.prices else 0.0

    def get_reference_price(self) -> float:
        return self.prices[-2] if len(self.prices) >= 2 else self.prices[-1] if self.prices else 0.0


@dataclass
class MarketStats:
    volume: float
    change_24h: float
    high: float
    low: float
    open: float
    last: float

# ===================================================================
# 4. المؤشرات الفنية (Indicators) - مختصر
# ===================================================================

class Indicators:
    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(prices)):
            diff = prices[i] - prices[i-1]
            gains.append(diff if diff >= 0 else 0.0)
            losses.append(abs(diff) if diff < 0 else 0.0)
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        return 100 - (100 / (1 + avg_gain / avg_loss))

    @staticmethod
    def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 0.0
        tr_list = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            tr_list.append(tr)
        if len(tr_list) < period:
            return 0.0
        atr = sum(tr_list[:period]) / period
        for i in range(period, len(tr_list)):
            atr = (atr * (period - 1) + tr_list[i]) / period
        return atr

    @staticmethod
    def calculate_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        if len(closes) < period * 2 + 1:
            return 0.0
        plus_dm, minus_dm, tr_list = [], [], []
        for i in range(1, len(closes)):
            up = highs[i] - highs[i-1]
            down = lows[i-1] - lows[i]
            if up > down and up > 0:
                plus_dm.append(up); minus_dm.append(0.0)
            elif down > up and down > 0:
                plus_dm.append(0.0); minus_dm.append(down)
            else:
                plus_dm.append(0.0); minus_dm.append(0.0)
            tr_list.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])))
        atr = sum(tr_list[:period]) / period
        plus_smooth = sum(plus_dm[:period]) / period
        minus_smooth = sum(minus_dm[:period]) / period
        dx_values = []
        plus_di = (plus_smooth / atr) * 100 if atr > 0 else 0
        minus_di = (minus_smooth / atr) * 100 if atr > 0 else 0
        dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
        dx_values.append(dx)
        for i in range(period, len(tr_list)):
            atr = (atr * (period - 1) + tr_list[i]) / period
            plus_smooth = (plus_smooth * (period - 1) + plus_dm[i]) / period
            minus_smooth = (minus_smooth * (period - 1) + minus_dm[i]) / period
            plus_di = (plus_smooth / atr) * 100 if atr > 0 else 0
            minus_di = (minus_smooth / atr) * 100 if atr > 0 else 0
            dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
            dx_values.append(dx)
        if len(dx_values) < period:
            return dx_values[-1] if dx_values else 0.0
        return sum(dx_values[-period:]) / period

    @staticmethod
    def calculate_sma(prices: List[float], period: int = 20) -> float:
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        return sum(prices[-period:]) / period

    @staticmethod
    def calculate_ema_series(prices: List[float], period: int) -> List[float]:
        if len(prices) < period:
            return [prices[-1]] * len(prices) if prices else []
        multiplier = 2 / (period + 1)
        ema_series = [prices[0]]
        for price in prices[1:]:
            ema = (price - ema_series[-1]) * multiplier + ema_series[-1]
            ema_series.append(ema)
        return ema_series

    @staticmethod
    def calculate_macd(prices: List[float], short: int = 12, long: int = 26, signal: int = 9) -> dict:
        if len(prices) < long:
            return {"histogram": 0.0}
        ema_short = Indicators.calculate_ema_series(prices, short)
        ema_long = Indicators.calculate_ema_series(prices, long)
        macd_line = [s - l for s, l in zip(ema_short, ema_long)]
        signal_line = Indicators.calculate_ema_series(macd_line, signal)
        return {"histogram": macd_line[-1] - signal_line[-1]}

    @staticmethod
    def calculate_bollinger(prices: List[float], period: int = 20, std_dev: int = 2) -> dict:
        if len(prices) < period:
            return {"upper": 0.0, "middle": 0.0, "lower": 0.0}
        sma = sum(prices[-period:]) / period
        variance = sum((p - sma) ** 2 for p in prices[-period:]) / period
        std = math.sqrt(variance)
        return {"upper": sma + std_dev * std, "middle": sma, "lower": sma - std_dev * std}

# ===================================================================
# 5. هيكل السوق (MarketStructure) - مختصر
# ===================================================================

class MarketStructure:
    def __init__(self, data: CandleData):
        self.data = data
        self.prices = data.closed_prices()
        self.highs = data.closed_highs()
        self.lows = data.closed_lows()
        self._swing_highs = None
        self._swing_lows = None

    def get_swing_highs(self, lookback: int = 3) -> List[Tuple[int, float]]:
        if self._swing_highs is not None:
            return self._swing_highs
        if len(self.highs) < lookback * 2:
            return []
        swings = []
        for i in range(lookback, len(self.highs) - lookback):
            is_peak = True
            for j in range(1, lookback + 1):
                if self.highs[i] < self.highs[i - j] or self.highs[i] < self.highs[i + j]:
                    is_peak = False
                    break
            if is_peak:
                swings.append((i, self.highs[i]))
        self._swing_highs = swings
        return swings

    def get_swing_lows(self, lookback: int = 3) -> List[Tuple[int, float]]:
        if self._swing_lows is not None:
            return self._swing_lows
        if len(self.lows) < lookback * 2:
            return []
        swings = []
        for i in range(lookback, len(self.lows) - lookback):
            is_trough = True
            for j in range(1, lookback + 1):
                if self.lows[i] > self.lows[i - j] or self.lows[i] > self.lows[i + j]:
                    is_trough = False
                    break
            if is_trough:
                swings.append((i, self.lows[i]))
        self._swing_lows = swings
        return swings

    def get_last_swing_high(self) -> Optional[float]:
        swings = self.get_swing_highs()
        return swings[-1][1] if swings else None

    def get_last_swing_low(self) -> Optional[float]:
        swings = self.get_swing_lows()
        return swings[-1][1] if swings else None

    def get_trend(self) -> str:
        if len(self.prices) < 50:
            return 'neutral'
        highs = self.get_swing_highs()
        lows = self.get_swing_lows()
        if len(highs) < 3 or len(lows) < 3:
            return 'neutral'
        recent_highs = [h for _, h in highs[-3:]]
        recent_lows = [l for _, l in lows[-3:]]
        if len(recent_highs) >= 3 and len(recent_lows) >= 3:
            hh = all(recent_highs[i] < recent_highs[i+1] for i in range(len(recent_highs)-1))
            hl = all(recent_lows[i] < recent_lows[i+1] for i in range(len(recent_lows)-1))
            if hh and hl:
                return 'bullish'
            lh = all(recent_highs[i] > recent_highs[i+1] for i in range(len(recent_highs)-1))
            ll = all(recent_lows[i] > recent_lows[i+1] for i in range(len(recent_lows)-1))
            if lh and ll:
                return 'bearish'
        return 'neutral'

# ===================================================================
# 6. جلب البيانات (BinanceClient) - مع RateLimiter مشترك
# ===================================================================

class BinanceRateLimiter:
    def __init__(self):
        self.timestamps = []
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        async with self.lock:
            now = time.time()
            self.timestamps = [t for t in self.timestamps if now - t < 60]
            if len(self.timestamps) >= config.MAX_REQUESTS_PER_MINUTE:
                wait_time = 60 - (now - self.timestamps[0]) + 0.5
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
            self.timestamps.append(now)

class BinanceClient:
    def __init__(self, session: aiohttp.ClientSession, rate_limiter: BinanceRateLimiter):
        self.session = session
        self.rate_limiter = rate_limiter
        self.base_url = config.BINANCE_BASE_URL
        self.timeout = config.BINANCE_TIMEOUT
        self.retries = config.BINANCE_RETRIES

    async def _request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        await self.rate_limiter.acquire()
        url = f"{self.base_url}{endpoint}"
        for attempt in range(self.retries):
            try:
                async with self.session.get(url, params=params, timeout=self.timeout) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429:
                        retry_after = int(resp.headers.get('Retry-After', 10))
                        logger.warning(f"⚠️ Rate limit hit, waiting {retry_after}s")
                        await asyncio.sleep(retry_after)
                    else:
                        break
            except Exception as e:
                logger.error(f"Request error: {e}")
                if attempt < self.retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return None
        return None

    async def get_klines(self, symbol: str, interval: str = '5m', limit: int = 100) -> Optional[CandleData]:
        params = {'symbol': symbol, 'interval': interval, 'limit': limit}
        data = await self._request('/api/v3/klines', params)
        if not data:
            return None
        timestamps = [datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc) for c in data]
        return CandleData(
            prices=[float(c[4]) for c in data],
            highs=[float(c[2]) for c in data],
            lows=[float(c[3]) for c in data],
            volumes=[float(c[5]) for c in data],
            opens=[float(c[1]) for c in data],
            timestamps=timestamps
        )

    async def get_24hr_stats(self, symbol: str) -> Optional[MarketStats]:
        data = await self._request('/api/v3/ticker/24hr', {'symbol': symbol})
        if not data:
            return None
        return MarketStats(
            volume=float(data.get('quoteVolume', 0)),
            change_24h=float(data.get('priceChangePercent', 0)),
            high=float(data.get('highPrice', 0)),
            low=float(data.get('lowPrice', 0)),
            open=float(data.get('openPrice', 0)),
            last=float(data.get('lastPrice', 0))
        )

    async def get_all_24hr_stats(self) -> Optional[List[Dict]]:
        data = await self._request('/api/v3/ticker/24hr')
        return data if isinstance(data, list) else []

# ===================================================================
# 7. قاعدة البيانات (Database) - مع دعم الاتصال الموحد
# ===================================================================

class Database:
    def __init__(self):
        self.db_path = config.DATABASE_URL.replace("sqlite:///", "")
        self._closed = False
        self._connection = None

    async def connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscribers (user_id TEXT PRIMARY KEY)
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS pending (user_id TEXT PRIMARY KEY)
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS signal_cooldown (symbol TEXT PRIMARY KEY, last_signal_time TEXT)
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS signals_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    timestamp TEXT,
                    signal_type TEXT,
                    entry_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    status TEXT DEFAULT 'OPEN',
                    exit_price REAL,
                    trade_return_percent REAL,
                    portfolio_pnl_percent REAL,
                    pnl_usd REAL,
                    duration_minutes INTEGER,
                    win BOOLEAN,
                    entry_time TEXT,
                    exit_time TEXT,
                    sector TEXT,
                    quality_score INTEGER,
                    position_fraction REAL,
                    capital_at_entry REAL,
                    exit_reason TEXT,
                    outcome TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS performance_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT UNIQUE,
                    total_trades INTEGER,
                    wins INTEGER,
                    losses INTEGER,
                    inconclusive INTEGER,
                    win_rate REAL,
                    profit_factor REAL,
                    avg_win REAL,
                    avg_loss REAL,
                    expectancy REAL,
                    max_drawdown REAL,
                    sharpe_ratio REAL,
                    consecutive_losses INTEGER,
                    total_return REAL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER,
                    user_id TEXT,
                    message TEXT,
                    status TEXT DEFAULT 'PENDING',
                    created_at TEXT,
                    sent_at TEXT,
                    retry_count INTEGER DEFAULT 0,
                    last_error TEXT,
                    FOREIGN KEY (signal_id) REFERENCES signals_history(id)
                )
            """)
            if config.ADMIN_CHAT_ID:
                await db.execute("INSERT OR IGNORE INTO subscribers (user_id) VALUES (?)", (config.ADMIN_CHAT_ID,))
            await db.commit()
        logger.info(f"✅ SQLite قاعدة بيانات متصلة: {self.db_path}")
        return True

    async def close(self):
        self._closed = True
        if self._connection:
            await self._connection.close()
            self._connection = None

    async def get_connection(self):
        """الحصول على اتصال للمعاملات"""
        if self._connection is None or self._closed:
            self._connection = await aiosqlite.connect(self.db_path)
            await self._connection.execute("PRAGMA journal_mode=WAL")
            await self._connection.execute("PRAGMA synchronous=NORMAL")
        return self._connection

    async def execute(self, query: str, *args):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, args)
            await db.commit()
            return cursor

    async def fetch(self, query: str, *args):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, args)
            return await cursor.fetchall()

    async def fetchrow(self, query: str, *args):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, args)
            return await cursor.fetchone()

# ===================================================================
# 8. مستودع البيانات (Repository) - مع Atomic Commit و Outbox
# ===================================================================

class SignalRecord(NamedTuple):
    id: int
    symbol: str
    entry_time: datetime
    entry_price: float
    stop_loss: float
    take_profit: float
    signal_type: str
    sector: str
    position_fraction: float
    capital_at_entry: float
    status: str = "OPEN"

class Repository:
    def __init__(self, db: Database):
        self.db = db

    async def get_subscribers(self) -> List[str]:
        rows = await self.db.fetch("SELECT user_id FROM subscribers")
        return [r[0] for r in rows]

    async def add_subscriber(self, user_id: str) -> None:
        await self.db.execute("INSERT OR IGNORE INTO subscribers (user_id) VALUES (?)", user_id)

    async def remove_subscriber(self, user_id: str) -> None:
        await self.db.execute("DELETE FROM subscribers WHERE user_id = ?", user_id)

    async def get_pending(self) -> List[str]:
        rows = await self.db.fetch("SELECT user_id FROM pending")
        return [r[0] for r in rows]

    async def add_pending(self, user_id: str) -> None:
        await self.db.execute("INSERT OR IGNORE INTO pending (user_id) VALUES (?)", user_id)

    async def remove_pending(self, user_id: str) -> None:
        await self.db.execute("DELETE FROM pending WHERE user_id = ?", user_id)

    async def get_cooldown(self, symbol: str) -> Optional[str]:
        row = await self.db.fetchrow("SELECT last_signal_time FROM signal_cooldown WHERE symbol = ?", symbol)
        return row[0] if row else None

    async def commit_signal(self, symbol: str, signal_type: str, entry_price: float, 
                            stop_loss: float, take_profit: float, sector: str = "OTHER",
                            quality_score: int = 0, position_fraction: float = 0.02,
                            capital: float = 10000.0, cooldown_time: str = None) -> Optional[int]:
        """حفظ الإشارة و cooldown في معاملة ذرية واحدة"""
        try:
            conn = await self.db.get_connection()
            async with conn.cursor() as cursor:
                await cursor.execute("BEGIN IMMEDIATE")
                
                # إعادة فحص cooldown داخل المعاملة
                cursor2 = await conn.execute("SELECT last_signal_time FROM signal_cooldown WHERE symbol = ?", (symbol,))
                cooldown_row = await cursor2.fetchone()
                if cooldown_row:
                    last_time = datetime.fromisoformat(cooldown_row[0])
                    if (datetime.now(timezone.utc) - last_time) < timedelta(minutes=config.COOLDOWN_MINUTES):
                        await cursor.execute("ROLLBACK")
                        logger.info(f"⏳ {symbol}: cooldown نشط أثناء المعاملة")
                        return None
                
                # إعادة فحص عدد الصفقات المفتوحة
                cursor2 = await conn.execute("SELECT COUNT(*) FROM signals_history WHERE status = 'OPEN'")
                open_count = (await cursor2.fetchone())[0]
                if open_count >= config.MAX_OPEN_TRADES:
                    await cursor.execute("ROLLBACK")
                    logger.warning(f"⛔ {symbol}: تجاوز الحد الأقصى للصفقات المفتوحة أثناء المعاملة")
                    return None
                
                # حفظ الإشارة
                now_utc = datetime.now(timezone.utc).isoformat()
                await cursor.execute(
                    """INSERT INTO signals_history 
                       (symbol, timestamp, signal_type, entry_price, stop_loss, take_profit, 
                        entry_time, sector, quality_score, position_fraction, capital_at_entry) 
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    symbol, now_utc, signal_type, entry_price, stop_loss, take_profit,
                    now_utc, sector, quality_score, position_fraction, capital
                )
                signal_id = cursor.lastrowid
                
                # حفظ cooldown
                if cooldown_time:
                    await cursor.execute(
                        "INSERT OR REPLACE INTO signal_cooldown (symbol, last_signal_time) VALUES (?, ?)",
                        symbol, cooldown_time
                    )
                
                await cursor.execute("COMMIT")
                logger.info(f"✅ {symbol}: تم حفظ الإشارة (ID: {signal_id})")
                return signal_id
        except Exception as e:
            logger.error(f"❌ فشل حفظ الإشارة {symbol}: {e}")
            return None

    async def add_to_outbox(self, signal_id: int, user_ids: List[str], message: str) -> None:
        """إضافة إشعارات إلى الـ Outbox"""
        now_utc = datetime.now(timezone.utc).isoformat()
        for user_id in user_ids:
            await self.db.execute(
                "INSERT INTO notification_outbox (signal_id, user_id, message, created_at) VALUES (?, ?, ?, ?)",
                signal_id, user_id, message, now_utc
            )

    async def get_pending_notifications(self, limit: int = 50) -> List[Tuple]:
        rows = await self.db.fetch(
            "SELECT id, signal_id, user_id, message, retry_count FROM notification_outbox WHERE status = 'PENDING' ORDER BY created_at ASC LIMIT ?",
            limit
        )
        return rows

    async def mark_notification_sent(self, notification_id: int) -> None:
        now_utc = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE notification_outbox SET status = 'SENT', sent_at = ? WHERE id = ?",
            now_utc, notification_id
        )

    async def mark_notification_failed(self, notification_id: int, error: str) -> None:
        await self.db.execute(
            "UPDATE notification_outbox SET status = 'FAILED', retry_count = retry_count + 1, last_error = ? WHERE id = ?",
            error, notification_id
        )

    async def get_open_signals(self) -> List[SignalRecord]:
        rows = await self.db.fetch(
            """SELECT id, symbol, entry_time, entry_price, stop_loss, take_profit, signal_type, sector, position_fraction, capital_at_entry, status 
               FROM signals_history WHERE status = 'OPEN'"""
        )
        records = []
        for row in rows:
            records.append(SignalRecord(
                id=row[0],
                symbol=row[1],
                entry_time=datetime.fromisoformat(row[2]),
                entry_price=row[3],
                stop_loss=row[4],
                take_profit=row[5],
                signal_type=row[6],
                sector=row[7] if len(row) > 7 else "OTHER",
                position_fraction=row[8] if len(row) > 8 else 0.02,
                capital_at_entry=row[9] if len(row) > 9 else 10000.0,
                status=row[10] if len(row) > 10 else "OPEN"
            ))
        return records

    async def close_signal(self, signal_id: int, exit_reason: str, outcome: str,
                          exit_price: float, trade_return_percent: float,
                          portfolio_pnl_percent: float, pnl_usd: float,
                          duration_minutes: int, win: Optional[bool], exit_time: str) -> None:
        await self.db.execute(
            """UPDATE signals_history SET 
               status = 'CLOSED', exit_reason = ?, outcome = ?,
               exit_price = ?, trade_return_percent = ?, portfolio_pnl_percent = ?,
               pnl_usd = ?, duration_minutes = ?, win = ?, exit_time = ? 
               WHERE id = ? AND status = 'OPEN'""",
            exit_reason, outcome, exit_price, trade_return_percent,
            portfolio_pnl_percent, pnl_usd, duration_minutes, win, exit_time, signal_id
        )

    async def get_latest_performance(self):
        return await self.db.fetchrow(
            "SELECT total_trades, wins, losses, inconclusive, win_rate, profit_factor, avg_win, avg_loss, expectancy, max_drawdown, sharpe_ratio, consecutive_losses, total_return FROM performance_metrics ORDER BY id DESC LIMIT 1"
        )

    async def update_performance(self, metrics: dict) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        await self.db.execute(
            """
            INSERT OR REPLACE INTO performance_metrics 
            (date, total_trades, wins, losses, inconclusive, win_rate, profit_factor, avg_win, avg_loss, expectancy, max_drawdown, sharpe_ratio, consecutive_losses, total_return)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            today, metrics.get('total_trades', 0), metrics.get('wins', 0),
            metrics.get('losses', 0), metrics.get('inconclusive', 0),
            metrics.get('win_rate', 0.0), metrics.get('profit_factor', 0.0),
            metrics.get('avg_win', 0.0), metrics.get('avg_loss', 0.0),
            metrics.get('expectancy', 0.0), metrics.get('max_drawdown', 0.0),
            metrics.get('sharpe_ratio', 0.0), metrics.get('consecutive_losses', 0),
            metrics.get('total_return', 0.0)
        )

    async def get_daily_portfolio_loss(self) -> float:
        today = datetime.now(timezone.utc).date().isoformat()
        rows = await self.db.fetch(
            "SELECT portfolio_pnl_percent FROM signals_history WHERE outcome = 'LOSS' AND date(exit_time) = ?",
            today
        )
        daily_loss = 0.0
        for row in rows:
            if row[0] and row[0] < 0:
                daily_loss += abs(row[0])
        return daily_loss * 100

    async def get_consecutive_losses(self) -> int:
        rows = await self.db.fetch(
            "SELECT outcome FROM signals_history WHERE outcome IN ('WIN', 'LOSS') ORDER BY id DESC LIMIT ?",
            config.MAX_CONSECUTIVE_LOSSES
        )
        if not rows:
            return 0
        consecutive = 0
        for row in rows:
            if row[0] == 'LOSS':
                consecutive += 1
            else:
                break
        return consecutive

    async def get_equity_curve(self) -> List[float]:
        rows = await self.db.fetch(
            "SELECT portfolio_pnl_percent FROM signals_history WHERE outcome IN ('WIN', 'LOSS') ORDER BY exit_time"
        )
        equity = [config.INITIAL_CAPITAL]
        for row in rows:
            if row[0]:
                new_equity = equity[-1] * (1 + row[0] / 100)
                equity.append(new_equity)
        return equity
    
    async def get_daily_returns(self) -> List[float]:
        rows = await self.db.fetch(
            "SELECT exit_time, portfolio_pnl_percent FROM signals_history WHERE outcome IN ('WIN', 'LOSS') ORDER BY exit_time"
        )
        if not rows:
            return []
        daily_pnl = {}
        for row in rows:
            dt = datetime.fromisoformat(row[0]).date()
            daily_pnl[dt] = daily_pnl.get(dt, 0.0) + (row[1] if row[1] else 0.0)
        equity = config.INITIAL_CAPITAL
        returns = []
        for date in sorted(daily_pnl.keys()):
            pnl = daily_pnl[date]
            new_equity = equity * (1 + pnl / 100)
            daily_return = (new_equity - equity) / equity if equity > 0 else 0
            returns.append(daily_return)
            equity = new_equity
        return returns

# ===================================================================
# 9. المحركات المتقدمة (Advanced Engines) - مختصر
# ===================================================================

class UniverseEngine:
    def __init__(self, all_stats: List[Dict]):
        self.all_stats = all_stats
        self.core_symbols = config.CORE_UNIVERSE
    
    def build(self) -> List[str]:
        universe = set(self.core_symbols)
        candidates = []
        for item in self.all_stats:
            symbol = item.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            if symbol in universe:
                continue
            if any(stable in symbol for stable in config.MAX_STABLE_COINS):
                continue
            if any(ex in symbol for ex in config.EXCLUDED_SYMBOLS):
                continue
            volume = float(item.get("quoteVolume", 0))
            change = float(item.get("priceChangePercent", 0))
            high = float(item.get("highPrice", 0))
            low = float(item.get("lowPrice", 0))
            count = float(item.get("count", 0))
            if volume < config.MIN_VOLUME_USD:
                continue
            if count < config.MIN_TRADES_24H:
                continue
            if abs(change) < config.MIN_VOLATILITY_DAILY:
                continue
            volatility = (high - low) / low * 100 if low > 0 else 0
            score = (volume / 10_000_000) * 0.4 + (abs(change) / 10) * 0.3 + (volatility / 20) * 0.3
            candidates.append({"symbol": symbol, "score": score})
        candidates.sort(key=lambda x: x["score"], reverse=True)
        dynamic_selected = [c["symbol"] for c in candidates[:config.DYNAMIC_SIZE]]
        universe.update(dynamic_selected)
        return list(universe)[:config.MAX_UNIVERSE_SIZE]


class LiquidityRankingEngine:
    def __init__(self, all_stats: List[Dict]):
        self.all_stats = all_stats
        self.ranked = []
    
    def filter_and_rank(self) -> List[Dict]:
        candidates = []
        for item in self.all_stats:
            symbol = item.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            if any(stable in symbol for stable in config.MAX_STABLE_COINS):
                continue
            if any(ex in symbol for ex in config.EXCLUDED_SYMBOLS):
                continue
            volume = float(item.get("quoteVolume", 0))
            count = float(item.get("count", 0))
            change = float(item.get("priceChangePercent", 0))
            high = float(item.get("highPrice", 0))
            low = float(item.get("lowPrice", 0))
            if volume < config.MIN_VOLUME_USD:
                continue
            if count < config.MIN_TRADES_24H:
                continue
            if abs(change) < config.MIN_VOLATILITY_DAILY:
                continue
            volatility = (high - low) / low * 100 if low > 0 else 0
            momentum = abs(change)
            volume_expansion = volume / 1_000_000
            score = (volume / 100_000_000) * 0.25 + (momentum / 10) * 0.20 + (volume_expansion / 50) * 0.20 + (volatility / 20) * 0.15 + (abs(change) / 5) * 0.20
            candidates.append({"symbol": symbol, "volume": volume, "change": change, "volatility": volatility, "momentum": momentum, "count": count, "score": score, "raw": item})
        candidates.sort(key=lambda x: x["score"], reverse=True)
        self.ranked = candidates[:config.MAX_UNIVERSE_SIZE]
        return self.ranked


class MarketRegimeEngine:
    @staticmethod
    def detect(data_4h: CandleData, data_1h: CandleData) -> Dict:
        prices_4h = data_4h.closed_prices()
        prices_1h = data_1h.closed_prices()
        if len(prices_4h) < 50 or len(prices_1h) < 50:
            return {"regime": "NEUTRAL", "volatility": 0.0}
        sma20_4h = sum(prices_4h[-20:]) / 20
        sma50_4h = sum(prices_4h[-50:]) / 50
        current_4h = prices_4h[-1]
        if current_4h > sma20_4h and sma20_4h > sma50_4h:
            regime = "STRONG_TREND" if current_4h > sma20_4h * 1.05 else "WEAK_TREND"
        elif current_4h < sma20_4h and sma20_4h < sma50_4h:
            regime = "STRONG_TREND" if current_4h < sma20_4h * 0.95 else "WEAK_TREND"
        else:
            atr = Indicators.calculate_atr(data_4h.closed_highs(), data_4h.closed_lows(), prices_4h, 14)
            avg_price = sum(prices_4h[-20:]) / 20
            volatility_ratio = (atr / avg_price) * 100 if avg_price > 0 else 0
            if volatility_ratio > 3:
                regime = "HIGH_VOLATILITY"
            elif volatility_ratio < 1:
                regime = "LOW_VOLATILITY"
            else:
                regime = "RANGE"
        bb = Indicators.calculate_bollinger(prices_4h, 20, 2)
        volatility = (bb["upper"] - bb["lower"]) / bb["middle"] * 100 if bb["middle"] > 0 else 0
        sma20_1h = sum(prices_1h[-20:]) / 20
        sma50_1h = sum(prices_1h[-50:]) / 50
        current_1h = prices_1h[-1]
        trend_1h = 'bullish' if current_1h > sma20_1h and sma20_1h > sma50_1h else 'bearish' if current_1h < sma20_1h and sma20_1h < sma50_1h else 'neutral'
        return {"regime": regime, "volatility": volatility, "trend_1h": trend_1h}


class RelativeStrengthEngine:
    @staticmethod
    def calculate(symbol: str, change_24h: float, btc_change: float, eth_change: float) -> Dict:
        if symbol == "BTCUSDT":
            return {"btc_relative": 0.0, "eth_relative": 0.0}
        if symbol == "ETHUSDT":
            return {"btc_relative": change_24h - btc_change, "eth_relative": 0.0}
        return {
            "btc_relative": change_24h - btc_change,
            "eth_relative": change_24h - eth_change
        }


class SectorRotationEngine:
    def __init__(self, symbols_stats: List[Tuple[str, MarketStats]]):
        self.sector_scores = {}
        self._calculate(symbols_stats)
    
    def _calculate(self, symbols_stats: List[Tuple[str, MarketStats]]):
        sector_performance = {}
        for sector, members in config.SECTORS.items():
            total_change = 0
            count = 0
            for sym, stats in symbols_stats:
                if any(member in sym for member in members):
                    total_change += stats.change_24h
                    count += 1
            if count > 0:
                sector_performance[sector] = total_change / count
        sorted_sectors = sorted(sector_performance.items(), key=lambda x: x[1], reverse=True)
        self.sector_scores = {sector: idx for idx, (sector, _) in enumerate(sorted_sectors)}
    
    def get_priority(self, symbol: str) -> int:
        for sector, members in config.SECTORS.items():
            if any(member in symbol for member in members):
                return self.sector_scores.get(sector, 999)
        return 999


class CorrelationFilter:
    @staticmethod
    def get_sector(symbol: str) -> str:
        for sector, members in config.SECTORS.items():
            if symbol in members:
                return sector
        return "OTHER"
    
    @staticmethod
    def is_allowed(symbol: str, open_signals: List[SignalRecord]) -> Tuple[bool, str]:
        sector = CorrelationFilter.get_sector(symbol)
        if sector == "OTHER":
            return True, ""
        sector_count = 0
        for signal in open_signals:
            if signal.sector == sector:
                sector_count += 1
        if sector_count >= config.MAX_SECTOR_EXPOSURE:
            return False, f"قطاع {sector} لديه {sector_count} صفقات مفتوحة (الحد {config.MAX_SECTOR_EXPOSURE})"
        return True, ""


class PortfolioRiskEngine:
    def __init__(self, repo: Repository):
        self.repo = repo
        self.capital = config.INITIAL_CAPITAL
    
    async def get_current_capital(self) -> float:
        equity_curve = await self.repo.get_equity_curve()
        return equity_curve[-1] if equity_curve else config.INITIAL_CAPITAL
    
    async def check_daily_loss_limit(self) -> Tuple[bool, float]:
        daily_loss = await self.repo.get_daily_portfolio_loss()
        if daily_loss >= config.DAILY_LOSS_LIMIT_PCT:
            logger.warning(f"⚠️ حد الخسارة اليومي {config.DAILY_LOSS_LIMIT_PCT}% تم تجاوزه ({daily_loss:.2f}%)")
            return False, daily_loss
        return True, daily_loss
    
    async def check_consecutive_losses(self) -> Tuple[bool, int]:
        consecutive = await self.repo.get_consecutive_losses()
        if consecutive >= config.MAX_CONSECUTIVE_LOSSES:
            logger.warning(f"⚠️ {consecutive} خسائر متتالية، تقليل المخاطرة")
            return False, consecutive
        return True, consecutive
    
    async def get_open_trades_count(self) -> int:
        rows = await self.repo.db.fetch("SELECT id FROM signals_history WHERE status = 'OPEN'")
        return len(rows)
    
    def calculate_position_size(self, stop_loss_percent: float) -> float:
        if stop_loss_percent <= 0:
            return 0.0
        risk_amount = config.RISK_PER_TRADE_PCT / 100
        position_fraction = risk_amount / (stop_loss_percent / 100)
        return min(position_fraction, config.MAX_POSITION_PCT / 100)
    
    async def can_trade(self) -> Tuple[bool, str]:
        open_count = await self.get_open_trades_count()
        if open_count >= config.MAX_OPEN_TRADES:
            return False, f"الحد الأقصى للصفقات المفتوحة ({config.MAX_OPEN_TRADES}) تم الوصول إليه"
        daily_ok, daily_loss = await self.check_daily_loss_limit()
        if not daily_ok:
            return False, f"تم تجاوز حد الخسارة اليومي ({daily_loss:.2f}%)"
        consec_ok, consecutive = await self.check_consecutive_losses()
        if not consec_ok:
            return False, f"{consecutive} خسائر متتالية"
        return True, ""

# ===================================================================
# 10. محرك الاستراتيجية (StrategyEngine)
# ===================================================================

class StrategyEngine:
    def __init__(self, symbol: str, data_5m: CandleData, data_1h: CandleData, data_4h: CandleData, stats: MarketStats):
        self.symbol = symbol
        self.data_5m = data_5m
        self.data_1h = data_1h
        self.data_4h = data_4h
        self.stats = stats
        self.action = None
        self._setup()

    def _setup(self):
        self.prices_5m = self.data_5m.closed_prices()
        self.highs_5m = self.data_5m.closed_highs()
        self.lows_5m = self.data_5m.closed_lows()
        self.volumes_5m = self.data_5m.closed_volumes()
        self.current_price = self.data_5m.get_current_price()
        self.ref_price = self.data_5m.get_reference_price()
        self.rsi = Indicators.calculate_rsi(self.prices_5m, config.RSI_PERIOD)
        self.adx = Indicators.calculate_adx(self.highs_5m, self.lows_5m, self.prices_5m, config.ADX_PERIOD)
        self.atr = Indicators.calculate_atr(self.highs_5m, self.lows_5m, self.prices_5m, 14)
        self.macd = Indicators.calculate_macd(self.prices_5m)
        self.bb = Indicators.calculate_bollinger(self.prices_5m)
        self.structure = MarketStructure(self.data_5m)
        self.trend = self.structure.get_trend()
        self.last_swing_high = self.structure.get_last_swing_high()
        self.last_swing_low = self.structure.get_last_swing_low()
        self.directional_bias = self._calculate_directional_bias()
        self.regime_data = MarketRegimeEngine.detect(self.data_4h, self.data_1h)
        self.market_regime = self.regime_data["regime"]
        self.trend_1h = self.regime_data.get("trend_1h", "neutral")
        self.change_1h = self._calculate_change_1h()
        self.volume_ratio = self._calculate_volume_ratio()
        self.quality_score = 0
        self.reasons = []

    def _calculate_directional_bias(self) -> str:
        prices_1h = self.data_1h.closed_prices()
        prices_4h = self.data_4h.closed_prices()
        if len(prices_1h) < 50 or len(prices_4h) < 50:
            return 'neutral'
        sma20_1h = sum(prices_1h[-20:]) / 20
        sma50_1h = sum(prices_1h[-50:]) / 50
        sma20_4h = sum(prices_4h[-20:]) / 20
        sma50_4h = sum(prices_4h[-50:]) / 50
        current_1h = prices_1h[-1]
        current_4h = prices_4h[-1]
        bullish_1h = current_1h > sma20_1h and sma20_1h > sma50_1h
        bullish_4h = current_4h > sma20_4h and sma20_4h > sma50_4h
        bearish_1h = current_1h < sma20_1h and sma20_1h < sma50_1h
        bearish_4h = current_4h < sma20_4h and sma20_4h < sma50_4h
        if bullish_1h and bullish_4h: return 'bullish'
        if bearish_1h and bearish_4h: return 'bearish'
        return 'neutral'

    def _calculate_change_1h(self) -> float:
        if len(self.prices_5m) < 13:
            return 0.0
        old_price = self.prices_5m[-13]
        if old_price == 0:
            return 0.0
        return ((self.ref_price - old_price) / old_price) * 100

    def _calculate_volume_ratio(self) -> float:
        if len(self.volumes_5m) < 20:
            return 0.0
        prev_vol = sorted(self.volumes_5m[-20:-1])
        median_vol = prev_vol[len(prev_vol)//2] if prev_vol else 1.0
        curr_vol = self.volumes_5m[-1] if self.volumes_5m else 0.0
        return curr_vol / median_vol if median_vol > 0 else 0.0

    def _calculate_quality_score(self, stop_loss: float = 0.0, take_profit: float = 0.0) -> int:
        score = 0
        if self.directional_bias != 'neutral':
            score += 20
        elif self.trend_1h != 'neutral':
            score += 10
        if self.trend != 'neutral':
            score += 15
        if abs(self.change_1h) > 1.0:
            score += 15
        elif abs(self.change_1h) > 0.5:
            score += 8
        if self.volume_ratio > 2.0:
            score += 15
        elif self.volume_ratio > 1.5:
            score += 8
        if self.adx > 30:
            score += 10
        elif self.adx > 25:
            score += 5
        if 40 <= self.rsi <= 60:
            score += 5
        if stop_loss > 0 and take_profit > 0 and self.current_price > 0:
            if self.action == 'BUY':
                rr_ratio = (take_profit - self.current_price) / (self.current_price - stop_loss)
            else:
                rr_ratio = (self.current_price - take_profit) / (stop_loss - self.current_price)
            if rr_ratio >= 2.0:
                score += 10
            elif rr_ratio >= 1.5:
                score += 5
        if self.market_regime in ["STRONG_TREND", "WEAK_TREND"]:
            score += 5
        return min(score, 100)

    def generate_signal(self, risk_engine: PortfolioRiskEngine):
        atr_pct = (self.atr / self.current_price) * 100 if self.current_price > 0 else 0
        dynamic_min_change = max(config.MIN_CHANGE_1H, atr_pct * 0.5)
        
        if abs(self.change_1h) < dynamic_min_change:
            return self._signal_result("WATCH", [f"التغير خلال ساعة {self.change_1h:.2f}% أقل من الحد الأدنى {dynamic_min_change:.2f}%"])
        if self.adx < config.MIN_ADX_STRONG:
            return self._signal_result("NO_TRADE", ["السوق ليس اتجاهياً (ADX ضعيف)"])
        if self.directional_bias == 'neutral':
            return self._signal_result("NO_TRADE", ["الاتجاه غير واضح"])
        if self.market_regime == "RANGE" and self.trend != 'neutral':
            return self._signal_result("WATCH", ["السوق في نطاق جانبي، إشارات الاختراق غير موثوقة"])
        volatility_multiplier = 1.2 if self.market_regime == "HIGH_VOLATILITY" else 1.0
        if self.trend == 'neutral':
            return self._signal_result("WATCH", ["هيكل السوق محايد"])
        if self.rsi > 80 or self.rsi < 20:
            return self._signal_result("WATCH", ["RSI متطرف"])
        if self.directional_bias == 'bullish':
            if not (40 <= self.rsi <= 70 and self.macd['histogram'] > 0):
                return self._signal_result("WATCH", ["ظروف الشراء غير مكتملة"])
            if self.trend != 'bullish':
                return self._signal_result("WATCH", ["الهيكل لا يدعم الشراء"])
            self.action = "BUY"
        else:
            if not (30 <= self.rsi <= 60 and self.macd['histogram'] < 0):
                return self._signal_result("WATCH", ["ظروف البيع غير مكتملة"])
            if self.trend != 'bearish':
                return self._signal_result("WATCH", ["الهيكل لا يدعم البيع"])
            self.action = "SELL"
        if self.volume_ratio < 1.5:
            return self._signal_result("WATCH", ["حجم ضعيف"])
        stop_loss, take_profit = self._calculate_risk(self.action, volatility_multiplier)
        if stop_loss == 0.0 or take_profit == 0.0:
            return self._signal_result("NO_TRADE", ["إدارة المخاطر غير صالحة"])
        stop_loss_percent = abs((stop_loss - self.current_price) / self.current_price) * 100
        position_fraction = risk_engine.calculate_position_size(stop_loss_percent)
        if position_fraction <= 0:
            return self._signal_result("NO_TRADE", ["حجم المركز غير صالح"])
        self.entry_price = self.current_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.quality_score = self._calculate_quality_score(stop_loss, take_profit)
        if self.quality_score < config.MIN_SIGNAL_SCORE:
            return self._signal_result("WATCH", [f"درجة الجودة {self.quality_score} أقل من الحد الأدنى {config.MIN_SIGNAL_SCORE}"])
        reasons = [
            f"اتجاه 4H: {self.directional_bias}",
            f"اتجاه 1H: {self.trend_1h}",
            f"هيكل 5M: {self.trend}",
            f"RSI: {self.rsi:.1f}",
            f"MACD Histogram: {self.macd['histogram']:.4f}",
            f"حجم: {self.volume_ratio:.2f}x",
            f"ADX: {self.adx:.1f}",
        ]
        return self._signal_result(self.action, reasons, stop_loss, take_profit, position_fraction, self.quality_score)

    def _signal_result(self, action: str, reasons: List[str], stop_loss: float = 0.0, take_profit: float = 0.0, position_fraction: float = 0.0, quality_score: int = 0):
        return {
            "symbol": self.symbol,
            "action": action,
            "entry_price": self.current_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "position_fraction": position_fraction,
            "position_percent": position_fraction * 100,
            "quality_score": quality_score,
            "reasons": reasons,
            "adx": self.adx,
            "rsi": self.rsi,
            "market_regime": self.market_regime,
            "directional_bias": self.directional_bias,
            "structure": self.trend,
            "is_actionable": action in ("BUY", "SELL")
        }

    def _calculate_risk(self, action: str, volatility_multiplier: float = 1.0) -> Tuple[float, float]:
        if action not in ('BUY', 'SELL'):
            return 0.0, 0.0
        min_stop_pct = 0.01
        atr_stop = self.atr * 2 * volatility_multiplier if self.atr > 0 else self.current_price * 0.015
        max_stop_distance = max(atr_stop, self.current_price * min_stop_pct)
        if action == 'BUY' and self.last_swing_low is not None:
            stop_loss = self.last_swing_low - self.atr * 0.25 * volatility_multiplier
            if self.current_price - stop_loss < max_stop_distance:
                stop_loss = self.current_price - max_stop_distance
            if (self.current_price - stop_loss) / self.current_price < min_stop_pct:
                stop_loss = self.current_price * (1 - min_stop_pct)
            if stop_loss > self.current_price * 0.98:
                return 0.0, 0.0
            take_profit = self.current_price + (self.current_price - stop_loss) * config.MIN_RISK_REWARD_RATIO
            return stop_loss, take_profit
        elif action == 'SELL' and self.last_swing_high is not None:
            stop_loss = self.last_swing_high + self.atr * 0.25 * volatility_multiplier
            if stop_loss - self.current_price < max_stop_distance:
                stop_loss = self.current_price + max_stop_distance
            if (stop_loss - self.current_price) / self.current_price < min_stop_pct:
                stop_loss = self.current_price * (1 + min_stop_pct)
            if stop_loss < self.current_price * 1.02:
                return 0.0, 0.0
            take_profit = self.current_price - (stop_loss - self.current_price) * config.MIN_RISK_REWARD_RATIO
            return stop_loss, take_profit
        return 0.0, 0.0

# ===================================================================
# 11. مقدم البيانات (DataProvider)
# ===================================================================

class DataProvider:
    def __init__(self):
        self._session = None
        self._client = None
        self._rate_limiter = BinanceRateLimiter()
        self._stats_cache = None
        self._stats_cache_time = 0
        self._cache_ttl = 300

    async def _ensure_client(self):
        if self._session is None:
            self._session = aiohttp.ClientSession()
        if self._client is None:
            self._client = BinanceClient(self._session, self._rate_limiter)
        return self._client

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None
            self._client = None

    async def fetch_klines(self, symbol: str, interval: str = '5m', limit: int = 100) -> Optional[CandleData]:
        client = await self._ensure_client()
        return await client.get_klines(symbol, interval, limit)

    async def fetch_stats(self, symbol: str) -> Optional[MarketStats]:
        client = await self._ensure_client()
        return await client.get_24hr_stats(symbol)

    async def get_all_24hr_stats(self) -> Optional[List[Dict]]:
        now = time.time()
        if self._stats_cache is not None and (now - self._stats_cache_time) < self._cache_ttl:
            return self._stats_cache
        client = await self._ensure_client()
        data = await client.get_all_24hr_stats()
        if data:
            self._stats_cache = data
            self._stats_cache_time = now
        return data

    async def filter_symbols(self) -> List[str]:
        all_stats = await self.get_all_24hr_stats()
        if not all_stats:
            return []
        universe_engine = UniverseEngine(all_stats)
        universe = universe_engine.build()
        filtered_stats = []
        for item in all_stats:
            if item.get("symbol") in universe:
                filtered_stats.append(item)
        ranking_engine = LiquidityRankingEngine(filtered_stats)
        ranked = ranking_engine.filter_and_rank()
        if not ranked:
            return []
        btc_stats = await self.fetch_stats("BTCUSDT")
        eth_stats = await self.fetch_stats("ETHUSDT")
        btc_change = btc_stats.change_24h if btc_stats else 0.0
        eth_change = eth_stats.change_24h if eth_stats else 0.0
        stats_list = []
        for item in ranked:
            stats = MarketStats(volume=item["volume"], change_24h=item["change"], high=0.0, low=0.0, open=0.0, last=0.0)
            stats_list.append((item["symbol"], stats))
        sector_engine = SectorRotationEngine(stats_list)
        for item in ranked:
            rel = RelativeStrengthEngine.calculate(item["symbol"], item["change"], btc_change, eth_change)
            item["btc_relative"] = max(rel["btc_relative"], 0.0)
            item["eth_relative"] = max(rel["eth_relative"], 0.0)
            item["sector_priority"] = sector_engine.get_priority(item["symbol"])
        for item in ranked:
            sector_bonus = (1 - item["sector_priority"] / 10) if item["sector_priority"] < 999 else 0
            item["final_score"] = item["score"] * 0.60 + item["btc_relative"] * 0.15 + item["eth_relative"] * 0.10 + sector_bonus * 0.15
        ranked.sort(key=lambda x: x["final_score"], reverse=True)
        return [item["symbol"] for item in ranked[:config.MAX_UNIVERSE_SIZE]]

# ===================================================================
# 12. الماسح الضوئي (Scanner) - مع Outbox
# ===================================================================

class Scanner:
    def __init__(self, provider: DataProvider, repo: Repository, bot_app: Optional[Application] = None):
        self.provider = provider
        self.repo = repo
        self.bot_app = bot_app
        self.last_filter_time = 0
        self.dynamic_watch_list = []
        self.is_running = False
        self.risk_engine = PortfolioRiskEngine(repo)

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        while self.is_running:
            try:
                await self._scan()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Scanner error: {e}")
            await asyncio.sleep(config.SCAN_INTERVAL_SECONDS)

    async def stop(self):
        self.is_running = False

    async def _scan(self):
        try:
            new_list = await self.provider.filter_symbols()
            if new_list:
                self.dynamic_watch_list = new_list
                self.last_filter_time = time.time()
                logger.info(f"🔍 Updated watchlist: {len(self.dynamic_watch_list)} symbols")
        except Exception as e:
            logger.error(f"Error updating watchlist: {e}")

        all_symbols = list(set(self.dynamic_watch_list))
        if not all_symbols:
            return
        logger.info(f"🔄 Scanning {len(all_symbols)} symbols...")
        sem = asyncio.Semaphore(config.REQUEST_BURST)
        tasks = [self._process_symbol(sym, sem) for sym in all_symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception):
                logger.error(f"Symbol processing error: {res}")

    async def _process_symbol(self, symbol: str, sem: asyncio.Semaphore):
        async with sem:
            # == المرحلة 1: تحليل موازي (بدون قفل) ==
            cooldown = await self.repo.get_cooldown(symbol)
            if cooldown:
                last_time = datetime.fromisoformat(cooldown)
                if (datetime.now(timezone.utc) - last_time) < timedelta(minutes=config.COOLDOWN_MINUTES):
                    return None

            data_5m = await self.provider.fetch_klines(symbol, '5m', 100)
            data_1h = await self.provider.fetch_klines(symbol, '1h', 100)
            data_4h = await self.provider.fetch_klines(symbol, '4h', 60)
            stats = await self.provider.fetch_stats(symbol)
            if not all([data_5m, data_1h, data_4h, stats]) or stats.volume < config.MIN_VOLUME_USD:
                return None

            engine = StrategyEngine(symbol, data_5m, data_1h, data_4h, stats)
            signal = engine.generate_signal(self.risk_engine)
            if not signal['is_actionable']:
                return None
            if signal['stop_loss'] == 0.0 or signal['take_profit'] == 0.0:
                return None

            # == المرحلة 2: التنفيذ الذري (معاملة DB) ==
            sector = CorrelationFilter.get_sector(symbol)
            capital = await self.risk_engine.get_current_capital()
            cooldown_time = datetime.now(timezone.utc).isoformat()
            
            signal_id = await self.repo.commit_signal(
                signal['symbol'], signal['action'],
                signal['entry_price'], signal['stop_loss'], signal['take_profit'],
                sector, signal.get('quality_score', 0),
                signal.get('position_fraction', 0.02), capital, cooldown_time
            )
            
            if signal_id is None:
                return None

            # == المرحلة 3: إضافة إلى Outbox (خارج المعاملة) ==
            subscribers = await self.repo.get_subscribers()
            if subscribers:
                msg = self._build_message(signal)
                await self.repo.add_to_outbox(signal_id, subscribers, msg)

            logger.info(f"✅ Signal generated for {symbol}: {signal['action']} (Score: {signal.get('quality_score', 0)})")
            return signal

    def _build_message(self, signal: dict) -> str:
        symbol = html.escape(signal['symbol'])
        action = html.escape(signal['action'])
        regime = html.escape(signal.get('market_regime', 'N/A'))
        quality = signal.get('quality_score', 0)
        if quality >= config.SCORE_ELITE:
            quality_label = "🌟 ELITE"
        elif quality >= config.SCORE_STRONG:
            quality_label = "💪 STRONG"
        elif quality >= config.SCORE_GOOD:
            quality_label = "✅ GOOD"
        else:
            quality_label = "⚠️ WATCH"
        reasons = [html.escape(str(r)) for r in signal['reasons'][:5]]
        reasons_text = "\n".join(f"• {r}" for r in reasons)
        return (
            f"<b>🚨 إشارة تداول جديدة</b>\n\n"
            f"<b>📊 العملة:</b> <code>{symbol}</code>\n"
            f"<b>⚡ الإجراء:</b> <code>{action}</code>\n"
            f"<b>💰 سعر الدخول:</b> <code>{signal['entry_price']:.4f}</code>\n"
            f"<b>🛑 وقف الخسارة:</b> <code>{signal['stop_loss']:.4f}</code>\n"
            f"<b>🎯 جني الأرباح:</b> <code>{signal['take_profit']:.4f}</code>\n"
            f"<b>📊 حجم المركز:</b> <code>{signal.get('position_percent', 0):.2f}%</code>\n"
            f"<b>⭐ الجودة:</b> <code>{quality}/100</code> {quality_label}\n"
            f"<b>📈 ADX:</b> {signal['adx']:.1f} | <b>RSI:</b> {signal['rsi']:.1f}\n"
            f"<b>📉 نظام السوق:</b> <code>{regime}</code>\n"
            f"<b>📝 الأسباب:</b>\n{reasons_text}"
        )

# ===================================================================
# 13. Outbox Worker (معالج الإشعارات)
# ===================================================================

class OutboxWorker:
    def __init__(self, repo: Repository, bot_app: Application):
        self.repo = repo
        self.bot_app = bot_app
        self.is_running = False

    async def start(self):
        self.is_running = True
        while self.is_running:
            try:
                await self._process()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Outbox error: {e}")
            await asyncio.sleep(5)

    async def stop(self):
        self.is_running = False

    async def _process(self):
        pending = await self.repo.get_pending_notifications(limit=20)
        if not pending:
            return
        for notif_id, signal_id, user_id, message, retry_count in pending:
            try:
                await self.bot_app.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode="HTML"
                )
                await self.repo.mark_notification_sent(notif_id)
                logger.info(f"📨 تم إرسال الإشارة (ID: {signal_id}) للمستخدم {user_id}")
            except Exception as e:
                # حذف المستخدمين المحظورين
                if "Forbidden" in str(e) or "bot was blocked" in str(e):
                    await self.repo.remove_subscriber(str(user_id))
                    await self.repo.mark_notification_sent(notif_id)  # تجاهل
                    logger.warning(f"🚫 المستخدم {user_id} محظور، تم حذفه")
                else:
                    await self.repo.mark_notification_failed(notif_id, str(e))
                    logger.warning(f"⚠️ فشل إرسال الإشعار {notif_id}: {e}")

# ===================================================================
# 14. المتتبع (Tracker) - مع فجوات البيانات و timezone
# ===================================================================

class Tracker:
    def __init__(self, provider: DataProvider, repo: Repository):
        self.provider = provider
        self.repo = repo
        self.is_running = False

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        while self.is_running:
            try:
                await self._track()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Tracker error: {e}")
            await asyncio.sleep(60)

    async def stop(self):
        self.is_running = False

    async def _track(self):
        open_signals = await self.repo.get_open_signals()
        if not open_signals:
            return
        
        for signal in open_signals:
            if signal.status != "OPEN":
                continue
                
            entry_time = signal.entry_time.astimezone(timezone.utc)
            now = datetime.now(timezone.utc)
            
            minutes_diff = math.ceil((now - entry_time).total_seconds() / 60)
            minutes_needed = max(5, minutes_diff + 5)
            max_minutes = config.MAX_TRADE_DURATION_HOURS * 60 + 10
            limit = min(minutes_needed, max_minutes)
            
            data_1m = await self.provider.fetch_klines(signal.symbol, '1m', limit)
            if not data_1m or len(data_1m.prices) < 2:
                continue
            
            timestamps = [ts.astimezone(timezone.utc) for ts in data_1m.timestamps]
            
            # التحقق من تغطية وقت الدخول
            first_timestamp = timestamps[0]
            last_timestamp = timestamps[-1]
            
            if first_timestamp > entry_time:
                logger.warning(f"⚠️ {signal.symbol}: البيانات لا تغطي وقت الدخول")
                data_1m = await self.provider.fetch_klines(signal.symbol, '1m', limit + 30)
                if not data_1m:
                    continue
                timestamps = [ts.astimezone(timezone.utc) for ts in data_1m.timestamps]
                first_timestamp = timestamps[0]
                if first_timestamp > entry_time:
                    continue
            
            # التحقق من فجوات البيانات
            gap_detected = False
            for prev_ts, curr_ts in zip(timestamps, timestamps[1:]):
                if curr_ts - prev_ts > timedelta(minutes=2):
                    logger.warning(f"⚠️ {signal.symbol}: فجوة بيانات بين {prev_ts} و {curr_ts}")
                    gap_detected = True
                    break
            if gap_detected:
                continue
            
            if last_timestamp < now - timedelta(minutes=2):
                logger.warning(f"⚠️ {signal.symbol}: البيانات متأخرة")
                continue
            
            # First-hit logic
            exit_reason = None
            outcome = None
            exit_price = None
            exit_time = None
            trade_return_percent = 0.0
            portfolio_pnl_percent = 0.0
            pnl_usd = 0.0
            win = None
            
            for i in range(len(timestamps)):
                if timestamps[i] < entry_time:
                    continue
                high = data_1m.highs[i]
                low = data_1m.lows[i]
                
                if signal.signal_type == 'BUY':
                    if low <= signal.stop_loss and high >= signal.take_profit:
                        exit_reason = "INCONCLUSIVE"
                        outcome = "INCONCLUSIVE"
                        exit_price = (signal.stop_loss + signal.take_profit) / 2
                        exit_time = timestamps[i]
                        break
                    elif low <= signal.stop_loss:
                        exit_reason = "SL"
                        outcome = "LOSS"
                        exit_price = signal.stop_loss
                        exit_time = timestamps[i]
                        break
                    elif high >= signal.take_profit:
                        exit_reason = "TP"
                        outcome = "WIN"
                        exit_price = signal.take_profit
                        exit_time = timestamps[i]
                        break
                else:  # SELL
                    if high >= signal.stop_loss and low <= signal.take_profit:
                        exit_reason = "INCONCLUSIVE"
                        outcome = "INCONCLUSIVE"
                        exit_price = (signal.stop_loss + signal.take_profit) / 2
                        exit_time = timestamps[i]
                        break
                    elif high >= signal.stop_loss:
                        exit_reason = "SL"
                        outcome = "LOSS"
                        exit_price = signal.stop_loss
                        exit_time = timestamps[i]
                        break
                    elif low <= signal.take_profit:
                        exit_reason = "TP"
                        outcome = "WIN"
                        exit_price = signal.take_profit
                        exit_time = timestamps[i]
                        break
            
            # TIME_EXIT
            if exit_time is None:
                duration_hours = (now - entry_time).total_seconds() / 3600
                if duration_hours >= config.MAX_TRADE_DURATION_HOURS:
                    exit_time = now
                    exit_price = data_1m.get_current_price()
                    exit_reason = "TIME"
                    trade_return_percent = ((exit_price - signal.entry_price) / signal.entry_price) * 100 if signal.signal_type == 'BUY' else ((signal.entry_price - exit_price) / signal.entry_price) * 100
                    outcome = "WIN" if trade_return_percent > 0 else "LOSS" if trade_return_percent < 0 else "BREAKEVEN"
                    win = outcome == "WIN"
                else:
                    continue
            
            if exit_reason is None:
                continue
            
            # حساب PnL
            if outcome != "INCONCLUSIVE":
                position_fraction = signal.position_fraction if signal.position_fraction is not None else config.MAX_POSITION_PCT / 100
                capital_at_entry = signal.capital_at_entry if signal.capital_at_entry is not None else config.INITIAL_CAPITAL
                trade_return_percent = ((exit_price - signal.entry_price) / signal.entry_price) * 100 if signal.signal_type == 'BUY' else ((signal.entry_price - exit_price) / signal.entry_price) * 100
                portfolio_pnl_percent = trade_return_percent * position_fraction
                pnl_usd = capital_at_entry * position_fraction * (trade_return_percent / 100)
                win = outcome == "WIN"
            
            duration_minutes = int((exit_time - entry_time).total_seconds() / 60)
            
            await self.repo.close_signal(
                signal.id, exit_reason, outcome,
                exit_price, trade_return_percent,
                portfolio_pnl_percent, pnl_usd,
                duration_minutes, win, exit_time.isoformat()
            )
            
            await self._update_performance()
            logger.info(f"✅ Signal {signal.id} ({signal.symbol}) closed: {exit_reason} ({outcome}) (Trade: {trade_return_percent:.2f}%, Portfolio: {portfolio_pnl_percent:.2f}%)")

    async def _update_performance(self):
        # إحصائيات الصفقات الحاسمة فقط (استبعاد INCONCLUSIVE)
        stats = await self.repo.db.fetchrow(
            """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN outcome = 'INCONCLUSIVE' THEN 1 ELSE 0 END) as inconclusive,
                AVG(CASE WHEN outcome = 'WIN' THEN trade_return_percent END) as avg_win,
                AVG(CASE WHEN outcome = 'LOSS' THEN trade_return_percent END) as avg_loss,
                SUM(CASE WHEN outcome = 'WIN' THEN portfolio_pnl_percent ELSE 0 END) as gross_profit,
                SUM(CASE WHEN outcome = 'LOSS' THEN ABS(portfolio_pnl_percent) ELSE 0 END) as gross_loss
            FROM signals_history WHERE outcome IN ('WIN', 'LOSS', 'INCONCLUSIVE')
            """
        )
        if not stats or stats[0] == 0:
            return
        
        total, wins, losses, inconclusive, avg_win, avg_loss, gross_profit, gross_loss = stats
        decisive_total = total - inconclusive
        
        if decisive_total > 0:
            win_rate = wins / decisive_total if decisive_total > 0 else 0.0
            if gross_loss > 0:
                profit_factor = gross_profit / gross_loss
            else:
                profit_factor = None
            expectancy = (win_rate * (avg_win or 0)) - ((1 - win_rate) * abs(avg_loss or 0))
        else:
            win_rate = 0.0
            profit_factor = None
            expectancy = 0.0
        
        equity_curve = await self.repo.get_equity_curve()
        if len(equity_curve) < 2:
            max_drawdown = 0.0
            total_return = 0.0
            sharpe_ratio = 0.0
        else:
            peak = equity_curve[0]
            max_drawdown = 0.0
            for eq in equity_curve:
                if eq > peak:
                    peak = eq
                drawdown = (peak - eq) / peak if peak > 0 else 0
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
            total_return = ((equity_curve[-1] - equity_curve[0]) / equity_curve[0]) * 100 if equity_curve[0] > 0 else 0
            daily_returns = await self.repo.get_daily_returns()
            sharpe_ratio = 0.0
            if len(daily_returns) > 5:
                avg_return = sum(daily_returns) / len(daily_returns)
                variance = sum((r - avg_return) ** 2 for r in daily_returns) / len(daily_returns)
                std_dev = math.sqrt(variance) if variance > 0 else 0.01
                sharpe_ratio = (avg_return / std_dev) * math.sqrt(365) if std_dev > 0 else 0.0
        
        consecutive_losses = await self.repo.get_consecutive_losses()
        
        metrics = {
            'total_trades': total,
            'wins': wins or 0,
            'losses': losses or 0,
            'inconclusive': inconclusive or 0,
            'win_rate': win_rate,
            'profit_factor': profit_factor if profit_factor is not None else 0.0,
            'avg_win': avg_win or 0.0,
            'avg_loss': avg_loss or 0.0,
            'expectancy': expectancy,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio,
            'consecutive_losses': consecutive_losses,
            'total_return': total_return
        }
        await self.repo.update_performance(metrics)
        logger.info(f"📈 أداء: {wins}/{decisive_total} ربح ({win_rate*100:.1f}%) | عائد: {total_return:.2f}% | MaxDD: {max_drawdown*100:.1f}%")

# ===================================================================
# 15. أوامر التليجرام (CommandHandlers)
# ===================================================================

class CommandHandlers:
    def __init__(self, repo):
        self.repo = repo

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        subscribers = await self.repo.get_subscribers()
        pending = await self.repo.get_pending()
        if user_id in subscribers:
            await update.message.reply_text("ℹ️ أنت مشترك بالفعل.")
            return
        if user_id in pending:
            await update.message.reply_text("⏳ طلبك قيد الانتظار.")
            return
        await self.repo.add_pending(user_id)
        await update.message.reply_text("✅ تم استلام طلب الاشتراك. سيتم الموافقة عليه من قبل المالك.")
        if config.ADMIN_CHAT_ID:
            await context.bot.send_message(chat_id=config.ADMIN_CHAT_ID, text=f"📩 طلب اشتراك جديد: <code>{user_id}</code>\n/approve {user_id}", parse_mode="HTML")

    async def approve(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_user.id) != str(config.ADMIN_CHAT_ID):
            await update.message.reply_text("⛔ فقط للمالك.")
            return
        if not context.args:
            await update.message.reply_text("⚠️ استخدم: /approve USER_ID")
            return
        user_id = context.args[0].strip()
        pending = await self.repo.get_pending()
        if user_id in pending:
            await self.repo.remove_pending(user_id)
            await self.repo.add_subscriber(user_id)
            await update.message.reply_text(f"✅ تمت الموافقة على <code>{user_id}</code>.", parse_mode="HTML")
            try:
                await context.bot.send_message(chat_id=user_id, text="🎉 تمت الموافقة على اشتراكك!")
            except:
                pass
        else:
            await update.message.reply_text("❌ غير موجود في قائمة الانتظار.")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        subscribers = await self.repo.get_subscribers()
        pending = await self.repo.get_pending()
        open_signals = await self.repo.get_open_signals()
        open_count = len(open_signals)
        await update.message.reply_text(
            f"📊 <b>حالة البوت</b>\n"
            f"👥 المشتركين: {len(subscribers)}\n"
            f"⏳ في الانتظار: {len(pending)}\n"
            f"📊 الصفقات المفتوحة: {open_count}/{config.MAX_OPEN_TRADES}\n"
            f"⏱️ فترة التبريد: {config.COOLDOWN_MINUTES} دقيقة\n"
            f"🔄 قاعدة البيانات: SQLite",
            parse_mode="HTML"
        )

    async def performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        latest = await self.repo.get_latest_performance()
        if not latest:
            await update.message.reply_text("📊 لا توجد بيانات أداء كافية حتى الآن.")
            return
        total_trades, wins, losses, inconclusive, win_rate, profit_factor, avg_win, avg_loss, expectancy, max_drawdown, sharpe_ratio, consecutive_losses, total_return = latest
        rating = "🌟 ممتاز" if win_rate >= 0.6 and sharpe_ratio > 1.0 else "👍 جيد" if win_rate >= 0.5 else "📊 يحتاج تحسين"
        profit_factor_str = "∞" if profit_factor == float('inf') or profit_factor == 0.0 and total_trades > 0 else f"{profit_factor:.2f}"
        await update.message.reply_text(
            f"📈 <b>أداء البوت المتقدم</b>\n\n"
            f"📊 إجمالي الصفقات: {total_trades}\n"
            f"✅ الصفقات الرابحة: {wins}\n"
            f"❌ الصفقات الخاسرة: {losses}\n"
            f"❓ غير محسومة: {inconclusive}\n"
            f"📈 نسبة الربح: {win_rate*100:.1f}%\n"
            f"💰 متوسط الربح: {avg_win:.2f}%\n"
            f"📉 متوسط الخسارة: {avg_loss:.2f}%\n"
            f"📊 معامل الربح: {profit_factor_str}\n"
            f"📈 العائد المتوقع: {expectancy:.2f}%\n"
            f"📈 العائد الكلي: {total_return:.2f}%\n"
            f"📉 أقصى انخفاض: {max_drawdown*100:.1f}%\n"
            f"📊 نسبة شارب: {sharpe_ratio:.2f}\n"
            f"📉 خسائر متتالية: {consecutive_losses}\n"
            f"🏆 التقييم: {rating}",
            parse_mode="HTML"
        )

# ===================================================================
# 16. بوت التليجرام (SignalBot)
# ===================================================================

class SignalBot:
    def __init__(self, repo):
        self.repo = repo
        self.handlers = CommandHandlers(repo)
        self.application = None
        self.is_running = False

    def build(self):
        self.application = Application.builder().token(config.TELEGRAM_TOKEN).build()
        self.application.add_handler(CommandHandler("start", self.handlers.start))
        self.application.add_handler(CommandHandler("approve", self.handlers.approve))
        self.application.add_handler(CommandHandler("status", self.handlers.status))
        self.application.add_handler(CommandHandler("performance", self.handlers.performance))
        logger.info("✅ Telegram bot built")
        return self.application

    async def start_polling(self):
        if not self.application:
            self.build()
        try:
            await self.application.initialize()
            logger.info("✅ Application initialized")
            await self.application.bot.delete_webhook(drop_pending_updates=False)
            logger.info("✅ Webhook deleted")
            await self.application.start()
            logger.info("✅ Application started")
            await self.application.updater.start_polling()
            logger.info("✅ Polling started successfully")
            self.is_running = True
        except Exception as e:
            logger.error(f"❌ Failed to start bot: {e}")
            try:
                await self.application.stop()
            except:
                pass
            try:
                await self.application.shutdown()
            except:
                pass
            raise

    async def stop(self):
        if not self.application or not self.is_running:
            return
        try:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
            self.is_running = False
            logger.info("✅ Telegram bot stopped")
        except Exception as e:
            logger.error(f"❌ Error stopping bot: {e}")

# ===================================================================
# 17. تشغيل Flask (Health Server)
# ===================================================================

def run_flask():
    flask_app = Flask(__name__)
    @flask_app.route('/')
    @flask_app.route('/healthcheck')
    def healthcheck():
        return "✅ Elite Signal Bot V23 - Fully Operational"
    flask_app.run(host='0.0.0.0', port=config.PORT, debug=False, use_reloader=False)

# ===================================================================
# 18. المدير الرئيسي للخدمات (ServiceManager) - مع Supervisor و Backoff
# ===================================================================

class ServiceManager:
    def __init__(self):
        self.db = None
        self.provider = None
        self.repo = None
        self.scanner = None
        self.tracker = None
        self.outbox = None
        self.bot = None
        self._tasks = []
        self.is_running = False
        self._restart_delays = {"Scanner": 1, "Tracker": 1, "Outbox": 1}
        self._max_restart_delay = 300
        self._task_stable_time = {}

    async def _restart_task(self, task_name: str) -> Optional[asyncio.Task]:
        delay = self._restart_delays.get(task_name, 1)
        self._restart_delays[task_name] = min(delay * 2, self._max_restart_delay)
        logger.info(f"🔄 إعادة تشغيل {task_name} بعد {delay} ثانية...")
        await asyncio.sleep(delay)
        if task_name == "Scanner":
            return asyncio.create_task(self.scanner.start(), name="Scanner")
        elif task_name == "Tracker":
            return asyncio.create_task(self.tracker.start(), name="Tracker")
        elif task_name == "Outbox":
            return asyncio.create_task(self.outbox.start(), name="Outbox")
        return None

    def _reset_backoff(self, task_name: str):
        self._restart_delays[task_name] = 1

    async def initialize(self):
        self.db = Database()
        if not await self.db.connect():
            raise RuntimeError("فشل الاتصال بقاعدة البيانات")
        self.provider = DataProvider()
        self.repo = Repository(self.db)
        self.bot = SignalBot(self.repo)
        self.bot.build()
        self.scanner = Scanner(self.provider, self.repo, self.bot.application)
        self.tracker = Tracker(self.provider, self.repo)
        self.outbox = OutboxWorker(self.repo, self.bot.application)
        logger.info("✅ تم تهيئة جميع الخدمات بنجاح")

    async def start_services(self):
        self.is_running = True
        try:
            await self.bot.start_polling()
        except Exception as e:
            logger.error(f"❌ فشل بدء التليجرام: {e}")
            raise
        
        scanner_task = asyncio.create_task(self.scanner.start(), name="Scanner")
        tracker_task = asyncio.create_task(self.tracker.start(), name="Tracker")
        outbox_task = asyncio.create_task(self.outbox.start(), name="Outbox")
        self._tasks = [scanner_task, tracker_task, outbox_task]
        self._task_stable_time = {"Scanner": time.time(), "Tracker": time.time(), "Outbox": time.time()}
        
        while self.is_running:
            done, pending = await asyncio.wait(
                self._tasks,
                timeout=30,
                return_when=asyncio.FIRST_COMPLETED
            )
            
            for task in done:
                task_name = task.get_name()
                if task_name not in self._tasks:
                    continue
                self._tasks.remove(task)
                
                if task.cancelled():
                    logger.warning(f"⚠️ {task_name} تم إلغاؤها")
                    continue
                
                exc = task.exception()
                if exc:
                    logger.error(f"❌ {task_name} انهارت: {exc}")
                else:
                    logger.warning(f"⚠️ {task_name} انتهت بشكل غير متوقع")
                
                if self.is_running:
                    new_task = await self._restart_task(task_name)
                    if new_task:
                        self._tasks.append(new_task)
                        self._task_stable_time[task_name] = time.time()
                else:
                    break
            
            # إعادة ضبط backoff للمهام المستقرة
            for task in self._tasks:
                task_name = task.get_name()
                if time.time() - self._task_stable_time.get(task_name, 0) > 300:
                    self._reset_backoff(task_name)
                    self._task_stable_time[task_name] = time.time()
            
            await asyncio.sleep(1)

    async def shutdown(self):
        logger.info("🛑 بدء إيقاف الخدمات...")
        self.is_running = False
        if self.scanner:
            await self.scanner.stop()
        if self.tracker:
            await self.tracker.stop()
        if self.outbox:
            await self.outbox.stop()
        for task in self._tasks:
            if not task.done():
                task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("❌ خطأ أثناء إنهاء مهمة")
        if self.bot:
            try:
                await self.bot.stop()
            except Exception:
                logger.exception("❌ خطأ أثناء إغلاق Telegram")
        if self.provider:
            try:
                await self.provider.close()
            except Exception:
                logger.exception("❌ خطأ أثناء إغلاق DataProvider")
        if self.db:
            try:
                await self.db.close()
            except Exception:
                logger.exception("❌ خطأ أثناء إغلاق Database")
        logger.info("✅ تم إيقاف جميع الخدمات وتنظيف الموارد")

# ===================================================================
# 19. الدالة الرئيسية
# ===================================================================

async def main_async():
    manager = ServiceManager()
    try:
        await manager.initialize()
        flask_thread = threading.Thread(target=run_flask, daemon=True, name="FlaskHealthServer")
        flask_thread.start()
        logger.info("🌐 تم تشغيل Flask")
        await manager.start_services()
    except asyncio.CancelledError:
        logger.info("⚠️ تم إلغاء التطبيق")
        raise
    except Exception:
        logger.exception("❌ حدث خطأ فادح")
    finally:
        await manager.shutdown()

# ===================================================================
# 20. نقطة الدخول الرئيسية
# ===================================================================

if __name__ == "__main__":
    logger.info("🚀 تشغيل البوت...")
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("🛑 تم الإيقاف بواسطة المستخدم")
    except Exception:
        logger.exception("💥 فشل التشغيل")
    finally:
        logger.info("🏁 تم إنهاء التطبيق")
