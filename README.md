# 🤖 Crypto Elite Signal Bot (Multi-Strategy)

بوت إشارات تداول احترافي يجمع بين 4 استراتيجيات (RSI, EMA, Volume Spikes, Breakout) ويرصد أكثر من 200 عملة بما فيها عملات الميم الساخنة عبر DexScreener.

## الميزات
- نظام نقاط (Scoring) يجمع 4 استراتيجيات في إشارة واحدة.
- اكتشاف تلقائي لعملات الميم الرائجة من DexScreener.
- معالجة متوازية (ThreadPool) لفحص 200 عملة بسرعة دون استهلاك ذاكرة.
- أوامر تليجرام تفاعلية: `/status`, `/add`, `/remove`, `/signal`.
- يعمل 24/7 على Render مع نظام إبقاء على الحياة ذاتي.

## النشر
1. أضف متغيرات البيئة: `TELEGRAM_TOKEN` و `CHAT_ID`.
2. استخدم Render.com Web Service مع أمر البدء: `python bot.py`
