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
    (UPLOAD_DIR / "detection_jobs").mkdir(parents=True, exist_ok=True)

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
              created_at TEXT NOT NULL,
              image_hash TEXT,
              updated_at TEXT,
              deleted_at TEXT
            );

            CREATE TABLE IF NOT EXISTS face_sync_changes (
              version INTEGER PRIMARY KEY AUTOINCREMENT,
              change_type TEXT NOT NULL,
              face_sample_id TEXT NOT NULL,
              person_id TEXT NOT NULL,
              member_name TEXT NOT NULL,
              role TEXT NOT NULL,
              authorized INTEGER NOT NULL,
              file_url TEXT,
              image_hash TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS face_library_sync_state (
              device_id TEXT PRIMARY KEY,
              synced_version INTEGER NOT NULL DEFAULT 0,
              synced_at TEXT NOT NULL,
              message TEXT
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

            CREATE TABLE IF NOT EXISTS detection_jobs (
              id TEXT PRIMARY KEY,
              device_id TEXT NOT NULL,
              status TEXT NOT NULL,
              expected_fire_count INTEGER NOT NULL DEFAULT 0,
              expected_drone_count INTEGER NOT NULL DEFAULT 0,
              total_count INTEGER NOT NULL DEFAULT 0,
              completed_count INTEGER NOT NULL DEFAULT 0,
              failed_count INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS detection_job_items (
              id TEXT PRIMARY KEY,
              job_id TEXT NOT NULL,
              expected_label TEXT NOT NULL,
              filename TEXT NOT NULL,
              file_url TEXT NOT NULL,
              status TEXT NOT NULL,
              yolo_labels_json TEXT NOT NULL DEFAULT '[]',
              error_message TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
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

        face_sample_columns = {row["name"] for row in conn.execute("PRAGMA table_info(face_samples)").fetchall()}
        if "image_hash" not in face_sample_columns:
            conn.execute("ALTER TABLE face_samples ADD COLUMN image_hash TEXT")
        if "updated_at" not in face_sample_columns:
            conn.execute("ALTER TABLE face_samples ADD COLUMN updated_at TEXT")
        if "deleted_at" not in face_sample_columns:
            conn.execute("ALTER TABLE face_samples ADD COLUMN deleted_at TEXT")
        conn.execute("UPDATE face_samples SET updated_at = created_at WHERE updated_at IS NULL")

        has_face_changes = conn.execute("SELECT 1 FROM face_sync_changes LIMIT 1").fetchone()
        if not has_face_changes:
            rows = conn.execute(
                """
                SELECT fs.*, p.name, p.role, p.authorized
                FROM face_samples fs
                JOIN persons p ON p.id = fs.person_id
                WHERE fs.deleted_at IS NULL
                ORDER BY fs.created_at ASC, fs.rowid ASC
                """
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO face_sync_changes (
                      change_type, face_sample_id, person_id, member_name, role, authorized,
                      file_url, image_hash, created_at
                    )
                    VALUES ('upsert', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        row["person_id"],
                        row["name"],
                        row["role"],
                        row["authorized"],
                        row["file_url"],
                        row["image_hash"],
                        row["updated_at"] or row["created_at"],
                    ),
                )
        conn.execute(
            """
            INSERT INTO devices (id, name, type, online, last_seen)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (DEFAULT_DEVICE_ID, DEFAULT_DEVICE_NAME, DEFAULT_DEVICE_TYPE, now_iso()),
        )
