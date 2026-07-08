from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

import httpx


def require_ok(response: httpx.Response) -> dict:
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(body)
    return body["data"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test the Group 27 smart home backend.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    with httpx.Client(timeout=10) as client:
        print("health", require_ok(client.get(f"{base_url}/api/health")))

        telemetry = {
            "device_id": "orange-pi-main",
            "temperature_c": 28.4,
            "door_open": False,
            "window_open": True,
            "light_level": 70,
            "fan_on": True,
        }
        print("telemetry", require_ok(client.post(f"{base_url}/api/device/telemetry", json=telemetry)))

        command = {
            "device_id": "orange-pi-main",
            "type": "SET_LIGHT",
            "payload": {"level": 80},
        }
        created = require_ok(client.post(f"{base_url}/api/commands", json=command))
        print("command", created)

        with tempfile.NamedTemporaryFile(suffix=".jpg") as image:
            image.write(b"fake-photo-image")
            image.flush()
            labels = [{"label": "person", "confidence": 0.91}]
            with Path(image.name).open("rb") as fh:
                photo = require_ok(
                    client.post(
                        f"{base_url}/api/device/photos",
                        data={
                            "device_id": "orange-pi-main",
                            "yolo_labels_json": json.dumps(labels),
                        },
                        files={"image": ("smoke.jpg", fh, "image/jpeg")},
                    )
                )
            print("photo", photo)

        pending = require_ok(
            client.get(f"{base_url}/api/device/commands/pending", params={"device_id": "orange-pi-main"})
        )
        print("pending", pending)


if __name__ == "__main__":
    main()

