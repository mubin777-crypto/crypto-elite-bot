# utils.py
# DataFetcher + WebSocket + RateLimiter + AdaptiveWeights
# v2026.3 - With endpoint memory, cached weights, fixed WS callback

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
                self.websocket = await websockets.connect(
                    endpoint,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                    max_size=2**20,
                )
                logger.info(f"WebSocket connected: {endpoint}")
                self.reconnect_delay = 1.0
                return True
            except Exception as e:
                logger.warning(f"WS failed: {endpoint} | {e}")
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, 30)
        logger.error("All WebSocket endpoints failed")
        return False

    async def _subscribe_symbol(self, symbol: str):
        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": [f"{symbol.lower()}@trade"],
            "id": int(time.time() * 1000) % 2147483647,
        }
        await self.websocket.send(json.dumps(subscribe_msg))
        logger.debug(f"WS subscribed: {symbol}")

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
                    # إعادة الاشتراك في كل الرموز بعد إعادة الاتصال
                    for symbol in list(self.subscribed_symbols):
                        try:
                            await self._subscribe_symbol(symbol)
                        except Exception as e:
                            logger.warning(f"Resubscribe failed {symbol}: {e}")

                message = await self.websocket.recv()
                data = json.loads(message)

                if "data" in data and "s" in data.get("data", {}):
                    trade = data.get("data", {})
                    symbol = trade.get("s")
                    try:
                        price = float(trade.get("p", 0))
                    except (ValueError, TypeError):
                        continue
                    if symbol and price > 0:
                        # 🆕 حد أقصى للـ price cache
                        if len(self.price_data) > self.price_data_max_size:
                            keys = list(self.price_data.keys())[:int(self.price_data_max_size * 0.2)]
                            for k in keys:
                                del self.price_data[k]

                        self.price_data[symbol] = {
                            "price": price,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }

                        # ✅ إصلاح: await callback بدل create_task
                        for callback in self.callbacks:
                            async with self.task_semaphore:
                                try:
                                    await callback(symbol, price)
                                except Exception as cb_exc:
                                    logger.debug(f"WS callback error: {cb_exc}")

            except websockets.ConnectionClosed:
                logger.warning("WS closed, reconnecting...")
                self.websocket = None
                self.reconnect_delay = min(self.reconnect_delay * 2, 30)
                await asyncio.sleep(self.reconnect_delay)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WS error: {e}")
                self.websocket = None
                await asyncio.sleep(2)

    def add_callback(self, callback):
        self.callbacks.append(callback)

    async def stop(self):
        self.running = False
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception:
                pass
            self.websocket = None


