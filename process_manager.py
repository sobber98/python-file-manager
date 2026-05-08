from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import threading
import time

import dependency_manager
import logger_manager
import models


_PROCESS_LOCK = threading.Lock()
_RUNNING_PROCESSES: dict[int, subprocess.Popen[str]] = {}
_STOP_REQUESTS: set[int] = set()
_PROCESS_START_TIMES: dict[int, float] = {}
_AUTO_RESTART_ATTEMPTS: dict[int, int] = {}
_AUTO_RESTARTING: set[int] = set()
_MONITOR_THREAD: threading.Thread | None = None

_SYSTEM_USERNAME = "system"
_SYSTEM_IP_ADDRESS = "127.0.0.1"
AUTO_RESTART_DELAY_SECONDS = max(0.0, float(os.environ.get("AUTO_RESTART_DELAY_SECONDS", "2")))
AUTO_RESTART_MAX_ATTEMPTS = int(os.environ.get("AUTO_RESTART_MAX_ATTEMPTS", "3"))
AUTO_RESTART_RESET_AFTER_SECONDS = max(0.0, float(os.environ.get("AUTO_RESTART_RESET_AFTER_SECONDS", "60")))
PROCESS_MONITOR_INTERVAL_SECONDS = max(1.0, float(os.environ.get("PROCESS_MONITOR_INTERVAL_SECONDS", "5")))


def _read_cmdline(pid: int) -> list[str]:
    cmdline_path = Path("/proc") / str(pid) / "cmdline"
    if not cmdline_path.exists():
        return []
    raw = cmdline_path.read_bytes().split(b"\x00")
    return [entry.decode("utf-8", errors="ignore") for entry in raw if entry]


