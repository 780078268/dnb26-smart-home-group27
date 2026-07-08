from __future__ import annotations

import os
import random
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
MODEL_DIR = ROOT_DIR / "models"

DISPLAY_CONFIDENCE = float(os.getenv("YOLO_DISPLAY_CONFIDENCE", "0.45"))
LIGHT_COMMAND_CONFIDENCE = float(os.getenv("YOLO_LIGHT_COMMAND_CONFIDENCE", "0.55"))
LIGHT_COMMAND_LEVEL = int(os.getenv("YOLO_LIGHT_COMMAND_LEVEL", "80"))
DEFAULT_GROUP27_MODEL = MODEL_DIR / "group27_v3_balanced_lowlr_yolov8m_960_candidate.pt"
YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH", str(DEFAULT_GROUP27_MODEL))
YOLO_FALLBACK_MODEL = os.getenv("YOLO_FALLBACK_MODEL", "yolov8n.pt")
YOLO_IMAGE_SIZE = int(os.getenv("YOLO_IMAGE_SIZE", "960"))
YOLO_AUGMENT = os.getenv("YOLO_AUGMENT", "0").lower() in {"1", "true", "yes", "on"}
YOLO_DEVICE = os.getenv("YOLO_DEVICE") or None
YOLO_USE_REAL_MODEL = os.getenv("YOLO_USE_REAL_MODEL", "1").lower() not in {"0", "false", "no"}
YOLO_CONFIG_DIR = os.getenv("YOLO_CONFIG_DIR", str(DATA_DIR / "ultralytics"))
Path(YOLO_CONFIG_DIR).mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YOLO_CONFIG_DIR", YOLO_CONFIG_DIR)

MODEL_LABEL_ALIASES = {
    "drone": "drone",
    "uav": "drone",
    "unmanned aerial vehicle": "drone",
    "fire extinguisher": "fire_extinguisher",
    "fire_extinguisher": "fire_extinguisher",
    "extinguisher": "fire_extinguisher",
    "light bulb": "light_bulb",
    "lightbulb": "light_bulb",
    "lamp": "light_bulb",
    "vehicle": "car",
    "automobile": "car",
    "motorcar": "car",
    "person": "person",
    "car": "car",
    "light_bulb": "light_bulb",
    "light-bulb": "light_bulb",
    "fire-extinguisher": "fire_extinguisher",
}

GROUP27_LABELS = {"drone", "fire_extinguisher", "light_bulb"}
DETECTION_LABELS = GROUP27_LABELS | {"person", "car"}
LIGHT_KEYWORDS = ("light", "lamp", "bulb", "led", "灯", "灯泡", "台灯")
DRONE_KEYWORDS = ("drone", "uav", "quadcopter", "无人机", "航拍")
EXTINGUISHER_KEYWORDS = ("fire_extinguisher", "fire extinguisher", "extinguisher", "灭火器")
CAR_KEYWORDS = ("car", "vehicle", "auto", "车", "汽车")
PERSON_KEYWORDS = ("person", "people", "face", "human", "人", "owner", "family")


def _normalize_label(label: str) -> str:
    key = label.strip().lower().replace("-", " ").replace("_", " ")
    return MODEL_LABEL_ALIASES.get(key, key.replace(" ", "_"))


def _resolve_candidate_model(candidate: Path) -> Path | str | None:
    if candidate.is_absolute():
        return candidate if candidate.exists() else None

    project_path = ROOT_DIR / candidate
    if project_path.exists():
        return project_path

    if candidate.exists():
        return candidate

    # Keep Ultralytics built-in names such as yolov8n.pt available as a final fallback.
    if candidate.suffix == ".pt" and len(candidate.parts) == 1:
        return str(candidate)
    return None


def _model_candidates() -> list[Path]:
    candidates = [
        Path(YOLO_MODEL_PATH),
        DEFAULT_GROUP27_MODEL,
        MODEL_DIR / "best.pt",
        MODEL_DIR / "required_objects_all_best.pt",
        Path(YOLO_FALLBACK_MODEL),
    ]
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


@lru_cache(maxsize=1)
def _load_yolo_model() -> Any | None:
    if not YOLO_USE_REAL_MODEL:
        return None

    try:
        from ultralytics import YOLO
    except Exception:
        return None

    for candidate in _model_candidates():
        resolved = _resolve_candidate_model(candidate)
        if resolved is None:
            continue
        try:
            model = YOLO(str(resolved))
            setattr(model, "_group27_model_path", str(resolved))
            return model
        except Exception:
            continue
    return None


