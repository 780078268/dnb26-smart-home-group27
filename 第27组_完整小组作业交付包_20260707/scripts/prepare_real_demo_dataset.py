from __future__ import annotations

import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image


ROOT_DIR = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT_DIR / "真实测试数据集"
CANDIDATE_DIR = ROOT_DIR / "data" / "real_yolo_candidates"
FACE_TEST_DIR = ROOT_DIR / "data" / "face_test_samples"

sys.path.insert(0, str(ROOT_DIR / "backend"))
import recognizer  # noqa: E402


YOLO_SOURCES: dict[str, list[str]] = {
    "drone": [
        "File:2015 Dron DJI Phantom 3 Advanced.JPG",
        "File:DJI Phantom 4K drone in action.jpg",
        "File:DJI Phantom 4 Drone (32285759641).jpg",
        "File:Quadcopter Drone in flight.jpg",
    ],
    "fire_extinguisher": [
        "File:A fire extinguisher in Bangalore in 2011.jpg",
        "File:Doorstop fire extinguisher, Xavier University New Orleans.jpg",
        "File:Fire extinguisher (40242871690).jpg",
        "File:Fire extinguisher in Germany.JPG",
    ],
    "light_bulb": [
        "File:LED-E27-Light-Bulb-1112 01.jpg",
        "File:LED-E27-Light-Bulb-1134.jpg",
        "File:Light Bulb (130919449).jpeg",
        "File:Light bulb (3014150328).jpg",
    ],
}

LABEL_DIRS = {
    "drone": ("无人机", "无人机_drone"),
    "fire_extinguisher": ("灭火器", "灭火器_fire_extinguisher"),
    "light_bulb": ("灯泡", "灯泡_light_bulb"),
}


def safe_name(value: str) -> str:
    name = value.replace("File:", "")
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")


def ensure_clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def image_is_valid(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


def commons_image_info(session: requests.Session, title: str) -> dict[str, Any]:
    params = {
        "action": "query",
        "format": "json",
        "titles": title,
        "prop": "imageinfo",
        "iiprop": "url|mime|size|extmetadata",
        "iiurlwidth": 800,
    }
    response = get_with_retry(session, "https://commons.wikimedia.org/w/api.php", params=params)
    pages = response.json().get("query", {}).get("pages", {})
    page = next(iter(pages.values()))
    info = (page.get("imageinfo") or [{}])[0]
    if not (info.get("mime") or "").startswith("image/"):
        raise ValueError(f"{title} is not an image: {info.get('mime')}")
    return info


def get_with_retry(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    attempts: int = 5,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, params=params, timeout=60)
            if response.status_code == 429 and attempt < attempts:
                wait = int(response.headers.get("Retry-After", "3"))
                time.sleep(min(wait, 10))
                continue
            response.raise_for_status()
            time.sleep(0.45)
            return response
        except Exception as exc:  # pragma: no cover - network resilience
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2 * attempt, 10))
    raise RuntimeError(f"Failed to request {url}") from last_error


def download_yolo_candidates() -> list[dict[str, Any]]:
    ensure_clean_dir(CANDIDATE_DIR)
    session = requests.Session()
    session.headers.update({"User-Agent": "Group27SmartHomeDemo/1.0 (course prototype)"})

    candidates: list[dict[str, Any]] = []
    for label, titles in YOLO_SOURCES.items():
        label_dir = CANDIDATE_DIR / label
        label_dir.mkdir(parents=True, exist_ok=True)
        for index, title in enumerate(titles, 1):
            info = commons_image_info(session, title)
            image_url = info.get("thumburl") or info.get("url")
            if not image_url:
                raise ValueError(f"No downloadable image URL for {title}")

            suffix = Path(image_url.split("?")[0]).suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png"}:
                suffix = ".jpg"
            target = label_dir / f"{label}_{index:02d}_{safe_name(title)}{suffix}"
            response = download_with_retry(session, image_url)
            target.write_bytes(response.content)
            if not image_is_valid(target):
                target.unlink(missing_ok=True)
                raise ValueError(f"Downloaded file is not a valid image: {target}")

            metadata = info.get("extmetadata", {})
            candidates.append(
                {
                    "expected_label": label,
                    "source_title": title,
                    "candidate_path": str(target.relative_to(ROOT_DIR)),
                    "source_page": info.get("descriptionurl"),
                    "image_url": image_url,
                    "license": (metadata.get("LicenseShortName") or {}).get("value"),
                    "artist": (metadata.get("Artist") or {}).get("value"),
                }
            )
    return candidates


def download_with_retry(session: requests.Session, url: str, attempts: int = 5) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, timeout=60)
            if response.status_code == 429 and attempt < attempts:
                wait = int(response.headers.get("Retry-After", "2"))
                time.sleep(min(wait, 8))
                continue
            response.raise_for_status()
            time.sleep(0.35)
            return response
        except Exception as exc:  # pragma: no cover - network resilience
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2 * attempt, 8))
    raise RuntimeError(f"Failed to download {url}") from last_error


