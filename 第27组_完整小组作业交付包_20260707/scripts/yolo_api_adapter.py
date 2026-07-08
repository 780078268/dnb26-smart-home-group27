from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from .yolo_contract import commands_from_yolo_labels
    from .yolo_service import Group27YoloService
except ImportError:
    from yolo_contract import commands_from_yolo_labels
    from yolo_service import Group27YoloService


_SERVICE: Group27YoloService | None = None


def get_yolo_service() -> Group27YoloService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = Group27YoloService(mode="custom")
    return _SERVICE


def recognize_photo_for_api(
    image_path: str | Path,
    *,
    device_id: str = "orange-pi-main",
    captured_at: str | None = None,
    file_url: str | None = None,
    conf: float = 0.45,
    imgsz: int = 960,
    augment: bool = False,
) -> dict[str, Any]:
    image_path = Path(image_path)
    captured_at = captured_at or datetime.now(timezone.utc).isoformat()
    yolo_labels = get_yolo_service().detect(image_path, conf=conf, imgsz=imgsz, augment=augment)
    return {
        "photo": {
            "id": f"photo_{uuid4().hex[:8]}",
            "device_id": device_id,
            "captured_at": captured_at,
            "file_url": file_url or str(image_path),
            "yolo_labels": yolo_labels,
            "face_result": None,
            "access_decision": "unknown",
        },
        "commands": commands_from_yolo_labels(yolo_labels, device_id=device_id),
    }
