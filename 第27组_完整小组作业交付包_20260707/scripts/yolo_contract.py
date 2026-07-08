from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


SUPPORTED_LABELS = {"drone", "fire_extinguisher", "light_bulb"}

OPEN_VOCAB_PROMPTS = [
    "drone",
    "unmanned aerial vehicle",
    "quadcopter",
    "fire extinguisher",
    "extinguisher",
    "light bulb",
    "lamp bulb",
]

LABEL_ALIASES = {
    "drone": "drone",
    "uav": "drone",
    "unmanned aerial vehicle": "drone",
    "quadcopter": "drone",
    "fire extinguisher": "fire_extinguisher",
    "fire_extinguisher": "fire_extinguisher",
    "fire-extinguisher": "fire_extinguisher",
    "extinguisher": "fire_extinguisher",
    "light bulb": "light_bulb",
    "light_bulb": "light_bulb",
    "light-bulb": "light_bulb",
    "lightbulb": "light_bulb",
    "lamp bulb": "light_bulb",
    "lamp": "light_bulb",
}


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_ultralytics_runtime_env() -> None:
    """Keep Ultralytics settings inside the package instead of AppData/Home."""
    config_dir = package_root() / "runtime" / "ultralytics_config"
    config_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(config_dir))


def default_custom_model_path() -> Path:
    model_dir = package_root() / "models"
    preferred_models = [
        "group27_v3_balanced_lowlr_yolov8m_960_candidate.pt",
        "group27_v2_three_class_best.pt",
    ]
    for name in preferred_models:
        model_path = model_dir / name
        if model_path.exists():
            return model_path
    return model_dir / "group27_all_three_drone_fire_lightbulb_best.pt"


def default_open_vocab_model_path() -> Path:
    local = package_root() / "models" / "yoloe-26s-seg.pt"
    return local if local.exists() else Path("yoloe-26s-seg.pt")


def ensure_mobileclip_weight() -> None:
    """Make YOLOE's MobileCLIP text encoder discoverable for set_classes()."""
    bundled = package_root() / "models" / "mobileclip2_b.ts"
    if not bundled.exists():
        return
    target = Path.cwd() / "mobileclip2_b.ts"
    if target.exists() and target.stat().st_size == bundled.stat().st_size:
        return
    try:
        shutil.copy2(bundled, target)
    except OSError:
        pass


def normalize_label(label: str | int) -> str:
    key = str(label).strip().lower().replace("-", " ").replace("_", " ")
    return LABEL_ALIASES.get(key, key.replace(" ", "_"))


def normalize_detection(raw_label: str | int, confidence: float) -> dict[str, Any] | None:
    label = normalize_label(raw_label)
    if label not in SUPPORTED_LABELS:
        return None
    return {"label": label, "confidence": round(float(confidence), 4)}


def dedupe_labels(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for item in labels:
        label = normalize_label(item.get("label", ""))
        if label not in SUPPORTED_LABELS:
            continue
        confidence = round(float(item.get("confidence", 0.0)), 4)
        normalized = {"label": label, "confidence": confidence}
        if label not in best or confidence > float(best[label]["confidence"]):
            best[label] = normalized
    return sorted(best.values(), key=lambda item: float(item["confidence"]), reverse=True)


def commands_from_yolo_labels(
    yolo_labels: list[dict[str, Any]],
    *,
    light_threshold: float = 0.55,
    device_id: str = "orange-pi-main",
    light_level: int = 80,
) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for item in dedupe_labels(yolo_labels):
        if item["label"] == "light_bulb" and float(item["confidence"]) >= light_threshold:
            commands.append(
                {
                    "device_id": device_id,
                    "type": "SET_LIGHT",
                    "payload": {"level": int(light_level)},
                    "reason": "detected_light_bulb",
                    "source_confidence": item["confidence"],
                }
            )
    return commands


def ok_response(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def error_response(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": {"code": code, "message": message}}
