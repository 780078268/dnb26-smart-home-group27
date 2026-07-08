from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))

import face_service  # noqa: E402


DOC_PATH = ROOT_DIR / "docs" / "face_recognition_checklist.md"


def run_acceptance() -> dict:
    manifest = face_service.load_manifest()
    if not manifest.get("samples"):
        raise RuntimeError("No face samples found. Run scripts/prepare_lfw_face_samples.py first.")

    results = []
    for sample in manifest["samples"]:
        result = face_service.verify_sample(sample["sample_id"])
        results.append(result)

    passed = [item for item in results if item["passed"]]
    failed = [item for item in results if not item["passed"]]
    summary = {
        "total": len(results),
        "passed": len(passed),
        "failed": len(failed),
        "all_passed": not failed,
        "registered_people": manifest.get("registered_people", []),
        "unknown_people": manifest.get("unknown_people", []),
        "results": results,
    }
    return summary


def write_doc(summary: dict) -> None:
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 人脸识别 5 真 5 假验收记录",
        "",
        "## 数据来源",
        "",
        "- 数据集：LFW / Labeled Faces in the Wild, lfw-funneled",
        "- 用途：课堂原型演示的人脸录入与判别测试",
        "- 判定目标：录入 5 人，不录入 5 人；录入人员 allow，未录入人员 deny",
        "",
        "## 已录入人员",
        "",
    ]
    for item in summary["registered_people"]:
        lines.append(f"- `{item['person_id']}`: {item['name']} (`{item['lfw_name']}`)")
    lines.extend(["", "## 未录入测试人员", ""])
    for item in summary["unknown_people"]:
        lines.append(f"- `{item['sample_id']}`: {item['name']} (`{item['lfw_name']}`)")
    lines.extend(
        [
            "",
            "## 10 张测试图判别结果",
            "",
            "| 样本 | 类型 | 人名 | 期望 | 实际 | 匹配人员 | 相似度 | 结果 |",
            "| --- | --- | --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for item in summary["results"]:
        sample = item["sample"]
        matched_name = item.get("matched_name") or "-"
        status = "通过" if item["passed"] else "失败"
        lines.append(
            f"| `{sample['sample_id']}` | {sample['kind']} | {sample['name']} | "
            f"{item['expected_decision']} | {item['decision']} | {matched_name} | "
            f"{item['similarity']:.4f} | {status} |"
        )
    lines.extend(
        [
            "",
            "## 结论",
            "",
            f"- 总样本数：{summary['total']}",
            f"- 通过：{summary['passed']}",
            f"- 失败：{summary['failed']}",
            f"- 5 真 5 假：{'通过' if summary['all_passed'] else '未通过'}",
            "- 老师最低 2 真 1 假：使用 `P01_test`、`P02_test`、`U01_test` 演示。",
        ]
    )
    DOC_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    summary = run_acceptance()
    write_doc(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(0 if summary["all_passed"] else 1)


if __name__ == "__main__":
    main()
