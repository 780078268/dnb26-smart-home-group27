# 香橙派板端接入后端 API 说明

版本：v0.4
面向对象：负责 Orange Pi / 设备网关的同学
默认设备 ID：`orange-pi-main`

## 1. 接入结论

Orange Pi 作为设备网关和 YOLO/人脸识别边缘节点，负责从硬件侧收集传感器、摄像头、灯、风扇、门窗等数据，在本地运行识别，然后通过 HTTP 发给服务器后端。

因为 Orange Pi 在校园网或网线直连笔记本场景下通常没有公网 IP，所以服务器不会主动访问 Orange Pi。所有链路都由 Orange Pi 主动访问公网服务器：

```text
前端按钮 -> 后端生成命令 -> Orange Pi 轮询命令 -> Orange Pi 本地识别 -> Orange Pi 上传结果
```

识别上传策略：

- 人脸识别：Orange Pi 本地每 0.5 秒自动识别，可以高频上传最新结果；后端只覆盖 latest，且同一个人或同一个陌生人 5 秒内不重复写历史。
- 灭火器/无人机：只在前端发起主动识别命令后执行；执行完上传一次结果，后端同样做 5 秒事件冷却。
- 图片不再每张都永久保存：`latest` 图片固定覆盖，只有关键事件才进入 `/api/photos` 历史。

板端主要只需要对接下面 5 类接口：

| 功能 | 方法与路径 | 说明 |
| --- | --- | --- |
| 健康检查 | `GET /api/health` | 确认后端是否在线 |
| 状态上报 | `POST /api/device/telemetry` | 上传温度、门窗、灯光、风扇状态 |
| 图片与识别结果上传 | `POST /api/device/photos` | 覆盖 latest；关键事件通过 5 秒冷却后进入历史 |
| 查询最新识别结果 | `GET /api/photos/latest?device_id=orange-pi-main` | 前端实时展示最新图片和标签 |
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

## 5. 图片与识别结果上传

Orange Pi 拍到照片后，先在本地运行 YOLO/人脸识别，再通过这个接口把图片和识别结果一起传给服务器后端。

后端处理规则：

- 每次上传都会覆盖最新结果，图片固定保存在类似 `/uploads/latest/orange-pi-main.jpg` 的路径。
- 只有识别事件通过 5 秒冷却后，才会额外复制一份到 `/uploads/photos/` 并写入历史。
- 同一个人脸、同一个陌生人、同一个灭火器/无人机事件 5 秒内重复上传时，`history_saved` 会是 `false`。

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
| `face_result_json` | string | 否 | Orange Pi 本地人脸识别结果 JSON 字符串 |
| `access_decision` | string | 否 | `allow`、`deny` 或 `unknown` |
| `source` | string | 否 | 上传来源：`auto_face`、`fire_extinguisher`、`drone`，默认 `auto_face` |
| `mode` | string | 否 | `latest` 或 `event`；人脸自动识别用 `latest`，主动识别用 `event` |

人脸自动识别上传示例：

```bash
curl -X POST http://82.156.238.244/api/device/photos \
  -F "device_id=orange-pi-main" \
  -F "source=auto_face" \
  -F "mode=latest" \
  -F "image=@/home/orangepi/face.jpg" \
  -F 'yolo_labels_json=[{"label":"person","confidence":0.91}]' \
  -F 'face_result_json={"matched_person_id":"person_001","matched_name":"张三","confidence":0.86}' \
  -F "access_decision=allow"
```

主动识别无人机上传示例：

```bash
curl -X POST http://82.156.238.244/api/device/photos \
  -F "device_id=orange-pi-main" \
  -F "source=drone" \
  -F "mode=event" \
  -F "captured_at=2026-07-06T09:30:00+08:00" \
  -F "image=@/home/orangepi/drone.jpg" \
  -F 'yolo_labels_json=[{"label":"drone","confidence":0.88}]'
```

返回示例：

