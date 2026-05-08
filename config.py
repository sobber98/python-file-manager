from __future__ import annotations

from datetime import timedelta, timezone
import os
import secrets
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


class Config:
    BASE_DIR = BASE_DIR
    DB_PATH = Path(os.environ.get("PYRUNNER_DB_PATH") or (BASE_DIR / "app.db"))
    UPLOAD_DIR = BASE_DIR / "uploads"
    LOG_DIR = BASE_DIR / "logs"
    VENV_DIR = BASE_DIR / "venvs"
    STATIC_DIR = BASE_DIR / "static"
    TEMPLATE_DIR = BASE_DIR / "templates"

    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_SIZE", 10 * 1024 * 1024))
    LOG_MAX_BYTES = int(os.environ.get("LOG_MAX_BYTES", 5 * 1024 * 1024))
    LOG_BACKUP_COUNT = int(os.environ.get("LOG_BACKUP_COUNT", 3))
    APP_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")

    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123456")
    DEFAULT_HOST = os.environ.get("PYRUNNER_HOST", "127.0.0.1")
    DEFAULT_PORT = int(os.environ.get("PYRUNNER_PORT", 5000))


def ensure_runtime_dirs() -> None:
    for path in (
        Config.DB_PATH.parent,
        Config.UPLOAD_DIR,
        Config.LOG_DIR,
        Config.VENV_DIR,
        Config.STATIC_DIR,
        Config.TEMPLATE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)