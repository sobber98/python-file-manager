#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── 颜色输出 ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    C_BOLD="\033[1m"
    C_CYAN="\033[1;36m"
    C_GREEN="\033[1;32m"
    C_YELLOW="\033[1;33m"
    C_RED="\033[1;31m"
    C_DIM="\033[2m"
    C_RESET="\033[0m"
else
    C_BOLD="" C_CYAN="" C_GREEN="" C_YELLOW="" C_RED="" C_DIM="" C_RESET=""
fi

print_header() {
    echo
    echo -e "${C_CYAN}${C_BOLD}  PyRunner 管理脚本${C_RESET}"
    echo -e "${C_DIM}  项目目录：$PROJECT_DIR${C_RESET}"
    echo
}

# ── 用法说明 ──────────────────────────────────────────────────────────────────
usage() {
    cat <<EOF
用法：sudo bash deploy/install.sh <命令> [配置文件]

命令：
  install    安装或升级项目（需要配置文件，默认：deploy/install.conf）
  uninstall  卸载服务、Nginx 站点及运行时配置文件
  start      启动服务
  stop       停止服务
  restart    重启服务
  status     查看服务状态

示例：
  sudo bash deploy/install.sh install
  sudo bash deploy/install.sh install /path/to/custom.conf
  sudo bash deploy/install.sh restart
  sudo bash deploy/install.sh uninstall
EOF
}

# ── 交互式菜单 ────────────────────────────────────────────────────────────────
interactive_menu() {
    print_header
    while true; do
        echo -e "${C_BOLD}请选择操作：${C_RESET}"
        echo -e "  ${C_GREEN}1)${C_RESET} 安装 / 升级"
        echo -e "  ${C_YELLOW}2)${C_RESET} 启动服务"
        echo -e "  ${C_YELLOW}3)${C_RESET} 停止服务"
        echo -e "  ${C_YELLOW}4)${C_RESET} 重启服务"
        echo -e "  ${C_CYAN}5)${C_RESET} 查看服务状态"
        echo -e "  ${C_RED}6)${C_RESET} 卸载"
        echo -e "  ${C_DIM}0)${C_RESET} 退出"
        echo
        read -r -p "  请输入编号 [0-6]：" choice
        echo
        case "$choice" in
            1)
                if [[ "$EUID" -ne 0 ]]; then
                    echo -e "${C_RED}安装需要 root 权限，请使用 sudo 重新执行。${C_RESET}"
                else
                    read -r -p "  配置文件路径（回车使用默认 deploy/install.conf）：" cf
                    [[ -n "$cf" ]] && CONFIG_FILE="$cf"
                    cmd_install
                fi
                ;;
            2)
                if [[ "$EUID" -ne 0 ]]; then
                    echo -e "${C_RED}启动需要 root 权限，请使用 sudo 重新执行。${C_RESET}"
                else
                    cmd_start
                fi
                ;;
            3)
                if [[ "$EUID" -ne 0 ]]; then
                    echo -e "${C_RED}停止需要 root 权限，请使用 sudo 重新执行。${C_RESET}"
                else
                    cmd_stop
                fi
                ;;
            4)
                if [[ "$EUID" -ne 0 ]]; then
                    echo -e "${C_RED}重启需要 root 权限，请使用 sudo 重新执行。${C_RESET}"
                else
                    cmd_restart
                fi
                ;;
            5)
                cmd_status
                ;;
            6)
                if [[ "$EUID" -ne 0 ]]; then
                    echo -e "${C_RED}卸载需要 root 权限，请使用 sudo 重新执行。${C_RESET}"
                else
                    cmd_uninstall
                fi
                ;;
            0|q|Q)
                echo "已退出。"
                exit 0
                ;;
            *)
                echo -e "${C_YELLOW}无效输入，请输入 0-6 之间的数字。${C_RESET}"
                ;;
        esac
        echo
    done
}

# ── 参数解析 ──────────────────────────────────────────────────────────────────
COMMAND="${1:-}"
shift || true
CONFIG_FILE="${1:-$SCRIPT_DIR/install.conf}"