def detect_objects(image_path: str | Path, confidence: float = DISPLAY_CONFIDENCE) -> list[dict[str, Any]]:
    """Return OpenAPI-ready YOLO labels for a local image.

    The function prefers a real Ultralytics model and falls back to the stable
    classroom demo recognizer if weights are missing or first-run downloads fail.
    """
    image_path = Path(image_path)
    model = _load_yolo_model()
    if model is None:
        return _demo_detect_objects(image_path, None, confidence)

    try:
        predict_kwargs: dict[str, Any] = {
            "conf": confidence,
            "imgsz": YOLO_IMAGE_SIZE,
            "augment": YOLO_AUGMENT,
            "verbose": False,
        }
        if YOLO_DEVICE:
            predict_kwargs["device"] = YOLO_DEVICE
        results = model.predict(str(image_path), **predict_kwargs)
    except Exception:
        return _demo_detect_objects(image_path, None, confidence)

    labels: list[dict[str, Any]] = []
    for result in results:
        names = result.names or {}
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue
        for box in boxes:
            cls_id = int(box.cls[0])
            score = float(box.conf[0])
            label = _normalize_label(str(names.get(cls_id, cls_id)))
            if label not in DETECTION_LABELS:
                continue
            if score < confidence:
                continue
            labels.append(
                {
                    "label": label,
                    "confidence": round(score, 4),
                    "box": _normalized_box(box, result),
                }
            )

    labels = _dedupe_labels(labels)
    if not labels and _resolve_candidate_model(Path(YOLO_MODEL_PATH)) is None:
        return _demo_detect_objects(image_path, None, confidence)
    return labels


def analyze_image(image_path: Path, original_name: str | None = None) -> dict[str, Any]:
    image_path = Path(image_path)
    labels = detect_objects(image_path, DISPLAY_CONFIDENCE)
    image_meta = _image_metadata(image_path)

    if labels:
        engine = _runtime_engine_name()
        objects = [
            {
                "label": item["label"],
                "confidence": item["confidence"],
                "box": item.get("box", [0.18, 0.16, 0.78, 0.82]),
            }
            for item in labels
        ]
    else:
        engine = "demo-yolo-compatible"
        objects = _demo_objects(image_path, original_name)
        labels = [{"label": item["label"], "confidence": item["confidence"]} for item in objects]

    top = labels[0] if labels else {"label": "unknown", "confidence": 0.0}
    return {
        "engine": engine,
        "objects": objects,
        "labels": labels,
        "image": image_meta,
        "summary": f"Detected {top['label']} with confidence {top['confidence']}",
    }


def yolo_labels_from_detection(detection: dict[str, Any]) -> list[dict[str, Any]]:
    if detection.get("labels"):
        return [
            {"label": item.get("label", "unknown"), "confidence": float(item.get("confidence", 0))}
            for item in detection["labels"]
        ]
    return [
        {"label": item.get("label", "unknown"), "confidence": float(item.get("confidence", 0))}
        for item in detection.get("objects", [])
    ]


def should_turn_on_light(detection: dict[str, Any]) -> bool:
    return light_command_from_detection(detection) is not None


def commands_from_yolo_labels(
    yolo_labels: list[dict[str, Any]],
    *,
    light_threshold: float = LIGHT_COMMAND_CONFIDENCE,
    device_id: str = "orange-pi-main",
    light_level: int = LIGHT_COMMAND_LEVEL,
) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for item in _dedupe_labels(yolo_labels):
        if item.get("label") == "light_bulb" and float(item.get("confidence", 0)) >= light_threshold:
            commands.append(
                {
                    "device_id": device_id,
                    "type": "SET_LIGHT",
                    "payload": {"level": int(light_level)},
                    "reason": "detected_light_bulb",
                    "source_confidence": round(float(item.get("confidence", 0)), 4),
                }
            )
    return commands


def light_command_from_detection(
    detection: dict[str, Any],
    *,
    device_id: str = "orange-pi-main",
) -> dict[str, Any] | None:
    commands = commands_from_yolo_labels(yolo_labels_from_detection(detection), device_id=device_id)
    return commands[0] if commands else None


