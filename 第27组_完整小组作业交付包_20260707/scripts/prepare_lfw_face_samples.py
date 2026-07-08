from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tarfile
import urllib.request
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import requests
from PIL import Image

import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))

import face_service  # noqa: E402


LFW_URLS = [
    # scikit-learn's official fetch_lfw_people mirror for lfw-funneled.tgz.
    "https://ndownloader.figshare.com/files/5976015",
    "https://vis-www.cs.umass.edu/lfw/lfw-funneled.tgz",
    "http://vis-www.cs.umass.edu/lfw/lfw-funneled.tgz",
]
LFW_URL = LFW_URLS[0]
RAW_DIR = ROOT_DIR / "data" / "lfw_raw"
ARCHIVE_PATH = RAW_DIR / "lfw-funneled.tgz"
EXTRACTED_DIR = RAW_DIR / "lfw_funneled"
AUTHORIZED_DIR = ROOT_DIR / "data" / "authorized_faces"
TEST_DIR = ROOT_DIR / "data" / "face_test_samples"
MANIFEST_PATH = TEST_DIR / "manifest.json"


def download_lfw() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if ARCHIVE_PATH.exists() and ARCHIVE_PATH.stat().st_size > 0:
        return
    errors: list[str] = []
    tmp_path = ARCHIVE_PATH.with_suffix(".tgz.tmp")
    for url in LFW_URLS:
        print(f"Downloading LFW from {url}")
        tmp_path.unlink(missing_ok=True)
        try:
            with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "Mozilla/5.0"}) as response:
                response.raise_for_status()
                with tmp_path.open("wb") as file:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            file.write(chunk)
            tmp_path.replace(ARCHIVE_PATH)
            return
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            tmp_path.unlink(missing_ok=True)

    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl:
        for url in LFW_URLS:
            print(f"Downloading LFW with curl from {url}")
            try:
                subprocess.run([curl, "-L", "--fail", "--retry", "3", "-o", str(tmp_path), url], check=True)
                tmp_path.replace(ARCHIVE_PATH)
                return
            except Exception as exc:
                errors.append(f"curl {url}: {exc}")
                tmp_path.unlink(missing_ok=True)

    raise RuntimeError("Could not download LFW archive:\n" + "\n".join(errors))


def extract_lfw() -> None:
    if EXTRACTED_DIR.exists() and any(EXTRACTED_DIR.iterdir()):
        return
    print("Extracting LFW archive")
    with tarfile.open(ARCHIVE_PATH, "r:gz") as archive:
        archive.extractall(RAW_DIR)


def person_images() -> dict[str, list[Path]]:
    people: dict[str, list[Path]] = {}
    for person_dir in sorted(path for path in EXTRACTED_DIR.iterdir() if path.is_dir()):
        images = sorted(person_dir.glob("*.jpg"))
        if images:
            people[person_dir.name] = images
    return people


def feature_for(path: Path) -> np.ndarray | None:
    try:
        return face_service.extract_feature(path)["vector"]
    except Exception:
        return None


def choose_people(people: dict[str, list[Path]], enrolled_count: int, unknown_count: int) -> tuple[list[str], list[str]]:
    candidates = [name for name, images in people.items() if len(images) >= 3]
    scored: list[tuple[float, str]] = []
    for name in candidates:
        feats = [feature_for(path) for path in people[name][:5]]
        feats = [feat for feat in feats if feat is not None]
        if len(feats) < 3:
            continue
        centroid = np.mean(np.vstack(feats[:2]), axis=0)
        own = face_service.similarity(feats[2], centroid)
        scored.append((own, name))
    scored.sort(reverse=True)

    enrolled: list[str] = []
    enrolled_centroids: list[np.ndarray] = []
    for _, name in scored:
        feats = [feature_for(path) for path in people[name][:2]]
        if any(feat is None for feat in feats):
            continue
        centroid = np.mean(np.vstack(feats), axis=0)
        if enrolled_centroids and max(face_service.similarity(centroid, item) for item in enrolled_centroids) > 0.90:
            continue
        enrolled.append(name)
        enrolled_centroids.append(centroid)
        if len(enrolled) >= enrolled_count:
            break

    if len(enrolled) < enrolled_count:
        raise RuntimeError(f"Only selected {len(enrolled)} enrolled people; need {enrolled_count}")

    unknown_pool = [name for name, images in people.items() if name not in enrolled and images]
    unknown_scored: list[tuple[float, str]] = []
    for name in unknown_pool:
        feat = feature_for(people[name][0])
        if feat is None:
            continue
        nearest = max(face_service.similarity(feat, centroid) for centroid in enrolled_centroids)
        unknown_scored.append((nearest, name))
    unknown_scored.sort()
    unknown = [name for _, name in unknown_scored[:unknown_count]]
    if len(unknown) < unknown_count:
        raise RuntimeError(f"Only selected {len(unknown)} unknown people; need {unknown_count}")
    return enrolled, unknown


