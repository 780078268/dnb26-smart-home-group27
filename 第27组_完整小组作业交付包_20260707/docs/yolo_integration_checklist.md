# YOLO 最终对接验收记录

## 对接来源

- 外部 YOLO 包：`E:\group27_yolo_interface_package`
- 重点接口：`scripts/yolo_api_adapter.py`、`scripts/yolo_contract.py`
- 默认模型：`models/group27_v3_balanced_lowlr_yolov8m_960_candidate.pt`
- 统一标签：`drone`、`fire_extinguisher`、`light_bulb`

## 后端合同

```json
[
  {
    "label": "drone",
    "confidence": 0.91
  },
  {
    "label": "fire_extinguisher",
    "confidence": 0.88
  },
  {
    "label": "light_bulb",
    "confidence": 0.78
  }
]
```

检测到 `light_bulb` 且置信度大于等于 `0.55` 时，生成 `SET_LIGHT` 命令，亮度为 `80`。

## 本次自动验收

- 模型路径：`C:\Users\74975\Desktop\codex\smart_home_ist\models\group27_v3_balanced_lowlr_yolov8m_960_candidate.pt`
- 模型大小：207451485 bytes
- 实际加载模型：`C:\Users\74975\Desktop\codex\smart_home_ist\models\group27_v3_balanced_lowlr_yolov8m_960_candidate.pt`
- 模型加载引擎：`group27-yolov8m-custom`
- 测试图片：`C:\Users\74975\Desktop\codex\smart_home_ist\data\yolo_test_samples\light_bulb_contract_demo.jpg`
- 合同标签归一结果：`[{"label": "light_bulb", "confidence": 0.78}, {"label": "fire_extinguisher", "confidence": 0.62}, {"label": "drone", "confidence": 0.59}]`
- 合同命令结果：`[{"device_id": "orange-pi-main", "type": "SET_LIGHT", "payload": {"level": 80}, "reason": "detected_light_bulb", "source_confidence": 0.78}]`
- 后端识别引擎：`demo-yolo-compatible`
- 后端返回标签：`[{"label": "light_bulb", "confidence": 0.86}]`
- 后端命令函数：`[{"device_id": "orange-pi-main", "type": "SET_LIGHT", "payload": {"level": 80}, "reason": "detected_light_bulb", "source_confidence": 0.56}]`
- 备注：本脚本生成的灯泡图是合同冒烟样本，用于验证接口和命令；真实识别准确率以训练包 metrics 为准。

## 检查项

- `model_exists`：通过
- `real_model_loaded`：通过
- `real_model_is_group27_v3`：通过
- `contract_labels_are_supported`：通过
- `contract_light_command_level_80`：通过
- `backend_light_command_level_80`：通过
- `backend_output_is_list`：通过
- `backend_engine_present`：通过

## 训练指标说明

- 默认 v3 模型普通验证 overall mAP50 为 `0.80660`。
- 默认 v3 模型开启 TTA 后 overall mAP50 为 `0.83172`，`light_bulb AP50=0.80556`。
- 无人机辅助 v4 模型开启 TTA 后 `drone AP50=0.81212`，但会降低灯泡 AP50，因此不作为默认模型。
- 本项目对接不虚报单类 88%；答辩建议展示真实指标、接口闭环和灯泡创新联动。

## 结论

- YOLO 接口对接：通过
- 已保留人脸识别与物联接口闭环，不影响其它模块。
