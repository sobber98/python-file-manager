# PyRunner Web Manager 项目规划

## 1. 项目概述
**目标**：在 Linux 服务器上通过 Web 界面，实现对多个 Python 脚本的远程管理，包括上传、自动依赖安装、后台运行/停止、日志查看与滚动覆盖，所有操作单用户鉴权并完整记录。

**核心特性**：
- 轻量 Web 后端（Flask + SQLite）
- 自动解析 Python 脚本的 `import` 语句以提取依赖，无需上传 `requirements.txt`
- 默认共享虚拟环境，避免资源浪费，同时支持按需独立环境
- 日志按大小自动轮转，前端实时查看
- 单用户模式，记录操作者、时间、IP
- 最小化资源消耗，适合中小规模服务器

---

## 2. 技术栈
| 层次 | 选型 | 说明 |
|------|------|------|
| 后端框架 | Flask 2.x | 轻量、扩展性好 |
| 前端 | HTML + Bootstrap 5 + Vanilla JS | 无需构建工具，直接模板渲染 |
| 数据库 | SQLite3 | 零配置，适合单用户场景 |
| 进程管理 | `subprocess` + `psutil`（可选） | 管理脚本启停，检测存活 |
| 日志 | `logging` + `RotatingFileHandler` | 按文件大小滚动覆盖 |
| 依赖分析 | `ast` 模块（标准库） | 解析 `import` 语句 |
| 认证 | Flask Session + 密码哈希 | 单用户，固定凭据 |
| 部署 | Gunicorn + Nginx（生产）或简单 `python app.py` | 灵活选择 |

---

## 3. 核心功能模块

### 3.1 用户认证与操作记录
- 首次启动自动创建 `admin` 用户（密码可配置）
- 登录后 Session 维持，除登录页外所有请求需认证
- 操作日志记录到 `operation_log` 表：**用户名、动作（上传、启动、停止、安装依赖等）、详情、客户端 IP、时间**

### 3.2 脚本上传与列表
- 支持上传单个 `.py` 文件，大小限制可配置
- 脚本存储在专用上传目录
- 同名 `.py` 文件再次上传时覆盖原文件，并复用原脚本记录自动重启
- 前端显示脚本列表、运行状态（running/stopped/error）

### 3.3 自动依赖提取与安装
- 使用 `ast` 静态分析 `.py` 文件的 `import` 及 `from ... import` 语句
- 自动过滤 Python 标准库
- 得到候选包名列表，使用对应虚拟环境的 `pip install` 安装
- 安装失败（如包名不匹配）记录日志，允许用户手动补充
- **取消强制上传 `requirements.txt`**，但保留手动输入接口

### 3.4 进程管理（后台运行）
- 通过 `subprocess.Popen` 启动脚本，重定向输出到日志管道
- 支持基于 pid 的状态检测、正常终止（SIGTERM）与强制终止（SIGKILL）
- 管理进程组，避免僵尸进程
- 运行中的脚本若异常退出，将自动监测并按默认策略自动重启
- 每个脚本可单独启动/停止/重启

### 3.5 日志管理
- 每个脚本一个日志文件，路径 `logs/script_<id>.log`
- 主进程通过管道读取子进程输出，写入 `RotatingFileHandler`
- 日志按大小滚动（默认 5 MB），保留最近 3 个备份
- API 提供按行数取尾部日志，前端实时刷新

### 3.6 虚拟环境管理（资源优化）
- 默认创建**共享虚拟环境** `venvs/default`，所有脚本默认使用
- 支持创建额外独立环境，按需隔离冲突依赖
- 环境与脚本通过 `environment_id` 关联
- 依赖安装时，针对脚本所属环境执行 `pip install`

---

## 4. 数据库设计

### 表 `user`
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| username | TEXT | UNIQUE NOT NULL | |
| password_hash | TEXT | NOT NULL | |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | |

### 表 `script`
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| name | TEXT | NOT NULL | 显示名称 |
| file_path | TEXT | NOT NULL | 脚本绝对路径 |
| environment_id | INTEGER | REFERENCES environment(id) | 关联的虚拟环境 |
| status | TEXT | DEFAULT 'stopped' | running/stopped/error |
| pid | INTEGER | | 运行进程ID |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | |

> **注意**：SQLite 不支持 `ON UPDATE CURRENT_TIMESTAMP`，`updated_at` 需由应用层在每次 UPDATE 时手动赋值。