# ── 通用检查 ──────────────────────────────────────────────────────────────────
require_root() {
    if [[ "$EUID" -ne 0 ]]; then
        echo "请使用 root 权限执行，例如：sudo bash deploy/install.sh $COMMAND" >&2
        exit 1
    fi
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "缺少命令：$1" >&2
        exit 1
    fi
}

is_true() {
    case "${1,,}" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

# ── 加载配置（install/uninstall 必须有配置文件；其他命令若无则用默认值） ──────
load_config() {
    local required="${1:-false}"

    if [[ -f "$CONFIG_FILE" ]]; then
        # shellcheck disable=SC1090
        source "$CONFIG_FILE"
    elif is_true "$required"; then
        echo "未找到配置文件：$CONFIG_FILE" >&2
        echo "请先编辑 deploy/install.conf，再重新执行安装脚本。" >&2
        exit 1
    fi

    SERVICE_NAME="${SERVICE_NAME:-pyrunner}"
    RUN_USER="${RUN_USER:-www-data}"
    RUN_GROUP="${RUN_GROUP:-www-data}"
    PYTHON_BIN="${PYTHON_BIN:-python3}"
    PYRUNNER_DB_PATH="${PYRUNNER_DB_PATH:-$PROJECT_DIR/data/app.db}"
    PYRUNNER_HOST="${PYRUNNER_HOST:-127.0.0.1}"
    PYRUNNER_PORT="${PYRUNNER_PORT:-5000}"
    INSTALL_SYSTEM_PACKAGES="${INSTALL_SYSTEM_PACKAGES:-true}"
    ENABLE_NGINX="${ENABLE_NGINX:-true}"
    NGINX_SITE_NAME="${NGINX_SITE_NAME:-$SERVICE_NAME}"
    SERVER_NAME="${SERVER_NAME:-_}"
    NGINX_LISTEN_PORT="${NGINX_LISTEN_PORT:-80}"
    NGINX_CLIENT_MAX_BODY_SIZE="${NGINX_CLIENT_MAX_BODY_SIZE:-10m}"
    DISABLE_DEFAULT_NGINX_SITE="${DISABLE_DEFAULT_NGINX_SITE:-true}"
    ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
    ADMIN_PASSWORD="${ADMIN_PASSWORD:-auto}"
    SECRET_KEY="${SECRET_KEY:-auto}"
    MAX_UPLOAD_SIZE="${MAX_UPLOAD_SIZE:-10485760}"
    LOG_MAX_BYTES="${LOG_MAX_BYTES:-5242880}"
    LOG_BACKUP_COUNT="${LOG_BACKUP_COUNT:-3}"
    AUTO_RESTART_DELAY_SECONDS="${AUTO_RESTART_DELAY_SECONDS:-2}"
    AUTO_RESTART_MAX_ATTEMPTS="${AUTO_RESTART_MAX_ATTEMPTS:-3}"
    AUTO_RESTART_RESET_AFTER_SECONDS="${AUTO_RESTART_RESET_AFTER_SECONDS:-60}"
    PROCESS_MONITOR_INTERVAL_SECONDS="${PROCESS_MONITOR_INTERVAL_SECONDS:-5}"
    GUNICORN_WORKERS="${GUNICORN_WORKERS:-1}"
    GUNICORN_THREADS="${GUNICORN_THREADS:-4}"
    GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-120}"
    GUNICORN_GRACEFUL_TIMEOUT="${GUNICORN_GRACEFUL_TIMEOUT:-30}"
    GUNICORN_KEEPALIVE="${GUNICORN_KEEPALIVE:-5}"

    RUNTIME_CONFIG_DIR="/etc/${SERVICE_NAME}"
    RUNTIME_ENV_FILE="$RUNTIME_CONFIG_DIR/${SERVICE_NAME}.env"
    SYSTEMD_SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
    NGINX_SITE_AVAILABLE="/etc/nginx/sites-available/${NGINX_SITE_NAME}.conf"
    NGINX_SITE_ENABLED="/etc/nginx/sites-enabled/${NGINX_SITE_NAME}.conf"
}

# ── 安装辅助函数 ───────────────────────────────────────────────────────────────
generate_secret() {
    "$PYTHON_BIN" - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
}

generate_password() {
    "$PYTHON_BIN" - <<'PY'
import secrets
print(secrets.token_urlsafe(18))
PY
}

