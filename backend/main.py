from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
import json
import shutil
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import APP_VERSION, BASE_URL, DEFAULT_DEVICE_ID, UPLOAD_DIR
from .database import connect, init_db, json_dumps, now_iso
from .schemas import CommandAck, CommandCreate, PersonCreate, PersonPatch, TelemetryInput


def ok(data: Any = None) -> dict[str, Any]:
    return {"ok": True, "data": data if data is not None else {}, "error": None}


def fail(code: str, message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "data": None, "error": {"code": code, "message": message}},
    )


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def to_bool(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def bool_to_db(value: bool | None) -> int | None:
    return None if value is None else int(value)


def parse_json(value: str, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {exc.msg}") from exc


def validate_yolo_labels(raw: str) -> list[dict[str, Any]]:
    labels = parse_json(raw, [])
    if not isinstance(labels, list):
        raise HTTPException(status_code=400, detail="yolo_labels_json must be a JSON array")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(labels):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail=f"yolo_labels_json[{index}] must be an object")
        label = item.get("label")
        confidence = item.get("confidence")
        if not isinstance(label, str) or not label:
            raise HTTPException(status_code=400, detail=f"yolo_labels_json[{index}].label is required")
        if not isinstance(confidence, int | float):
            raise HTTPException(status_code=400, detail=f"yolo_labels_json[{index}].confidence is required")
        if not 0 <= float(confidence) <= 1:
            raise HTTPException(status_code=400, detail=f"yolo_labels_json[{index}].confidence must be 0-1")
        normalized.append({"label": label, "confidence": float(confidence)})
    return normalized


def save_upload(file: UploadFile, directory: Path, prefix: str) -> str:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}_{uuid4().hex}{suffix}"
    target = directory / filename
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return f"/uploads/{directory.name}/{filename}"


def telemetry_from_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "device_id": row["device_id"],
        "captured_at": row["captured_at"],
        "temperature_c": row["temperature_c"],
        "door_open": to_bool(row["door_open"]),
        "window_open": to_bool(row["window_open"]),
        "light_level": row["light_level"],
        "fan_on": to_bool(row["fan_on"]),
    }


def person_from_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "role": row["role"],
        "authorized": bool(row["authorized"]),
        "face_enrolled": bool(row["face_enrolled"]),
    }


def photo_from_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "device_id": row["device_id"],
        "captured_at": row["captured_at"],
        "file_url": row["file_url"],
        "yolo_labels": parse_json(row["yolo_labels_json"], []),
        "face_result": parse_json(row["face_result_json"], {}),
        "access_decision": row["access_decision"],
    }


def command_from_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "device_id": row["device_id"],
        "type": row["type"],
        "payload": parse_json(row["payload_json"], {}),
        "status": row["status"],
        "created_at": row["created_at"],
        "executed_at": row["executed_at"],
    }


def event_from_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "type": row["type"],
        "severity": row["severity"],
        "message": row["message"],
        "created_at": row["created_at"],
    }


def add_event(conn: Any, event_type: str, message: str, severity: str = "info") -> None:
    conn.execute(
        "INSERT INTO events (id, type, severity, message, created_at) VALUES (?, ?, ?, ?, ?)",
        (make_id("evt"), event_type, severity, message, now_iso()),
    )


def touch_device(conn: Any, device_id: str) -> None:
    conn.execute(
        """
        INSERT INTO devices (id, name, type, online, last_seen)
        VALUES (?, ?, 'gateway', 1, ?)
        ON CONFLICT(id) DO UPDATE SET online = 1, last_seen = excluded.last_seen
        """,
        (device_id, f"Device {device_id}", now_iso()),
    )