### 表 `environment`
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| name | TEXT | UNIQUE NOT NULL | 如 'default' |
| venv_path | TEXT | NOT NULL | 虚拟环境绝对路径 |
| python_path | TEXT | NOT NULL | python 可执行文件路径 |
| is_default | BOOLEAN | DEFAULT 0 | 是否为默认环境 |

### 表 `operation_log`
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| username | TEXT | NOT NULL | |
| action | TEXT | NOT NULL | 动作类型 |
| details | TEXT | | 补充信息 |
| ip_address | TEXT | | 客户端IP |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | |

### 表 `dependency_install_log`（可选）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| script_id | INTEGER | REFERENCES script(id) | |
| package_name | TEXT | | |
| version | TEXT | | |
| success | BOOLEAN | | 是否安装成功 |
| error_msg | TEXT | | 失败原因 |
| installed_at | TIMESTAMP | | |

---

## 5. 项目文件结构

```text
pyrunner/
├── app.py                 # Flask 应用入口，注册蓝图与配置
├── config.py              # 配置类（路径、密钥、日志限制等）
├── auth.py                # 认证相关（登录、登出、装饰器）
├── models.py              # 数据库初始化、表创建、CRUD 函数
├── process_manager.py     # 进程启动、停止、状态探测
├── logger_manager.py      # 日志文件创建、读写、轮转
├── dependency_manager.py  # 依赖提取、虚拟环境管理、pip 安装
├── utils.py               # IP 获取、标准库列表等工具
├── templates/
│   ├── login.html         # 登录页面
│   ├── dashboard.html     # 主管理界面（脚本列表、操作区、日志窗口）
│   └── base.html          # 基础模板（可选）
├── static/
│   └── (css/js 可选，或使用 CDN)
├── uploads/               # 用户上传的脚本存储目录
├── logs/                  # 运行日志存放目录
├── venvs/                 # 虚拟环境目录（default + 自定义）
├── data/
│   └── app.db             # SQLite 数据库文件（部署时默认创建）
└── requirements.txt       # 项目自身依赖
```

---

## 6. 核心流程说明

### 6.1 依赖自动提取流程
1. 用户上传 Python 脚本（或已存在脚本）
2. 用户点击“安装依赖”（或系统配置为上传后自动安装）
3. 后端调用 `dependency_manager.extract_imports(script_path)`
   - 使用 `ast.parse` 遍历所有 `Import` 和 `ImportFrom` 节点
   - 提取顶层模块名（如 `numpy`、`requests`）
   - 过滤内置标准库（预置列表或 `sys.stdlib_module_names`）
4. 确认脚本所属虚拟环境（如不存在则创建默认环境）
5. 在后台线程中使用 `subprocess.run([python_path, '-m', 'pip', 'install', *candidates])` 安装，避免阻塞 Web 请求
6. 捕获失败信息，记录到数据库或返回前端

### 6.2 脚本启动流程
1. 校验脚本文件存在且状态为 `stopped`
2. 打开日志文件（通过 `logger_manager` 获取 handler）
3. 以 `subprocess.Popen` 启动：`[python_path, script_path]`，stdout/stderr 重定向到 `PIPE`
4. 在单独线程中读取管道输出，利用 `RotatingFileHandler` 写入日志文件
5. 记录 PID 并更新状态为 `running`
6. 操作记录写入 `operation_log`
7. 代理线程监测管道 EOF（子进程退出），自动将 `status` 更新为 `stopped` 或 `error`

### 6.3 日志滚动策略
- 使用 `logging.handlers.RotatingFileHandler` 配置 `maxBytes=5*1024*1024`, `backupCount=3`
- 主代理线程持续写入，当文件达到上限自动轮转，旧文件命名为 `script_<id>.log.1`、`.2`、`.3`
- 前端通过 `GET /api/logs/<id>?tail=500` 读取最新 N 行，不会读取归档文件

---

## 7. 安全设计
- 密码哈希存储（Werkzeug 提供的 `generate_password_hash` / `check_password_hash`）
- 所有命令调用避免 `shell=True`，使用列表参数防止注入
- 上传文件严格限制扩展名为 `.py`，限制大小，保存在隔离目录
- 虚拟环境目录与上传目录均在应用根目录下，脚本执行路径不可自定义
- Web 默认绑定 `127.0.0.1`，生产环境建议 Nginx 反向代理 + HTTPS
- Session 密钥随机生成，生产环境务必修改
- 启用 CSRF 防护（如 Flask-WTF），防止跨站请求伪造攻击

