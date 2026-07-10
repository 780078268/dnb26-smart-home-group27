from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
import hashlib
import io
import json
import shutil
import time
import zipfile
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import APP_VERSION, BASE_URL, DEFAULT_DEVICE_ID, UPLOAD_DIR
from .database import connect, init_db, json_dumps, now_iso
from .schemas import (
    CommandAck,
    CommandCreate,
    DetectionJobResultUpload,
    FaceLibraryAck,
    PersonCreate,
    PersonPatch,
    TelemetryInput,
)

EVENT_COOLDOWN_SECONDS = 5
ACTIVE_DETECTION_SOURCES = {"fire_extinguisher", "drone"}
DETECTION_LABELS = {"fire_extinguisher", "drone"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
ZIP_SUFFIXES = {".zip"}
TARGET_LABEL_ALIASES = {
    "fire_extinguisher": {"fire_extinguisher", "fire extinguisher", "extinguisher", "灭火器"},
    "drone": {"drone", "uav", "无人机"},
}


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


def normalize_yolo_labels(labels: Any, field_name: str = "yolo_labels_json") -> list[dict[str, Any]]:
    if not isinstance(labels, list):
        raise HTTPException(status_code=400, detail=f"{field_name} must be a JSON array")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(labels):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail=f"{field_name}[{index}] must be an object")
        label = item.get("label")
        confidence = item.get("confidence")
        if not isinstance(label, str) or not label:
            raise HTTPException(status_code=400, detail=f"{field_name}[{index}].label is required")
        if not isinstance(confidence, int | float):
            raise HTTPException(status_code=400, detail=f"{field_name}[{index}].confidence is required")
        if not 0 <= float(confidence) <= 1:
            raise HTTPException(status_code=400, detail=f"{field_name}[{index}].confidence must be 0-1")
        normalized.append({"label": label, "confidence": float(confidence)})
    return normalized


def validate_yolo_labels(raw: str) -> list[dict[str, Any]]:
    return normalize_yolo_labels(parse_json(raw, []))


def validate_face_result(raw: str | None) -> dict[str, Any] | None:
    if raw is None or raw == "":
        return None
    value = parse_json(raw, {})
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="face_result_json must be a JSON object")
    confidence = value.get("confidence")
    if confidence is not None:
        if not isinstance(confidence, int | float):
            raise HTTPException(status_code=400, detail="face_result_json.confidence must be numeric")
        if not 0 <= float(confidence) <= 1:
            raise HTTPException(status_code=400, detail="face_result_json.confidence must be 0-1")
        value["confidence"] = float(confidence)
    return value


def validate_access_decision(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if value not in {"allow", "deny", "unknown"}:
        raise HTTPException(status_code=400, detail="access_decision must be allow, deny, or unknown")
    return value


def normalize_source(value: str | None) -> str:
    source = (value or "auto_face").strip().lower().replace("-", "_").replace(" ", "_")
    return source or "auto_face"


def validate_upload_mode(value: str | None) -> str:
    mode = (value or "latest").strip().lower()
    if mode not in {"latest", "event"}:
        raise HTTPException(status_code=400, detail="mode must be latest or event")
    return mode


def image_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix in IMAGE_SUFFIXES else ".jpg"


def safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)


def save_upload(file: UploadFile, directory: Path, prefix: str) -> str:
    suffix = image_suffix(file.filename)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}_{uuid4().hex}{suffix}"
    target = directory / filename
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return f"/uploads/{directory.name}/{filename}"


def save_latest_upload(file: UploadFile, device_id: str) -> tuple[str, Path]:
    directory = UPLOAD_DIR / "latest"
    directory.mkdir(parents=True, exist_ok=True)
    stem = safe_name(device_id)
    for existing in directory.glob(f"{stem}.*"):
        existing.unlink(missing_ok=True)
    suffix = image_suffix(file.filename)
    filename = f"{stem}{suffix}"
    target = directory / filename
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return f"/uploads/latest/{filename}", target