ensure_system_account() {
    if ! getent group "$RUN_GROUP" >/dev/null 2>&1; then
        groupadd --system "$RUN_GROUP"
    fi
    if ! id -u "$RUN_USER" >/dev/null 2>&1; then
        useradd --system --gid "$RUN_GROUP" --home-dir "$PROJECT_DIR" --shell /usr/sbin/nologin "$RUN_USER"
    fi
}

install_system_packages() {
    require_command apt-get
    local packages=(python3 python3-venv python3-pip)
    if is_true "$ENABLE_NGINX"; then
        packages+=(nginx)
    fi
    apt-get update
    apt-get install -y "${packages[@]}"
}

setup_virtualenv() {
    require_command "$PYTHON_BIN"
    local venv_dir="$PROJECT_DIR/.venv"
    local venv_python="$venv_dir/bin/python"

    if [[ ! -x "$venv_python" ]] || ! "$venv_python" -m pip --version >/dev/null 2>&1; then
        "$PYTHON_BIN" -m venv --clear "$venv_dir"
    fi

    "$venv_python" -m pip install --upgrade pip
    "$PROJECT_DIR/.venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
}

prepare_runtime_dirs() {
    local legacy_db_path="$PROJECT_DIR/app.db"
    local db_dir suffix

    db_dir="$(dirname "$PYRUNNER_DB_PATH")"
    install -d -o "$RUN_USER" -g "$RUN_GROUP" -m 0755 "$db_dir"

    if [[ "$PYRUNNER_DB_PATH" != "$legacy_db_path" && -f "$legacy_db_path" && ( ! -e "$PYRUNNER_DB_PATH" || ! -s "$PYRUNNER_DB_PATH" ) ]]; then
        install -o "$RUN_USER" -g "$RUN_GROUP" -m 0640 "$legacy_db_path" "$PYRUNNER_DB_PATH"
    fi
    if [[ "$PYRUNNER_DB_PATH" != "$legacy_db_path" ]]; then
        for suffix in -wal -shm; do
            if [[ -e "${legacy_db_path}${suffix}" && ! -e "${PYRUNNER_DB_PATH}${suffix}" ]]; then
                install -o "$RUN_USER" -g "$RUN_GROUP" -m 0640 "${legacy_db_path}${suffix}" "${PYRUNNER_DB_PATH}${suffix}"
            fi
        done
    fi

    install -d -o "$RUN_USER" -g "$RUN_GROUP" -m 0755 "$PROJECT_DIR/uploads"
    install -d -o "$RUN_USER" -g "$RUN_GROUP" -m 0755 "$PROJECT_DIR/logs"
    install -d -o "$RUN_USER" -g "$RUN_GROUP" -m 0755 "$PROJECT_DIR/venvs"
    touch "$PYRUNNER_DB_PATH"
    chown "$RUN_USER:$RUN_GROUP" "$PYRUNNER_DB_PATH"
    chmod 0640 "$PYRUNNER_DB_PATH"
}

write_runtime_env() {
    install -d -o root -g "$RUN_GROUP" -m 0750 "$RUNTIME_CONFIG_DIR"
    cat > "$RUNTIME_ENV_FILE" <<EOF
SECRET_KEY=$SECRET_KEY
ADMIN_USERNAME=$ADMIN_USERNAME
ADMIN_PASSWORD=$ADMIN_PASSWORD
PYRUNNER_DB_PATH=$PYRUNNER_DB_PATH
PYRUNNER_HOST=$PYRUNNER_HOST
PYRUNNER_PORT=$PYRUNNER_PORT
MAX_UPLOAD_SIZE=$MAX_UPLOAD_SIZE
LOG_MAX_BYTES=$LOG_MAX_BYTES
LOG_BACKUP_COUNT=$LOG_BACKUP_COUNT
AUTO_RESTART_DELAY_SECONDS=$AUTO_RESTART_DELAY_SECONDS
AUTO_RESTART_MAX_ATTEMPTS=$AUTO_RESTART_MAX_ATTEMPTS
AUTO_RESTART_RESET_AFTER_SECONDS=$AUTO_RESTART_RESET_AFTER_SECONDS
PROCESS_MONITOR_INTERVAL_SECONDS=$PROCESS_MONITOR_INTERVAL_SECONDS
GUNICORN_WORKERS=$GUNICORN_WORKERS
GUNICORN_THREADS=$GUNICORN_THREADS
GUNICORN_TIMEOUT=$GUNICORN_TIMEOUT
GUNICORN_GRACEFUL_TIMEOUT=$GUNICORN_GRACEFUL_TIMEOUT
GUNICORN_KEEPALIVE=$GUNICORN_KEEPALIVE
EOF
    chown root:"$RUN_GROUP" "$RUNTIME_ENV_FILE"
    chmod 0640 "$RUNTIME_ENV_FILE"
}