```json
{
  "ok": true,
	  "data": {
	    "id": "latest_orange-pi-main",
	    "device_id": "orange-pi-main",
	    "captured_at": "2026-07-06T09:30:00+08:00",
	    "file_url": "/uploads/latest/orange-pi-main.jpg",
	    "yolo_labels": [
	      {
	        "label": "drone",
	        "confidence": 0.88
	      }
	    ],
	    "face_result": {
	      "matched_person_id": null,
	      "matched_name": null,
	      "confidence": 0
	    },
	    "access_decision": "unknown",
	    "source": "drone",
	    "updated_at": "2026-07-06T09:30:00+08:00",
	    "history_saved": true,
	    "history_photo_id": "photo_abc123",
	    "history_file_url": "/uploads/photos/photo_abc123_xxx.jpg",
	    "event_key": "active:drone:drone",
	    "cooldown_seconds": 5
	  },
	  "error": null
	}
```

## 6. 查询最新识别结果

前端实时展示使用这个接口。它只返回每台设备最新的一帧，不代表历史。

```http
GET /api/photos/latest?device_id=orange-pi-main
```

curl 示例：

```bash
curl "http://82.156.238.244/api/photos/latest?device_id=orange-pi-main"
```

## 7. 拉取待执行命令

前端点击开灯、开风扇、开门、识别灭火器、识别无人机后，后端会生成命令。Orange Pi 周期性轮询这个接口，拿到命令后控制硬件或执行本地识别。

```http
GET /api/device/commands/pending?device_id=orange-pi-main
```

建议轮询间隔：0.5-1 秒。

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
| `REQUEST_DETECT_FIRE_EXTINGUISHER` | `{ "target": "fire_extinguisher", "upload_mode": "event" }` | 主动识别灭火器 |
| `REQUEST_DETECT_DRONE` | `{ "target": "drone", "upload_mode": "event" }` | 主动识别无人机 |

## 8. 命令执行回执

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

## 9. Orange Pi Python 最小示例

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


def upload_photo(path, source="auto_face", mode="latest", face_result=None, access_decision=None):
    yolo_labels = run_yolo(path)
    with open(path, "rb") as f:
        files = {"image": f}
        data = {
            "device_id": DEVICE_ID,
            "captured_at": iso_now(),
            "yolo_labels_json": json.dumps(yolo_labels, ensure_ascii=False),
            "source": source,
            "mode": mode,
        }
        if face_result is not None:
            data["face_result_json"] = json.dumps(face_result, ensure_ascii=False)
        if access_decision is not None:
            data["access_decision"] = access_decision
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
        elif command_type == "REQUEST_DETECT_FIRE_EXTINGUISHER":
            print("detect fire extinguisher")
            upload_photo("/home/orangepi/fire_extinguisher.jpg", source="fire_extinguisher", mode="event")
        elif command_type == "REQUEST_DETECT_DRONE":
            print("detect drone")
            upload_photo("/home/orangepi/drone.jpg", source="drone", mode="event")
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
        time.sleep(1)
```

人脸自动识别循环可以单独每 0.5 秒跑一次，本地识别后调用：

```python
upload_photo(
    "/home/orangepi/face_latest.jpg",
    source="auto_face",
    mode="latest",
    face_result={"matched_person_id": "person_001", "matched_name": "张三", "confidence": 0.86},
    access_decision="allow",
)
```

后端会覆盖 latest，并按 5 秒冷却决定是否写历史。

## 10. 串口备用方案

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

## 11. 联调 checklist

- [ ] Orange Pi 能访问服务器 `http://82.156.238.244`。
- [ ] 服务器安全组和防火墙允许访问 HTTP 服务端口。
- [ ] Orange Pi 能 `curl http://82.156.238.244/api/health`。
- [ ] Orange Pi 能成功上传一条 `/api/device/telemetry`。
- [ ] 前端或后端能看到最新状态变化。
- [ ] 前端创建控制命令后，Orange Pi 能从 pending 接口拉到命令。
- [ ] Orange Pi 执行命令后能调用 ack 接口。
- [ ] 摄像头图片能通过 `/api/device/photos` 上传成功。
- [ ] 前端点击“识别灭火器/识别无人机”后，Orange Pi 能拉到 `REQUEST_DETECT_*` 命令。
- [ ] 人脸自动识别重复上传时，`history_saved` 在 5 秒内不会一直为 `true`。
- [ ] 前端能通过 `/api/photos/latest` 看到最新画面。
