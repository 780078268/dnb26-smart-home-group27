# API 接口文档

后端地址：

```text
http://127.0.0.1:8000
```

## 1. 健康检查

```http
GET /api/health
```

返回：

```json
{
  "ok": true,
  "project": "smart_home_ist"
}
```

## 2. 获取当前状态

```http
GET /api/state
```

用于 GUI 获取温度、门窗、灯光、人员、图片识别和待执行命令。

## 3. 上传传感器数据

```http
POST /api/sensors
Content-Type: application/json
```

请求示例：

```json
{
  "temperature": 28.5,
  "door_open": false,
  "window_open": true,
  "light_level": 70,
  "fan_on": false,
  "source": "esp32"
}
```

说明：

- `temperature`：室内温度。
- `door_open`：门是否打开。
- `window_open`：窗是否打开。
- `light_level`：灯光亮度，0 到 100。
- `fan_on`：风扇是否开启。
- `source`：数据来源，例如 `esp32`、`arduino`、`demo-button`。

如果温度达到 30 摄氏度且风扇未开启，系统会自动生成开风扇命令。

## 4. 上传摄像头图片

```http
POST /api/images
Content-Type: multipart/form-data
```

字段：

- `image`：图片文件。
- `image_type`：默认 `camera`。
- `face_code`：演示身份码，例如 `owner_alice`、`family_bob`、`visitor_chen`。

返回内容包括：

- 图片保存路径。
- 目标识别结果。
- 人脸门禁结果。
- 自动生成的控制命令。

开门规则：

- 图片目标识别为 `person`，且 `face_code` 对应授权人员，系统才生成开门命令。
- 图片目标识别为 `car`、`light_bulb` 或 `unknown` 时，不会生成开门命令。
- 图片目标识别为 `person` 但身份未授权时，不会生成开门命令。

如果识别为灯泡，系统生成开灯命令。

## 5. 发送控制命令

```http
POST /api/control
Content-Type: application/json
```

请求示例：

```json
{
  "device": "light",
  "action": "set_brightness",
  "value": 80,
  "source": "gui"
}
```

常用设备：

- `door`
- `light`
- `fan`

常用动作：

- `open`
- `close`
- `turn_on`
- `turn_off`
- `set_brightness`

## 6. 硬件拉取待执行命令

```http
GET /api/commands/pending
```

硬件组定时调用该接口，获取还没有执行的控制命令。

## 7. 标记命令已执行

```http
POST /api/commands/{command_id}/executed
```

硬件执行完成后调用该接口，避免重复执行。

## 8. 查询历史数据

```http
GET /api/history?kind=sensors&limit=30
GET /api/history?kind=images&limit=30
GET /api/history?kind=commands&limit=30
```

用于 GUI 展示历史数据，也可用于报告里的数据统计分析。
