# bot.py
# Main Application v2026.3 - Rebuilt with Circuit Breaker + Near-Miss + Fixed Bugs

import asyncio
import json
import signal
import time
from datetime import datetime, timezone, timedelta
import aiohttp
from aiohttp import web
import config
from database import Database
from utils import (
    DataFetcher, AdaptiveWeights, klines_to_dataframe, logger,
)
from signals import SignalEngine
from telegram_bot import TelegramBot


# ============================================================
# Web Handlers
# ============================================================
async def handle_health(request):
    return web.json_response({
        "status": "healthy",
        "service": "Quant Signal Engine v2026.3",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, status=200)


async def handle_telegram_webhook(request):
    try:
        data = await request.json()
        bot_instance = request.app['bot_instance']
        await bot_instance.telegram.process_update(data)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.Response(status=500)


async def handle_status_api(request):
    bot_instance = request.app['bot_instance']
    try:
        open_signals = await bot_instance.db.get_open_signals()
        daily_stats = await bot_instance.db.get_daily_pnl()
        subscribers = await bot_instance.db.get_subscribers()
        return web.json_response({
            "open_trades": len(open_signals),
            "daily_pnl": daily_stats.get("pnl", 0.0),
            "wins": daily_stats.get("wins", 0),
            "losses": daily_stats.get("losses", 0),
            "subscribers": len(subscribers),
            "known_symbols": len(bot_instance.known_symbols),
            "circuit_breaker": bot_instance.circuit_breaker_active,
        }, status=200)
    except Exception as e:
        logger.error(f"Status API error: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_accuracy_api(request):
    bot_instance = request.app['bot_instance']
    try:
        days = int(request.query.get("days", 30))
        overall = await bot_instance.db.get_accuracy_stats(days)
        by_score = await bot_instance.db.get_accuracy_by_score(days)
        by_type = await bot_instance.db.get_accuracy_by_type(days)
        return web.json_response({
            "period_days": days,
            "overall": overall,
            "by_score": by_score,
            "by_type": by_type,
        }, status=200)
    except Exception as e:
        logger.error(f"Accuracy API error: {e}")
        return web.json_response({"error": str(e)}, status=500)


# ============================================================
# TradingBot Class
# ============================================================
class TradingBot:
    def __init__(self):
        self.running = True
        self.fetcher = DataFetcher()
        self.db = Database()
        self.weights = AdaptiveWeights()
        self.engine = SignalEngine(self.weights)
        self.telegram = TelegramBot(self.db, self.engine, self.fetcher)
        self.last_data_update = {}
        self.last_data_update_max = config.MAX_LAST_DATA_UPDATE
        self.scan_counter = 0
        self.daily_capital = config.INITIAL_CAPITAL
        self.last_health_alert = 0
        self.tasks = []
        self.known_symbols = set(config.CORE_UNIVERSE)
        self.web_runner = None
        self.scan_semaphore = asyncio.Semaphore(5)
        self.last_daily_summary_date = None
        # 🆕 Circuit Breaker (من multi-market-trading-bot)[reference:37]
        self.circuit_breaker_active = False
        self.consecutive_losses = 0
        self.circuit_breaker_until = None

    # ============================================================
    # Web Server
    # ============================================================
    async def init_web_server(self):
        app = web.Application()
        app['bot_instance'] = self
        app.router.add_get("/", handle_health)
        app.router.add_get("/health", handle_health)
        app.router.add_post(config.WEBHOOK_PATH, handle_telegram_webhook)
        app.router.add_get("/api/status", handle_status_api)
        app.router.add_get("/api/accuracy", handle_accuracy_api)
        self.web_runner = web.AppRunner(app)
        await self.web_runner.setup()
        site = web.TCPSite(self.web_runner, "0.0.0.0", config.PORT)
        await site.start()
        logger.info(f"🌐 Web Server on port {config.PORT}")
        return self.web_runner

    async def setup_telegram_webhook(self):
        if config.TELEGRAM_USE_WEBHOOK and config.WEBHOOK_URL:
            wb = config.WEBHOOK_URL.rstrip("/")
            if wb.endswith("/webhook"):
                wb = wb[:-8]
            full = f"{wb}{config.WEBHOOK_PATH}"
            if await self.telegram.set_webhook(full):
                logger.info(f"✅ Webhook: {full}")
        else:
            logger.info("ℹ️ Polling mode")

    async def load_weights(self):
        saved = await self.db.get_weights()
        if saved:
            self.weights = AdaptiveWeights(saved)
            self.engine = SignalEngine(self.weights)
            self.telegram.signal_engine = self.engine
            logger.info(f"Weights loaded: {self.weights.to_dict()}")

    # ============================================================
    # Cooldown & Circuit Breaker
    # ============================================================
    async def cooldown_allowed(self, symbol, direction):
        record = await self.db.get_cooldown(symbol)
        if not record:
            return True
        try:
            created = datetime.fromisoformat(record["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - created).total_seconds()
        except Exception:
            return True
        same_limit = config.COOLDOWN_MINUTES * 60
        opp_limit = config.OPPOSITE_COOLDOWN_HOURS * 3600
        if record["direction"] == direction:
            return elapsed >= same_limit
        return elapsed >= opp_limit

    async def daily_loss_exceeded(self):
        stats = await self.db.get_daily_pnl()
        limit = -self.daily_capital * config.DAILY_MAX_LOSS_PERCENT
        return stats["pnl"] <= limit

    # 🆕 Circuit Breaker (من multi-market-trading-bot)[reference:38]
    async def check_circuit_breaker(self):
        if not config.ENABLE_CIRCUIT_BREAKER:
            return False
        if self.circuit_breaker_active:
            if self.circuit_breaker_until and datetime.now(timezone.utc) < self.circuit_breaker_until:
                return True
            self.circuit_breaker_active = False
            self.circuit_breaker_until = None
            self.consecutive_losses = 0
            logger.info("✅ Circuit breaker reset")

        if self.consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
            self.circuit_breaker_active = True
            self.circuit_breaker_until = datetime.now(timezone.utc) + timedelta(hours=12)
            await self.telegram.broadcast(
                f"🚨 <b>Circuit Breaker Activated</b>\n\n"
                f"خسائر متتالية: {self.consecutive_losses}\n"
                f"سيتوقف النظام 12 ساعة"
            )
            logger.warning(f"Circuit breaker: {self.consecutive_losses} consecutive losses")
            return True
        return False

    async def is_rate_limited(self):
        if await self.db.count_signals_today() >= config.MAX_SIGNALS_PER_DAY:
            return True
        if await self.db.count_recent_signals_global(hours=1) >= config.MAX_SIGNALS_PER_HOUR:
            return True
        return False

    async def is_duplicate(self, symbol):
        count = await self.db.count_recent_signals_for_symbol(
            symbol, hours=config.DUPLICATE_WINDOW_HOURS)
        return count >= config.MAX_SIGNALS_PER_SYMBOL_PER_DAY

    # ============================================================
    # Symbol Validation
    # ============================================================
    def is_valid_symbol(self, symbol):
        if not symbol or not symbol.endswith("USDT"):
            return False
        if symbol in config.EXCLUDED_SYMBOLS:
            return False
        base = symbol[:-4]
        if not base:
            return False
        if config.EXCLUDE_TOKENIZED_STOCKS and base.endswith("B") and len(base) > 2:
            known = {"BNB", "ARB", "BGB", "WIF", "SATS", "ORDI", "VIB", "SNB", "ACB"}
            if base not in known:
                return False
        for suffix in config.EXCLUDED_SUFFIXES:
            if symbol.endswith(suffix):
                return False
        if base[0].isdigit():
            return False
        if not base.isascii() or not base.isalnum():
            return False
        if len(base) < 2 or len(base) > 10:
            return False
        return True

    # ============================================================
    # Scan Symbol
    # ============================================================
    async def scan_symbol(self, symbol):
        async with self.scan_semaphore:
            try:
                if await self.is_duplicate(symbol):
                    return
                try:
                    klines = await asyncio.wait_for(
                        self.fetcher.klines(symbol, config.ANALYSIS_INTERVAL, config.KLINE_LIMIT),
                        timeout=15.0)
                except asyncio.TimeoutError:
                    return
                if not klines:
                    return
                self.last_data_update[symbol] = time.time()
                if len(self.last_data_update) > self.last_data_update_max:
                    oldest = min(self.last_data_update, key=self.last_data_update.get)
                    del self.last_data_update[oldest]

                df = klines_to_dataframe(klines)
                if len(df) < 60:
                    return
                try:
                    klines_15m = await asyncio.wait_for(
                        self.fetcher.klines(symbol, config.TREND_INTERVAL, 50),
                        timeout=15.0)
                except asyncio.TimeoutError:
                    klines_15m = None
                df_15m = klines_to_dataframe(klines_15m) if klines_15m else None

                result = self.engine.analyze(symbol, df, self.daily_capital, df_15m)
                if not result:
                    return
                if not await self.cooldown_allowed(symbol, result["direction"]):
                    return

                signal_id = await self.db.add_signal(result)
                if not signal_id:
                    return
                result["signal_id"] = signal_id

                entry_dt = datetime.now(timezone.utc)
                await self.db.create_outcome(
                    signal_id=signal_id,
                    symbol=result["symbol"],
                    direction=result["direction"],
                    signal_type=result["signal_type"],
                    entry_price=result["entry"],
                    entry_time=entry_dt,
                )
                formatted = self.telegram.format_signal(result)
                await self.telegram.broadcast(formatted)
                await self.db.set_cooldown(symbol, result["direction"])
                logger.info(
                    f"📊 Signal: {symbol} {result['direction']} "
                    f"type={result['signal_type']} score={result['score']} (id={signal_id})"
                )
            except Exception as e:
                logger.exception(f"scan_symbol error {symbol}: {e}")

    async def scan_market(self):
        # ✅ فحص مرة واحدة (Bug #4 fix)
        if await self.daily_loss_exceeded():
            logger.info("Daily loss limit reached")
            return
        if await self.is_rate_limited():
            logger.info("Rate limit reached")
            return
        if await self.check_circuit_breaker():
            logger.info("Circuit breaker active")
            return

        prewatch = await self.db.get_prewatch(config.MAX_PREWATCH_TO_SCAN)
        prewatch_syms = [
            item["symbol"] for item in prewatch
            if item["symbol"] not in self.known_symbols
            and self.is_valid_symbol(item["symbol"])
        ]
        symbols = list(dict.fromkeys(list(self.known_symbols) + prewatch_syms))
        tasks = [self.scan_symbol(s) for s in symbols]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.error(f"Scan task: {r}")

    async def scan_prewatch(self):
        data = await self.fetcher.ticker_24h()
        if not isinstance(data, list):
            return
        added = 0
        for item in data:
            symbol = item.get("symbol", "").upper()
            if not self.is_valid_symbol(symbol):
                continue
            if symbol in self.known_symbols:
                continue
            try:
                change = float(item.get("priceChangePercent", 0))
                volume = float(item.get("quoteVolume", 0))
                trades = int(item.get("count", 0))
                price = float(item.get("lastPrice", 0))
            except (ValueError, TypeError):
                continue
            if volume < config.PREWATCH_MIN_VOLUME_USDT:
                continue
            if trades < config.PREWATCH_MIN_TRADES:
                continue
            if price > config.PREWATCH_MAX_PRICE or price < config.PREWATCH_MIN_PRICE:
                continue
            if abs(change) > config.PREWATCH_PRICE_CHANGE or volume > config.PREWATCH_VOLUME_USDT:
                reasons = []
                if abs(change) > config.PREWATCH_PRICE_CHANGE:
                    reasons.append("price_move")
                if volume > config.PREWATCH_VOLUME_USDT:
                    reasons.append("high_volume")
                await self.db.add_prewatch(symbol, ",".join(reasons), change, volume, trades, price)
                self.known_symbols.add(symbol)
                added += 1
        if added > 0:
            logger.info(f"📋 Pre-watch: Added {added} symbols")

    # ============================================================
    # Track Signal Outcomes (Fixed Bug #1 + #2)
    # ============================================================
    async def track_signal_outcomes(self):
        while self.running:
            try:
                pending = await self.db.get_pending_outcomes()
                for outcome in pending:
                    try:
                        await self._process_outcome(outcome)
                    except Exception as e:
                        logger.error(f"Outcome error signal={outcome.get('signal_id')}: {e}")
                await self._maybe_send_daily_summary()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"Tracker error: {exc}")
            await asyncio.sleep(config.TRACKER_INTERVAL)

    async def _process_outcome(self, outcome):
        signal_id = outcome["signal_id"]
        symbol = outcome["symbol"]
        direction = outcome["direction"]
        entry_price = float(outcome["entry_price"])
        sl = float(outcome["sl"])
        tp = float(outcome["tp"])
        quantity = float(outcome.get("quantity", 0))

        entry_time = outcome["entry_time"]
        if isinstance(entry_time, str):
            entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
        else:
            entry_dt = entry_time
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=timezone.utc)

        # ✅ Bug #2 fix: fetch from entry time
        entry_ms = int(entry_dt.timestamp() * 1000)
        try:
            klines = await asyncio.wait_for(
                self.fetcher.request("/api/v3/klines", {
                    "symbol": symbol.upper(),
                    "interval": config.ANALYSIS_INTERVAL,
                    "startTime": entry_ms,
                    "limit": 500,
                }), timeout=15.0)
        except asyncio.TimeoutError:
            return
        if not isinstance(klines, list) or not klines:
            return

        highest = float(outcome.get("highest_price") or entry_price)
        lowest = float(outcome.get("lowest_price") or entry_price)
        max_dd = float(outcome.get("max_drawdown_during_trade") or 0.0)
        outcome_result = None
        hit_tp = 0
        hit_sl = 0
        exit_price = entry_price
        risk = abs(entry_price - sl)

        for candle in klines:
            candle_time = datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc)
            if candle_time <= entry_dt:
                continue
            high = float(candle[2])
            low = float(candle[3])
            if high > highest:
                highest = high
            if low < lowest:
                lowest = low
            if direction == "BUY":
                dd = (highest - low) / entry_price
            else:
                dd = (high - lowest) / entry_price
            if dd > max_dd:
                max_dd = dd

            if direction == "BUY":
                hit_tp_c = high >= tp
                hit_sl_c = low <= sl
            else:
                hit_tp_c = low <= tp
                hit_sl_c = high >= sl

            if hit_sl_c and hit_tp_c:
                outcome_result = "LOSS"; hit_sl = 1; exit_price = sl; break
            elif hit_sl_c:
                outcome_result = "LOSS"; hit_sl = 1; exit_price = sl; break
            elif hit_tp_c:
                outcome_result = "WIN"; hit_tp = 1; exit_price = tp; break

        candles_since = [c for c in klines
                         if datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc) > entry_dt]
        if outcome_result is None:
            if len(candles_since) >= config.OUTCOME_CHECK_CANDLES:
                outcome_result = "TIMEOUT"
                exit_price = float(candles_since[-1][4])
            else:
                await self.db.update_outcome_highlow(signal_id, highest, lowest)
                return

        if risk > 0:
            if direction == "BUY":
                r_multiple = (exit_price - entry_price) / risk
            else:
                r_multiple = (entry_price - exit_price) / risk
        else:
            r_multiple = 0.0

        pips_gained = max(0.0, (exit_price - entry_price) if direction == "BUY"
                          else (entry_price - exit_price))
        pips_lost = max(0.0, (entry_price - exit_price) if direction == "BUY"
                        else (exit_price - entry_price))
        duration_min = int((datetime.now(timezone.utc) - entry_dt).total_seconds() / 60)

        await self.db.update_outcome_highlow(signal_id, highest, lowest)
        await self.db.finalize_outcome(
            signal_id=signal_id, exit_price=exit_price, outcome=outcome_result,
            hit_tp=hit_tp, hit_sl=hit_sl, r_multiple=round(r_multiple, 4),
            duration_minutes=duration_min,
            pips_gained=round(pips_gained, 8), pips_lost=round(pips_lost, 8),
            max_dd=round(max_dd, 6),
        )

        # ✅ Bug #1 fix: use actual risk amount
        if quantity > 0:
            actual_risk_amount = quantity * abs(entry_price - sl)
        else:
            actual_risk_amount = self.daily_capital * config.RISK_PER_TRADE
        result_amount = r_multiple * actual_risk_amount
        await self.db.close_signal(signal_id, result_amount, r_multiple, outcome_result)

        category = "win" if outcome_result == "WIN" else (
            "loss" if outcome_result == "LOSS" else "timeout")
        await self.db.add_daily_result(self.daily_capital, result_amount, category)

        # Circuit breaker tracking
        if outcome_result == "LOSS":
            self.consecutive_losses += 1
        elif outcome_result == "WIN":
            self.consecutive_losses = 0

        # Update weights
        signal_row = await self.db.get_signal(signal_id)
        if signal_row and signal_row.get("factor_contributions"):
            try:
                fc = signal_row["factor_contributions"]
                if isinstance(fc, str):
                    fc = json.loads(fc)
            except Exception:
                fc = {}
            success = outcome_result == "WIN"
            for factor, contrib in fc.items():
                if factor in config.FACTORS:
                    self.weights.update(factor, success, contribution=contrib)
                    await self.db.save_weight(factor, self.weights.weights[factor])

        logger.info(f"📈 Outcome: signal={signal_id} {symbol} {outcome_result} R={r_multiple:.2f}")
        if config.TELEGRAM_ADMIN_ID and outcome_result in ("WIN", "LOSS"):
            emoji = "✅" if outcome_result == "WIN" else "❌"
            await self.telegram.send_message(
                config.TELEGRAM_ADMIN_ID,
                f"{emoji} <b>إشارة #{signal_id}</b>\n"
                f"{symbol} {direction}\n"
                f"النتيجة: <b>{outcome_result}</b> ({r_multiple:.2f}R)\n"
                f"المدة: {duration_min} دقيقة"
            )

    # ============================================================
    # Daily Summary (Fixed Bug #5)
    # ============================================================
    async def _maybe_send_daily_summary(self):
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        if now.hour > 2:
            return
        last_sent = await self.db.get_bot_state("last_summary_date")
        if last_sent == today:
            return
        try:
            y = await self.db.get_accuracy_stats(days=1)
            w = await self.db.get_accuracy_stats(days=7)
            m = await self.db.get_accuracy_stats(days=30)
            text = (
                f"📊 <b>ملخص يومي</b> — {today}\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📅 <b>24 ساعة:</b> {y['total']} إشارة | Win {y['win_rate']}% | R {y['avg_r']}\n"
                f"📆 <b>7 أيام:</b> {w['total']} إشارة | Win {w['win_rate']}% | R {w['avg_r']}\n"
                f"📈 <b>30 يوم:</b> {m['total']} إشارة | Win {m['win_rate']}% | R {m['avg_r']}"
            )
            await self.telegram.broadcast(text)
            await self.db.set_bot_state("last_summary_date", today)
            logger.info("📊 Daily summary sent")
        except Exception as e:
            logger.error(f"Daily summary error: {e}")

    # ============================================================
    # Health Monitor
    # ============================================================
    async def health_monitor(self):
        while self.running:
            try:
                await asyncio.sleep(config.HEALTH_CHECK_INTERVAL)
                now = time.time()
                stale = [s for s, ts in self.last_data_update.items() if now - ts > 900]
                if len(stale) > 3 and now - self.last_health_alert > config.HEALTH_CHECK_INTERVAL:
                    await self.telegram.broadcast(
                        f"⚠️ <b>تنبيه صحة البيانات</b>\nعملات متوقفة: {len(stale)}"
                    )
                    self.last_health_alert = now
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"Health error: {exc}")

    # ============================================================
    # Self Ping
    # ============================================================
    async def self_ping(self):
        url = config.RENDER_EXTERNAL_URL or f"http://127.0.0.1:{config.PORT}/health"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while self.running:
                try:
                    async with session.get(url) as r:
                        logger.debug(f"Ping: {r.status}")
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.warning(f"Ping failed: {exc}")
                await asyncio.sleep(config.SELF_PING_INTERVAL)

    # ============================================================
    # Scanner Loop
    # ============================================================
    async def scanner_loop(self):
        while self.running:
            started = time.monotonic()
            try:
                self.scan_counter += 1
                if self.scan_counter % config.PREWATCH_SCAN_EVERY == 0:
                    await self.scan_prewatch()
                await self.scan_market()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"Scanner error: {exc}")
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(1, config.SCAN_INTERVAL - elapsed))

    # ============================================================
    # Start / Shutdown
    # ============================================================
    async def start(self):
        config.validate_config()
        logger.info(f"🔧 DB: {'PostgreSQL' if config.USE_POSTGRES else 'SQLite'}")
        logger.info(f"🔧 Telegram: {'Webhook' if config.TELEGRAM_USE_WEBHOOK else 'Polling'}")
        try:
            await self.db.init()
            logger.info("✅ DB initialized")
        except Exception as e:
            logger.error(f"❌ DB init failed: {e}")
            return

        await self.load_weights()
        await self.fetcher.start()
        await self.init_web_server()
        await self.setup_telegram_webhook()
        await self.telegram.start()

        if config.TELEGRAM_ADMIN_ID:
            await self.db.add_subscriber(config.TELEGRAM_ADMIN_ID)
            await self.telegram.send_message(
                config.TELEGRAM_ADMIN_ID,
                "✅ <b>النظام v2026.3 يعمل!</b>\n\n"
                "🆕 الجديد:\n"
                "• Market Regime Detection (ADX+ER)\n"
                "• Circuit Breaker تلقائي\n"
                "• Near-Miss Logging\n"
                "• Kaufman Efficiency Ratio\n"
                "• 3 أنواع إشارات\n"
                "• تتبع نتائج كامل\n"
                "• حماية من التكرار"
            )

        self.tasks = [
            asyncio.create_task(self.scanner_loop()),
            asyncio.create_task(self.track_signal_outcomes()),
            asyncio.create_task(self.health_monitor()),
            asyncio.create_task(self.self_ping()),
        ]
        logger.info("🚀 System v2026.3 started")
        try:
            while self.running:
                await asyncio.sleep(1)
        finally:
            await self.shutdown()

    async def shutdown(self):
        logger.info("🛑 Shutting down...")
        if config.TELEGRAM_USE_WEBHOOK:
            await self.telegram.delete_webhook()
        self.running = False
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.web_runner:
            await self.web_runner.cleanup()
        await self.telegram.close()
        await self.fetcher.close()
        await self.db.close()
        logger.info("✅ Shutdown complete")

    def stop(self):
        self.running = False


# ============================================================
# Main
# ============================================================
async def main():
    bot = TradingBot()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, bot.stop)
        except NotImplementedError:
            pass
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
