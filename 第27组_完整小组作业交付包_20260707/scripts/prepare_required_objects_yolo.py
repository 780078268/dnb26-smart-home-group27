from __future__ import annotations

import argparse
import csv
import io
import math
import random
import requests
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path

from PIL import Image


DRONE_PARQUET_URLS = {
    "train": [
        f"https://huggingface.co/datasets/pathikg/drone-detection-dataset/resolve/main/data/train-{idx:05d}-of-00009.parquet"
        for idx in range(9)
    ],
    "val": [
        "https://huggingface.co/datasets/pathikg/drone-detection-dataset/resolve/main/data/test-00000-of-00001.parquet",
    ],
}

MENDELEY_INDOOR_VOC_URL = (
    "https://data.mendeley.com/public-files/datasets/3ggxwf2vpr/files/"
    "e9e913c0-e5eb-476b-a296-2ab55e55ada5/file_downloaded"
)
FIRE_EXTINGUISHER_NAMES = {"fire extinguisher", "fireextinguisher", "fire-extinguisher", "extinguisher"}

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

ALL_CLASSES = ["drone", "fire_extinguisher", "light_bulb"]
CLASS_TO_ID = {"drone": 0, "fire_extinguisher": 1, "light_bulb": 2}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the required real-photo YOLO dataset: drone, fire_extinguisher, light_bulb."
    )
    parser.add_argument("--output", default="data/yolo_required", help="Output YOLO dataset directory.")
    parser.add_argument("--raw-dir", default="data/required_objects_raw", help="Cache directory for source data.")
    parser.add_argument("--max-per-class", type=int, default=100, help="Maximum images per class across train/val/test.")
    parser.add_argument("--seed", type=int, default=27)
    parser.add_argument("--no-download-images", action="store_true")
    parser.add_argument(
        "--class-set",
        choices=["all", "required", "drone", "fire-extinguisher", "light-bulb"],
        default="all",
        help="Build all classes, only the required teacher classes, or only the light bulb innovation class.",
    )
    parser.add_argument(
        "--min-box-area",
        type=float,
        default=0.0,
        help="Drop boxes smaller than this normalized area, for example 0.002 means 0.2%% of the image.",
    )
    parser.add_argument("--min-drone-box-area", type=float, default=None)
    parser.add_argument("--min-fire-extinguisher-box-area", type=float, default=None)
    parser.add_argument("--min-light-bulb-box-area", type=float, default=None)
    args = parser.parse_args()

    global CLASS_TO_ID
    class_names = selected_classes(args.class_set)
    CLASS_TO_ID = {name: idx for idx, name in enumerate(class_names)}
    min_areas = min_box_areas(args)

    root = Path(__file__).resolve().parents[1]
    output = (root / args.output).resolve()
    raw_dir = (root / args.raw_dir).resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    limits = split_limits(args.max_per_class)
    if "drone" in CLASS_TO_ID:
        prepare_drone(output, raw_dir, limits, args.seed, min_areas["drone"])
    if "fire_extinguisher" in CLASS_TO_ID:
        prepare_fire_extinguisher(output, raw_dir, limits, args.seed, min_areas["fire_extinguisher"])
    if "light_bulb" in CLASS_TO_ID:
        prepare_light_bulb(output, raw_dir, limits, args.seed, args.no_download_images, min_areas["light_bulb"])
    write_yaml(output)
    print_dataset_summary(output)
    print(f"Dataset ready: {output}")
    print("Sources:")
    print("- drone: pathikg/drone-detection-dataset on Hugging Face, MIT license")
    print("- fire_extinguisher: Indoor Object Detection Dataset on Mendeley Data, CC BY 4.0")
    print("- light_bulb: Open Images detection annotations and real images")


def selected_classes(class_set: str) -> list[str]:
    if class_set == "required":
        return ["drone", "fire_extinguisher"]
    if class_set == "drone":
        return ["drone"]
    if class_set == "fire-extinguisher":
        return ["fire_extinguisher"]
    if class_set == "light-bulb":
        return ["light_bulb"]
    return list(ALL_CLASSES)