# ============================================================
# Binance DataFetcher
# ============================================================
class DataFetcher:
    def __init__(self):
        self.session = None
        self.limiter = RateLimiter(max_calls=8, period=1.0)
        self.semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_REQUESTS)
        self.websocket = BinanceWebSocket()
        self.websocket_task = None
        self.price_cache = {}
        self.last_data_update = {}
        self.last_data_update_max = config.MAX_LAST_DATA_UPDATE
        self._initialized = False
        # 🆕 Endpoint memory
        self._good_endpoint = None
        self._bad_endpoints = {}

    async def start(self):
        if self._initialized:
            return self.session

        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=config.BINANCE_TIMEOUT, connect=3, sock_read=4)
            connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={"User-Agent": "QuantCryptoSignalSystem/2026.3"},
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

    # ============================================================
    # Internal Request
    # ============================================================
    async def _request(self, endpoint, path, params=None):
        async with self.semaphore:
            await self.limiter.acquire()
            await asyncio.sleep(config.REQUEST_DELAY)
            url = endpoint + path
            try:
                async with self.session.get(url, params=params) as response:
                    if response.status == 418:
                        # IP Ban
                        retry_after = response.headers.get("Retry-After", "120")
                        logger.critical(f"⚠️ IP BANNED by Binance. Retry after {retry_after}s")
                        raise RuntimeError(f"IP_BANNED: {retry_after}")
                    if response.status == 429:
                        retry_after = response.headers.get("Retry-After", "10")
                        logger.warning(f"Rate limited. Wait {retry_after}s")
                        raise RuntimeError(f"RATE_LIMITED: {retry_after}")
                    if response.status != 200:
                        text = await response.text()
                        raise RuntimeError(f"HTTP {response.status}: {text[:200]}")
                    return await response.json()
            except asyncio.TimeoutError:
                raise RuntimeError(f"Timeout: {url}")
            except aiohttp.ClientResponseError as e:
                raise RuntimeError(f"HTTP {e.status}: {url}")
            except aiohttp.ClientError as e:
                raise RuntimeError(f"Request failed: {url} | {e}")
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Invalid JSON: {url} | {e}")

    # ============================================================
    # ✅ Public request with endpoint memory
    # ============================================================
    async def request(self, path, params=None):
        """
        يحاول كل الـ endpoints بالترتيب.
        - يتذكر آخر endpoint ناجح (يبدأ به أولاً)
        - يتخطى الـ endpoints الفاشلة خلال آخر 5 دقائق
        - يعيد None لو فشلت كل الـ endpoints
        """
        now = time.time()
        endpoints = list(config.BINANCE_ENDPOINTS)
        # ابدأ بآخر endpoint ناجح
        if self._good_endpoint and self._good_endpoint in endpoints:
            endpoints.remove(self._good_endpoint)
            endpoints.insert(0, self._good_endpoint)
        # تخطى الفاشلة حديثاً
        healthy = [ep for ep in endpoints if now - self._bad_endpoints.get(ep, 0) > 300]
        if not healthy:
            healthy = endpoints
            self._bad_endpoints.clear()

        for endpoint in healthy:
            for attempt in range(config.BINANCE_RETRIES + 1):
                try:
                    data = await self._request(endpoint, path, params)
                    self._good_endpoint = endpoint
                    return data
                except Exception as exc:
                    error_str = str(exc)
                    # IP Ban → توقف كامل
                    if "IP_BANNED" in error_str:
                        logger.critical("🚨 IP Ban detected - cooling down 5 min")
                        self._bad_endpoints[endpoint] = now + 300
                        await asyncio.sleep(300)
                        break
                    logger.warning(
                        f"Binance failed: {endpoint} | attempt={attempt+1} | {exc}"
                    )
                    if attempt < config.BINANCE_RETRIES:
                        await asyncio.sleep(0.25 * (attempt + 1))
                    else:
                        self._bad_endpoints[endpoint] = now
        logger.error(f"All Binance endpoints failed: {path}")
        return None

    # ============================================================
    # Public API methods
    # ============================================================
    async def get_price(self, symbol: str) -> Optional[float]:
        if symbol in self.price_cache:
            return self.price_cache[symbol]
        data = await self.request("/api/v3/ticker/price", {"symbol": symbol.upper()})
        if data and "price" in data:
            price = float(data["price"])
            self.price_cache[symbol] = price
            return price
        return None

    async def klines(self, symbol, interval="5m", limit=250, start_time=None, end_time=None):
        """
        جلب الشموع.
        - start_time / end_time بـ milliseconds (اختياري)
        """
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit,
        }
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        data = await self.request("/api/v3/klines", params)
        return data if isinstance(data, list) else []

    async def klines_since(self, symbol, interval, start_time_ms, limit=500):
        """🆕 helper للـ outcome tracker"""
        return await self.klines(symbol, interval, limit, start_time=start_time_ms)

    async def ticker_24h(self, symbol=None):
        params = {}
        if symbol:
            params["symbol"] = symbol.upper()
        return await self.request("/api/v3/ticker/24hr", params)

    async def exchange_info(self):
        return await self.request("/api/v3/exchangeInfo")

    async def ping(self):
        return await self.request("/api/v3/ping")


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
    # معالجة الحالة التي تكون فيها klines أقل من 12 عموداً
    if len(klines[0]) < 12:
        columns = columns[:len(klines[0])]
    df = pd.DataFrame(klines, columns=columns)
    numeric_cols = ["open", "high", "low", "close", "volume", "quote_volume"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    if "close_time" in df.columns:
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])
    return df.reset_index(drop=True)


# ============================================================
# Adaptive Weights (with cache)
# ============================================================
class AdaptiveWeights:
    """
    أوزان تكيفية لكل عامل مع cache.
    - alpha = 0.05 (بطيء لتجنب التذبذب)
    - clamp [0.3, 2.0]
    - normalize مع cache لتجنب الحساب المتكرر
    """
    def __init__(self, initial=None):
        self.weights = {factor: 1.0 for factor in config.FACTORS}
        if initial:
            for factor, weight in initial.items():
                if factor in self.weights:
                    try:
                        w = float(weight)
                        self.weights[factor] = max(0.3, min(2.0, w))
                    except (ValueError, TypeError):
                        pass
        self.performance = {
            factor: {"correct": 0, "incorrect": 0} for factor in config.FACTORS
        }
        self._normalized_cache = None
        self._cache_dirty = True

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
            total = (self.performance[factor]["correct"]
                     + self.performance[factor]["incorrect"])
            if total > 0:
                success_rate = self.performance[factor]["correct"] / total
                target = 1.0 + (success_rate - 0.5) * 0.6
            else:
                target = 1.0

        old = self.weights[factor]
        new = old * (1 - alpha) + target * alpha
        self.weights[factor] = max(0.3, min(2.0, new))
        self._cache_dirty = True

    def normalize(self):
        if not self._cache_dirty and self._normalized_cache is not None:
            return self._normalized_cache
        total = sum(self.weights.values())
        if total <= 0:
            result = dict(self.weights)
        else:
            count = len(self.weights)
            result = {k: v * count / total for k, v in self.weights.items()}
        self._normalized_cache = result
        self._cache_dirty = False
        return result

    def get(self, factor, default=1.0):
        return self.normalize().get(factor, default)

    def to_dict(self):
        return self.normalize().copy()

    def reset(self):
        self.weights = {f: 1.0 for f in config.FACTORS}
        self.performance = {f: {"correct": 0, "incorrect": 0} for f in config.FACTORS}
        self._cache_dirty = True
