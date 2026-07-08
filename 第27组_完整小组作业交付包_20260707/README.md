# Smart Home IST 智科智能家居系统

这是 DnB26 智能家居题目中智科方向的可演示原型，包含后端、数据库、GUI、YOLO 目标识别、人脸门禁和远程控制接口。

## 项目位置

```text
C:\Users\74975\Desktop\codex\smart_home_ist
```

所有项目文件都放在这个文件夹里。

## 已安装环境

项目使用独立虚拟环境：

```text
.venv
```

主要依赖：

- FastAPI：后端 API
- Uvicorn：运行服务器
- SQLite：本地数据库
- Pillow / OpenCV：图像处理
- Ultralytics：真实 YOLO 推理与训练
- 原生 HTML/CSS/JS：GUI 页面

## 启动方法

在 PowerShell 中进入项目目录：

```powershell
cd C:\Users\74975\Desktop\codex\smart_home_ist
.\.venv\Scripts\python.exe backend\server.py
```

浏览器打开：

```text
http://127.0.0.1:8000
```

## 演示内容

1. Dashboard 展示温度、门、窗、灯光、风扇状态。
2. 点击“模拟传感器更新”，生成历史数据。
3. 在 YOLO 区选择无人机、灭火器或灯泡，上传真实测试照片，系统优先使用第 27 组最终 YOLOv8m 权重识别。
4. 点击“使用真实无人机样本 / 使用真实灭火器样本 / 使用真实灯泡样本”，可直接从 `真实测试数据集` 加载验收照片。
5. 在人脸区选择 LFW 真实人脸样本，完成 5 人录入、5 人未录入，以及 2 真 1 假快捷验收。
6. 上传无人机或灭火器图片时展示目标识别结果，不生成开门命令。
7. 上传灯泡图片时生成 `SET_LIGHT` 亮度 80 的开灯命令，不生成开门命令。
8. 点击开灯、关灯、开风扇等按钮，生成远程控制命令。
9. 历史数据表展示传感器、图片识别、控制命令记录。

## 文件结构

```text
smart_home_ist/
  README.md
  requirements.txt
  backend/
    server.py
    database.py
    recognizer.py
  frontend/
    index.html
    style.css
    app.js
  data/
    smart_home.db
    uploads/
    authorized_faces/
    demo_images/
  真实测试数据集/
    YOLO识别/
    人脸识别/
    README_真实测试数据集.md
  旧版绘制样本_不用于验收/
  docs/
    API接口文档.md
    YOLO真实数据训练与对接说明.md
    智科分工说明.md
    演示脚本.md
    阶段1成果清单.md
  scripts/
    prepare_real_demo_dataset.py
    prepare_open_images_yolo.py
    train_yolo.py
    predict_yolo.py
```

## 给硬件组的集成方式

推荐硬件组按仓库 OpenAPI 契约调用接口：

- 上传传感器数据：`POST /api/device/telemetry`
- 上传摄像头图片：`POST /api/device/photos`
- 拉取待执行命令：`GET /api/device/commands/pending?device_id=orange-pi-main`
- 确认命令执行：`POST /api/device/commands/{id}/ack`

本地旧 GUI 兼容接口仍可使用：

- 上传传感器数据：`POST /api/sensors`
- 上传摄像头图片：`POST /api/images`
- 拉取待执行命令：`GET /api/commands/pending`
- 标记命令已执行：`POST /api/commands/{id}/executed`

详细格式见 `docs/API接口文档.md`。

## YOLO 最终模型对接

详见 `docs/YOLO真实数据训练与对接说明.md`。

当前默认模型来自另一个训练环境的最终交付包：

```text
models/group27_v3_balanced_lowlr_yolov8m_960_candidate.pt
```

统一识别标签为 `drone`、`fire_extinguisher`、`light_bulb`。灯泡置信度达到 `0.55` 后自动生成 `SET_LIGHT` 命令，亮度为 `80`。

对接验收：

```powershell
.\.venv\Scripts\python.exe scripts\test_yolo_integration.py
```

## 后续增强

- 接入真实摄像头、ESP32、Arduino 或树莓派。
- 增加登录权限、图表统计、报警通知。