def min_box_areas(args: argparse.Namespace) -> dict[str, float]:
    return {
        "drone": args.min_drone_box_area if args.min_drone_box_area is not None else args.min_box_area,
        "fire_extinguisher": (
            args.min_fire_extinguisher_box_area
            if args.min_fire_extinguisher_box_area is not None
            else args.min_box_area
        ),
        "light_bulb": (
            args.min_light_bulb_box_area if args.min_light_bulb_box_area is not None else args.min_box_area
        ),
    }


def split_limits(max_per_class: int) -> dict[str, int]:
    train = math.ceil(max_per_class * 0.8)
    val = max(1, round(max_per_class * 0.1))
    test = max(1, max_per_class - train - val)
    return {"train": train, "val": val, "test": test}


def download(url: str, path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        if path.suffix.lower() == ".zip" and not looks_like_zip(path):
            path.unlink()
        else:
            return
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url}")
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        with requests.get(url, headers=headers, stream=True, timeout=180) as response:
            response.raise_for_status()
            with tmp_path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file.write(chunk)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        subprocess.run(
            ["curl.exe", "-L", "--fail", "--retry", "3", "-A", "Mozilla/5.0", "-o", str(tmp_path), url],
            check=True,
        )
    tmp_path.replace(path)
    if path.suffix.lower() == ".zip" and not looks_like_zip(path):
        size = path.stat().st_size if path.exists() else 0
        path.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded file is not a valid zip: {path} ({size} bytes)")