def write_jpeg(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as image:
        image.convert("RGB").save(dst, "JPEG", quality=92)


def calibrate_threshold(enrolled_people: list[str], unknown_people: list[str], people: dict[str, list[Path]]) -> float:
    registered_profiles: dict[str, np.ndarray] = {}
    for idx, name in enumerate(enrolled_people, start=1):
        person_id = f"P{idx:02d}"
        vectors = [feature_for(AUTHORIZED_DIR / person_id / f"enroll_{sample_idx}.jpg") for sample_idx in (1, 2)]
        vectors = [vector for vector in vectors if vector is not None]
        registered_profiles[person_id] = np.mean(np.vstack(vectors), axis=0)

    authorized_scores = []
    for idx, name in enumerate(enrolled_people, start=1):
        person_id = f"P{idx:02d}"
        feat = feature_for(TEST_DIR / "authorized" / f"{person_id}_test.jpg")
        if feat is not None:
            authorized_scores.append(face_service.similarity(feat, registered_profiles[person_id]))

    unknown_scores = []
    for idx, name in enumerate(unknown_people, start=1):
        feat = feature_for(TEST_DIR / "unknown" / f"U{idx:02d}_test.jpg")
        if feat is not None:
            nearest = max(face_service.similarity(feat, profile) for profile in registered_profiles.values())
            unknown_scores.append(nearest)

    if not authorized_scores or not unknown_scores:
        return face_service.DEFAULT_THRESHOLD

    low_positive = min(authorized_scores)
    high_negative = max(unknown_scores)
    if low_positive <= high_negative:
        # Keep a conservative fallback. The acceptance script will expose failures.
        return round((low_positive + high_negative) / 2, 4)
    return round((low_positive + high_negative) / 2, 4)


def prepare(args: argparse.Namespace) -> dict:
    download_lfw()
    extract_lfw()
    people = person_images()
    enrolled_people, unknown_people = choose_people(people, args.enrolled_count, args.unknown_count)

    AUTHORIZED_DIR.mkdir(parents=True, exist_ok=True)
    TEST_DIR.mkdir(parents=True, exist_ok=True)

    manifest = {
        "source": "LFW / Labeled Faces in the Wild, lfw-funneled",
        "source_url": LFW_URL,
        "registered_people": [],
        "unknown_people": [],
        "samples": [],
    }

    for idx, name in enumerate(enrolled_people, start=1):
        person_id = f"P{idx:02d}"
        person_dir = AUTHORIZED_DIR / person_id
        face_service.write_person_metadata(
            person_dir,
            {
                "person_id": person_id,
                "name": name.replace("_", " "),
                "role": "authorized-demo",
                "authorized": True,
                "source": "LFW",
                "lfw_name": name,
            },
        )
        write_jpeg(people[name][0], person_dir / "enroll_1.jpg")
        write_jpeg(people[name][1], person_dir / "enroll_2.jpg")
        test_target = TEST_DIR / "authorized" / f"{person_id}_test.jpg"
        write_jpeg(people[name][2], test_target)
        manifest["registered_people"].append({"person_id": person_id, "name": name.replace("_", " "), "lfw_name": name})
        manifest["samples"].append(
            {
                "sample_id": f"{person_id}_test",
                "kind": "authorized",
                "person_id": person_id,
                "name": name.replace("_", " "),
                "lfw_name": name,
                "expected_decision": "allow",
                "relative_path": f"authorized/{person_id}_test.jpg",
            }
        )

    for idx, name in enumerate(unknown_people, start=1):
        sample_id = f"U{idx:02d}_test"
        test_target = TEST_DIR / "unknown" / f"{sample_id}.jpg"
        write_jpeg(people[name][0], test_target)
        manifest["unknown_people"].append({"sample_id": sample_id, "name": name.replace("_", " "), "lfw_name": name})
        manifest["samples"].append(
            {
                "sample_id": sample_id,
                "kind": "unknown",
                "person_id": None,
                "name": name.replace("_", " "),
                "lfw_name": name,
                "expected_decision": "deny",
                "relative_path": f"unknown/{sample_id}.jpg",
            }
        )

    threshold = calibrate_threshold(enrolled_people, unknown_people, people)
    manifest["threshold"] = threshold
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    profiles = face_service.rebuild_profiles(threshold=threshold)
    print(json.dumps({"manifest": str(MANIFEST_PATH), "threshold": threshold, "profiles": len(profiles["people"])}, ensure_ascii=False, indent=2))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare 5 true and 5 false LFW face-recognition samples.")
    parser.add_argument("--enrolled-count", type=int, default=5)
    parser.add_argument("--unknown-count", type=int, default=5)
    args = parser.parse_args()
    prepare(args)


if __name__ == "__main__":
    main()
