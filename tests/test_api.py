from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_HOME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SMART_HOME_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SMART_HOME_DATABASE", str(tmp_path / "data" / "test.sqlite3"))

    for name in list(sys.modules):
        if name == "backend" or name.startswith("backend."):
            del sys.modules[name]

    from backend.main import app

    with TestClient(app) as test_client:
        yield test_client


def assert_ok(response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["error"] is None
    return body["data"]


def test_health_and_device_seed(client):
    data = assert_ok(client.get("/api/health"))
    assert data["status"] == "ok"

    devices = assert_ok(client.get("/api/devices"))
    assert devices[0]["id"] == "orange-pi-main"
    assert devices[0]["online"] is True


def test_telemetry_latest_and_history(client):
    payload = {
        "device_id": "orange-pi-main",
        "captured_at": "2026-07-06T09:30:00+08:00",
        "temperature_c": 28.4,
        "door_open": False,
        "window_open": True,
        "light_level": 70,
        "fan_on": True,
    }
    saved = assert_ok(client.post("/api/device/telemetry", json=payload))
    assert saved["saved"] is True

    second_payload = {
        **payload,
        "captured_at": payload.get("captured_at") or "2026-07-06T09:30:00+08:00",
        "temperature_c": 29.5,
        "fan_on": False,
    }
    saved = assert_ok(client.post("/api/device/telemetry", json=second_payload))
    assert saved["saved"] is True

    latest = assert_ok(client.get("/api/status/latest"))
    assert latest["temperature_c"] == 29.5
    assert latest["window_open"] is True

    history = assert_ok(client.get("/api/telemetry", params={"limit": 10}))
    assert len(history) == 2
    assert history[0]["id"] == latest["id"]


def test_person_photo_yolo_command_flow(client):
    person = assert_ok(
        client.post(
            "/api/persons",
            json={"name": "Person A", "role": "student", "authorized": True},
        )
    )
    assert person["face_enrolled"] is False

    enrolled = assert_ok(
        client.post(
            f"/api/persons/{person['id']}/face-samples",
            files={"image": ("face.jpg", b"fake-face-image", "image/jpeg")},
        )
    )
    assert enrolled["face_enrolled"] is True

    photo = assert_ok(
        client.post(
            "/api/device/photos",
            data={
                "device_id": "orange-pi-main",
                "yolo_labels_json": '[{"label":"person","confidence":0.91},{"label":"light bulb","confidence":0.78}]',
            },
            files={"image": ("photo.jpg", b"fake-photo-image", "image/jpeg")},
        )
    )
    assert photo["access_decision"] == "allow"
    assert photo["yolo_labels"][0]["label"] == "person"
    assert photo["file_url"].startswith("/uploads/photos/")

    created = assert_ok(
        client.post(
            "/api/commands",
            json={"device_id": "orange-pi-main", "type": "SET_FAN", "payload": {"on": True}},
        )
    )
    assert created["status"] == "pending"

    pending = assert_ok(
        client.get("/api/device/commands/pending", params={"device_id": "orange-pi-main"})
    )
    command_ids = {command["id"] for command in pending}
    assert created["id"] in command_ids
    assert any(command["type"] == "OPEN_DOOR" for command in pending)
    assert any(command["type"] == "SET_LIGHT" for command in pending)

    ack = assert_ok(
        client.post(
            f"/api/device/commands/{created['id']}/ack",
            json={"device_id": "orange-pi-main", "status": "done", "message": "fan on"},
        )
    )
    assert ack["updated"] is True

    events = assert_ok(client.get("/api/events", params={"limit": 20}))
    assert any(event["type"] == "command_done" for event in events)