---

## 8. 部署与运行
### 开发模式
```bash
pip install -r requirements.txt
python app.py
# 访问 http://127.0.0.1:5000
```

### 生产模式

- 使用 Gunicorn 启动：`gunicorn -w 1 -b 127.0.0.1:5000 app:app`（建议单 worker，多 worker 进程不共享内存，会导致 PID 状态不一致）
- 搭配 Nginx 反向代理，配置 SSL
- 设置 `SECRET_KEY` 环境变量
- 可使用 systemd 管理服务，实现开机自启

### 依赖清单（项目自身）

```text
Flask>=2.0
psutil>=5.8      # 可选，但推荐用于更准确进程管理
gunicorn>=20.1   # 生产环境需要
```

---

## 9. 开发路线图

1. 搭建骨架：Flask 项目结构、配置加载、基础路由
2. 数据库与认证：建表、默认用户创建、登录/登出、`@login_required`
3. 脚本上传与管理：文件上传、列表展示、数据库 CRUD
4. 虚拟环境管理：创建默认环境，关联脚本与环境
5. 依赖自动提取与安装：ast 解析、过滤标准库、后台线程 pip 安装
6. 进程管理：启动、停止、状态检测、代理日志输出
7. 日志滚动与查看：RotatingFileHandler 集成、API 读取最新日志
8. 操作记录：所有动作记录到 `operation_log`，前端展示
9. 前端优化：状态轮询、实时日志刷新、错误提示
10. 测试与文档：关键函数测试、README、部署说明

---

## 10. 资源消耗考量

- **磁盘**：默认共享虚拟环境，所有脚本共用一套第三方包，避免重复；仅冲突时才新建环境。
- **内存**：每个运行的 Python 进程加载各自依赖，正常脚本内存占用很小；可配置同时运行数量上限。
- **CPU**：仅在启动/安装依赖时短暂占用；日志写入和 Web 请求均为轻量操作。

此方案在提供足够隔离性的前提下，最大限度地复用了公共环境，实现了"最小化资源消耗"的设计目标。

---

## 11. 当前仓库的部署说明

以下内容对应当前仓库已提供的部署文件：

- Gunicorn 配置：`gunicorn.conf.py`
- 一键部署脚本：`deploy/install.sh`
- 一键部署配置：`deploy/install.conf`
- systemd 服务文件：`deploy/systemd/pyrunner.service`
- Nginx 站点配置：`deploy/nginx/pyrunner.conf`
- 环境变量模板：`deploy/systemd/pyrunner.env.example`

### 11.1 一键部署脚本

当前仓库已新增一键部署脚本，适用于 Debian/Ubuntu + systemd + Nginx 的单机部署场景。

使用方式：

```bash
cd /opt/python-file-manager
editor deploy/install.conf
sudo bash deploy/install.sh
```

脚本会自动完成以下操作：

- 安装 Python、venv、pip 和 Nginx（可通过配置关闭）
- 创建 `.venv` 并安装 `requirements.txt`
- 创建 `uploads/`、`logs/`、`venvs/`、`data/app.db`
- 生成 `/etc/<service_name>/<service_name>.env`
- 安装并重启 systemd 服务
- 生成并启用 Nginx 站点配置
- 自动执行健康检查

`deploy/install.conf` 中常用配置项说明：

- `SERVICE_NAME`：systemd 服务名，默认 `pyrunner`
- `RUN_USER` / `RUN_GROUP`：服务运行用户和组，默认 `www-data`
- `PYRUNNER_DB_PATH`：SQLite 数据库文件路径，默认 `/opt/python-file-manager/data/app.db`
- `PYRUNNER_HOST` / `PYRUNNER_PORT`：Gunicorn 监听地址与端口
- `ADMIN_USERNAME` / `ADMIN_PASSWORD`：后台管理员账号，密码支持 `auto`
- `SECRET_KEY`：Flask 密钥，支持 `auto`
- `ENABLE_NGINX`：是否安装并配置 Nginx 反向代理
- `SERVER_NAME`：Nginx 的 `server_name`
- `GUNICORN_WORKERS` / `GUNICORN_THREADS`：Gunicorn 并发参数

如果你只想部署应用本身，不接 Nginx，可将 `ENABLE_NGINX=false`。

