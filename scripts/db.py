"""SQLite schema and connection helpers for local training analysis."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from paths import DB_PATH, ensure_data_dirs

SCHEMA_VERSION = "1"

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
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    ensure_data_dirs()
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    own = conn is None
    conn = conn or connect()
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
