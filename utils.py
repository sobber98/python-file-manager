from __future__ import annotations

import sys
from pathlib import Path


def get_client_ip(request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if forwarded_for:
        return forwarded_for
    return request.remote_addr or "unknown"


def allowed_script_file(filename: str) -> bool:
    return filename.lower().endswith(".py") and "/" not in filename and "\\" not in filename


def top_level_module(name: str | None) -> str | None:
    if not name:
        return None
    return name.split(".", 1)[0]


def detect_stdlib_modules() -> set[str]:
    if hasattr(sys, "stdlib_module_names"):
        return set(sys.stdlib_module_names)

    fallback = {
        "abc",
        "argparse",
        "asyncio",
        "collections",
        "concurrent",
        "contextlib",
        "csv",
        "datetime",
        "email",
        "functools",
        "hashlib",
        "http",
        "importlib",
        "io",
        "itertools",
        "json",
        "logging",
        "math",
        "multiprocessing",
        "os",
        "pathlib",
        "queue",
        "random",
        "re",
        "shutil",
        "signal",
        "socket",
        "sqlite3",
        "statistics",
        "string",
        "subprocess",
        "sys",
        "tempfile",
        "threading",
        "time",
        "typing",
        "unittest",
        "urllib",
        "uuid",
        "venv",
        "xml",
    }
    return fallback


STDLIB_MODULES = detect_stdlib_modules()


def normalize_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()