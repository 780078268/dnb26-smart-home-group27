from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw


ROOT_DIR = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT_DIR / "docs" / "iot_integration_checklist.md"
SAMPLE_DIR = ROOT_DIR / "data" / "iot_test_samples"
SAMPLE_IMAGE = SAMPLE_DIR / "light_bulb_demo.jpg"
BASE_URL = "http://127.0.0.1:8000"
DEVICE_ID = "orange-pi-main"


def ensure_sample_image() -> Path:
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (360, 260), "#f7f2d0")
    draw = ImageDraw.Draw(image)
    draw.ellipse((135, 35, 225, 125), fill="#fff6a8", outline="#d5a500", width=4)
    draw.rectangle((160, 122, 200, 175), fill="#5d6770")
    draw.line((158, 142, 202, 142), fill="#d9e0e6", width=3)
    draw.line((158, 157, 202, 157), fill="#d9e0e6", width=3)
    draw.text((98, 204), "light bulb demo", fill="#34433f")
    image.save(SAMPLE_IMAGE, "JPEG", quality=92)
    return SAMPLE_IMAGE


def request_json(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    response = requests.request(method, f"{BASE_URL}{path}", timeout=30, **kwargs)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError(json.dumps(data, ensure_ascii=False))
    return data


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_acceptance() -> dict[str, Any]:
    health = request_json("GET", "/api/health")
    devices = request_json("GET", "/api/devices")["data"]
    assert_true(any(item["id"] == DEVICE_ID for item in devices), "device list does not include orange-pi-main")

    telemetry_results = []
    for idx in range(5):
        payload = {
            "device_id": DEVICE_ID,
            "captured_at": f"2026-07-07T13:{idx:02d}:00+08:00",
            "temperature_c": 27.5 + idx,
            "door_open": idx % 2 == 0,
            "window_open": idx % 2 == 1,
            "light_level": 40 + idx * 10,
            "fan_on": idx >= 3,
        }
        telemetry_results.append(request_json("POST", "/api/device/telemetry", json=payload)["data"])

    latest = request_json("GET", "/api/status/latest")["data"]
    history = request_json("GET", "/api/telemetry", params={"limit": 5, "device_id": DEVICE_ID})["data"]
    assert_true(latest["device_id"] == DEVICE_ID, "latest status device_id mismatch")
    assert_true(len(history) >= 5, "telemetry history returned fewer than 5 records")

    command = request_json(
        "POST",
        "/api/commands",
        json={"device_id": DEVICE_ID, "type": "SET_LIGHT", "payload": {"level": 80}},
    )["data"]
    pending = request_json("GET", "/api/device/commands/pending", params={"device_id": DEVICE_ID})["data"]
    assert_true(any(item["id"] == command["id"] for item in pending), "created command is not pending")

    ack = request_json(
        "POST",
        f"/api/device/commands/{command['id']}/ack",
        json={"device_id": DEVICE_ID, "status": "done", "message": "integration test ack"},
    )["data"]
    assert_true(ack["command"]["status"] == "done", "command ack did not mark command done")

    sample_image = ensure_sample_image()
    with sample_image.open("rb") as file:
        photo = request_json(
            "POST",
            "/api/device/photos",
            data={"device_id": DEVICE_ID, "captured_at": "2026-07-07T13:10:00+08:00"},
            files={"image": ("light_bulb_demo.jpg", file, "image/jpeg")},
        )["data"]
    assert_true(photo["file_url"].startswith("/uploads/"), "photo upload did not return file_url")
    assert_true(isinstance(photo["yolo_labels"], list), "photo upload did not return yolo labels")

    events = request_json("GET", "/api/events", params={"limit": 20})["data"]
    event_types = {item["type"] for item in events}
    assert_true({"telemetry", "command", "photo"}.issubset(event_types), "events do not include telemetry, command, and photo")

    return {
        "health": health,
        "devices": devices,
        "telemetry_count": len(telemetry_results),
        "latest": latest,
        "history_count": len(history),
        "command": command,
        "ack": ack,
        "photo": photo,
        "event_types": sorted(event_types),
    }


def write_doc(summary: dict[str, Any]) -> None:
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 物联接口闭环验收记录",
        "",
        "## 验收范围",
        "",
        "- 设备列表：`GET /api/devices`",
        "- 最新状态：`GET /api/status/latest`",
        "- 设备状态上报：`POST /api/device/telemetry`",
        "- 历史状态查询：`GET /api/telemetry`",
        "- GUI/后端下发命令：`POST /api/commands`",
        "- 硬件拉取命令：`GET /api/device/commands/pending`",
        "- 硬件确认执行：`POST /api/device/commands/{id}/ack`",
        "- 图片上传链路：`POST /api/device/photos`",
        "- 事件流：`GET /api/events`",
        "",
        "## 自动验收结果",
        "",
        f"- 设备 ID：`{DEVICE_ID}`",
        f"- 状态上报数量：{summary['telemetry_count']}",
        f"- 历史查询返回数量：{summary['history_count']}",
        f"- 测试命令：`{summary['command']['type']}`，状态从 `pending` 变为 `{summary['ack']['command']['status']}`",
        f"- 图片保存地址：`{summary['photo']['file_url']}`",
        f"- 图片识别标签：{json.dumps(summary['photo']['yolo_labels'], ensure_ascii=False)}",
        f"- 事件类型：{', '.join(summary['event_types'])}",
        "",
        "## 结论",
        "",
        "- 物联接口闭环：通过",
        "- 硬件组可以按接口文档直接联调设备状态、图片、命令拉取和 ack。",
        "- YOLO 最终模型已接入 `models/group27_v3_balanced_lowlr_yolov8m_960_candidate.pt`；后续继续训练时只要保持 `drone`、`fire_extinguisher`、`light_bulb` 字段合同不变即可。",
    ]
    DOC_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    try:
        summary = run_acceptance()
    except requests.ConnectionError:
        print("Backend is not running. Start it first: .\\.venv\\Scripts\\python.exe backend\\server.py", file=sys.stderr)
        raise SystemExit(2)
    write_doc(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
