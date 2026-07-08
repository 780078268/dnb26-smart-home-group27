from __future__ import annotations

import argparse
import math
import csv
import random
import shutil
import urllib.request
from collections import defaultdict
from pathlib import Path


OPEN_IMAGES_BASE = "https://storage.googleapis.com/openimages/v7"
CLASS_DESCRIPTION_URL = f"{OPEN_IMAGES_BASE}/oidv7-class-descriptions-boxable.csv"
ANNOTATION_URLS = {
    "train": "https://storage.googleapis.com/openimages/v6/oidv6-train-annotations-bbox.csv",
    "validation": "https://storage.googleapis.com/openimages/v5/validation-annotations-bbox.csv",
    "test": "https://storage.googleapis.com/openimages/v5/test-annotations-bbox.csv",
}
IMAGE_METADATA_URLS = {
    "train": "https://storage.googleapis.com/openimages/2018_04/train/train-images-boxable-with-rotation.csv",
    "validation": "https://storage.googleapis.com/openimages/2018_04/validation/validation-images-with-rotation.csv",
    "test": "https://storage.googleapis.com/openimages/2018_04/test/test-images-with-rotation.csv",
}

TARGET_CLASSES = {
    "Person": "person",
    "Car": "car",
    "Light bulb": "light_bulb",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a real-photo YOLO dataset from Open Images V7.")
    parser.add_argument("--output", default="data/yolo_real", help="Output dataset directory.")
    parser.add_argument("--raw-dir", default="data/open_images_raw", help="Cache directory for CSVs and images.")
    parser.add_argument("--max-per-class", type=int, default=200, help="Maximum images per class across all splits.")
    parser.add_argument("--seed", type=int, default=27)
    parser.add_argument("--no-download-images", action="store_true", help="Only write labels for images already cached.")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    output = (root / args.output).resolve()
    raw_dir = (root / args.raw_dir).resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)

    class_ids = load_class_ids(raw_dir)
    selected = select_annotations(raw_dir, class_ids, args.max_per_class, args.seed)
    metadata = load_image_metadata(raw_dir, selected)
    build_dataset(output, raw_dir, selected, metadata, args.no_download_images)
    write_yaml(output)
    print(f"Dataset ready: {output}")
    print(f"Images per class cap: {args.max_per_class}")
    print("Increase --max-per-class to 1000-3000 for final training.")


def download(url: str, path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url}")
    urllib.request.urlretrieve(url, path)


def load_class_ids(raw_dir: Path) -> dict[str, str]:
    class_csv = raw_dir / "oidv7-class-descriptions-boxable.csv"
    download(CLASS_DESCRIPTION_URL, class_csv)
    lookup: dict[str, str] = {}
    with class_csv.open("r", encoding="utf-8", newline="") as file:
        for class_id, name in csv.reader(file):
            if name in TARGET_CLASSES:
                lookup[class_id] = TARGET_CLASSES[name]
    missing = set(TARGET_CLASSES.values()) - set(lookup.values())
    if missing:
        raise RuntimeError(f"Missing Open Images classes: {sorted(missing)}")
    return lookup