write_systemd_service() {
    cat > "$SYSTEMD_SERVICE_FILE" <<EOF
[Unit]
Description=PyRunner Web Manager
After=network.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_GROUP
WorkingDirectory=$PROJECT_DIR
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-$RUNTIME_ENV_FILE
ExecStart=$PROJECT_DIR/.venv/bin/gunicorn -c $PROJECT_DIR/gunicorn.conf.py app:app
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
    chmod 0644 "$SYSTEMD_SERVICE_FILE"
}

write_nginx_config() {
    cat > "$NGINX_SITE_AVAILABLE" <<EOF
upstream ${SERVICE_NAME}_app {
    server ${PYRUNNER_HOST}:${PYRUNNER_PORT};
    keepalive 16;
}

server {
    listen ${NGINX_LISTEN_PORT};
    server_name ${SERVER_NAME};

    client_max_body_size ${NGINX_CLIENT_MAX_BODY_SIZE};

    location /static/ {
        alias ${PROJECT_DIR}/static/;
        access_log off;
        expires 1h;
    }

    location / {
        proxy_pass http://${SERVICE_NAME}_app;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Connection "";
        proxy_read_timeout 120s;
    }
}
EOF
    ln -sf "$NGINX_SITE_AVAILABLE" "$NGINX_SITE_ENABLED"
    if is_true "$DISABLE_DEFAULT_NGINX_SITE" && [[ -L /etc/nginx/sites-enabled/default ]]; then
        rm -f /etc/nginx/sites-enabled/default
    fi
}

reload_services() {
    require_command systemctl
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    if ! systemctl restart "$SERVICE_NAME"; then
        journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true
        exit 1
    fi

    if is_true "$ENABLE_NGINX"; then
        require_command nginx
        nginx -t
        systemctl enable nginx
        systemctl reload nginx
    fi
}

health_check() {
    local health_host="$PYRUNNER_HOST"
    if [[ "$health_host" == "0.0.0.0" ]]; then
        health_host="127.0.0.1"
    fi
    if ! HEALTH_URL="http://${health_host}:${PYRUNNER_PORT}/health" "$PROJECT_DIR/.venv/bin/python" - <<'PY'
import json
import os
import time
import urllib.error
import urllib.request

url = os.environ["HEALTH_URL"]
deadline = time.monotonic() + 30
last_error = None

while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("status") != "ok":
            raise SystemExit(f"健康检查失败: {payload}")
        print(f"健康检查通过: {url}")
        raise SystemExit(0)
    except urllib.error.URLError as error:
        last_error = error
        time.sleep(1)

raise SystemExit(f"健康检查超时，服务未在 30 秒内就绪: {last_error}")
PY
    then
        journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true
        exit 1
    fi
}

# ── 命令实现 ───────────────────────────────────────────────────────────────────
cmd_install() {
    load_config true   # 安装必须有配置文件

    local generated_admin_password=""

    if is_true "$INSTALL_SYSTEM_PACKAGES"; then
        install_system_packages
    fi

    if [[ "$SECRET_KEY" == "auto" ]]; then
        SECRET_KEY="$(generate_secret)"
    fi

    if [[ "$ADMIN_PASSWORD" == "auto" ]]; then
        generated_admin_password="$(generate_password)"
        ADMIN_PASSWORD="$generated_admin_password"
    fi

    ensure_system_account
    setup_virtualenv
    prepare_runtime_dirs
    write_runtime_env
    write_systemd_service
    if is_true "$ENABLE_NGINX"; then
        write_nginx_config
    fi
    reload_services
    health_check

    echo
    echo "部署完成。"
    echo "项目目录       : $PROJECT_DIR"
    echo "systemd 服务   : $SERVICE_NAME"
    echo "运行环境文件   : $RUNTIME_ENV_FILE"
    if is_true "$ENABLE_NGINX"; then
        echo "Nginx 站点     : $NGINX_SITE_AVAILABLE"
    fi
    echo "管理员账号     : $ADMIN_USERNAME"
    if [[ -n "$generated_admin_password" ]]; then
        echo "管理员密码(自动生成): $generated_admin_password"
    else
        echo "管理员密码     : 使用 install.conf 中配置的值"
    fi
}

