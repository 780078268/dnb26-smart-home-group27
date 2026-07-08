from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import os
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import database
import face_service
import recognizer


ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT_DIR / "frontend"
DATA_DIR = ROOT_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
REAL_SAMPLE_DIR = ROOT_DIR / "真实测试数据集"
os.environ.setdefault("YOLO_CONFIG_DIR", str(DATA_DIR / "ultralytics"))

class SensorPayload(BaseModel):
    temperature: float = Field(..., ge=-20, le=80)
    door_open: bool = False
    window_open: bool = False
    light_level: int = Field(0, ge=0, le=100)
    fan_on: bool = False
    source: str = "hardware"


class ControlPayload(BaseModel):
    device: str
    action: str
    value: str | int | float | bool | None = None
    source: str = "gui"


class DeviceTelemetryPayload(BaseModel):
    device_id: str = "orange-pi-main"
    captured_at: str | None = None
    temperature_c: float = Field(..., ge=-20, le=80)
    door_open: bool = False
    window_open: bool = False
    light_level: int = Field(0, ge=0, le=100)
    fan_on: bool = False


class CommandCreatePayload(BaseModel):
    device_id: str = "orange-pi-main"
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandAckPayload(BaseModel):
    device_id: str = "orange-pi-main"
    status: str = Field(..., pattern="^(done|failed)$")
    message: str | None = None


class PersonCreatePayload(BaseModel):
    name: str
    role: str = "student"
    authorized: bool = True
    face_code: str | None = None


class PersonUpdatePayload(BaseModel):
    name: str | None = None
    role: str | None = None
    authorized: bool | None = None
    face_code: str | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "authorized_faces").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "face_test_samples").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "demo_images").mkdir(parents=True, exist_ok=True)
    REAL_SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    database.init_db()
    yield


app = FastAPI(
    title="Smart Home IST System",
    description="Data intelligence backend for the DnB26 smart home prototype.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
(DATA_DIR / "face_test_samples").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "yolo_test_samples").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "iot_test_samples").mkdir(parents=True, exist_ok=True)
REAL_SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/face-samples", StaticFiles(directory=DATA_DIR / "face_test_samples"), name="face-samples")
app.mount("/yolo-samples", StaticFiles(directory=DATA_DIR / "yolo_test_samples"), name="yolo-samples")
app.mount("/iot-samples", StaticFiles(directory=DATA_DIR / "iot_test_samples"), name="iot-samples")
app.mount("/real-samples", StaticFiles(directory=REAL_SAMPLE_DIR), name="real-samples")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "data": {"status": "ok", "version": "0.2"}, "error": None, "project": "smart_home_ist"}


@app.get("/api/state")
def state() -> dict[str, Any]:
    return {
        "sensor": database.latest_sensor_state(),
        "people": database.list_authorized_people(),
        "images": database.latest_image_events(5),
        "pending_commands": database.pending_commands(),
    }


@app.get("/api/people")
def people() -> list[dict[str, Any]]:
    return database.list_authorized_people()


@app.get("/api/devices")
def devices() -> dict[str, Any]:
    latest = database.latest_sensor_state()
    last_seen = latest.get("captured_at") or latest.get("created_at")
    items = [
        {
            "id": "orange-pi-main",
            "name": "Orange Pi Smart Home Gateway",
            "type": "gateway",
            "online": True,
            "last_seen": last_seen,
        },
        {
            "id": "camera-main",
            "name": "Smart Home Camera",
            "type": "camera",
            "online": True,
            "last_seen": last_seen,
        },
    ]
    latest_source = latest.get("source")
    if latest_source and latest_source not in {item["id"] for item in items}:
        items.append(
            {
                "id": latest_source,
                "name": f"{latest_source} Source",
                "type": "gateway",
                "online": True,
                "last_seen": last_seen,
            }
        )
    return openapi_response(
        items
    )


@app.get("/api/status/latest")
def status_latest() -> dict[str, Any]:
    return openapi_response(telemetry_to_openapi(database.latest_sensor_state()))


