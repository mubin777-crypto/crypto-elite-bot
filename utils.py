# utils.py
# DataFetcher + RateLimiter + AdaptiveWeights

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
import aiohttp
import pandas as pd
import config  # ✅ تم التعديل: استخدام import config بدلاً من from config import CFG

# ============================================================
# JSON Logger
# ============================================================
class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)

def setup_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(handler)
    else:
        root.handlers.clear()
        root.addHandler(handler)
    root.setLevel(config.LOG_LEVEL.upper())
    return logging.getLogger("quant_bot")

logger = setup_logging()

# ============================================================
# Rate Limiter
# ============================================================
class RateLimiter:
    def __init__(self, max_calls=8, period=1.0):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
        self.lock = asyncio.Lock()

    async def acquire(self):
        while True:
            async with self.lock:
                now = time.monotonic()
                self.calls = [t for t in self.calls if now - t < self.period]
                if len(self.calls) < self.max_calls:
                    self.calls.append(now)
                    return
                wait_time = self.period - (now - self.calls[0])
            await asyncio.sleep(max(0.01, wait_time))

# ============================================================
# Binance DataFetcher
# ============================================================
class DataFetcher:
    def __init__(self):
        self.session = None
        self.limiter = RateLimiter(max_calls=8, period=1.0)
        self.semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_REQUESTS)
        self.endpoint_index = 0

    async def start(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(
                total=config.BINANCE_TIMEOUT,
                connect=3,
                sock_read=4
            )
            connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={"User-Agent": "QuantCryptoSignalSystem/2026"}
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None
            logger.info("Binance session closed")

    async def _request(self, endpoint, path, params=None):
        await self.start()
        async with self.semaphore:
            await self.limiter.acquire()
            await asyncio.sleep(config.REQUEST_DELAY)
            url = endpoint + path
            try:
                async with self.session.get(url, params=params) as response:
                    if response.status != 200:
                        text = await response.text()
                        raise RuntimeError(f"HTTP {response.status}: {text[:200]}")
                    return await response.json()
            except asyncio.TimeoutError:
                raise RuntimeError(f"Timeout: {url}")
            except Exception as exc:
                raise RuntimeError(f"Request failed: {url} | {exc}")

    async def request(self, path, params=None):
        endpoints = list(config.BINANCE_ENDPOINTS)
        start = self.endpoint_index % len(endpoints)
        ordered = endpoints[start:] + endpoints[:start]
        for endpoint in ordered:
            for attempt in range(config.BINANCE_RETRIES + 1):
                try:
                    data = await self._request(endpoint, path, params)
                    self.endpoint_index = (endpoints.index(endpoint) + 1) % len(endpoints)
                    return data
                except Exception as exc:
                    logger.warning(f"Binance endpoint failed: {endpoint} | attempt={attempt+1} | {exc}")
                    if attempt < config.BINANCE_RETRIES:
                        await asyncio.sleep(0.25 * (attempt + 1))
        logger.error(f"All Binance endpoints failed: {path}")
        return None

    async def klines(self, symbol, interval="5m", limit=250):
        data = await self.request("/api/v3/klines", {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit,
        })
        return data if isinstance(data, list) else []

    async def ticker_24h(self, symbol=None):
        params = {}
        if symbol:
            params["symbol"] = symbol.upper()
        data = await self.request("/api/v3/ticker/24hr", params)
        return data

    async def exchange_info(self):
        return await self.request("/api/v3/exchangeInfo")

# ============================================================
# Klines -> DataFrame
# ============================================================
def klines_to_dataframe(klines):
    if not klines:
        return pd.DataFrame()
    columns = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_base",
        "taker_quote", "ignore",
    ]
    df = pd.DataFrame(klines, columns=columns)
    numeric_columns = ["open", "high", "low", "close", "volume", "quote_volume"]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])
    return df.reset_index(drop=True)

# ============================================================
# Adaptive Weights
# ============================================================
class AdaptiveWeights:
    def __init__(self, initial=None):
        self.weights = {factor: 1.0 for factor in config.FACTORS}
        if initial:
            for factor, weight in initial.items():
                if factor in self.weights:
                    self.weights[factor] = float(max(0.5, min(1.5, weight)))

    def update(self, factor, success):
        if factor not in self.weights:
            return
        alpha = 0.05
        target = 1.10 if success else 0.90
        old = self.weights[factor]
        new = old * (1 - alpha) + target * alpha
        self.weights[factor] = max(0.5, min(1.5, new))

    def normalize(self):
        total = sum(self.weights.values())
        if total <= 0:
            return dict(self.weights)
        count = len(self.weights)
        return {key: value * count / total for key, value in self.weights.items()}

    def get(self, factor, default=1.0):
        return self.normalize().get(factor, default)

    def to_dict(self):
        return self.normalize().copy()
