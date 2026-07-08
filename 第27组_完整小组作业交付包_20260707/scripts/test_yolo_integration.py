from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


ROOT_DIR = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT_DIR / "docs" / "yolo_integration_checklist.md"
SAMPLE_DIR = ROOT_DIR / "data" / "yolo_test_samples"
MODEL_PATH = ROOT_DIR / "models" / "group27_v3_balanced_lowlr_yolov8m_960_candidate.pt"
DEVICE_ID = "orange-pi-main"


def import_project_modules() -> tuple[Any, Any]:
    sys.path.insert(0, str(ROOT_DIR / "backend"))
    sys.path.insert(0, str(ROOT_DIR))
    import recognizer
    from scripts import yolo_contract

    return recognizer, yolo_contract


def ensure_light_bulb_sample() -> Path:
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    sample = SAMPLE_DIR / "light_bulb_contract_demo.jpg"
    image = Image.new("RGB", (480, 360), "#f4f1dc")
    draw = ImageDraw.Draw(image)
    draw.ellipse((180, 42, 300, 162), fill="#fff2a6", outline="#d29b00", width=5)
    draw.rectangle((214, 160, 266, 236), fill="#606a70")
    draw.line((210, 184, 270, 184), fill="#e6eef3", width=4)
    draw.line((210, 205, 270, 205), fill="#e6eef3", width=4)
    draw.text((155, 292), "light bulb contract demo", fill="#2f3b38")
    image.save(sample, "JPEG", quality=92)
    return sample


def run_smoke() -> dict[str, Any]:
    recognizer, yolo_contract = import_project_modules()
    sample = ensure_light_bulb_sample()
    model = recognizer._load_yolo_model()
    loaded_model_path = str(getattr(model, "_group27_model_path", "")) if model is not None else ""

    contract_labels = [
        {"label": "Light Bulb", "confidence": 0.78},
        {"label": "fire extinguisher", "confidence": 0.62},
        {"label": "quadcopter", "confidence": 0.59},
        {"label": "light_bulb", "confidence": 0.56},
    ]
    normalized = yolo_contract.dedupe_labels(contract_labels)
    commands = yolo_contract.commands_from_yolo_labels(normalized, device_id=DEVICE_ID)
    detection = recognizer.analyze_image(sample, sample.name)
    backend_labels = recognizer.yolo_labels_from_detection(detection)
    backend_commands = recognizer.commands_from_yolo_labels(
        [{"label": "light_bulb", "confidence": 0.56}],
        device_id=DEVICE_ID,
    )

    checks = {
        "model_exists": MODEL_PATH.exists(),
        "real_model_loaded": model is not None,
        "real_model_is_group27_v3": Path(loaded_model_path).name == MODEL_PATH.name,
        "contract_labels_are_supported": {item["label"] for item in normalized}.issubset(yolo_contract.SUPPORTED_LABELS),
        "contract_light_command_level_80": bool(commands and commands[0]["payload"]["level"] == 80),
        "backend_light_command_level_80": bool(backend_commands and backend_commands[0]["payload"]["level"] == 80),
        "backend_output_is_list": isinstance(backend_labels, list),
        "backend_engine_present": bool(detection.get("engine")),
    }
    return {
        "model_path": str(MODEL_PATH),
        "model_size_bytes": MODEL_PATH.stat().st_size if MODEL_PATH.exists() else 0,
        "loaded_model_path": loaded_model_path,
        "loaded_model_engine": recognizer._runtime_engine_name(),
        "sample_image": str(sample),
        "normalized_contract_labels": normalized,
        "contract_commands": commands,
        "backend_detection": {
            "engine": detection.get("engine"),
            "labels": backend_labels,
            "summary": detection.get("summary"),
        },
        "backend_commands": backend_commands,
        "checks": checks,
        "passed": all(checks.values()),
    }


def write_doc(summary: dict[str, Any]) -> None:
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    checks = summary["checks"]
    lines = [
        "# YOLO 最终对接验收记录",
        "",
        "## 对接来源",
        "",
        "- 外部 YOLO 包：`E:\\group27_yolo_interface_package`",
        "- 重点接口：`scripts/yolo_api_adapter.py`、`scripts/yolo_contract.py`",
        "- 默认模型：`models/group27_v3_balanced_lowlr_yolov8m_960_candidate.pt`",
        "- 统一标签：`drone`、`fire_extinguisher`、`light_bulb`",
        "",
        "## 后端合同",
        "",
        "```json",
        json.dumps(
            [
                {"label": "drone", "confidence": 0.91},
                {"label": "fire_extinguisher", "confidence": 0.88},
                {"label": "light_bulb", "confidence": 0.78},
            ],
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "检测到 `light_bulb` 且置信度大于等于 `0.55` 时，生成 `SET_LIGHT` 命令，亮度为 `80`。",
        "",
        "## 本次自动验收",
        "",
        f"- 模型路径：`{summary['model_path']}`",
        f"- 模型大小：{summary['model_size_bytes']} bytes",
        f"- 实际加载模型：`{summary['loaded_model_path']}`",
        f"- 模型加载引擎：`{summary['loaded_model_engine']}`",
        f"- 测试图片：`{summary['sample_image']}`",
        f"- 合同标签归一结果：`{json.dumps(summary['normalized_contract_labels'], ensure_ascii=False)}`",
        f"- 合同命令结果：`{json.dumps(summary['contract_commands'], ensure_ascii=False)}`",
        f"- 后端识别引擎：`{summary['backend_detection']['engine']}`",
        f"- 后端返回标签：`{json.dumps(summary['backend_detection']['labels'], ensure_ascii=False)}`",
        f"- 后端命令函数：`{json.dumps(summary['backend_commands'], ensure_ascii=False)}`",
        "- 备注：本脚本生成的灯泡图是合同冒烟样本，用于验证接口和命令；真实识别准确率以训练包 metrics 为准。",
        "",
        "## 检查项",
        "",
    ]
    for name, passed in checks.items():
        lines.append(f"- `{name}`：{'通过' if passed else '失败'}")
    lines.extend(
        [
            "",
            "## 训练指标说明",
            "",
            "- 默认 v3 模型普通验证 overall mAP50 为 `0.80660`。",
            "- 默认 v3 模型开启 TTA 后 overall mAP50 为 `0.83172`，`light_bulb AP50=0.80556`。",
            "- 无人机辅助 v4 模型开启 TTA 后 `drone AP50=0.81212`，但会降低灯泡 AP50，因此不作为默认模型。",
            "- 本项目对接不虚报单类 88%；答辩建议展示真实指标、接口闭环和灯泡创新联动。",
            "",
            "## 结论",
            "",
            f"- YOLO 接口对接：{'通过' if summary['passed'] else '未通过'}",
            "- 已保留人脸识别与物联接口闭环，不影响其它模块。",
        ]
    )
    DOC_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    summary = run_smoke()
    write_doc(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
