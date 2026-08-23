#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Elite Signal Bot V19 - الإصدار النهائي المتكامل
يشمل: Universe Engine, Ranking Engine, Market Regime, Correlation Filter, Portfolio Risk Engine
يعمل بكفاءة على Render
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
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timedelta
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ===================================================================
# 1. الإعدادات (Config) - النسخة النهائية المعدّلة
# ===================================================================

class Config:
    # -------------------- إعدادات التشغيل الأساسية --------------------
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
    ADMIN_CHAT_ID = os.environ.get("CHAT_ID", "")
    DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///elite_signal_bot.db")
    BINANCE_BASE_URL = os.environ.get("BINANCE_BASE_URL", "https://api.binance.com")
    BINANCE_TIMEOUT = 10
    BINANCE_RETRIES = 3
    PORT = int(os.environ.get("PORT", 10000))
    
    # -------------------- إعدادات الكون (Universe) --------------------
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
    
    # -------------------- معايير التصفية الأساسية --------------------
    MIN_VOLUME_USD = 1_000_000
    MIN_TRADES_24H = 500
    MIN_VOLATILITY_DAILY = 1.0
    MAX_STABLE_COINS = ["USDC", "FDUSD", "TUSD", "BUSD", "DAI"]
    EXCLUDED_SYMBOLS = ["UP", "DOWN", "BULL", "BEAR", "HALF"]
    
    # -------------------- أوزان تصنيف الفرص --------------------
    LIQUIDITY_WEIGHT = 0.25
    RELATIVE_VOLUME_WEIGHT = 0.20
    MOMENTUM_WEIGHT = 0.20
    VOLATILITY_WEIGHT = 0.15
    TREND_STRENGTH_WEIGHT = 0.10
    MARKET_STRUCTURE_WEIGHT = 0.10
    
    # -------------------- إعدادات جودة الإشارة --------------------
    MIN_SIGNAL_SCORE = 70
    SCORE_ELITE = 90
    SCORE_STRONG = 80
    SCORE_GOOD = 70
    
    # -------------------- إعدادات إدارة المخاطر --------------------
    MAX_OPEN_TRADES = 5
    MAX_SECTOR_EXPOSURE = 2
    DAILY_LOSS_LIMIT_PCT = 3.0
    MAX_CONSECUTIVE_LOSSES = 3
    CORRELATION_THRESHOLD = 0.80
    
    # -------------------- إعدادات المخاطرة لكل صفقة --------------------
    RISK_PER_TRADE_PCT = 1.0
    ALLOCATION_PCT = 2.0
    MIN_RISK_REWARD_RATIO = 1.5
    MAX_TRADE_DURATION_HOURS = 48
    
    # -------------------- إعدادات الماسح الضوئي --------------------
    SCAN_INTERVAL_SECONDS = 300
    COOLDOWN_MINUTES = 45
    
    # -------------------- إعدادات المؤشرات الفنية --------------------
    RSI_PERIOD = 6
    ADX_PERIOD = 14
    MIN_ADX_STRONG = 25
    MIN_CHANGE_1H = 0.3
    
    # -------------------- أقسام السوق (Sectors) --------------------
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

    def __post_init__(self):
        if not (len(self.prices) == len(self.highs) == len(self.lows) == len(self.volumes) == len(self.opens)):
            raise ValueError("All candle lists must have the same length")

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
# 4. المؤشرات الفنية (Indicators)
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
# 5. هيكل السوق (MarketStructure)
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
# 6. جلب البيانات (BinanceClient)
# ===================================================================

