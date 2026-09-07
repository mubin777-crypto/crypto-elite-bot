# database.py
# Database Manager - PostgreSQL / SQLite

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import config

logger = logging.getLogger("quant_bot.database")

class Database:
    def __init__(self):
        self.conn = None
        self.use_postgres = config.USE_POSTGRES
        self.db_path = config.DB_PATH
        self.database_url = config.DATABASE_URL

    async def init(self):
        """تهيئة قاعدة البيانات"""
        if self.use_postgres and self.database_url:
            await self._init_postgres()
        else:
            await self._init_sqlite()
        logger.info(f"Database initialized: {'PostgreSQL' if self.use_postgres else 'SQLite'}")

    async def _init_postgres(self):
        """تهيئة PostgreSQL"""
        try:
            import asyncpg
            self.conn = await asyncpg.connect(self.database_url)

            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS subscribers (
                    user_id BIGINT PRIMARY KEY,
                    active INTEGER DEFAULT 1,
                    created_at TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cooldown (
                    symbol TEXT PRIMARY KEY,
                    direction TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS signals (
                    id SERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    score REAL,
                    quality REAL,
                    entry REAL,
                    sl REAL,
                    tp REAL,
                    rr REAL,
                    quantity REAL,
                    actual_risk_percent REAL,
                    factor_contributions JSONB,
                    factor_weights JSONB,
                    used_factors JSONB,
                    status TEXT DEFAULT 'OPEN',
                    result REAL DEFAULT 0,
                    result_r REAL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL,
                    closed_at TIMESTAMP,
                    exit_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS pre_watch (
                    symbol TEXT PRIMARY KEY,
                    reason TEXT,
                    price_change REAL,
                    quote_volume REAL,
                    trades INTEGER,
                    price REAL,
                    added_at TIMESTAMP NOT NULL,
                    last_seen TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS adaptive_weights (
                    factor TEXT PRIMARY KEY,
                    weight REAL NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date DATE PRIMARY KEY,
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
                    id SERIAL PRIMARY KEY,
                    signal_id INTEGER,
                    symbol TEXT,
                    direction TEXT,
                    entry REAL,
                    exit_price REAL,
                    result_r REAL,
                    exit_reason TEXT,
                    timestamp TIMESTAMP
                );
            """)
            logger.info("PostgreSQL tables created")

            # إنشاء الأوزان الأولية
            for factor in config.FACTORS:
                existing = await self.get_weight(factor)
                if existing is None:
                    await self.save_weight(factor, 1.0)

        except ImportError:
            logger.warning("asyncpg not installed, falling back to SQLite")
            self.use_postgres = False
            await self._init_sqlite()
        except Exception as e:
            logger.error(f"PostgreSQL init error: {e}")
            self.use_postgres = False
            await self._init_sqlite()

    async def _init_sqlite(self):
        """تهيئة SQLite"""
        import aiosqlite
        self.conn = await aiosqlite.connect(self.db_path)
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
                actual_risk_percent REAL,
                factor_contributions TEXT,
                factor_weights TEXT,
                used_factors TEXT,
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

        # إنشاء الأوزان الأولية
        for factor in config.FACTORS:
            existing = await self.get_weight(factor)
            if existing is None:
                await self.save_weight(factor, 1.0)

    async def close(self):
        """إغلاق اتصال قاعدة البيانات"""
        if self.conn:
            await self.conn.close()
            self.conn = None

    # ============================================================
    # Subscribers
    # ============================================================
    async def add_subscriber(self, user_id):
        now = datetime.now(timezone.utc).isoformat()
        try:
            if self.use_postgres:
                await self.conn.execute(
                    """INSERT INTO subscribers (user_id, active, created_at)
                       VALUES ($1, 1, $2) ON CONFLICT (user_id) DO UPDATE SET active = 1""",
                    user_id, now
                )
            else:
                await self.conn.execute(
                    """INSERT INTO subscribers (user_id, active, created_at)
                       VALUES (?, 1, ?) ON CONFLICT(user_id) DO UPDATE SET active = 1""",
                    (user_id, now)
                )
                await self.conn.commit()
            logger.info(f"Subscriber added/updated: {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to add subscriber {user_id}: {e}")
            return False

    async def remove_subscriber(self, user_id):
        try:
            if self.use_postgres:
                await self.conn.execute("UPDATE subscribers SET active = 0 WHERE user_id = $1", user_id)
            else:
                await self.conn.execute("UPDATE subscribers SET active = 0 WHERE user_id = ?", (user_id,))
                await self.conn.commit()
            logger.info(f"Subscriber removed: {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove subscriber {user_id}: {e}")
            return False

    async def get_subscribers(self):
        try:
            if self.use_postgres:
                rows = await self.conn.fetch("SELECT user_id FROM subscribers WHERE active = 1 ORDER BY user_id")
                return [int(row["user_id"]) for row in rows]
            else:
                cursor = await self.conn.execute("SELECT user_id FROM subscribers WHERE active = 1 ORDER BY user_id")
                rows = await cursor.fetchall()
                return [int(row["user_id"]) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get subscribers: {e}")
            return []

    # ============================================================
    # Cooldown
    # ============================================================
    async def get_cooldown(self, symbol: str):
        try:
            if self.use_postgres:
                row = await self.conn.fetchrow("SELECT * FROM cooldown WHERE symbol = $1", symbol)
                return dict(row) if row else None
            else:
                cursor = await self.conn.execute("SELECT * FROM cooldown WHERE symbol = ?", (symbol,))
                row = await cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get cooldown for {symbol}: {e}")
            return None

    async def set_cooldown(self, symbol: str, direction: str):
        now = datetime.now(timezone.utc).isoformat()
        try:
            if self.use_postgres:
                await self.conn.execute("""
                    INSERT INTO cooldown (symbol, direction, created_at)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (symbol) DO UPDATE SET direction = $2, created_at = $3
                """, symbol, direction, now)
            else:
                await self.conn.execute("""
                    INSERT INTO cooldown (symbol, direction, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET direction = excluded.direction, created_at = excluded.created_at
                """, (symbol, direction, now))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to set cooldown for {symbol}: {e}")

    # ============================================================
    # Signals
    # ============================================================
    async def add_signal(self, signal):
        now = datetime.now(timezone.utc).isoformat()
        try:
            factor_contributions = json.dumps(signal.get("factor_contributions", {}))
            factor_weights = json.dumps(signal.get("factor_weights", {}))
            used_factors = json.dumps(signal.get("used_factors", {}))

            if self.use_postgres:
                row = await self.conn.fetchrow("""
                    INSERT INTO signals (
                        symbol, direction, score, quality, entry, sl, tp, rr, quantity,
                        actual_risk_percent, factor_contributions, factor_weights, used_factors,
                        created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    RETURNING id
                """, signal["symbol"], signal["direction"], signal["score"],
                    signal.get("quality", 0), signal["entry"], signal["sl"],
                    signal["tp"], signal["rr"], signal["position_size"],
                    signal.get("actual_risk_percent", 0),
                    factor_contributions, factor_weights, used_factors, now)
                return row["id"]
            else:
                cursor = await self.conn.execute("""
                    INSERT INTO signals (
                        symbol, direction, score, quality, entry, sl, tp, rr, quantity,
                        actual_risk_percent, factor_contributions, factor_weights, used_factors,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, signal["symbol"], signal["direction"], signal["score"],
                    signal.get("quality", 0), signal["entry"], signal["sl"],
                    signal["tp"], signal["rr"], signal["position_size"],
                    signal.get("actual_risk_percent", 0),
                    factor_contributions, factor_weights, used_factors, now)
                await self.conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"Failed to add signal: {e}")
            return None

    async def get_signal(self, signal_id):
        try:
            if self.use_postgres:
                row = await self.conn.fetchrow("SELECT * FROM signals WHERE id = $1", signal_id)
                return dict(row) if row else None
            else:
                cursor = await self.conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,))
                row = await cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get signal: {e}")
            return None

    async def get_open_signals(self):
        try:
            if self.use_postgres:
                rows = await self.conn.fetch("SELECT * FROM signals WHERE status = 'OPEN' ORDER BY id ASC")
                return [dict(row) for row in rows]
            else:
                cursor = await self.conn.execute("SELECT * FROM signals WHERE status = 'OPEN' ORDER BY id ASC")
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get open signals: {e}")
            return []

    async def close_signal(self, signal_id, result, result_r, exit_reason="SL"):
        now = datetime.now(timezone.utc).isoformat()
        try:
            if self.use_postgres:
                await self.conn.execute("""
                    UPDATE signals SET status = 'CLOSED', result = $1, result_r = $2,
                    closed_at = $3, exit_reason = $4 WHERE id = $5
                """, result, result_r, now, exit_reason, signal_id)
            else:
                await self.conn.execute("""
                    UPDATE signals SET status = 'CLOSED', result = ?, result_r = ?,
                    closed_at = ?, exit_reason = ? WHERE id = ?
                """, (result, result_r, now, exit_reason, signal_id))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to close signal: {e}")

    async def get_daily_signals(self):
        today = datetime.now(timezone.utc).date().isoformat()
        try:
            if self.use_postgres:
                rows = await self.conn.fetch(
                    "SELECT * FROM signals WHERE created_at::date = $1::date ORDER BY id DESC", today
                )
                return [dict(row) for row in rows]
            else:
                cursor = await self.conn.execute(
                    "SELECT * FROM signals WHERE DATE(created_at) = ? ORDER BY id DESC", (today,)
                )
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get daily signals: {e}")
            return []

    # ============================================================
    # Pre-watch
    # ============================================================
    async def add_prewatch(self, symbol, reason, price_change, quote_volume, trades, price):
        now = datetime.now(timezone.utc).isoformat()
        try:
            if self.use_postgres:
                await self.conn.execute("""
                    INSERT INTO pre_watch (symbol, reason, price_change, quote_volume, trades, price, added_at, last_seen)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $7)
                    ON CONFLICT (symbol) DO UPDATE SET
                    reason = $2, price_change = $3, quote_volume = $4, trades = $5, price = $6, last_seen = $7
                """, symbol, reason, price_change, quote_volume, trades, price, now)
            else:
                await self.conn.execute("""
                    INSERT INTO pre_watch (symbol, reason, price_change, quote_volume, trades, price, added_at, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                    reason = excluded.reason, price_change = excluded.price_change,
                    quote_volume = excluded.quote_volume, trades = excluded.trades,
                    price = excluded.price, last_seen = excluded.last_seen
                """, (symbol, reason, price_change, quote_volume, trades, price, now, now))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to add prewatch for {symbol}: {e}")

    async def get_prewatch(self, limit=30):
        try:
            if self.use_postgres:
                rows = await self.conn.fetch("SELECT * FROM pre_watch ORDER BY added_at DESC LIMIT $1", limit)
                return [dict(row) for row in rows]
            else:
                cursor = await self.conn.execute("SELECT * FROM pre_watch ORDER BY added_at DESC LIMIT ?", (limit,))
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get prewatch: {e}")
            return []

    # ============================================================
    # Adaptive Weights
    # ============================================================
    async def get_weight(self, factor):
        """الحصول على وزن عامل معين"""
        try:
            if self.use_postgres:
                row = await self.conn.fetchrow(
                    "SELECT weight FROM adaptive_weights WHERE factor = $1", factor
                )
                return float(row["weight"]) if row else None
            else:
                cursor = await self.conn.execute(
                    "SELECT weight FROM adaptive_weights WHERE factor = ?", (factor,)
                )
                row = await cursor.fetchone()
                return float(row["weight"]) if row else None
        except Exception as e:
            logger.error(f"Failed to get weight for {factor}: {e}")
            return None

    async def save_weight(self, factor, weight):
        now = datetime.now(timezone.utc).isoformat()
        try:
            if self.use_postgres:
                await self.conn.execute("""
                    INSERT INTO adaptive_weights (factor, weight, updated_at)
                    VALUES ($1, $2, $3) ON CONFLICT (factor) DO UPDATE SET weight = $2, updated_at = $3
                """, factor, float(weight), now)
            else:
                await self.conn.execute("""
                    INSERT INTO adaptive_weights (factor, weight, updated_at)
                    VALUES (?, ?, ?) ON CONFLICT(factor) DO UPDATE SET weight = excluded.weight, updated_at = excluded.updated_at
                """, (factor, float(weight), now))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to save weight: {e}")

    async def get_weights(self):
        try:
            if self.use_postgres:
                rows = await self.conn.fetch("SELECT factor, weight FROM adaptive_weights")
                return {row["factor"]: float(row["weight"]) for row in rows}
            else:
                cursor = await self.conn.execute("SELECT factor, weight FROM adaptive_weights")
                rows = await cursor.fetchall()
                return {row["factor"]: float(row["weight"]) for row in rows}
        except Exception as e:
            logger.error(f"Failed to get weights: {e}")
            return {}

    # ============================================================
    # Daily Stats
    # ============================================================
    async def get_daily_pnl(self):
        today = datetime.now(timezone.utc).date().isoformat()
        try:
            if self.use_postgres:
                row = await self.conn.fetchrow("""
                    SELECT pnl, wins, losses, breakeven, inconclusive, timeout
                    FROM daily_stats WHERE date = $1::date
                """, today)
                if row:
                    return {
                        "pnl": float(row["pnl"]),
                        "wins": row["wins"],
                        "losses": row["losses"],
                        "breakeven": row["breakeven"],
                        "inconclusive": row["inconclusive"],
                        "timeout": row["timeout"]
                    }
            else:
                cursor = await self.conn.execute("""
                    SELECT pnl, wins, losses, breakeven, inconclusive, timeout
                    FROM daily_stats WHERE date = ?
                """, (today,))
                row = await cursor.fetchone()
                if row:
                    return {
                        "pnl": float(row["pnl"]),
                        "wins": row["wins"],
                        "losses": row["losses"],
                        "breakeven": row["breakeven"],
                        "inconclusive": row["inconclusive"],
                        "timeout": row["timeout"]
                    }
            return {"pnl": 0.0, "wins": 0, "losses": 0, "breakeven": 0, "inconclusive": 0, "timeout": 0}
        except Exception as e:
            logger.error(f"Failed to get daily pnl: {e}")
            return {"pnl": 0.0, "wins": 0, "losses": 0, "breakeven": 0, "inconclusive": 0, "timeout": 0}

    async def add_daily_result(self, capital, pnl, outcome):
        today = datetime.now(timezone.utc).date().isoformat()
        try:
            if self.use_postgres:
                if outcome == "win":
                    await self.conn.execute("""
                        INSERT INTO daily_stats (date, capital, pnl, signals, wins)
                        VALUES ($1, $2, $3, 1, 1) ON CONFLICT (date) DO UPDATE SET
                        pnl = daily_stats.pnl + $3, signals = daily_stats.signals + 1,
                        wins = daily_stats.wins + 1
                    """, today, capital, pnl)
                elif outcome == "loss":
                    await self.conn.execute("""
                        INSERT INTO daily_stats (date, capital, pnl, signals, losses)
                        VALUES ($1, $2, $3, 1, 1) ON CONFLICT (date) DO UPDATE SET
                        pnl = daily_stats.pnl + $3, signals = daily_stats.signals + 1,
                        losses = daily_stats.losses + 1
                    """, today, capital, pnl)
                elif outcome == "breakeven":
                    await self.conn.execute("""
                        INSERT INTO daily_stats (date, capital, pnl, signals, breakeven)
                        VALUES ($1, $2, $3, 1, 1) ON CONFLICT (date) DO UPDATE SET
                        pnl = daily_stats.pnl + $3, signals = daily_stats.signals + 1,
                        breakeven = daily_stats.breakeven + 1
                    """, today, capital, pnl)
                elif outcome == "inconclusive":
                    await self.conn.execute("""
                        INSERT INTO daily_stats (date, capital, pnl, signals, inconclusive)
                        VALUES ($1, $2, $3, 1, 1) ON CONFLICT (date) DO UPDATE SET
                        pnl = daily_stats.pnl + $3, signals = daily_stats.signals + 1,
                        inconclusive = daily_stats.inconclusive + 1
                    """, today, capital, pnl)
                elif outcome == "timeout":
                    await self.conn.execute("""
                        INSERT INTO daily_stats (date, capital, pnl, signals, timeout)
                        VALUES ($1, $2, $3, 1, 1) ON CONFLICT (date) DO UPDATE SET
                        pnl = daily_stats.pnl + $3, signals = daily_stats.signals + 1,
                        timeout = daily_stats.timeout + 1
                    """, today, capital, pnl)
            else:
                if outcome == "win":
                    await self.conn.execute("""
                        INSERT INTO daily_stats (date, capital, pnl, signals, wins)
                        VALUES (?, ?, ?, 1, 1) ON CONFLICT(date) DO UPDATE SET
                        pnl = pnl + excluded.pnl, signals = signals + 1, wins = wins + 1
                    """, (today, capital, pnl))
                elif outcome == "loss":
                    await self.conn.execute("""
                        INSERT INTO daily_stats (date, capital, pnl, signals, losses)
                        VALUES (?, ?, ?, 1, 1) ON CONFLICT(date) DO UPDATE SET
                        pnl = pnl + excluded.pnl, signals = signals + 1, losses = losses + 1
                    """, (today, capital, pnl))
                elif outcome == "breakeven":
                    await self.conn.execute("""
                        INSERT INTO daily_stats (date, capital, pnl, signals, breakeven)
                        VALUES (?, ?, ?, 1, 1) ON CONFLICT(date) DO UPDATE SET
                        pnl = pnl + excluded.pnl, signals = signals + 1, breakeven = breakeven + 1
                    """, (today, capital, pnl))
                elif outcome == "inconclusive":
                    await self.conn.execute("""
                        INSERT INTO daily_stats (date, capital, pnl, signals, inconclusive)
                        VALUES (?, ?, ?, 1, 1) ON CONFLICT(date) DO UPDATE SET
                        pnl = pnl + excluded.pnl, signals = signals + 1, inconclusive = inconclusive + 1
                    """, (today, capital, pnl))
                elif outcome == "timeout":
                    await self.conn.execute("""
                        INSERT INTO daily_stats (date, capital, pnl, signals, timeout)
                        VALUES (?, ?, ?, 1, 1) ON CONFLICT(date) DO UPDATE SET
                        pnl = pnl + excluded.pnl, signals = signals + 1, timeout = timeout + 1
                    """, (today, capital, pnl))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to add daily result: {e}")

    async def reset_daily(self, capital):
        today = datetime.now(timezone.utc).date().isoformat()
        try:
            if self.use_postgres:
                await self.conn.execute("DELETE FROM daily_stats WHERE date = $1::date", today)
            else:
                await self.conn.execute("DELETE FROM daily_stats WHERE date = ?", (today,))
                await self.conn.commit()
            logger.info("Daily stats reset")
        except Exception as e:
            logger.error(f"Failed to reset daily stats: {e}")

    # ============================================================
    # Trade Log
    # ============================================================
    async def log_trade(self, signal_id, symbol, direction, entry, exit_price, result_r, exit_reason):
        now = datetime.now(timezone.utc).isoformat()
        try:
            if self.use_postgres:
                await self.conn.execute("""
                    INSERT INTO trade_log (signal_id, symbol, direction, entry, exit_price, result_r, exit_reason, timestamp)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """, signal_id, symbol, direction, entry, exit_price, result_r, exit_reason, now)
            else:
                await self.conn.execute("""
                    INSERT INTO trade_log (signal_id, symbol, direction, entry, exit_price, result_r, exit_reason, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (signal_id, symbol, direction, entry, exit_price, result_r, exit_reason, now))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to log trade: {e}")
