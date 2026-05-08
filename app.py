from __future__ import annotations

from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.utils import secure_filename

from auth import current_username, init_app as init_auth, login_required
from config import Config, ensure_runtime_dirs
import dependency_manager
import logger_manager
import models
import process_manager
from utils import allowed_script_file, get_client_ip


csrf = CSRFProtect()


def json_error(message: str, status_code: int):
    return jsonify({"error": message}), status_code


def request_data() -> dict:
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict()


def upload_script_path(filename: str) -> Path:
    sanitized = secure_filename(filename)
    stem = Path(sanitized).stem or "script"
    suffix = Path(sanitized).suffix or ".py"
    return Config.UPLOAD_DIR / f"{stem}{suffix}"


def get_script_by_upload_path(file_path: Path) -> dict | None:
    target_path = str(file_path)
    for script in models.list_scripts():
        if script.get("file_path") == target_path:
            return script
    return None


def serialize_scripts() -> list[dict]:
    process_manager.reconcile_scripts()
    scripts = models.list_scripts()
    for script in scripts:
        script["installing"] = dependency_manager.is_install_active(script["id"])
    return scripts


def serialize_script(script_id: int) -> dict | None:
    process_manager.reconcile_scripts()
    script = models.get_script_by_id(script_id)
    if script is None:
        return None
    script["installing"] = dependency_manager.is_install_active(script_id)
    return script


