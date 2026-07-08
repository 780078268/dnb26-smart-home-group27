from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "smart_home.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) or {} for row in rows]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS authorized_people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                face_code TEXT NOT NULL UNIQUE,
                is_authorized INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sensor_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                temperature REAL NOT NULL,
                door_open INTEGER NOT NULL,
                window_open INTEGER NOT NULL,
                light_level INTEGER NOT NULL,
                fan_on INTEGER NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS image_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                original_name TEXT NOT NULL,
                image_type TEXT NOT NULL,
                detection_json TEXT NOT NULL,
                face_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS control_commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device TEXT NOT NULL,
                action TEXT NOT NULL,
                value TEXT,
                source TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                executed_at TEXT
            );
            """
        )

        _ensure_column(conn, "sensor_events", "captured_at", "TEXT")
        _ensure_column(conn, "image_events", "captured_at", "TEXT")
        _ensure_column(conn, "control_commands", "device_id", "TEXT NOT NULL DEFAULT 'orange-pi-main'")
        conn.execute("UPDATE sensor_events SET captured_at = created_at WHERE captured_at IS NULL")
        conn.execute("UPDATE image_events SET captured_at = created_at WHERE captured_at IS NULL")

        people_count = conn.execute("SELECT COUNT(*) AS count FROM authorized_people").fetchone()["count"]
        if people_count == 0:
            conn.executemany(
                """
                INSERT INTO authorized_people (name, role, face_code, is_authorized, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    ("Alice Zhang", "Owner", "owner_alice", 1, now_iso()),
                    ("Bob Li", "Family", "family_bob", 1, now_iso()),
                    ("Visitor Chen", "Visitor", "visitor_chen", 0, now_iso()),
                ],
            )

        sensor_count = conn.execute("SELECT COUNT(*) AS count FROM sensor_events").fetchone()["count"]
        if sensor_count == 0:
            insert_sensor_event(
                temperature=26.5,
                door_open=False,
                window_open=False,
                light_level=55,
                fan_on=False,
                source="seed",
                conn=conn,
            )


