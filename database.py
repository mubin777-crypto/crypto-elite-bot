# database.py
# Database Manager v2026.3.2 - Naive UTC for PostgreSQL TIMESTAMP

import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta, date
from typing import Optional
import config

logger = logging.getLogger("quant_bot.database")


# ============================================================
# Helpers - كلها تُعيد NAIVE datetime (بدون tzinfo)
# ============================================================
def _utcnow():
    """UTC الآن - naive (بدون tzinfo) لـ PostgreSQL TIMESTAMP"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_naive_datetime(val):
    """تحويل أي قيمة إلى naive datetime UTC"""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is not None:
            return val.astimezone(timezone.utc).replace(tzinfo=None)
        return val
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except Exception:
            return None
    return None


def _to_date(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val).date()
        except Exception:
            try:
                return datetime.strptime(val[:10], "%Y-%m-%d").date()
            except Exception:
                return None
    return None


class Database:
    def __init__(self):
        self.conn = None
        self.use_postgres = config.USE_POSTGRES
        self.db_path = config.DB_PATH
        self.database_url = config.DATABASE_URL
        self._pg_lock = asyncio.Lock()

    async def init(self):
        if self.use_postgres and self.database_url:
            await self._init_postgres()
        else:
            await self._init_sqlite()
        logger.info(f"Database: {'PostgreSQL' if self.use_postgres else 'SQLite'}")

    async def _init_postgres(self):
        try:
            import asyncpg
            self.conn = await asyncpg.connect(
                self.database_url,
                statement_cache_size=0,
                server_settings={"application_name": "crypto_bot"},
            )
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS subscribers (
                    user_id BIGINT PRIMARY KEY, active INTEGER DEFAULT 1, created_at TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cooldown (
                    symbol TEXT PRIMARY KEY, direction TEXT NOT NULL, created_at TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS signals (
                    id SERIAL PRIMARY KEY, symbol TEXT NOT NULL, direction TEXT NOT NULL,
                    signal_type TEXT, score REAL, quality REAL, entry REAL, sl REAL, tp REAL,
                    rr REAL, quantity REAL, actual_risk_percent REAL,
                    factor_contributions JSONB, factor_weights JSONB, used_factors JSONB,
                    status TEXT DEFAULT 'OPEN', result REAL DEFAULT 0, result_r REAL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL, closed_at TIMESTAMP, exit_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS pre_watch (
                    symbol TEXT PRIMARY KEY, reason TEXT, price_change REAL, quote_volume REAL,
                    trades INTEGER, price REAL, added_at TIMESTAMP NOT NULL, last_seen TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS adaptive_weights (
                    factor TEXT PRIMARY KEY, weight REAL NOT NULL, updated_at TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date DATE PRIMARY KEY, capital REAL NOT NULL, pnl REAL DEFAULT 0,
                    signals INTEGER DEFAULT 0, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
                    breakeven INTEGER DEFAULT 0, inconclusive INTEGER DEFAULT 0, timeout INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS trade_log (
                    id SERIAL PRIMARY KEY, signal_id INTEGER, symbol TEXT, direction TEXT,
                    entry REAL, exit_price REAL, result_r REAL, exit_reason TEXT, timestamp TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS signal_outcomes (
                    id SERIAL PRIMARY KEY, signal_id INTEGER UNIQUE, symbol TEXT NOT NULL,
                    direction TEXT NOT NULL, signal_type TEXT, entry_price REAL NOT NULL,
                    exit_price REAL, entry_time TIMESTAMP NOT NULL, exit_time TIMESTAMP,
                    highest_price REAL, lowest_price REAL, outcome TEXT,
                    pips_gained REAL DEFAULT 0, pips_lost REAL DEFAULT 0,
                    r_multiple REAL DEFAULT 0, duration_minutes INTEGER DEFAULT 0,
                    hit_tp INTEGER DEFAULT 0, hit_sl INTEGER DEFAULT 0,
                    max_drawdown_during_trade REAL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS near_miss (
                    id SERIAL PRIMARY KEY, symbol TEXT, reason TEXT, details JSONB,
                    timestamp TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
                CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
                CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
                CREATE INDEX IF NOT EXISTS idx_outcomes_symbol ON signal_outcomes(symbol);
                CREATE INDEX IF NOT EXISTS idx_outcomes_created ON signal_outcomes(entry_time);
            """)
            logger.info("PostgreSQL tables created")
            for factor in config.FACTORS:
                if await self.get_weight(factor) is None:
                    await self.save_weight(factor, 1.0)
        except ImportError:
            logger.warning("asyncpg missing, falling back to SQLite")
            self.use_postgres = False
            await self._init_sqlite()
        except Exception as e:
            logger.error(f"PostgreSQL init error: {e}")
            self.use_postgres = False
            await self._init_sqlite()

    async def _init_sqlite(self):
        import aiosqlite
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA busy_timeout=5000")
        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS subscribers (
                user_id INTEGER PRIMARY KEY, active INTEGER DEFAULT 1, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cooldown (
                symbol TEXT PRIMARY KEY, direction TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, direction TEXT NOT NULL,
                signal_type TEXT, score REAL, quality REAL, entry REAL, sl REAL, tp REAL,
                rr REAL, quantity REAL, actual_risk_percent REAL,
                factor_contributions TEXT, factor_weights TEXT, used_factors TEXT,
                status TEXT DEFAULT 'OPEN', result REAL DEFAULT 0, result_r REAL DEFAULT 0,
                created_at TEXT NOT NULL, closed_at TEXT, exit_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS pre_watch (
                symbol TEXT PRIMARY KEY, reason TEXT, price_change REAL, quote_volume REAL,
                trades INTEGER, price REAL, added_at TEXT NOT NULL, last_seen TEXT
            );
            CREATE TABLE IF NOT EXISTS adaptive_weights (
                factor TEXT PRIMARY KEY, weight REAL NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY, capital REAL NOT NULL, pnl REAL DEFAULT 0,
                signals INTEGER DEFAULT 0, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
                breakeven INTEGER DEFAULT 0, inconclusive INTEGER DEFAULT 0, timeout INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id INTEGER, symbol TEXT,
                direction TEXT, entry REAL, exit_price REAL, result_r REAL,
                exit_reason TEXT, timestamp TEXT
            );
            CREATE TABLE IF NOT EXISTS signal_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id INTEGER UNIQUE,
                symbol TEXT NOT NULL, direction TEXT NOT NULL, signal_type TEXT,
                entry_price REAL NOT NULL, exit_price REAL, entry_time TEXT NOT NULL,
                exit_time TEXT, highest_price REAL, lowest_price REAL, outcome TEXT,
                pips_gained REAL DEFAULT 0, pips_lost REAL DEFAULT 0,
                r_multiple REAL DEFAULT 0, duration_minutes INTEGER DEFAULT 0,
                hit_tp INTEGER DEFAULT 0, hit_sl INTEGER DEFAULT 0,
                max_drawdown_during_trade REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS near_miss (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, reason TEXT,
                details TEXT, timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
            CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
            CREATE INDEX IF NOT EXISTS idx_outcomes_symbol ON signal_outcomes(symbol);
        """)
        await self.conn.commit()
        for factor in config.FACTORS:
            if await self.get_weight(factor) is None:
                await self.save_weight(factor, 1.0)

    async def close(self):
        if self.conn:
            await self.conn.close()
            self.conn = None

    # ============================================================
    # 🔒 Safe PG wrappers
    # ============================================================
    async def _pg_execute(self, query, *args):
        async with self._pg_lock:
            return await self.conn.execute(query, *args)

    async def _pg_fetch(self, query, *args):
        async with self._pg_lock:
            return await self.conn.fetch(query, *args)

    async def _pg_fetchrow(self, query, *args):
        async with self._pg_lock:
            return await self.conn.fetchrow(query, *args)

    # ============================================================
    # Subscribers
    # ============================================================
    async def add_subscriber(self, user_id):
        try:
            if self.use_postgres:
                await self._pg_execute(
                    "INSERT INTO subscribers (user_id, active, created_at) VALUES ($1,1,$2) ON CONFLICT (user_id) DO UPDATE SET active=1",
                    user_id, _utcnow())
            else:
                await self.conn.execute(
                    "INSERT INTO subscribers (user_id, active, created_at) VALUES (?,1,?) ON CONFLICT(user_id) DO UPDATE SET active=1",
                    (user_id, _utcnow().isoformat()))
                await self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"add_subscriber error: {e}")
            return False

    async def remove_subscriber(self, user_id):
        try:
            if self.use_postgres:
                await self._pg_execute("UPDATE subscribers SET active=0 WHERE user_id=$1", user_id)
            else:
                await self.conn.execute("UPDATE subscribers SET active=0 WHERE user_id=?", (user_id,))
                await self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"remove_subscriber error: {e}")
            return False

    async def get_subscribers(self):
        try:
            if self.use_postgres:
                rows = await self._pg_fetch("SELECT user_id FROM subscribers WHERE active=1 ORDER BY user_id")
                return [int(r["user_id"]) for r in rows]
            cursor = await self.conn.execute("SELECT user_id FROM subscribers WHERE active=1 ORDER BY user_id")
            rows = await cursor.fetchall()
            return [int(r["user_id"]) for r in rows]
        except Exception as e:
            logger.error(f"get_subscribers error: {e}")
            return []

    # ============================================================
    # Cooldown
    # ============================================================
    async def get_cooldown(self, symbol):
        try:
            if self.use_postgres:
                row = await self._pg_fetchrow("SELECT * FROM cooldown WHERE symbol=$1", symbol)
                return dict(row) if row else None
            cursor = await self.conn.execute("SELECT * FROM cooldown WHERE symbol=?", (symbol,))
            row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"get_cooldown error: {e}")
            return None

    async def set_cooldown(self, symbol, direction):
        try:
            if self.use_postgres:
                await self._pg_execute("""
                    INSERT INTO cooldown (symbol, direction, created_at) VALUES ($1,$2,$3)
                    ON CONFLICT (symbol) DO UPDATE SET direction=$2, created_at=$3
                """, symbol, direction, _utcnow())
            else:
                await self.conn.execute("""
                    INSERT INTO cooldown (symbol, direction, created_at) VALUES (?,?,?)
                    ON CONFLICT(symbol) DO UPDATE SET direction=excluded.direction, created_at=excluded.created_at
                """, (symbol, direction, _utcnow().isoformat()))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"set_cooldown error: {e}")

    # ============================================================
    # Counts
    # ============================================================
    async def count_recent_signals_for_symbol(self, symbol, hours=4):
        try:
            cutoff = _utcnow() - timedelta(hours=hours)
            if self.use_postgres:
                row = await self._pg_fetchrow(
                    "SELECT COUNT(*) as cnt FROM signals WHERE symbol=$1 AND created_at>=$2",
                    symbol, cutoff)
                return int(row["cnt"]) if row else 0
            cursor = await self.conn.execute(
                "SELECT COUNT(*) as cnt FROM signals WHERE symbol=? AND created_at>=?",
                (symbol, cutoff.isoformat()))
            row = await cursor.fetchone()
            return int(row["cnt"]) if row else 0
        except Exception as e:
            logger.error(f"count_recent_signals_for_symbol error: {e}")
            return 0

    async def count_recent_signals_global(self, hours=1):
        try:
            cutoff = _utcnow() - timedelta(hours=hours)
            if self.use_postgres:
                row = await self._pg_fetchrow(
                    "SELECT COUNT(*) as cnt FROM signals WHERE created_at>=$1", cutoff)
                return int(row["cnt"]) if row else 0
            cursor = await self.conn.execute(
                "SELECT COUNT(*) as cnt FROM signals WHERE created_at>=?", (cutoff.isoformat(),))
            row = await cursor.fetchone()
            return int(row["cnt"]) if row else 0
        except Exception as e:
            logger.error(f"count_recent_signals_global error: {e}")
            return 0

    async def count_signals_today(self):
        try:
            today = _utcnow().date()
            if self.use_postgres:
                row = await self._pg_fetchrow(
                    "SELECT COUNT(*) as cnt FROM signals WHERE created_at::date=$1", today)
                return int(row["cnt"]) if row else 0
            cursor = await self.conn.execute(
                "SELECT COUNT(*) as cnt FROM signals WHERE DATE(created_at)=?", (today.isoformat(),))
            row = await cursor.fetchone()
            return int(row["cnt"]) if row else 0
        except Exception as e:
            logger.error(f"count_signals_today error: {e}")
            return 0

    # ============================================================
    # Signals
    # ============================================================
    async def add_signal(self, signal):
        try:
            fc = json.dumps(signal.get("factor_contributions", {}))
            fw = json.dumps(signal.get("factor_weights", {}))
            uf = json.dumps(signal.get("used_factors", {}))
            st = signal.get("signal_type", "TREND")
            if self.use_postgres:
                row = await self._pg_fetchrow("""
                    INSERT INTO signals (symbol, direction, signal_type, score, quality,
                        entry, sl, tp, rr, quantity, actual_risk_percent,
                        factor_contributions, factor_weights, used_factors, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                    RETURNING id
                """, signal["symbol"], signal["direction"], st,
                    signal["score"], signal.get("quality", 0),
                    signal["entry"], signal["sl"], signal["tp"], signal["rr"],
                    signal["position_size"], signal.get("actual_risk_percent", 0),
                    fc, fw, uf, _utcnow())
                return row["id"]
            params = (signal["symbol"], signal["direction"], st,
                      signal["score"], signal.get("quality", 0),
                      signal["entry"], signal["sl"], signal["tp"], signal["rr"],
                      signal["position_size"], signal.get("actual_risk_percent", 0),
                      fc, fw, uf, _utcnow().isoformat())
            cursor = await self.conn.execute("""
                INSERT INTO signals (symbol, direction, signal_type, score, quality,
                    entry, sl, tp, rr, quantity, actual_risk_percent,
                    factor_contributions, factor_weights, used_factors, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, params)
            await self.conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"add_signal error: {e}")
            return None

    async def get_signal(self, signal_id):
        try:
            if self.use_postgres:
                row = await self._pg_fetchrow("SELECT * FROM signals WHERE id=$1", signal_id)
                return dict(row) if row else None
            cursor = await self.conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"get_signal error: {e}")
            return None

    async def get_open_signals(self):
        try:
            if self.use_postgres:
                rows = await self._pg_fetch("SELECT * FROM signals WHERE status='OPEN' ORDER BY id ASC")
                return [dict(r) for r in rows]
            cursor = await self.conn.execute("SELECT * FROM signals WHERE status='OPEN' ORDER BY id ASC")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"get_open_signals error: {e}")
            return []

    async def close_signal(self, signal_id, result, result_r, exit_reason="SL"):
        try:
            if self.use_postgres:
                await self._pg_execute("""
                    UPDATE signals SET status='CLOSED', result=$1, result_r=$2,
                    closed_at=$3, exit_reason=$4 WHERE id=$5
                """, result, result_r, _utcnow(), exit_reason, signal_id)
            else:
                await self.conn.execute("""
                    UPDATE signals SET status='CLOSED', result=?, result_r=?,
                    closed_at=?, exit_reason=? WHERE id=?
                """, (result, result_r, _utcnow().isoformat(), exit_reason, signal_id))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"close_signal error: {e}")

    async def get_daily_signals(self):
        try:
            today = _utcnow().date()
            if self.use_postgres:
                rows = await self._pg_fetch(
                    "SELECT * FROM signals WHERE created_at::date=$1 ORDER BY id DESC", today)
                return [dict(r) for r in rows]
            cursor = await self.conn.execute(
                "SELECT * FROM signals WHERE DATE(created_at)=? ORDER BY id DESC", (today.isoformat(),))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"get_daily_signals error: {e}")
            return []

    # ============================================================
    # Signal Outcomes
    # ============================================================
    async def create_outcome(self, signal_id, symbol, direction, signal_type,
                             entry_price, entry_time, initial_high=None, initial_low=None):
        try:
            et = _to_naive_datetime(entry_time) or _utcnow()
            if self.use_postgres:
                await self._pg_execute("""
                    INSERT INTO signal_outcomes (signal_id, symbol, direction, signal_type,
                        entry_price, entry_time, highest_price, lowest_price, outcome, duration_minutes)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'PENDING',0)
                    ON CONFLICT (signal_id) DO NOTHING
                """, signal_id, symbol, direction, signal_type, entry_price, et,
                    initial_high or entry_price, initial_low or entry_price)
            else:
                await self.conn.execute("""
                    INSERT OR IGNORE INTO signal_outcomes (signal_id, symbol, direction, signal_type,
                        entry_price, entry_time, highest_price, lowest_price, outcome, duration_minutes)
                    VALUES (?,?,?,?,?,?,?,?,'PENDING',0)
                """, (signal_id, symbol, direction, signal_type, entry_price,
                      et.isoformat(), initial_high or entry_price, initial_low or entry_price))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"create_outcome error: {e}")

    async def update_outcome_highlow(self, signal_id, current_high, current_low):
        try:
            if self.use_postgres:
                await self._pg_execute("""
                    UPDATE signal_outcomes SET highest_price=GREATEST(highest_price,$1),
                    lowest_price=LEAST(lowest_price,$2) WHERE signal_id=$3 AND outcome='PENDING'
                """, current_high, current_low, signal_id)
            else:
                await self.conn.execute("""
                    UPDATE signal_outcomes SET highest_price=MAX(highest_price,?),
                    lowest_price=MIN(lowest_price,?) WHERE signal_id=? AND outcome='PENDING'
                """, (current_high, current_low, signal_id))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"update_outcome_highlow error: {e}")

    async def finalize_outcome(self, signal_id, exit_price, outcome,
                                hit_tp, hit_sl, r_multiple, duration_minutes,
                                pips_gained, pips_lost, max_dd):
        try:
            if self.use_postgres:
                await self._pg_execute("""
                    UPDATE signal_outcomes SET exit_price=$1, exit_time=$2, outcome=$3,
                    hit_tp=$4, hit_sl=$5, r_multiple=$6, duration_minutes=$7,
                    pips_gained=$8, pips_lost=$9, max_drawdown_during_trade=$10
                    WHERE signal_id=$11
                """, exit_price, _utcnow(), outcome, hit_tp, hit_sl, r_multiple,
                    duration_minutes, pips_gained, pips_lost, max_dd, signal_id)
            else:
                await self.conn.execute("""
                    UPDATE signal_outcomes SET exit_price=?, exit_time=?, outcome=?,
                    hit_tp=?, hit_sl=?, r_multiple=?, duration_minutes=?,
                    pips_gained=?, pips_lost=?, max_drawdown_during_trade=?
                    WHERE signal_id=?
                """, (exit_price, _utcnow().isoformat(), outcome, hit_tp, hit_sl, r_multiple,
                      duration_minutes, pips_gained, pips_lost, max_dd, signal_id))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"finalize_outcome error: {e}")

    async def get_pending_outcomes(self):
        try:
            sql = """
                SELECT so.*, s.sl, s.tp, s.quantity, s.score, s.quality
                FROM signal_outcomes so
                JOIN signals s ON s.id = so.signal_id
                WHERE so.outcome = 'PENDING'
                ORDER BY so.id ASC
            """
            if self.use_postgres:
                rows = await self._pg_fetch(sql)
                return [dict(r) for r in rows]
            cursor = await self.conn.execute(sql)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"get_pending_outcomes error: {e}")
            return []

    # ============================================================
    # Accuracy
    # ============================================================
    async def get_accuracy_stats(self, days=30):
        try:
            cutoff = _utcnow() - timedelta(days=days)
            if self.use_postgres:
                rows = await self._pg_fetch("""
                    SELECT outcome, r_multiple FROM signal_outcomes
                    WHERE entry_time>=$1 AND outcome!='PENDING'
                """, cutoff)
            else:
                cursor = await self.conn.execute("""
                    SELECT outcome, r_multiple FROM signal_outcomes
                    WHERE entry_time>=? AND outcome!='PENDING'
                """, (cutoff.isoformat(),))
                rows = await cursor.fetchall()
            return self._compute_stats(rows)
        except Exception as e:
            logger.error(f"get_accuracy_stats error: {e}")
            return self._empty_stats()

    async def get_accuracy_by_score(self, days=30):
        try:
            cutoff = _utcnow() - timedelta(days=days)
            if self.use_postgres:
                rows = await self._pg_fetch("""
                    SELECT so.outcome, so.r_multiple, s.score
                    FROM signal_outcomes so JOIN signals s ON s.id=so.signal_id
                    WHERE so.entry_time>=$1 AND so.outcome!='PENDING'
                """, cutoff)
            else:
                cursor = await self.conn.execute("""
                    SELECT so.outcome, so.r_multiple, s.score
                    FROM signal_outcomes so JOIN signals s ON s.id=so.signal_id
                    WHERE so.entry_time>=? AND so.outcome!='PENDING'
                """, (cutoff.isoformat(),))
                rows = await cursor.fetchall()
            buckets = {"7.5-8.0": [], "8.0-8.5": [], "8.5-9.0": [], "9.0+": []}
            for r in rows:
                sc = float(r["score"]) if r["score"] is not None else 0
                if sc < 8.0:
                    buckets["7.5-8.0"].append(r)
                elif sc < 8.5:
                    buckets["8.0-8.5"].append(r)
                elif sc < 9.0:
                    buckets["8.5-9.0"].append(r)
                else:
                    buckets["9.0+"].append(r)
            return {k: self._compute_stats(v) for k, v in buckets.items()}
        except Exception as e:
            logger.error(f"get_accuracy_by_score error: {e}")
            return {}

    async def get_accuracy_by_symbol(self, days=30, limit=10):
        try:
            cutoff = _utcnow() - timedelta(days=days)
            if self.use_postgres:
                rows = await self._pg_fetch("""
                    SELECT symbol, outcome, r_multiple FROM signal_outcomes
                    WHERE entry_time>=$1 AND outcome!='PENDING'
                """, cutoff)
            else:
                cursor = await self.conn.execute("""
                    SELECT symbol, outcome, r_multiple FROM signal_outcomes
                    WHERE entry_time>=? AND outcome!='PENDING'
                """, (cutoff.isoformat(),))
                rows = await cursor.fetchall()
            by_sym = {}
            for r in rows:
                by_sym.setdefault(r["symbol"], []).append(r)
            stats = {s: self._compute_stats(v) for s, v in by_sym.items()}
            sorted_s = sorted(stats.items(), key=lambda x: x[1]["win_rate"], reverse=True)
            return {
                "best": [{"symbol": s, "stats": st} for s, st in sorted_s[:limit]],
                "worst": [{"symbol": s, "stats": st} for s, st in sorted_s[-limit:]],
            }
        except Exception as e:
            logger.error(f"get_accuracy_by_symbol error: {e}")
            return {"best": [], "worst": []}

    async def get_accuracy_by_type(self, days=30):
        try:
            cutoff = _utcnow() - timedelta(days=days)
            if self.use_postgres:
                rows = await self._pg_fetch("""
                    SELECT signal_type, outcome, r_multiple FROM signal_outcomes
                    WHERE entry_time>=$1 AND outcome!='PENDING'
                """, cutoff)
            else:
                cursor = await self.conn.execute("""
                    SELECT signal_type, outcome, r_multiple FROM signal_outcomes
                    WHERE entry_time>=? AND outcome!='PENDING'
                """, (cutoff.isoformat(),))
                rows = await cursor.fetchall()
            by_t = {}
            for r in rows:
                st = r["signal_type"] or "TREND"
                by_t.setdefault(st, []).append(r)
            return {k: self._compute_stats(v) for k, v in by_t.items()}
        except Exception as e:
            logger.error(f"get_accuracy_by_type error: {e}")
            return {}

    async def get_recent_outcomes(self, limit=10):
        try:
            if self.use_postgres:
                rows = await self._pg_fetch("""
                    SELECT * FROM signal_outcomes WHERE outcome!='PENDING' ORDER BY id DESC LIMIT $1
                """, limit)
                return [dict(r) for r in rows]
            cursor = await self.conn.execute("""
                SELECT * FROM signal_outcomes WHERE outcome!='PENDING' ORDER BY id DESC LIMIT ?
            """, (limit,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"get_recent_outcomes error: {e}")
            return []

    def _compute_stats(self, rows):
        total = len(rows)
        if total == 0:
            return self._empty_stats()
        wins = sum(1 for r in rows if r["outcome"] == "WIN")
        losses = sum(1 for r in rows if r["outcome"] == "LOSS")
        timeouts = sum(1 for r in rows if r["outcome"] == "TIMEOUT")
        be = sum(1 for r in rows if r["outcome"] == "BREAKEVEN")
        rv = [float(r["r_multiple"]) for r in rows if r["r_multiple"] is not None]
        avg_r = sum(rv) / len(rv) if rv else 0.0
        gp = sum(v for v in rv if v > 0)
        gl = abs(sum(v for v in rv if v < 0))
        pf = (gp / gl) if gl > 0 else 999.0
        return {
            "total": total, "wins": wins, "losses": losses,
            "timeouts": timeouts, "breakeven": be,
            "win_rate": round(wins / total * 100, 2) if total > 0 else 0.0,
            "avg_r": round(avg_r, 4),
            "profit_factor": round(pf, 3),
        }

    def _empty_stats(self):
        return {"total": 0, "wins": 0, "losses": 0, "timeouts": 0,
                "breakeven": 0, "win_rate": 0.0, "avg_r": 0.0, "profit_factor": 0.0}

    # ============================================================
    # Near-Miss
    # ============================================================
    async def log_near_miss(self, symbol, reason, details):
        try:
            d = json.dumps(details) if not isinstance(details, str) else details
            if self.use_postgres:
                await self._pg_execute(
                    "INSERT INTO near_miss (symbol, reason, details, timestamp) VALUES ($1,$2,$3,$4)",
                    symbol, reason, d, _utcnow())
            else:
                await self.conn.execute(
                    "INSERT INTO near_miss (symbol, reason, details, timestamp) VALUES (?,?,?,?)",
                    (symbol, reason, d, _utcnow().isoformat()))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"log_near_miss error: {e}")

    # ============================================================
    # Bot State
    # ============================================================
    async def get_bot_state(self, key):
        try:
            if self.use_postgres:
                row = await self._pg_fetchrow("SELECT value FROM bot_state WHERE key=$1", key)
                return row["value"] if row else None
            cursor = await self.conn.execute("SELECT value FROM bot_state WHERE key=?", (key,))
            row = await cursor.fetchone()
            return row["value"] if row else None
        except Exception as e:
            logger.error(f"get_bot_state error: {e}")
            return None

    async def set_bot_state(self, key, value):
        try:
            if self.use_postgres:
                await self._pg_execute("""
                    INSERT INTO bot_state (key, value, updated_at) VALUES ($1,$2,$3)
                    ON CONFLICT (key) DO UPDATE SET value=$2, updated_at=$3
                """, key, value, _utcnow())
            else:
                await self.conn.execute("""
                    INSERT INTO bot_state (key, value, updated_at) VALUES (?,?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """, (key, value, _utcnow().isoformat()))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"set_bot_state error: {e}")

    # ============================================================
    # Pre-watch
    # ============================================================
    async def add_prewatch(self, symbol, reason, price_change, quote_volume, trades, price):
        try:
            if self.use_postgres:
                await self._pg_execute("""
                    INSERT INTO pre_watch (symbol, reason, price_change, quote_volume, trades, price, added_at, last_seen)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$7)
                    ON CONFLICT (symbol) DO UPDATE SET
                    reason=$2, price_change=$3, quote_volume=$4, trades=$5, price=$6, last_seen=$7
                """, symbol, reason, price_change, quote_volume, trades, price, _utcnow())
            else:
                now_iso = _utcnow().isoformat()
                await self.conn.execute("""
                    INSERT INTO pre_watch (symbol, reason, price_change, quote_volume, trades, price, added_at, last_seen)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(symbol) DO UPDATE SET
                    reason=excluded.reason, price_change=excluded.price_change,
                    quote_volume=excluded.quote_volume, trades=excluded.trades,
                    price=excluded.price, last_seen=excluded.last_seen
                """, (symbol, reason, price_change, quote_volume, trades, price, now_iso, now_iso))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"add_prewatch error: {e}")

    async def get_prewatch(self, limit=30):
        try:
            if self.use_postgres:
                rows = await self._pg_fetch("SELECT * FROM pre_watch ORDER BY added_at DESC LIMIT $1", limit)
                return [dict(r) for r in rows]
            cursor = await self.conn.execute("SELECT * FROM pre_watch ORDER BY added_at DESC LIMIT ?", (limit,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"get_prewatch error: {e}")
            return []

    # ============================================================
    # Weights
    # ============================================================
    async def get_weight(self, factor):
        try:
            if self.use_postgres:
                row = await self._pg_fetchrow("SELECT weight FROM adaptive_weights WHERE factor=$1", factor)
                return float(row["weight"]) if row else None
            cursor = await self.conn.execute("SELECT weight FROM adaptive_weights WHERE factor=?", (factor,))
            row = await cursor.fetchone()
            return float(row["weight"]) if row else None
        except Exception as e:
            logger.error(f"get_weight error: {e}")
            return None

    async def save_weight(self, factor, weight):
        try:
            if self.use_postgres:
                await self._pg_execute("""
                    INSERT INTO adaptive_weights (factor, weight, updated_at) VALUES ($1,$2,$3)
                    ON CONFLICT (factor) DO UPDATE SET weight=$2, updated_at=$3
                """, factor, float(weight), _utcnow())
            else:
                await self.conn.execute("""
                    INSERT INTO adaptive_weights (factor, weight, updated_at) VALUES (?,?,?)
                    ON CONFLICT(factor) DO UPDATE SET weight=excluded.weight, updated_at=excluded.updated_at
                """, (factor, float(weight), _utcnow().isoformat()))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"save_weight error: {e}")

    async def get_weights(self):
        try:
            if self.use_postgres:
                rows = await self._pg_fetch("SELECT factor, weight FROM adaptive_weights")
                return {r["factor"]: float(r["weight"]) for r in rows}
            cursor = await self.conn.execute("SELECT factor, weight FROM adaptive_weights")
            rows = await cursor.fetchall()
            return {r["factor"]: float(r["weight"]) for r in rows}
        except Exception as e:
            logger.error(f"get_weights error: {e}")
            return {}

    # ============================================================
    # Daily Stats
    # ============================================================
    async def get_daily_pnl(self):
        try:
            today = _utcnow().date()
            if self.use_postgres:
                row = await self._pg_fetchrow("""
                    SELECT pnl, wins, losses, breakeven, inconclusive, timeout
                    FROM daily_stats WHERE date=$1
                """, today)
                if row:
                    return {"pnl": float(row["pnl"]), "wins": row["wins"],
                            "losses": row["losses"], "breakeven": row["breakeven"],
                            "inconclusive": row["inconclusive"], "timeout": row["timeout"]}
            else:
                cursor = await self.conn.execute("""
                    SELECT pnl, wins, losses, breakeven, inconclusive, timeout
                    FROM daily_stats WHERE date=?
                """, (today.isoformat(),))
                row = await cursor.fetchone()
                if row:
                    return {"pnl": float(row["pnl"]), "wins": row["wins"],
                            "losses": row["losses"], "breakeven": row["breakeven"],
                            "inconclusive": row["inconclusive"], "timeout": row["timeout"]}
            return {"pnl": 0.0, "wins": 0, "losses": 0, "breakeven": 0,
                    "inconclusive": 0, "timeout": 0}
        except Exception as e:
            logger.error(f"get_daily_pnl error: {e}")
            return {"pnl": 0.0, "wins": 0, "losses": 0, "breakeven": 0,
                    "inconclusive": 0, "timeout": 0}

    async def add_daily_result(self, capital, pnl, outcome):
        col_map = {"win": "wins", "loss": "losses", "breakeven": "breakeven",
                   "inconclusive": "inconclusive", "timeout": "timeout"}
        col = col_map.get(outcome)
        if not col:
            return
        try:
            today = _utcnow().date()
            if self.use_postgres:
                await self._pg_execute(f"""
                    INSERT INTO daily_stats (date, capital, pnl, signals, {col})
                    VALUES ($1,$2,$3,1,1) ON CONFLICT (date) DO UPDATE SET
                    pnl=daily_stats.pnl+$3, signals=daily_stats.signals+1,
                    {col}=daily_stats.{col}+1
                """, today, capital, pnl)
            else:
                await self.conn.execute(f"""
                    INSERT INTO daily_stats (date, capital, pnl, signals, {col})
                    VALUES (?,?,?,1,1) ON CONFLICT(date) DO UPDATE SET
                    pnl=pnl+excluded.pnl, signals=signals+1, {col}={col}+1
                """, (today.isoformat(), capital, pnl))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"add_daily_result error: {e}")

    async def reset_daily(self, capital):
        try:
            today = _utcnow().date()
            if self.use_postgres:
                await self._pg_execute("DELETE FROM daily_stats WHERE date=$1", today)
            else:
                await self.conn.execute("DELETE FROM daily_stats WHERE date=?", (today.isoformat(),))
                await self.conn.commit()
        except Exception as e:
            logger.error(f"reset_daily error: {e}")