def create_app() -> Flask:
    ensure_runtime_dirs()
    app = Flask(__name__, template_folder=str(Config.TEMPLATE_DIR), static_folder=str(Config.STATIC_DIR))
    app.config.from_object(Config)
    csrf.init_app(app)
    init_auth(app)

    models.init_db()
    models.create_default_admin(Config.ADMIN_USERNAME, Config.ADMIN_PASSWORD)
    default_environment = dependency_manager.get_or_create_default_environment()
    models.ensure_scripts_have_environment(default_environment["id"])
    process_manager.reconcile_scripts()
    process_manager.start_process_monitoring()

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error: CSRFError):
        if request.path.startswith("/api/"):
            return json_error(error.description or "CSRF 校验失败", 400)
        return render_template("login.html", error=error.description or "CSRF 校验失败"), 400

    @app.errorhandler(413)
    def handle_large_upload(_error):
        if request.path.startswith("/api/"):
            return json_error("上传文件超过大小限制", 413)
        return render_template("login.html", error="上传文件超过大小限制"), 413

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    @login_required
    def index():
        return render_template("dashboard.html")

    @app.get("/scripts/<int:script_id>")
    @login_required
    def script_detail(script_id: int):
        script = serialize_script(script_id)
        if script is None:
            abort(404)
        return render_template("script_detail.html", script=script)

    @app.get("/api/scripts")
    @login_required
    def list_scripts_api():
        return jsonify({"scripts": serialize_scripts()})

    @app.post("/api/scripts/upload")
    @login_required
    def upload_script_api():
        upload = request.files.get("script")
        if upload is None or not upload.filename:
            return json_error("请选择要上传的 Python 文件", 400)

        filename = secure_filename(upload.filename)
        if not allowed_script_file(filename):
            return json_error("仅支持上传 .py 文件", 400)

        target_path = upload_script_path(filename)
        existing_script = get_script_by_upload_path(target_path)
        upload.save(target_path)

        username = current_username() or "unknown"
        ip_address = get_client_ip(request)
        auto_install = request.form.get("auto_install", "true").lower() in {"1", "true", "on", "yes"}
        manual_packages = request.form.get("manual_packages")
        packages: list[str] = []

        if existing_script is None:
            default_environment = dependency_manager.get_or_create_default_environment()
            script = models.create_script(target_path.stem, str(target_path), default_environment["id"])
            models.log_operation(username, "upload", f"上传脚本 {target_path.name}", ip_address)
            if auto_install:
                packages = dependency_manager.schedule_dependency_install(script, manual_packages, username, ip_address)
            return jsonify({"message": "脚本上传成功", "script": models.get_script_by_id(script["id"]), "packages": packages}), 201

        script = models.get_script_by_id(existing_script["id"]) or existing_script
        models.log_operation(username, "replace_script", f"替换脚本 {target_path.name}", ip_address)
        try:
            script = process_manager.restart_script(script, username, ip_address)
        except (FileNotFoundError, RuntimeError) as error:
            return json_error(f"同名脚本已替换，但重启失败：{error}", 409)

        if auto_install:
            try:
                packages = dependency_manager.schedule_dependency_install(script, manual_packages, username, ip_address)
            except RuntimeError as error:
                return jsonify(
                    {
                        "message": f"同名脚本已替换并重启，但依赖安装未启动：{error}",
                        "script": models.get_script_by_id(script["id"]),
                        "packages": [],
                    }
                )

        return jsonify({"message": "同名脚本已替换并重启", "script": models.get_script_by_id(script["id"]), "packages": packages})

    @app.delete("/api/scripts/<int:script_id>")
    @login_required
    def delete_script_api(script_id: int):
        script = models.get_script_by_id(script_id)
        if script is None:
            return json_error("脚本不存在", 404)

        if script.get("pid") and process_manager.pid_matches_script(script["pid"], script["file_path"]):
            return json_error("请先停止脚本后再删除", 409)

        script_path = Path(script["file_path"])
        if script_path.exists():
            script_path.unlink()
        logger_manager.delete_logs(script_id)
        models.delete_script(script_id)
        models.log_operation(current_username() or "unknown", "delete_script", f"删除脚本 {script['name']}", get_client_ip(request))
        return jsonify({"message": "脚本已删除"})

    @app.post("/api/scripts/<int:script_id>/start")
    @login_required
    def start_script_api(script_id: int):
        script = models.get_script_by_id(script_id)
        if script is None:
            return json_error("脚本不存在", 404)

        try:
            started = process_manager.start_script(script, current_username() or "unknown", get_client_ip(request))
        except FileNotFoundError as error:
            return json_error(str(error), 404)
        except RuntimeError as error:
            return json_error(str(error), 409)

        return jsonify({"message": "脚本已启动", "script": started})

    @app.post("/api/scripts/<int:script_id>/stop")
    @login_required
    def stop_script_api(script_id: int):
        script = models.get_script_by_id(script_id)
        if script is None:
            return json_error("脚本不存在", 404)

        force = str(request_data().get("force", "false")).lower() in {"1", "true", "yes", "on"}
        stopped = process_manager.stop_script(script, current_username() or "unknown", get_client_ip(request), force=force)
        return jsonify({"message": "脚本已停止", "script": stopped})

    @app.post("/api/scripts/<int:script_id>/restart")
    @login_required
    def restart_script_api(script_id: int):
        script = models.get_script_by_id(script_id)
        if script is None:
            return json_error("脚本不存在", 404)

        try:
            restarted = process_manager.restart_script(script, current_username() or "unknown", get_client_ip(request))
        except FileNotFoundError as error:
            return json_error(str(error), 404)
        except RuntimeError as error:
            return json_error(str(error), 409)

        return jsonify({"message": "脚本已重启", "script": restarted})

    @app.post("/api/scripts/<int:script_id>/install-dependencies")
    @login_required
    def install_dependencies_api(script_id: int):
        script = models.get_script_by_id(script_id)
        if script is None:
            return json_error("脚本不存在", 404)

        payload = request_data()
        try:
            packages = dependency_manager.schedule_dependency_install(
                script,
                payload.get("manual_packages", ""),
                current_username() or "unknown",
                get_client_ip(request),
            )
        except RuntimeError as error:
            return json_error(str(error), 409)

        return jsonify({"message": "依赖安装任务已启动", "packages": packages})

    @app.get("/api/scripts/<int:script_id>/dependencies")
    @login_required
    def dependency_logs_api(script_id: int):
        script = models.get_script_by_id(script_id)
        if script is None:
            return json_error("脚本不存在", 404)

        progress = dependency_manager.get_install_progress(script_id)
        logs = dependency_manager.format_install_logs(models.list_dependency_install_logs(script_id, limit=50))
        return jsonify(
            {
                "installing": progress["installing"],
                "progress": progress,
                "logs": logs,
            }
        )

    @app.get("/api/logs/<int:script_id>")
    @login_required
    def script_logs_api(script_id: int):
        script = models.get_script_by_id(script_id)
        if script is None:
            return json_error("脚本不存在", 404)

        tail = request.args.get("tail", default=300, type=int)
        after = request.args.get("after", default=0, type=int)
        return jsonify(logger_manager.read_log_update(script_id, after=after, tail=tail))

    @app.get("/api/operation-logs")
    @login_required
    def operation_logs_api():
        page = request.args.get("page", default=1, type=int)
        limit = request.args.get("limit", default=20, type=int)
        page = max(1, page)
        limit = max(1, min(limit, 100))
        offset = (page - 1) * limit
        total = models.count_operation_logs()
        logs = models.list_operation_logs(limit=limit, offset=offset)
        return jsonify(
            {
                "logs": logs,
                "pagination": {
                    "page": page,
                    "page_size": limit,
                    "total": total,
                    "has_prev": page > 1,
                    "has_next": offset + len(logs) < total,
                },
            }
        )

    @app.get("/api/environments")
    @login_required
    def list_environments_api():
        return jsonify({"environments": models.list_environments()})

    @app.post("/api/environments")
    @login_required
    def create_environment_api():
        payload = request_data()
        name = str(payload.get("name", "")).strip()
        if not name:
            return json_error("环境名称不能为空", 400)

        try:
            environment = dependency_manager.create_named_environment(name)
        except ValueError as error:
            return json_error(str(error), 400)

        models.log_operation(current_username() or "unknown", "create_environment", f"创建环境 {environment['name']}", get_client_ip(request))
        return jsonify({"message": "环境已创建", "environment": environment}), 201

    @app.patch("/api/scripts/<int:script_id>/environment")
    @login_required
    def update_script_environment_api(script_id: int):
        script = models.get_script_by_id(script_id)
        if script is None:
            return json_error("脚本不存在", 404)

        if script.get("pid") and process_manager.pid_matches_script(script["pid"], script["file_path"]):
            return json_error("运行中的脚本不能切换环境", 409)

        payload = request_data()
        try:
            environment_id = int(payload.get("environment_id", 0))
        except (TypeError, ValueError):
            return json_error("环境参数无效", 400)

        environment = models.get_environment_by_id(environment_id)
        if environment is None:
            return json_error("目标环境不存在", 404)

        updated = models.update_script_environment(script_id, environment_id)
        models.log_operation(current_username() or "unknown", "switch_environment", f"脚本 {script['name']} 切换到环境 {environment['name']}", get_client_ip(request))
        return jsonify({"message": "脚本环境已更新", "script": updated})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host=Config.DEFAULT_HOST, port=Config.DEFAULT_PORT, debug=False)