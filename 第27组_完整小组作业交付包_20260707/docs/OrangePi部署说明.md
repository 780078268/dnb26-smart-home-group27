# Orange Pi 部署说明

本项目可以部署到 Orange Pi，但建议按实际性能选择部署方式。

## 推荐方式：Orange Pi 做硬件端，电脑做识别服务器

这是最稳、最适合课堂答辩的方案。

- Windows/笔记本：运行后端、GUI、YOLO、人脸识别。
- Orange Pi：采集传感器、拍照、上传数据、拉取控制命令、执行灯光/门禁/风扇动作。

优点：

- YOLOv8m 模型在电脑上更快，识别更稳定。
- Orange Pi 只负责物联硬件闭环，不会被模型推理拖慢。
- 老师能同时看到 GUI、历史记录、命令下发和设备确认。

电脑端启动：

```powershell
cd C:\Users\74975\Desktop\codex\smart_home_ist
$env:HOST="0.0.0.0"
.\.venv\Scripts\python.exe backend\server.py
```

查看电脑局域网 IP：

```powershell
ipconfig
```

然后在 Orange Pi 里把服务器地址写成：

```text
http://电脑局域网IP:8000
```

例如：

```text
http://192.168.1.23:8000
```

## Orange Pi 硬件端安装

Orange Pi 建议使用 64 位 Ubuntu / Debian / Armbian 系统。

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv
python3 -m venv ~/smart_home_client_env
source ~/smart_home_client_env/bin/activate
pip install requests
```

复制本项目中的硬件端示例脚本到 Orange Pi：

```text
scripts/orange_pi_device_client.py
```

运行：

```bash
python3 orange_pi_device_client.py --server http://电脑局域网IP:8000
```

这个脚本会演示：

- `POST /api/device/telemetry`：上报温度、门窗、灯光、风扇状态。
- `GET /api/device/commands/pending`：拉取待执行命令。
- `POST /api/device/commands/{id}/ack`：确认命令已执行。
- 可选上传照片到 `POST /api/device/photos`。

## 一体化方式：后端和 GUI 都跑在 Orange Pi

如果一定要把完整项目放到 Orange Pi：

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv libgl1 libglib2.0-0
cd ~/smart_home_ist
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

启动：

```bash
HOST=0.0.0.0 PORT=8000 python backend/server.py
```

浏览器访问：

```text
http://OrangePi局域网IP:8000
```

注意：

- Orange Pi CPU 跑 YOLOv8m 会明显慢，首次加载模型也会慢。
- 如果安装 `ultralytics` 或 `opencv-python` 失败，可先把 Orange Pi 作为硬件端，模型识别继续放在电脑端。
- 若后续要在 Orange Pi 本机高效跑 YOLO，建议把模型导出为 ONNX / NCNN，并使用轻量推理方案。

## systemd 开机自启

在 Orange Pi 上创建服务：

```bash
sudo nano /etc/systemd/system/smart-home-ist.service
```

内容：

```ini
[Unit]
Description=Group27 Smart Home IST Backend
After=network-online.target

[Service]
WorkingDirectory=/home/orangepi/smart_home_ist
Environment=HOST=0.0.0.0
Environment=PORT=8000
ExecStart=/home/orangepi/smart_home_ist/.venv/bin/python backend/server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable smart-home-ist
sudo systemctl start smart-home-ist
sudo systemctl status smart-home-ist
```

查看日志：

```bash
journalctl -u smart-home-ist -f
```

