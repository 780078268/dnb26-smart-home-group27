# 香橙派板端接入后端 API 说明

版本：v0.2  
面向对象：负责 Orange Pi / 设备网关的同学  
默认设备 ID：`orange-pi-main`

## 1. 接入结论

Orange Pi 作为设备网关和 YOLO 边缘节点，负责从硬件侧收集传感器、摄像头、灯、风扇、门窗等数据，在本地运行 YOLO，然后通过 HTTP 发给服务器后端。

板端主要只需要对接下面 5 类接口：

| 功能 | 方法与路径 | 说明 |
| --- | --- | --- |
| 健康检查 | `GET /api/health` | 确认后端是否在线 |
| 状态上报 | `POST /api/device/telemetry` | 上传温度、门窗、灯光、风扇状态 |
| 图片与 YOLO 结果上传 | `POST /api/device/photos` | 上传摄像头照片和 Orange Pi 本地 YOLO 结果 |
| 拉取命令 | `GET /api/device/commands/pending?device_id=orange-pi-main` | Orange Pi 轮询后端待执行命令 |
| 命令回执 | `POST /api/device/commands/{command_id}/ack` | 执行完命令后告诉后端结果 |

## 2. 基础地址

生产服务器地址：

```text
http://82.156.238.244
```

Orange Pi 实机联调不要使用 `localhost`，固定使用上面的生产服务器地址。只有后端同学在自己电脑本地自测 API 时，才使用本地地址，例如：

```text
http://localhost:8000
```

所有 JSON 请求都使用：

```http
Content-Type: application/json
```

统一返回格式：

```json
{
  "ok": true,
  "data": {},
  "error": null
}
```

