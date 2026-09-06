# database.py
# SQLite State Management

from datetime import datetime, timezone
import aiosqlite
import config
import logging

logger = logging.getLogger("quant_bot.database")

class Database:
    def __init__(self, path):
        self.path = path
        self.conn = None

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
                quality REAL,
                entry REAL,
                sl REAL,
                tp REAL,
                rr REAL,
                quantity REAL,
                status TEXT DEFAULT 'OPEN',
                result REAL DEFAULT 0,
                result_r REAL DEFAULT 0,
                created_at TEXT NOT NULL,
                closed_at TEXT,
                exit_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS pre_watch (
                symbol TEXT PRIMARY KEY,
                reason TEXT,
                price_change REAL,
                quote_volume REAL,
                trades INTEGER,
                price REAL,
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
                signals INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                breakeven INTEGER DEFAULT 0,
                inconclusive INTEGER DEFAULT 0,
                timeout INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER,
                symbol TEXT,
                direction TEXT,
                entry REAL,
                exit_price REAL,
                result_r REAL,
                exit_reason TEXT,
                timestamp TEXT
            );
        """)
        await self.conn.commit()
        for factor in config.FACTORS:
            existing = await self.get_weight(factor)
            if existing is None:
                await self.save_weight(factor, 1.0)
        logger.info("Database initialized successfully")

    async def close(self):
        if self.conn:
            await self.conn.close()
            self.conn = None

    # ============================================================
    # Subscribers
    # ============================================================
    async def add_subscriber(self, user_id):
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self.conn.execute(
                """INSERT INTO subscribers (user_id, active, created_at)
                   VALUES (?, 1, ?) ON CONFLICT(user_id) DO UPDATE SET active = 1""",
                (int(user_id), now)
            )
            await self.conn.commit()
            logger.info(f"Subscriber added/updated: {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to add subscriber {user_id}: {e}")
            return False

    async def remove_subscriber(self, user_id):
        try:
            await self.conn.execute(
                """UPDATE subscribers SET active = 0 WHERE user_id = ?""",
                (int(user_id),)
            )
            await self.conn.commit()
            logger.info(f"Subscriber removed: {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove subscriber {user_id}: {e}")
            return False

    async def get_subscribers(self):
        try:
            cursor = await self.conn.execute(
                """SELECT user_id FROM subscribers WHERE active = 1 ORDER BY user_id"""
            )
            rows = await cursor.fetchall()
            return [int(row["user_id"]) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get subscribers: {e}")
            return []

    async def count_subscribers(self):
        try:
            cursor = await self.conn.execute(
                """SELECT COUNT(*) FROM subscribers WHERE active = 1"""
            )
            row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"Failed to count subscribers: {e}")
            return 0

    # ============================================================
    # Cooldown
    # ============================================================
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

    # ============================================================
    # Signals
    # ============================================================
    async def add_signal(self, signal):
        cursor = await self.conn.execute(
            """INSERT INTO signals (
                symbol, direction, score, quality, entry, sl, tp, rr, quantity, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                signal["symbol"],
                signal["direction"],
                signal["score"],
                signal.get("quality", 0),
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

    async def close_signal(self, signal_id, result, result_r, exit_reason="SL"):
        now = datetime.now(timezone.utc).isoformat()
        await self.conn.execute(
            """UPDATE signals SET status = 'CLOSED', result = ?, result_r = ?, closed_at = ?, exit_reason = ?
               WHERE id = ?""",
            (result, result_r, now, exit_reason, signal_id)
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

    # ============================================================
    # Pre-watch
    # ============================================================
    async def add_prewatch(self, symbol, reason, price_change, quote_volume, trades=0, price=0):
        now = datetime.now(timezone.utc).isoformat()
        await self.conn.execute(
            """INSERT INTO pre_watch (symbol, reason, price_change, quote_volume, trades, price, added_at, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET
               reason = excluded.reason, price_change = excluded.price_change,
               quote_volume = excluded.quote_volume, trades = excluded.trades,
               price = excluded.price, last_seen = excluded.last_seen""",
            (symbol.upper(), reason, price_change, quote_volume, trades, price, now, now)
        )
        await self.conn.commit()

    async def get_prewatch(self, limit=20):
        cursor = await self.conn.execute(
            """SELECT * FROM pre_watch ORDER BY ABS(price_change) DESC, quote_volume DESC LIMIT ?""",
            (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ============================================================
    # Adaptive Weights
    # ============================================================
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

    # ============================================================
    # Daily Stats
    # ============================================================
    async def get_daily_pnl(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cursor = await self.conn.execute(
            """SELECT pnl, wins, losses, breakeven, inconclusive, timeout FROM daily_stats WHERE date = ?""",
            (today,)
        )
        row = await cursor.fetchone()
        if row:
            return {"pnl": float(row[0]), "wins": row[1], "losses": row[2],
                    "breakeven": row[3], "inconclusive": row[4], "timeout": row[5]}
        return {"pnl": 0.0, "wins": 0, "losses": 0, "breakeven": 0, "inconclusive": 0, "timeout": 0}

    async def add_daily_result(self, capital, pnl, outcome):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if outcome == "win":
            await self.conn.execute(
                """INSERT INTO daily_stats (date, capital, pnl, signals, wins)
                   VALUES (?, ?, ?, 1, 1) ON CONFLICT(date) DO UPDATE SET
                   pnl = pnl + excluded.pnl, signals = signals + 1, wins = wins + 1""",
                (today, capital, pnl)
            )
        elif outcome == "loss":
            await self.conn.execute(
                """INSERT INTO daily_stats (date, capital, pnl, signals, losses)
                   VALUES (?, ?, ?, 1, 1) ON CONFLICT(date) DO UPDATE SET
                   pnl = pnl + excluded.pnl, signals = signals + 1, losses = losses + 1""",
                (today, capital, pnl)
            )
        elif outcome == "breakeven":
            await self.conn.execute(
                """INSERT INTO daily_stats (date, capital, pnl, signals, breakeven)
                   VALUES (?, ?, ?, 1, 1) ON CONFLICT(date) DO UPDATE SET
                   pnl = pnl + excluded.pnl, signals = signals + 1, breakeven = breakeven + 1""",
                (today, capital, pnl)
            )
        elif outcome == "inconclusive":
            await self.conn.execute(
                """INSERT INTO daily_stats (date, capital, pnl, signals, inconclusive)
                   VALUES (?, ?, ?, 1, 1) ON CONFLICT(date) DO UPDATE SET
                   pnl = pnl + excluded.pnl, signals = signals + 1, inconclusive = inconclusive + 1""",
                (today, capital, pnl)
            )
        elif outcome == "timeout":
            await self.conn.execute(
                """INSERT INTO daily_stats (date, capital, pnl, signals, timeout)
                   VALUES (?, ?, ?, 1, 1) ON CONFLICT(date) DO UPDATE SET
                   pnl = pnl + excluded.pnl, signals = signals + 1, timeout = timeout + 1""",
                (today, capital, pnl)
            )
        await self.conn.commit()

    async def reset_daily(self, capital):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await self.conn.execute(
            """INSERT OR REPLACE INTO daily_stats (date, capital, pnl, signals, wins, losses, breakeven, inconclusive, timeout)
               VALUES (?, ?, 0, 0, 0, 0, 0, 0, 0)""",
            (today, capital)
        )
        await self.conn.commit()

    # ============================================================
    # Trade Log
    # ============================================================
    async def log_trade(self, signal_id, symbol, direction, entry, exit_price, result_r, exit_reason):
        now = datetime.now(timezone.utc).isoformat()
        await self.conn.execute(
            """INSERT INTO trade_log (signal_id, symbol, direction, entry, exit_price, result_r, exit_reason, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (signal_id, symbol, direction, entry, exit_price, result_r, exit_reason, now)
        )
        await self.conn.commit()
