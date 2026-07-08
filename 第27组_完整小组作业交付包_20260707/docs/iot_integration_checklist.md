# 物联接口闭环验收记录

## 验收范围

- 设备列表：`GET /api/devices`
- 最新状态：`GET /api/status/latest`
- 设备状态上报：`POST /api/device/telemetry`
- 历史状态查询：`GET /api/telemetry`
- GUI/后端下发命令：`POST /api/commands`
- 硬件拉取命令：`GET /api/device/commands/pending`
- 硬件确认执行：`POST /api/device/commands/{id}/ack`
- 图片上传链路：`POST /api/device/photos`
- 事件流：`GET /api/events`

## 自动验收结果

- 设备 ID：`orange-pi-main`
- 状态上报数量：5
- 历史查询返回数量：5
- 测试命令：`SET_LIGHT`，状态从 `pending` 变为 `done`
- 图片保存地址：`/uploads/cc9733acf7f3459f9f0a20cfd17fac40.jpg`
- 图片识别标签：[{"label": "light_bulb", "confidence": 0.6286}]
- 事件类型：command, photo, telemetry

## 结论

- 物联接口闭环：通过
- 硬件组可以按接口文档直接联调设备状态、图片、命令拉取和 ack。
- YOLO 最终模型已接入 `models/group27_v3_balanced_lowlr_yolov8m_960_candidate.pt`；后续继续训练时只要保持 `drone`、`fire_extinguisher`、`light_bulb` 字段合同不变即可。