def insert_sensor_event(
    *,
    temperature: float,
    door_open: bool,
    window_open: bool,
    light_level: int,
    fan_on: bool,
    source: str,
    captured_at: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns_connection = conn is None
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

    try:
        created_at = now_iso()
        captured_at = captured_at or created_at
        cursor = conn.execute(
            """
            INSERT INTO sensor_events
                (temperature, door_open, window_open, light_level, fan_on, source, captured_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                float(temperature),
                int(bool(door_open)),
                int(bool(window_open)),
                int(max(0, min(100, light_level))),
                int(bool(fan_on)),
                source,
                captured_at,
                created_at,
            ),
        )
        if owns_connection:
            conn.commit()
        return get_sensor_event(cursor.lastrowid, conn=conn)
    finally:
        if owns_connection:
            conn.close()


def get_sensor_event(event_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns_connection = conn is None
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM sensor_events WHERE id = ?", (event_id,)).fetchone()
        data = row_to_dict(row) or {}
        return normalize_sensor(data)
    finally:
        if owns_connection:
            conn.close()


def latest_sensor_state() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sensor_events ORDER BY id DESC LIMIT 1").fetchone()
        return normalize_sensor(row_to_dict(row) or {})


def normalize_sensor(data: dict[str, Any]) -> dict[str, Any]:
    if not data:
        return {
            "temperature": 0,
            "door_open": False,
            "window_open": False,
            "light_level": 0,
            "fan_on": False,
            "source": "empty",
            "captured_at": None,
            "created_at": None,
        }
    data["door_open"] = bool(data["door_open"])
    data["window_open"] = bool(data["window_open"])
    data["fan_on"] = bool(data["fan_on"])
    data["captured_at"] = data.get("captured_at") or data.get("created_at")
    return data


def list_sensor_events(
    *,
    device_id: str | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    limit = max(1, min(500, limit))
    conditions: list[str] = []
    params: list[Any] = []
    if device_id:
        conditions.append("source = ?")
        params.append(device_id)
    if from_time:
        conditions.append("captured_at >= ?")
        params.append(from_time)
    if to_time:
        conditions.append("captured_at <= ?")
        params.append(to_time)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM sensor_events {where} ORDER BY id DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [normalize_sensor(row_to_dict(row) or {}) for row in rows]


def _person_to_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    data = row_to_dict(row) if isinstance(row, sqlite3.Row) else row
    if not data:
        return None
    data["is_authorized"] = bool(data["is_authorized"])
    return data


def list_authorized_people() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM authorized_people ORDER BY id").fetchall()
        return [_person_to_dict(row) or {} for row in rows]


def _face_code_from_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "person"


def create_person(
    *,
    name: str,
    role: str = "student",
    authorized: bool = True,
    face_code: str | None = None,
) -> dict[str, Any]:
    with connect() as conn:
        base_face_code = face_code or _face_code_from_name(name)
        candidate = base_face_code
        suffix = 2
        while conn.execute("SELECT 1 FROM authorized_people WHERE face_code = ?", (candidate,)).fetchone():
            candidate = f"{base_face_code}_{suffix}"
            suffix += 1
        cursor = conn.execute(
            """
            INSERT INTO authorized_people (name, role, face_code, is_authorized, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, role, candidate, int(bool(authorized)), now_iso()),
        )
        return get_person(cursor.lastrowid, conn=conn) or {}


def get_person(person_id: int | str, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    numeric_id = normalize_person_id(person_id)
    owns_connection = conn is None
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM authorized_people WHERE id = ?", (numeric_id,)).fetchone()
        return _person_to_dict(row)
    finally:
        if owns_connection:
            conn.close()


def get_person_by_face_code(face_code: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM authorized_people WHERE face_code = ?",
            (face_code,),
        ).fetchone()
        return _person_to_dict(row)


def update_person(
    person_id: int | str,
    *,
    name: str | None = None,
    role: str | None = None,
    authorized: bool | None = None,
    face_code: str | None = None,
) -> dict[str, Any] | None:
    numeric_id = normalize_person_id(person_id)
    updates: list[str] = []
    params: list[Any] = []
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if role is not None:
        updates.append("role = ?")
        params.append(role)
    if authorized is not None:
        updates.append("is_authorized = ?")
        params.append(int(bool(authorized)))
    if face_code is not None:
        updates.append("face_code = ?")
        params.append(face_code)
    if not updates:
        return get_person(numeric_id)

    with connect() as conn:
        conn.execute(
            f"UPDATE authorized_people SET {', '.join(updates)} WHERE id = ?",
            [*params, numeric_id],
        )
        return get_person(numeric_id, conn=conn)


def delete_person(person_id: int | str) -> bool:
    numeric_id = normalize_person_id(person_id)
    with connect() as conn:
        cursor = conn.execute("DELETE FROM authorized_people WHERE id = ?", (numeric_id,))
        return cursor.rowcount > 0


def normalize_person_id(person_id: int | str) -> int:
    if isinstance(person_id, int):
        return person_id
    value = str(person_id)
    if value.startswith("person_"):
        value = value.split("_", 1)[1]
    return int(value)


def insert_image_event(
    *,
    filename: str,
    original_name: str,
    image_type: str,
    detection: dict[str, Any],
    face: dict[str, Any],
    captured_at: str | None = None,
) -> dict[str, Any]:
    with connect() as conn:
        created_at = now_iso()
        cursor = conn.execute(
            """
            INSERT INTO image_events
                (filename, original_name, image_type, detection_json, face_json, captured_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filename,
                original_name,
                image_type,
                json.dumps(detection, ensure_ascii=False),
                json.dumps(face, ensure_ascii=False),
                captured_at or created_at,
                created_at,
            ),
        )
        return get_image_event(cursor.lastrowid, conn=conn)


def get_image_event(event_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns_connection = conn is None
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM image_events WHERE id = ?", (event_id,)).fetchone()
        data = row_to_dict(row) or {}
        if data:
            data["detection"] = json.loads(data.pop("detection_json") or "{}")
            data["face"] = json.loads(data.pop("face_json") or "{}")
            data["captured_at"] = data.get("captured_at") or data.get("created_at")
        return data
    finally:
        if owns_connection:
            conn.close()


def latest_image_events(limit: int = 8) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM image_events ORDER BY id DESC LIMIT ?",
            (max(1, min(300, limit)),),
        ).fetchall()
        events = []
        for row in rows:
            item = row_to_dict(row) or {}
            item["detection"] = json.loads(item.pop("detection_json") or "{}")
            item["face"] = json.loads(item.pop("face_json") or "{}")
            item["captured_at"] = item.get("captured_at") or item.get("created_at")
            events.append(item)
        return events


def insert_command(
    *,
    device: str,
    action: str,
    value: Any = None,
    source: str = "gui",
    device_id: str = "orange-pi-main",
) -> dict[str, Any]:
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO control_commands (device_id, device, action, value, source, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (device_id, device, action, None if value is None else str(value), source, now_iso()),
        )
        return get_command(cursor.lastrowid, conn=conn)


def get_command(command_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns_connection = conn is None
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM control_commands WHERE id = ?", (command_id,)).fetchone()
        return row_to_dict(row) or {}
    finally:
        if owns_connection:
            conn.close()


def pending_commands(device_id: str | None = None) -> list[dict[str, Any]]:
    conditions = ["status = 'pending'"]
    params: list[Any] = []
    if device_id:
        conditions.append("device_id = ?")
        params.append(device_id)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM control_commands WHERE {' AND '.join(conditions)} ORDER BY id",
            params,
        ).fetchall()
        return rows_to_dicts(rows)


def list_commands(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM control_commands ORDER BY id DESC LIMIT ?",
            (max(1, min(300, limit)),),
        ).fetchall()
        return rows_to_dicts(rows)


def mark_command_executed(command_id: int, status: str = "executed") -> dict[str, Any]:
    with connect() as conn:
        conn.execute(
            """
            UPDATE control_commands
            SET status = ?, executed_at = ?
            WHERE id = ?
            """,
            (status, now_iso(), command_id),
        )
        return get_command(command_id, conn=conn)


def list_history(kind: str, limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(300, limit))
    if kind == "commands":
        return list_commands(limit)
    if kind == "images":
        return latest_image_events(limit)
    return list_sensor_events(limit=limit)
