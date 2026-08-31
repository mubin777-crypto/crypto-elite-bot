"""
utils.py - طبقة جلب البيانات مع حلول Binance و Proxies احتياطية.
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

# ─── 🔥 مصادر البيانات مع Proxies احتياطية ───
BINANCE_ENDPOINTS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api.binance.us",
]

# 🔥 Proxies احتياطية (مجانية) يمكن استخدامها إذا استمر الحظر
PROXY_LIST = [
    None,  # اتصال مباشر أولاً
    "http://proxy1.example.com:8080",  # يمكنك إضافة Proxies حقيقية هنا
    "http://proxy2.example.com:8080",
]

_symbol_filters_cache: Dict[str, Dict] = {}

class DataFetcher:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self._proxy_index = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            timeout = aiohttp.ClientTimeout(
                total=30,
                connect=15,
                sock_read=15
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
        
        # 🔥 محاولة مع كل Proxy
        for proxy in PROXY_LIST:
            for base_url in BINANCE_ENDPOINTS:
                try:
                    await limiter.acquire()
                    session = await self._get_session()
                    url = f"{base_url}/api/v3/klines"
                    
                    # استخدام Proxy إذا كان محدداً
                    async with session.get(url, params=params, proxy=proxy) as resp:
                        limiter.release()
                        if resp.status == 429:
                            logger.warning("Rate limit hit", extra={"symbol": symbol})
                            await asyncio.sleep(2)
                            continue
                        resp.raise_for_status()
                        data = await resp.json()
                        if not data:
                            continue
                        df = self._parse_klines(data)
                        df = self._clean_data(df)
                        return df
                except aiohttp.ClientError as e:
                    limiter.release()
                    logger.warning(f"Failover from {base_url} via {proxy or 'direct'}", extra={"error": str(e), "symbol": symbol})
                    await asyncio.sleep(1)
                    continue
                except asyncio.TimeoutError:
                    limiter.release()
                    logger.warning(f"Timeout from {base_url}", extra={"symbol": symbol})
                    await asyncio.sleep(2)
                    continue
                except Exception as e:
                    limiter.release()
                    logger.error(f"Error fetching {symbol}", extra={"error": str(e)})
                    raise
            # إذا فشلت جميع النقاط لـ Proxy معين، انتقل إلى التالي
            await asyncio.sleep(1)
        
        raise RuntimeError(f"All endpoints and proxies failed for {symbol}")

    # ... باقي الدوال (parse_klines, clean_data, fetch_top_symbols, etc.) كما هي ...

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
