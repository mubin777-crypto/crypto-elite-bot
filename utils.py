# utils.py - مع دوال المراقبة الاستباقية
import asyncio
import aiohttp
import logging
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional

from config import config

logger = logging.getLogger(__name__)

# -------------------- دوال جلب البيانات الحالية (نفس السابق) --------------------
async def fetch_binance_com_klines(session, symbol, interval='5m', limit=50):
    try:
        url = f"{config.BINANCE_COM_BASE}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        async with session.get(url, headers=headers, timeout=10) as resp:
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
        logger.debug(f"Binance.com error for {symbol}: {e}")
    return None

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

async def fetch_klines(session, symbol, interval='5m', limit=50, retries=3):
    await asyncio.sleep(config.RATE_LIMIT_DELAY)
    for attempt in range(retries):
        data = await fetch_binance_com_klines(session, symbol, interval, limit)
        if data and len(data['prices']) > 10:
            return data
        data = await fetch_binance_us_klines(session, symbol, interval, limit)
        if data and len(data['prices']) > 10:
            return data
        data = await fetch_coinbase_klines(session, symbol, interval, limit)
        if data and len(data['prices']) > 10:
            return data
        if attempt < retries - 1:
            await asyncio.sleep(2 ** attempt)
    return None

# -------------------- دوال جلب البيانات العامة --------------------
async def fetch_24hr_stats(session, symbol):
    """جلب إحصائيات 24 ساعة من Binance.com أو .us"""
    try:
        url = f"{config.BINANCE_COM_BASE}/api/v3/ticker/24hr?symbol={symbol}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        async with session.get(url, headers=headers, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "volume": float(data.get('quoteVolume', 0)),
                    "change_24h": float(data.get('priceChangePercent', 0)),
                    "high": float(data.get('highPrice', 0)),
                    "low": float(data.get('lowPrice', 0)),
                    "open": float(data.get('openPrice', 0)),
                    "last": float(data.get('lastPrice', 0))
                }
    except:
        pass
    try:
        url = f"{config.BINANCE_US_BASE}/api/v3/ticker/24hr?symbol={symbol}"
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "volume": float(data.get('quoteVolume', 0)),
                    "change_24h": float(data.get('priceChangePercent', 0)),
                    "high": float(data.get('highPrice', 0)),
                    "low": float(data.get('lowPrice', 0)),
                    "open": float(data.get('openPrice', 0)),
                    "last": float(data.get('lastPrice', 0))
                }
    except:
        pass
    # Coinbase fallback
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
                return {
                    "volume": volume,
                    "change_24h": change,
                    "high": float(data.get('high', 0)),
                    "low": float(data.get('low', 0)),
                    "open": open_price,
                    "last": last
                }
    except:
        pass
    return {"volume": 0, "change_24h": 0, "high": 0, "low": 0, "open": 0, "last": 0}

