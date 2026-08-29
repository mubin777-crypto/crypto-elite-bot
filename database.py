# database.py - قاعدة البيانات مع جدول المراقبة الاستباقية
import aiosqlite
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict
from config import config

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.db_path = config.DB_PATH
        self._conn = None

    async def _get_conn(self):
        if self._conn is None:
            self._conn = await aiosqlite.connect(self.db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    async def init(self):
        conn = await self._get_conn()
        # الجداول الحالية
        await conn.execute('''CREATE TABLE IF NOT EXISTS subscribers (user_id TEXT PRIMARY KEY)''')
        await conn.execute('''CREATE TABLE IF NOT EXISTS pending (user_id TEXT PRIMARY KEY)''')
        await conn.execute('''CREATE TABLE IF NOT EXISTS signal_cooldown (symbol TEXT PRIMARY KEY, last_signal_time TEXT)''')
        
        await conn.execute('''CREATE TABLE IF NOT EXISTS signals_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            timestamp TEXT,
            signal_type TEXT,
            price REAL,
            stop_loss REAL,
            take_profit REAL,
            result TEXT,
            profit_loss REAL,
            entry_time TEXT,
            exit_time TEXT,
            position_size REAL,
            outcome TEXT
        )''')
        
        await conn.execute('''CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            action TEXT,
            entry_price REAL,
            stop_loss REAL,
            take_profit REAL,
            position_size REAL,
            entry_time TEXT,
            exit_price REAL,
            exit_time TEXT,
            profit_loss REAL,
            status TEXT DEFAULT 'OPEN'
        )''')
        
        await conn.execute('''CREATE TABLE IF NOT EXISTS performance_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            total_trades INTEGER,
            wins INTEGER,
            losses INTEGER,
            win_rate REAL,
            profit_factor REAL,
            avg_win REAL,
            avg_loss REAL,
            expectancy REAL,
            max_drawdown REAL,
            sharpe_ratio REAL,
            consecutive_losses INTEGER,
            total_return REAL
        )''')
        
        await conn.execute('''CREATE TABLE IF NOT EXISTS factor_performance (
            factor TEXT PRIMARY KEY,
            total_signals INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            weight REAL DEFAULT 1.0,
            last_updated TEXT
        )''')
        for factor in ['rsi', 'trend', 'momentum', 'volume', 'adx']:
            await conn.execute("INSERT OR IGNORE INTO factor_performance (factor, weight) VALUES (?, ?)", (factor, 1.0))
        
        # 🔥 جدول المراقبة الاستباقية (جديد)
        await conn.execute('''CREATE TABLE IF NOT EXISTS pre_watch (
            symbol TEXT PRIMARY KEY,
            flagged_at TEXT,
            last_checked TEXT,
            score REAL,
            volume_24h REAL,
            change_24h REAL,
            market_cap REAL,
            reason TEXT,
            status TEXT DEFAULT 'ACTIVE',
            alert_sent INTEGER DEFAULT 0
        )''')
        
        if config.ADMIN_CHAT_ID:
            await conn.execute("INSERT OR IGNORE INTO subscribers (user_id) VALUES (?)", (config.ADMIN_CHAT_ID,))
        await conn.commit()
        logger.info(f"✅ قاعدة البيانات مهيأة: {self.db_path}")

    # -------------------- دوال المشتركين --------------------
    async def get_subscribers(self):
        rows = await self.fetch("SELECT user_id FROM subscribers")
        return [row[0] for row in rows]

    async def add_subscriber(self, user_id):
        await self.execute("INSERT OR IGNORE INTO subscribers (user_id) VALUES (?)", user_id)

    async def remove_subscriber(self, user_id):
        await self.execute("DELETE FROM subscribers WHERE user_id = ?", user_id)

    async def get_pending(self):
        rows = await self.fetch("SELECT user_id FROM pending")
        return [row[0] for row in rows]

    async def add_pending(self, user_id):
        await self.execute("INSERT OR IGNORE INTO pending (user_id) VALUES (?)", user_id)

    async def remove_pending(self, user_id):
        await self.execute("DELETE FROM pending WHERE user_id = ?", user_id)

    # -------------------- دوال التبريد --------------------
    async def get_cooldown(self, symbol):
        row = await self.fetchone("SELECT last_signal_time FROM signal_cooldown WHERE symbol = ?", symbol)
        return row[0] if row else None

    async def set_cooldown(self, symbol, timestamp):
        await self.execute("INSERT OR REPLACE INTO signal_cooldown (symbol, last_signal_time) VALUES (?, ?)", symbol, timestamp)

    # -------------------- دوال الصفقات --------------------
    async def save_signal(self, symbol, signal_type, price, stop_loss, take_profit, position_size=0):
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self.execute(
            """INSERT INTO signals_history 
               (symbol, timestamp, signal_type, price, stop_loss, take_profit, position_size, entry_time) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            symbol, now, signal_type, price, stop_loss, take_profit, position_size, now
        )
        return cursor.lastrowid

    async def save_paper_trade(self, symbol, action, entry_price, stop_loss, take_profit, position_size):
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self.execute(
            """INSERT INTO paper_trades 
               (symbol, action, entry_price, stop_loss, take_profit, position_size, entry_time) 
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            symbol, action, entry_price, stop_loss, take_profit, position_size, now
        )
        return cursor.lastrowid

    async def update_paper_trade(self, trade_id, exit_price, exit_time, profit_loss, status='CLOSED'):
        await self.execute(
            """UPDATE paper_trades SET exit_price = ?, exit_time = ?, profit_loss = ?, status = ? WHERE id = ?""",
            exit_price, exit_time, profit_loss, status, trade_id
        )

    async def get_open_paper_trades(self):
        return await self.fetch("SELECT id, symbol, action, entry_price, stop_loss, take_profit, position_size, entry_time FROM paper_trades WHERE status = 'OPEN'")

    # -------------------- دوال الأداء --------------------
    async def update_performance(self, metrics):
        today = datetime.now(timezone.utc).date().isoformat()
        await self.execute(
            """INSERT OR REPLACE INTO performance_metrics 
               (date, total_trades, wins, losses, win_rate, profit_factor, avg_win, avg_loss, expectancy, max_drawdown, sharpe_ratio, consecutive_losses, total_return)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            today, metrics.get('total_trades', 0), metrics.get('wins', 0),
            metrics.get('losses', 0), metrics.get('win_rate', 0.0),
            metrics.get('profit_factor', 0.0), metrics.get('avg_win', 0.0),
            metrics.get('avg_loss', 0.0), metrics.get('expectancy', 0.0),
            metrics.get('max_drawdown', 0.0), metrics.get('sharpe_ratio', 0.0),
            metrics.get('consecutive_losses', 0), metrics.get('total_return', 0.0)
        )

    async def get_performance(self):
        return await self.fetchone("SELECT * FROM performance_metrics ORDER BY id DESC LIMIT 1")

    # -------------------- دوال التعلم التكيفي --------------------
    async def get_factor_weights(self):
        rows = await self.fetch("SELECT factor, weight FROM factor_performance")
        return {row[0]: row[1] for row in rows}

    async def update_factor_weight(self, factor, weight):
        await self.execute("UPDATE factor_performance SET weight = ?, last_updated = ? WHERE factor = ?",
                           weight, datetime.now(timezone.utc).isoformat(), factor)

    async def record_factor_result(self, factor, win):
        await self.execute("UPDATE factor_performance SET total_signals = total_signals + 1, wins = wins + ? WHERE factor = ?",
                           (1 if win else 0, factor))

    # -------------------- دوال المراقبة الاستباقية (جديدة) --------------------
    async def add_to_pre_watch(self, symbol: str, score: float, volume_24h: float, change_24h: float, market_cap: float, reason: str):
        now = datetime.now(timezone.utc).isoformat()
        await self.execute(
            """INSERT OR REPLACE INTO pre_watch 
               (symbol, flagged_at, last_checked, score, volume_24h, change_24h, market_cap, reason) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            symbol, now, now, score, volume_24h, change_24h, market_cap, reason
        )

    async def update_pre_watch(self, symbol: str, score: float, reason: str):
        now = datetime.now(timezone.utc).isoformat()
        await self.execute(
            """UPDATE pre_watch SET last_checked = ?, score = ?, reason = ? WHERE symbol = ?""",
            now, score, reason, symbol
        )

    async def get_pre_watch(self, limit: int = 20) -> List[Dict]:
        rows = await self.fetch(
            """SELECT symbol, flagged_at, last_checked, score, volume_24h, change_24h, market_cap, reason, alert_sent 
               FROM pre_watch WHERE status = 'ACTIVE' ORDER BY score DESC LIMIT ?""",
            limit
        )
        return [dict(row) for row in rows]

    async def mark_pre_watch_alert_sent(self, symbol: str):
        await self.execute("UPDATE pre_watch SET alert_sent = 1 WHERE symbol = ?", symbol)

    async def remove_from_pre_watch(self, symbol: str):
        await self.execute("DELETE FROM pre_watch WHERE symbol = ?", symbol)

    async def clean_expired_pre_watch(self, hours: int = 48):
        expiry = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        await self.execute("DELETE FROM pre_watch WHERE last_checked < ?", expiry)

    # -------------------- دوال مساعدة --------------------
    async def execute(self, query, *args):
        conn = await self._get_conn()
        cursor = await conn.execute(query, args)
        await conn.commit()
        return cursor

    async def fetch(self, query, *args):
        conn = await self._get_conn()
        cursor = await conn.execute(query, args)
        return await cursor.fetchall()

    async def fetchone(self, query, *args):
        conn = await self._get_conn()
        cursor = await conn.execute(query, args)
        return await cursor.fetchone()

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None

db = Database()