def select_annotations(
    raw_dir: Path,
    class_ids: dict[str, str],
    max_per_class: int,
    seed: int,
) -> dict[str, dict[str, list[dict[str, str]]]]:
    rng = random.Random(seed)
    selected: dict[str, dict[str, list[dict[str, str]]]] = {
        "train": defaultdict(list),
        "val": defaultdict(list),
        "test": defaultdict(list),
    }
    split_map = {"train": "train", "validation": "val", "test": "test"}
    split_limits = {
        "train": math.ceil(max_per_class * 0.8),
        "validation": max(1, round(max_per_class * 0.1)),
        "test": max(1, max_per_class - math.ceil(max_per_class * 0.8) - max(1, round(max_per_class * 0.1))),
    }
    per_split_seen: dict[str, dict[str, int]] = {
        split: defaultdict(int) for split in split_map
    }

    for open_images_split, yolo_split in split_map.items():
        per_class_limit = split_limits[open_images_split]
        annotation_csv = raw_dir / Path(ANNOTATION_URLS[open_images_split]).name
        download(ANNOTATION_URLS[open_images_split], annotation_csv)
        rows_by_image: dict[str, list[dict[str, str]]] = defaultdict(list)
        with annotation_csv.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                label_name = class_ids.get(row["LabelName"])
                if label_name is None:
                    continue
                rows_by_image[row["ImageID"]].append({**row, "TargetLabel": label_name, "SourceSplit": open_images_split})

        image_ids = list(rows_by_image.keys())
        rng.shuffle(image_ids)
        for image_id in image_ids:
            labels = {row["TargetLabel"] for row in rows_by_image[image_id]}
            if not any(per_split_seen[open_images_split][label] < per_class_limit for label in labels):
                continue
            selected[yolo_split][image_id].extend(rows_by_image[image_id])
            for label in labels:
                per_split_seen[open_images_split][label] += 1
            if all(per_split_seen[open_images_split][label] >= per_class_limit for label in TARGET_CLASSES.values()):
                break

        counts = ", ".join(
            f"{label}={per_split_seen[open_images_split][label]}"
            for label in TARGET_CLASSES.values()
        )
        print(f"Selected {open_images_split}: {counts}")

    return selected


def load_image_metadata(
    raw_dir: Path,
    selected: dict[str, dict[str, list[dict[str, str]]]],
) -> dict[str, dict[str, dict[str, str]]]:
    split_map = {"train": "train", "validation": "val", "test": "test"}
    needed = {
        open_images_split: set(selected[yolo_split])
        for open_images_split, yolo_split in split_map.items()
    }
    metadata: dict[str, dict[str, dict[str, str]]] = {"train": {}, "validation": {}, "test": {}}
    for open_images_split, url in IMAGE_METADATA_URLS.items():
        metadata_csv = raw_dir / Path(url).name
        download(url, metadata_csv)
        with metadata_csv.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                image_id = row["ImageID"]
                if image_id in needed[open_images_split]:
                    metadata[open_images_split][image_id] = row
        missing = needed[open_images_split] - set(metadata[open_images_split])
        if missing:
            print(f"Warning: missing metadata for {len(missing)} {open_images_split} images")
    return metadata


def build_dataset(
    output: Path,
    raw_dir: Path,
    selected: dict[str, dict[str, list[dict[str, str]]]],
    metadata: dict[str, dict[str, dict[str, str]]],
    no_download_images: bool,
) -> None:
    class_to_id = {"person": 0, "car": 1, "light_bulb": 2}
    for split, images in selected.items():
        image_dir = output / split / "images"
        label_dir = output / split / "labels"
        image_dir.mkdir(parents=True, exist_ok=True)
        label_dir.mkdir(parents=True, exist_ok=True)

        for image_id, rows in images.items():
            source_split = rows[0]["SourceSplit"]
            cached = raw_dir / "images" / source_split / f"{image_id}.jpg"
            if not cached.exists() and not no_download_images:
                try:
                    image_meta = metadata.get(source_split, {}).get(image_id, {})
                    image_url = image_meta.get("Thumbnail300KURL") or image_meta.get("OriginalURL")
                    if not image_url:
                        raise RuntimeError("No image URL in metadata")
                    download(image_url, cached)
                except Exception as exc:
                    print(f"Skip {image_id}: {exc}")
                    continue
            if not cached.exists():
                continue

            shutil.copy2(cached, image_dir / f"{image_id}.jpg")
            label_lines = []
            for row in rows:
                cls = class_to_id[row["TargetLabel"]]
                xmin = float(row["XMin"])
                xmax = float(row["XMax"])
                ymin = float(row["YMin"])
                ymax = float(row["YMax"])
                x_center = (xmin + xmax) / 2
                y_center = (ymin + ymax) / 2
                width = xmax - xmin
                height = ymax - ymin
                label_lines.append(f"{cls} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}")
            (label_dir / f"{image_id}.txt").write_text("\n".join(label_lines) + "\n", encoding="utf-8")


def write_yaml(output: Path) -> None:
    text = f"""path: {output.as_posix()}
train: train/images
val: val/images
test: test/images
names:
  0: person
  1: car
  2: light_bulb
"""
    (output / "dataset.yaml").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
