from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
AUTHORIZED_FACE_DIR = DATA_DIR / "authorized_faces"
FACE_TEST_DIR = DATA_DIR / "face_test_samples"
PROFILE_PATH = AUTHORIZED_FACE_DIR / "face_profiles.json"
MANIFEST_PATH = FACE_TEST_DIR / "manifest.json"

DEFAULT_THRESHOLD = float(os.getenv("FACE_MATCH_THRESHOLD", "0.55"))
FACE_MATCH_MARGIN = float(os.getenv("FACE_MATCH_MARGIN", "0.02"))
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_person_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    if not value:
        raise ValueError("person_id is required")
    return value[:40]


def safe_filename(value: str, fallback: str = "face.jpg") -> str:
    value = Path(value or fallback).name
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return value or fallback


def image_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)


def _cascade():
    if not hasattr(cv2, "CascadeClassifier"):
        return None
    path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    if not path.exists():
        return None
    cascade = cv2.CascadeClassifier(str(path))
    if hasattr(cascade, "empty") and cascade.empty():
        return None
    return cascade


def _largest_face(gray: np.ndarray) -> tuple[int, int, int, int] | None:
    cascade = _cascade()
    if cascade is None:
        return None
    faces = cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=4, minSize=(32, 32))
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda box: int(box[2]) * int(box[3]))
    return int(x), int(y), int(w), int(h)


