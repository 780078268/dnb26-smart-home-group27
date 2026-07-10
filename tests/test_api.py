from __future__ import annotations

import base64
import io
import sys
import zipfile
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


SAMPLE_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "2wBDAf//////////////////////////////////////////////////////////////////////////////////////"
    "wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/"
    "xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/ASP/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oA"
    "CAICAQE/ASP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Al//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/Iqf/2gAMAwEAAgADAAAAEP/E"
    "FBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EFBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EFBABAQAAAAAAAAAAAAAAAAAAARD/2gAI"
    "AQEABj8QH//Z"
)


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
            files={"image": ("face.jpg", SAMPLE_JPEG, "image/jpeg")},
        )
    )
    assert enrolled["face_enrolled"] is True

    photo = assert_ok(
        client.post(
            "/api/device/photos",
            data={
                "device_id": "orange-pi-main",
                "yolo_labels_json": '[{"label":"person","confidence":0.91},{"label":"light bulb","confidence":0.78}]',
                "source": "auto_face",
            },
            files={"image": ("photo.jpg", SAMPLE_JPEG, "image/jpeg")},
        )
    )
    assert photo["access_decision"] == "allow"
    assert photo["yolo_labels"][0]["label"] == "person"
    assert photo["file_url"].startswith("/uploads/latest/")
    assert photo["history_saved"] is True
    assert photo["history_file_url"].startswith("/uploads/photos/")

    latest_photo = assert_ok(client.get("/api/photos/latest", params={"device_id": "orange-pi-main"}))
    assert latest_photo["file_url"] == photo["file_url"]
    assert latest_photo["source"] == "auto_face"

    duplicate_photo = assert_ok(
        client.post(
            "/api/device/photos",
            data={
                "device_id": "orange-pi-main",
                "yolo_labels_json": '[{"label":"person","confidence":0.92}]',
                "source": "auto_face",
            },
            files={"image": ("photo.jpg", SAMPLE_JPEG, "image/jpeg")},
        )
    )
    assert duplicate_photo["history_saved"] is False

    historical_photos = assert_ok(client.get("/api/photos", params={"limit": 10}))
    assert len(historical_photos) == 1
    assert historical_photos[0]["event_key"].startswith("face:person:")

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


def test_face_library_sync_and_orange_pi_capture_flow(client):
    person = assert_ok(
        client.post(
            "/api/persons",
            json={"name": "Family A", "role": "student", "authorized": True},
        )
    )

    enrolled = assert_ok(
        client.post(
            f"/api/persons/{person['id']}/face-samples",
            files={"image": ("family-a.jpg", SAMPLE_JPEG, "image/jpeg")},
        )
    )
    first_sample = enrolled["sample"]
    assert first_sample["sync_version"] == 1
    assert first_sample["image_url"].startswith("http://82.156.238.244/uploads/faces/")

    sync = assert_ok(
        client.get(
            "/api/device/face-library/sync",
            params={"device_id": "orange-pi-main", "since_version": 0},
        )
    )
    assert sync["to_version"] == first_sample["sync_version"]
    assert sync["has_more"] is False
    assert sync["changes"][0]["change_type"] == "upsert"
    assert sync["changes"][0]["member_name"] == "Family A"
    assert sync["changes"][0]["authorized"] is True

    ack = assert_ok(
        client.post(
            "/api/device/face-library/ack",
            json={"device_id": "orange-pi-main", "synced_version": sync["to_version"], "message": "ok"},
        )
    )
    assert ack["synced_version"] == sync["to_version"]

    removed_trigger = client.post(
        f"/api/persons/{person['id']}/face-capture-request",
        json={"device_id": "orange-pi-main"},
    )
    assert removed_trigger.status_code == 404

    captured = assert_ok(
        client.post(
            "/api/device/face-captures",
            data={
                "device_id": "orange-pi-main",
                "person_id": person["id"],
            },
            files={"image": ("orange-pi-face.jpg", SAMPLE_JPEG, "image/jpeg")},
        )
    )
    assert captured["face_enrolled"] is True
    assert captured["sample"]["sync_version"] > sync["to_version"]

    second_sync = assert_ok(
        client.get(
            "/api/device/face-library/sync",
            params={"device_id": "orange-pi-main", "since_version": sync["to_version"]},
        )
    )
    assert len(second_sync["changes"]) == 1
    assert second_sync["changes"][0]["face_sample_id"] == captured["sample"]["id"]

    updated = assert_ok(client.patch(f"/api/persons/{person['id']}", json={"authorized": False}))
    assert updated["authorized"] is False
    third_sync = assert_ok(
        client.get(
            "/api/device/face-library/sync",
            params={"device_id": "orange-pi-main", "since_version": second_sync["to_version"]},
        )
    )
    assert {change["authorized"] for change in third_sync["changes"]} == {False}
    assert {change["change_type"] for change in third_sync["changes"]} == {"upsert"}

    deleted = assert_ok(client.delete(f"/api/persons/{person['id']}"))
    assert deleted["deleted"] is True
    delete_sync = assert_ok(
        client.get(
            "/api/device/face-library/sync",
            params={"device_id": "orange-pi-main", "since_version": third_sync["to_version"]},
        )
    )
    assert {change["change_type"] for change in delete_sync["changes"]} == {"delete"}


