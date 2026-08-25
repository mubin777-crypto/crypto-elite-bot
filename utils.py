# utils.py
import asyncio
import aiohttp
import logging
import time
from datetime import datetime, timezone

from config import config

logger = logging.getLogger(__name__)

async def fetch_binance_us_klines(session, symbol, interval='5m', limit=50):
    try:
        url = f"{config.BINANCE_US_BASE}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data and len(data) > 0:
                    return {
                        "prices": [float(c[4]) for c in data],
                        "highs": [float(c[2]) for c in data],
                        "lows": [float(c[3]) for c in data],
                        "volumes": [float(c[5]) for c in data],
                        "opens": [float(c[1]) for c in data]
                    }
    except Exception as e:
        logger.debug(f"Binance.US error for {symbol}: {e}")
    return None

async def fetch_coinbase_klines(session, symbol, interval='5m', limit=50):
    try:
        symbol_cb = symbol.replace('USDT', '-USD')
        url = f"{config.COINBASE_BASE}/products/{symbol_cb}/candles"
        granularity = 300 if interval == '5m' else 900
        params = {'granularity': granularity, 'limit': limit}
        async with session.get(url, params=params, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data and len(data) > 0:
                    return {
                        "prices": [c[4] for c in data],
                        "highs": [c[2] for c in data],
                        "lows": [c[1] for c in data],
                        "volumes": [c[5] for c in data],
                        "opens": [c[3] for c in data]
                    }
    except Exception as e:
        logger.debug(f"Coinbase error for {symbol}: {e}")
    return None

async def fetch_coincap_klines(session, symbol, interval='5m', limit=50):
    try:
        asset_id = symbol.lower().replace('usdt', '')
        url = f"{config.COINCAP_BASE}/assets/{asset_id}/history"
        interval_map = {'5m': 'm5', '1h': 'h1', '4h': 'h4'}
        params = {'interval': interval_map.get(interval, 'm5'), 'limit': limit}
        async with session.get(url, params=params, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data and 'data' in data:
                    items = data['data']
                    prices = [float(item['priceUsd']) for item in items]
                    highs = [p * 1.02 for p in prices]
                    lows = [p * 0.98 for p in prices]
                    volumes = [float(item.get('volumeUsd', 0)) / p if p > 0 else 0 for p in prices]
                    return {"prices": prices, "highs": highs, "lows": lows, "volumes": volumes, "opens": prices}
    except Exception as e:
        logger.debug(f"CoinCap error for {symbol}: {e}")
    return None

async def fetch_klines(session, symbol, interval='5m', limit=50, retries=3):
    await asyncio.sleep(config.RATE_LIMIT_DELAY)
    for attempt in range(retries):
        data = await fetch_binance_us_klines(session, symbol, interval, limit)
        if data and len(data['prices']) > 10:
            return data
        data = await fetch_coinbase_klines(session, symbol, interval, limit)
        if data and len(data['prices']) > 10:
            return data
        data = await fetch_coincap_klines(session, symbol, interval, limit)
        if data and len(data['prices']) > 10:
            return data
        if attempt < retries - 1:
            await asyncio.sleep(2 ** attempt)
    return None

async def fetch_24hr_stats(session, symbol):
    try:
        url = f"{config.BINANCE_US_BASE}/api/v3/ticker/24hr?symbol={symbol}"
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {"volume": float(data.get('quoteVolume', 0)), "change_24h": float(data.get('priceChangePercent', 0)),
                        "high": float(data.get('highPrice', 0)), "low": float(data.get('lowPrice', 0)),
                        "open": float(data.get('openPrice', 0)), "last": float(data.get('lastPrice', 0))}
    except:
        pass
    try:
        symbol_cb = symbol.replace('USDT', '-USD')
        url = f"{config.COINBASE_BASE}/products/{symbol_cb}/stats"
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                last = float(data.get('last', 0))
                open_price = float(data.get('open', 0))
                change = ((last - open_price) / open_price * 100) if open_price != 0 else 0
                volume = float(data.get('volume', 0)) * last
                return {"volume": volume, "change_24h": change, "high": float(data.get('high', 0)),
                        "low": float(data.get('low', 0)), "open": open_price, "last": last}
    except:
        pass
    return {"volume": 0, "change_24h": 0, "high": 0, "low": 0, "open": 0, "last": 0}

async def fetch_top_symbols(session, limit=70):
    try:
        url = f"{config.BINANCE_US_BASE}/api/v3/ticker/24hr"
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                candidates = []
                for item in data:
                    symbol = item.get('symbol', '')
                    if not symbol.endswith('USDT'):
                        continue
                    volume = float(item.get('quoteVolume', 0))
                    change = float(item.get('priceChangePercent', 0))
                    if volume < config.MIN_VOLUME_USD:
                        continue
                    if abs(change) < config.MIN_VOLATILITY_DAILY:
                        continue
                    candidates.append((symbol, volume, abs(change)))
                candidates.sort(key=lambda x: x[1], reverse=True)
                return [sym for sym, _, _ in candidates[:limit]]
    except Exception as e:
        logger.error(f"Error fetching top symbols: {e}")
    return []

async def fetch_news(session, symbol):
    try:
        url = f"{config.CRYPTOPANIC_BASE}/posts/"
        params = {'currencies': symbol.replace('USDT', ''), 'limit': 5}
        async with session.get(url, params=params, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('results'):
                    important = [item for item in data['results'] if item.get('metadata', {}).get('importance') in ['high', 'medium']]
                    if important:
                        return important[0]
    except Exception as e:
        logger.debug(f"News error for {symbol}: {e}")
    return None

async def self_pinger():
    url = f"https://{config.RENDER_EXTERNAL_HOSTNAME}" if config.RENDER_EXTERNAL_HOSTNAME != "localhost" else f"http://localhost:{config.PORT}"
    logger.info(f"🔄 Self-Pinger started, pinging {url} every 10 minutes")
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        logger.info("✅ Self-ping successful")
                    else:
                        logger.warning(f"⚠️ Self-ping status {resp.status}")
        except Exception as e:
            logger.error(f"❌ Self-ping error: {e}")
        await asyncio.sleep(600)

def symbol_to_currency_name(symbol):
    mapping = {"BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum", "SOLUSDT": "Solana",
               "XRPUSDT": "XRP", "DOGEUSDT": "Dogecoin", "ADAUSDT": "Cardano",
               "DOTUSDT": "Polkadot", "LINKUSDT": "Chainlink", "UNIUSDT": "Uniswap"}
    return mapping.get(symbol, symbol.replace("USDT", ""))
