#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
bot.py - الملف الرئيسي لتشغيل البوت
الإصدار النهائي مع إصلاحات Conflict و Task destruction
"""

import os
import sys
import asyncio
import threading
import logging
import time
from datetime import datetime, timedelta, timezone
from flask import Flask
from telegram.ext import Application, CommandHandler, ContextTypes

from config import config
from database import db
from utils import self_pinger, fetch_top_symbols, fetch_klines, fetch_24hr_stats, fetch_news, symbol_to_currency_name
from telegram_bot import handlers
from signals import SignalEngine, ConfirmationEngine

# -------------------- إعدادات التسجيل --------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# -------------------- Flask للتشغيل الصحي --------------------
app = Flask('')

@app.route('/')
@app.route('/healthcheck')
def home():
    return "✅ Elite Bot V4 - Fully Operational"

def run_flask():
    """تشغيل Flask في خيط منفصل لـ health check"""
    app.run(host='0.0.0.0', port=config.PORT, debug=False, use_reloader=False)

# -------------------- متغيرات عامة --------------------
dynamic_watch_list = []
last_dynamic_update = 0
background_tasks = []

# -------------------- دوال المسح --------------------
async def market_scanner_loop():
    """حلقة المسح الرئيسية - تفحص العملات وترسل الإشارات"""
    global dynamic_watch_list, last_dynamic_update
    logger.info("🚀 بدء الماسح المحسن...")
    semaphore = asyncio.Semaphore(config.SEMAPHORE_LIMIT)
    
    async with aiohttp.ClientSession() as session:
        async with aiohttp.ClientSession() as send_session:
            while True:
                try:
                    # تحديث القائمة الديناميكية كل 30 دقيقة
                    if time.time() - last_dynamic_update > config.DYNAMIC_UPDATE_INTERVAL:
                        new_symbols = await fetch_top_symbols(session, config.DYNAMIC_SYMBOLS_LIMIT)
                        if new_symbols:
                            dynamic_watch_list = new_symbols
                            last_dynamic_update = time.time()
                            logger.info(f"🔥 تحديث القائمة الديناميكية: {len(dynamic_watch_list)} عملة")

                    all_symbols = list(set(config.CORE_UNIVERSE + dynamic_watch_list))
                    logger.info(f"🔄 فحص {len(all_symbols)} عملة ...")

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
                    break
                except Exception as e:
                    logger.error(f"خطأ في حلقة المسح: {e}")
                    await asyncio.sleep(60)

async def process_single_symbol(session, symbol, semaphore, send_session):
    """معالجة عملة واحدة: تحليل، تأكيد، إرسال"""
    async with semaphore:
        try:
            # التحقق من التبريد
            cooldown = await db.get_cooldown(symbol)
            if cooldown:
                last_time = datetime.fromisoformat(cooldown)
                if (datetime.now(timezone.utc) - last_time) < timedelta(minutes=config.COOLDOWN_MINUTES):
                    return None

            # جلب البيانات
            data_5m = await fetch_klines(session, symbol, '5m', 100)
            data_1h = await fetch_klines(session, symbol, '1h', 30)
            data_4h = await fetch_klines(session, symbol, '4h', 20)
            stats = await fetch_24hr_stats(session, symbol)

            if not data_5m or not data_1h or not data_4h or stats.get('volume', 0) < config.MIN_VOLUME_USD:
                return None

            # التحليل الأساسي
            engine = SignalEngine(symbol, data_5m, data_1h, data_4h, stats)
            result = engine.evaluate()

            if not result['is_actionable']:
                logger.debug(f"⏭️ {symbol}: نقاط {result['score']} < عتبة {config.SIGNAL_SCORE_THRESHOLD}")
                return None

            # إذا كانت الإشارة شراء/بيع → ننتظر التأكيد
            if "شراء" in result['signal'] or "بيع" in result['signal']:
                conf_engine = ConfirmationEngine(engine)
                confirmed = await conf_engine.wait_and_confirm(session)
                if confirmed:
                    await send_confirmed_signal(send_session, confirmed, engine)
                    await db.set_cooldown(symbol, datetime.now(timezone.utc).isoformat())
                    logger.info(f"✅ إشارة مؤكدة لـ {symbol}: {confirmed['signal']}")
                else:
                    logger.info(f"⏭️ {symbol}: لم يتم تأكيد الإشارة")
            else:
                # إشارة مراقبة
                await send_watch_signal(send_session, result)
                logger.info(f"👀 إشارة مراقبة لـ {symbol}")

        except Exception as e:
            logger.error(f"خطأ في معالجة {symbol}: {e}")

# -------------------- دوال إرسال الرسائل --------------------
async def send_message_async(session, chat_id, message):
    """إرسال رسالة إلى تليجرام مع إعادة محاولة"""
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    for attempt in range(3):
        try:
            async with session.post(
                url,
                json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
                timeout=5
            ) as resp:
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
    """إرسال رسالة لجميع المشتركين"""
    subscribers = await db.get_subscribers()
    if not subscribers:
        return
    tasks = [send_message_async(session, chat_id, message) for chat_id in subscribers]
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info(f"✅ تم إرسال الرسالة لـ {len(subscribers)} مشترك")

async def send_confirmed_signal(session, signal, engine):
    """بناء وإرسال إشارة مؤكدة مع الأخبار والمخاطر"""
    news = await fetch_news(session, signal['symbol'])
    news_text = f"\n📰 أخبار: {news['title']}" if news else ""

    entry = signal['price']
    stop_loss, take_profit, pos_size = engine.calculate_risk(entry)

    msg = (
        f"🔥 *إشارة مؤكدة!*\n"
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

    # تسجيل صفقة وهمية (Paper Trading)
    if config.PAPER_TRADING:
        trade_id = await db.save_paper_trade(
            signal['symbol'], signal['signal'], entry, stop_loss, take_profit, pos_size
        )
        logger.info(f"📝 تم تسجيل صفقة وهمية: {signal['symbol']} (ID: {trade_id})")

async def send_watch_signal(session, signal):
    """إرسال إشارة مراقبة (تنبيه أولي)"""
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
    """تهيئة البوت بعد بدء التطبيق"""
    global background_tasks
    await db.init()

    # حذف webhook مع تجاهل التحديثات المعلقة لتجنب Conflict
    await application.bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Webhook deleted (drop_pending_updates=True)")

    # انتظار 5 ثوانٍ للسماح للعمليات السابقة بالإغلاق
    await asyncio.sleep(5)
    logger.info("⏳ انتظار 5 ثوانٍ لتجنب التعارض")

    # تشغيل المهام الخلفية
    scanner_task = asyncio.create_task(market_scanner_loop(), name="scanner")
    pinger_task = asyncio.create_task(self_pinger(), name="pinger")
    background_tasks = [scanner_task, pinger_task]

    logger.info("✅ Scanner started")
    logger.info("✅ Self-Pinger started")

async def shutdown():
    """إيقاف نظيف للمهام الخلفية"""
    logger.info("🛑 جارٍ إيقاف المهام الخلفية...")
    for task in background_tasks:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    await db.close()
    logger.info("✅ تم إيقاف جميع المهام")

def main():
    """الدالة الرئيسية لتشغيل البوت"""
    # التحقق من وجود التوكن
    if not config.TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN غير موجود! يرجى تعيينه في متغيرات البيئة.")
        sys.exit(1)

    # تشغيل Flask في خيط منفصل
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask Server Started")

    # بناء تطبيق التليجرام
    application = Application.builder().token(config.TELEGRAM_TOKEN).post_init(post_init).build()

    # إضافة معالجات الأوامر
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("approve", handlers.approve))
    application.add_handler(CommandHandler("adduser", handlers.adduser))
    application.add_handler(CommandHandler("status", handlers.status))
    application.add_handler(CommandHandler("performance", handlers.performance))
    application.add_handler(CommandHandler("signal", handlers.signal_now))

    logger.info("✅ Starting Telegram Bot with Polling...")
    try:
        application.run_polling(allowed_updates=["message", "callback_query"])
    finally:
        # إيقاف نظيف عند الخروج
        asyncio.run(shutdown())

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("🛑 تم إيقاف البوت يدوياً")
    except Exception as e:
        logger.error(f"⚠️ توقف غير متوقع: {e}")
    finally:
        logger.info("🏁 تم إنهاء التطبيق")
