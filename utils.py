"""
utils.py - طبقة جلب البيانات مع Timeout قصير.
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

# ─── إعادة ترتيب النقاط (الأقل حظراً أولاً) ───
BINANCE_ENDPOINTS = [
    "https://api.binance.us",
    "https://data-api.binance.vision",
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

            # 🔥 تقليل المهلة إلى 3 ثوانٍ
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
        logger.warning("Failed to fetch top symbols, returning empty list")
        return []

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
                        if item["symbol"].
