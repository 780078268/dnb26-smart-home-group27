from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


DEVICE_ID = "orange-pi-main"


def post_telemetry(server: str) -> None:
    payload = {
        "device_id": DEVICE_ID,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "temperature_c": 28.0,
        "door_open": False,
        "window_open": False,
        "light_level": 50,
        "fan_on": False,
    }
    response = requests.post(f"{server}/api/device/telemetry", json=payload, timeout=15)
    response.raise_for_status()
    print("telemetry uploaded", response.json())


def upload_photo(server: str, image_path: Path) -> None:
    with image_path.open("rb") as file:
        response = requests.post(
            f"{server}/api/device/photos",
            files={"image": (image_path.name, file, "image/jpeg")},
            data={"device_id": DEVICE_ID, "captured_at": datetime.now(timezone.utc).isoformat()},
            timeout=120,
        )
    response.raise_for_status()
    print("photo uploaded", response.json())


def execute_command(command: dict[str, Any]) -> str:
    command_type = command.get("type")
    payload = command.get("payload") or {}
    if command_type == "SET_LIGHT":
        print(f"set light level to {payload.get('level', 0)}")
    elif command_type == "SET_FAN":
        print(f"set fan on={bool(payload.get('on'))}")
    elif command_type == "OPEN_DOOR":
        print("open door")
    elif command_type == "CLOSE_DOOR":
        print("close door")
    else:
        print(f"received command {command_type}")
    return "done"


def poll_and_ack(server: str) -> None:
    response = requests.get(
        f"{server}/api/device/commands/pending",
        params={"device_id": DEVICE_ID},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    commands = payload.get("data", payload)
    if not commands:
        print("no pending commands")
        return

    for command in commands:
        status = execute_command(command)
        ack = requests.post(
            f"{server}/api/device/commands/{command['id']}/ack",
            json={"device_id": DEVICE_ID, "status": status, "message": "Orange Pi demo client ack"},
            timeout=15,
        )
        ack.raise_for_status()
        print("acked command", command["id"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Orange Pi hardware client for Group27 smart home demo")
    parser.add_argument("--server", required=True, help="Backend server, for example http://192.168.1.23:8000")
    parser.add_argument("--photo", type=Path, help="Optional camera photo to upload once")
    parser.add_argument("--loop", action="store_true", help="Keep uploading telemetry and polling commands")
    parser.add_argument("--interval", type=float, default=5.0, help="Loop interval seconds")
    args = parser.parse_args()

    server = args.server.rstrip("/")
    while True:
        post_telemetry(server)
        if args.photo:
            upload_photo(server, args.photo)
            args.photo = None
        poll_and_ack(server)
        if not args.loop:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
