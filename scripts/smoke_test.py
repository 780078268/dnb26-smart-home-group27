from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import tempfile

import httpx


SAMPLE_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "2wBDAf//////////////////////////////////////////////////////////////////////////////////////"
    "wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/"
    "xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/ASP/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oA"
    "CAICAQE/ASP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Al//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/Iqf/2gAMAwEAAgADAAAAEP/E"
    "FBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EFBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EFBABAQAAAAAAAAAAAAAAAAAAARD/2gAI"
    "AQEABj8QH//Z"
)


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
            image.write(SAMPLE_JPEG)
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

