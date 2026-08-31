"""
utils.py - طبقة جلب البيانات مع Timeout قصير لتجنب تجميد البوت.
"""
import asyncio
import aiohttp
import json
import logging
import time
import ssl
from typing import List, Dict, Optional, Any, Tuple
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from config import CFG

# ─── إعداد التسجيل ───
class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra"):
            log_obj.update(record.extra)
        return json.dumps(log_obj, ensure_ascii=False)

logger = logging.getLogger("crypto_bot")
handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)
logger.setLevel(getattr(logging, CFG.LOG_LEVEL))

# ─── إدارة Rate Limit ───
class RateLimiter:
    def __init__(self, max_concurrent: int = CFG.MAX_CONCURRENT_REQUESTS, delay_between: float = 0.15):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.delay = delay_between
        self._last_request = 0.0

    async def acquire(self):
        await self.semaphore.acquire()
        now = time.monotonic()
        elapsed = now - self._last_request
        if elapsed < self.delay:
            await asyncio.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

    def release(self):
        self.semaphore.release()

limiter = RateLimiter(max_concurrent=CFG.MAX_CONCURRENT_REQUESTS, delay_between=0.15)

# ─── 🔥 إعادة ترتيب النقاط (الأقل حظراً أولاً) ───
BINANCE_ENDPOINTS = [
    "https://api.binance.us",              # الأقل حظراً على Render
    "https://data-api.binance.vision",     # بيانات عامة
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]

_symbol_filters_cache: Dict[str, Dict] = {}