class BinanceClient:
    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self.session = session
        self.base_url = config.BINANCE_BASE_URL
        self.timeout = config.BINANCE_TIMEOUT
        self.retries = config.BINANCE_RETRIES
        self._owns_session = session is None

    async def __aenter__(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
            self._owns_session = True
        return self

    async def __aexit__(self, *args):
        if self._owns_session and self.session:
            await self.session.close()

    async def _request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        url = f"{self.base_url}{endpoint}"
        for attempt in range(self.retries):
            try:
                async with self.session.get(url, params=params, timeout=self.timeout) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429:
                        retry_after = int(resp.headers.get('Retry-After', 5))
                        await asyncio.sleep(retry_after)
                    else:
                        break
            except Exception:
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
        return CandleData(
            prices=[float(c[4]) for c in data],
            highs=[float(c[2]) for c in data],
            lows=[float(c[3]) for c in data],
            volumes=[float(c[5]) for c in data],
            opens=[float(c[1]) for c in data]
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

    async def get_exchange_info(self) -> Optional[list]:
        data = await self._request('/api/v3/exchangeInfo')
        if not data:
            return []
        symbols = []
        for s in data.get('symbols', []):
            if s.get('status') == 'TRADING' and s.get('quoteAsset') == 'USDT' and s.get('isSpotTradingAllowed'):
                symbols.append(s['symbol'])
        return symbols

# ===================================================================
# 7. قاعدة البيانات (Database)
# ===================================================================

class Database:
    def __init__(self):
        self.db_path = config.DATABASE_URL.replace("sqlite:///", "")
        self._closed = False

    async def connect(self):
        async with aiosqlite.connect(self.db_path) as db:
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
                    profit_loss REAL,
                    duration_minutes INTEGER,
                    win BOOLEAN,
                    entry_time TEXT,
                    sector TEXT,
                    quality_score INTEGER
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS performance_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT UNIQUE,
                    total_trades INTEGER,
                    wins INTEGER,
                    losses INTEGER,
                    win_rate REAL,
                    profit_factor REAL,
                    avg_win REAL,
                    avg_loss REAL,
                    expectancy REAL,
                    max_drawdown REAL,
                    sharpe_ratio REAL,
                    consecutive_losses INTEGER
                )
            """)
            if config.ADMIN_CHAT_ID:
                await db.execute("INSERT OR IGNORE INTO subscribers (user_id) VALUES (?)", (config.ADMIN_CHAT_ID,))
            await db.commit()
        logger.info(f"✅ SQLite قاعدة بيانات متصلة: {self.db_path}")
        return True

    async def close(self):
        self._closed = True

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
# 8. مستودع البيانات (Repository) - معدل
# ===================================================================

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

    async def set_cooldown(self, symbol: str, timestamp: str) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO signal_cooldown (symbol, last_signal_time) VALUES (?, ?)",
            symbol, timestamp
        )

    async def save_signal(self, symbol: str, signal_type: str, entry_price: float, stop_loss: float, take_profit: float, sector: str = "OTHER", quality_score: int = 0) -> None:
        await self.db.execute(
            "INSERT INTO signals_history (symbol, timestamp, signal_type, entry_price, stop_loss, take_profit, entry_time, sector, quality_score) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            symbol, datetime.now().isoformat(), signal_type, entry_price, stop_loss, take_profit, datetime.now().isoformat(), sector, quality_score
        )

    async def get_open_signals(self) -> List[Tuple]:
        rows = await self.db.fetch(
            "SELECT id, symbol, entry_time, entry_price, stop_loss, take_profit, signal_type, sector FROM signals_history WHERE status = 'OPEN'"
        )
        return rows

    async def close_signal(self, signal_id: int, status: str, exit_price: float, profit_loss: float, duration_minutes: int, win: bool) -> None:
        await self.db.execute(
            "UPDATE signals_history SET status = ?, exit_price = ?, profit_loss = ?, duration_minutes = ?, win = ? WHERE id = ?",
            status, exit_price, profit_loss, duration_minutes, win, signal_id
        )

    async def get_latest_performance(self):
        return await self.db.fetchrow(
            "SELECT total_trades, wins, losses, win_rate, profit_factor, avg_win, avg_loss, expectancy, max_drawdown, sharpe_ratio, consecutive_losses FROM performance_metrics ORDER BY id DESC LIMIT 1"
        )

    async def update_performance(self, metrics: dict) -> None:
        today = datetime.now().date().isoformat()
        await self.db.execute(
            """
            INSERT OR REPLACE INTO performance_metrics 
            (date, total_trades, wins, losses, win_rate, profit_factor, avg_win, avg_loss, expectancy, max_drawdown, sharpe_ratio, consecutive_losses)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            today, metrics.get('total_trades', 0), metrics.get('wins', 0),
            metrics.get('losses', 0), metrics.get('win_rate', 0.0),
            metrics.get('profit_factor', 0.0), metrics.get('avg_win', 0.0),
            metrics.get('avg_loss', 0.0), metrics.get('expectancy', 0.0),
            metrics.get('max_drawdown', 0.0), metrics.get('sharpe_ratio', 0.0),
            metrics.get('consecutive_losses', 0)
        )

    async def get_daily_loss(self) -> float:
        today = datetime.now().date().isoformat()
        row = await self.db.fetchrow(
            "SELECT SUM(profit_loss) FROM signals_history WHERE status IN ('LOSS', 'TIME_EXIT') AND date(timestamp) = ?",
            today
        )
        return abs(row[0]) if row and row[0] else 0.0

    async def get_consecutive_losses(self) -> int:
        rows = await self.db.fetch(
            "SELECT status FROM signals_history WHERE status IN ('LOSS', 'WIN') ORDER BY id DESC LIMIT ?",
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

# ===================================================================
# 9. المحركات المتقدمة (Advanced Engines)
# ===================================================================

class UniverseEngine:
    """بناء الكون الديناميكي من 70 عملة (Core + Dynamic)"""
    
    def __init__(self, all_stats: List[Dict]):
        self.all_stats = all_stats
        self.core_symbols = config.CORE_UNIVERSE
        self.dynamic_symbols = []
    
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
            score = (
                (volume / 10_000_000) * 0.4 +
                (abs(change) / 10) * 0.3 +
                (volatility / 20) * 0.3
            )
            candidates.append({"symbol": symbol, "score": score})
        
        candidates.sort(key=lambda x: x["score"], reverse=True)
        dynamic_selected = [c["symbol"] for c in candidates[:config.DYNAMIC_SIZE]]
        universe.update(dynamic_selected)
        return list(universe)[:config.MAX_UNIVERSE_SIZE]


class LiquidityRankingEngine:
    """تقييم الفرص بناءً على السيولة والزخم والاتجاه"""
    
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
            
            score = (
                (volume / 100_000_000) * config.LIQUIDITY_WEIGHT +
                (momentum / 10) * config.MOMENTUM_WEIGHT +
                (volume_expansion / 50) * config.RELATIVE_VOLUME_WEIGHT +
                (volatility / 20) * config.VOLATILITY_WEIGHT
            )
            
            candidates.append({
                "symbol": symbol,
                "volume": volume,
                "change": change,
                "volatility": volatility,
                "momentum": momentum,
                "count": count,
                "score": score,
                "raw": item
            })
        
        candidates.sort(key=lambda x: x["score"], reverse=True)
        self.ranked = candidates[:config.MAX_UNIVERSE_SIZE]
        return self.ranked


class MarketRegimeEngine:
    """تحديد حالة السوق بدقة عالية"""
    
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
            if current_4h > sma20_4h * 1.05:
                regime = "STRONG_TREND"
            else:
                regime = "WEAK_TREND"
        elif current_4h < sma20_4h and sma20_4h < sma50_4h:
            if current_4h < sma20_4h * 0.95:
                regime = "STRONG_TREND"
            else:
                regime = "WEAK_TREND"
        else:
            atr = Indicators.calculate_atr(
                data_4h.closed_highs(), data_4h.closed_lows(), prices_4h, 14
            )
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
        
        return {"regime": regime, "volatility": volatility}


class RelativeStrengthEngine:
    """حساب القوة النسبية مقابل BTC و ETH"""
    
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
    """تتبع أداء القطاعات وإعطاء أولوية للأقوى"""
    
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
    """منع الإشارات المتشابهة في نفس القطاع"""
    
    @staticmethod
    def get_sector(symbol: str) -> str:
        for sector, members in config.SECTORS.items():
            if symbol in members:
                return sector
        return "OTHER"
    
    @staticmethod
    def is_allowed(symbol: str, open_signals: List[Tuple]) -> Tuple[bool, str]:
        sector = CorrelationFilter.get_sector(symbol)
        if sector == "OTHER":
            return True, ""
        
        sector_count = 0
        for signal in open_signals:
            sig_sector = CorrelationFilter.get_sector(signal[7])  # signal[7] = sector (مخزن في DB)
            if sig_sector == sector:
                sector_count += 1
        
        if sector_count >= config.MAX_SECTOR_EXPOSURE:
            return False, f"قطاع {sector} لديه {sector_count} صفقات مفتوحة (الحد {config.MAX_SECTOR_EXPOSURE})"
        
        return True, ""


class PortfolioRiskEngine:
    """إدارة المخاطر على مستوى المحفظة"""
    
    def __init__(self, repo: Repository):
        self.repo = repo
    
    async def check_daily_loss_limit(self) -> bool:
        daily_loss = await self.repo.get_daily_loss()
        if daily_loss >= config.DAILY_LOSS_LIMIT_PCT:
            logger.warning(f"⚠️ حد الخسارة اليومي {config.DAILY_LOSS_LIMIT_PCT}% تم تجاوزه ({daily_loss:.2f}%)")
            return False
        return True
    
    async def check_consecutive_losses(self) -> bool:
        consecutive = await self.repo.get_consecutive_losses()
        if consecutive >= config.MAX_CONSECUTIVE_LOSSES:
            logger.warning(f"⚠️ {consecutive} خسائر متتالية، تقليل المخاطرة")
            return False
        return True
    
    async def get_open_trades_count(self) -> int:
        rows = await self.repo.db.fetch("SELECT id FROM signals_history WHERE status = 'OPEN'")
        return len(rows)
    
    async def can_trade(self) -> Tuple[bool, str]:
        open_count = await self.get_open_trades_count()
        if open_count >= config.MAX_OPEN_TRADES:
            return False, f"الحد الأقصى للصفقات المفتوحة ({config.MAX_OPEN_TRADES}) تم الوصول إليه"
        
        if not await self.check_daily_loss_limit():
            return False, "تم تجاوز حد الخسارة اليومي"
        
        if not await self.check_consecutive_losses():
            return False, f"{config.MAX_CONSECUTIVE_LOSSES} خسائر متتالية"
        
        return True, ""

# ===================================================================
# 10. محرك الاستراتيجية (StrategyEngine) - معدل
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
        self.change_1h = self._calculate_change_1h()
        self.volume_ratio = self._calculate_volume_ratio()
        self.score = 0
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

    def _calculate_quality_score(self) -> int:
        """حساب درجة جودة الإشارة (من 100)"""
        score = 0
        
        # اتجاه 4H (15 نقطة)
        if self.directional_bias == 'bullish' or self.directional_bias == 'bearish':
            score += 15
        
        # اتجاه 1H (15 نقطة)
        if self.trend != 'neutral':
            score += 15
        
        # هيكل 5M (15 نقطة)
        if self.structure.get_trend() != 'neutral':
            score += 15
        
        # Momentum (15 نقطة)
        if abs(self.change_1h) > 1.0:
            score += 15
        elif abs(self.change_1h) > 0.5:
            score += 8
        
        # Volume (15 نقطة)
        if self.volume_ratio > 2.0:
            score += 15
        elif self.volume_ratio > 1.5:
            score += 8
        
        # ADX (10 نقاط)
        if self.adx > 30:
            score += 10
        elif self.adx > 25:
            score += 5
        
        # RSI Position (5 نقاط)
        if 40 <= self.rsi <= 60:
            score += 5
        
        # Risk/Reward (5 نقاط)
        if self.atr > 0 and self.action:
            if self.action == 'BUY':
                rr_ratio = (self.take_profit - self.entry_price) / (self.entry_price - self.stop_loss) if self.stop_loss > 0 else 0
            else:
                rr_ratio = (self.entry_price - self.take_profit) / (self.stop_loss - self.entry_price) if self.stop_loss > 0 else 0
            if rr_ratio >= 2.0:
                score += 5
            elif rr_ratio >= 1.5:
                score += 3
        
        # Market Regime (5 نقاط)
        if self.market_regime in ["STRONG_TREND", "WEAK_TREND"]:
            score += 5
        
        return min(score, 100)

    def generate_signal(self):
        # 1. التحقق من التغير خلال ساعة
        if abs(self.change_1h) < config.MIN_CHANGE_1H:
            return self._signal_result("WATCH", [f"التغير خلال ساعة {self.change_1h:.2f}% أقل من الحد الأدنى"])
        
        # 2. التحقق من قوة الاتجاه
        if self.adx < config.MIN_ADX_STRONG:
            return self._signal_result("NO_TRADE", ["السوق ليس اتجاهياً (ADX ضعيف)"])
        
        # 3. التحقق من الاتجاه
        if self.directional_bias == 'neutral':
            return self._signal_result("NO_TRADE", ["الاتجاه غير واضح"])
        
        # 4. نظام السوق
        if self.market_regime == "RANGE" and self.trend != 'neutral':
            return self._signal_result("WATCH", ["السوق في نطاق جانبي، إشارات الاختراق غير موثوقة"])
        
        if self.market_regime == "HIGH_VOLATILITY":
            # توسيع وقف الخسارة بنسبة 20%
            volatility_multiplier = 1.2
        else:
            volatility_multiplier = 1.0
        
        # 5. التحقق من الهيكل
        if self.trend == 'neutral':
            return self._signal_result("WATCH", ["هيكل السوق محايد"])
        
        # 6. التحقق من RSI
        if self.rsi > 80 or self.rsi < 20:
            return self._signal_result("WATCH", ["RSI متطرف"])
        
        # 7. التحقق من إشارات MACD
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
        
        # 8. التحقق من الحجم
        if self.volume_ratio < 1.5:
            return self._signal_result("WATCH", ["حجم ضعيف"])
        
        # 9. حساب المخاطر
        stop_loss, take_profit, allocation_pct = self._calculate_risk(self.action, volatility_multiplier)
        if stop_loss == 0.0 or take_profit == 0.0 or allocation_pct == 0.0:
            return self._signal_result("NO_TRADE", ["إدارة المخاطر غير صالحة"])
        
        self.entry_price = self.current_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        
        # 10. حساب درجة الجودة (100)
        quality_score = self._calculate_quality_score()
        
        # 11. التحقق من الحد الأدنى للجودة
        if quality_score < config.MIN_SIGNAL_SCORE:
            return self._signal_result("WATCH", [f"درجة الجودة {quality_score} أقل من الحد الأدنى {config.MIN_SIGNAL_SCORE}"])
        
        # 12. إرسال الإشارة
        return self._signal_result(self.action, ["إشارة جيدة"], stop_loss, take_profit, allocation_pct, quality_score)

    def _signal_result(self, action: str, reasons: List[str], stop_loss: float = 0.0, take_profit: float = 0.0, allocation_pct: float = 0.0, quality_score: int = 0):
        return {
            "symbol": self.symbol,
            "action": action,
            "entry_price": self.current_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "allocation_pct": allocation_pct * 100,
            "quality_score": quality_score,
            "reasons": reasons,
            "adx": self.adx,
            "rsi": self.rsi,
            "market_regime": self.market_regime,
            "directional_bias": self.directional_bias,
            "structure": self.trend,
            "is_actionable": action in ("BUY", "SELL")
        }

    def _calculate_risk(self, action: str, volatility_multiplier: float = 1.0) -> Tuple[float, float, float]:
        if action not in ('BUY', 'SELL'):
            return 0.0, 0.0, 0.0
        
        min_stop_pct = 0.01
        atr_stop = self.atr * 2 * volatility_multiplier if self.atr > 0 else self.current_price * 0.015
        max_stop_distance = max(atr_stop, self.current_price * min_stop_pct)
        
        if action == 'BUY' and self.last_swing_low is not None:
            stop_loss = self.last_swing_low - self.atr * 0.25 * volatility_multiplier
            if self.current_price - stop_loss < max_stop_distance:
                stop_loss = self.current_price - max_stop_distance
            if stop_loss > self.current_price * 0.98:
                return 0.0, 0.0, 0.0
            take_profit = self.current_price + (self.current_price - stop_loss) * config.MIN_RISK_REWARD_RATIO
            stop_distance = abs(self.current_price - stop_loss) / self.current_price
            if stop_distance > 0:
                risk_amount = config.RISK_PER_TRADE_PCT / 100
                allocation_pct = min(risk_amount / stop_distance, config.ALLOCATION_PCT / 100)
            else:
                allocation_pct = 0.0
            return stop_loss, take_profit, allocation_pct
        
        elif action == 'SELL' and self.last_swing_high is not None:
            stop_loss = self.last_swing_high + self.atr * 0.25 * volatility_multiplier
            if stop_loss - self.current_price < max_stop_distance:
                stop_loss = self.current_price + max_stop_distance
            if stop_loss < self.current_price * 1.02:
                return 0.0, 0.0, 0.0
            take_profit = self.current_price - (stop_loss - self.current_price) * config.MIN_RISK_REWARD_RATIO
            stop_distance = abs(self.current_price - stop_loss) / self.current_price
            if stop_distance > 0:
                risk_amount = config.RISK_PER_TRADE_PCT / 100
                allocation_pct = min(risk_amount / stop_distance, config.ALLOCATION_PCT / 100)
            else:
                allocation_pct = 0.0
            return stop_loss, take_profit, allocation_pct
        
        return 0.0, 0.0, 0.0

# ===================================================================
# 11. مقدم البيانات (DataProvider) - معدل
# ===================================================================

class DataProvider:
    def __init__(self):
        self._session = None

    async def _get_session(self):
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None

    async def fetch_klines(self, symbol: str, interval: str = '5m', limit: int = 100) -> Optional[CandleData]:
        session = await self._get_session()
        async with BinanceClient(session) as client:
            return await client.get_klines(symbol, interval, limit)

    async def fetch_stats(self, symbol: str) -> Optional[MarketStats]:
        session = await self._get_session()
        async with BinanceClient(session) as client:
            return await client.get_24hr_stats(symbol)

    async def get_active_symbols(self) -> List[str]:
        session = await self._get_session()
        async with BinanceClient(session) as client:
            return await client.get_exchange_info() or []

    async def get_all_24hr_stats(self) -> Optional[List[Dict]]:
        session = await self._get_session()
        async with BinanceClient(session) as client:
            data = await client._request('/api/v3/ticker/24hr')
            return data if isinstance(data, list) else []

    async def filter_symbols(self) -> List[str]:
        """تصفية ديناميكية باستخدام Universe Engine + Ranking Engine + Sector Rotation"""
        all_stats = await self.get_all_24hr_stats()
        if not all_stats:
            return []
        
        # 1. بناء الكون الديناميكي
        universe_engine = UniverseEngine(all_stats)
        universe = universe_engine.build()
        
        # 2. تصفية الإحصائيات للعملات المختارة فقط
        filtered_stats = []
        for item in all_stats:
            if item.get("symbol") in universe:
                filtered_stats.append(item)
        
        # 3. تشغيل محرك التصنيف
        ranking_engine = LiquidityRankingEngine(filtered_stats)
        ranked = ranking_engine.filter_and_rank()
        
        if not ranked:
            return []
        
        # 4. جلب إحصائيات BTC و ETH للقوة النسبية
        btc_stats = await self.fetch_stats("BTCUSDT")
        eth_stats = await self.fetch_stats("ETHUSDT")
        btc_change = btc_stats.change_24h if btc_stats else 0.0
        eth_change = eth_stats.change_24h if eth_stats else 0.0
        
        # 5. بناء قائمة للإحصائيات لـ Sector Rotation
        stats_list = []
        for item in ranked:
            stats = MarketStats(
                volume=item["volume"],
                change_24h=item["change"],
                high=0.0, low=0.0, open=0.0, last=0.0
            )
            stats_list.append((item["symbol"], stats))
        
        # 6. تشغيل محرك القطاعات
        sector_engine = SectorRotationEngine(stats_list)
        
        # 7. إضافة القوة النسبية وأولوية القطاع
        for item in ranked:
            rel = RelativeStrengthEngine.calculate(
                item["symbol"], item["change"], btc_change, eth_change
            )
            item["btc_relative"] = rel["btc_relative"]
            item["eth_relative"] = rel["eth_relative"]
            item["sector_priority"] = sector_engine.get_priority(item["symbol"])
        
        # 8. حساب النتيجة النهائية
        for item in ranked:
            sector_bonus = (1 - item["sector_priority"] / 10) if item["sector_priority"] < 999 else 0
            item["final_score"] = (
                item["score"] * 0.60 +
                abs(item["btc_relative"]) * 0.15 +
                abs(item["eth_relative"]) * 0.10 +
                sector_bonus * 0.15
            )
        
        ranked.sort(key=lambda x: x["final_score"], reverse=True)
        return [item["symbol"] for item in ranked[:config.MAX_UNIVERSE_SIZE]]

# ===================================================================
# 12. الماسح الضوئي (Scanner) - معدل
# ===================================================================

class Scanner:
    def __init__(self, provider: DataProvider, repo: Repository, bot_app: Optional[Application] = None):
        self.provider = provider
        self.repo = repo
        self.bot_app = bot_app
        self.last_filter_time = 0
        self.dynamic_watch_list = []
        self.is_running = False

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
        if time.time() - self.last_filter_time > 1800:
            self.dynamic_watch_list = await self.provider.filter_symbols()
            self.last_filter_time = time.time()
            logger.info(f"🔍 Updated watchlist: {len(self.dynamic_watch_list)} symbols")
        
        all_symbols = list(set(self.dynamic_watch_list))
        if not all_symbols:
            return
        
        logger.info(f"🔄 Scanning {len(all_symbols)} symbols...")
        sem = asyncio.Semaphore(5)
        tasks = [self._process_symbol(sym, sem) for sym in all_symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for res in results:
            if isinstance(res, Exception):
                logger.error(f"Symbol processing error: {res}")

    async def _process_symbol(self, symbol: str, sem: asyncio.Semaphore):
        async with sem:
            # 1. التحقق من فترة التبريد
            cooldown = await self.repo.get_cooldown(symbol)
            if cooldown:
                last_time = datetime.fromisoformat(cooldown)
                if (datetime.now() - last_time) < timedelta(minutes=config.COOLDOWN_MINUTES):
                    return None
            
            # 2. التحقق من المخاطر على مستوى المحفظة
            risk_engine = PortfolioRiskEngine(self.repo)
            can_trade, reason = await risk_engine.can_trade()
            if not can_trade:
                logger.warning(f"⛔ {reason}")
                return None
            
            # 3. جلب البيانات
            data_5m = await self.provider.fetch_klines(symbol, '5m', 100)
            data_1h = await self.provider.fetch_klines(symbol, '1h', 100)
            data_4h = await self.provider.fetch_klines(symbol, '4h', 60)
            stats = await self.provider.fetch_stats(symbol)
            
            if not all([data_5m, data_1h, data_4h, stats]) or stats.volume < config.MIN_VOLUME_USD:
                return None
            
            # 4. التحقق من القطاع
            open_signals = await self.repo.get_open_signals()
            allowed, reason = CorrelationFilter.is_allowed(symbol, open_signals)
            if not allowed:
                logger.warning(f"⛔ {reason}")
                return None
            
            # 5. تحليل الإشارة
            engine = StrategyEngine(symbol, data_5m, data_1h, data_4h, stats)
            signal = engine.generate_signal()
            
            if not signal['is_actionable']:
                return None
            if signal['stop_loss'] == 0.0 or signal['take_profit'] == 0.0:
                return None
            
            # 6. حفظ الإشارة
            sector = CorrelationFilter.get_sector(symbol)
            await self.repo.save_signal(
                signal['symbol'], signal['action'],
                signal['entry_price'], signal['stop_loss'], signal['take_profit'],
                sector, signal.get('quality_score', 0)
            )
            
            # 7. إرسال الإشارة
            await self._broadcast_signal(signal)
            await self.repo.set_cooldown(symbol, datetime.now().isoformat())
            logger.info(f"✅ Signal generated for {symbol}: {signal['action']} (Score: {signal.get('quality_score', 0)})")
            return signal

    async def _broadcast_signal(self, signal: dict):
        if not self.bot_app:
            logger.error("❌ Bot application not set for broadcasting")
            return
        
        subscribers = await self.repo.get_subscribers()
        if not subscribers:
            logger.warning("📢 لا يوجد مشتركين")
            return
        
        quality_label = "ELITE" if signal.get('quality_score', 0) >= config.SCORE_ELITE else "STRONG" if signal.get('quality_score', 0) >= config.SCORE_STRONG else "GOOD"
        
        msg = (
            f"🚨 *إشارة تداول جديدة*\n\n"
            f"📊 العملة: `{signal['symbol']}`\n"
            f"⚡ الإجراء: `{signal['action']}`\n"
            f"💰 سعر الدخول: `{signal['entry_price']:.4f}`\n"
            f"🛑 وقف الخسارة: `{signal['stop_loss']:.4f}`\n"
            f"🎯 جني الأرباح: `{signal['take_profit']:.4f}`\n"
            f"📊 تخصيص رأس المال: `{signal.get('allocation_pct', 0):.1f}%`\n"
            f"⭐ جودة الإشارة: `{signal.get('quality_score', 0)}/100` ({quality_label})\n"
            f"📈 ADX: {signal['adx']:.1f} | RSI: {signal['rsi']:.1f}\n"
            f"📉 نظام السوق: `{signal.get('market_regime', 'N/A')}`\n"
            f"📝 الأسباب: {', '.join(signal['reasons'][:3])}"
        )
        
        for user_id in subscribers:
            try:
                await self.bot_app.bot.send_message(
                    chat_id=user_id,
                    text=msg,
                    parse_mode="Markdown"
                )
                logger.info(f"📨 تم إرسال الإشارة للمستخدم {user_id}")
            except Exception as e:
                logger.error(f"فشل إرسال الإشارة لـ {user_id}: {e}")

# ===================================================================
# 13. المتتبع (Tracker) - معدل
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
            signal_id, symbol, entry_time, entry_price, stop_loss, take_profit, signal_type, sector = signal
            
            data_1m = await self.provider.fetch_klines(symbol, '1m', 60)
            if not data_1m:
                continue
            
            hit_sl_time = None
            hit_tp_time = None
            
            for i, (high, low) in enumerate(zip(data_1m.closed_highs(), data_1m.closed_lows())):
                if signal_type == 'BUY':
                    if low <= stop_loss and hit_sl_time is None:
                        hit_sl_time = i
                    if high >= take_profit and hit_tp_time is None:
                        hit_tp_time = i
                else:
                    if high >= stop_loss and hit_sl_time is None:
                        hit_sl_time = i
                    if low <= take_profit and hit_tp_time is None:
                        hit_tp_time = i
            
            now = datetime.now()
            duration_hours = (now - datetime.fromisoformat(entry_time)).total_seconds() / 3600
            
            status, exit_price, profit_loss, win = 'OPEN', data_1m.get_current_price(), 0.0, False
            
            if hit_sl_time is not None and hit_tp_time is not None:
                if hit_sl_time < hit_tp_time:
                    status, exit_price, profit_loss = 'LOSS', stop_loss, ((stop_loss - entry_price) / entry_price) * 100 if signal_type == 'BUY' else ((entry_price - stop_loss) / entry_price) * 100
                else:
                    status, exit_price, profit_loss, win = 'WIN', take_profit, ((take_profit - entry_price) / entry_price) * 100 if signal_type == 'BUY' else ((entry_price - take_profit) / entry_price) * 100, True
            elif hit_sl_time is not None:
                status, exit_price, profit_loss = 'LOSS', stop_loss, ((stop_loss - entry_price) / entry_price) * 100 if signal_type == 'BUY' else ((entry_price - stop_loss) / entry_price) * 100
            elif hit_tp_time is not None:
                status, exit_price, profit_loss, win = 'WIN', take_profit, ((take_profit - entry_price) / entry_price) * 100 if signal_type == 'BUY' else ((entry_price - take_profit) / entry_price) * 100, True
            elif duration_hours >= config.MAX_TRADE_DURATION_HOURS:
                status = "TIME_EXIT"
                exit_price = data_1m.get_current_price()
                if signal_type == "BUY":
                    profit_loss = ((exit_price - entry_price) / entry_price) * 100
                else:
                    profit_loss = ((entry_price - exit_price) / entry_price) * 100
                win = profit_loss > 0
            else:
                continue
            
            duration_minutes = int(duration_hours * 60)
            await self.repo.close_signal(signal_id, status, exit_price, profit_loss, duration_minutes, win)
            await self._update_global_performance()
            logger.info(f"✅ Signal {signal_id} ({symbol}) closed: {status} ({profit_loss:.2f}%)")

    async def _update_global_performance(self):
        """تحديث مقاييس الأداء الكلية مع Max Drawdown و Sharpe Ratio"""
        stats = await self.repo.db.fetchrow(
            """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN win = 1 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN win = 0 AND status IN ('LOSS', 'TIME_EXIT') THEN 1 ELSE 0 END) as losses,
                AVG(CASE WHEN win = 1 THEN profit_loss END) as avg_win,
                AVG(CASE WHEN win = 0 AND status IN ('LOSS', 'TIME_EXIT') THEN profit_loss END) as avg_loss
            FROM signals_history WHERE status IN ('WIN', 'LOSS', 'TIME_EXIT')
            """
        )
        if not stats or stats[0] == 0:
            return
        
        total, wins, losses, avg_win, avg_loss = stats
        win_rate = wins / total if total > 0 else 0.0
        
        gross_profit_row = await self.repo.db.fetchrow("SELECT SUM(profit_loss) FROM signals_history WHERE win = 1")
        gross_loss_row = await self.repo.db.fetchrow("SELECT SUM(profit_loss) FROM signals_history WHERE win = 0 AND status IN ('LOSS', 'TIME_EXIT')")
        gross_profit = gross_profit_row[0] if gross_profit_row and gross_profit_row[0] else 0.0
        gross_loss = abs(gross_loss_row[0]) if gross_loss_row and gross_loss_row[0] else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
        
        expectancy = (win_rate * (avg_win or 0)) - ((1 - win_rate) * abs(avg_loss or 0))
        
        # حساب Max Drawdown
        rows = await self.repo.db.fetch(
            "SELECT profit_loss FROM signals_history WHERE status IN ('WIN', 'LOSS', 'TIME_EXIT') ORDER BY timestamp"
        )
        cumulative = 0.0
        peak = 0.0
        max_drawdown = 0.0
        returns = []
        
        for row in rows:
            pl = row[0] if row[0] else 0.0
            cumulative += pl
            returns.append(pl)
            if cumulative > peak:
                peak = cumulative
            if peak > 0:
                drawdown = (peak - cumulative) / peak
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
        
        # حساب Sharpe Ratio (بسيط)
        sharpe_ratio = 0.0
        if returns and len(returns) > 1:
            avg_return = sum(returns) / len(returns)
            variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
            std_dev = math.sqrt(variance) if variance > 0 else 0.01
            sharpe_ratio = (avg_return / std_dev) * math.sqrt(365) if std_dev > 0 else 0.0
        
        # حساب الخسائر المتتالية
        consecutive_losses = await self.repo.get_consecutive_losses()
        
        metrics = {
            'total_trades': total,
            'wins': wins or 0,
            'losses': losses or 0,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_win': avg_win or 0.0,
            'avg_loss': avg_loss or 0.0,
            'expectancy': expectancy,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio,
            'consecutive_losses': consecutive_losses
        }
        await self.repo.update_performance(metrics)
        logger.info(f"📈 أداء: {wins}/{total} ربح ({win_rate*100:.1f}%) | MaxDD: {max_drawdown*100:.1f}% | Sharpe: {sharpe_ratio:.2f}")

# ===================================================================
# 14. أوامر التليجرام (CommandHandlers)
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
            await context.bot.send_message(
                chat_id=config.ADMIN_CHAT_ID,
                text=f"📩 طلب اشتراك جديد: <code>{user_id}</code>\n/approve {user_id}",
                parse_mode="HTML"
            )

    async def approve(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_user.id) != config.ADMIN_CHAT_ID:
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
                await context.bot.send_message(chat_id=user_id, text="🎉 تمت الموافقة على اشتراكك! ستستلم إشارات التداول تلقائياً.")
            except:
                pass
        else:
            await update.message.reply_text("❌ غير موجود في قائمة الانتظار.")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        subscribers = await self.repo.get_subscribers()
        pending = await self.repo.get_pending()
        open_count = len(await self.repo.get_open_signals())
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
        
        total_trades, wins, losses, win_rate, profit_factor, avg_win, avg_loss, expectancy, max_drawdown, sharpe_ratio, consecutive_losses = latest
        
        # تصنيف الأداء
        rating = "🌟 ممتاز" if win_rate >= 0.6 and sharpe_ratio > 1.0 else "👍 جيد" if win_rate >= 0.5 else "📊 يحتاج تحسين"
        
        await update.message.reply_text(
            f"📈 <b>أداء البوت المتقدم</b>\n\n"
            f"📊 إجمالي الصفقات: {total_trades}\n"
            f"✅ الصفقات الرابحة: {wins}\n"
            f"❌ الصفقات الخاسرة: {losses}\n"
            f"📈 نسبة الربح: {win_rate*100:.1f}%\n"
            f"💰 متوسط الربح: {avg_win:.2f}%\n"
            f"📉 متوسط الخسارة: {avg_loss:.2f}%\n"
            f"📊 معامل الربح: {profit_factor:.2f}\n"
            f"📈 العائد المتوقع: {expectancy:.2f}%\n"
            f"📉 أقصى انخفاض: {max_drawdown*100:.1f}%\n"
            f"📊 نسبة شارب: {sharpe_ratio:.2f}\n"
            f"📉 خسائر متتالية: {consecutive_losses}\n"
            f"🏆 التقييم: {rating}",
            parse_mode="HTML"
        )

# ===================================================================
# 15. بوت التليجرام (SignalBot)
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
            await self.application.bot.delete_webhook()
            logger.info("✅ Webhook deleted")
            
            await asyncio.sleep(3)
            
            await self.application.initialize()
            logger.info("✅ Application initialized")
            
            await self.application.start()
            logger.info("✅ Application started")
            
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    await self.application.updater.start_polling()
                    logger.info("✅ Polling started successfully")
                    self.is_running = True
                    return
                except Exception as e:
                    if "Conflict" in str(e) and attempt < max_retries - 1:
                        logger.warning(f"⚠️ تعارض توكن، إعادة محاولة {attempt+1}/{max_retries} بعد 5 ثوان...")
                        await asyncio.sleep(5)
                        await self.application.bot.delete_webhook()
                    else:
                        raise
            
        except Exception as e:
            logger.error(f"❌ Failed to start bot: {e}")
            raise

    async def stop(self):
        if not self.application:
            return
        try:
            if self.application.updater:
                try:
                    if self.application.updater.running:
                        await self.application.updater.stop()
                except:
                    pass
            
            if self.application.running:
                await self.application.stop()
            await self.application.shutdown()
            self.is_running = False
            logger.info("✅ Telegram bot stopped")
        except asyncio.CancelledError:
            logger.info("⚠️ Telegram shutdown cancelled")
            raise
        except Exception:
            logger.exception("❌ Error stopping Telegram bot")

# ===================================================================
# 16. تشغيل Flask
# ===================================================================

def run_flask():
    flask_app = Flask(__name__)
    @flask_app.route('/')
    @flask_app.route('/healthcheck')
    def healthcheck():
        return "✅ Elite Signal Bot V19 - Fully Operational"
    flask_app.run(host='0.0.0.0', port=config.PORT, debug=False, use_reloader=False)

# ===================================================================
# 17. المدير الرئيسي للخدمات (ServiceManager)
# ===================================================================

class ServiceManager:
    def __init__(self):
        self.db = None
        self.provider = None
        self.repo = None
        self.scanner = None
        self.tracker = None
        self.bot = None
        self._tasks = []
        self.is_running = False

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
        
        logger.info("✅ تم تهيئة جميع الخدمات بنجاح")

    async def start_services(self):
        self.is_running = True
        
        scanner_task = asyncio.create_task(self.scanner.start(), name="Scanner")
        tracker_task = asyncio.create_task(self.tracker.start(), name="Tracker")
        self._tasks.extend([scanner_task, tracker_task])
        logger.info("🔄 تم تشغيل Scanner و Tracker")

        try:
            await self.bot.start_polling()
        except Exception as e:
            logger.error(f"❌ فشل بدء التليجرام: {e}")
            raise

        while self.is_running:
            await asyncio.sleep(60)

    async def shutdown(self):
        logger.info("🛑 بدء إيقاف الخدمات...")
        self.is_running = False
        
        if self.scanner:
            await self.scanner.stop()
        if self.tracker:
            await self.tracker.stop()
        
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
                if self.bot.application and self.bot.application.updater:
                    try:
                        await self.bot.application.updater.stop()
                    except:
                        pass
                await self.bot.stop()
            except Exception:
                logger.exception("❌ خطأ أثناء إغلاق Telegram")
            self.bot.is_running = False
        
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
# 18. الدالة الرئيسية
# ===================================================================

async def main_async():
    manager = ServiceManager()
    try:
        await manager.initialize()
        flask_thread = threading.Thread(
            target=run_flask,
            daemon=True,
            name="FlaskHealthServer"
        )
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
# 19. نقطة الدخول الرئيسية
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