cmd_uninstall() {
    load_config false  # 配置文件可选，用于确定服务名与 Nginx 站点名

    echo "即将卸载服务：$SERVICE_NAME"
    echo "将移除：systemd 服务文件、Nginx 站点配置、运行时环境文件"
    echo "不会移除：项目代码、数据库、上传文件、虚拟环境"
    echo
    read -r -p "确认卸载？[y/N] " confirm
    if [[ "${confirm,,}" != "y" ]]; then
        echo "已取消。"
        exit 0
    fi

    require_command systemctl

    # 停止并禁用 systemd 服务
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        echo "正在停止服务 $SERVICE_NAME ..."
        systemctl stop "$SERVICE_NAME"
    fi
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl disable "$SERVICE_NAME"
    fi

    # 删除 systemd 服务文件
    if [[ -f "$SYSTEMD_SERVICE_FILE" ]]; then
        rm -f "$SYSTEMD_SERVICE_FILE"
        echo "已删除：$SYSTEMD_SERVICE_FILE"
    fi
    systemctl daemon-reload

    # 删除 Nginx 站点配置
    if [[ -L "$NGINX_SITE_ENABLED" ]]; then
        rm -f "$NGINX_SITE_ENABLED"
        echo "已删除：$NGINX_SITE_ENABLED"
    fi
    if [[ -f "$NGINX_SITE_AVAILABLE" ]]; then
        rm -f "$NGINX_SITE_AVAILABLE"
        echo "已删除：$NGINX_SITE_AVAILABLE"
    fi
    if command -v nginx >/dev/null 2>&1 && systemctl is-active --quiet nginx 2>/dev/null; then
        nginx -t && systemctl reload nginx || true
    fi

    # 删除运行时环境文件目录
    if [[ -d "$RUNTIME_CONFIG_DIR" ]]; then
        rm -rf "$RUNTIME_CONFIG_DIR"
        echo "已删除：$RUNTIME_CONFIG_DIR"
    fi

    echo
    echo "卸载完成。项目文件保留在：$PROJECT_DIR"
}

cmd_start() {
    load_config false
    require_command systemctl
    echo "正在启动服务：$SERVICE_NAME"
    systemctl start "$SERVICE_NAME"
    systemctl --no-pager status "$SERVICE_NAME"
}

cmd_stop() {
    load_config false
    require_command systemctl
    echo "正在停止服务：$SERVICE_NAME"
    systemctl stop "$SERVICE_NAME"
    echo "服务已停止。"
}

cmd_restart() {
    load_config false
    require_command systemctl
    echo "正在重启服务：$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    systemctl --no-pager status "$SERVICE_NAME"
}

cmd_status() {
    load_config false
    require_command systemctl
    systemctl --no-pager status "$SERVICE_NAME" || true
}

# ── 入口分发 ───────────────────────────────────────────────────────────────────
case "$COMMAND" in
    install)
        require_root
        cmd_install
        ;;
    uninstall)
        require_root
        cmd_uninstall
        ;;
    start)
        require_root
        cmd_start
        ;;
    stop)
        require_root
        cmd_stop
        ;;
    restart)
        require_root
        cmd_restart
        ;;
    status)
        cmd_status
        ;;
    help|--help|-h)
        usage
        ;;
    "")
        # 无参数时进入交互式菜单
        interactive_menu
        ;;
    *)
        echo "未知命令：$COMMAND" >&2
        echo >&2
        usage >&2
        exit 1
        ;;
esac
