from __future__ import annotations

import importlib
import io
import re
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from config import Config
import dependency_manager
import logger_manager
import models
import process_manager


def _extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if match:
        return match.group(1)

    match = re.search(r'meta name="csrf-token" content="([^"]+)"', html)
    if match:
        return match.group(1)

    raise AssertionError("未找到 CSRF token")


class ImmediateThread:
    def __init__(self, target, args=(), daemon=None):
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self):
        self.target(*self.args)


class ApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.original_config = {
            "DB_PATH": Config.DB_PATH,
            "UPLOAD_DIR": Config.UPLOAD_DIR,
            "LOG_DIR": Config.LOG_DIR,
            "VENV_DIR": Config.VENV_DIR,
        }
        Config.DB_PATH = Config.BASE_DIR / ".test-placeholder.db"
        Config.UPLOAD_DIR = Config.BASE_DIR / ".test-placeholder-uploads"
        Config.LOG_DIR = Config.BASE_DIR / ".test-placeholder-logs"
        Config.VENV_DIR = Config.BASE_DIR / ".test-placeholder-venvs"

        base_path = tempfile.mkdtemp(dir=cls.temp_dir.name)
        cls.runtime_root = base_path
        Config.DB_PATH = Config.DB_PATH.__class__(base_path) / "app.db"
        Config.UPLOAD_DIR = Config.UPLOAD_DIR.__class__(base_path) / "uploads"
        Config.LOG_DIR = Config.LOG_DIR.__class__(base_path) / "logs"
        Config.VENV_DIR = Config.VENV_DIR.__class__(base_path) / "venvs"

        cls.default_environment_patcher = patch(
            "dependency_manager.get_or_create_default_environment",
            side_effect=cls._fake_default_environment,
        )
        cls.default_environment_patcher.start()

        if "app" in sys.modules:
            del sys.modules["app"]
        cls.app_module = importlib.import_module("app")
        cls.app = cls.app_module.app
        cls.app.config.update(TESTING=True)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._cleanup_processes()
        cls.default_environment_patcher.stop()
        for key, value in cls.original_config.items():
            setattr(Config, key, value)
        shutil.rmtree(cls.runtime_root, ignore_errors=True)
        cls.temp_dir.cleanup()

    @classmethod
    def _fake_default_environment(cls):
        existing = models.get_default_environment()
        if existing:
            return existing
        return models.create_environment("default", str(Config.VENV_DIR / "default"), sys.executable, True)

    @classmethod
    def _cleanup_processes(cls):
        with process_manager._PROCESS_LOCK:
            processes = list(process_manager._RUNNING_PROCESSES.values())
            process_manager._RUNNING_PROCESSES.clear()
            process_manager._STOP_REQUESTS.clear()

        for process in processes:
            try:
                process.kill()
            except OSError:
                pass

        dependency_manager._ACTIVE_INSTALLS.clear()
        dependency_manager._INSTALL_PACKAGES.clear()
        process_manager._PROCESS_START_TIMES.clear()
        process_manager._AUTO_RESTART_ATTEMPTS.clear()
        process_manager._AUTO_RESTARTING.clear()
        for script_id in list(logger_manager._SCRIPT_LOGGERS):
            logger_manager.close_logger(script_id)

    def setUp(self):
        self._cleanup_processes()
        for path in (Config.UPLOAD_DIR, Config.LOG_DIR, Config.VENV_DIR):
            shutil.rmtree(path, ignore_errors=True)
            path.mkdir(parents=True, exist_ok=True)

        models.init_db()
        models.create_default_admin(Config.ADMIN_USERNAME, Config.ADMIN_PASSWORD)

        with models.get_db(commit=True) as connection:
            connection.execute("DELETE FROM dependency_install_log")
            connection.execute("DELETE FROM operation_log")
            connection.execute("DELETE FROM script")
            connection.execute("DELETE FROM environment")

        self._fake_default_environment()
        self.client = self.app.test_client()
        self.dashboard_csrf = self.login()

    def tearDown(self):
        self._cleanup_processes()

    def login(self) -> str:
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        token = _extract_csrf_token(response.get_data(as_text=True))

        response = self.client.post(
            "/login",
            data={
                "username": Config.ADMIN_USERNAME,
                "password": Config.ADMIN_PASSWORD,
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        return _extract_csrf_token(dashboard.get_data(as_text=True))

    def api_headers(self) -> dict[str, str]:
        return {"X-CSRFToken": self.dashboard_csrf}

    def upload_script(self, filename: str, content: str, auto_install: bool = False, expected_status: int = 201) -> dict:
        response = self.client.post(
            "/api/scripts/upload",
            data={
                "script": (io.BytesIO(content.encode("utf-8")), filename),
                "auto_install": "true" if auto_install else "false",
            },
            headers=self.api_headers(),
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, expected_status)
        return response.get_json()

    def wait_for(self, predicate, timeout: float = 3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(0.1)
        return None

    def fetch_logs_with_text(self, script_id: int, needle: str):
        lines = self.client.get(f"/api/logs/{script_id}?tail=20").get_json()["lines"]
        if any(needle in line for line in lines):
            return lines
        return None

    def test_login_and_protected_api(self):
        anonymous = self.app.test_client()
        response = anonymous.get("/api/scripts")
        self.assertEqual(response.status_code, 401)

        response = self.client.get("/api/scripts")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["scripts"], [])

    def test_upload_api_creates_script_and_operation_log(self):
        payload = self.upload_script("demo.py", "print('hello')\n")

        script = payload["script"]
        self.assertEqual(script["status"], "stopped")
        self.assertTrue((Config.UPLOAD_DIR / "demo.py").exists())

        logs_response = self.client.get("/api/operation-logs?limit=2&page=1")
        self.assertEqual(logs_response.status_code, 200)
        logs_payload = logs_response.get_json()
        self.assertEqual(logs_payload["pagination"]["page"], 1)
        self.assertGreaterEqual(logs_payload["pagination"]["total"], 2)
        self.assertEqual(logs_payload["logs"][0]["action"], "upload")

    def test_dashboard_hides_detail_modules_and_detail_page_shows_them(self):
        payload = self.upload_script("detail_demo.py", "print('hello detail')\n")
        script_id = payload["script"]["id"]

        dashboard_response = self.client.get("/")
        self.assertEqual(dashboard_response.status_code, 200)
        dashboard_html = dashboard_response.get_data(as_text=True)
        self.assertNotIn("依赖安装记录", dashboard_html)
        self.assertNotIn("实时日志", dashboard_html)

        detail_response = self.client.get(f"/scripts/{script_id}")
        self.assertEqual(detail_response.status_code, 200)
        detail_html = detail_response.get_data(as_text=True)
        self.assertIn("脚本详情", detail_html)
        self.assertIn("依赖安装记录", detail_html)
        self.assertIn("实时日志", detail_html)
        self.assertIn(f'data-script-id="{script_id}"', detail_html)

    def test_start_and_stop_api_manage_process_and_logs(self):
        payload = self.upload_script(
            "runner.py",
            "import time\nprint('ready', flush=True)\nwhile True:\n    time.sleep(0.1)\n",
        )
        script_id = payload["script"]["id"]

        response = self.client.post(f"/api/scripts/{script_id}/start", json={}, headers=self.api_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["script"]["status"], "running")

        logs = self.wait_for(
            lambda: self.fetch_logs_with_text(script_id, "ready"),
            timeout=3.0,
        )
        self.assertIsNotNone(logs)
        self.assertTrue(any("ready" in line for line in logs))

        response = self.client.post(f"/api/scripts/{script_id}/stop", json={}, headers=self.api_headers())
        self.assertEqual(response.status_code, 200)

        stopped = self.wait_for(
            lambda: next(
                (item for item in self.client.get("/api/scripts").get_json()["scripts"] if item["id"] == script_id and item["status"] == "stopped"),
                None,
            ),
            timeout=3.0,
        )
        self.assertIsNotNone(stopped)

    def test_upload_same_name_replaces_existing_script_and_restarts_it(self):
        payload = self.upload_script(
            "replace_runner.py",
            "import time\nprint('version-1', flush=True)\nwhile True:\n    time.sleep(0.1)\n",
        )
        script_id = payload["script"]["id"]

        start_response = self.client.post(f"/api/scripts/{script_id}/start", json={}, headers=self.api_headers())
        self.assertEqual(start_response.status_code, 200)
        old_pid = start_response.get_json()["script"]["pid"]

        initial_logs = self.wait_for(
            lambda: self.fetch_logs_with_text(script_id, "version-1"),
            timeout=3.0,
        )
        self.assertIsNotNone(initial_logs)

        replacement = self.upload_script(
            "replace_runner.py",
            "import time\nprint('version-2', flush=True)\nwhile True:\n    time.sleep(0.1)\n",
            expected_status=200,
        )

        self.assertEqual(replacement["script"]["id"], script_id)
        self.assertEqual(replacement["script"]["status"], "running")
        self.assertNotEqual(replacement["script"]["pid"], old_pid)
        self.assertEqual(replacement["message"], "同名脚本已替换并重启")
        self.assertEqual((Config.UPLOAD_DIR / "replace_runner.py").read_text(encoding="utf-8").splitlines()[1], "print('version-2', flush=True)")

        replacement_logs = self.wait_for(
            lambda: self.fetch_logs_with_text(script_id, "version-2"),
            timeout=3.0,
        )
        self.assertIsNotNone(replacement_logs)

        scripts = self.client.get("/api/scripts").get_json()["scripts"]
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]["id"], script_id)

    def test_install_dependencies_api_records_progress_and_logs(self):
        payload = self.upload_script("deps.py", "import requests\nprint('done')\n")
        script_id = payload["script"]["id"]

        def fake_install_worker(script_id: int, packages: list[str], python_path: str, username: str, ip_address: str) -> None:
            models.clear_dependency_install_logs(script_id)
            for package in packages:
                long_output = "\n".join(
                    [
                        "Collecting requests",
                        "Downloading requests-2.33.1.whl",
                        "Installing collected packages: requests",
                        "Successfully installed requests-2.33.1",
                        "detail 1",
                        "detail 2",
                        "detail 3",
                        "detail 4",
                    ]
                )
                models.insert_dependency_install_log(script_id, package, "2.33.1", True, long_output)
            models.log_operation(username, "install_dependencies", f"脚本 {script_id} 安装依赖完成，成功 {len(packages)} 个，失败 0 个", ip_address)
            with dependency_manager._INSTALL_LOCK:
                dependency_manager._ACTIVE_INSTALLS.discard(script_id)
                dependency_manager._INSTALL_PACKAGES.pop(script_id, None)

        with patch("dependency_manager._install_worker", side_effect=fake_install_worker), patch("dependency_manager.threading.Thread", ImmediateThread):
            response = self.client.post(f"/api/scripts/{script_id}/install-dependencies", json={}, headers=self.api_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["packages"], ["requests"])

        dependency_response = self.client.get(f"/api/scripts/{script_id}/dependencies")
        self.assertEqual(dependency_response.status_code, 200)
        dependency_payload = dependency_response.get_json()
        self.assertFalse(dependency_payload["installing"])
        self.assertEqual(dependency_payload["progress"]["total_packages"], 1)
        self.assertEqual(dependency_payload["progress"]["completed_packages"], 1)
        self.assertEqual(dependency_payload["logs"][0]["package_name"], "requests")
        self.assertTrue(dependency_payload["logs"][0]["success"])
        self.assertTrue(dependency_payload["logs"][0]["output_collapsed"])
        self.assertIn("Successfully installed requests-2.33.1", dependency_payload["logs"][0]["output_summary"])
        self.assertIn("Collecting requests", dependency_payload["logs"][0]["output_preview"])

    def test_abnormal_exit_triggers_auto_restart(self):
        payload = self.upload_script(
            "crash_restart.py",
            "import pathlib\nimport sys\nimport time\nmarker = pathlib.Path(__file__).with_suffix('.state')\ncount = int(marker.read_text()) if marker.exists() else 0\nmarker.write_text(str(count + 1))\nprint(f'run-{count + 1}', flush=True)\nif count == 0:\n    sys.exit(1)\nwhile True:\n    time.sleep(0.1)\n",
        )
        script_id = payload["script"]["id"]

        response = self.client.post(f"/api/scripts/{script_id}/start", json={}, headers=self.api_headers())
        self.assertEqual(response.status_code, 200)
        first_pid = response.get_json()["script"]["pid"]

        restarted = self.wait_for(
            lambda: next(
                (
                    item
                    for item in self.client.get("/api/scripts").get_json()["scripts"]
                    if item["id"] == script_id and item["status"] == "running" and item.get("pid") not in (None, first_pid)
                ),
                None,
            ),
            timeout=6.0,
        )
        self.assertIsNotNone(restarted)

        logs = self.wait_for(
            lambda: self.fetch_logs_with_text(script_id, "run-2"),
            timeout=6.0,
        )
        self.assertIsNotNone(logs)
        self.assertTrue(any("脚本已自动重启" in line for line in logs))

    def test_dependency_progress_counts_all_install_logs(self):
        payload = self.upload_script("many_deps.py", "print('done')\n")
        script_id = payload["script"]["id"]

        for index in range(505):
            models.insert_dependency_install_log(
                script_id,
                f"package-{index}",
                "1.0.0",
                index % 2 == 0,
                f"log line {index}",
            )

        response = self.client.get(f"/api/scripts/{script_id}/dependencies")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["progress"]["installing"])
        self.assertEqual(payload["progress"]["total_packages"], 505)
        self.assertEqual(payload["progress"]["completed_packages"], 505)
        self.assertEqual(payload["progress"]["successful_packages"], 253)
        self.assertEqual(payload["progress"]["failed_packages"], 252)
        self.assertEqual(len(payload["logs"]), 50)


if __name__ == "__main__":
    unittest.main()