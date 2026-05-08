from __future__ import annotations

from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading

from config import Config


_LOGGER_LOCK = threading.Lock()
_SCRIPT_LOGGERS: dict[int, logging.Logger] = {}


class ChinaTimeFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        current_time = datetime.fromtimestamp(record.created, Config.APP_TIMEZONE)
        if datefmt:
            return current_time.strftime(datefmt)
        return current_time.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]


def get_log_path(script_id: int) -> Path:
    return Config.LOG_DIR / f"script_{script_id}.log"


def get_script_logger(script_id: int) -> logging.Logger:
    with _LOGGER_LOCK:
        logger = _SCRIPT_LOGGERS.get(script_id)
        if logger is not None:
            return logger

        logger = logging.getLogger(f"pyrunner.script.{script_id}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.handlers.clear()

        handler = RotatingFileHandler(
            get_log_path(script_id),
            maxBytes=Config.LOG_MAX_BYTES,
            backupCount=Config.LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        formatter = ChinaTimeFormatter("%(asctime)s %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        _SCRIPT_LOGGERS[script_id] = logger
        return logger


def write_log(script_id: int, message: str, level: int = logging.INFO) -> None:
    text = str(message).rstrip()
    if not text:
        return

    logger = get_script_logger(script_id)
    for line in text.splitlines():
        logger.log(level, line)


def close_logger(script_id: int) -> None:
    with _LOGGER_LOCK:
        logger = _SCRIPT_LOGGERS.pop(script_id, None)
        if logger is None:
            return

        handlers = list(logger.handlers)
        for handler in handlers:
            handler.close()
            logger.removeHandler(handler)


def delete_logs(script_id: int) -> None:
    close_logger(script_id)

    base_path = get_log_path(script_id)
    candidates = [base_path]
    candidates.extend(base_path.with_name(f"{base_path.name}.{index}") for index in range(1, Config.LOG_BACKUP_COUNT + 1))

    for path in candidates:
        if path.exists():
            path.unlink()


def read_tail(script_id: int, lines: int = 500) -> list[str]:
    log_path = get_log_path(script_id)
    if not log_path.exists():
        return []

    limit = max(1, min(int(lines), 2000))
    chunk_size = 4096
    buffer = bytearray()

    with log_path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        newline_budget = limit + 1

        while position > 0 and newline_budget > 0:
            read_size = min(chunk_size, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            buffer[:0] = chunk
            newline_budget -= chunk.count(b"\n")

    return buffer.decode("utf-8", errors="replace").splitlines()[-limit:]


def read_log_update(script_id: int, after: int = 0, tail: int = 500) -> dict[str, object]:
    log_path = get_log_path(script_id)
    if not log_path.exists():
        return {"lines": [], "cursor": 0, "truncated": False}

    text = log_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    cursor = len(lines)
    safe_after = max(0, int(after))

    if safe_after == 0:
        return {"lines": lines[-max(1, min(int(tail), 2000)):], "cursor": cursor, "truncated": False}

    if safe_after > cursor:
        fallback_tail = max(1, min(int(tail), 2000))
        return {"lines": lines[-fallback_tail:], "cursor": cursor, "truncated": True}

    return {"lines": lines[safe_after:], "cursor": cursor, "truncated": False}