@app.get("/api/telemetry")
def telemetry_history(
    from_time: str | None = Query(None, alias="from"),
    to_time: str | None = Query(None, alias="to"),
    device_id: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    events = database.list_sensor_events(device_id=device_id, from_time=from_time, to_time=to_time, limit=limit)
    return openapi_response([telemetry_to_openapi(event) for event in events])


@app.get("/api/persons")
def persons() -> dict[str, Any]:
    registered = face_service.list_registered_people()
    if registered:
        data = [
            {
                "id": item["person_id"],
                "name": item["name"],
                "role": item["role"],
                "authorized": item["authorized"],
                "face_enrolled": item["face_enrolled"],
                "sample_count": item["sample_count"],
                "lfw_name": item.get("lfw_name"),
            }
            for item in registered
        ]
    else:
        data = [
            {
                "id": str(item["id"]),
                "name": item["name"],
                "role": item["role"],
                "authorized": bool(item["is_authorized"]),
                "face_enrolled": True,
                "sample_count": 0,
                "lfw_name": None,
            }
            for item in database.list_authorized_people()
        ]
    return openapi_response(data)


@app.post("/api/persons")
def create_person(payload: PersonCreatePayload) -> dict[str, Any]:
    person = database.create_person(
        name=payload.name,
        role=payload.role,
        authorized=payload.authorized,
        face_code=payload.face_code,
    )
    return openapi_response(person_to_openapi(person))


@app.patch("/api/persons/{person_id}")
def update_person(person_id: str, payload: PersonUpdatePayload) -> dict[str, Any]:
    try:
        person = database.update_person(
            person_id,
            name=payload.name,
            role=payload.role,
            authorized=payload.authorized,
            face_code=payload.face_code,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid person_id") from exc
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return openapi_response(person_to_openapi(person))


@app.delete("/api/persons/{person_id}")
def delete_person(person_id: str) -> dict[str, Any]:
    try:
        deleted = database.delete_person(person_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid person_id") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Person not found")
    return openapi_response({"deleted": True, "person_id": person_id})


@app.post("/api/persons/{person_id}/face-samples")
def enroll_face_sample(
    person_id: str,
    image: UploadFile = File(...),
    name: str = Form("Manual Person"),
    role: str = Form("authorized-demo"),
) -> dict[str, Any]:
    suffix = Path(image.filename or "face.jpg").suffix.lower() or ".jpg"
    temp_path = UPLOAD_DIR / f"{uuid4().hex}{suffix}"
    with temp_path.open("wb") as file:
        shutil.copyfileobj(image.file, file)
    try:
        saved = face_service.save_enrollment_sample(person_id, name, role, temp_path, image.filename or "face.jpg")
    finally:
        temp_path.unlink(missing_ok=True)
    return openapi_response({"person_id": person_id, "face_enrolled": True, "saved_sample": str(saved.relative_to(DATA_DIR))})


@app.post("/api/sensors")
def sensors(payload: SensorPayload) -> dict[str, Any]:
    event = database.insert_sensor_event(**payload.model_dump())
    commands = []
    if event["temperature"] >= 30 and not event["fan_on"]:
        commands.append(database.insert_command(device="fan", action="turn_on", source="auto-temperature"))
    return {"event": event, "auto_commands": commands}


@app.post("/api/device/telemetry")
def device_telemetry(payload: DeviceTelemetryPayload) -> dict[str, Any]:
    event = database.insert_sensor_event(
        temperature=payload.temperature_c,
        door_open=payload.door_open,
        window_open=payload.window_open,
        light_level=payload.light_level,
        fan_on=payload.fan_on,
        source=payload.device_id,
        captured_at=payload.captured_at,
    )
    commands = []
    if event["temperature"] >= 30 and not event["fan_on"]:
        commands.append(database.insert_command(device="fan", action="turn_on", source="auto-temperature"))
    return openapi_response({"saved": True, "event": telemetry_to_openapi(event), "auto_commands": commands})


@app.post("/api/control")
def control(payload: ControlPayload) -> dict[str, Any]:
    command = database.insert_command(**payload.model_dump())
    return {"command": command}


@app.get("/api/commands/pending")
def commands_pending() -> list[dict[str, Any]]:
    return database.pending_commands()


@app.get("/api/device/commands/pending")
def device_commands_pending(device_id: str = "orange-pi-main") -> dict[str, Any]:
    return openapi_response([command_to_openapi(command, device_id) for command in database.pending_commands(device_id)])


@app.post("/api/commands/{command_id}/executed")
def command_executed(command_id: int) -> dict[str, Any]:
    command = database.mark_command_executed(command_id)
    if not command:
        raise HTTPException(status_code=404, detail="Command not found")
    return {"command": command}


@app.post("/api/device/commands/{command_id}/ack")
def device_command_ack(command_id: int, payload: CommandAckPayload) -> dict[str, Any]:
    command = database.mark_command_executed(command_id, "executed" if payload.status == "done" else "failed")
    if not command:
        raise HTTPException(status_code=404, detail="Command not found")
    return openapi_response({"updated": True, "command": command_to_openapi(command), "message": payload.message})


@app.post("/api/images")
def upload_image(
    image: UploadFile = File(...),
    image_type: str = Form("camera"),
    face_code: str | None = Form(None),
) -> dict[str, Any]:
    return save_and_recognize_image(image=image, image_type=image_type, face_code=face_code)


@app.post("/api/device/photos")
def upload_device_photo(
    image: UploadFile = File(...),
    device_id: str = Form("orange-pi-main"),
    captured_at: str | None = Form(None),
    face_code: str | None = Form(None),
) -> dict[str, Any]:
    result = save_and_recognize_image(image=image, image_type=device_id, face_code=face_code, captured_at=captured_at)
    return openapi_response(photo_to_openapi(result["event"], result["image_url"], result["auto_commands"]))


@app.get("/api/photos")
def photos(limit: int = 50) -> dict[str, Any]:
    items = []
    for event in database.latest_image_events(max(1, min(300, limit))):
        items.append(photo_to_openapi(event, f"/uploads/{event['filename']}", []))
    return openapi_response(items)


@app.get("/api/face/samples")
def face_samples() -> dict[str, Any]:
    return openapi_response(
        {
            "people": face_service.list_registered_people(),
            "samples": face_service.list_face_samples(),
            "manifest": face_service.load_manifest(),
        }
    )


@app.post("/api/face/verify")
def verify_face(
    image: UploadFile | None = File(None),
    sample_id: str | None = Form(None),
) -> dict[str, Any]:
    if sample_id:
        try:
            return openapi_response(face_service.verify_sample(sample_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    if image is None:
        raise HTTPException(status_code=400, detail="Either image or sample_id is required")

    suffix = Path(image.filename or "face.jpg").suffix.lower() or ".jpg"
    safe_name = f"{uuid4().hex}{suffix}"
    target = UPLOAD_DIR / safe_name
    with target.open("wb") as file:
        shutil.copyfileobj(image.file, file)
    result = face_service.verify_image(target)
    return openapi_response({**result, "file_url": f"/uploads/{safe_name}"})


@app.post("/api/commands")
def create_command(payload: CommandCreatePayload) -> dict[str, Any]:
    device, action, value = openapi_command_to_legacy(payload.type, payload.payload)
    command = database.insert_command(device=device, action=action, value=value, source="openapi", device_id=payload.device_id)
    return openapi_response(command_to_openapi(command, payload.device_id))


@app.post("/api/demo/sensors")
def demo_sensors() -> dict[str, Any]:
    latest = database.latest_sensor_state()
    temperature = float(latest["temperature"]) + 1.3
    if temperature > 33:
        temperature = 24.8
    light_level = (int(latest["light_level"]) + 17) % 101
    event = database.insert_sensor_event(
        temperature=round(temperature, 1),
        door_open=not latest["door_open"],
        window_open=temperature > 29,
        light_level=light_level,
        fan_on=temperature >= 30,
        source="demo-button",
    )
    return {"event": event}


def access_reason(detected_labels: set[str], face: dict[str, Any]) -> str:
    if "person" not in detected_labels:
        return "No person detected, door remains closed"
    if not face.get("authorized"):
        return "Person detected but identity is not authorized"
    return "Person detected and identity is authorized"


@app.get("/api/history")
def history(kind: str = "sensors", limit: int = 50) -> dict[str, Any]:
    return {"kind": kind, "items": database.list_history(kind, limit)}


@app.get("/api/events")
def events(limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(300, limit))
    items = []
    for event in database.list_history("sensors", limit):
        items.append(
            {
                "id": f"tel_{event.get('id')}",
                "type": "telemetry",
                "title": "设备状态上报",
                "message": f"{event.get('source')} temperature={event.get('temperature')}C light={event.get('light_level')}%",
                "created_at": event.get("created_at"),
                "data": telemetry_to_openapi(event),
            }
        )
    for command in database.list_history("commands", limit):
        items.append(
            {
                "id": f"cmd_{command.get('id')}",
                "type": "command",
                "title": "控制命令",
                "message": f"{command.get('device')} {command.get('action')} status={command.get('status')}",
                "created_at": command.get("created_at"),
                "data": command_to_openapi(command),
            }
        )
    for image in database.list_history("images", limit):
        labels = recognizer.yolo_labels_from_detection(image.get("detection", {}))
        label_text = ", ".join(item["label"] for item in labels) or "unknown"
        items.append(
            {
                "id": f"photo_{image.get('id')}",
                "type": "photo",
                "title": "图片识别",
                "message": f"detected {label_text}",
                "created_at": image.get("created_at"),
                "data": photo_to_openapi(image, f"/uploads/{image['filename']}", []),
            }
        )
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return openapi_response(items[:limit])


def save_and_recognize_image(
    *,
    image: UploadFile,
    image_type: str = "camera",
    face_code: str | None = None,
    captured_at: str | None = None,
) -> dict[str, Any]:
    suffix = Path(image.filename or "upload.jpg").suffix.lower() or ".jpg"
    safe_name = f"{uuid4().hex}{suffix}"
    target = UPLOAD_DIR / safe_name
    with target.open("wb") as file:
        shutil.copyfileobj(image.file, file)

    detection = recognizer.analyze_image(target, image.filename)
    face = face_service.verify_image(target) if not face_code else recognizer.recognize_face(face_code, database.list_authorized_people())
    face = normalize_face_result(face)
    detected_labels = {item.get("label") for item in recognizer.yolo_labels_from_detection(detection)}
    event = database.insert_image_event(
        filename=safe_name,
        original_name=image.filename or safe_name,
        image_type=image_type,
        detection=detection,
        face=face,
        captured_at=captured_at,
    )

    commands = []
    access_allowed = "person" in detected_labels and bool(face.get("authorized"))
    if access_allowed:
        commands.append(database.insert_command(device="door", action="open", source="face-recognition"))
    light_command = recognizer.light_command_from_detection(
        detection,
        device_id=image_type if image_type != "camera" else "orange-pi-main",
    )
    if light_command:
        level = int(light_command.get("payload", {}).get("level", 80))
        commands.append(
            database.insert_command(
                device="light",
                action="turn_off" if level <= 0 else "set_brightness",
                value=level,
                source=light_command.get("reason", "detected_light_bulb"),
                device_id=light_command.get("device_id", "orange-pi-main"),
            )
        )

    return {
        "event": event,
        "image_url": f"/uploads/{safe_name}",
        "detection": detection,
        "face": {
            **face,
            "access_allowed": access_allowed,
            "access_reason": access_reason(detected_labels, face),
        },
        "auto_commands": commands,
    }


def openapi_response(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def telemetry_to_openapi(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"tel_{event.get('id')}",
        "device_id": event.get("source", "orange-pi-main"),
        "captured_at": event.get("captured_at") or event.get("created_at"),
        "temperature_c": event.get("temperature"),
        "door_open": bool(event.get("door_open")),
        "window_open": bool(event.get("window_open")),
        "light_level": event.get("light_level"),
        "fan_on": bool(event.get("fan_on")),
    }


def photo_to_openapi(event: dict[str, Any], image_url: str, auto_commands: list[dict[str, Any]]) -> dict[str, Any]:
    detection = event.get("detection", {})
    face = event.get("face", {})
    yolo_labels = recognizer.yolo_labels_from_detection(detection)
    matched = bool(face.get("matched"))
    authorized = bool(face.get("authorized"))
    access_decision = "allow" if matched and authorized and any(item["label"] == "person" for item in yolo_labels) else "deny"
    if not matched and not face.get("face_code"):
        access_decision = "unknown"

    return {
        "id": f"photo_{event.get('id')}",
        "device_id": event.get("image_type", "orange-pi-main"),
        "captured_at": event.get("captured_at") or event.get("created_at"),
        "file_url": image_url,
        "yolo_labels": yolo_labels,
        "face_result": {
            "matched_person_id": face.get("matched_person_id") if matched else None,
            "matched_name": face.get("name") if matched else None,
            "confidence": face.get("confidence", 0.86 if matched else 0),
        },
        "access_decision": access_decision,
        "auto_commands": [command_to_openapi(command) for command in auto_commands],
    }


def normalize_face_result(face: dict[str, Any]) -> dict[str, Any]:
    matched_person_id = face.get("matched_person_id") or face.get("face_code")
    matched_name = face.get("matched_name") or face.get("name") or "Unknown"
    return {
        **face,
        "matched_person_id": matched_person_id if face.get("matched") else None,
        "face_code": matched_person_id,
        "name": matched_name,
        "authorized": bool(face.get("authorized")),
        "confidence": float(face.get("confidence", 0.86 if face.get("matched") else 0)),
    }


def person_to_openapi(person: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"person_{person.get('id')}",
        "name": person.get("name"),
        "role": person.get("role"),
        "authorized": bool(person.get("is_authorized")),
        "face_enrolled": bool(person.get("face_code")),
        "face_code": person.get("face_code"),
        "created_at": person.get("created_at"),
    }


def command_to_openapi(command: dict[str, Any], device_id: str = "orange-pi-main") -> dict[str, Any]:
    command_type, payload = legacy_command_to_openapi(command)
    status = command.get("status")
    if status == "executed":
        status = "done"
    return {
        "id": str(command.get("id")),
        "device_id": command.get("device_id") or device_id,
        "type": command_type,
        "payload": payload,
        "status": status,
        "created_at": command.get("created_at"),
        "executed_at": command.get("executed_at"),
    }


def legacy_command_to_openapi(command: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    device = command.get("device")
    action = command.get("action")
    value = command.get("value")
    if device == "light":
        if action == "turn_off":
            return "SET_LIGHT", {"level": 0}
        if action in {"turn_on", "set_brightness"}:
            return "SET_LIGHT", {"level": int(float(value or 100))}
    if device == "fan":
        return "SET_FAN", {"on": action == "turn_on"}
    if device == "door":
        return "OPEN_DOOR" if action == "open" else "CLOSE_DOOR", {}
    return "REQUEST_PHOTO", {}


def openapi_command_to_legacy(command_type: str, payload: dict[str, Any]) -> tuple[str, str, Any]:
    if command_type == "SET_LIGHT":
        level = int(payload.get("level", 100))
        return "light", "turn_off" if level <= 0 else "set_brightness", level
    if command_type == "SET_FAN":
        return "fan", "turn_on" if payload.get("on") else "turn_off", None
    if command_type == "OPEN_DOOR":
        return "door", "open", None
    if command_type == "CLOSE_DOOR":
        return "door", "close", None
    return "camera", "request_photo", None


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
