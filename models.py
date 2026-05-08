from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from config import Config


def now_string() -> str:
    return datetime.now(Config.APP_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None

    data = dict(row)
    for key in ("success", "is_default"):
        if key in data and data[key] is not None:
            data[key] = bool(data[key])
    return data


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(Config.DB_PATH, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def get_db(commit: bool = False) -> Iterator[sqlite3.Connection]:
    connection = connect_db()
    try:
        yield connection
        if commit:
            connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with get_db(commit=True) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS user (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS environment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                venv_path TEXT NOT NULL,
                python_path TEXT NOT NULL,
                is_default INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS script (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                environment_id INTEGER REFERENCES environment(id),
                status TEXT DEFAULT 'stopped',
                pid INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS operation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT,
                ip_address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS dependency_install_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                script_id INTEGER REFERENCES script(id),
                package_name TEXT,
                version TEXT,
                success INTEGER,
                error_msg TEXT,
                installed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_script_status ON script(status);
            CREATE INDEX IF NOT EXISTS idx_operation_created_at ON operation_log(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_dependency_script ON dependency_install_log(script_id, installed_at DESC);
            """
        )


def create_default_admin(username: str, password: str) -> dict[str, Any]:
    existing = get_user_by_username(username)
    if existing:
        return existing

    from werkzeug.security import generate_password_hash

    password_hash = generate_password_hash(password)
    with get_db(commit=True) as connection:
        cursor = connection.execute(
            "INSERT INTO user (username, password_hash) VALUES (?, ?)",
            (username, password_hash),
        )
        user_id = cursor.lastrowid
    return get_user_by_id(user_id)


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with get_db() as connection:
        row = connection.execute("SELECT * FROM user WHERE id = ?", (user_id,)).fetchone()
    return row_to_dict(row)


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with get_db() as connection:
        row = connection.execute("SELECT * FROM user WHERE username = ?", (username,)).fetchone()
    return row_to_dict(row)


def list_environments() -> list[dict[str, Any]]:
    with get_db() as connection:
        rows = connection.execute(
            "SELECT * FROM environment ORDER BY is_default DESC, name ASC"
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def get_environment_by_id(environment_id: int) -> dict[str, Any] | None:
    with get_db() as connection:
        row = connection.execute("SELECT * FROM environment WHERE id = ?", (environment_id,)).fetchone()
    return row_to_dict(row)


def get_environment_by_name(name: str) -> dict[str, Any] | None:
    with get_db() as connection:
        row = connection.execute("SELECT * FROM environment WHERE name = ?", (name,)).fetchone()
    return row_to_dict(row)


def get_default_environment() -> dict[str, Any] | None:
    with get_db() as connection:
        row = connection.execute(
            "SELECT * FROM environment WHERE is_default = 1 ORDER BY id ASC LIMIT 1"
        ).fetchone()
    return row_to_dict(row)


def create_environment(name: str, venv_path: str, python_path: str, is_default: bool = False) -> dict[str, Any]:
    if is_default:
        clear_default_environment_flag()

    with get_db(commit=True) as connection:
        cursor = connection.execute(
            "INSERT INTO environment (name, venv_path, python_path, is_default) VALUES (?, ?, ?, ?)",
            (name, venv_path, python_path, int(is_default)),
        )
        environment_id = cursor.lastrowid
    return get_environment_by_id(environment_id)


def update_environment_paths(environment_id: int, venv_path: str, python_path: str, is_default: bool) -> dict[str, Any]:
    if is_default:
        clear_default_environment_flag()

    with get_db(commit=True) as connection:
        connection.execute(
            """
            UPDATE environment
            SET venv_path = ?, python_path = ?, is_default = ?
            WHERE id = ?
            """,
            (venv_path, python_path, int(is_default), environment_id),
        )
    return get_environment_by_id(environment_id)


def clear_default_environment_flag() -> None:
    with get_db(commit=True) as connection:
        connection.execute("UPDATE environment SET is_default = 0 WHERE is_default = 1")


def ensure_scripts_have_environment(environment_id: int) -> None:
    with get_db(commit=True) as connection:
        connection.execute(
            """
            UPDATE script
            SET environment_id = ?, updated_at = ?
            WHERE environment_id IS NULL
            """,
            (environment_id, now_string()),
        )


def create_script(name: str, file_path: str, environment_id: int | None) -> dict[str, Any]:
    now = now_string()
    with get_db(commit=True) as connection:
        cursor = connection.execute(
            """
            INSERT INTO script (name, file_path, environment_id, status, pid, created_at, updated_at)
            VALUES (?, ?, ?, 'stopped', NULL, ?, ?)
            """,
            (name, file_path, environment_id, now, now),
        )
        script_id = cursor.lastrowid
    return get_script_by_id(script_id)


def get_script_by_id(script_id: int) -> dict[str, Any] | None:
    with get_db() as connection:
        row = connection.execute(
            """
            SELECT script.*, environment.name AS environment_name
            FROM script
            LEFT JOIN environment ON environment.id = script.environment_id
            WHERE script.id = ?
            """,
            (script_id,),
        ).fetchone()
    return row_to_dict(row)


def list_scripts() -> list[dict[str, Any]]:
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT script.*, environment.name AS environment_name
            FROM script
            LEFT JOIN environment ON environment.id = script.environment_id
            ORDER BY script.created_at DESC, script.id DESC
            """
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def list_scripts_with_pid() -> list[dict[str, Any]]:
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT script.*, environment.name AS environment_name
            FROM script
            LEFT JOIN environment ON environment.id = script.environment_id
            WHERE script.pid IS NOT NULL OR script.status = 'running'
            ORDER BY script.id ASC
            """
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def update_script_status(script_id: int, status: str, pid: int | None) -> dict[str, Any] | None:
    with get_db(commit=True) as connection:
        connection.execute(
            """
            UPDATE script
            SET status = ?, pid = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, pid, now_string(), script_id),
        )
    return get_script_by_id(script_id)


def update_script_environment(script_id: int, environment_id: int) -> dict[str, Any] | None:
    with get_db(commit=True) as connection:
        connection.execute(
            """
            UPDATE script
            SET environment_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (environment_id, now_string(), script_id),
        )
    return get_script_by_id(script_id)


def delete_script(script_id: int) -> None:
    with get_db(commit=True) as connection:
        connection.execute("DELETE FROM dependency_install_log WHERE script_id = ?", (script_id,))
        connection.execute("DELETE FROM script WHERE id = ?", (script_id,))


def clear_dependency_install_logs(script_id: int) -> None:
    with get_db(commit=True) as connection:
        connection.execute("DELETE FROM dependency_install_log WHERE script_id = ?", (script_id,))


def insert_dependency_install_log(
    script_id: int,
    package_name: str,
    version: str,
    success: bool,
    error_msg: str,
) -> None:
    with get_db(commit=True) as connection:
        connection.execute(
            """
            INSERT INTO dependency_install_log (script_id, package_name, version, success, error_msg, installed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (script_id, package_name, version, int(success), error_msg, now_string()),
        )


def list_dependency_install_logs(script_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM dependency_install_log
            WHERE script_id = ?
            ORDER BY installed_at DESC, id DESC
            LIMIT ?
            """,
            (script_id, limit),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def dependency_install_stats(script_id: int) -> dict[str, int]:
    with get_db() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS completed,
                COALESCE(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END), 0) AS successful,
                COALESCE(SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END), 0) AS failed
            FROM dependency_install_log
            WHERE script_id = ?
            """,
            (script_id,),
        ).fetchone()
    return {
        "completed": int(row["completed"] if row else 0),
        "successful": int(row["successful"] if row else 0),
        "failed": int(row["failed"] if row else 0),
    }


def log_operation(username: str, action: str, details: str, ip_address: str) -> None:
    with get_db(commit=True) as connection:
        connection.execute(
            """
            INSERT INTO operation_log (username, action, details, ip_address, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (username, action, details, ip_address, now_string()),
        )


def count_operation_logs() -> int:
    with get_db() as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM operation_log").fetchone()
    return int(row["count"] if row else 0)


def list_operation_logs(limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM operation_log
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, max(0, offset)),
        ).fetchall()
    return [row_to_dict(row) for row in rows]