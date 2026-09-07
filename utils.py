# utils.py
# DataFetcher + WebSocket + RateLimiter + AdaptiveWeights

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import aiohttp
import pandas as pd
import config
import websockets

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
# Binance WebSocket Client
# ============================================================
class BinanceWebSocket:
    def __init__(self):
        self.websocket = None
        self.subscribed_symbols = set()
        self.price_data = {}
        self.price_data_max_size = 200
        self.callbacks = []
        self.running = False
        self.reconnect_delay = 1.0
        self.task_semaphore = asyncio.Semaphore(50)

    async def connect(self):
        for endpoint in config.BINANCE_WS_ENDPOINTS:
            try:
                self.websocket = await websockets.connect(endpoint)
                logger.info(f"WebSocket connected: {endpoint}")
                return True
            except Exception as e:
                logger.warning(f"WebSocket connection failed: {endpoint} | {e}")
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, 30)
        logger.error("All WebSocket endpoints failed")
        return False

    async def _subscribe_symbol(self, symbol: str):
        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": [f"{symbol.lower()}@trade"],
            "id": int(time.time())
        }
        await self.websocket.send(json.dumps(subscribe_msg))
        logger.debug(f"WebSocket subscribed: {symbol}")

    async def subscribe(self, symbol: str):
        if symbol in self.subscribed_symbols:
            return
        await self._subscribe_symbol(symbol)
        self.subscribed_symbols.add(symbol)

    async def start(self):
        self.running = True
        while self.running:
            try:
                if not self.websocket:
                    if not await self.connect():
                        await asyncio.sleep(self.reconnect_delay)
                        continue
                    for symbol in list(self.subscribed_symbols):
                        await self._subscribe_symbol(symbol)

                message = await self.websocket.recv()
                data = json.loads(message)

                if "data" in data and "s" in data.get("data", {}):
                    trade = data.get("data", {})
                    symbol = trade.get("s")
                    price = float(trade.get("p", 0))
                    if symbol and price > 0:
                        if len(self.price_data) > self.price_data_max_size:
                            keys = list(self.price_data.keys())[:int(self.price_data_max_size * 0.2)]
                            for k in keys:
                                del self.price_data[k]

                        self.price_data[symbol] = {
                            "price": price,
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        }

                        for callback in self.callbacks:
                            async with self.task_semaphore:
                                asyncio.create_task(callback(symbol, price))

            except websockets.ConnectionClosed:
                logger.warning("WebSocket connection closed, reconnecting...")
                self.websocket = None
                self.reconnect_delay = min(self.reconnect_delay * 2, 30)
                await asyncio.sleep(self.reconnect_delay)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                await asyncio.sleep(1)

    def add_callback(self, callback):
        self.callbacks.append(callback)

    async def stop(self):
        self.running = False
        if self.websocket:
            await self.websocket.close()

# ============================================================
# Binance DataFetcher
# ============================================================
class DataFetcher:
    def __init__(self):
        self.session = None
        self.limiter = RateLimiter(max_calls=8, period=1.0)
        self.semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_REQUESTS)
        self.endpoint_index = 0
        self.websocket = BinanceWebSocket()
        self.websocket_task = None
        self.price_cache = {}
        self.last_data_update = {}
        self.last_data_update_max = config.MAX_LAST_DATA_UPDATE
        self._initialized = False

    async def start(self):
        if self._initialized:
            return self.session

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

        if not self.websocket_task or self.websocket_task.done():
            self.websocket.add_callback(self._on_price_update)
            self.websocket_task = asyncio.create_task(self.websocket.start())

        self._initialized = True
        return self.session

    async def _on_price_update(self, symbol: str, price: float):
        self.price_cache[symbol] = price
        self.last_data_update[symbol] = time.time()

        if len(self.last_data_update) > self.last_data_update_max:
            oldest = min(self.last_data_update, key=self.last_data_update.get)
            del self.last_data_update[oldest]

    async def close(self):
        if self.websocket_task and not self.websocket_task.done():
            await self.websocket.stop()
            self.websocket_task.cancel()
            try:
                await self.websocket_task
            except asyncio.CancelledError:
                pass

        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None
            self._initialized = False
            logger.info("Binance session closed")

    async def _request(self, endpoint, path, params=None):
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
            except aiohttp.ClientResponseError as e:
                if e.status == 451:
                    raise RuntimeError(f"Geo-blocked: {url}")
                raise RuntimeError(f"HTTP {e.status}: {url}")
            except aiohttp.ClientError as e:
                raise RuntimeError(f"Request failed: {url} | {e}")
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Invalid JSON: {url} | {e}")

    async def request(self, path, params=None):
        for endpoint in config.BINANCE_ENDPOINTS:
            for attempt in range(config.BINANCE_RETRIES + 1):
                try:
                    data = await self._request(endpoint, path, params)
                    return data
                except Exception as exc:
                    logger.warning(f"Binance endpoint failed: {endpoint} | attempt={attempt+1} | {exc}")
                    if attempt < config.BINANCE_RETRIES:
                        await asyncio.sleep(0.25 * (attempt + 1))
        logger.error(f"All Binance endpoints failed: {path}")
        return None

    async def get_price(self, symbol: str) -> Optional[float]:
        if symbol in self.price_cache:
            return self.price_cache[symbol]

        data = await self.request("/api/v3/ticker/price", {"symbol": symbol.upper()})
        if data and "price" in data:
            price = float(data["price"])
            self.price_cache[symbol] = price
            return price
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
                    self.weights[factor] = float(max(0.3, min(2.0, weight)))
        self.performance = {factor: {"correct": 0, "incorrect": 0} for factor in config.FACTORS}

    def update(self, factor, success, contribution=None):
        if factor not in self.weights:
            return

        if success:
            self.performance[factor]["correct"] += 1
        else:
            self.performance[factor]["incorrect"] += 1

        alpha = 0.05

        if contribution is not None:
            if success:
                target = 1.0 + (0.3 * contribution)
            else:
                target = 1.0 - (0.3 * contribution)
        else:
            total = self.performance[factor]["correct"] + self.performance[factor]["incorrect"]
            if total > 0:
                success_rate = self.performance[factor]["correct"] / total
                target = 1.0 + (success_rate - 0.5) * 0.6
            else:
                target = 1.0

        old = self.weights[factor]
        new = old * (1 - alpha) + target * alpha
        self.weights[factor] = max(0.3, min(2.0, new))

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
