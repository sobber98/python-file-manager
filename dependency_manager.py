from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess
import sys
import threading

from config import Config
import models
from utils import STDLIB_MODULES, top_level_module


PACKAGE_NAME_ALIASES = {
    "PIL": "Pillow",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "Crypto": "pycryptodome",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
}

_ACTIVE_INSTALLS: set[int] = set()
_INSTALL_LOCK = threading.Lock()
_INSTALL_PACKAGES: dict[int, list[str]] = {}
INSTALL_OUTPUT_PREVIEW_LINES = 6
INSTALL_OUTPUT_PREVIEW_CHARS = 800
INSTALL_OUTPUT_FULL_CHARS = 12000
INSTALL_OUTPUT_SUMMARY_CHARS = 180


def is_local_module(script_dir: Path, module_name: str) -> bool:
    return (script_dir / f"{module_name}.py").exists() or (script_dir / module_name / "__init__.py").exists()


def normalize_package_name(module_name: str) -> str:
    return PACKAGE_NAME_ALIASES.get(module_name, module_name)


def parse_manual_packages(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {item.strip() for item in re.split(r"[\s,]+", raw) if item.strip()}


def extract_imports(script_path: str | Path) -> list[str]:
    path = Path(script_path)
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    packages: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = top_level_module(alias.name)
                if not module_name:
                    continue
                if module_name in STDLIB_MODULES or is_local_module(path.parent, module_name):
                    continue
                packages.add(normalize_package_name(module_name))

        if isinstance(node, ast.ImportFrom):
            module_name = top_level_module(node.module)
            if not module_name:
                continue
            if module_name in STDLIB_MODULES or is_local_module(path.parent, module_name):
                continue
            packages.add(normalize_package_name(module_name))

    return sorted(packages)


def _python_path_for_venv(venv_path: Path) -> Path:
    return venv_path / "bin" / "python"


def _create_venv(venv_path: Path) -> Path:
    python_path = _python_path_for_venv(venv_path)
    if python_path.exists():
        return python_path

    try:
        subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        stderr = getattr(error, "stderr", "") or getattr(error, "stdout", "") or str(error)
        raise RuntimeError(stderr.strip() or "创建虚拟环境失败") from error

    return python_path


def ensure_environment(name: str, is_default: bool = False, allow_system_fallback: bool = False) -> dict:
    venv_path = Config.VENV_DIR / name
    existing = models.get_environment_by_name(name)

    try:
        python_path = _create_venv(venv_path)
        python_value = str(python_path)
    except RuntimeError:
        if not allow_system_fallback:
            raise
        python_value = sys.executable

    if existing:
        return models.update_environment_paths(existing["id"], str(venv_path), python_value, is_default)

    return models.create_environment(name, str(venv_path), python_value, is_default)


def get_or_create_default_environment() -> dict:
    default = models.get_default_environment()
    if default and Path(default["python_path"]).exists():
        return default
    return ensure_environment("default", is_default=True, allow_system_fallback=True)


def create_named_environment(name: str) -> dict:
    normalized = name.strip().lower().replace(" ", "-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,31}", normalized):
        raise ValueError("环境名称仅支持 2-32 位字母、数字、下划线和连字符")
    if models.get_environment_by_name(normalized):
        raise ValueError("环境名称已存在")
    try:
        return ensure_environment(normalized, is_default=False)
    except RuntimeError as error:
        raise ValueError(f"创建环境失败: {error}") from error


def is_install_active(script_id: int) -> bool:
    with _INSTALL_LOCK:
        return script_id in _ACTIVE_INSTALLS


def _truncate_output(text: str, limit: int, suffix: str) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + suffix, True


def _summarize_install_output(success: bool, version: str, lines: list[str]) -> str:
    compact_lines = [line.strip() for line in lines if line.strip()]
    if success:
        success_line = next(
            (
                line
                for line in compact_lines
                if line.lower().startswith("successfully installed") or line.lower().startswith("requirement already satisfied:")
            ),
            "",
        )
        if success_line:
            summary, _ = _truncate_output(success_line, INSTALL_OUTPUT_SUMMARY_CHARS, "...")
            return summary
        if version:
            return f"安装成功，版本 {version}"
        return "安装成功"

    error_line = next((line for line in compact_lines if "error" in line.lower()), "")
    if error_line:
        summary, _ = _truncate_output(error_line, INSTALL_OUTPUT_SUMMARY_CHARS, "...")
        return summary

    first_line = compact_lines[0] if compact_lines else "安装失败"
    summary, _ = _truncate_output(first_line, INSTALL_OUTPUT_SUMMARY_CHARS, "...")
    return summary


def format_install_log(record: dict) -> dict:
    output = str(record.get("error_msg") or "").strip()
    raw_lines = output.splitlines() if output else []
    visible_lines = [line.rstrip() for line in raw_lines if line.strip()]
    preview_source = "\n".join(visible_lines[:INSTALL_OUTPUT_PREVIEW_LINES])
    preview, preview_trimmed = _truncate_output(preview_source, INSTALL_OUTPUT_PREVIEW_CHARS, "\n...[预览已截断]")
    full_output, full_trimmed = _truncate_output(output, INSTALL_OUTPUT_FULL_CHARS, "\n...[完整输出已截断]")
    output_line_count = len(raw_lines)
    output_collapsed = bool(output and (output_line_count > INSTALL_OUTPUT_PREVIEW_LINES or preview_trimmed or full_trimmed))

    enriched = dict(record)
    enriched.update(
        {
            "output_summary": _summarize_install_output(bool(record.get("success")), str(record.get("version") or ""), raw_lines),
            "output_preview": preview,
            "output_full": full_output,
            "output_collapsed": output_collapsed,
            "output_trimmed": full_trimmed,
            "output_line_count": output_line_count,
        }
    )
    return enriched


def format_install_logs(records: list[dict]) -> list[dict]:
    return [format_install_log(record) for record in records]


def get_install_progress(script_id: int) -> dict[str, int | bool]:
    stats = models.dependency_install_stats(script_id)
    completed = stats["completed"]
    successful = stats["successful"]
    failed = stats["failed"]

    with _INSTALL_LOCK:
        packages = list(_INSTALL_PACKAGES.get(script_id, []))
        installing = script_id in _ACTIVE_INSTALLS

    total = max(len(packages), completed)
    return {
        "installing": installing,
        "total_packages": total,
        "completed_packages": completed,
        "successful_packages": successful,
        "failed_packages": failed,
    }


def _package_version(python_path: str, package_name: str) -> str:
    command = [python_path, "-m", "pip", "show", package_name]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        if line.lower().startswith("version:"):
            return line.split(":", 1)[1].strip()
    return ""


def _install_worker(script_id: int, packages: list[str], python_path: str, username: str, ip_address: str) -> None:
    success_count = 0
    failure_count = 0

    try:
        models.clear_dependency_install_logs(script_id)
        for package in packages:
            command = [python_path, "-m", "pip", "install", package]
            result = subprocess.run(command, capture_output=True, text=True)
            success = result.returncode == 0
            version = _package_version(python_path, package) if success else ""
            error_output = (result.stderr or result.stdout).strip()
            models.insert_dependency_install_log(script_id, package, version, success, error_output)
            if success:
                success_count += 1
            else:
                failure_count += 1

        summary = f"脚本 {script_id} 安装依赖完成，成功 {success_count} 个，失败 {failure_count} 个"
        models.log_operation(username, "install_dependencies", summary, ip_address)
    finally:
        with _INSTALL_LOCK:
            _ACTIVE_INSTALLS.discard(script_id)
            _INSTALL_PACKAGES.pop(script_id, None)


def schedule_dependency_install(
    script: dict,
    manual_packages: str | None,
    username: str,
    ip_address: str,
) -> list[str]:
    script_id = script["id"]

    with _INSTALL_LOCK:
        if script_id in _ACTIVE_INSTALLS:
            raise RuntimeError("该脚本的依赖安装任务正在执行")
        _ACTIVE_INSTALLS.add(script_id)

    try:
        environment = models.get_environment_by_id(script["environment_id"] or 0)
        if environment is None:
            environment = get_or_create_default_environment()
            models.update_script_environment(script_id, environment["id"])

        packages = set(extract_imports(script["file_path"]))
        packages.update(normalize_package_name(item) for item in parse_manual_packages(manual_packages))
        sorted_packages = sorted(packages)

        if not sorted_packages:
            models.clear_dependency_install_logs(script_id)
            models.log_operation(username, "install_dependencies", f"脚本 {script['name']} 未检测到第三方依赖", ip_address)
            with _INSTALL_LOCK:
                _ACTIVE_INSTALLS.discard(script_id)
                _INSTALL_PACKAGES.pop(script_id, None)
            return []

        with _INSTALL_LOCK:
            _INSTALL_PACKAGES[script_id] = sorted_packages

        thread = threading.Thread(
            target=_install_worker,
            args=(script_id, sorted_packages, environment["python_path"], username, ip_address),
            daemon=True,
        )
        thread.start()
        return sorted_packages
    except Exception:
        with _INSTALL_LOCK:
            _ACTIVE_INSTALLS.discard(script_id)
            _INSTALL_PACKAGES.pop(script_id, None)
        raise
