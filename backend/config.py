from pathlib import Path
import os


APP_VERSION = "0.3"
DEFAULT_DEVICE_ID = "orange-pi-main"
DEFAULT_DEVICE_NAME = "Orange Pi Smart Home Gateway"
DEFAULT_DEVICE_TYPE = "gateway"

BASE_URL = os.getenv("SMART_HOME_BASE_URL", "http://82.156.238.244").rstrip("/")

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("SMART_HOME_DATA_DIR", ROOT_DIR / "data"))
UPLOAD_DIR = Path(os.getenv("SMART_HOME_UPLOAD_DIR", ROOT_DIR / "uploads"))
DATABASE_PATH = Path(os.getenv("SMART_HOME_DATABASE", DATA_DIR / "smart_home.sqlite3"))

