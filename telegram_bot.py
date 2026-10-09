# telegram_bot.py
# Telegram Bot v2026.3 - With Market Regime + Near-Miss + All Accuracy Commands

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

    def safe_text(self, text):
        return html.escape(str(text))

    async def start(self):
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
        self.is_running = True
        timeout = aiohttp.ClientTimeout(total=config.TELEGRAM_API_TIMEOUT)
        self.session = aiohttp.ClientSession(timeout=timeout)
        if not self.webhook_mode:
            await self.api_call("deleteWebhook", {"drop_pending_updates": True})
            await asyncio.sleep(2)
            self.polling_task = asyncio.create_task(self.polling_loop())
        if self.admin_id:
            await self.send_message(self.admin_id, "🤖 <b>البوت جاهز (v2026.3)</b>")

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

    async def set_webhook(self, url):
        if not self.session:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=config.TELEGRAM_API_TIMEOUT))
        r = await self.api_call("setWebhook", {
            "url": url, "drop_pending_updates": False, "allowed_updates": ["message"]})
        return bool(r and r.get("ok"))

    async def delete_webhook(self):
        if not self.session:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=config.TELEGRAM_API_TIMEOUT))
        r = await self.api_call("deleteWebhook", {"drop_pending_updates": True})
        return bool(r and r.get("ok"))

    async def polling_loop(self):
        while self.is_running:
            try:
                r = await self.api_call("getUpdates", {
                    "offset": self.last_update_id + 1,
                    "timeout": config.TELEGRAM_LONG_POLL_TIMEOUT,
                })
                if r and r.get("ok"):
                    for u in r.get("result", []):
                        self.last_update_id = u.get("update_id", self.last_update_id)
                        await self.handle_update(u)
                elif r and r.get("error_code") == 409:
                    logger.warning("Polling conflict, waiting 10s")
                    await asyncio.sleep(10)
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Polling error: {exc}")
                await asyncio.sleep(5)

    async def process_update(self, update):
        await self.handle_update(update)

    async def api_call(self, method, payload=None, retries=config.TELEGRAM_MAX_RETRIES):
        if not self.session:
            return None
        backoff = config.TELEGRAM_RETRY_BACKOFF_BASE
        for attempt in range(retries + 1):
            try:
                async with self.session.post(
                    f"{self.base_url}/{method}", json=payload or {}) as response:
                    data = await response.json()
                    if not data.get("ok") and response.status == 429:
                        ra = data.get("parameters", {}).get("retry_after", 5)
                        await asyncio.sleep(ra)
                        continue
                    return data
            except asyncio.TimeoutError:
                if attempt < retries:
                    await asyncio.sleep(backoff)
                    backoff *= 2
            except Exception:
                if attempt < retries:
                    await asyncio.sleep(backoff)
                    backoff *= 2
        return None

    async def send_message(self, chat_id, text, parse_mode="HTML",
                            retries=config.TELEGRAM_MAX_RETRIES):
        if not text:
            return None
        if len(text) > 4096:
            text = text[:4000] + "\n... (مقطوع)"
        backoff = config.TELEGRAM_RETRY_BACKOFF_BASE
        for attempt in range(retries + 1):
            try:
                result = await self.api_call("sendMessage", {
                    "chat_id": chat_id, "text": text,
                    "parse_mode": parse_mode, "disable_web_page_preview": True,
                })
                if result and result.get("ok"):
                    return result
                if result and result.get("error_code") in (400, 403):
                    return result
            except Exception as exc:
                logger.warning(f"Send attempt {attempt+1}: {exc}")
            if attempt < retries:
                await asyncio.sleep(backoff)
                backoff *= 2
        return None

    async def broadcast(self, text):
        subscribers = await self.database.get_subscribers()
        count = len(subscribers)
        if count == 0:
            if self.admin_id:
                await self.send_message(
                    self.admin_id, "⚠️ لا يوجد مشتركون. استخدم /adduser ID")
            return
        if self.admin_id and self.admin_id not in subscribers:
            subscribers = [self.admin_id] + subscribers
        success = 0
        for uid in subscribers:
            try:
                r = await self.send_message(uid, text)
                if r and r.get("ok"):
                    success += 1
                elif r:
                    ec = r.get("error_code")
                    ed = r.get("description", "").lower()
                    if ec == 403 or "blocked" in ed or "chat not found" in ed:
                        await self.database.remove_subscriber(uid)
                await asyncio.sleep(0.05)
            except Exception:
                pass
        logger.info(f"📢 Broadcast: {success}/{len(subscribers)}")

    # ============================================================
    # Update Handler
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
        cmd = text.split()[0].lower() if text else ""
        logger.info(f"📩 {cmd} from {user_id}")

        if cmd.startswith("/start"):
            await self.send_message(chat_id, self.help_text())
        elif cmd.startswith("/status"):
            await self.cmd_status(chat_id)
        elif cmd.startswith("/prewatch"):
            await self.cmd_prewatch(chat_id)
        elif cmd.startswith("/performance"):
            await self.cmd_performance(chat_id)
        elif cmd.startswith("/subscribers"):
            await self.cmd_subscribers(chat_id, user_id)
        elif cmd.startswith("/signal"):
            parts = text.split()
            if len(parts) != 2:
                await self.send_message(chat_id, "الاستخدام: /signal BTCUSDT")
            else:
                await self.cmd_signal(chat_id, parts[1].upper())
        elif cmd.startswith("/adduser"):
            await self.cmd_adduser(chat_id, user_id, text)
        elif cmd.startswith("/removeuser"):
            await self.cmd_removeuser(chat_id, user_id, text)
        elif cmd.startswith("/reset_daily"):
            if not self.is_admin(user_id):
                await self.send_message(chat_id, "⛔ للمشرف فقط.")
                return
            await self.database.reset_daily(config.INITIAL_CAPITAL)
            await self.send_message(chat_id, "✅ تم إعادة الإحصائيات.")
        elif cmd.startswith("/accuracy_by_score"):
            await self.cmd_accuracy_by_score(chat_id)
        elif cmd.startswith("/accuracy_by_symbol"):
            await self.cmd_accuracy_by_symbol(chat_id)
        elif cmd.startswith("/accuracy_by_type"):
            await self.cmd_accuracy_by_type(chat_id)
        elif cmd.startswith("/accuracy"):
            await self.cmd_accuracy(chat_id)
        elif cmd.startswith("/last_signals"):
            await self.cmd_last_signals(chat_id, text)
        elif cmd.startswith("/near_miss"):
            await self.cmd_near_miss(chat_id, user_id)
        elif cmd.startswith("/regime"):
            await self.cmd_regime(chat_id, text)
        else:
            await self.send_message(chat_id, "❓ أمر غير معروف. استخدم /start.")

    def is_admin(self, user_id):
        return int(user_id) == int(self.admin_id)

    def help_text(self):
        return (
            "🤖 <b>نظام إشارات v2026.3</b>\n\n"
            "📋 <b>أوامر عامة:</b>\n"
            "/status - حالة النظام\n"
            "/prewatch - قائمة المراقبة\n"
            "/performance - الأداء (30 يوم)\n"
            "/signal BTCUSDT - تحليل فوري\n"
            "/regime BTCUSDT - حالة السوق\n\n"
            "📊 <b>تتبع النتائج:</b>\n"
            "/accuracy - Win Rate (7/30/90)\n"
            "/accuracy_by_score - حسب النقاط\n"
            "/accuracy_by_symbol - أفضل/أسوأ\n"
            "/accuracy_by_type - انفجار/اختراق/ترند\n"
            "/last_signals 10 - آخر الإشارات\n\n"
            "🔐 <b>المشرف:</b>\n"
            "/adduser ID | /removeuser ID\n"
            "/subscribers | /reset_daily\n"
            "/near_miss 20 - الإشارات المرفوضة\n\n"
            "💡 💥 انفجار | ⚡ اختراق | 📈 ترند"
        )

    # ============================================================
    # Commands
    # ============================================================
    async def cmd_status(self, chat_id):
        signals = await self.database.get_daily_signals()
        stats = await self.database.get_daily_pnl()
        prewatch = await self.database.get_prewatch(20)
        subscribers = await self.database.get_subscribers()
        type_counts = {"EXPLOSION": 0, "BREAKOUT": 0, "TREND": 0}
        for s in signals:
            st = s.get("signal_type") or "TREND"
            if st in type_counts:
                type_counts[st] += 1
        text = (
            f"📊 <b>حالة النظام v2026.3</b>\n\n"
            f"📈 إشارات اليوم: {len(signals)}\n"
            f"   💥 {type_counts['EXPLOSION']} | ⚡ {type_counts['BREAKOUT']} | 📈 {type_counts['TREND']}\n\n"
            f"💰 PnL: {stats['pnl']:.2f}\n"
            f"🏆 ر/خ/ت: {stats['wins']}/{stats['losses']}/{stats['breakeven']}\n"
            f"🔭 مراقبة: {len(prewatch)}\n"
            f"👥 مشتركون: {len(subscribers)}"
        )
        await self.send_message(chat_id, text)

    async def cmd_prewatch(self, chat_id):
        items = await self.database.get_prewatch(10)
        if not items:
            await self.send_message(chat_id, "🔭 قائمة المراقبة فارغة.")
            return
        lines = ["🔭 <b>قائمة المراقبة</b>\n"]
        for i in items:
            lines.append(f"• <b>{i['symbol']}</b> | {i['price_change']:.2f}% | ${i['quote_volume']:,.0f}")
        await self.send_message(chat_id, "\n".join(lines))

    async def cmd_performance(self, chat_id):
        stats = await self.database.get_accuracy_stats(days=30)
        if stats["total"] == 0:
            await self.send_message(chat_id, "لا توجد بيانات كافية.")
            return
        pf = "∞" if stats["profit_factor"] >= 999 else f"{stats['profit_factor']:.2f}"
        text = (
            f"📈 <b>الأداء (30 يوم)</b>\n\n"
            f"📊 إجمالي: {stats['total']}\n"
            f"✅ ربح: {stats['wins']} | ❌ خسارة: {stats['losses']}\n"
            f"⏰ مهلة: {stats['timeouts']} | ➖ تعادل: {stats['breakeven']}\n\n"
            f"🎯 Win Rate: <b>{stats['win_rate']}%</b>\n"
            f"📉 Average R: {stats['avg_r']}\n"
            f"💹 Profit Factor: {pf}"
        )
        await self.send_message(chat_id, text)

    async def cmd_subscribers(self, chat_id, user_id):
        if not self.is_admin(user_id):
            await self.send_message(chat_id, "⛔ للمشرف فقط.")
            return
        subs = await self.database.get_subscribers()
        if not subs:
            await self.send_message(chat_id, "📭 لا يوجد مشتركون.")
            return
        lines = [f"📋 المشتركون ({len(subs)}):"]
        for uid in subs:
            lines.append(f"• {uid}")
        await self.send_message(chat_id, "\n".join(lines))

    async def cmd_adduser(self, chat_id, user_id, text):
        if not self.is_admin(user_id):
            await self.send_message(chat_id, "⛔ للمشرف فقط.")
            return
        parts = text.split()
        if len(parts) != 2:
            await self.send_message(chat_id, "الاستخدام: /adduser USER_ID")
            return
        try:
            tid = int(parts[1])
            if await self.database.add_subscriber(tid):
                await self.send_message(chat_id, f"✅ تم إضافة {tid}")
        except ValueError:
            await self.send_message(chat_id, "❌ USER_ID رقمي فقط")

    async def cmd_removeuser(self, chat_id, user_id, text):
        if not self.is_admin(user_id):
            await self.send_message(chat_id, "⛔ للمشرف فقط.")
            return
        parts = text.split()
        if len(parts) != 2:
            await self.send_message(chat_id, "الاستخدام: /removeuser USER_ID")
            return
        try:
            tid = int(parts[1])
            if await self.database.remove_subscriber(tid):
                await self.send_message(chat_id, f"✅ تم حذف {tid}")
        except ValueError:
            await self.send_message(chat_id, "❌ USER_ID رقمي فقط")

    async def cmd_accuracy(self, chat_id):
        d7 = await self.database.get_accuracy_stats(days=7)
        d30 = await self.database.get_accuracy_stats(days=30)
        d90 = await self.database.get_accuracy_stats(days=90)
        def fmt(s):
            if s["total"] == 0:
                return "  لا توجد بيانات"
            return (f"  • إشارات: {s['total']}\n"
                    f"  • Win Rate: <b>{s['win_rate']}%</b>\n"
                    f"  • Avg R: {s['avg_r']}\n"
                    f"  • PF: {s['profit_factor']}")
        await self.send_message(chat_id,
            f"📊 <b>Win Rate</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📅 <b>7 أيام:</b>\n{fmt(d7)}\n\n"
            f"📆 <b>30 يوم:</b>\n{fmt(d30)}\n\n"
            f"📈 <b>90 يوم:</b>\n{fmt(d90)}")

    async def cmd_accuracy_by_score(self, chat_id):
        buckets = await self.database.get_accuracy_by_score(days=30)
        if not buckets:
            await self.send_message(chat_id, "لا توجد بيانات.")
            return
        lines = ["📊 <b>Win Rate حسب النقاط</b>\n"]
        for name, stats in buckets.items():
            if stats["total"] == 0:
                lines.append(f"⭐ <b>{name}</b>: لا بيانات")
            else:
                lines.append(f"⭐ <b>{name}</b>: {stats['total']} | Win {stats['win_rate']}% | R {stats['avg_r']}")
        await self.send_message(chat_id, "\n\n".join(lines))

    async def cmd_accuracy_by_symbol(self, chat_id):
        data = await self.database.get_accuracy_by_symbol(days=30, limit=10)
        best = data.get("best", [])
        worst = data.get("worst", [])
        if not best and not worst:
            await self.send_message(chat_id, "لا توجد بيانات.")
            return
        lines = ["📊 <b>Win Rate حسب العملة</b>\n"]
        if best:
            lines.append("🏆 <b>الأفضل:</b>")
            for item in best:
                st = item["stats"]
                lines.append(f"  • {item['symbol']}: {st['win_rate']}% ({st['total']})")
        if worst:
            lines.append("\n📉 <b>الأسوأ:</b>")
            for item in worst:
                st = item["stats"]
                lines.append(f"  • {item['symbol']}: {st['win_rate']}% ({st['total']})")
        await self.send_message(chat_id, "\n".join(lines))

    async def cmd_accuracy_by_type(self, chat_id):
        data = await self.database.get_accuracy_by_type(days=30)
        if not data:
            await self.send_message(chat_id, "لا توجد بيانات.")
            return
        names = {"EXPLOSION": "💥 انفجار", "BREAKOUT": "⚡ اختراق", "TREND": "📈 ترند"}
        lines = ["📊 <b>Win Rate حسب النوع</b>\n"]
        for st, stats in data.items():
            lines.append(f"<b>{names.get(st, st)}</b>\n"
                         f"  • {stats['total']} إشارة | Win {stats['win_rate']}% | R {stats['avg_r']}")
        await self.send_message(chat_id, "\n\n".join(lines))

    async def cmd_last_signals(self, chat_id, text):
        parts = text.split()
        limit = min(int(parts[1]), 30) if len(parts) == 2 and parts[1].isdigit() else 10
        outcomes = await self.database.get_recent_outcomes(limit)
        if not outcomes:
            await self.send_message(chat_id, "لا توجد إشارات مغلقة.")
            return
        lines = [f"📋 <b>آخر {len(outcomes)} إشارة</b>\n"]
        for o in outcomes:
            e = {"WIN": "✅", "LOSS": "❌", "TIMEOUT": "⏰", "BREAKEVEN": "➖"}.get(o["outcome"], "❓")
            te = {"EXPLOSION": "💥", "BREAKOUT": "⚡", "TREND": "📈"}.get(o.get("signal_type"), "")
            lines.append(f"{e} {te} <b>{o['symbol']}</b> {o['direction']} → {o['outcome']} ({o['r_multiple']:.2f}R)")
        await self.send_message(chat_id, "\n".join(lines))

    async def cmd_near_miss(self, chat_id, user_id):
        if not self.is_admin(user_id):
            await self.send_message(chat_id, "⛔ للمشرف فقط.")
            return
        # Simple display - in production would query DB
        await self.send_message(chat_id, "📊 Near-miss logging يعمل في الخلفية. تحقق من /api/accuracy أو DB.")

    async def cmd_regime(self, chat_id, text):
        parts = text.split()
        if len(parts) != 2:
            await self.send_message(chat_id, "الاستخدام: /regime BTCUSDT")
            return
        symbol = parts[1].upper()
        klines = await self.data_fetcher.klines(symbol, config.ANALYSIS_INTERVAL, 250)
        if not klines:
            await self.send_message(chat_id, f"❌ لا توجد بيانات لـ {symbol}")
            return
        from utils import klines_to_dataframe
        from indicators import add_indicators, detect_market_regime
        df = klines_to_dataframe(klines)
        df = add_indicators(df)
        regime = detect_market_regime(df)
        emoji = {"TRENDING": "🟢", "RANGING": "🟡", "NEUTRAL": "⚪", "UNKNOWN": "❓"}.get(regime["regime"], "❓")
        await self.send_message(chat_id,
            f"{emoji} <b>حالة السوق: {symbol}</b>\n\n"
            f"📊 النظام: <b>{regime['regime']}</b>\n"
            f"📈 ADX: {regime['adx']:.1f}\n"
            f"📉 Efficiency Ratio: {regime['er']:.3f}")

    async def cmd_signal(self, chat_id, symbol):
        klines = await self.data_fetcher.klines(symbol, config.ANALYSIS_INTERVAL, config.KLINE_LIMIT)
        if not klines:
            await self.send_message(chat_id, f"❌ لا توجد بيانات لـ {symbol}")
            return
        from utils import klines_to_dataframe
        df = klines_to_dataframe(klines)
        klines_15m = await self.data_fetcher.klines(symbol, config.TREND_INTERVAL, 50)
        df_15m = klines_to_dataframe(klines_15m) if klines_15m else None
        result = self.signal_engine.analyze(symbol, df, config.INITIAL_CAPITAL, df_15m)
        if not result:
            await self.send_message(
                chat_id,
                f"⚪ لا توجد إشارة لـ {symbol}\n"
                f"(score≥{config.MIN_SCORE}, ADX≥{config.MIN_ADX})")
            return
        await self.send_message(chat_id, self.format_signal(result))

    # ============================================================
    # Format Signal
    # ============================================================
    def format_signal(self, signal):
        def fmt(v):
            if abs(v) < 1e-5:
                return self.safe_text(f"{v:.4e}")
            return self.safe_text(f"{v:.6f}")

        direction = signal["direction"]
        dir_ar = "🟢 شراء" if direction == "BUY" else "🔴 بيع"
        st = signal.get("signal_type", "TREND")
        score = signal.get("score", 0)

        if st == "EXPLOSION":
            title = f"💥 <b>تنبؤ بانفجار — {self.safe_text(signal['symbol'])}</b>"
        elif st == "BREAKOUT":
            title = f"⚡ <b>اختراق مؤكد — {self.safe_text(signal['symbol'])}</b>"
        else:
            title = f"📈 <b>اتجاه قوي — {self.safe_text(signal['symbol'])}</b>"

        rating = ("🔥 استثنائية" if score >= 9.0 else
                  "⚡ قوية جداً" if score >= 8.5 else
                  "✅ قوية" if score >= 8.0 else "📊 مقبولة")

        explosion_section = ""
        if st == "EXPLOSION":
            details = signal.get("explosion_details", {})
            cond = signal.get("explosion_conditions", 0)
            lines = []
            if details.get("squeeze"):
                lines.append("  ✅ انضغاط بولنجر")
            if details.get("consolidation"):
                lines.append("  ✅ تجميع سعري")
            if details.get("volume_buildup"):
                lines.append("  ✅ تراكم حجم")
            if details.get("breakout_proximity"):
                lines.append("  ✅ اقتراب مقاومة")
            explosion_section = f"\n💥 <b>إعداد انفجار</b> ({cond}/4)\n" + "\n".join(lines) + "\n"

        position = signal.get('position_size', 0)
        pos_str = self.safe_text(f"{position:.6f}" if position >= 0.01 else f"{position:.4e}")
        regime = signal.get("market_regime", "N/A")
        er_val = signal.get("efficiency_ratio", 0)

        return (
            f"{title}\n{'━' * 20}\n\n"
            f"📌 النوع: {signal.get('signal_type_ar', '📊')}\n"
            f"🎯 الاتجاه: {dir_ar}\n"
            f"⭐ النقاط: {score}/10 ({rating})\n"
            f"⚡ الجودة: {signal.get('quality', 0)}%\n"
            f"🎯 المخاطرة: {signal.get('actual_risk_percent', 0):.2f}%\n"
            f"📊 العوامل: {signal.get('factor_count', 0)}/7\n"
            f"🏛 حالة السوق: {regime} | ER: {er_val:.2f}\n"
            f"{explosion_section}\n"
            f"━━━━━ إدارة المخاطر ━━━━━\n\n"
            f"💰 الدخول: {fmt(signal['entry'])}\n"
            f"🛑 وقف الخسارة: {fmt(signal['sl'])}\n"
            f"🎯 جني الأرباح: {fmt(signal['tp'])}\n"
            f"📊 R/R: {self.safe_text(signal['rr'])}\n"
            f"📦 الحجم: {pos_str}\n\n"
            f"━━━━━ المؤشرات ━━━━━\n\n"
            f"📈 RSI: {self.safe_text(signal['rsi'])}\n"
            f"📊 ADX: {self.safe_text(signal['adx'])}\n"
            f"📉 ATR: {fmt(signal['atr'])}\n"
            f"📊 Volume: {self.safe_text(signal.get('volume_ratio', 0))}x\n\n"
            f"⏱ {self.safe_text(signal['timestamp'][:19].replace('T', ' '))} UTC\n\n"
            "⚠️ <i>إشارة تحليلية وليست ضماناً للربح.</i>"
        )
