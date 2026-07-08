#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/780078268/dnb26-smart-home-group27.git}"
APP_DIR="${APP_DIR:-/opt/dnb26-smart-home-group27}"
BASE_URL="${BASE_URL:-http://82.156.238.244}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run with sudo or as root:" >&2
  echo "  sudo bash deploy/install_on_server.sh" >&2
  exit 1
fi

apt-get update
apt-get install -y git python3 python3-venv python3-pip apache2

if [[ -d "${APP_DIR}/.git" ]]; then
  git -C "${APP_DIR}" pull --ff-only
else
  rm -rf "${APP_DIR}"
  git clone "${REPO_URL}" "${APP_DIR}"
fi

cd "${APP_DIR}"

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

install -m 0644 deploy/smart-home-backend.service /etc/systemd/system/smart-home-backend.service
systemctl daemon-reload
systemctl enable --now smart-home-backend
systemctl restart smart-home-backend

a2enmod proxy proxy_http headers
install -m 0644 deploy/apache-smart-home-api.conf /etc/apache2/sites-available/smart-home-api.conf
a2ensite smart-home-api.conf
systemctl reload apache2

echo
echo "Backend deployment finished."
echo "Check service:"
echo "  systemctl status smart-home-backend"
echo "Health check:"
echo "  curl ${BASE_URL}/api/health"