def score_candidate(item: dict[str, Any]) -> dict[str, Any]:
    path = ROOT_DIR / item["candidate_path"]
    detection = recognizer.analyze_image(path, path.name)
    labels = recognizer.yolo_labels_from_detection(detection)
    expected = item["expected_label"]
    expected_scores = [float(row["confidence"]) for row in labels if row.get("label") == expected]
    item["detected_labels"] = labels
    item["engine"] = detection.get("engine")
    item["expected_confidence"] = max(expected_scores, default=0.0)
    return item


def copy_ranked_yolo_samples(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    yolo_root = DATASET_DIR / "YOLO识别"
    output: list[dict[str, Any]] = []
    for label, (cn_dir, file_prefix) in LABEL_DIRS.items():
        target_dir = yolo_root / cn_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        ranked = sorted(
            [item for item in candidates if item["expected_label"] == label],
            key=lambda item: item.get("expected_confidence", 0),
            reverse=True,
        )
        for index, item in enumerate(ranked[:3], 1):
            suffix = Path(item["candidate_path"]).suffix.lower() or ".jpg"
            target = target_dir / f"{file_prefix}_{index:02d}{suffix}"
            shutil.copy2(ROOT_DIR / item["candidate_path"], target)
            copied = {
                **item,
                "demo_file": str(target.relative_to(ROOT_DIR)),
                "public_url": f"/real-samples/YOLO识别/{cn_dir}/{target.name}",
                "rank": index,
            }
            output.append(copied)
    return output


def copy_face_samples() -> list[dict[str, Any]]:
    face_root = DATASET_DIR / "人脸识别"
    authorized_dir = face_root / "已录入人员_应允许"
    unknown_dir = face_root / "未录入人员_应拒绝"
    authorized_dir.mkdir(parents=True, exist_ok=True)
    unknown_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = FACE_TEST_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    copied: list[dict[str, Any]] = []
    for sample in manifest.get("samples", []):
        source = FACE_TEST_DIR / sample["relative_path"]
        target_dir = authorized_dir if sample["kind"] == "authorized" else unknown_dir
        target = target_dir / f"{sample['sample_id']}_{safe_name(sample['name'])}.jpg"
        shutil.copy2(source, target)
        copied.append(
            {
                "sample_id": sample["sample_id"],
                "name": sample["name"],
                "expected_decision": sample["expected_decision"],
                "demo_file": str(target.relative_to(ROOT_DIR)),
                "source": "LFW funneled public face dataset",
            }
        )
    return copied


def write_readme(yolo_samples: list[dict[str, Any]], face_samples: list[dict[str, Any]]) -> None:
    lines = [
        "# 第27组真实测试数据集",
        "",
        "本文件夹专门给老师验收和手动测试使用，所有图片均为真实照片，不再使用绘制图、卡通图或合成图。",
        "",
        "## 文件夹说明",
        "",
        "- `YOLO识别/无人机`：真实无人机照片，对应标签 `drone`。",
        "- `YOLO识别/灭火器`：真实灭火器照片，对应标签 `fire_extinguisher`。",
        "- `YOLO识别/灯泡`：真实灯泡照片，对应标签 `light_bulb`。",
        "- `人脸识别/已录入人员_应允许`：LFW 已录入 5 人测试照，应输出 `allow`。",
        "- `人脸识别/未录入人员_应拒绝`：LFW 未录入 5 人测试照，应输出 `deny`。",
        "",
        "## GUI 快捷样本",
        "",
    ]
    for label, (cn_dir, _) in LABEL_DIRS.items():
        first = next(item for item in yolo_samples if item["expected_label"] == label and item["rank"] == 1)
        lines.append(
            f"- {cn_dir}: `{first['demo_file']}`，当前模型最高匹配置信度 `{first['expected_confidence']:.4f}`。"
        )

    lines.extend(
        [
            "",
            "## 数据来源",
            "",
            "- YOLO 样本：Wikimedia Commons 真实照片，来源与许可见 `manifest.json`。",
            "- 人脸样本：LFW funneled 公开人脸数据集，仅用于课堂原型演示。",
            "",
            "## 手动测试方式",
            "",
            "1. 打开 GUI。",
            "2. 在 YOLO 区选择无人机、灭火器或灯泡，也可以直接点真实样本按钮。",
            "3. 在人脸区选择 P01/P02/U01 或上传本文件夹中的人脸照片。",
            "4. 查看识别标签、置信度、allow/deny 和物联联动命令。",
        ]
    )
    (DATASET_DIR / "README_真实测试数据集.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ensure_clean_dir(DATASET_DIR)
    candidates = [score_candidate(item) for item in download_yolo_candidates()]
    yolo_samples = copy_ranked_yolo_samples(candidates)
    face_samples = copy_face_samples()

    manifest = {
        "dataset_dir": str(DATASET_DIR.relative_to(ROOT_DIR)),
        "yolo_samples": yolo_samples,
        "face_samples": face_samples,
        "gui_samples": {
            label: next(
                item["public_url"]
                for item in yolo_samples
                if item["expected_label"] == label and item["rank"] == 1
            )
            for label in LABEL_DIRS
        },
    }
    (DATASET_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_readme(yolo_samples, face_samples)
    print(json.dumps(manifest["gui_samples"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
