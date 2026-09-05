# database.py
# SQLite State Management

from datetime import datetime, timezone
import aiosqlite
import config  # تم التعديل: استيراد config مباشرة بدلاً من CFG

class Database:
    def __init__(self, path):
        self.path = path
        self.conn = None

    # ========================================================
    # Init
    # ========================================================
    async def init(self):
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA busy_timeout=5000")
        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS subscribers (
                user_id INTEGER PRIMARY KEY,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cooldown (
                symbol TEXT PRIMARY KEY,
                direction TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                score REAL,
                entry REAL,
                sl REAL,
                tp REAL,
                rr REAL,
                quantity REAL,
                status TEXT DEFAULT 'OPEN',
                result REAL DEFAULT 0,
                result_r REAL DEFAULT 0,
                created_at TEXT NOT NULL,
                closed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS pre_watch (
                symbol TEXT PRIMARY KEY,
                reason TEXT,
                price_change REAL,
                quote_volume REAL,
                added_at TEXT NOT NULL,
                last_seen TEXT
            );
            CREATE TABLE IF NOT EXISTS adaptive_weights (
                factor TEXT PRIMARY KEY,
                weight REAL NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                capital REAL NOT NULL,
                pnl REAL DEFAULT 0,
                signals INTEGER DEFAULT 0
            );
        """)
        await self.conn.commit()
        # إنشاء الأوزان الأولية
        for factor in config.FACTORS:
            existing = await self.get_weight(factor)
            if existing is None:
                await self.save_weight(factor, 1.0)

    # ========================================================
    # Close
    # ========================================================
    async def close(self):
        if self.conn:
            await self.conn.close()
            self.conn = None

    # ========================================================
    # Subscribers
    # ========================================================
    async def add_subscriber(self, user_id):
        now = datetime.now(timezone.utc).isoformat()
        await self.conn.execute(
            """INSERT INTO subscribers (user_id, active, created_at)
               VALUES (?, 1, ?) ON CONFLICT(user_id) DO UPDATE SET active = 1""",
            (int(user_id), now)
        )
        await self.conn.commit()

    async def remove_subscriber(self, user_id):
        await self.conn.execute(
            """UPDATE subscribers SET active = 0 WHERE user_id = ?""",
            (int(user_id),)
        )
        await self.conn.commit()

    async def get_subscribers(self):
        cursor = await self.conn.execute(
            """SELECT user_id FROM subscribers WHERE active = 1 ORDER BY user_id"""
        )
        rows = await cursor.fetchall()
        return [int(row["user_id"]) for row in rows]

    # ========================================================
    # Cooldown
    # ========================================================
    async def set_cooldown(self, symbol, direction):
        now = datetime.now(timezone.utc).isoformat()
        await self.conn.execute(
            """INSERT INTO cooldown (symbol, direction, created_at)
               VALUES (?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET
               direction = excluded.direction, created_at = excluded.created_at""",
            (symbol.upper(), direction, now)
        )
        await self.conn.commit()

    async def get_cooldown(self, symbol):
        cursor = await self.conn.execute(
            """SELECT * FROM cooldown WHERE symbol = ?""",
            (symbol.upper(),)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    # ========================================================
    # Signals
    # ========================================================
    async def add_signal(self, signal):
        cursor = await self.conn.execute(
            """INSERT INTO signals (
                symbol, direction, score, entry, sl, tp, rr, quantity, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                signal["symbol"],
                signal["direction"],
                signal["score"],
                signal["entry"],
                signal["sl"],
                signal["tp"],
                signal["rr"],
                signal["position_size"],
                signal["timestamp"],
            )
        )
        await self.conn.commit()
        return cursor.lastrowid

    async def get_signal(self, signal_id):
        cursor = await self.conn.execute(
            """SELECT * FROM signals WHERE id = ?""",
            (signal_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_open_signals(self):
        cursor = await self.conn.execute(
            """SELECT * FROM signals WHERE status = 'OPEN' ORDER BY id ASC"""
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def close_signal(self, signal_id, result, result_r):
        now = datetime.now(timezone.utc).isoformat()
        await self.conn.execute(
            """UPDATE signals SET status = 'CLOSED', result = ?, result_r = ?, closed_at = ?
               WHERE id = ?""",
            (result, result_r, now, signal_id)
        )
        await self.conn.commit()

    async def get_daily_signals(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cursor = await self.conn.execute(
            """SELECT * FROM signals WHERE substr(created_at, 1, 10) = ? ORDER BY id DESC""",
            (today,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ========================================================
    # Pre-watch
    # ========================================================
    async def add_prewatch(self, symbol, reason, price_change, quote_volume):
        now = datetime.now(timezone.utc).isoformat()
        await self.conn.execute(
            """INSERT INTO pre_watch (symbol, reason, price_change, quote_volume, added_at, last_seen)
               VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET
               reason = excluded.reason, price_change = excluded.price_change,
               quote_volume = excluded.quote_volume, last_seen = excluded.last_seen""",
            (symbol.upper(), reason, price_change, quote_volume, now, now)
        )
        await self.conn.commit()

    async def get_prewatch(self, limit=20):
        cursor = await self.conn.execute(
            """SELECT * FROM pre_watch ORDER BY ABS(price_change) DESC, quote_volume DESC LIMIT ?""",
            (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ========================================================
    # Adaptive Weights
    # ========================================================
    async def save_weight(self, factor, weight):
        now = datetime.now(timezone.utc).isoformat()
        await self.conn.execute(
            """INSERT INTO adaptive_weights (factor, weight, updated_at)
               VALUES (?, ?, ?) ON CONFLICT(factor) DO UPDATE SET
               weight = excluded.weight, updated_at = excluded.updated_at""",
            (factor, float(weight), now)
        )
        await self.conn.commit()

    async def get_weight(self, factor):
        cursor = await self.conn.execute(
            """SELECT weight FROM adaptive_weights WHERE factor = ?""",
            (factor,)
        )
        row = await cursor.fetchone()
        return float(row["weight"]) if row else None

    async def get_weights(self):
        cursor = await self.conn.execute(
            """SELECT factor, weight FROM adaptive_weights"""
        )
        rows = await cursor.fetchall()
        return {row["factor"]: float(row["weight"]) for row in rows}

    # ========================================================
    # Daily Stats
    # ========================================================
    async def get_daily_pnl(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cursor = await self.conn.execute(
            """SELECT pnl FROM daily_stats WHERE date = ?""",
            (today,)
        )
        row = await cursor.fetchone()
        return float(row["pnl"]) if row else 0.0

    async def add_daily_pnl(self, capital, pnl):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await self.conn.execute(
            """INSERT INTO daily_stats (date, capital, pnl, signals)
               VALUES (?, ?, ?, 1) ON CONFLICT(date) DO UPDATE SET
               pnl = pnl + excluded.pnl, signals = signals + 1""",
            (today, capital, pnl)
        )
        await self.conn.commit()

    async def reset_daily(self, capital):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await self.conn.execute(
            """INSERT OR REPLACE INTO daily_stats (date, capital, pnl, signals)
               VALUES (?, ?, 0, 0)""",
            (today, capital)
        )
        await self.conn.commit()