错误格式：

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "INVALID_REQUEST",
    "message": "temperature_c is required"
  }
}
```

时间字段统一使用 ISO 8601，例如：

```text
2026-07-06T09:30:00+08:00
```

## 3. 健康检查

用于确认 Orange Pi 能连上后端。

```http
GET /api/health
```

curl 示例：

```bash
curl http://82.156.238.244/api/health
```

返回示例：

```json
{
  "ok": true,
  "data": {
    "status": "ok",
    "version": "0.1"
  },
  "error": null
}
```

## 4. 状态上报

Orange Pi 收到硬件侧传感器数据后，调用这个接口上传给后端。

```http
POST /api/device/telemetry
```

请求 JSON：

```json
{
  "device_id": "orange-pi-main",
  "captured_at": "2026-07-06T09:30:00+08:00",
  "temperature_c": 28.4,
  "door_open": false,
  "window_open": true,
  "light_level": 70,
  "fan_on": true
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `device_id` | string | 是 | 设备 ID，默认 `orange-pi-main` |
| `captured_at` | string | 否 | 采集时间，ISO 8601 格式 |
| `temperature_c` | number | 是 | 摄氏温度 |
| `door_open` | boolean | 否 | 门是否打开 |
| `window_open` | boolean | 否 | 窗是否打开 |
| `light_level` | integer | 否 | 灯光亮度，0-100 |
| `fan_on` | boolean | 否 | 风扇是否开启 |

curl 示例：

```bash
curl -X POST http://82.156.238.244/api/device/telemetry \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "orange-pi-main",
    "temperature_c": 28.4,
    "door_open": false,
    "window_open": true,
    "light_level": 70,
    "fan_on": true
  }'
```

返回示例：

```json
{
  "ok": true,
  "data": {
    "saved": true
  },
  "error": null
}
```

## 5. 图片上传

Orange Pi 拍到照片后，先在本地运行 YOLO，再通过这个接口把图片和 YOLO 结果一起传给服务器后端。后端负责保存图片、保存识别结果，并给前端展示。

```http
POST /api/device/photos
```

请求类型：

```http
multipart/form-data
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `device_id` | string | 是 | 设备 ID，默认 `orange-pi-main` |
| `captured_at` | string | 否 | 拍摄时间，ISO 8601 格式 |
| `image` | file | 是 | 图片文件 |
| `yolo_labels_json` | string | 是 | Orange Pi 本地 YOLO 输出的 JSON 字符串；没有目标时传 `[]` |

curl 示例：

```bash
curl -X POST http://82.156.238.244/api/device/photos \
  -F "device_id=orange-pi-main" \
  -F "captured_at=2026-07-06T09:30:00+08:00" \
  -F "image=@/home/orangepi/test.jpg" \
  -F 'yolo_labels_json=[{"label":"person","confidence":0.91}]'
```

返回示例：

```json
{
  "ok": true,
  "data": {
    "id": "photo_001",
    "device_id": "orange-pi-main",
    "captured_at": "2026-07-06T09:30:00+08:00",
    "file_url": "/uploads/photo_001.jpg",
    "yolo_labels": [
      {
        "label": "person",
        "confidence": 0.91
      }
    ],
    "face_result": {
      "matched_person_id": "person_001",
      "matched_name": "Person A",
      "confidence": 0.86
    },
    "access_decision": "allow"
  },
  "error": null
}
```

## 6. 拉取待执行命令

前端点击开灯、开风扇、开门后，后端会生成命令。Orange Pi 周期性轮询这个接口，拿到命令后控制硬件。

```http
GET /api/device/commands/pending?device_id=orange-pi-main
```

建议轮询间隔：1-2 秒。

curl 示例：

```bash
curl "http://82.156.238.244/api/device/commands/pending?device_id=orange-pi-main"
```

返回示例：

```json
{
  "ok": true,
  "data": [
    {
      "id": "cmd_001",
      "device_id": "orange-pi-main",
      "type": "SET_LIGHT",
      "payload": {
        "level": 80
      },
      "status": "pending",
      "created_at": "2026-07-06T09:31:00+08:00"
    }
  ],
  "error": null
}
```

命令类型约定：

| 命令类型 | payload 示例 | 说明 |
| --- | --- | --- |
| `SET_LIGHT` | `{ "level": 80 }` | 设置灯光亮度，0-100 |
| `SET_FAN` | `{ "on": true }` | 开关风扇 |
| `OPEN_DOOR` | `{}` | 开门 |
| `CLOSE_DOOR` | `{}` | 关门 |
| `REQUEST_PHOTO` | `{}` | 请求 Orange Pi 拍照并上传 |

## 7. 命令执行回执

Orange Pi 执行完命令后，调用这个接口告诉后端执行成功或失败。

```http
POST /api/device/commands/{command_id}/ack
```

请求 JSON：

```json
{
  "device_id": "orange-pi-main",
  "status": "done",
  "message": "light level set to 80"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `device_id` | string | 是 | 设备 ID |
| `status` | string | 是 | `done` 或 `failed` |
| `message` | string | 否 | 执行说明或错误原因 |

curl 示例：

```bash
curl -X POST http://82.156.238.244/api/device/commands/cmd_001/ack \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "orange-pi-main",
    "status": "done",
    "message": "light level set to 80"
  }'
```

返回示例：

```json
{
  "ok": true,
  "data": {
    "updated": true
  },
  "error": null
}
```

## 8. Orange Pi Python 最小示例

安装依赖：

```bash
pip install requests
```

示例代码：

```python
import json
import time
from datetime import datetime, timezone

import requests

BASE_URL = "http://82.156.238.244"
DEVICE_ID = "orange-pi-main"


def iso_now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def upload_telemetry():
    payload = {
        "device_id": DEVICE_ID,
        "captured_at": iso_now(),
        "temperature_c": 28.4,
        "door_open": False,
        "window_open": True,
        "light_level": 70,
        "fan_on": True,
    }
    r = requests.post(f"{BASE_URL}/api/device/telemetry", json=payload, timeout=5)
    print("telemetry:", r.status_code, r.text)


def run_yolo(path):
    # TODO: 替换成 Orange Pi 上真实的 YOLO 推理代码。
    return [
        {"label": "person", "confidence": 0.91},
    ]


def upload_photo(path):
    yolo_labels = run_yolo(path)
    with open(path, "rb") as f:
        files = {"image": f}
        data = {
            "device_id": DEVICE_ID,
            "captured_at": iso_now(),
            "yolo_labels_json": json.dumps(yolo_labels, ensure_ascii=False),
        }
        r = requests.post(f"{BASE_URL}/api/device/photos", data=data, files=files, timeout=20)
    print("photo:", r.status_code, r.text)


def poll_commands():
    r = requests.get(
        f"{BASE_URL}/api/device/commands/pending",
        params={"device_id": DEVICE_ID},
        timeout=5,
    )
    r.raise_for_status()
    body = r.json()
    return body.get("data", []) if body.get("ok") else []


def ack_command(command_id, status="done", message="ok"):
    payload = {
        "device_id": DEVICE_ID,
        "status": status,
        "message": message,
    }
    r = requests.post(
        f"{BASE_URL}/api/device/commands/{command_id}/ack",
        json=payload,
        timeout=5,
    )
    print("ack:", r.status_code, r.text)


def handle_command(cmd):
    command_type = cmd["type"]
    payload = cmd.get("payload") or {}

    try:
        if command_type == "SET_LIGHT":
            level = payload.get("level", 0)
            print("set light:", level)
        elif command_type == "SET_FAN":
            on = payload.get("on", False)
            print("set fan:", on)
        elif command_type == "OPEN_DOOR":
            print("open door")
        elif command_type == "CLOSE_DOOR":
            print("close door")
        elif command_type == "REQUEST_PHOTO":
            print("request photo")
            upload_photo("/home/orangepi/test.jpg")
        else:
            raise ValueError(f"unknown command: {command_type}")

        ack_command(cmd["id"], "done", "ok")
    except Exception as exc:
        ack_command(cmd["id"], "failed", str(exc))


if __name__ == "__main__":
    while True:
        upload_telemetry()
        for command in poll_commands():
            handle_command(command)
        time.sleep(2)
```

## 9. 串口备用方案

如果硬件侧不能直接联网，则硬件通过串口给 Orange Pi 发 JSON Lines，Orange Pi 再转成上面的 HTTP 接口。

硬件发状态：

```json
{"type":"telemetry","device_id":"orange-pi-main","temperature_c":28.4,"door_open":false,"window_open":true,"light_level":70,"fan_on":true}
```

Orange Pi 给硬件发命令：

```json
{"type":"command","id":"cmd_001","command":"SET_LIGHT","payload":{"level":80}}
```

硬件回执：

```json
{"type":"ack","id":"cmd_001","status":"done","message":"ok"}
```

## 10. 联调 checklist

- [ ] Orange Pi 能访问服务器 `http://82.156.238.244`。
- [ ] 服务器安全组和防火墙允许访问 HTTP 服务端口。
- [ ] Orange Pi 能 `curl http://82.156.238.244/api/health`。
- [ ] Orange Pi 能成功上传一条 `/api/device/telemetry`。
- [ ] 前端或后端能看到最新状态变化。
- [ ] 前端创建控制命令后，Orange Pi 能从 pending 接口拉到命令。
- [ ] Orange Pi 执行命令后能调用 ack 接口。
- [ ] 摄像头图片能通过 `/api/device/photos` 上传成功。
