# 物联接口闭环说明

本步骤只完成物联/智科接口闭环，不修改 YOLO 训练逻辑。

## 启动后端

```powershell
cd C:\Users\74975\Desktop\codex\smart_home_ist
.\.venv\Scripts\python.exe backend\server.py
```

浏览器打开：

```text
http://127.0.0.1:8000
```

## 硬件组最小联调流程

1. 设备上报状态：

```http
POST /api/device/telemetry
```

```json
{
  "device_id": "orange-pi-main",
  "captured_at": "2026-07-07T13:00:00+08:00",
  "temperature_c": 28.4,
  "door_open": false,
  "window_open": true,
  "light_level": 70,
  "fan_on": false
}
```

2. GUI 或后端下发命令：

```http
POST /api/commands
```

```json
{
  "device_id": "orange-pi-main",
  "type": "SET_LIGHT",
  "payload": { "level": 80 }
}
```

3. 设备拉取待执行命令：

```http
GET /api/device/commands/pending?device_id=orange-pi-main
```

4. 设备执行后确认：

```http
POST /api/device/commands/{command_id}/ack
```

```json
{
  "device_id": "orange-pi-main",
  "status": "done",
  "message": "light level set to 80"
}
```

5. 摄像头上传图片：

```http
POST /api/device/photos
Content-Type: multipart/form-data
```

字段：

- `device_id`: `orange-pi-main`
- `captured_at`: 拍摄时间
- `image`: 图片文件

## GUI/答辩展示接口

- `GET /api/devices`：设备列表。
- `GET /api/status/latest`：最新状态。
- `GET /api/telemetry?limit=200`：历史状态。
- `GET /api/photos?limit=50`：图片识别历史。
- `GET /api/events?limit=100`：事件流。

## 自动验收

后端启动后运行：

```powershell
.\.venv\Scripts\python.exe scripts\test_iot_integration.py
```

脚本会自动完成：

- 5 条设备状态上报。
- 最新状态和历史状态查询。
- 创建一条 `SET_LIGHT` 命令。
- 设备拉取命令。
- 设备 ack 标记 done。
- 上传一张测试图片。
- 查询事件流确认 telemetry、command、photo 都出现。

验收记录写入：

```text
docs/iot_integration_checklist.md
```
