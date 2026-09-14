# telegram_bot.py
# Telegram Bot - Production Ready with Arabic Signals

import asyncio
import logging
import aiohttp
import html
import config
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
        self.is_running = False
        self._webhook_set = False

    # ============================================================
    # Helper: HTML Escape
    # ============================================================
    def safe_text(self, text):
        return html.escape(str(text))

    # ============================================================
    # Start / Stop
    # ============================================================
    async def start(self):
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN missing")

        self.is_running = True
        timeout = aiohttp.ClientTimeout(total=config.TELEGRAM_API_TIMEOUT)
        self.session = aiohttp.ClientSession(timeout=timeout)

        if self.webhook_mode:
            logger.info("ℹ️ Telegram Webhook mode enabled (will be set by bot.py)")
        else:
            await self.api_call("deleteWebhook", {"drop_pending_updates": True})
            logger.info("ℹ️ Telegram polling mode selected")
            self.polling_task = asyncio.create_task(self.polling_loop())

        if self.admin_id:
            await self.send_message(self.admin_id, "🤖 <b>تم تشغيل البوت بنجاح!</b>")

    async def close(self):
        self.is_running = False
        if self.polling_task and not self.polling_task.done():
            self.polling_task.cancel()
            try:
                await self.polling_task
            except asyncio.CancelledError:
                pass
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    # ============================================================
    # Webhook Management
    # ============================================================
    async def set_webhook(self, webhook_url: str) -> bool:
        if not self.session:
            timeout = aiohttp.ClientTimeout(total=config.TELEGRAM_API_TIMEOUT)
            self.session = aiohttp.ClientSession(timeout=timeout)

        result = await self.api_call("setWebhook", {
            "url": webhook_url,
            "drop_pending_updates": False,
            "allowed_updates": ["message"],
        })

        if result and result.get("ok"):
            self._webhook_set = True
            logger.info(f"✅ Webhook registered: {webhook_url}")
            return True
        else:
            logger.error(f"❌ Failed to register webhook: {result}")
            return False

    async def delete_webhook(self) -> bool:
        if not self.session:
            timeout = aiohttp.ClientTimeout(total=config.TELEGRAM_API_TIMEOUT)
            self.session = aiohttp.ClientSession(timeout=timeout)

        result = await self.api_call("deleteWebhook", {"drop_pending_updates": True})
        if result and result.get("ok"):
            self._webhook_set = False
            logger.info("✅ Webhook deleted successfully")
            return True
        else:
            logger.warning(f"⚠️ Failed to delete webhook: {result}")
            return False

    # ============================================================
    # Polling Loop
    # ============================================================
    async def polling_loop(self):
        while self.is_running:
            try:
                await asyncio.sleep(1)
                params = {"offset": self.last_update_id + 1, "timeout": 30}
                result = await self.api_call("getUpdates", params)
                if result and result.get("ok"):
                    updates = result.get("result", [])
                    for update in updates:
                        self.last_update_id = update.get("update_id", self.last_update_id)
                        await self.handle_update(update)
                else:
                    if result:
                        error_code = result.get("error_code")
                        if error_code == 409:
                            logger.warning("Polling conflict, another instance may be running")
                            await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Polling loop error: {exc}")
                await asyncio.sleep(5)

    # ============================================================
    # Process Update (for Webhook)
    # ============================================================
    async def process_update(self, update: dict):
        await self.handle_update(update)

    # ============================================================
    # Telegram API Call
    # ============================================================
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

    # ============================================================
    # Send Message
    # ============================================================
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
                if result:
                    error_code = result.get("error_code")
                    if error_code in (400, 403):
                        return result
            except Exception as exc:
                logger.warning(f"Send attempt {attempt+1} exception for {chat_id}: {exc}")
            if attempt < retries:
                await asyncio.sleep(backoff)
                backoff *= 2
        return None

    # ============================================================
    # Broadcast to All Subscribers
    # ============================================================
    async def broadcast(self, text):
        subscribers = await self.database.get_subscribers()
        count = len(subscribers)
        logger.info(f"📢 Broadcasting to {count} subscribers")
        if count == 0:
            logger.warning("⚠️ No active subscribers found!")
            if self.admin_id:
                await self.send_message(self.admin_id, "⚠️ لا يوجد مشتركون نشطون. استخدم /adduser لإضافة مستخدمين.")
            return

        if self.admin_id and self.admin_id not in subscribers:
            subscribers = [self.admin_id] + subscribers

        blocked_users = []
        success_count = 0
        failure_count = 0
        for user_id in subscribers:
            try:
                result = await self.send_message(user_id, text)
                if result and result.get("ok"):
                    success_count += 1
                else:
                    failure_count += 1
                    if result:
                        error_code = result.get("error_code")
                        error_desc = result.get("description", "").lower()
                        if (
                            error_code == 403
                            or "blocked" in error_desc
                            or "chat not found" in error_desc
                            or "user not found" in error_desc
                        ):
                            logger.warning(f"User {user_id} invalid/blocked, removing: {error_desc}")
                            await self.database.remove_subscriber(user_id)
                            blocked_users.append(user_id)
                await asyncio.sleep(0.05)
            except Exception as exc:
                failure_count += 1
                logger.warning(f"Broadcast exception for {user_id}: {exc}")

        if blocked_users:
            logger.info(f"✅ Removed invalid users: {blocked_users}")
        logger.info(f"✅ Broadcast: {success_count}/{len(subscribers)} sent, {failure_count} failed")

    # ============================================================
    # Handle Update (Commands)
    # ============================================================
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
                await self.send_message(chat_id, "⛔ للمشرف فقط.")
                return
            parts = text.split()
            if len(parts) != 2:
                await self.send_message(chat_id, "الاستخدام: /adduser USER_ID")
                return
            try:
                target_id = int(parts[1])
                if await self.database.add_subscriber(target_id):
                    await self.send_message(chat_id, f"✅ تم إضافة المشترك {target_id}.")
                else:
                    await self.send_message(chat_id, "❌ فشل إضافة المشترك.")
            except ValueError:
                await self.send_message(chat_id, "❌ يجب أن يكون USER_ID رقمياً.")
        elif command.startswith("/removeuser"):
            if not self.is_admin(user_id):
                await self.send_message(chat_id, "⛔ للمشرف فقط.")
                return
            parts = text.split()
            if len(parts) != 2:
                await self.send_message(chat_id, "الاستخدام: /removeuser USER_ID")
                return
            try:
                target_id = int(parts[1])
                if await self.database.remove_subscriber(target_id):
                    await self.send_message(chat_id, f"✅ تم حذف المشترك {target_id}.")
                else:
                    await self.send_message(chat_id, "❌ فشل حذف المشترك.")
            except ValueError:
                await self.send_message(chat_id, "❌ يجب أن يكون USER_ID رقمياً.")
        elif command.startswith("/reset_daily"):
            if not self.is_admin(user_id):
                await self.send_message(chat_id, "⛔ للمشرف فقط.")
                return
            await self.database.reset_daily(config.INITIAL_CAPITAL)
            await self.send_message(chat_id, "✅ تم إعادة الإحصائيات اليومية.")
        else:
            await self.send_message(chat_id, "❓ أمر غير معروف. استخدم /start للمساعدة.")

    # ============================================================
    # Admin Check
    # ============================================================
    def is_admin(self, user_id):
        return int(user_id) == int(self.admin_id)

    # ============================================================
    # Help Text
    # ============================================================
    def help_text(self):
        return (
            "🤖 <b>نظام إشارات العملات الرقمية v2026</b>\n\n"
            "📋 <b>الأوامر المتاحة:</b>\n"
            "/status - حالة النظام\n"
            "/prewatch - قائمة المراقبة\n"
            "/performance - الأداء\n"
            "/subscribers - قائمة المشتركين (للمشرف)\n"
            "/signal BTCUSDT - تحليل فوري\n"
            "/adduser USER_ID - إضافة مشترك\n"
            "/removeuser USER_ID - حذف مشترك\n"
            "/reset_daily - إعادة الإحصائيات\n\n"
            "💥 النظام يرسل الإشارات القوية فقط + تنبؤات الانفجارات"
        )

    # ============================================================
    # Subscribers List
    # ============================================================
    async def subscribers_list(self, chat_id, user_id):
        if not self.is_admin(user_id):
            await self.send_message(chat_id, "⛔ للمشرف فقط.")
            return
        subs = await self.database.get_subscribers()
        count = len(subs)
        if count == 0:
            await self.send_message(chat_id, "📭 لا يوجد مشتركون.")
            return
        lines = [f"📋 المشتركون ({count}):"]
        for uid in subs:
            lines.append(f"• {uid}")
        await self.send_message(chat_id, "\n".join(lines))

    # ============================================================
    # Status Command
    # ============================================================
    async def status(self, chat_id):
        signals = await self.database.get_daily_signals()
        stats = await self.database.get_daily_pnl()
        prewatch = await self.database.get_prewatch(20)
        subscribers = await self.database.get_subscribers()

        explosion_count = sum(
            1 for s in signals
            if s.get("exit_reason") is None and s.get("score", 0) >= config.EARLY_SNIPE_SCORE
        )

        text = (
            f"📊 <b>حالة النظام</b>\n\n"
            f"📈 إشارات اليوم: {self.safe_text(len(signals))}\n"
            f"💥 تنبيهات الانفجار: {self.safe_text(explosion_count)}\n"
            f"💰 الأرباح اليومية: {self.safe_text(f'{stats['pnl']:.2f}')}\n"
            f"🏆 ربح/خسارة/تعادل/غير محدد/مهلة: {stats['wins']}/{stats['losses']}/{stats['breakeven']}/{stats['inconclusive']}/{stats['timeout']}\n"
            f"🔭 قائمة المراقبة: {self.safe_text(len(prewatch))}\n"
            f"👥 المشتركون: {self.safe_text(len(subscribers))}"
        )
        await self.send_message(chat_id, text)

    # ============================================================
    # Prewatch Command
    # ============================================================
    async def prewatch(self, chat_id):
        items = await self.database.get_prewatch(10)
        if not items:
            await self.send_message(chat_id, "🔭 قائمة المراقبة فارغة.")
            return
        lines = ["🔭 <b>قائمة المراقبة</b>\n"]
        for item in items:
            lines.append(
                f"• <b>{self.safe_text(item['symbol'])}</b> | "
                f"{self.safe_text(f'{item['price_change']:.2f}')}% | "
                f"${self.safe_text(f'{item['quote_volume']:,.0f}')} | "
                f"الصفقات: {self.safe_text(item.get('trades', 0))}"
            )
        await self.send_message(chat_id, "\n".join(lines))

    # ============================================================
    # Performance Command
    # ============================================================
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

        pf_text = "∞" if profit_factor == float("inf") else f"{profit_factor:.2f}"
        text = (
            f"📈 <b>تقرير الأداء</b>\n\n"
            f"📊 الصفقات المغلقة: {self.safe_text(len(closed))}\n"
            f"🏆 ربح/خسارة/تعادل/غير محدد/مهلة: {len(wins)}/{len(losses)}/{len(breakeven)}/{len(inconclusive)}/{len(timeouts)}\n"
            f"🎯 نسبة الفوز: {self.safe_text(f'{win_rate:.2f}')}%\n"
            f"💹 معامل الربح: {self.safe_text(pf_text)}\n"
            f"📉 نسبة شارب (R): {self.safe_text(f'{sharpe:.2f}')}"
        )
        await self.send_message(chat_id, text)

    # ============================================================
    # Manual Signal Analysis
    # ============================================================
    async def signal(self, chat_id, symbol):
        klines = await self.data_fetcher.klines(symbol, config.ANALYSIS_INTERVAL, config.KLINE_LIMIT)
        if not klines:
            await self.send_message(chat_id, f"❌ لا توجد بيانات لـ {self.safe_text(symbol)}.")
            return
        from utils import klines_to_dataframe
        df = klines_to_dataframe(klines)
        klines_15m = await self.data_fetcher.klines(symbol, config.TREND_INTERVAL, 50)
        df_15m = klines_to_dataframe(klines_15m) if klines_15m else None
        result = self.signal_engine.analyze(symbol, df, config.INITIAL_CAPITAL, df_15m)
        if not result:
            await self.send_message(
                chat_id,
                f"⚪ لا توجد إشارة قوية لـ {self.safe_text(symbol)}.\n"
                f"(المعايير مشددة: الحد الأدنى للنقاط={config.MIN_SCORE}, الحد الأدنى للاتجاه={config.MIN_ADX})"
            )
            return
        await self.send_message(chat_id, self.format_signal(result))

    # ============================================================
    # Format Signal (Arabic + Explosion Detection)
    # ============================================================
    def format_signal(self, signal):
        def fmt(val):
            if abs(val) < 1e-5:
                return self.safe_text(f"{val:.4e}")
            else:
                return self.safe_text(f"{val:.6f}")

        # ✅ تحديد الاتجاه بالعربية
        if signal["direction"] == "BUY":
            direction_ar = "🟢 شراء"
            emoji = "🟢"
        else:
            direction_ar = "🔴 بيع"
            emoji = "🔴"

        # ✅ حساب التقييم
        score_val = signal.get('score', 0)
        if score_val >= 9.0:
            rating = "🔥 إشارة استثنائية"
        elif score_val >= 8.0:
            rating = "⚡ إشارة قوية جداً"
        elif score_val >= 7.5:
            rating = "✅ إشارة قوية"
        else:
            rating = "📊 إشارة مقبولة"

        quality_label = f"⚡ الجودة: {signal.get('quality', 0)}%" if signal.get('quality') else ""
        risk_label = f"🎯 المخاطرة: {signal.get('actual_risk_percent', 0):.2f}%" if signal.get('actual_risk_percent') else ""

        # ✅ تمييز الانفجارات
        if signal.get("early_snipe"):
            title = f"{emoji} <b>💥 تنبؤ بانفجار — {self.safe_text(signal['symbol'])}</b>"
            explosion_info = signal.get("explosion_details", {})
            conditions = signal.get("explosion_conditions", 0)

            details_lines = []
            if explosion_info.get("squeeze"):
                details_lines.append("  ✅ انضغاط بولينجر (TTM Squeeze)")
            if explosion_info.get("consolidation"):
                details_lines.append("  ✅ تجميع سعري (Consolidation)")
            if explosion_info.get("volume_buildup"):
                details_lines.append("  ✅ تراكم حجم (Volume Buildup)")
            if explosion_info.get("breakout_proximity"):
                details_lines.append("  ✅ اقتراب من مقاومة/دعم")

            explosion_section = (
                f"\n💥 <b>إعداد انفجار</b> ({conditions}/4 شروط)\n"
                + "\n".join(details_lines)
                + "\n"
            )
        else:
            title = f"{emoji} <b>{rating} — {self.safe_text(signal['symbol'])}</b>"
            explosion_section = ""

        # ✅ عدد العوامل المتوافقة
        factor_count = signal.get("factor_count", 0)
        factor_section = f"📊 العوامل المتوافقة: {factor_count}/7\n" if factor_count else ""

        # ✅ حجم الصفقة بحجم مقروء
        position = signal.get('position_size', 0)
        position_str = self.safe_text(f"{position:.6f}" if position >= 0.01 else f"{position:.4e}")

        return (
            f"{title}\n"
            f"{'━' * 20}\n\n"
            f"📌 <b>الاتجاه:</b> {direction_ar}\n"
            f"⭐ <b>النقاط:</b> {self.safe_text(score_val)}/10\n"
            f"{quality_label}\n"
            f"{risk_label}\n"
            f"{factor_section}"
            f"{explosion_section}\n"
            f"━━━━━ <b>إدارة المخاطر</b> ━━━━━\n\n"
            f"💰 <b>سعر الدخول:</b> {fmt(signal['entry'])}\n"
            f"🛑 <b>وقف الخسارة:</b> {fmt(signal['sl'])}\n"
            f"🎯 <b>جني الأرباح:</b> {fmt(signal['tp'])}\n"
            f"📊 <b>نسبة R/R:</b> {self.safe_text(signal['rr'])}\n"
            f"📦 <b>حجم الصفقة:</b> {position_str}\n\n"
            f"━━━━━ <b>المؤشرات الفنية</b> ━━━━━\n\n"
            f"📈 <b>RSI:</b> {self.safe_text(signal['rsi'])}\n"
            f"📊 <b>ADX:</b> {self.safe_text(signal['adx'])}\n"
            f"📉 <b>ATR:</b> {fmt(signal['atr'])}\n\n"
            f"⏱ <b>الوقت:</b> {self.safe_text(signal['timestamp'][:19].replace('T', ' '))}\n\n"
            "⚠️ <i>إشارة تحليلية وليست ضماناً للربح.</i>\n"
            "📊 <i>الجودة تعبر عن قوة الإشارة وليست احتمالية ربح.</i>"
        )