def copy_saved_upload(source: Path, directory: Path, prefix: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix if source.suffix.lower() in IMAGE_SUFFIXES else ".jpg"
    filename = f"{prefix}_{uuid4().hex}{suffix}"
    target = directory / filename
    shutil.copyfile(source, target)
    return f"/uploads/{directory.name}/{filename}"


def upload_url_to_path(file_url: str) -> Path:
    if not file_url.startswith("/uploads/"):
        raise ValueError(f"not a local upload url: {file_url}")
    return UPLOAD_DIR / file_url.removeprefix("/uploads/")


def absolute_upload_url(file_url: str | None) -> str | None:
    if not file_url:
        return None
    if file_url.startswith(("http://", "https://")):
        return file_url
    return f"{BASE_URL}{file_url}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_image_filename(filename: str | None) -> bool:
    return Path(filename or "").suffix.lower() in IMAGE_SUFFIXES


def is_zip_filename(filename: str | None) -> bool:
    return Path(filename or "").suffix.lower() in ZIP_SUFFIXES


def save_detection_upload(file: UploadFile, job_dir: Path, expected_label: str, prefix: str | None = None) -> tuple[str, str]:
    if not is_image_filename(file.filename):
        raise HTTPException(status_code=400, detail=f"{file.filename or 'file'} is not a supported image")
    job_dir.mkdir(parents=True, exist_ok=True)
    suffix = image_suffix(file.filename)
    name_prefix = safe_name(prefix or expected_label)
    filename = f"{name_prefix}_{uuid4().hex}{suffix}"
    target = job_dir / filename
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return filename, f"/uploads/detection_jobs/{job_dir.name}/{filename}"


def save_detection_zip(file: UploadFile, job_dir: Path, expected_label: str) -> list[tuple[str, str]]:
    if not is_zip_filename(file.filename):
        raise HTTPException(status_code=400, detail=f"{file.filename or 'file'} is not a zip file")
    job_dir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[str, str]] = []
    try:
        zip_bytes = file.file.read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            for member in archive.infolist():
                if member.is_dir() or not is_image_filename(member.filename):
                    continue
                source_name = Path(member.filename).name
                if not source_name:
                    continue
                suffix = image_suffix(source_name)
                stem = safe_name(Path(source_name).stem)[:40] or expected_label
                filename = f"{safe_name(expected_label)}_{stem}_{uuid4().hex}{suffix}"
                target = job_dir / filename
                with archive.open(member) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)
                saved.append((filename, f"/uploads/detection_jobs/{job_dir.name}/{filename}"))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail=f"{file.filename or 'zip'} is not a valid zip file") from exc
    return saved


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


