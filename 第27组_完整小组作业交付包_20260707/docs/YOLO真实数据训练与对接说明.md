# YOLO 最终模型与后端对接说明

负责人：汪任城  
目标：完成老师要求的 `drone`、`fire_extinguisher` 识别，并保留创新点 `light_bulb` 自动开灯。

## 1. 模块边界

YOLO 只负责图片目标识别和灯泡联动判断，不负责人脸识别、传感器采集、数据库页面和 GUI 绘制。

统一输出格式：

```json
[
  { "label": "drone", "confidence": 0.91 },
  { "label": "fire_extinguisher", "confidence": 0.88 },
  { "label": "light_bulb", "confidence": 0.78 }
]
```

显示到 GUI 或答辩材料时可翻译为：

- `drone`：无人机
- `fire_extinguisher`：灭火器
- `light_bulb`：灯泡

## 2. 已接入模型

默认模型：

```text
models/group27_v3_balanced_lowlr_yolov8m_960_candidate.pt
```

模型来源：

```text
E:\group27_yolo_interface_package\models\group27_v3_balanced_lowlr_yolov8m_960_candidate.pt
```

后端默认加载该模型；如果需要临时替换，可设置：

```powershell
$env:YOLO_MODEL_PATH="models\your_model.pt"
```

## 3. 后端接口

后端已支持小组 OpenAPI 契约：

- 图片上传：`POST /api/device/photos`
- 查询识别结果：`GET /api/photos`
- 设备拉取命令：`GET /api/device/commands/pending?device_id=orange-pi-main`
- 设备确认命令：`POST /api/device/commands/{command_id}/ack`

本地 GUI 兼容接口 `/api/images` 仍可继续使用，不影响演示。

## 4. 推理参数

默认参数与 YOLO 包保持一致：

- 展示阈值：`YOLO_DISPLAY_CONFIDENCE=0.45`
- 图片尺寸：`YOLO_IMAGE_SIZE=960`
- 灯泡自动开灯阈值：`YOLO_LIGHT_COMMAND_CONFIDENCE=0.55`
- 灯泡自动开灯亮度：`YOLO_LIGHT_COMMAND_LEVEL=80`
- TTA 增强：默认关闭，可用 `$env:YOLO_AUGMENT="1"` 打开，识别更稳但速度更慢。

灯泡联动规则：

```json
{
  "device_id": "orange-pi-main",
  "type": "SET_LIGHT",
  "payload": { "level": 80 },
  "reason": "detected_light_bulb"
}
```

## 5. 训练指标说明

默认 v3 模型普通验证：

```text
overall mAP50: 0.80660
drone AP50: 0.74477
fire_extinguisher AP50: 0.89698
light_bulb AP50: 0.77504
```

默认 v3 模型开启 TTA：

```text
overall mAP50: 0.83172
drone AP50: 0.77915
fire_extinguisher AP50: 0.91046
light_bulb AP50: 0.80556
```

无人机辅助 v4 模型开启 TTA：

```text
drone AP50: 0.81212
fire_extinguisher AP50: 0.93037
light_bulb AP50: 0.76808
```

最终选择 v3 作为默认模型，因为三类综合最稳；v4 只作为无人机增强实验依据，不默认接入，避免拉低灯泡识别。

## 6. 推理测试

单张图片：

```powershell
.\.venv\Scripts\python.exe scripts\predict_yolo.py path\to\image.jpg --model models\group27_v3_balanced_lowlr_yolov8m_960_candidate.pt --conf 0.45
```

后端对接验收：

```powershell
.\.venv\Scripts\python.exe scripts\test_yolo_integration.py
```

验收记录写入：

```text
docs/yolo_integration_checklist.md
```

## 7. 和组员对接

- 后端：调用 `recognizer.detect_objects(image_path)` 或 `recognizer.analyze_image(image_path)`，保存 `yolo_labels`。
- 前端：读取 `/api/photos` 或 `/api/device/photos` 返回的 `file_url`、`yolo_labels`、`auto_commands`。
- 硬件：上传摄像头图片，并轮询 pending commands。
- 人脸识别：与 YOLO 并行执行；人脸模块判别授权，YOLO 模块负责无人机/灭火器/灯泡目标识别与灯泡联动。