### 11.2 前置条件

推荐在 Debian/Ubuntu 上准备以下软件：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx
```

### 11.3 初始化项目与 Python 环境

假设项目已经位于 `/opt/python-file-manager`：

```bash
cd /opt/python-file-manager
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

### 11.4 创建运行时目录和权限

当前 `pyrunner.service` 以 `www-data` 用户运行，因此需要提前把运行时目录交给该用户写入：

```bash
sudo install -d -o www-data -g www-data -m 0755 /opt/python-file-manager/uploads
sudo install -d -o www-data -g www-data -m 0755 /opt/python-file-manager/logs
sudo install -d -o www-data -g www-data -m 0755 /opt/python-file-manager/venvs
sudo install -d -o www-data -g www-data -m 0755 /opt/python-file-manager/data
sudo touch /opt/python-file-manager/data/app.db
sudo chown www-data:www-data /opt/python-file-manager/data/app.db
```

如果你希望让服务首次启动时自动创建这些目录，也可以直接把整个项目目录的拥有者改成 `www-data`，但那通常不适合开发环境和部署环境混用。

### 11.5 安装环境变量文件

服务文件中已经声明：

```text
EnvironmentFile=-/etc/pyrunner/pyrunner.env
```

可以按下面步骤安装：

```bash
sudo install -d -o root -g www-data -m 0750 /etc/pyrunner
sudo install -o root -g www-data -m 0640 deploy/systemd/pyrunner.env.example /etc/pyrunner/pyrunner.env
sudo editor /etc/pyrunner/pyrunner.env
```

模板内容如下：

```dotenv
# 使用下面命令生成：python3 -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=replace-with-a-random-secret
ADMIN_USERNAME=admin
ADMIN_PASSWORD=ChangeMe_123456
PYRUNNER_HOST=127.0.0.1
PYRUNNER_PORT=5000
MAX_UPLOAD_SIZE=10485760
# 每个脚本日志文件 5 MB
LOG_MAX_BYTES=5242880
# 每个脚本日志保留 3 个轮转备份
LOG_BACKUP_COUNT=3
```

建议至少修改以下项：

- `SECRET_KEY`：改成随机高强度字符串，可直接使用上面的命令生成
- `ADMIN_PASSWORD`：改成生产密码
- `PYRUNNER_PORT`：如果要配合 Nginx 以外的代理，可按需调整

### 11.6 安装并启动 systemd 服务

```bash
sudo cp deploy/systemd/pyrunner.service /etc/systemd/system/pyrunner.service
sudo systemctl daemon-reload
sudo systemctl enable --now pyrunner
sudo systemctl status pyrunner
```

查看运行日志：

```bash
sudo journalctl -u pyrunner -f
```

### 11.7 安装 Nginx 反向代理

```bash
sudo cp deploy/nginx/pyrunner.conf /etc/nginx/sites-available/pyrunner.conf
sudo ln -sf /etc/nginx/sites-available/pyrunner.conf /etc/nginx/sites-enabled/pyrunner.conf
sudo nginx -t
sudo systemctl reload nginx
```

默认反向代理链路如下：

- Nginx 对外监听 `80`
- Gunicorn 监听 `127.0.0.1:5000`
- Flask 应用由 `app:app` 提供

如果你要启用 HTTPS，可以在 Nginx 上追加证书配置，Gunicorn 和 Flask 侧无需修改。

### 11.8 首次检查命令

```bash
curl http://127.0.0.1:5000/health
sudo systemctl status pyrunner
sudo journalctl -u pyrunner -n 50 --no-pager
sudo nginx -t
```

### 11.8 更新流程

```bash
cd /opt/python-file-manager
git pull
.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart pyrunner
```

如果更新影响了 Nginx 配置，再执行：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

### 11.9 备份建议

建议至少备份以下内容：

- `data/app.db`
- `uploads/`
- `logs/`
- `venvs/`（如果不想在恢复时重新安装依赖）

---

## 12. 依赖安装结果展示策略

当前实现已经针对 pip 安装输出做了前后端协同优化：

- 后端保留完整原始输出，并额外生成摘要、预览、折叠标记和行数元数据
- 前端列表默认只展示摘要和短预览
- 输出较长时，界面使用折叠面板展开完整内容，避免依赖列表被长日志撑满
- 如果完整输出过长，后端会对展开内容再做一次上限截断，避免页面一次性渲染过大的文本块