def face_sample_from_row(row: Any, sync_version: int | None = None) -> dict[str, Any]:
    data = {
        "id": row["id"],
        "person_id": row["person_id"],
        "file_url": row["file_url"],
        "image_url": absolute_upload_url(row["file_url"]),
        "image_hash": row["image_hash"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if sync_version is not None:
        data["sync_version"] = sync_version
    return data


def face_sync_change_from_row(row: Any) -> dict[str, Any]:
    return {
        "version": row["version"],
        "change_type": row["change_type"],
        "face_sample_id": row["face_sample_id"],
        "person_id": row["person_id"],
        "member_name": row["member_name"],
        "role": row["role"],
        "authorized": bool(row["authorized"]),
        "file_url": row["file_url"],
        "image_url": absolute_upload_url(row["file_url"]),
        "image_hash": row["image_hash"],
        "created_at": row["created_at"],
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
        "source": row["source"],
        "event_key": row["event_key"],
    }


def latest_result_from_row(row: Any) -> dict[str, Any]:
    return {
        "id": f"latest_{row['device_id']}",
        "device_id": row["device_id"],
        "captured_at": row["captured_at"],
        "file_url": row["file_url"],
        "yolo_labels": parse_json(row["yolo_labels_json"], []),
        "face_result": parse_json(row["face_result_json"], {}),
        "access_decision": row["access_decision"],
        "source": row["source"],
        "updated_at": row["updated_at"],
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


def detection_item_from_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "expected_label": row["expected_label"],
        "filename": row["filename"],
        "file_url": row["file_url"],
        "status": row["status"],
        "yolo_labels": parse_json(row["yolo_labels_json"], []),
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def detection_job_from_row(row: Any, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    data = {
        "id": row["id"],
        "device_id": row["device_id"],
        "status": row["status"],
        "expected_fire_count": row["expected_fire_count"],
        "expected_drone_count": row["expected_drone_count"],
        "total_count": row["total_count"],
        "completed_count": row["completed_count"],
        "failed_count": row["failed_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if items is not None:
        data["items"] = items
    return data


def add_event(conn: Any, event_type: str, message: str, severity: str = "info") -> None:
    conn.execute(
        "INSERT INTO events (id, type, severity, message, created_at) VALUES (?, ?, ?, ?, ?)",
        (make_id("evt"), event_type, severity, message, now_iso()),
    )


def latest_face_library_version(conn: Any) -> int:
    row = conn.execute("SELECT COALESCE(MAX(version), 0) AS version FROM face_sync_changes").fetchone()
    return int(row["version"] or 0)


def record_face_sync_change(
    conn: Any,
    change_type: str,
    face_sample_id: str,
    person_id: str,
    member_name: str,
    role: str,
    authorized: bool | int,
    file_url: str | None,
    image_hash: str | None,
    created_at: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO face_sync_changes (
          change_type, face_sample_id, person_id, member_name, role, authorized,
          file_url, image_hash, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            change_type,
            face_sample_id,
            person_id,
            member_name,
            role,
            int(bool(authorized)),
            file_url,
            image_hash,
            created_at or now_iso(),
        ),
    )
    return int(cur.lastrowid)


def record_person_face_metadata_changes(conn: Any, person_id: str) -> int:
    person = conn.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
    if not person:
        return 0
    rows = conn.execute(
        "SELECT * FROM face_samples WHERE person_id = ? AND deleted_at IS NULL ORDER BY created_at ASC, rowid ASC",
        (person_id,),
    ).fetchall()
    if not rows:
        return 0
    updated_at = now_iso()
    conn.execute("UPDATE face_samples SET updated_at = ? WHERE person_id = ? AND deleted_at IS NULL", (updated_at, person_id))
    for row in rows:
        record_face_sync_change(
            conn,
            "upsert",
            row["id"],
            person["id"],
            person["name"],
            person["role"],
            person["authorized"],
            row["file_url"],
            row["image_hash"],
            updated_at,
        )
    return len(rows)


def save_face_sample(
    conn: Any,
    person_id: str,
    image: UploadFile,
    created_at: str | None = None,
    event_type: str = "face_enrolled",
) -> dict[str, Any]:
    if not is_image_filename(image.filename):
        raise HTTPException(status_code=400, detail=f"{image.filename or 'file'} is not a supported image")

    person = conn.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
    if not person:
        raise HTTPException(status_code=404, detail="person not found")

    timestamp = created_at or now_iso()
    file_url = save_upload(image, UPLOAD_DIR / "faces", "face")
    image_hash = file_sha256(upload_url_to_path(file_url))
    face_sample_id = make_id("face")
    conn.execute(
        """
        INSERT INTO face_samples (id, person_id, file_url, created_at, image_hash, updated_at, deleted_at)
        VALUES (?, ?, ?, ?, ?, ?, NULL)
        """,
        (face_sample_id, person_id, file_url, timestamp, image_hash, timestamp),
    )
    conn.execute("UPDATE persons SET face_enrolled = 1 WHERE id = ?", (person_id,))
    sync_version = record_face_sync_change(
        conn,
        "upsert",
        face_sample_id,
        person["id"],
        person["name"],
        person["role"],
        person["authorized"],
        file_url,
        image_hash,
        timestamp,
    )
    add_event(conn, event_type, f"face sample uploaded for {person_id}")
    row = conn.execute("SELECT * FROM face_samples WHERE id = ?", (face_sample_id,)).fetchone()
    return face_sample_from_row(row, sync_version)


def label_key(label: str) -> str:
    return label.strip().lower().replace("-", "_").replace(" ", "_")


def detected_target_keys(labels: list[dict[str, Any]]) -> list[str]:
    normalized = {label_key(str(item["label"])) for item in labels}
    targets: list[str] = []
    for target, aliases in TARGET_LABEL_ALIASES.items():
        alias_keys = {label_key(alias) for alias in aliases}
        if normalized & alias_keys:
            targets.append(target)
    return sorted(targets)


def has_person_label(labels: list[dict[str, Any]]) -> bool:
    return any(label_key(str(item["label"])) == "person" or str(item["label"]).strip() == "人" for item in labels)


def recognition_event_key(
    source: str,
    mode: str,
    labels: list[dict[str, Any]],
    face_result: dict[str, Any],
    access_decision: str,
) -> str | None:
    targets = detected_target_keys(labels)
    if source in ACTIVE_DETECTION_SOURCES:
        result = "+".join(targets) if targets else "not_detected"
        return f"active:{source}:{result}"
    if targets:
        return f"object:{'+'.join(targets)}"
    if has_person_label(labels):
        matched_id = face_result.get("matched_person_id")
        matched_name = face_result.get("matched_name")
        if access_decision == "allow" and matched_id:
            return f"face:person:{matched_id}"
        if access_decision == "allow" and matched_name:
            return f"face:name:{matched_name}"
        return "face:unknown"
    if mode == "event":
        return f"manual:{source}:none"
    return None


def should_store_event(conn: Any, event_key: str, now_epoch: float) -> bool:
    row = conn.execute("SELECT last_seen_epoch FROM event_cooldowns WHERE event_key = ?", (event_key,)).fetchone()
    if row and now_epoch - float(row["last_seen_epoch"]) < EVENT_COOLDOWN_SECONDS:
        return False
    conn.execute(
        """
        INSERT INTO event_cooldowns (event_key, last_seen_epoch)
        VALUES (?, ?)
        ON CONFLICT(event_key) DO UPDATE SET last_seen_epoch = excluded.last_seen_epoch
        """,
        (event_key, now_epoch),
    )
    return True


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


def detection_job_with_items(conn: Any, job_id: str) -> dict[str, Any]:
    job_row = conn.execute("SELECT * FROM detection_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job_row:
        raise HTTPException(status_code=404, detail="detection job not found")
    item_rows = conn.execute(
        "SELECT * FROM detection_job_items WHERE job_id = ? ORDER BY created_at ASC, rowid ASC",
        (job_id,),
    ).fetchall()
    return detection_job_from_row(job_row, [detection_item_from_row(row) for row in item_rows])


def refresh_detection_job_status(conn: Any, job_id: str) -> None:
    rows = conn.execute("SELECT status FROM detection_job_items WHERE job_id = ?", (job_id,)).fetchall()
    total = len(rows)
    completed = sum(1 for row in rows if row["status"] == "done")
    failed = sum(1 for row in rows if row["status"] == "failed")
    if total == 0:
        status = "failed"
    elif completed + failed == total:
        status = "done" if failed == 0 else ("failed" if completed == 0 else "partial")
    elif completed or failed:
        status = "processing"
    else:
        status = "queued"
    conn.execute(
        """
        UPDATE detection_jobs
        SET status = ?, completed_count = ?, failed_count = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, completed, failed, now_iso(), job_id),
    )


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
        changed_samples = record_person_face_metadata_changes(conn, person_id)
        add_event(conn, "person_updated", f"person {person_id} updated")
        if changed_samples:
            add_event(conn, "face_library_changed", f"{changed_samples} face sample(s) updated for {person_id}")
        row = conn.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
    return ok(person_from_row(row))


@app.delete("/api/persons/{person_id}")
def delete_person(person_id: str) -> dict[str, Any]:
    with connect() as conn:
        person = conn.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
        if not person:
            raise HTTPException(status_code=404, detail="person not found")
        rows = conn.execute(
            "SELECT * FROM face_samples WHERE person_id = ? AND deleted_at IS NULL ORDER BY created_at ASC, rowid ASC",
            (person_id,),
        ).fetchall()
        deleted_at = now_iso()
        for row in rows:
            record_face_sync_change(
                conn,
                "delete",
                row["id"],
                person["id"],
                person["name"],
                person["role"],
                person["authorized"],
                row["file_url"],
                row["image_hash"],
                deleted_at,
            )
        if rows:
            conn.execute("UPDATE face_samples SET deleted_at = ?, updated_at = ? WHERE person_id = ? AND deleted_at IS NULL", (deleted_at, deleted_at, person_id))
        cur = conn.execute("DELETE FROM persons WHERE id = ?", (person_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="person not found")
        add_event(conn, "person_deleted", f"person {person_id} deleted")
    return ok({"deleted": True})


@app.post("/api/persons/{person_id}/face-samples")
def upload_face_sample(person_id: str, image: UploadFile = File(...)) -> dict[str, Any]:
    with connect() as conn:
        sample = save_face_sample(conn, person_id, image)
    return ok({"person_id": person_id, "face_enrolled": True, "sample": sample})


@app.post("/api/device/face-captures")
def upload_device_face_capture(
    device_id: str = Form(...),
    person_id: str = Form(...),
    image: UploadFile = File(...),
    command_id: str | None = Form(default=None),
    captured_at: str | None = Form(default=None),
) -> dict[str, Any]:
    with connect() as conn:
        touch_device(conn, device_id)
        sample = save_face_sample(conn, person_id, image, captured_at, "face_captured")
        if command_id:
            command = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
            if command and command["device_id"] == device_id:
                conn.execute(
                    """
                    UPDATE commands
                    SET status = 'done', executed_at = ?, message = ?
                    WHERE id = ?
                    """,
                    (now_iso(), f"face sample captured for {person_id}", command_id),
                )
                add_event(conn, "command_done", f"{command_id} done: face sample captured")
    return ok({"device_id": device_id, "person_id": person_id, "face_enrolled": True, "sample": sample})


@app.get("/api/device/face-library/sync")
def sync_face_library(
    device_id: str = Query(...),
    since_version: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    with connect() as conn:
        touch_device(conn, device_id)
        latest_version = latest_face_library_version(conn)
        rows = conn.execute(
            """
            SELECT * FROM face_sync_changes
            WHERE version > ?
            ORDER BY version ASC
            LIMIT ?
            """,
            (since_version, limit),
        ).fetchall()
        changes = [face_sync_change_from_row(row) for row in rows]
        to_version = changes[-1]["version"] if changes else latest_version
    return ok(
        {
            "device_id": device_id,
            "from_version": since_version,
            "to_version": to_version,
            "latest_version": latest_version,
            "has_more": bool(to_version < latest_version),
            "server_time": now_iso(),
            "changes": changes,
        }
    )


@app.post("/api/device/face-library/ack")
def ack_face_library_sync(payload: FaceLibraryAck) -> dict[str, Any]:
    synced_at = now_iso()
    with connect() as conn:
        touch_device(conn, payload.device_id)
        latest_version = latest_face_library_version(conn)
        conn.execute(
            """
            INSERT INTO face_library_sync_state (device_id, synced_version, synced_at, message)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
              synced_version = excluded.synced_version,
              synced_at = excluded.synced_at,
              message = excluded.message
            """,
            (payload.device_id, payload.synced_version, synced_at, payload.message),
        )
        add_event(conn, "face_library_synced", f"{payload.device_id} synced face library to {payload.synced_version}")
    return ok(
        {
            "device_id": payload.device_id,
            "synced_version": payload.synced_version,
            "latest_version": latest_version,
            "synced_at": synced_at,
        }
    )


@app.get("/api/photos")
def list_photos(limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM photos ORDER BY captured_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
    return ok([photo_from_row(row) for row in rows])


@app.get("/api/photos/latest")
def latest_photo(device_id: str = Query(default=DEFAULT_DEVICE_ID)) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM latest_results WHERE device_id = ?", (device_id,)).fetchone()
    return ok(latest_result_from_row(row) if row else {})


@app.get("/api/detection-jobs")
def list_detection_jobs(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM detection_jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return ok([detection_job_from_row(row) for row in rows])


@app.get("/api/detection-jobs/{job_id}")
def get_detection_job(job_id: str) -> dict[str, Any]:
    with connect() as conn:
        return ok(detection_job_with_items(conn, job_id))


@app.post("/api/detection-jobs")
def create_detection_job(
    device_id: str = Form(default=DEFAULT_DEVICE_ID),
    fire_extinguisher_images: list[UploadFile] | None = File(default=None),
    drone_images: list[UploadFile] | None = File(default=None),
    fire_extinguisher_zip: list[UploadFile] | None = File(default=None),
    drone_zip: list[UploadFile] | None = File(default=None),
) -> dict[str, Any]:
    job_id = make_id("det")
    job_dir = UPLOAD_DIR / "detection_jobs" / job_id
    created_at = now_iso()
    saved: list[tuple[str, str, str]] = []

    for file in fire_extinguisher_images or []:
        filename, file_url = save_detection_upload(file, job_dir, "fire_extinguisher")
        saved.append(("fire_extinguisher", filename, file_url))
    for file in drone_images or []:
        filename, file_url = save_detection_upload(file, job_dir, "drone")
        saved.append(("drone", filename, file_url))
    for file in fire_extinguisher_zip or []:
        if file.filename:
            for filename, file_url in save_detection_zip(file, job_dir, "fire_extinguisher"):
                saved.append(("fire_extinguisher", filename, file_url))
    for file in drone_zip or []:
        if file.filename:
            for filename, file_url in save_detection_zip(file, job_dir, "drone"):
                saved.append(("drone", filename, file_url))

    if not saved:
        raise HTTPException(status_code=400, detail="upload at least one image or zip with images")

    expected_fire_count = sum(1 for label, _, _ in saved if label == "fire_extinguisher")
    expected_drone_count = sum(1 for label, _, _ in saved if label == "drone")

    with connect() as conn:
        touch_device(conn, device_id)
        conn.execute(
            """
            INSERT INTO detection_jobs (
              id, device_id, status, expected_fire_count, expected_drone_count, total_count,
              completed_count, failed_count, created_at, updated_at
            )
            VALUES (?, ?, 'queued', ?, ?, ?, 0, 0, ?, ?)
            """,
            (job_id, device_id, expected_fire_count, expected_drone_count, len(saved), created_at, created_at),
        )
        command_items: list[dict[str, Any]] = []
        for expected_label, filename, file_url in saved:
            item_id = make_id("item")
            conn.execute(
                """
                INSERT INTO detection_job_items (
                  id, job_id, expected_label, filename, file_url, status, yolo_labels_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'queued', '[]', ?, ?)
                """,
                (item_id, job_id, expected_label, filename, file_url, created_at, created_at),
            )
            command_items.append({"item_id": item_id, "expected_label": expected_label, "file_url": file_url, "filename": filename})
        command = create_command(
            conn,
            device_id,
            "PROCESS_DETECTION_PACKAGE",
            {
                "job_id": job_id,
                "items": command_items,
                "upload_result_url": f"/api/device/detection-jobs/{job_id}/results",
            },
        )
        add_event(conn, "detection_job_created", f"detection job {job_id} queued with {len(saved)} image(s)")
        data = detection_job_with_items(conn, job_id)
    data["command"] = command
    return ok(data)


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
    face_result_json: str | None = Form(default=None),
    access_decision: str | None = Form(default=None),
    captured_at: str | None = Form(default=None),
    source: str = Form(default="auto_face"),
    mode: str = Form(default="latest"),
) -> dict[str, Any]:
    labels = validate_yolo_labels(yolo_labels_json)
    supplied_face_result = validate_face_result(face_result_json)
    supplied_access_decision = validate_access_decision(access_decision)
    source = normalize_source(source)
    mode = validate_upload_mode(mode)
    captured_at = captured_at or now_iso()
    latest_file_url, latest_path = save_latest_upload(image, device_id)

    with connect() as conn:
        touch_device(conn, device_id)
        fallback_face_result, fallback_access_decision = match_face(conn, labels)
        face_result = supplied_face_result if supplied_face_result is not None else fallback_face_result
        decision = supplied_access_decision or fallback_access_decision
        conn.execute(
            """
            INSERT INTO latest_results (
              device_id, captured_at, file_url, yolo_labels_json, face_result_json, access_decision, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
              captured_at = excluded.captured_at,
              file_url = excluded.file_url,
              yolo_labels_json = excluded.yolo_labels_json,
              face_result_json = excluded.face_result_json,
              access_decision = excluded.access_decision,
              source = excluded.source,
              updated_at = excluded.updated_at
            """,
            (device_id, captured_at, latest_file_url, json_dumps(labels), json_dumps(face_result), decision, source, now_iso()),
        )

        event_key = recognition_event_key(source, mode, labels, face_result, decision)
        history_saved = False
        history_photo_id: str | None = None
        history_file_url: str | None = None
        if event_key and should_store_event(conn, event_key, time.time()):
            history_photo_id = make_id("photo")
            history_file_url = copy_saved_upload(latest_path, UPLOAD_DIR / "photos", history_photo_id)
            conn.execute(
                """
                INSERT INTO photos (
                  id, device_id, captured_at, file_url, yolo_labels_json, face_result_json, access_decision, source, event_key
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history_photo_id,
                    device_id,
                    captured_at,
                    history_file_url,
                    json_dumps(labels),
                    json_dumps(face_result),
                    decision,
                    source,
                    event_key,
                ),
            )
            history_saved = True
            add_event(conn, "recognition_event", f"{event_key} uploaded from {device_id}")

        label_names = {item["label"].lower() for item in labels}
        if history_saved and decision == "allow":
            create_command(conn, device_id, "OPEN_DOOR", {})
        if history_saved and "light bulb" in label_names:
            create_command(conn, device_id, "SET_LIGHT", {"level": 80})

        latest_row = conn.execute("SELECT * FROM latest_results WHERE device_id = ?", (device_id,)).fetchone()
    data = latest_result_from_row(latest_row)
    data["history_saved"] = history_saved
    data["history_photo_id"] = history_photo_id
    data["history_file_url"] = history_file_url
    data["event_key"] = event_key
    data["cooldown_seconds"] = EVENT_COOLDOWN_SECONDS
    return ok(data)


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
            job_ids = [command_from_row(row).get("payload", {}).get("job_id") for row in rows if row["type"] == "PROCESS_DETECTION_PACKAGE"]
            for job_id in filter(None, job_ids):
                conn.execute(
                    "UPDATE detection_jobs SET status = 'processing', updated_at = ? WHERE id = ? AND status = 'queued'",
                    (now_iso(), job_id),
                )
                conn.execute(
                    "UPDATE detection_job_items SET status = 'processing', updated_at = ? WHERE job_id = ? AND status = 'queued'",
                    (now_iso(), job_id),
                )
            add_event(conn, "commands_pulled", f"{len(command_ids)} command(s) pulled by {device_id}")
    return ok([command_from_row(row) for row in rows])


@app.post("/api/device/detection-jobs/{job_id}/results")
def upload_detection_job_results(job_id: str, payload: DetectionJobResultUpload) -> dict[str, Any]:
    if not payload.items:
        raise HTTPException(status_code=400, detail="items is required")
    with connect() as conn:
        job_row = conn.execute("SELECT * FROM detection_jobs WHERE id = ?", (job_id,)).fetchone()
        if not job_row:
            raise HTTPException(status_code=404, detail="detection job not found")
        if job_row["device_id"] != payload.device_id:
            raise HTTPException(status_code=400, detail="device_id does not match detection job")

        for item in payload.items:
            item_row = conn.execute(
                "SELECT * FROM detection_job_items WHERE id = ? AND job_id = ?",
                (item.item_id, job_id),
            ).fetchone()
            if not item_row:
                raise HTTPException(status_code=404, detail=f"detection item not found: {item.item_id}")
            labels = normalize_yolo_labels(item.yolo_labels, f"items.{item.item_id}.yolo_labels")
            conn.execute(
                """
                UPDATE detection_job_items
                SET status = ?, yolo_labels_json = ?, error_message = ?, updated_at = ?
                WHERE id = ? AND job_id = ?
                """,
                (item.status, json_dumps(labels), item.error_message, now_iso(), item.item_id, job_id),
            )
        refresh_detection_job_status(conn, job_id)
        add_event(conn, "detection_job_result", f"detection job {job_id} received {len(payload.items)} result(s)")
        return ok(detection_job_with_items(conn, job_id))


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
