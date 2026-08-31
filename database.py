"""
database.py - إدارة قاعدة البيانات SQLite.
"""
import sqlite3
import json
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from contextlib import contextmanager
from config import CFG

class Database:
    def __init__(self, db_path: str = CFG.DB_PATH):
        self.db_path = db_path
        self._init_tables()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_tables(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    open_time TIMESTAMP NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    UNIQUE(symbol, timeframe, open_time)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    entry_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    position_size REAL,
                    confidence REAL,
                    score REAL,
                    reasons TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'PENDING'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS last_signal (
                    symbol TEXT PRIMARY KEY,
                    signal_type TEXT,
                    price REAL,
                    action TEXT,
                    direction TEXT,
                    timestamp TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY,
                    total_signals INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    pnl REAL DEFAULT 0.0,
                    capital REAL DEFAULT 0.0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scan_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS adaptive_weights (
                    factor TEXT PRIMARY KEY,
                    weight REAL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pre_watch (
                    symbol TEXT PRIMARY KEY,
                    score REAL DEFAULT 0.0,
                    change_24h REAL DEFAULT 0.0,
                    volume_24h REAL DEFAULT 0.0,
                    status TEXT DEFAULT 'ACTIVE',
                    reason TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf ON candles(symbol, timeframe, open_time DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol, created_at DESC)
            """)

    def save_candles(self, symbol: str, timeframe: str, df: pd.DataFrame):
        with self._connect() as conn:
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            conn.execute(
                "DELETE FROM candles WHERE symbol=? AND timeframe=? AND open_time < ?",
                (symbol, timeframe, cutoff)
            )
            records = []
            for _, row in df.iterrows():
                records.append((
                    symbol,
                    timeframe,
                    row["open_time"].to_pydatetime() if hasattr(row["open_time"], "to_pydatetime") else row["open_time"],
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row["volume"])
                ))
            conn.executemany("""
                INSERT OR REPLACE INTO candles (symbol, timeframe, open_time, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, records)

    def get_candles(self, symbol: str, timeframe: str, limit: int = 250) -> pd.DataFrame:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT open_time, open, high, low, close, volume
                FROM candles
                WHERE symbol=? AND timeframe=?
                ORDER BY open_time DESC
                LIMIT ?
            """, (symbol, timeframe, limit)).fetchall()
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
            df = df.sort_values("open_time").reset_index(drop=True)
            return df

    def save_signal(self, signal: Dict) -> int:
        with self._connect() as conn:
            cursor = conn.execute("""
                INSERT INTO signals (symbol, timeframe, signal_type, entry_price, stop_loss, take_profit,
                                     position_size, confidence, score, reasons)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal["symbol"],
                signal["timeframe"],
                signal["type"],
                signal["entry_price"],
                signal["stop_loss"],
                signal["take_profit"],
                signal["position_size"],
                signal["confidence"],
                signal["score"],
                json.dumps(signal.get("reasons", []), ensure_ascii=False)
            ))
            return cursor.lastrowid

    def get_last_signal(self, symbol: str) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT signal_type, price, action, direction, timestamp FROM last_signal WHERE symbol=?",
                (symbol,)
            ).fetchone()
            if row:
                return {
                    "signal_type": row[0],
                    "price": row[1],
                    "action": row[2],
                    "direction": row[3],
                    "timestamp": row[4]
                }
            return None

    def set_last_signal(self, symbol: str, signal_type: str, price: float, action: str, direction: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO last_signal (symbol, signal_type, price, action, direction, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (symbol, signal_type, price, action, direction, now)
            )

    def get_last_signal_time(self, symbol: str, minutes: int = 45) -> Optional[datetime]:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT created_at FROM signals
                WHERE symbol=? AND created_at > datetime('now', '-{} minutes')
                ORDER BY created_at DESC LIMIT 1
            """.format(minutes), (symbol,)).fetchone()
            return datetime.fromisoformat(row["created_at"]) if row else None

    def get_daily_stats(self, date: Optional[str] = None) -> Dict:
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM daily_stats WHERE date=?", (date,)).fetchone()
            if row:
                return dict(row)
            return {"date": date, "total_signals": 0, "wins": 0, "losses": 0, "pnl": 0.0, "capital": CFG.VIRTUAL_CAPITAL}

    def update_daily_stats(self, pnl: float, is_win: bool):
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO daily_stats (date, total_signals, wins, losses, pnl, capital)
                VALUES (?, 1, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    total_signals = total_signals + 1,
                    wins = wins + excluded.wins,
                    losses = losses + excluded.losses,
                    pnl = pnl + excluded.pnl,
                    capital = capital + excluded.pnl
            """, (
                date,
                1 if is_win else 0,
                0 if is_win else 1,
                pnl,
                CFG.VIRTUAL_CAPITAL + pnl
            ))

    def reset_daily_stats(self):
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._connect() as conn:
            conn.execute("DELETE FROM daily_stats WHERE date=?", (date,))

    def get_active_prewatch(self, limit: int = 30) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT symbol FROM pre_watch WHERE status = 'ACTIVE' ORDER BY score DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [r["symbol"] for r in rows]

    def add_to_prewatch(self, symbol: str, score: float = 1.0, change_24h: float = 0.0,
                        volume_24h: float = 0.0, reason: str = ""):
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO pre_watch (symbol, score, change_24h, volume_24h, reason, status, updated_at)
                VALUES (?, ?, ?, ?, ?, 'ACTIVE', CURRENT_TIMESTAMP)
                ON CONFLICT(symbol) DO UPDATE SET
                    score = excluded.score,
                    change_24h = excluded.change_24h,
                    volume_24h = excluded.volume_24h,
                    reason = excluded.reason,
                    updated_at = CURRENT_TIMESTAMP
            """, (symbol, score, change_24h, volume_24h, reason))

    def get_scan_state(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM scan_state WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None

    def set_scan_state(self, key: str, value: str):
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO scan_state (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
            """, (key, value))

    def get_adaptive_weights(self) -> Optional[Dict[str, float]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT factor, weight FROM adaptive_weights").fetchall()
            if not rows:
                return None
            return {r["factor"]: r["weight"] for r in rows}

    def save_adaptive_weights(self, weights: Dict[str, float]):
        with self._connect() as conn:
            for factor, weight in weights.items():
                conn.execute("""
                    INSERT INTO adaptive_weights (factor, weight)
                    VALUES (?, ?)
                    ON CONFLICT(factor) DO UPDATE SET weight=excluded.weight
                """, (factor, weight))

    def get_signals_for_backtest(self, days: int = 30) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM signals
                WHERE created_at > datetime('now', '-{} days') AND status = 'PENDING'
                ORDER BY created_at DESC
            """.format(days)).fetchall()
            return [dict(r) for r in rows]

    def update_signal_status(self, signal_id: int, status: str):
        with self._connect() as conn:
            conn.execute("UPDATE signals SET status=? WHERE id=?", (status, signal_id))

db = Database()