def process_exists(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def pid_matches_script(pid: int | None, script_path: str) -> bool:
    if not pid or not process_exists(pid):
        return False

    target = str(Path(script_path).resolve())
    return target in _read_cmdline(pid)


def _signal_process_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return
    except OSError:
        os.kill(pid, sig)


def _wait_for_exit(pid: int, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not process_exists(pid):
            return True
        time.sleep(0.1)
    return not process_exists(pid)


def _stream_process_output(script_id: int, process: subprocess.Popen[str]) -> None:
    if process.stdout is None:
        return
    try:
        for line in iter(process.stdout.readline, ""):
            if not line:
                break
            logger_manager.write_log(script_id, line)
    finally:
        process.stdout.close()


def _claim_auto_restart_locked(script_id: int, runtime_seconds: float | None = None) -> int | None:
    if script_id in _AUTO_RESTARTING or script_id in _STOP_REQUESTS:
        return None

    if runtime_seconds is not None and runtime_seconds >= AUTO_RESTART_RESET_AFTER_SECONDS:
        _AUTO_RESTART_ATTEMPTS.pop(script_id, None)

    next_attempt = _AUTO_RESTART_ATTEMPTS.get(script_id, 0) + 1
    if AUTO_RESTART_MAX_ATTEMPTS > 0 and next_attempt > AUTO_RESTART_MAX_ATTEMPTS:
        return None

    _AUTO_RESTART_ATTEMPTS[script_id] = next_attempt
    _AUTO_RESTARTING.add(script_id)
    return next_attempt


def _start_script_process(
    script: dict,
    username: str,
    ip_address: str,
    *,
    reset_restart_attempts: bool,
) -> tuple[dict, subprocess.Popen[str]]:
    if not Path(script["file_path"]).exists():
        raise FileNotFoundError("脚本文件不存在")

    if script.get("pid") and pid_matches_script(script["pid"], script["file_path"]):
        raise RuntimeError("脚本已在运行")

    environment = models.get_environment_by_id(script["environment_id"] or 0)
    if environment is None:
        environment = dependency_manager.get_or_create_default_environment()
        script = models.update_script_environment(script["id"], environment["id"])

    command = [environment["python_path"], "-u", script["file_path"]]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    process = subprocess.Popen(
        command,
        cwd=str(Path(script["file_path"]).parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        start_new_session=True,
        env=env,
    )

    with _PROCESS_LOCK:
        _RUNNING_PROCESSES[script["id"]] = process
        _PROCESS_START_TIMES[script["id"]] = time.monotonic()
        _STOP_REQUESTS.discard(script["id"])
        _AUTO_RESTARTING.discard(script["id"])
        if reset_restart_attempts:
            _AUTO_RESTART_ATTEMPTS.pop(script["id"], None)

    updated_script = models.update_script_status(script["id"], "running", process.pid) or models.get_script_by_id(script["id"]) or script
    threading.Thread(target=_stream_process_output, args=(updated_script["id"], process), daemon=True).start()
    threading.Thread(target=_monitor_process, args=(updated_script, process, username, ip_address), daemon=True).start()
    return updated_script, process


def _auto_restart_script(script_id: int, attempt: int, reason: str) -> bool:
    try:
        logger_manager.write_log(
            script_id,
            f"[system] 检测到异常退出（{reason}），{AUTO_RESTART_DELAY_SECONDS:g} 秒后自动重启，第 {attempt} 次",
        )

        if AUTO_RESTART_DELAY_SECONDS > 0:
            time.sleep(AUTO_RESTART_DELAY_SECONDS)

        with _PROCESS_LOCK:
            if script_id not in _AUTO_RESTARTING or script_id in _STOP_REQUESTS:
                return False

        script = models.get_script_by_id(script_id)
        if script is None:
            return False

        restarted_script, process = _start_script_process(
            script,
            _SYSTEM_USERNAME,
            _SYSTEM_IP_ADDRESS,
            reset_restart_attempts=False,
        )
        logger_manager.write_log(script_id, f"[system] 脚本已自动重启，PID={process.pid}，第 {attempt} 次")
        models.log_operation(
            _SYSTEM_USERNAME,
            "auto_restart",
            f"脚本 {restarted_script['name']} 异常退出后自动重启，第 {attempt} 次，PID={process.pid}",
            _SYSTEM_IP_ADDRESS,
        )
        return True
    except (FileNotFoundError, RuntimeError) as error:
        script = models.get_script_by_id(script_id)
        script_name = script["name"] if script else str(script_id)
        logger_manager.write_log(script_id, f"[system] 自动重启失败：{error}")
        models.log_operation(
            _SYSTEM_USERNAME,
            "auto_restart_failed",
            f"脚本 {script_name} 自动重启失败：{error}",
            _SYSTEM_IP_ADDRESS,
        )
        return False
    finally:
        with _PROCESS_LOCK:
            _AUTO_RESTARTING.discard(script_id)


def _monitor_running_scripts_once() -> None:
    for script in models.list_scripts_with_pid():
        script_id = script["id"]
        pid = script.get("pid")
        if not pid or pid_matches_script(pid, script["file_path"]):
            continue

        with _PROCESS_LOCK:
            if script_id in _RUNNING_PROCESSES or script_id in _AUTO_RESTARTING or script_id in _STOP_REQUESTS:
                continue
            attempt = _claim_auto_restart_locked(script_id)

        models.update_script_status(script_id, "error", None)
        logger_manager.write_log(script_id, f"[system] 监测到进程不存在或已异常退出，原 PID={pid}")
        models.log_operation(
            _SYSTEM_USERNAME,
            "process_missing",
            f"脚本 {script['name']} 的进程 {pid} 不存在或已异常退出",
            _SYSTEM_IP_ADDRESS,
        )

        if attempt is None:
            logger_manager.write_log(script_id, "[system] 已达到自动重启上限，不再自动重启")
            continue

        if not _auto_restart_script(script_id, attempt, f"监测到进程 {pid} 不存在"):
            models.update_script_status(script_id, "error", None)


def _background_monitor_loop() -> None:
    while True:
        try:
            _monitor_running_scripts_once()
        except Exception:
            pass
        time.sleep(PROCESS_MONITOR_INTERVAL_SECONDS)


def start_process_monitoring() -> None:
    global _MONITOR_THREAD

    with _PROCESS_LOCK:
        if _MONITOR_THREAD is not None and _MONITOR_THREAD.is_alive():
            return
        _MONITOR_THREAD = threading.Thread(target=_background_monitor_loop, daemon=True, name="pyrunner-process-monitor")
        _MONITOR_THREAD.start()


def _monitor_process(script: dict, process: subprocess.Popen[str], username: str, ip_address: str) -> None:
    script_id = script["id"]
    return_code = process.wait()
    auto_restart_attempt = None

    with _PROCESS_LOCK:
        current = _RUNNING_PROCESSES.get(script_id)
        if current is not process:
            return

        requested_stop = script_id in _STOP_REQUESTS
        _STOP_REQUESTS.discard(script_id)
        _RUNNING_PROCESSES.pop(script_id, None)
        started_at = _PROCESS_START_TIMES.pop(script_id, None)
        runtime_seconds = time.monotonic() - started_at if started_at is not None else None

        if requested_stop or return_code == 0:
            _AUTO_RESTART_ATTEMPTS.pop(script_id, None)
            _AUTO_RESTARTING.discard(script_id)
        else:
            auto_restart_attempt = _claim_auto_restart_locked(script_id, runtime_seconds)

    latest_script = models.get_script_by_id(script_id) or script
    status = "stopped" if requested_stop or return_code == 0 else "error"
    models.update_script_status(script_id, status, None)
    logger_manager.write_log(script_id, f"[system] 进程退出，返回码 {return_code}")
    models.log_operation(username, "process_exit", f"脚本 {latest_script['name']} 退出，返回码 {return_code}", ip_address)

    if auto_restart_attempt is not None:
        if _auto_restart_script(script_id, auto_restart_attempt, f"返回码 {return_code}"):
            return
        models.update_script_status(script_id, "error", None)
        return

    if not requested_stop and return_code != 0:
        logger_manager.write_log(script_id, "[system] 已达到自动重启上限，不再自动重启")


def reconcile_scripts() -> None:
    for script in models.list_scripts_with_pid():
        pid = script.get("pid")
        if pid and pid_matches_script(pid, script["file_path"]):
            if script["status"] != "running":
                models.update_script_status(script["id"], "running", pid)
            continue

        if script["status"] != "stopped" or script.get("pid") is not None:
            models.update_script_status(script["id"], "stopped", None)


def start_script(script: dict, username: str, ip_address: str) -> dict:
    started_script, process = _start_script_process(script, username, ip_address, reset_restart_attempts=True)
    logger_manager.write_log(started_script["id"], f"[system] 脚本已启动，PID={process.pid}")
    models.log_operation(username, "start_script", f"启动脚本 {started_script['name']}，PID={process.pid}", ip_address)
    return started_script


def stop_script(script: dict, username: str, ip_address: str, force: bool = False) -> dict:
    pid = script.get("pid")
    if not pid or not pid_matches_script(pid, script["file_path"]):
        with _PROCESS_LOCK:
            _RUNNING_PROCESSES.pop(script["id"], None)
            _PROCESS_START_TIMES.pop(script["id"], None)
            _STOP_REQUESTS.discard(script["id"])
            _AUTO_RESTARTING.discard(script["id"])
            _AUTO_RESTART_ATTEMPTS.pop(script["id"], None)
        models.update_script_status(script["id"], "stopped", None)
        return models.get_script_by_id(script["id"])

    with _PROCESS_LOCK:
        _STOP_REQUESTS.add(script["id"])
        process = _RUNNING_PROCESSES.get(script["id"])

    signal_type = signal.SIGKILL if force else signal.SIGTERM
    _signal_process_group(pid, signal_type)

    if process is not None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _signal_process_group(pid, signal.SIGKILL)
            process.wait(timeout=5)
    else:
        exited = _wait_for_exit(pid, timeout_seconds=5)
        if not exited and not force:
            _signal_process_group(pid, signal.SIGKILL)
            exited = _wait_for_exit(pid, timeout_seconds=5)
        if not exited:
            raise RuntimeError("进程仍在运行，停止失败")

    models.update_script_status(script["id"], "stopped", None)

    with _PROCESS_LOCK:
        _PROCESS_START_TIMES.pop(script["id"], None)
        _AUTO_RESTARTING.discard(script["id"])
        _AUTO_RESTART_ATTEMPTS.pop(script["id"], None)

    models.log_operation(username, "stop_script", f"停止脚本 {script['name']}，PID={pid}", ip_address)
    return models.get_script_by_id(script["id"])


def restart_script(script: dict, username: str, ip_address: str) -> dict:
    if script.get("pid") and pid_matches_script(script["pid"], script["file_path"]):
        stop_script(script, username, ip_address)
        script = models.get_script_by_id(script["id"])
    return start_script(script, username, ip_address)