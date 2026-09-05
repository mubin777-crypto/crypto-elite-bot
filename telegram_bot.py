# telegram_bot.py
# Telegram Webhook Interface

import asyncio
import logging
import aiohttp
import html
import config

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

    # ========================================================
    # Helper: safe text for HTML
    # ========================================================
    def safe_text(self, text):
        return html.escape(str(text))

    # ========================================================
    # Start
    # ========================================================
    async def start(self):
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

        if self.webhook_mode:
            webhook_url = config.WEBHOOK_URL + config.WEBHOOK_PATH
            result = await self.api_call("setWebhook", {
                "url": webhook_url,
                "drop_pending_updates": False,
                "allowed_updates": ["message"],
            })
            if not result or not result.get("ok", False):
                raise RuntimeError("Failed to register Telegram webhook")
            logger.info(f"Telegram webhook registered: {webhook_url}")
        else:
            await self.api_call("deleteWebhook", {"drop_pending_updates": True})
            logger.info("Telegram polling mode selected")

    # ========================================================
    # Close
    # ========================================================
    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    # ========================================================
    # Telegram API
    # ========================================================
    async def api_call(self, method, payload=None):
        if not self.session:
            return None
        try:
            async with self.session.post(f"{self.base_url}/{method}", json=payload or {}) as response:
                data = await response.json()
                if not data.get("ok"):
                    logger.error(f"Telegram API error: {data}")
                return data
        except Exception as exc:
            logger.error(f"Telegram API request failed: {exc}")
            return None

    # ========================================================
    # Send message with retries
    # ========================================================
    async def send_message(self, chat_id, text, retries=3):
        for attempt in range(retries):
            try:
                result = await self.api_call("sendMessage", {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                })
                if result and result.get("ok"):
                    return result
                logger.warning(f"Send attempt {attempt+1} failed for {chat_id}: {result}")
            except Exception as exc:
                logger.warning(f"Send attempt {attempt+1} exception for {chat_id}: {exc}")
            if attempt < retries - 1:
                await asyncio.sleep(1)
        logger.error(f"All retries failed for chat_id {chat_id}")
        return None

    # ========================================================
    # Broadcast with logging
    # ========================================================
    async def broadcast(self, text):
        subscribers = await self.database.get_subscribers()
        logger.info(f"Broadcasting to {len(subscribers)} subscribers")
        for user_id in subscribers:
            try:
                await self.send_message(user_id, text)
                logger.debug(f"Sent to {user_id}")
                await asyncio.sleep(0.05)
            except Exception as exc:
                logger.warning(f"Broadcast failed for {user_id}: {exc}")

    # ========================================================
    # Admin check
    # ========================================================
    def is_admin(self, user_id):
        return int(user_id) == int(config.TELEGRAM_ADMIN_ID)

    # ========================================================
    # Update Handler
    # ========================================================
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
        if command.startswith("/start"):
            await self.send_message(chat_id, self.help_text())
        elif command.startswith("/status"):
            await self.status(chat_id)
        elif command.startswith("/prewatch"):
            await self.prewatch(chat_id)
        elif command.startswith("/performance"):
            await self.performance(chat_id)
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
                await self.database.add_subscriber(target_id)
                await self.send_message(chat_id, "✅ Subscriber added.")
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
                await self.database.remove_subscriber(target_id)
                await self.send_message(chat_id, "✅ Subscriber removed.")
            except ValueError:
                await self.send_message(chat_id, "❌ USER_ID must be numeric.")
        elif command.startswith("/reset_daily"):
            if not self.is_admin(user_id):
                await self.send_message(chat_id, "⛔ Admin only.")
                return
            await self.database.reset_daily(config.INITIAL_CAPITAL)
            await self.send_message(chat_id, "✅ Daily statistics reset.")

    # ========================================================
    # Help text
    # ========================================================
    def help_text(self):
        return (
            "🤖 <b>Quant Crypto Signal System v2026</b>\n\n"
            "/status - حالة النظام\n"
            "/prewatch - قائمة المراقبة\n"
            "/performance - الأداء\n"
            "/signal BTCUSDT - تحليل فوري\n"
            "/adduser USER_ID - إضافة مشترك\n"
            "/removeuser USER_ID - حذف مشترك\n"
            "/reset_daily - إعادة الإحصائيات"
        )

    # ========================================================
    # Status
    # ========================================================
    async def status(self, chat_id):
        signals = await self.database.get_daily_signals()
        pnl = await self.database.get_daily_pnl()
        prewatch = await self.database.get_prewatch(20)
        subscribers = await self.database.get_subscribers()
        await self.send_message(chat_id,
            f"📊 <b>System Status</b>\n\n"
            f"Signals today: {self.safe_text(len(signals))}\n"
            f"Daily PnL: {self.safe_text(f'{pnl:.2f}')}\n"
            f"Pre-watch: {self.safe_text(len(prewatch))}\n"
            f"Subscribers: {self.safe_text(len(subscribers))}"
        )

    # ========================================================
    # Prewatch
    # ========================================================
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
                f"${self.safe_text(f'{item['quote_volume']:,.0f}')}"
            )
        await self.send_message(chat_id, "\n".join(lines))

    # ========================================================
    # Performance
    # ========================================================
    async def performance(self, chat_id):
        signals = await self.database.get_daily_signals()
        closed = [x for x in signals if x["status"] == "CLOSED"]
        if not closed:
            await self.send_message(chat_id, "لا توجد صفقات مغلقة كافية.")
            return

        wins = [x for x in closed if float(x["result_r"]) > 0]
        losses = [x for x in closed if float(x["result_r"]) < 0]
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
        await self.send_message(chat_id,
            f"📈 <b>Performance</b>\n\n"
            f"Closed: {self.safe_text(len(closed))}\n"
            f"Win Rate: {self.safe_text(f'{win_rate:.2f}')}%\n"
            f"Profit Factor: {self.safe_text(pf_text)}\n"
            f"Sharpe (R): {self.safe_text(f'{sharpe:.2f}')}"
        )

    # ========================================================
    # Instant signal
    # ========================================================
    async def signal(self, chat_id, symbol):
        klines = await self.data_fetcher.klines(symbol, config.ANALYSIS_INTERVAL, config.KLINE_LIMIT)
        if not klines:
            await self.send_message(chat_id, f"❌ No data for {self.safe_text(symbol)}.")
            return
        from utils import klines_to_dataframe
        df = klines_to_dataframe(klines)
        # جلب 15m للترند
        klines_15m = await self.data_fetcher.klines(symbol, config.TREND_INTERVAL, 50)
        df_15m = klines_to_dataframe(klines_15m) if klines_15m else None
        result = self.signal_engine.analyze(symbol, df, config.INITIAL_CAPITAL, df_15m)
        if not result:
            await self.send_message(chat_id, f"⚪ No qualified signal for {self.safe_text(symbol)}.")
            return
        await self.send_message(chat_id, self.format_signal(result))

    # ========================================================
    # Signal formatter (fully escaped)
    # ========================================================
    def format_signal(self, signal):
        def fmt(val):
            if abs(val) < 1e-5:
                return self.safe_text(f"{val:.4e}")
            else:
                return self.safe_text(f"{val:.6f}")

        emoji = "🟢" if signal["direction"] == "BUY" else "🔴"
        snipe = "\n🎯 <b>EARLY SNIPE</b>" if signal.get("early_snipe") else ""

        return (
            f"{emoji} <b>{self.safe_text(signal['symbol'])}</b>\n\n"
            f"Direction: <b>{self.safe_text(signal['direction'])}</b>\n"
            f"Score: <b>{self.safe_text(signal['score'])}/10</b>\n"
            f"Strength: {self.safe_text(signal['strength'])}%\n\n"
            f"Entry: {fmt(signal['entry'])}\n"
            f"SL: {fmt(signal['sl'])}\n"
            f"TP: {fmt(signal['tp'])}\n"
            f"R/R: {self.safe_text(signal['rr'])}\n"
            f"Position: {fmt(signal['position_size'])}\n\n"
            f"RSI: {self.safe_text(signal['rsi'])}\n"
            f"ADX: {self.safe_text(signal['adx'])}\n"
            f"ATR: {fmt(signal['atr'])}{snipe}\n\n"
            "⚠️ إشارة تحليلية وليست ضماناً للربح."
        )