class DataFetcher:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            # 🔥 تقليل المهلة إلى 3 ثوانٍ لمنع التجميد
            timeout = aiohttp.ClientTimeout(
                total=3,
                connect=2,
                sock_read=2
            )

            headers = {
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            connector = aiohttp.TCPConnector(ssl=ssl_context)

            self.session = aiohttp.ClientSession(
                timeout=timeout,
                headers=headers,
                connector=connector
            )
        return self.session

    async def fetch_klines(self, symbol: str, interval: str = "5m", limit: int = 250) -> pd.DataFrame:
        params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
        for base_url in BINANCE_ENDPOINTS:
            try:
                await limiter.acquire()
                session = await self._get_session()
                url = f"{base_url}/api/v3/klines"
                async with session.get(url, params=params) as resp:
                    limiter.release()
                    if resp.status == 429:
                        logger.warning("Rate limit hit", extra={"symbol": symbol})
                        await asyncio.sleep(1)
                        continue
                    resp.raise_for_status()
                    data = await resp.json()
                    if not data:
                        continue
                    df = self._parse_klines(data)
                    df = self._clean_data(df)
                    return df
            except asyncio.TimeoutError:
                limiter.release()
                logger.debug(f"Timeout from {base_url} for {symbol}")
                continue
            except aiohttp.ClientError as e:
                limiter.release()
                logger.debug(f"Failover from {base_url}", extra={"error": str(e), "symbol": symbol})
                await asyncio.sleep(0.5)
                continue
            except Exception as e:
                limiter.release()
                logger.error(f"Error fetching {symbol}", extra={"error": str(e)})
                raise
        # إذا فشلت جميع النقاط، نعيد DataFrame فارغاً (بدلاً من رفع استثناء)
        logger.warning(f"All endpoints failed for {symbol}, returning empty DataFrame")
        return pd.DataFrame()

    def _parse_klines(self, data: List[List[Any]]) -> pd.DataFrame:
        columns = ["open_time", "open", "high", "low", "close", "volume",
                   "close_time", "quote_volume", "trades", "taker_buy_base",
                   "taker_buy_quote", "ignore"]
        df = pd.DataFrame(data, columns=columns)
        numeric_cols = ["open", "high", "low", "close", "volume", "quote_volume"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        return df

    def _clean_data(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.sort_values("open_time").reset_index(drop=True)
        df = df.dropna(subset=["open", "high", "low", "close", "volume"])
        for col in ["open", "high", "low", "close", "volume"]:
            if df[col].isna().any():
                df[col] = df[col].fillna(method="ffill", limit=3)
                df[col] = df[col].fillna(method="bfill", limit=3)
        return df

    # ─── جلب أفضل العملات (مع Timeout قصير) ───
    async def fetch_top_symbols(self, limit: int = 50) -> List[str]:
        for base_url in BINANCE_ENDPOINTS:
            try:
                await limiter.acquire()
                session = await self._get_session()
                url = f"{base_url}/api/v3/ticker/24hr"
                async with session.get(url) as resp:
                    limiter.release()
                    resp.raise_for_status()
                    data = await resp.json()
                    usdt_pairs = [
                        item for item in data
                        if item["symbol"].endswith(CFG.QUOTE_ASSET)
                        and float(item.get("quoteVolume", 0)) > 1_000_000
                    ]
                    usdt_pairs.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
                    return [item["symbol"] for item in usdt_pairs[:limit]]
            except Exception as e:
                limiter.release()
                logger.debug(f"Failover fetching symbols from {base_url}", extra={"error": str(e)})
                await asyncio.sleep(0.5)
                continue
        # إذا فشل كل شيء، نعيد قائمة فارغة (سيتم التعامل معها في bot.py)
        logger.warning("Failed to fetch top symbols, returning empty list")
        return []

    # ─── جلب بيانات 24 ساعة ───
    async def fetch_24hr_tickers(self) -> List[Dict]:
        for base_url in BINANCE_ENDPOINTS:
            try:
                await limiter.acquire()
                session = await self._get_session()
                url = f"{base_url}/api/v3/ticker/24hr"
                async with session.get(url) as resp:
                    limiter.release()
                    resp.raise_for_status()
                    data = await resp.json()
                    return [
                        {
                            "symbol": item["symbol"],
                            "change_24h": float(item.get("priceChangePercent", 0)),
                            "volume_24h": float(item.get("quoteVolume", 0)),
                            "high": float(item.get("highPrice", 0)),
                            "low": float(item.get("lowPrice", 0)),
                        }
                        for item in data
                        if item["symbol"].endswith(CFG.QUOTE_ASSET)
                    ]
            except Exception as e:
                limiter.release()
                logger.debug(f"Failover 24hr from {base_url}", extra={"error": str(e)})
                await asyncio.sleep(0.5)
                continue
        return []

    # ─── جلب فلاتر العملة ───
    async def get_symbol_filters(self, symbol: str) -> Dict:
        if symbol in _symbol_filters_cache:
            return _symbol_filters_cache[symbol]
        for base_url in BINANCE_ENDPOINTS:
            try:
                await limiter.acquire()
                session = await self._get_session()
                url = f"{base_url}/api/v3/exchangeInfo"
                async with session.get(url) as resp:
                    limiter.release()
                    resp.raise_for_status()
                    data = await resp.json()
                    for s in data["symbols"]:
                        if s["symbol"] == symbol:
                            filters = {}
                            for f in s["filters"]:
                                if f["filterType"] == "PRICE_FILTER":
                                    filters["tick_size"] = float(f["tickSize"])
                                elif f["filterType"] == "LOT_SIZE":
                                    filters["step_size"] = float(f["stepSize"])
                                    filters["min_qty"] = float(f["minQty"])
                            _symbol_filters_cache[symbol] = filters
                            return filters
            except Exception:
                continue
        return {"tick_size": 0.0001, "step_size": 0.000001, "min_qty": 0.0}

    def adjust_price(self, price: float, tick_size: float) -> float:
        if tick_size <= 0:
            return round(price, 8)
        return round(round(price / tick_size) * tick_size, 8)

    def adjust_quantity(self, qty: float, step_size: float, min_qty: float) -> float:
        if step_size <= 0:
            return round(qty, 8)
        adjusted = round(round(qty / step_size) * step_size, 8)
        return max(adjusted, min_qty)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

fetcher = DataFetcher()

# ─── دوال مساعدة ───
def safe_divide(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()

class AdaptiveWeights:
    def __init__(self, initial_weights: Dict[str, float]):
        self.weights = initial_weights.copy()
        self.performance_history: Dict[str, List[float]] = {k: [] for k in initial_weights}

    def update(self, factor: str, result: float):
        self.performance_history[factor].append(result)
        self.performance_history[factor] = self.performance_history[factor][-30:]

    def recalculate(self):
        scores = {}
        for factor, history in self.performance_history.items():
            scores[factor] = sum(1 for r in history if r > 0) / len(history) if history else 1.0
        total = sum(scores.values())
        if total > 0:
            self.weights = {k: v / total for k, v in scores.items()}
            logger.info("Adaptive weights updated", extra={"weights": self.weights})
