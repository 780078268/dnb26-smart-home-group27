from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Iterator

from .config import (
    DATABASE_PATH,
    DATA_DIR,
    DEFAULT_DEVICE_ID,
    DEFAULT_DEVICE_NAME,
    DEFAULT_DEVICE_TYPE,
    UPLOAD_DIR,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / "photos").mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / "latest").mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / "faces").mkdir(parents=True, exist_ok=True)

    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS devices (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              type TEXT NOT NULL,
              online INTEGER NOT NULL DEFAULT 1,
              last_seen TEXT
            );

            CREATE TABLE IF NOT EXISTS telemetry (
              id TEXT PRIMARY KEY,
              device_id TEXT NOT NULL,
              captured_at TEXT NOT NULL,
              temperature_c REAL NOT NULL,
              door_open INTEGER,
              window_open INTEGER,
              light_level INTEGER,
              fan_on INTEGER
            );

            CREATE TABLE IF NOT EXISTS persons (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              role TEXT NOT NULL DEFAULT 'student',
              authorized INTEGER NOT NULL DEFAULT 1,
              face_enrolled INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS face_samples (
              id TEXT PRIMARY KEY,
              person_id TEXT NOT NULL,
              file_url TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS photos (
              id TEXT PRIMARY KEY,
              device_id TEXT NOT NULL,
              captured_at TEXT NOT NULL,
              file_url TEXT NOT NULL,
              yolo_labels_json TEXT NOT NULL,
              face_result_json TEXT NOT NULL,
              access_decision TEXT NOT NULL,
              source TEXT NOT NULL DEFAULT 'auto_face',
              event_key TEXT
            );

            CREATE TABLE IF NOT EXISTS latest_results (
              device_id TEXT PRIMARY KEY,
              captured_at TEXT NOT NULL,
              file_url TEXT NOT NULL,
              yolo_labels_json TEXT NOT NULL,
              face_result_json TEXT NOT NULL,
              access_decision TEXT NOT NULL,
              source TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS event_cooldowns (
              event_key TEXT PRIMARY KEY,
              last_seen_epoch REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS commands (
              id TEXT PRIMARY KEY,
              device_id TEXT NOT NULL,
              type TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              executed_at TEXT,
              message TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
              id TEXT PRIMARY KEY,
              type TEXT NOT NULL,
              severity TEXT NOT NULL,
              message TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(photos)").fetchall()}
        if "source" not in columns:
            conn.execute("ALTER TABLE photos ADD COLUMN source TEXT NOT NULL DEFAULT 'auto_face'")
        if "event_key" not in columns:
            conn.execute("ALTER TABLE photos ADD COLUMN event_key TEXT")
        conn.execute(
            """
            INSERT INTO devices (id, name, type, online, last_seen)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (DEFAULT_DEVICE_ID, DEFAULT_DEVICE_NAME, DEFAULT_DEVICE_TYPE, now_iso()),
        )
