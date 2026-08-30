#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
bot.py - الملف الرئيسي لتشغيل البوت
الإصدار النهائي V8 مع دعم ccxt و ADX ودمج Pre-watch
"""

import os
import sys
import asyncio
import threading
import logging
import time
import aiohttp
from datetime import datetime, timedelta, timezone
from flask import Flask
from telegram.ext import Application, CommandHandler, ContextTypes

from config import config
from database import db
from utils import (
    self_pinger, fetch_top_symbols, fetch_klines, fetch_24hr_stats,
    fetch_news, symbol_to_currency_name, scan_market_for_opportunities,
    analyze_pre_watch_candidate, scan_market_ccxt
)
from telegram_bot import handlers
from signals import SignalEngine, ConfirmationEngine

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask('')

@app.route('/')
@app.route('/healthcheck')
def home():
    return "✅ Elite Bot V8 - ccxt + ADX + PreWatch"

def run_flask():
    app.run(host='0.0.0.0', port=config.PORT, debug=False, use_reloader=False)

# -------------------- متغيرات عامة --------------------
dynamic_watch_list = []
last_dynamic_update = 0
background_tasks = []
shutdown_event = asyncio.Event()

# -------------------- حلقة المسح الرئيسية --------------------
async def market_scanner_loop():
    global dynamic_watch_list, last_dynamic_update
    logger.info("🚀 بدء الماسح المحسن (مع ccxt و ADX)...")
    semaphore = asyncio.Semaphore(config.SEMAPHORE_LIMIT)
    
    while not shutdown_event.is_set():
        try:
            async with aiohttp.ClientSession() as session:
                async with aiohttp.ClientSession() as send_session:
                    while not shutdown_event.is_set():
                        try:
                            # 1. المسح السريع باستخدام ccxt
                            if config.USE_CCXT:
                                try:
                                    logger.info("🔍 بدء المسح السريع باستخدام ccxt...")
                                    ccxt_results = await scan_market_ccxt(session, config.CCXT_MAX_SYMBOLS)
                                    if ccxt_results:
                                        for result in ccxt_results[:10]:
                                            symbol = result['symbol']
                                            if result['score'] > 70:
                                                await db.add_to_pre_watch(
                                                    symbol,
                                                    result['score'],
                                                    result.get('volume', 0),
                                                    result.get('change_24h', 0),
                                                    0,
                                                    f"ccxt: RSI {result['rsi']}, Vol {result['volume_ratio']}x"
                                                )
                                                logger.info(f"🔭 ccxt added {symbol} to pre-watch (score: {result['score']})")
                                except Exception as e:
                                    logger.error(f"ccxt scan error: {e}")

                            # 2. تحديث القائمة الديناميكية (أفضل 100 عملة من حيث الحجم)
                            if time.time() - last_dynamic_update > config.DYNAMIC_UPDATE_INTERVAL:
                                new_symbols = await fetch_top_symbols(session, config.DYNAMIC_SYMBOLS_LIMIT)
                                if new_symbols:
                                    dynamic_watch_list = new_symbols
                                    last_dynamic_update = time.time()
                                    logger.info(f"🔥 تحديث القائمة الديناميكية: {len(dynamic_watch_list)} عملة")

                            # 3. المراقبة الاستباقية (تضيف العملات الصامتة إلى قاعدة البيانات)
                            if config.PRE_WATCH_ENABLED:
                                await pre_watch_scanner(session, send_session)

                            # 4. 🔥 جمع العملات المرشحة للتحليل (الأساسية + الديناميكية + المرصودة)
                            pre_watch_symbols = []
                            if config.SCAN_UNLISTED_SYMBOLS:
                                pre_watch_symbols = await db.get_pre_watch(limit=config.MAX_PREWATCH_TO_SCAN)
                            pre_watch_list = [p["symbol"] for p in pre_watch_symbols]

                            all_symbols = list(set(config.CORE_UNIVERSE + dynamic_watch_list + pre_watch_list))
                            logger.info(f"🔄 فحص {len(all_symbols)} عملة (تشمل {len(pre_watch_list)} من Pre-watch)...")

                            tasks = [
                                process_single_symbol(session, symbol, semaphore, send_session)
                                for symbol in all_symbols
                            ]
                            results = await asyncio.gather(*tasks, return_exceptions=True)

                            for result in results:
                                if isinstance(result, Exception):
                                    logger.error(f"خطأ في المعالجة: {result}")

                            logger.info("✅ انتهت الدورة. انتظار 5 دقائق...")
                            await asyncio.sleep(300)

                        except asyncio.CancelledError:
                            logger.info("🛑 تم إلغاء الماسح")
                            return
                        except Exception as e:
                            logger.error(f"⚠️ خطأ في حلقة المسح: {e}")
                            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"💥 فشل الماسح بشكل جسيم: {e}, إعادة التشغيل بعد 10 ثوانٍ...")
            await asyncio.sleep(10)

# -------------------- المراقبة الاستباقية --------------------
async def pre_watch_scanner(session, send_session):
    try:
        logger.info("🔭 بدء المسح الاستباقي...")
        opportunities = await scan_market_for_opportunities(session)
        
        for opp in opportunities[:config.PRE_WATCH_MAX_SYMBOLS]:
            symbol = opp["symbol"]
            pre_watch = await db.get_pre_watch(limit=100)
            existing = [p["symbol"] for p in pre_watch]
            if symbol in existing:
                continue
            
            analysis = await analyze_pre_watch_candidate(session, opp)
            if not analysis:
                continue
            
            await db.add_to_pre_watch(
                symbol,
                analysis["score"],
                analysis["volume_24h"],
                analysis["change_24h"],
                analysis["market_cap"],
                ", ".join(analysis["reasons"][:3])
            )
            
            if analysis["score"] >= config.PRE_WATCH_ALERT_THRESHOLD:
                await send_pre_watch_alert(send_session, analysis)
                await db.mark_pre_watch_alert_sent(symbol)
            
            logger.info(f"🔭 تمت إضافة {symbol} إلى المراقبة (نقاط: {analysis['score']})")
        
        await db.clean_expired_pre_watch(48)
        
    except Exception as e:
        logger.error(f"خطأ في المراقبة الاستباقية: {e}")

async def send_pre_watch_alert(session, analysis):
    symbol = analysis["symbol"]
    score = analysis["score"]
    change = analysis["change_24h"]
    volume = analysis["volume_24h"] / 1_000_000
    reasons = analysis["reasons"]
    
    msg = (
        f"🔭 *تنبيه مراقبة استباقي!*\n\n"
        f"📊 العملة: `{symbol}`\n"
        f"🎯 نقاط الثقة: `{score}/100`\n"
        f"📈 التغير 24h: `{change:+.1f}%`\n"
        f"📊 الحجم 24h: `${volume:.1f}M`\n"
        f"📝 الأسباب: {', '.join(reasons[:3])}\n\n"
        f"⚠️ هذه العملة تظهر علامات انفجار مبكر.\n"
        f"🔄 سيتم مراقبتها تلقائياً حتى تأكيد الإشارة."
    )
    
    from utils import send_to_all_subscribers
    await send_to_all_subscribers(session, msg)

# -------------------- معالجة العملات الفردية --------------------
async def process_single_symbol(session, symbol, semaphore, send_session):
    async with semaphore:
        try:
            cooldown = await db.get_cooldown(symbol)
            if cooldown:
                last_time = datetime.fromisoformat(cooldown)
                if (datetime.now(timezone.utc) - last_time) < timedelta(minutes=config.COOLDOWN_MINUTES):
                    return None

            data_5m = await fetch_klines(session, symbol, '5m', 100)
            data_1h = await fetch_klines(session, symbol, '1h', 30)
            data_4h = await fetch_klines(session, symbol, '4h', 20)
            stats = await fetch_24hr_stats(session, symbol)

            if not data_5m or not data_1h or not data_4h or stats.get('volume', 0) < config.MIN_VOLUME_USD:
                return None

            engine = SignalEngine(symbol, data_5m, data_1h, data_4h, stats)
            result = await engine.evaluate()
            
            # ✅ سجل النقاط لمراقبة الأداء
            logger.info(f"📊 {symbol}: score={result['score']}, action={result['action']}, early_breakout={result.get('is_early_breakout', False)}")

            if not result['is_actionable'] and not result['is_explosion'] and not result.get('is_early_breakout', False):
                return None

            action = result.get('action', 'NEUTRAL')
            if action == "NEUTRAL" and not result['is_explosion'] and not result.get('is_early_breakout', False):
                return None

            stop_loss, take_profit, pos_size = engine.calculate_risk(
                result['price'], action, stop_loss=None
            )

            if stop_loss == 0 or take_profit == 0:
                logger.warning(f"⚠️ SL أو TP صفر لـ {symbol}")
                return None

            result['stop_loss'] = stop_loss
            result['take_profit'] = take_profit
            result['position_size'] = pos_size

            # إشارات الانفجار المبكر لها أولوية عالية
            if result.get('is_early_breakout', False):
                await send_confirmed_signal(send_session, result, early=True)
                await db.set_cooldown(symbol, datetime.now(timezone.utc).isoformat())
                logger.info(f"🚀 قنص مبكر لـ {symbol}: {result['signal']}")
                return result

            if result.get('is_explosion', False):
                await send_confirmed_signal(send_session, result, early=False)
                await db.set_cooldown(symbol, datetime.now(timezone.utc).isoformat())
                logger.info(f"⚡ إشارة انفجار فورية لـ {symbol}: {result['signal']}")
                return result

            if "شراء" in result['signal'] or "بيع" in result['signal']:
                conf_engine = ConfirmationEngine(engine)
                confirmed = await conf_engine.wait_and_confirm(session)
                if confirmed:
                    conf_action = confirmed.get('action', 'NEUTRAL')
                    if conf_action != "NEUTRAL":
                        sl, tp, ps = engine.calculate_risk(confirmed['price'], conf_action)
                        confirmed['stop_loss'] = sl
                        confirmed['take_profit'] = tp
                        confirmed['position_size'] = ps
                        if sl != 0 and tp != 0:
                            await send_confirmed_signal(send_session, confirmed, early=False)
                            await db.set_cooldown(symbol, datetime.now(timezone.utc).isoformat())
                            logger.info(f"✅ إشارة مؤكدة لـ {symbol}: {confirmed['signal']}")
            else:
                await send_watch_signal(send_session, result)
                logger.info(f"👀 إشارة مراقبة لـ {symbol}")

        except Exception as e:
            logger.error(f"خطأ في معالجة {symbol}: {e}")

# -------------------- دوال إرسال الرسائل --------------------
async def send_message_async(session, chat_id, message):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    for attempt in range(3):
        try:
            async with session.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=5) as resp:
                if resp.status == 200:
                    return
                elif resp.status == 429:
                    data = await resp.json()
                    retry_after = data.get('retry_after', 5)
                    await asyncio.sleep(retry_after)
                else:
                    logger.warning(f"Telegram error {resp.status}")
        except Exception as e:
            logger.error(f"Error sending to {chat_id}: {e}")
            await asyncio.sleep(2 ** attempt)

async def send_to_all_subscribers(session, message):
    subscribers = await db.get_subscribers()
    if not subscribers:
        return
    tasks = [send_message_async(session, chat_id, message) for chat_id in subscribers]
    await asyncio.gather(*tasks, return_exceptions=True)

async def send_confirmed_signal(session, signal, early=False):
    news = await fetch_news(session, signal['symbol'])
    news_text = f"\n📰 أخبار: {news['title']}" if news else ""

    entry = signal['price']
    stop_loss = signal.get('stop_loss', 0)
    take_profit = signal.get('take_profit', 0)
    pos_size = signal.get('position_size', 0.02)

    if stop_loss == 0 or take_profit == 0:
        logger.error(f"❌ SL أو TP صفر لـ {signal['symbol']}")
        return

    action = signal.get('action', 'NEUTRAL')
    if action == 'BUY':
        if stop_loss >= entry or take_profit <= entry:
            logger.error(f"❌ SL/TP غير منطقي للشراء في {signal['symbol']}")
            return
    elif action == 'SELL':
        if stop_loss <= entry or take_profit >= entry:
            logger.error(f"❌ SL/TP غير منطقي للبيع في {signal['symbol']}")
            return

    if early:
        explosion_tag = "🚀 *قنص مبكر!* "
    elif signal.get('is_explosion', False):
        explosion_tag = "⚡ *انفجار!* "
    else:
        explosion_tag = "🔥 "

    msg = (
        f"{explosion_tag}🔥 *إشارة مؤكدة!*\n"
        f"📊 {signal['symbol']} | النقاط: {signal['score']}/10\n"
        f"🔔 {signal['signal']}\n\n"
        f"💰 سعر الدخول: `{entry:.4f}`\n"
        f"🛑 وقف الخسارة: `{stop_loss:.4f}`\n"
        f"🎯 جني الأرباح: `{take_profit:.4f}`\n"
        f"📊 حجم الصفقة: `{pos_size*100:.2f}%`\n"
        f"📈 RSI: {signal['rsi']} | ADX: {signal['adx']}\n"
        f"📝 الأسباب: {', '.join(signal['reasons'][:3])}\n"
        f"{news_text}"
    )
    await send_to_all_subscribers(session, msg)

    if config.PAPER_TRADING:
        await db.save_paper_trade(signal['symbol'], signal['signal'], entry, stop_loss, take_profit, pos_size)

async def send_watch_signal(session, signal):
    msg = (
        f"👀 *مراقبة*: {signal['symbol']}\n"
        f"📊 النقاط: {signal['score']}/10\n"
        f"🔔 {signal['signal']}\n"
        f"💰 السعر: `{signal['price']:.4f}`\n"
        f"📈 RSI: {signal['rsi']} | ADX: {signal['adx']}\n"
        f"📝 الأسباب: {', '.join(signal['reasons'][:2])}"
    )
    await send_to_all_subscribers(session, msg)

# -------------------- دوال إدارة البوت --------------------
async def post_init(application):
    global background_tasks
    await db.init()

    await application.bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Webhook deleted (drop_pending_updates=True)")

    await asyncio.sleep(5)
    logger.info("⏳ انتظار 5 ثوانٍ لتجنب التعارض")

    scanner_task = asyncio.create_task(market_scanner_loop(), name="scanner")
    pinger_task = asyncio.create_task(self_pinger(), name="pinger")
    background_tasks = [scanner_task, pinger_task]

    asyncio.create_task(monitor_tasks())

    logger.info("✅ Scanner started")
    logger.info("✅ Self-Pinger started")

async def monitor_tasks():
    while not shutdown_event.is_set():
        for i, task in enumerate(background_tasks):
            if task.done():
                try:
                    exc = task.exception()
                    if exc:
                        logger.error(f"⚠️ المهمة {task.get_name()} انتهت بخطأ: {exc}")
                    else:
                        logger.warning(f"⚠️ المهمة {task.get_name()} انتهت بشكل غير متوقع")
                    
                    if task.get_name() == "scanner":
                        new_task = asyncio.create_task(market_scanner_loop(), name="scanner")
                    else:
                        new_task = asyncio.create_task(self_pinger(), name="pinger")
                    background_tasks[i] = new_task
                    logger.info(f"🔄 تم إعادة تشغيل {task.get_name()}")
                except Exception as e:
                    logger.error(f"❌ خطأ في مراقبة المهام: {e}")
        await asyncio.sleep(30)

async def shutdown():
    logger.info("🛑 جارٍ إيقاف المهام الخلفية...")
    shutdown_event.set()
    for task in background_tasks:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except RuntimeError as e:
                if "Event loop is closed" not in str(e):
                    logger.error(f"خطأ في إلغاء المهمة: {e}")
    await db.close()
    logger.info("✅ تم إيقاف جميع المهام")

# -------------------- الوظيفة الرئيسية --------------------
def main():
    if not config.TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN غير موجود! يرجى تعيينه في متغيرات البيئة.")
        sys.exit(1)

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask Server Started")

    application = Application.builder().token(config.TELEGRAM_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("approve", handlers.approve))
    application.add_handler(CommandHandler("add", handlers.add_user))
    application.add_handler(CommandHandler("adduser", handlers.adduser))
    application.add_handler(CommandHandler("removeuser", handlers.removeuser))
    application.add_handler(CommandHandler("status", handlers.status))
    application.add_handler(CommandHandler("prewatch", handlers.prewatch))
    application.add_handler(CommandHandler("performance", handlers.performance))
    application.add_handler(CommandHandler("signal", handlers.signal_now))

    logger.info("✅ Starting Telegram Bot with Polling...")
    
    # حل مشكلة Event Loop
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        application.run_polling(
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
            stop_signals=None
        )
    finally:
        loop.close()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("🛑 تم إيقاف البوت يدوياً")
    except Exception as e:
        logger.error(f"⚠️ توقف غير متوقع: {e}")
    finally:
        logger.info("🏁 تم إنهاء التطبيق")