def _face_crop(image_path: Path) -> tuple[np.ndarray, bool]:
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Cannot read image: {image_path}")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    box = _largest_face(gray)
    if box is None:
        height, width = gray.shape[:2]
        # LFW-funneled photos are already aligned; when Haar is unavailable,
        # this centered crop keeps the face while trimming background.
        side = int(min(width, height) * 0.82)
        x = max(0, (width - side) // 2)
        y = max(0, int((height - side) * 0.42))
        return gray[y : y + side, x : x + side], False

    x, y, w, h = box
    margin = int(max(w, h) * 0.22)
    x1 = max(0, x - margin)
    y1 = max(0, y - margin)
    x2 = min(gray.shape[1], x + w + margin)
    y2 = min(gray.shape[0], y + h + margin)
    return gray[y1:y2, x1:x2], True


def _normalize(vector: np.ndarray) -> np.ndarray:
    vector = vector.astype(np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-8:
        return vector
    return vector / norm


def _lbp_hist(gray64: np.ndarray) -> np.ndarray:
    center = gray64[1:-1, 1:-1]
    code = np.zeros(center.shape, dtype=np.uint8)
    neighbors = [
        gray64[:-2, :-2],
        gray64[:-2, 1:-1],
        gray64[:-2, 2:],
        gray64[1:-1, 2:],
        gray64[2:, 2:],
        gray64[2:, 1:-1],
        gray64[2:, :-2],
        gray64[1:-1, :-2],
    ]
    for idx, neighbor in enumerate(neighbors):
        code |= ((neighbor >= center).astype(np.uint8) << idx)
    hist, _ = np.histogram(code, bins=32, range=(0, 256), density=True)
    return hist.astype(np.float32)


def _gradient_hist(gray64: np.ndarray, cells: int = 4, bins: int = 9) -> np.ndarray:
    image = gray64.astype(np.float32) / 255.0
    gy, gx = np.gradient(image)
    magnitude = np.sqrt((gx * gx) + (gy * gy))
    angle = (np.arctan2(gy, gx) + np.pi) * (bins / (2 * np.pi))
    angle_bin = np.floor(angle).astype(np.int32) % bins
    cell_h = gray64.shape[0] // cells
    cell_w = gray64.shape[1] // cells
    histograms = []
    for row in range(cells):
        for col in range(cells):
            y1, y2 = row * cell_h, (row + 1) * cell_h
            x1, x2 = col * cell_w, (col + 1) * cell_w
            hist = np.bincount(
                angle_bin[y1:y2, x1:x2].reshape(-1),
                weights=magnitude[y1:y2, x1:x2].reshape(-1),
                minlength=bins,
            )
            histograms.append(hist.astype(np.float32))
    return np.concatenate(histograms)


def _region_stats(gray64: np.ndarray, grid: int = 8) -> np.ndarray:
    image = gray64.astype(np.float32) / 255.0
    cell_h = gray64.shape[0] // grid
    cell_w = gray64.shape[1] // grid
    values = []
    for row in range(grid):
        for col in range(grid):
            patch = image[row * cell_h : (row + 1) * cell_h, col * cell_w : (col + 1) * cell_w]
            values.extend([float(patch.mean()), float(patch.std())])
    return np.array(values, dtype=np.float32)


def extract_feature(image_path: str | Path) -> dict[str, Any]:
    image_path = Path(image_path)
    crop, face_detected = _face_crop(image_path)
    resized = cv2.resize(crop, (96, 96), interpolation=cv2.INTER_AREA)
    equalized = cv2.equalizeHist(resized)

    hog_img = cv2.resize(equalized, (64, 64), interpolation=cv2.INTER_AREA)
    if hasattr(cv2, "HOGDescriptor"):
        hog = cv2.HOGDescriptor((64, 64), (16, 16), (8, 8), (8, 8), 9).compute(hog_img).reshape(-1)
    else:
        hog = _gradient_hist(hog_img, cells=8, bins=9)
    small = cv2.resize(equalized, (36, 36), interpolation=cv2.INTER_AREA).astype(np.float32).reshape(-1)
    small = (small - float(small.mean())) / (float(small.std()) + 1e-6)
    lbp = _lbp_hist(hog_img)
    stats = _region_stats(hog_img)

    feature = np.concatenate(
        [
            _normalize(small) * 1.0,
            _normalize(hog) * 0.8,
            _normalize(lbp) * 0.45,
            _normalize(stats) * 0.35,
        ]
    )
    return {
        "vector": _normalize(feature),
        "face_detected": face_detected,
    }


def similarity(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(_normalize(left), _normalize(right)))


def person_metadata(person_dir: Path) -> dict[str, Any]:
    meta_path = person_dir / "person.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "person_id": person_dir.name,
        "name": person_dir.name,
        "role": "authorized",
        "authorized": True,
        "source": "manual",
    }


def write_person_metadata(person_dir: Path, metadata: dict[str, Any]) -> None:
    person_dir.mkdir(parents=True, exist_ok=True)
    (person_dir / "person.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def rebuild_profiles(threshold: float | None = None) -> dict[str, Any]:
    AUTHORIZED_FACE_DIR.mkdir(parents=True, exist_ok=True)
    previous = load_profiles(required=False)
    threshold = float(threshold if threshold is not None else previous.get("threshold", DEFAULT_THRESHOLD))

    people: list[dict[str, Any]] = []
    for person_dir in sorted(path for path in AUTHORIZED_FACE_DIR.iterdir() if path.is_dir()):
        samples = image_files(person_dir)
        feature_rows = []
        face_detected_count = 0
        for sample in samples:
            try:
                extracted = extract_feature(sample)
            except Exception:
                continue
            feature_rows.append(extracted["vector"])
            face_detected_count += int(bool(extracted["face_detected"]))
        if not feature_rows:
            continue
        metadata = person_metadata(person_dir)
        centroid = _normalize(np.mean(np.vstack(feature_rows), axis=0))
        people.append(
            {
                "person_id": metadata.get("person_id", person_dir.name),
                "name": metadata.get("name", person_dir.name),
                "role": metadata.get("role", "authorized"),
                "authorized": bool(metadata.get("authorized", True)),
                "source": metadata.get("source", "manual"),
                "lfw_name": metadata.get("lfw_name"),
                "sample_count": len(feature_rows),
                "face_detected_count": face_detected_count,
                "vector": centroid.tolist(),
            }
        )

    profiles = {
        "engine": "opencv-haar-hog-cosine",
        "threshold": threshold,
        "margin": FACE_MATCH_MARGIN,
        "generated_at": now_iso(),
        "people": people,
    }
    PROFILE_PATH.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    return profiles


def load_profiles(required: bool = True) -> dict[str, Any]:
    if not PROFILE_PATH.exists():
        if required:
            return rebuild_profiles()
        return {}
    try:
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        if required:
            return rebuild_profiles()
        return {}


def list_registered_people() -> list[dict[str, Any]]:
    profiles = load_profiles(required=False)
    items = []
    for person in profiles.get("people", []):
        items.append(
            {
                "person_id": person["person_id"],
                "name": person["name"],
                "role": person.get("role", "authorized"),
                "authorized": bool(person.get("authorized", True)),
                "face_enrolled": int(person.get("sample_count", 0)) > 0,
                "sample_count": int(person.get("sample_count", 0)),
                "lfw_name": person.get("lfw_name"),
            }
        )
    return items


def verify_image(image_path: str | Path, threshold: float | None = None) -> dict[str, Any]:
    profiles = load_profiles(required=True)
    people = profiles.get("people", [])
    threshold = float(threshold if threshold is not None else profiles.get("threshold", DEFAULT_THRESHOLD))
    if not people:
        return {
            "matched": False,
            "authorized": False,
            "matched_person_id": None,
            "matched_name": None,
            "confidence": 0.0,
            "similarity": 0.0,
            "threshold": threshold,
            "decision": "deny",
            "engine": profiles.get("engine", "opencv-haar-hog-cosine"),
            "message": "No enrolled face profiles found",
        }

    extracted = extract_feature(image_path)
    query = extracted["vector"]
    scored = []
    for person in people:
        score = similarity(query, np.array(person["vector"], dtype=np.float32))
        scored.append((score, person))
    scored.sort(key=lambda item: item[0], reverse=True)

    best_score, best_person = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else -1.0
    margin = float(profiles.get("margin", FACE_MATCH_MARGIN))
    matched = best_score >= threshold and (best_score - second_score) >= margin
    authorized = matched and bool(best_person.get("authorized", True))
    confidence = max(0.0, min(1.0, (best_score + 1.0) / 2.0))

    return {
        "matched": bool(matched),
        "authorized": bool(authorized),
        "matched_person_id": best_person["person_id"] if matched else None,
        "matched_name": best_person["name"] if matched else None,
        "confidence": round(confidence, 4),
        "similarity": round(float(best_score), 4),
        "second_similarity": round(float(second_score), 4),
        "threshold": round(threshold, 4),
        "margin": round(margin, 4),
        "decision": "allow" if authorized else "deny",
        "engine": profiles.get("engine", "opencv-haar-hog-cosine"),
        "face_detected": bool(extracted["face_detected"]),
        "message": "Access granted" if authorized else "Access denied",
    }


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {"samples": [], "registered_people": [], "unknown_people": []}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def list_face_samples() -> list[dict[str, Any]]:
    manifest = load_manifest()
    samples = []
    for item in manifest.get("samples", []):
        rel_path = item.get("relative_path", "")
        samples.append(
            {
                **item,
                "file_url": f"/face-samples/{rel_path.replace(os.sep, '/')}",
            }
        )
    return samples


def sample_path(sample_id: str) -> Path:
    manifest = load_manifest()
    for item in manifest.get("samples", []):
        if item.get("sample_id") == sample_id:
            path = FACE_TEST_DIR / item["relative_path"]
            if path.exists():
                return path
            raise FileNotFoundError(path)
    raise KeyError(f"Unknown face sample: {sample_id}")


def verify_sample(sample_id: str) -> dict[str, Any]:
    manifest = load_manifest()
    sample_meta = None
    for item in manifest.get("samples", []):
        if item.get("sample_id") == sample_id:
            sample_meta = item
            break
    if not sample_meta:
        raise KeyError(f"Unknown face sample: {sample_id}")
    result = verify_image(FACE_TEST_DIR / sample_meta["relative_path"])
    return {
        **result,
        "sample": sample_meta,
        "expected_decision": sample_meta.get("expected_decision"),
        "passed": result["decision"] == sample_meta.get("expected_decision"),
    }


def save_enrollment_sample(person_id: str, name: str, role: str, image_path: Path, original_name: str) -> Path:
    person_id = safe_person_id(person_id)
    person_dir = AUTHORIZED_FACE_DIR / person_id
    write_person_metadata(
        person_dir,
        {
            "person_id": person_id,
            "name": name,
            "role": role,
            "authorized": True,
            "source": "manual-upload",
        },
    )
    target = person_dir / safe_filename(original_name, f"{person_id}.jpg")
    if target.exists():
        target = person_dir / f"{target.stem}_{datetime.now().strftime('%H%M%S')}{target.suffix}"
    shutil.copy2(image_path, target)
    rebuild_profiles()
    return target