def match_face(conn: Any, labels: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    has_person = any(item["label"].lower() == "person" for item in labels)
    if not has_person:
        return {"matched_person_id": None, "matched_name": None, "confidence": 0}, "unknown"

    row = conn.execute(
        """
        SELECT * FROM persons
        WHERE authorized = 1 AND face_enrolled = 1
        ORDER BY id ASC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return {"matched_person_id": None, "matched_name": None, "confidence": 0}, "unknown"

    return {
        "matched_person_id": row["id"],
        "matched_name": row["name"],
        "confidence": 0.86,
    }, "allow"


def create_command(conn: Any, device_id: str, command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    command_id = make_id("cmd")
    created_at = now_iso()
    conn.execute(
        """
        INSERT INTO commands (id, device_id, type, payload_json, status, created_at)
        VALUES (?, ?, ?, ?, 'pending', ?)
        """,
        (command_id, device_id, command_type, json_dumps(payload), created_at),
    )
    row = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
    add_event(conn, "command_created", f"{command_type} queued for {device_id}")
    return command_from_row(row)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Group 27 Smart Home Software API", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR, check_dir=False), name="uploads")


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else json_dumps(exc.detail)
    return fail("REQUEST_ERROR", detail, exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return fail("INVALID_REQUEST", str(exc), 422)


@app.get("/")
def root() -> dict[str, Any]:
    return ok({"name": "Group 27 Smart Home API", "version": APP_VERSION, "base_url": BASE_URL})


@app.get("/api/health")
def health() -> dict[str, Any]:
    return ok({"status": "ok", "version": APP_VERSION})


@app.get("/api/devices")
def list_devices() -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM devices ORDER BY id ASC").fetchall()
    return ok(
        [
            {
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "online": bool(row["online"]),
                "last_seen": row["last_seen"],
            }
            for row in rows
        ]
    )


@app.get("/api/status/latest")
def latest_status() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM telemetry ORDER BY captured_at DESC, rowid DESC LIMIT 1").fetchone()
    return ok(telemetry_from_row(row) if row else {})


@app.get("/api/telemetry")
def query_telemetry(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    sql = "SELECT * FROM telemetry WHERE 1 = 1"
    params: list[Any] = []
    if from_:
        sql += " AND captured_at >= ?"
        params.append(from_)
    if to:
        sql += " AND captured_at <= ?"
        params.append(to)
    sql += " ORDER BY captured_at DESC, rowid DESC LIMIT ?"
    params.append(limit)

    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return ok([telemetry_from_row(row) for row in rows])


@app.get("/api/persons")
def list_persons() -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM persons ORDER BY id ASC").fetchall()
    return ok([person_from_row(row) for row in rows])


@app.post("/api/persons")
def create_person(payload: PersonCreate) -> dict[str, Any]:
    person_id = make_id("person")
    with connect() as conn:
        conn.execute(
            "INSERT INTO persons (id, name, role, authorized, face_enrolled) VALUES (?, ?, ?, ?, 0)",
            (person_id, payload.name, payload.role, int(payload.authorized)),
        )
        add_event(conn, "person_created", f"person {payload.name} created")
        row = conn.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
    return ok(person_from_row(row))


@app.patch("/api/persons/{person_id}")
def update_person(person_id: str, payload: PersonPatch) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")

    assignments: list[str] = []
    params: list[Any] = []
    for key, value in updates.items():
        assignments.append(f"{key} = ?")
        params.append(int(value) if key == "authorized" else value)
    params.append(person_id)

    with connect() as conn:
        cur = conn.execute(f"UPDATE persons SET {', '.join(assignments)} WHERE id = ?", params)
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="person not found")
        add_event(conn, "person_updated", f"person {person_id} updated")
        row = conn.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
    return ok(person_from_row(row))


@app.delete("/api/persons/{person_id}")
def delete_person(person_id: str) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.execute("DELETE FROM persons WHERE id = ?", (person_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="person not found")
        add_event(conn, "person_deleted", f"person {person_id} deleted")
    return ok({"deleted": True})


@app.post("/api/persons/{person_id}/face-samples")
def upload_face_sample(person_id: str, image: UploadFile = File(...)) -> dict[str, Any]:
    with connect() as conn:
        person = conn.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
        if not person:
            raise HTTPException(status_code=404, detail="person not found")

        file_url = save_upload(image, UPLOAD_DIR / "faces", "face")
        conn.execute(
            "INSERT INTO face_samples (id, person_id, file_url, created_at) VALUES (?, ?, ?, ?)",
            (make_id("face"), person_id, file_url, now_iso()),
        )
        conn.execute("UPDATE persons SET face_enrolled = 1 WHERE id = ?", (person_id,))
        add_event(conn, "face_enrolled", f"face sample uploaded for {person_id}")
    return ok({"person_id": person_id, "face_enrolled": True})


@app.get("/api/photos")
def list_photos(limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM photos ORDER BY captured_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
    return ok([photo_from_row(row) for row in rows])


@app.post("/api/commands")
def post_command(payload: CommandCreate) -> dict[str, Any]:
    with connect() as conn:
        touch_device(conn, payload.device_id)
        command = create_command(conn, payload.device_id, payload.type, payload.payload)
    return ok(command)


@app.get("/api/commands")
def list_commands(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM commands ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
    return ok([command_from_row(row) for row in rows])


@app.get("/api/events")
def list_events(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
    return ok([event_from_row(row) for row in rows])


@app.post("/api/device/telemetry")
def upload_telemetry(payload: TelemetryInput) -> dict[str, Any]:
    telemetry_id = make_id("tel")
    captured_at = payload.captured_at or now_iso()
    with connect() as conn:
        touch_device(conn, payload.device_id)
        conn.execute(
            """
            INSERT INTO telemetry (
              id, device_id, captured_at, temperature_c, door_open, window_open, light_level, fan_on
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telemetry_id,
                payload.device_id,
                captured_at,
                payload.temperature_c,
                bool_to_db(payload.door_open),
                bool_to_db(payload.window_open),
                payload.light_level,
                bool_to_db(payload.fan_on),
            ),
        )
        add_event(conn, "telemetry_saved", f"telemetry saved for {payload.device_id}")
        if payload.temperature_c >= 30:
            add_event(conn, "temperature_high", f"temperature is {payload.temperature_c}C", "warning")
    return ok({"saved": True, "id": telemetry_id})


@app.post("/api/device/photos")
def upload_photo(
    device_id: str = Form(...),
    image: UploadFile = File(...),
    yolo_labels_json: str = Form(...),
    captured_at: str | None = Form(default=None),
) -> dict[str, Any]:
    labels = validate_yolo_labels(yolo_labels_json)
    photo_id = make_id("photo")
    captured_at = captured_at or now_iso()
    file_url = save_upload(image, UPLOAD_DIR / "photos", photo_id)

    with connect() as conn:
        touch_device(conn, device_id)
        face_result, access_decision = match_face(conn, labels)
        conn.execute(
            """
            INSERT INTO photos (
              id, device_id, captured_at, file_url, yolo_labels_json, face_result_json, access_decision
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (photo_id, device_id, captured_at, file_url, json_dumps(labels), json_dumps(face_result), access_decision),
        )
        add_event(conn, "photo_saved", f"photo uploaded from {device_id}")

        label_names = {item["label"].lower() for item in labels}
        if access_decision == "allow":
            create_command(conn, device_id, "OPEN_DOOR", {})
        if "light bulb" in label_names:
            create_command(conn, device_id, "SET_LIGHT", {"level": 80})

        row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    return ok(photo_from_row(row))


@app.get("/api/device/commands/pending")
def pending_commands(device_id: str = Query(...)) -> dict[str, Any]:
    with connect() as conn:
        touch_device(conn, device_id)
        rows = conn.execute(
            """
            SELECT * FROM commands
            WHERE device_id = ? AND status = 'pending'
            ORDER BY created_at ASC, rowid ASC
            """,
            (device_id,),
        ).fetchall()
        command_ids = [row["id"] for row in rows]
        if command_ids:
            conn.executemany("UPDATE commands SET status = 'sent' WHERE id = ?", [(command_id,) for command_id in command_ids])
            add_event(conn, "commands_pulled", f"{len(command_ids)} command(s) pulled by {device_id}")
    return ok([command_from_row(row) for row in rows])


@app.post("/api/device/commands/{command_id}/ack")
def ack_command(command_id: str, payload: CommandAck) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="command not found")
        if row["device_id"] != payload.device_id:
            raise HTTPException(status_code=400, detail="device_id does not match command")

        conn.execute(
            """
            UPDATE commands
            SET status = ?, executed_at = ?, message = ?
            WHERE id = ?
            """,
            (payload.status, now_iso(), payload.message, command_id),
        )
        add_event(conn, f"command_{payload.status}", f"{command_id} {payload.status}: {payload.message or ''}".strip())
    return ok({"updated": True})