def recognize_face(face_code: str | None, people: list[dict[str, Any]]) -> dict[str, Any]:
    face_code = (face_code or "").strip()
    if not face_code:
        return {
            "matched": False,
            "authorized": False,
            "name": "Unknown",
            "face_code": None,
            "message": "No face code supplied",
        }

    for person in people:
        if person["face_code"] == face_code:
            return {
                "matched": True,
                "authorized": bool(person["is_authorized"]),
                "name": person["name"],
                "role": person["role"],
                "face_code": face_code,
                "message": "Access granted" if person["is_authorized"] else "Access denied",
            }

    return {
        "matched": False,
        "authorized": False,
        "name": "Unknown",
        "face_code": face_code,
        "message": "Access denied",
    }


def _dedupe_labels(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for item in labels:
        label = _normalize_label(str(item.get("label", "")))
        if label not in DETECTION_LABELS:
            continue
        confidence = round(float(item.get("confidence", 0.0)), 4)
        normalized = {**item, "label": label, "confidence": confidence}
        if label not in best or confidence > float(best[label]["confidence"]):
            best[label] = normalized
    return sorted(best.values(), key=lambda item: item["confidence"], reverse=True)


def _normalized_box(box: Any, result: Any) -> list[float]:
    try:
        height, width = result.orig_shape[:2]
        x1, y1, x2, y2 = [float(value) for value in box.xyxy[0].detach().cpu().tolist()]
        return [
            round(max(0.0, min(1.0, x1 / width)), 4),
            round(max(0.0, min(1.0, y1 / height)), 4),
            round(max(0.0, min(1.0, x2 / width)), 4),
            round(max(0.0, min(1.0, y2 / height)), 4),
        ]
    except Exception:
        return [0.18, 0.16, 0.78, 0.82]


def _runtime_engine_name() -> str:
    model = _load_yolo_model()
    model_path = Path(str(getattr(model, "_group27_model_path", YOLO_MODEL_PATH))).name if model is not None else ""
    if model_path == DEFAULT_GROUP27_MODEL.name:
        return "group27-yolov8m-custom"
    if model_path:
        return f"ultralytics-yolo:{model_path}"
    return "ultralytics-yolo"


def _image_metadata(image_path: Path) -> dict[str, Any]:
    width = 0
    height = 0
    brightness = 0.0
    try:
        with Image.open(image_path) as img:
            width, height = img.size
            stat = ImageStat.Stat(img.convert("L"))
            brightness = float(stat.mean[0])
    except Exception:
        pass
    return {"width": width, "height": height, "brightness": round(brightness, 2)}


def _demo_detect_objects(
    image_path: Path,
    original_name: str | None = None,
    confidence: float = DISPLAY_CONFIDENCE,
) -> list[dict[str, Any]]:
    return [
        {"label": item["label"], "confidence": item["confidence"]}
        for item in _demo_objects(image_path, original_name)
        if item["confidence"] >= confidence
    ]


def _demo_objects(image_path: Path, original_name: str | None = None) -> list[dict[str, Any]]:
    image_path = Path(image_path)
    name = f"{image_path.name} {original_name or ''}".lower()
    meta = _image_metadata(image_path)
    width = meta["width"]
    height = meta["height"]
    brightness = meta["brightness"]

    label = "unknown"
    confidence = 0.55
    if any(word in name for word in DRONE_KEYWORDS):
        label = "drone"
        confidence = 0.84
    elif any(word in name for word in EXTINGUISHER_KEYWORDS):
        label = "fire_extinguisher"
        confidence = 0.85
    elif any(word in name for word in CAR_KEYWORDS):
        label = "car"
        confidence = 0.83
    elif any(word in name for word in PERSON_KEYWORDS):
        label = "person"
        confidence = 0.86
    elif any(word in name for word in LIGHT_KEYWORDS):
        label = "light_bulb"
        confidence = 0.88
    elif brightness > 205:
        label = "light_bulb"
        confidence = 0.71
    elif width and height and height > width * 1.15:
        label = "person"
        confidence = 0.68
    elif width and height and width > height * 1.35:
        label = "car"
        confidence = 0.64

    jitter = random.Random(image_path.name).uniform(-0.025, 0.025)
    confidence = round(max(0.5, min(0.98, confidence + jitter)), 2)
    return [{"label": label, "confidence": confidence, "box": [0.18, 0.16, 0.78, 0.82]}]
