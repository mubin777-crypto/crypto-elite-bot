# telegram_bot.py
# Telegram Webhook & Polling Interface

import asyncio
import logging
import aiohttp
import html
import config
import time
from datetime import datetime, timezone

logger = logging.getLogger("quant_bot.telegram")

class TelegramBot:
    def __init__(self, database, signal_engine, data_fetcher):
        self.database = database
        self.signal_engine = signal_engine
        self.data_fetcher = data_fetcher
        self.token = config.TELEGRAM_BOT_TOKEN
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.session = None
        self.webhook_mode = config.TELEGRAM_USE_WEBHOOK
        self.admin_id = config.TELEGRAM_ADMIN_ID
        self.polling_task = None
        self.last_update_id = 0

    def safe_text(self, text):
        return html.escape(str(text))

    async def start(self):
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
        timeout = aiohttp.ClientTimeout(total=config.TELEGRAM_API_TIMEOUT)
        self.session = aiohttp.ClientSession(timeout=timeout)

        if self.webhook_mode:
            webhook_url = config.WEBHOOK_URL.rstrip("/") + config.WEBHOOK_PATH
            result = await self.api_call("setWebhook", {
                "url": webhook_url,
                "drop_pending_updates": False,
                "allowed_updates": ["message"],
            })
            if not result or not result.get("ok", False):
                logger.warning("Failed to register webhook, falling back to polling")
                self.webhook_mode = False
            else:
                logger.info(f"Telegram webhook registered: {webhook_url}")

        if not self.webhook_mode:
            await self.api_call("deleteWebhook", {"drop_pending_updates": True})
            logger.info("Telegram polling mode selected")
            self.polling_task = asyncio.create_task(self.polling_loop())

        if self.admin_id:
            await self.send_message(self.admin_id, "🤖 <b>Bot started successfully!</b>")

    async def close(self):
        if self.polling_task:
            self.polling_task.cancel()
            try:
                await self.polling_task
            except asyncio.CancelledError:
                pass
        if self.session:
            await self.session.close()
            self.session = None

    async def polling_loop(self):
        """حلقة Polling لتلقي التحديثات من Telegram"""
        while True:
            try:
                await asyncio.sleep(1)
                params = {"offset": self.last_update_id + 1, "timeout": 10}
                result = await self.api_call("getUpdates", params)
                if result and result.get("ok"):
                    updates = result.get("result", [])
                    for update in updates:
                        self.last_update_id = update.get("update_id", self.last_update_id)
                        await self.handle_update(update)
                else:
                    logger.warning("Polling error: no updates")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Polling loop error: {exc}")
                await asyncio.sleep(5)

    async def api_call(self, method, payload=None, retries=config.TELEGRAM_MAX_RETRIES):
        if not self.session:
            return None
        backoff = config.TELEGRAM_RETRY_BACKOFF_BASE
        for attempt in range(retries + 1):
            try:
                async with self.session.post(f"{self.base_url}/{method}", json=payload or {}) as response:
                    data = await response.json()
                    if not data.get("ok"):
                        if response.status == 429:
                            retry_after = data.get("parameters", {}).get("retry_after", 5)
                            logger.warning(f"Rate limited. Retry after {retry_after}s")
                            await asyncio.sleep(retry_after)
                            continue
                        logger.error(f"Telegram API error: {data}")
                    return data
            except asyncio.TimeoutError:
                logger.warning(f"Telegram API timeout (attempt {attempt+1})")
                if attempt < retries:
                    await asyncio.sleep(backoff)
                    backoff *= 2
            except Exception as exc:
                logger.error(f"Telegram API request failed: {exc}")
                if attempt < retries:
                    await asyncio.sleep(backoff)
                    backoff *= 2
        logger.error(f"All retries failed for {method}")
        return None

    async def send_message(self, chat_id, text, parse_mode="HTML", retries=config.TELEGRAM_MAX_RETRIES):
        if not text:
            return None
        if len(text) > 4096:
            text = text[:4000] + "\n... (مقطوع)"
        backoff = config.TELEGRAM_RETRY_BACKOFF_BASE
        for attempt in range(retries + 1):
            try:
                result = await self.api_call("sendMessage", {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                })
                if result and result.get("ok"):
                    return result
            except Exception as exc:
                logger.warning(f"Send attempt {attempt+1} exception for {chat_id}: {exc}")
            if attempt < retries:
                await asyncio.sleep(backoff)
                backoff *= 2
        return None

    async def broadcast(self, text):
        subscribers = await self.database.get_subscribers()
        count = len(subscribers)
        logger.info(f"📢 Broadcasting to {count} subscribers")
        if count == 0:
            logger.warning("⚠️ No active subscribers found!")
            if self.admin_id:
                await self.send_message(self.admin_id, "⚠️ No active subscribers. Please add users with /adduser")
            return

        if self.admin_id and self.admin_id not in subscribers:
            subscribers = [self.admin_id] + subscribers

        success_count = 0
        failure_count = 0
        for user_id in subscribers:
            try:
                result = await self.send_message(user_id, text)
                if result and result.get("ok"):
                    success_count += 1
                else:
                    failure_count += 1
                await asyncio.sleep(0.05)
            except Exception as exc:
                failure_count += 1
                logger.warning(f"Broadcast exception for {user_id}: {exc}")

        logger.info(f"✅ Broadcast: {success_count}/{len(subscribers)} sent, {failure_count} failed")

    async def handle_update(self, update):
        if not isinstance(update, dict):
            return
        message = update.get("message")
        if not message:
            return
        chat = message.get("chat", {})
        user = message.get("from", {})
        chat_id = chat.get("id")
        user_id = user.get("id")
        text = (message.get("text", "") or "").strip()
        if chat_id is None or user_id is None:
            return

        command = text.split()[0].lower() if text else ""
        logger.info(f"📩 Command: {command} from {user_id}")

        if command.startswith("/start"):
            await self.send_message(chat_id, self.help_text())
        elif command.startswith("/status"):
            await self.status(chat_id)
        elif command.startswith("/prewatch"):
            await self.prewatch(chat_id)
        elif command.startswith("/performance"):
            await self.performance(chat_id)
        elif command.startswith("/subscribers"):
            await self.subscribers_list(chat_id, user_id)
        elif command.startswith("/signal"):
            parts = text.split()
            if len(parts) != 2:
                await self.send_message(chat_id, "الاستخدام:\n/signal BTCUSDT")
            else:
                await self.signal(chat_id, parts[1].upper())
        elif command.startswith("/adduser"):
            if not self.is_admin(user_id):
                await self.send_message(chat_id, "⛔ Admin only.")
                return
            parts = text.split()
            if len(parts) != 2:
                await self.send_message(chat_id, "Usage: /adduser USER_ID")
                return
            try:
                target_id = int(parts[1])
                if await self.database.add_subscriber(target_id):
                    await self.send_message(chat_id, f"✅ Subscriber {target_id} added.")
                else:
                    await self.send_message(chat_id, "❌ Failed to add subscriber.")
            except ValueError:
                await self.send_message(chat_id, "❌ USER_ID must be numeric.")
        elif command.startswith("/removeuser"):
            if not self.is_admin(user_id):
                await self.send_message(chat_id, "⛔ Admin only.")
                return
            parts = text.split()
            if len(parts) != 2:
                await self.send_message(chat_id, "Usage: /removeuser USER_ID")
                return
            try:
                target_id = int(parts[1])
                if await self.database.remove_subscriber(target_id):
                    await self.send_message(chat_id, f"✅ Subscriber {target_id} removed.")
                else:
                    await self.send_message(chat_id, "❌ Failed to remove subscriber.")
            except ValueError:
                await self.send_message(chat_id, "❌ USER_ID must be numeric.")
        elif command.startswith("/reset_daily"):
            if not self.is_admin(user_id):
                await self.send_message(chat_id, "⛔ Admin only.")
                return
            await self.database.reset_daily(config.INITIAL_CAPITAL)
            await self.send_message(chat_id, "✅ Daily statistics reset.")
        else:
            await self.send_message(chat_id, "❓ أمر غير معروف. استخدم /start للمساعدة.")

    def is_admin(self, user_id):
        return int(user_id) == int(self.admin_id)

    def help_text(self):
        return (
            "🤖 <b>Quant Crypto Signal System v2026</b>\n\n"
            "/status - حالة النظام\n"
            "/prewatch - قائمة المراقبة\n"
            "/performance - الأداء\n"
            "/subscribers - قائمة المشتركين (للمشرف)\n"
            "/signal BTCUSDT - تحليل فوري\n"
            "/adduser USER_ID - إضافة مشترك\n"
            "/removeuser USER_ID - حذف مشترك\n"
            "/reset_daily - إعادة الإحصائيات"
        )

    async def subscribers_list(self, chat_id, user_id):
        if not self.is_admin(user_id):
            await self.send_message(chat_id, "⛔ Admin only.")
            return
        subs = await self.database.get_subscribers()
        count = len(subs)
        if count == 0:
            await self.send_message(chat_id, "📭 No subscribers.")
            return
        lines = [f"📋 Subscribers ({count}):"]
        for uid in subs:
            lines.append(f"• {uid}")
        await self.send_message(chat_id, "\n".join(lines))

    async def status(self, chat_id):
        signals = await self.database.get_daily_signals()
        stats = await self.database.get_daily_pnl()
        prewatch = await self.database.get_prewatch(20)
        subscribers = await self.database.get_subscribers()
        text = (
            f"📊 <b>System Status</b>\n\n"
            f"Signals today: {self.safe_text(len(signals))}\n"
            f"Daily PnL: {self.safe_text(f'{stats['pnl']:.2f}')}\n"
            f"W/L/B/I/T: {stats['wins']}/{stats['losses']}/{stats['breakeven']}/{stats['inconclusive']}/{stats['timeout']}\n"
            f"Pre-watch: {self.safe_text(len(prewatch))}\n"
            f"Subscribers: {self.safe_text(len(subscribers))}"
        )
        await self.send_message(chat_id, text)

    async def prewatch(self, chat_id):
        items = await self.database.get_prewatch(10)
        if not items:
            await self.send_message(chat_id, "🔭 Pre-watch فارغة.")
            return
        lines = ["🔭 <b>Pre-watch</b>\n"]
        for item in items:
            lines.append(
                f"• <b>{self.safe_text(item['symbol'])}</b> | "
                f"{self.safe_text(f'{item['price_change']:.2f}')}% | "
                f"${self.safe_text(f'{item['quote_volume']:,.0f}')} | "
                f"Trades: {self.safe_text(item.get('trades', 0))}"
            )
        await self.send_message(chat_id, "\n".join(lines))

    async def performance(self, chat_id):
        signals = await self.database.get_daily_signals()
        closed = [x for x in signals if x["status"] == "CLOSED"]
        if not closed:
            await self.send_message(chat_id, "لا توجد صفقات مغلقة كافية.")
            return
        wins = [x for x in closed if float(x["result_r"]) > 0]
        losses = [x for x in closed if float(x["result_r"]) < 0]
        breakeven = [x for x in closed if float(x["result_r"]) == 0]
        inconclusive = [x for x in closed if x.get("exit_reason") == "INCONCLUSIVE"]
        timeouts = [x for x in closed if x.get("exit_reason") == "TIMEOUT"]
        win_rate = len(wins) / len(closed) * 100
        gross_profit = sum(float(x["result_r"]) for x in wins)
        gross_loss = abs(sum(float(x["result_r"]) for x in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        returns = [float(x["result_r"]) for x in closed]
        import statistics
        if len(returns) > 1:
            avg = statistics.mean(returns)
            stdev = statistics.stdev(returns)
            sharpe = avg / stdev if stdev > 0 else 0
        else:
            sharpe = 0
        pf_text = "INF" if profit_factor == float("inf") else f"{profit_factor:.2f}"
        text = (
            f"📈 <b>Performance</b>\n\n"
            f"Closed: {self.safe_text(len(closed))}\n"
            f"Win/Loss/BE/Inc/TO: {len(wins)}/{len(losses)}/{len(breakeven)}/{len(inconclusive)}/{len(timeouts)}\n"
            f"Win Rate: {self.safe_text(f'{win_rate:.2f}')}%\n"
            f"Profit Factor: {self.safe_text(pf_text)}\n"
            f"Sharpe (R): {self.safe_text(f'{sharpe:.2f}')}"
        )
        await self.send_message(chat_id, text)

    async def signal(self, chat_id, symbol):
        klines = await self.data_fetcher.klines(symbol, config.ANALYSIS_INTERVAL, config.KLINE_LIMIT)
        if not klines:
            await self.send_message(chat_id, f"❌ No data for {self.safe_text(symbol)}.")
            return
        from utils import klines_to_dataframe
        df = klines_to_dataframe(klines)
        klines_15m = await self.data_fetcher.klines(symbol, config.TREND_INTERVAL, 50)
        df_15m = klines_to_dataframe(klines_15m) if klines_15m else None
        result = self.signal_engine.analyze(symbol, df, config.INITIAL_CAPITAL, df_15m)
        if not result:
            await self.send_message(chat_id, f"⚪ No qualified signal for {self.safe_text(symbol)}.")
            return
        await self.send_message(chat_id, self.format_signal(result))

    def format_signal(self, signal):
        def fmt(val):
            if abs(val) < 1e-5:
                return self.safe_text(f"{val:.4e}")
            else:
                return self.safe_text(f"{val:.6f}")

        emoji = "🟢" if signal["direction"] == "BUY" else "🔴"
        snipe = "\n🎯 <b>EARLY SNIPE</b>" if signal.get("early_snipe") else ""
        quality_label = f"Quality: {signal.get('quality', 0)}%" if signal.get('quality') else ""

        return (
            f"{emoji} <b>{self.safe_text(signal['symbol'])}</b>\n\n"
            f"Direction: <b>{self.safe_text(signal['direction'])}</b>\n"
            f"Score: <b>{self.safe_text(signal['score'])}/10</b>\n"
            f"{quality_label}\n\n"
            f"Entry: {fmt(signal['entry'])}\n"
            f"SL: {fmt(signal['sl'])}\n"
            f"TP: {fmt(signal['tp'])}\n"
            f"R/R: {self.safe_text(signal['rr'])}\n"
            f"Position: {fmt(signal['position_size'])}\n\n"
            f"RSI: {self.safe_text(signal['rsi'])}\n"
            f"ADX: {self.safe_text(signal['adx'])}\n"
            f"ATR: {fmt(signal['atr'])}{snipe}\n\n"
            "⚠️ إشارة تحليلية وليست ضماناً للربح.\n"
            "📊 Quality تعبر عن قوة الإشارة وليست احتمالية ربح."
        )
