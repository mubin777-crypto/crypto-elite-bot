# database.py - قاعدة البيانات مع إضافات المراقبة الاستباقية
import aiosqlite
import logging
from datetime import datetime, timezone
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
        
        # الجداول الحالية...
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

    # ... باقي الدوال (نفس السابق) ...

    # -------------------- دوال المراقبة الاستباقية (جديدة) --------------------
    async def add_to_pre_watch(self, symbol: str, score: float, volume_24h: float, change_24h: float, market_cap: float, reason: str):
        """إضافة عملة إلى قائمة المراقبة الاستباقية"""
        now = datetime.now(timezone.utc).isoformat()
        await self.execute(
            """INSERT OR REPLACE INTO pre_watch 
               (symbol, flagged_at, last_checked, score, volume_24h, change_24h, market_cap, reason) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            symbol, now, now, score, volume_24h, change_24h, market_cap, reason
        )

    async def update_pre_watch(self, symbol: str, score: float, reason: str):
        """تحديث عملة في قائمة المراقبة"""
        now = datetime.now(timezone.utc).isoformat()
        await self.execute(
            """UPDATE pre_watch SET last_checked = ?, score = ?, reason = ? WHERE symbol = ?""",
            now, score, reason, symbol
        )

    async def get_pre_watch(self, limit: int = 20) -> List[Dict]:
        """جلب قائمة المراقبة الاستباقية"""
        rows = await self.fetch(
            """SELECT symbol, flagged_at, last_checked, score, volume_24h, change_24h, market_cap, reason, alert_sent 
               FROM pre_watch WHERE status = 'ACTIVE' ORDER BY score DESC LIMIT ?""",
            limit
        )
        return [dict(row) for row in rows]

    async def mark_pre_watch_alert_sent(self, symbol: str):
        """تحديث حالة إرسال التنبيه"""
        await self.execute("UPDATE pre_watch SET alert_sent = 1 WHERE symbol = ?", symbol)

    async def remove_from_pre_watch(self, symbol: str):
        """إزالة عملة من قائمة المراقبة"""
        await self.execute("DELETE FROM pre_watch WHERE symbol = ?", symbol)

    async def clean_expired_pre_watch(self, hours: int = 48):
        """إزالة العملات القديمة من المراقبة"""
        expiry = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        await self.execute("DELETE FROM pre_watch WHERE last_checked < ?", expiry)

db = Database()