def looks_like_zip(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 1024:
        return False
    with path.open("rb") as file:
        return file.read(4) == b"PK\x03\x04"


def prepare_drone(output: Path, raw_dir: Path, limits: dict[str, int], seed: int, min_box_area: float) -> None:
    try:
        import pyarrow.parquet as pq
    except Exception as exc:
        raise RuntimeError("pyarrow is required to read the Hugging Face drone parquet files.") from exc

    rng = random.Random(seed)
    written = {"train": 0, "val": 0, "test": 0}
    parquet_dir = raw_dir / "drone_parquet"
    source_splits = {"train": "train", "val": "val", "test": "val"}

    for target_split, source_split in source_splits.items():
        target = limits[target_split]
        rows = []
        for url in DRONE_PARQUET_URLS[source_split]:
            parquet_path = parquet_dir / Path(url).name
            download(url, parquet_path)
            table = pq.read_table(parquet_path, columns=["width", "height", "objects", "image", "image_id"])
            rows.extend(table.to_pylist())
            if source_split != "train" and len(rows) >= target * 6:
                break
        rng.shuffle(rows)
        used_ids: set[str] = set()
        for row in rows:
            if written[target_split] >= target:
                break
            image_id = f"drone_{row['image_id']}"
            if image_id in used_ids:
                continue
            objects = row.get("objects") or {}
            boxes = objects.get("bbox") or []
            image = row.get("image") or {}
            image_bytes = image.get("bytes")
            if not boxes or not image_bytes:
                continue
            label_lines = []
            width = int(row["width"])
            height = int(row["height"])
            for x, y, w, h in boxes:
                if w <= 0 or h <= 0:
                    continue
                if normalized_area(w, h, width, height) < min_box_area:
                    continue
                label_lines.append(to_yolo_line(CLASS_TO_ID["drone"], x + w / 2, y + h / 2, w, h, width, height))
            if not label_lines:
                continue
            write_image_and_label(output, target_split, f"{image_id}_{written[target_split]:05d}.jpg", image_bytes, label_lines)
            used_ids.add(image_id)
            written[target_split] += 1
        print(f"Prepared drone {target_split}: {written[target_split]}")


def prepare_fire_extinguisher(output: Path, raw_dir: Path, limits: dict[str, int], seed: int, min_box_area: float) -> None:
    rng = random.Random(seed)
    zip_path = raw_dir / "mendeley_indoor_voc" / "Indoor_Objects_v1i_voc.zip"
    download(MENDELEY_INDOOR_VOC_URL, zip_path)

    split_aliases = {
        "train": {"train", "training"},
        "val": {"valid", "val", "validation"},
        "test": {"test", "testing"},
    }
    with zipfile.ZipFile(zip_path) as archive:
        xml_names = [name for name in archive.namelist() if name.lower().endswith(".xml")]
        image_names = {
            (split_from_path(name, split_aliases), Path(name).stem): name
            for name in archive.namelist()
            if Path(name).suffix.lower() in IMAGE_SUFFIXES
        }
        xml_by_split: dict[str, list[str]] = {"train": [], "val": [], "test": []}
        for xml_name in xml_names:
            split = split_from_path(xml_name, split_aliases)
            if split:
                xml_by_split[split].append(xml_name)
        if not xml_by_split["test"]:
            xml_by_split["test"] = list(xml_by_split["val"])

        for target_split in ["train", "val", "test"]:
            candidates = list(xml_by_split[target_split])
            rng.shuffle(candidates)
            written = 0
            used_stems: set[str] = set()
            for xml_name in candidates:
                if written >= limits[target_split]:
                    break
                stem = Path(xml_name).stem
                if stem in used_stems:
                    continue
                image_name = image_names.get((target_split, stem))
                if image_name is None and target_split == "test":
                    image_name = image_names.get(("val", stem))
                if image_name is None:
                    continue
                label_lines = fire_extinguisher_voc_to_yolo(archive.read(xml_name), min_box_area)
                if not label_lines:
                    continue
                out_name = f"fire_extinguisher_{target_split}_{written:05d}{Path(image_name).suffix.lower()}"
                write_image_and_label(output, target_split, out_name, archive.read(image_name), label_lines)
                written += 1
                used_stems.add(stem)
            print(f"Prepared fire_extinguisher {target_split}: {written}")


def split_from_path(name: str, aliases: dict[str, set[str]]) -> str | None:
    parts = {part.lower() for part in Path(name).parts}
    for split, names in aliases.items():
        if parts & names:
            return split
    return None


def fire_extinguisher_voc_to_yolo(xml_bytes: bytes, min_box_area: float) -> list[str]:
    root = ET.fromstring(xml_bytes)
    size = root.find("size")
    if size is None:
        return []
    image_width = int(float(size.findtext("width", "0")))
    image_height = int(float(size.findtext("height", "0")))
    if image_width <= 0 or image_height <= 0:
        return []

    lines = []
    for obj in root.findall("object"):
        name = (obj.findtext("name") or "").strip().lower().replace("_", " ")
        if name not in FIRE_EXTINGUISHER_NAMES:
            continue
        box = obj.find("bndbox")
        if box is None:
            continue
        xmin = float(box.findtext("xmin", "0"))
        ymin = float(box.findtext("ymin", "0"))
        xmax = float(box.findtext("xmax", "0"))
        ymax = float(box.findtext("ymax", "0"))
        width = max(0.0, xmax - xmin)
        height = max(0.0, ymax - ymin)
        if width <= 0 or height <= 0:
            continue
        if normalized_area(width, height, image_width, image_height) < min_box_area:
            continue
        lines.append(
            to_yolo_line(
                CLASS_TO_ID["fire_extinguisher"],
                xmin + width / 2,
                ymin + height / 2,
                width,
                height,
                image_width,
                image_height,
            )
        )
    return lines


def prepare_light_bulb(
    output: Path,
    raw_dir: Path,
    limits: dict[str, int],
    seed: int,
    no_download_images: bool,
    min_box_area: float,
) -> None:
    metadata_dir = raw_dir.parent / "open_images_raw"
    class_ids = load_light_bulb_class_ids(metadata_dir)
    selected = select_light_bulb_annotations(metadata_dir, class_ids, seed)
    metadata = load_open_images_metadata(metadata_dir, selected)
    build_light_bulb_dataset(output, raw_dir, selected, metadata, limits, no_download_images, seed, min_box_area)


def load_light_bulb_class_ids(raw_dir: Path) -> dict[str, str]:
    class_csv = raw_dir / "open_images" / "oidv7-class-descriptions-boxable.csv"
    download(CLASS_DESCRIPTION_URL, class_csv)
    lookup = {}
    with class_csv.open("r", encoding="utf-8", newline="") as file:
        for class_id, name in csv.reader(file):
            if name == "Light bulb":
                lookup[class_id] = "light_bulb"
    if not lookup:
        raise RuntimeError("Missing Open Images class: Light bulb")
    return lookup


def select_light_bulb_annotations(
    raw_dir: Path,
    class_ids: dict[str, str],
    seed: int,
) -> dict[str, list[dict[str, str]]]:
    rng = random.Random(seed)
    selected: dict[str, list[dict[str, str]]] = defaultdict(list)
    split_map = {"train": "train", "validation": "val", "test": "test"}

    for open_split, yolo_split in split_map.items():
        annotation_csv = raw_dir / "open_images" / Path(ANNOTATION_URLS[open_split]).name
        download(ANNOTATION_URLS[open_split], annotation_csv)
        rows_by_image: dict[str, list[dict[str, str]]] = defaultdict(list)
        with annotation_csv.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                if row["LabelName"] not in class_ids:
                    continue
                rows_by_image[row["ImageID"]].append({**row, "SourceSplit": open_split})
        for image_id, rows in rows_by_image.items():
            selected[image_id].extend(rows)
        print(f"Found light_bulb candidates in Open Images {yolo_split}: {len(rows_by_image)}")

    image_ids = list(selected.keys())
    rng.shuffle(image_ids)
    shuffled = {image_id: selected[image_id] for image_id in image_ids}
    print(f"Selected light_bulb candidates total: {len(shuffled)}")
    return shuffled


def load_open_images_metadata(
    raw_dir: Path,
    selected: dict[str, list[dict[str, str]]],
) -> dict[str, dict[str, dict[str, str]]]:
    needed: dict[str, set[str]] = {"train": set(), "validation": set(), "test": set()}
    for image_id, rows in selected.items():
        if rows:
            needed[rows[0]["SourceSplit"]].add(image_id)
    metadata: dict[str, dict[str, dict[str, str]]] = {"train": {}, "validation": {}, "test": {}}
    for open_split, url in IMAGE_METADATA_URLS.items():
        metadata_csv = raw_dir / "open_images" / Path(url).name
        download(url, metadata_csv)
        with metadata_csv.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                if row["ImageID"] in needed[open_split]:
                    metadata[open_split][row["ImageID"]] = row
    return metadata


def build_light_bulb_dataset(
    output: Path,
    raw_dir: Path,
    selected: dict[str, list[dict[str, str]]],
    metadata: dict[str, dict[str, dict[str, str]]],
    limits: dict[str, int],
    no_download_images: bool,
    seed: int,
    min_box_area: float,
) -> None:
    failed_path = raw_dir.parent / "open_images_raw" / "light_bulb_failed_downloads.txt"
    failed_ids = set()
    if failed_path.exists():
        failed_ids = {line.strip() for line in failed_path.read_text(encoding="utf-8").splitlines() if line.strip()}

    downloaded: list[tuple[str, list[dict[str, str]], Path]] = []
    target_total = sum(limits.values())
    for image_id, rows in selected.items():
        if len(downloaded) >= target_total:
            break
        rows = [row for row in rows if open_images_row_area(row) >= min_box_area]
        if not rows:
            continue
        source_split = rows[0]["SourceSplit"]
        cached = raw_dir / "open_images" / "images" / source_split / f"{image_id}.jpg"
        if not cached.exists() and not no_download_images and image_id not in failed_ids:
            image_meta = metadata.get(source_split, {}).get(image_id, {})
            image_urls = [
                url
                for url in (image_meta.get("Thumbnail300KURL"), image_meta.get("OriginalURL"))
                if url
            ]
            last_error: Exception | None = None
            for image_url in dict.fromkeys(image_urls):
                try:
                    download(image_url, cached)
                    break
                except Exception as exc:
                    cached.unlink(missing_ok=True)
                    last_error = exc
            if not cached.exists():
                failed_ids.add(image_id)
                print(f"Skip light_bulb {image_id}: {last_error or 'missing image URL'}")
                continue
        if cached.exists():
            downloaded.append((image_id, rows, cached))

    failed_path.parent.mkdir(parents=True, exist_ok=True)
    failed_path.write_text("\n".join(sorted(failed_ids)) + ("\n" if failed_ids else ""), encoding="utf-8")

    rng = random.Random(seed)
    rng.shuffle(downloaded)
    split_targets = effective_split_limits(len(downloaded), limits)
    cursor = 0
    for split in ["train", "val", "test"]:
        written = 0
        for image_id, rows, cached in downloaded[cursor : cursor + split_targets[split]]:
            label_lines = []
            for row in rows:
                xmin = float(row["XMin"])
                xmax = float(row["XMax"])
                ymin = float(row["YMin"])
                ymax = float(row["YMax"])
                x_center = (xmin + xmax) / 2
                y_center = (ymin + ymax) / 2
                width = xmax - xmin
                height = ymax - ymin
                label_lines.append(f"{CLASS_TO_ID['light_bulb']} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}")
            out_name = f"light_bulb_{split}_{written:05d}.jpg"
            write_image_and_label(output, split, out_name, cached.read_bytes(), label_lines)
            written += 1
        cursor += split_targets[split]
        print(f"Prepared light_bulb {split}: {written}")
    if len(downloaded) < target_total:
        print(f"Only {len(downloaded)} downloadable real light_bulb images were available for target {target_total}.")


def effective_split_limits(total: int, limits: dict[str, int]) -> dict[str, int]:
    requested = sum(limits.values())
    if total >= requested:
        return dict(limits)
    if total <= 0:
        return {"train": 0, "val": 0, "test": 0}

    result = {
        split: min(limits[split], int(round(total * limits[split] / requested)))
        for split in ["train", "val", "test"]
    }
    while sum(result.values()) > total:
        for split in ["train", "val", "test"]:
            if result[split] > 0 and sum(result.values()) > total:
                result[split] -= 1
    while sum(result.values()) < total:
        for split in ["train", "val", "test"]:
            if result[split] < limits[split] and sum(result.values()) < total:
                result[split] += 1
    return result


def normalized_area(width: float, height: float, image_width: int, image_height: int) -> float:
    if image_width <= 0 or image_height <= 0:
        return 0.0
    return max(0.0, width) * max(0.0, height) / (image_width * image_height)


def open_images_row_area(row: dict[str, str]) -> float:
    width = max(0.0, float(row["XMax"]) - float(row["XMin"]))
    height = max(0.0, float(row["YMax"]) - float(row["YMin"]))
    return width * height


def to_yolo_line(cls: int, x_center: float, y_center: float, width: float, height: float, image_width: int, image_height: int) -> str:
    x_center = clip(x_center / image_width)
    y_center = clip(y_center / image_height)
    width = clip(width / image_width)
    height = clip(height / image_height)
    return f"{cls} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}"


def clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def write_image_and_label(output: Path, split: str, name: str, image_bytes: bytes, label_lines: list[str]) -> None:
    image_dir = output / split / "images"
    label_dir = output / split / "labels"
    image_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(io.BytesIO(image_bytes)) as image:
        image.convert("RGB").save(image_dir / Path(name).with_suffix(".jpg").name, format="JPEG", quality=90)
    (label_dir / Path(name).with_suffix(".txt").name).write_text("\n".join(label_lines) + "\n", encoding="utf-8")


def write_yaml(output: Path) -> None:
    names = "\n".join(f"  {idx}: {name}" for name, idx in sorted(CLASS_TO_ID.items(), key=lambda item: item[1]))
    text = f"""path: {output.as_posix()}
train: train/images
val: val/images
test: test/images
names:
{names}
"""
    (output / "dataset.yaml").write_text(text, encoding="utf-8")


def print_dataset_summary(output: Path) -> None:
    for split in ["train", "val", "test"]:
        image_count = len(list((output / split / "images").glob("*.jpg")))
        label_count = len(list((output / split / "labels").glob("*.txt")))
        print(f"{split}: images={image_count}, labels={label_count}")


if __name__ == "__main__":
    main()