def test_active_detection_command_and_event_upload(client):
    command = assert_ok(
        client.post(
            "/api/commands",
            json={
                "device_id": "orange-pi-main",
                "type": "REQUEST_DETECT_DRONE",
                "payload": {"target": "drone", "upload_mode": "event"},
            },
        )
    )
    assert command["status"] == "pending"

    pending = assert_ok(
        client.get("/api/device/commands/pending", params={"device_id": "orange-pi-main"})
    )
    assert any(item["type"] == "REQUEST_DETECT_DRONE" for item in pending)

    result = assert_ok(
        client.post(
            "/api/device/photos",
            data={
                "device_id": "orange-pi-main",
                "yolo_labels_json": '[{"label":"drone","confidence":0.88}]',
                "source": "drone",
                "mode": "event",
            },
            files={"image": ("drone.jpg", SAMPLE_JPEG, "image/jpeg")},
        )
    )
    assert result["history_saved"] is True
    assert result["event_key"] == "active:drone:drone"

    duplicate = assert_ok(
        client.post(
            "/api/device/photos",
            data={
                "device_id": "orange-pi-main",
                "yolo_labels_json": '[{"label":"drone","confidence":0.89}]',
                "source": "drone",
                "mode": "event",
            },
            files={"image": ("drone.jpg", SAMPLE_JPEG, "image/jpeg")},
        )
    )
    assert duplicate["history_saved"] is False

    history = assert_ok(client.get("/api/photos", params={"limit": 10}))
    assert len(history) == 1
    assert history[0]["source"] == "drone"


def make_zip(*names):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, SAMPLE_JPEG)
    buffer.seek(0)
    return buffer.getvalue()


def test_detection_package_upload_command_and_results(client):
    created = assert_ok(
        client.post(
            "/api/detection-jobs",
            data={"device_id": "orange-pi-main"},
            files=[
                ("fire_extinguisher_images", ("fire-1.jpg", SAMPLE_JPEG, "image/jpeg")),
                ("drone_images", ("drone-1.jpg", SAMPLE_JPEG, "image/jpeg")),
                ("drone_images", ("drone-2.png", SAMPLE_JPEG, "image/png")),
            ],
        )
    )
    assert created["status"] == "queued"
    assert created["expected_fire_count"] == 1
    assert created["expected_drone_count"] == 2
    assert created["total_count"] == 3
    assert created["command"]["type"] == "PROCESS_DETECTION_PACKAGE"
    assert created["command"]["payload"]["job_id"] == created["id"]

    pending = assert_ok(client.get("/api/device/commands/pending", params={"device_id": "orange-pi-main"}))
    package_command = next(command for command in pending if command["type"] == "PROCESS_DETECTION_PACKAGE")
    assert len(package_command["payload"]["items"]) == 3

    pulled_job = assert_ok(client.get(f"/api/detection-jobs/{created['id']}"))
    assert pulled_job["status"] == "processing"
    assert all(item["status"] == "processing" for item in pulled_job["items"])

    result_items = [
        {
            "item_id": item["id"],
            "status": "done",
            "yolo_labels": [{"label": item["expected_label"], "confidence": 0.9}],
        }
        for item in pulled_job["items"]
    ]
    completed = assert_ok(
        client.post(
            f"/api/device/detection-jobs/{created['id']}/results",
            json={"device_id": "orange-pi-main", "items": result_items},
        )
    )
    assert completed["status"] == "done"
    assert completed["completed_count"] == 3
    assert completed["failed_count"] == 0
    assert completed["items"][0]["yolo_labels"][0]["confidence"] == 0.9


def test_detection_package_accepts_zip_files(client):
    fire_zip = make_zip("fire/a.jpg", "fire/b.png", "notes.txt")
    drone_zip = make_zip("drone/a.jpg")
    created = assert_ok(
        client.post(
            "/api/detection-jobs",
            data={"device_id": "orange-pi-main"},
            files=[
                ("fire_extinguisher_zip", ("fire.zip", fire_zip, "application/zip")),
                ("drone_zip", ("drone.zip", drone_zip, "application/zip")),
            ],
        )
    )
    assert created["expected_fire_count"] == 2
    assert created["expected_drone_count"] == 1
    assert created["total_count"] == 3
    assert all(item["file_url"].startswith(f"/uploads/detection_jobs/{created['id']}/") for item in created["items"])