# -------------------- دوال المراقبة الاستباقية --------------------
async def scan_market_for_opportunities(session):
    """
    مسح السوق بالكامل لاكتشاف العملات التي تظهر عليها علامات الانفجار
    تعمل بشكل مستقل عن القائمة الأساسية
    """
    opportunities = []
    try:
        # جلب جميع العملات من Binance.com
        url = f"{config.BINANCE_COM_BASE}/api/v3/ticker/24hr"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        async with session.get(url, headers=headers, timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                for item in data:
                    symbol = item.get('symbol', '')
                    if not symbol.endswith('USDT'):
                        continue
                    # استبعاد العملات المستقرة والرافعات
                    if any(stable in symbol for stable in ["USDC", "FDUSD", "TUSD", "BUSD", "DAI"]):
                        continue
                    if any(ex in symbol for ex in ["UP", "DOWN", "BULL", "BEAR", "HALF"]):
                        continue
                    
                    volume = float(item.get('quoteVolume', 0))
                    change = float(item.get('priceChangePercent', 0))
                    high = float(item.get('highPrice', 0))
                    low = float(item.get('lowPrice', 0))
                    open_price = float(item.get('openPrice', 0))
                    last = float(item.get('lastPrice', 0))
                    
                    # فحص العلامات الأولية
                    if volume < config.PRE_WATCH_MIN_VOLUME:
                        continue
                    if abs(change) < config.PRE_WATCH_MIN_CHANGE:
                        continue
                    
                    # حساب نقاط الفرصة
                    score = 0
                    reasons = []
                    
                    # 1. الزخم السعري (40 نقطة)
                    if change > 5.0:
                        score += 40
                        reasons.append(f"زخم صاعد قوي {change:.1f}%")
                    elif change > 3.0:
                        score += 30
                        reasons.append(f"زخم صاعد {change:.1f}%")
                    elif change > 2.0:
                        score += 20
                        reasons.append(f"زخم معتدل {change:.1f}%")
                    elif change < -3.0:
                        score += 30
                        reasons.append(f"انهيار {change:.1f}%")
                    elif change < -2.0:
                        score += 20
                        reasons.append(f"تراجع {change:.1f}%")
                    
                    # 2. الحجم (30 نقطة)
                    if volume > 50_000_000:
                        score += 30
                        reasons.append(f"حجم ضخم (${volume/1_000_000:.1f}M)")
                    elif volume > 10_000_000:
                        score += 20
                        reasons.append(f"حجم كبير (${volume/1_000_000:.1f}M)")
                    elif volume > 5_000_000:
                        score += 10
                        reasons.append(f"حجم متوسط (${volume/1_000_000:.1f}M)")
                    
                    # 3. التقلب (20 نقطة)
                    if high > 0 and low > 0:
                        volatility = ((high - low) / low) * 100
                        if volatility > 10:
                            score += 20
                            reasons.append(f"تقلب عالٍ {volatility:.1f}%")
                        elif volatility > 5:
                            score += 10
                            reasons.append(f"تقلب متوسط {volatility:.1f}%")
                    
                    # 4. القيمة السوقية التقريبية (10 نقاط)
                    # تقدير تقريبي باستخدام الحجم * السعر / 100
                    approx_market_cap = volume * last / 100 if last > 0 else 0
                    if 10_000_000 < approx_market_cap < 500_000_000:
                        score += 10
                        reasons.append(f"قيمة سوقية مناسبة (${approx_market_cap/1_000_000:.1f}M)")
                    elif approx_market_cap < 10_000_000:
                        score += 5
                        reasons.append(f"قيمة سوقية صغيرة (${approx_market_cap/1_000_000:.1f}M)")
                    
                    # إضافة العملة إذا حصلت على نقاط كافية
                    if score >= 50:  # عتبة الدخول للمراقبة
                        opportunities.append({
                            "symbol": symbol,
                            "price": last,
                            "change_24h": change,
                            "volume_24h": volume,
                            "market_cap_approx": approx_market_cap,
                            "score": score,
                            "reasons": reasons,
                            "high": high,
                            "low": low
                        })
    except Exception as e:
        logger.error(f"Error scanning market: {e}")
    
    # ترتيب حسب النقاط
    opportunities.sort(key=lambda x: x["score"], reverse=True)
    return opportunities[:config.PRE_WATCH_MAX_SYMBOLS]

async def analyze_pre_watch_candidate(session, candidate):
    """
    تحليل عميق للمرشح للتحقق من جودة الفرصة
    """
    symbol = candidate["symbol"]
    data_5m = await fetch_klines(session, symbol, '5m', 100)
    if not data_5m:
        return None
    
    # حساب المؤشرات الأساسية
    from indicators import Indicators
    prices = data_5m['prices']
    highs = data_5m['highs']
    lows = data_5m['lows']
    volumes = data_5m['volumes']
    
    rsi = Indicators.calculate_rsi(prices, config.RSI_PERIOD)
    adx = Indicators.calculate_adx(highs, lows, prices, config.ADX_PERIOD)
    volume_ratio = volumes[-1] / (sum(volumes[-12:]) / 12) if len(volumes) >= 12 else 1
    
    # تحديث النقاط بناءً على التحليل العميق
    final_score = candidate["score"]
    reasons = candidate["reasons"].copy()
    
    if rsi > 70:
        final_score += 10
        reasons.append(f"RSI مرتفع ({rsi:.1f}) - زخم قوي")
    elif rsi > 60:
        final_score += 5
        reasons.append(f"RSI جيد ({rsi:.1f})")
    
    if adx > 25:
        final_score += 15
        reasons.append(f"اتجاه قوي (ADX {adx:.1f})")
    elif adx > 20:
        final_score += 10
        reasons.append(f"اتجاه متوسط (ADX {adx:.1f})")
    
    if volume_ratio > 3.0:
        final_score += 15
        reasons.append(f"انفجار حجم ({volume_ratio:.1f}x)")
    elif volume_ratio > 2.0:
        final_score += 10
        reasons.append(f"حجم مرتفع ({volume_ratio:.1f}x)")
    
    return {
        "symbol": symbol,
        "price": candidate["price"],
        "change_24h": candidate["change_24h"],
        "volume_24h": candidate["volume_24h"],
        "market_cap": candidate["market_cap_approx"],
        "score": final_score,
        "reasons": reasons,
        "rsi": rsi,
        "adx": adx,
        "volume_ratio": volume_ratio
    }

# -------------------- دوال مساعدة أخرى --------------------
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

async def fetch_top_symbols(session, limit=100):
    """جلب العملات النشطة من Binance.com"""
    try:
        url = f"{config.BINANCE_COM_BASE}/api/v3/ticker/24hr"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        async with session.get(url, headers=headers, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                candidates = []
                for item in data:
                    symbol = item.get('symbol', '')
                    if not symbol.endswith('USDT'):
                        continue
                    if any(stable in symbol for stable in ["USDC", "FDUSD", "TUSD", "BUSD", "DAI"]):
                        continue
                    if any(ex in symbol for ex in ["UP", "DOWN", "BULL", "BEAR", "HALF"]):
                        continue
                    volume = float(item.get('quoteVolume', 0))
                    change = float(item.get('priceChangePercent', 0))
                    if volume < 100_000:
                        continue
                    candidates.append((symbol, volume, abs(change)))
                candidates.sort(key=lambda x: x[1], reverse=True)
                return [sym for sym, _, _ in candidates[:limit]]
    except Exception as e:
        logger.error(f"Binance.com error: {e}")
    # Fallback
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
                    if volume < 100_000:
                        continue
                    candidates.append((symbol, volume, abs(change)))
                candidates.sort(key=lambda x: x[1], reverse=True)
                return [sym for sym, _, _ in candidates[:limit]]
    except Exception as e:
        logger.error(f"Binance.US error: {e}")
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
