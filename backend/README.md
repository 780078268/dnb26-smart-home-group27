# Backend Quick Start

This backend implements the Group 27 smart home API with FastAPI and SQLite.

## Local Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Local health check:

```bash
curl http://127.0.0.1:8000/api/health
```

## Smoke Test

```bash
python scripts/smoke_test.py --base-url http://127.0.0.1:8000
```

## Server Deployment

Recommended server path:

```bash
/opt/dnb26-smart-home-group27
```

The current server already has Apache on port 80, so run FastAPI on local port 8000 and let Apache proxy `/api` and `/uploads`.

Deploy commands on the server:

```bash
cd /opt/dnb26-smart-home-group27
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SMART_HOME_BASE_URL=http://82.156.238.244
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Apache reverse proxy:

```bash
sudo cp deploy/apache-smart-home-api.conf /etc/apache2/sites-available/smart-home-api.conf
sudo a2enmod proxy proxy_http headers
sudo a2ensite smart-home-api.conf
sudo systemctl reload apache2
```

Run as a system service:

```bash
sudo cp deploy/smart-home-backend.service /etc/systemd/system/smart-home-backend.service
sudo systemctl daemon-reload
sudo systemctl enable --now smart-home-backend
sudo systemctl status smart-home-backend
```

Persistent runtime data is written to:

```text
data/smart_home.sqlite3
uploads/
```

These paths are intentionally ignored by git.
