from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .yolo_contract import default_custom_model_path
except ImportError:
    from yolo_contract import default_custom_model_path


LABEL_ALIASES = {
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
}

ALLOWED_LABELS = {"drone", "fire_extinguisher", "light_bulb", "person", "car"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run YOLO prediction and print group-contract JSON labels.")
    parser.add_argument("source", help="Image file or directory.")
    parser.add_argument("--model", default=str(default_custom_model_path()))
    parser.add_argument("--conf", type=float, default=0.45)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--augment", action="store_true", help="Use test-time augmentation for a slower but steadier demo.")
    parser.add_argument("--save", action="store_true", help="Save annotated prediction images under runs/predict.")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    model_path = Path(args.model)
    if not model_path.is_absolute():
        model_path = root / model_path

    from ultralytics import YOLO

    model = YOLO(str(model_path if model_path.exists() else args.model))
    results = model.predict(args.source, conf=args.conf, imgsz=args.imgsz, augment=args.augment, save=args.save, verbose=False)
    payload = []
    for result in results:
        image_labels = []
        names = result.names or {}
        boxes = getattr(result, "boxes", None)
        if boxes is not None:
            for box in boxes:
                raw_label = str(names.get(int(box.cls[0]), int(box.cls[0])))
                label = normalize_label(raw_label)
                if label not in ALLOWED_LABELS:
                    continue
                image_labels.append({"label": label, "confidence": round(float(box.conf[0]), 4)})
        payload.append({"image": str(result.path), "yolo_labels": dedupe(image_labels)})

    print(json.dumps(payload, ensure_ascii=False, indent=2))


def normalize_label(label: str) -> str:
    key = label.strip().lower().replace("-", " ").replace("_", " ")
    return LABEL_ALIASES.get(key, key.replace(" ", "_"))


def dedupe(labels: list[dict[str, float | str]]) -> list[dict[str, float | str]]:
    best: dict[str, dict[str, float | str]] = {}
    for item in labels:
        label = str(item["label"])
        if label not in best or float(item["confidence"]) > float(best[label]["confidence"]):
            best[label] = item
    return sorted(best.values(), key=lambda item: float(item["confidence"]), reverse=True)


if __name__ == "__main__":
    main()
