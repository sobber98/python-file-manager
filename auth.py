from __future__ import annotations

from functools import wraps
from urllib.parse import urlparse

from flask import Blueprint, Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

import models
from utils import get_client_ip


bp = Blueprint("auth", __name__)


def current_username() -> str | None:
    return session.get("username")


def _safe_next_url(target: str | None) -> str:
    if not target:
        return url_for("index")

    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return url_for("index")
    return target


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "需要先登录"}), 401
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("index"))

    error = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = models.get_user_by_username(username)
        ip_address = get_client_ip(request)

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            models.log_operation(user["username"], "login", "登录成功", ip_address)
            next_url = _safe_next_url(request.args.get("next"))
            return redirect(next_url)

        error = "用户名或密码错误"
        models.log_operation(username or "anonymous", "login_failed", "登录失败", ip_address)

    return render_template("login.html", error=error)


@bp.post("/logout")
@login_required
def logout():
    username = session.get("username", "unknown")
    models.log_operation(username, "logout", "退出登录", get_client_ip(request))
    session.clear()
    return redirect(url_for("auth.login"))


def init_app(app: Flask) -> None:
    app.register_blueprint(bp)

    @app.context_processor
    def inject_user() -> dict[str, str | None]:
        return {"current_user": session.get("username")}