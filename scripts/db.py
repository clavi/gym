"""SQLite schema and connection helpers for local training analysis."""

from __future__ import annotations

import sqlite3
from pathlib import Path

if __package__:
    from .paths import DB_PATH, ensure_data_dirs
else:
    from paths import DB_PATH, ensure_data_dirs

SCHEMA_VERSION = "2"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_session (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    datestr TEXT NOT NULL,
    localid INTEGER,
    title TEXT,
    start_ms INTEGER,
    end_ms INTEGER,
    duration_s INTEGER,
    UNIQUE (datestr, localid)
);

CREATE TABLE IF NOT EXISTS training_set (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    movement TEXT NOT NULL,
    set_index INTEGER NOT NULL,
    set_type TEXT,
    weight_kg REAL,
    reps REAL,
    rpe REAL,
    done INTEGER,
    rest_seconds REAL,
    is_warmup INTEGER DEFAULT 0,
    parent_movement TEXT,
    FOREIGN KEY (session_id) REFERENCES training_session(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cardio_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    movement TEXT NOT NULL,
    distance REAL,
    kcal REAL,
    workout_time_s REAL,
    avg_hr REAL,
    max_hr REAL,
    min_hr REAL,
    pace TEXT,
    FOREIGN KEY (session_id) REFERENCES training_session(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS heart_rate_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    scope TEXT NOT NULL,
    movement TEXT,
    set_index INTEGER,
    avg REAL,
    max REAL,
    min REAL,
    duration REAL,
    values_json TEXT,
    FOREIGN KEY (session_id) REFERENCES training_session(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_session_datestr ON training_session(datestr);
CREATE INDEX IF NOT EXISTS idx_set_movement ON training_set(movement);
CREATE INDEX IF NOT EXISTS idx_set_session ON training_set(session_id);

CREATE TABLE IF NOT EXISTS access_cache (
    cache_key TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    query_json TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS food_day (
    datestr TEXT PRIMARY KEY,
    template_id TEXT,
    calories REAL,
    protein REAL,
    carb REAL,
    fat REAL,
    water REAL,
    protein_limit REAL,
    carb_limit REAL,
    fat_limit REAL,
    data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS food_record (
    datestr TEXT NOT NULL REFERENCES food_day(datestr) ON DELETE CASCADE,
    record_index INTEGER NOT NULL,
    record_id TEXT,
    meal_type TEXT,
    name TEXT NOT NULL,
    amount REAL,
    unit TEXT,
    data_json TEXT NOT NULL,
    PRIMARY KEY (datestr, record_index)
);
CREATE INDEX IF NOT EXISTS idx_food_name ON food_record(name);
CREATE TABLE IF NOT EXISTS body_record (
    datestr TEXT NOT NULL,
    type TEXT NOT NULL,
    value REAL,
    unit TEXT,
    data_json TEXT NOT NULL,
    PRIMARY KEY (datestr, type)
);
CREATE TABLE IF NOT EXISTS agent_template (
    template_id TEXT PRIMARY KEY,
    deleted INTEGER NOT NULL DEFAULT 0,
    data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS access_state (
    name TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    if db_path is None:
        ensure_data_dirs()
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def connect_readonly(db_path: Path | None = None) -> sqlite3.Connection:
    """Open an existing database without initialization or filesystem writes."""
    path = (db_path or DB_PATH).resolve()
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    own = conn is None
    conn = conn or connect()
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
        version = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if version and (not str(version[0]).isdigit() or int(version[0]) > int(SCHEMA_VERSION)):
            if own:
                conn.close()
            raise ValueError("Unsupported database schema version; no downgrade was attempted")
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("schema_version", SCHEMA_VERSION),
    )
    conn.commit()
    if own:
        return conn
    return conn


def delete_sessions_for_date(conn: sqlite3.Connection, datestr: str) -> None:
    rows = conn.execute(
        "SELECT id FROM training_session WHERE datestr = ?", (datestr,)
    ).fetchall()
    ids = [r["id"] for r in rows]
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    for table in ("training_set", "cardio_record", "heart_rate_summary"):
        conn.execute(f"DELETE FROM {table} WHERE session_id IN ({placeholders})", ids)
    conn.execute(f"DELETE FROM training_session WHERE id IN ({placeholders})", ids)
