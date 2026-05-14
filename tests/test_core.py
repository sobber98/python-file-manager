from __future__ import annotations

import logging
from pathlib import Path
import tempfile
import unittest

from config import Config, ensure_runtime_dirs
import dependency_manager
import logger_manager


class ExtractImportsTests(unittest.TestCase):
    def test_extract_imports_filters_stdlib_and_local_modules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            script_dir = Path(temp_dir)
            (script_dir / "helper.py").write_text("print('local')\n", encoding="utf-8")
            (script_dir / "local_mod.py").write_text("VALUE = 1\n", encoding="utf-8")
            script_path = script_dir / "sample.py"
            script_path.write_text(
                """
import os
import requests
import helper
from numpy.random import rand
from .local_mod import VALUE
import yaml
                """.strip(),
                encoding="utf-8",
            )

            imports = dependency_manager.extract_imports(script_path)

        self.assertEqual(imports, ["PyYAML", "numpy", "requests"])

    def test_extract_imports_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "bom_script.py"
            script_path.write_text("# -*- coding: utf-8 -*-\nimport requests\n", encoding="utf-8-sig")

            imports = dependency_manager.extract_imports(script_path)

        self.assertEqual(imports, ["requests"])


class DependencyOutputFormattingTests(unittest.TestCase):
    def test_format_install_log_generates_summary_and_collapsed_preview(self):
        long_output = "\n".join(
            [
                "Collecting requests",
                "Downloading requests-2.33.1.whl",
                "Installing collected packages: requests",
                "Successfully installed requests-2.33.1",
                "extra line 1",
                "extra line 2",
                "extra line 3",
                "extra line 4",
                "extra line 5",
            ]
        )

        formatted = dependency_manager.format_install_log(
            {
                "package_name": "requests",
                "version": "2.33.1",
                "success": True,
                "error_msg": long_output,
            }
        )

        self.assertTrue(formatted["output_collapsed"])
        self.assertIn("Successfully installed requests-2.33.1", formatted["output_summary"])
        self.assertIn("Collecting requests", formatted["output_preview"])
        self.assertEqual(formatted["output_line_count"], 9)


class LoggerTailTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_log_dir = Config.LOG_DIR
        Config.LOG_DIR = Path(self.temp_dir.name)

    def tearDown(self):
        logger_manager.close_logger(101)
        Config.LOG_DIR = self.original_log_dir
        self.temp_dir.cleanup()

    def test_read_tail_returns_latest_lines(self):
        for index in range(40):
            logger_manager.write_log(101, f"line {index}")

        lines = logger_manager.read_tail(101, 5)

        self.assertEqual(len(lines), 5)
        self.assertTrue(lines[-1].endswith("line 39"))
        self.assertTrue(lines[0].endswith("line 35"))

    def test_formatter_uses_china_timezone(self):
        formatter = logger_manager.ChinaTimeFormatter("%(asctime)s %(message)s")
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
        record.created = 0

        formatted = formatter.format(record)

        self.assertTrue(formatted.startswith("1970-01-01 08:00:00,000 hello"))


class RuntimePathTests(unittest.TestCase):
    def test_ensure_runtime_dirs_creates_database_parent_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_db_path = Config.DB_PATH
            original_upload_dir = Config.UPLOAD_DIR
            original_log_dir = Config.LOG_DIR
            original_venv_dir = Config.VENV_DIR
            original_static_dir = Config.STATIC_DIR
            original_template_dir = Config.TEMPLATE_DIR

            Config.DB_PATH = root / "data" / "app.db"
            Config.UPLOAD_DIR = root / "uploads"
            Config.LOG_DIR = root / "logs"
            Config.VENV_DIR = root / "venvs"
            Config.STATIC_DIR = root / "static"
            Config.TEMPLATE_DIR = root / "templates"

            try:
                ensure_runtime_dirs()
                self.assertTrue(Config.DB_PATH.parent.exists())
            finally:
                Config.DB_PATH = original_db_path
                Config.UPLOAD_DIR = original_upload_dir
                Config.LOG_DIR = original_log_dir
                Config.VENV_DIR = original_venv_dir
                Config.STATIC_DIR = original_static_dir
                Config.TEMPLATE_DIR = original_template_dir


if __name__ == "__main__":
    unittest.main()
