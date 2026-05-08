const dashboardRoot = document.getElementById("dashboard-root");
const scriptDetailRoot = document.getElementById("script-detail-root");

if (dashboardRoot || scriptDetailRoot) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function setNotice(element, message, isError = false) {
        if (!element) {
            return;
        }

        if (!message) {
            element.hidden = true;
            element.textContent = "";
            element.classList.remove("is-error");
            return;
        }

        element.hidden = false;
        element.textContent = message;
        element.classList.toggle("is-error", isError);
    }

    async function api(path, options = {}) {
        const settings = { ...options };
        settings.headers = new Headers(settings.headers || {});
        settings.headers.set("Accept", "application/json");

        if (settings.body && !(settings.body instanceof FormData) && !settings.headers.has("Content-Type")) {
            settings.headers.set("Content-Type", "application/json");
        }

        const method = (settings.method || "GET").toUpperCase();
        if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
            settings.headers.set("X-CSRFToken", csrfToken);
        }

        const response = await fetch(path, settings);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.error || "请求失败");
        }
        return data;
    }

    function buildEnvironmentOptions(environments, selectedId) {
        return environments
            .map((environment) => {
                const selected = environment.id === selectedId ? "selected" : "";
                const suffix = environment.is_default ? " (default)" : "";
                return `<option value="${environment.id}" ${selected}>${escapeHtml(environment.name + suffix)}</option>`;
            })
            .join("");
    }

    function shouldRenderDependencyPreview(outputSummary, outputPreview) {
        if (!outputPreview) {
            return false;
        }

        if (outputPreview.includes("\n")) {
            return true;
        }

        const normalizedSummary = String(outputSummary || "").replace(/\.\.\.$/, "").trim();
        const normalizedPreview = String(outputPreview || "").replace(/\.\.\.$/, "").trim();
        if (!normalizedPreview) {
            return false;
        }

        return normalizedPreview !== normalizedSummary && !normalizedPreview.startsWith(normalizedSummary);
    }

    function renderDependencies(elements, logs, progress) {
        const totalPackages = Number(progress.total_packages || 0);
        const completedPackages = Number(progress.completed_packages || 0);
        const successfulPackages = Number(progress.successful_packages || 0);
        const failedPackages = Number(progress.failed_packages || 0);
        const percentage = totalPackages > 0 ? Math.min(100, Math.round((completedPackages / totalPackages) * 100)) : 0;

        elements.dependencyStatus.textContent = progress.installing ? "依赖安装进行中" : "最近安装记录";
        elements.dependencyProgressFill.style.width = `${percentage}%`;
        if (totalPackages > 0) {
            elements.dependencyProgressMeta.textContent = `已完成 ${completedPackages}/${totalPackages}，成功 ${successfulPackages}，失败 ${failedPackages}`;
        } else {
            elements.dependencyProgressMeta.textContent = progress.installing ? "依赖任务已启动，等待写入结果" : "暂无依赖任务";
        }

        if (!logs.length) {
            elements.dependencyList.innerHTML = '<div class="empty-state">暂无依赖安装记录</div>';
            return;
        }

        elements.dependencyList.innerHTML = logs.map((item) => {
            const badgeClass = item.success ? "status-running" : "status-error";
            const badgeLabel = item.success ? "success" : "failed";
            const outputSummary = item.output_summary || (item.success ? "安装成功" : "安装失败");
            const outputPreview = item.output_preview || "";
            const outputFull = item.output_full || "";
            const outputCollapsed = Boolean(item.output_collapsed);
            const outputTrimmed = Boolean(item.output_trimmed);
            const outputLineCount = Number(item.output_line_count || 0);
            const showPreview = shouldRenderDependencyPreview(outputSummary, outputPreview);
            const detailsLabel = outputTrimmed
                ? `展开完整输出（已截断，${outputLineCount} 行）`
                : `展开完整输出（${outputLineCount} 行）`;
            return `
                <article class="dependency-item">
                    <div class="dependency-header">
                        <strong>${escapeHtml(item.package_name || "unknown")}</strong>
                        <span class="status-pill ${badgeClass}">${badgeLabel}</span>
                    </div>
                    <small>${escapeHtml(item.installed_at || "")}${item.version ? ` · v${escapeHtml(item.version)}` : ""}</small>
                    <div class="dependency-summary mt-2">${escapeHtml(outputSummary)}</div>
                    ${showPreview ? `<pre class="dependency-output-preview">${escapeHtml(outputPreview)}</pre>` : ""}
                    ${outputCollapsed ? `<details class="dependency-output-details"><summary>${escapeHtml(detailsLabel)}</summary><pre class="dependency-output-full">${escapeHtml(outputFull)}</pre></details>` : ""}
                </article>
            `;
        }).join("");
    }

    function installDependencies(scriptId, setPageNotice) {
        const extras = window.prompt("可输入额外包名，使用逗号分隔；留空则仅自动解析 import", "") || "";
        return api(`/api/scripts/${scriptId}/install-dependencies`, {
            method: "POST",
            body: JSON.stringify({ manual_packages: extras }),
        }).then((data) => {
            const packageText = data.packages?.length ? `：${data.packages.join(", ")}` : "";
            setPageNotice((data.message || "依赖安装任务已启动") + packageText);
        });
    }

    function initDashboardPage() {
        const state = {
            environments: [],
            scripts: [],
            operationPagination: {
                page: 1,
                pageSize: 8,
                total: 0,
                hasPrev: false,
                hasNext: false,
            },
        };

        const elements = {
            notice: document.getElementById("notice-banner"),
            uploadForm: document.getElementById("upload-form"),
            environmentForm: document.getElementById("environment-form"),
            refreshButton: document.getElementById("refresh-button"),
            tableBody: document.getElementById("script-table-body"),
            operationList: document.getElementById("operation-list"),
            operationPrev: document.getElementById("operation-prev"),
            operationNext: document.getElementById("operation-next"),
            operationPageInfo: document.getElementById("operation-page-info"),
        };

        function setPageNotice(message, isError = false) {
            setNotice(elements.notice, message, isError);
        }

        function renderScripts() {
            if (!state.scripts.length) {
                elements.tableBody.innerHTML = '<tr><td colspan="4" class="empty-cell">暂无脚本</td></tr>';
                return;
            }

            elements.tableBody.innerHTML = state.scripts.map((script) => {
                const installingBadge = script.installing ? '<span class="status-pill status-installing">installing</span>' : "";
                const isRunning = script.status === "running";
                return `
                    <tr data-script-id="${script.id}">
                        <td>
                            <div class="script-name">${escapeHtml(script.name)}</div>
                            <div class="script-path">${escapeHtml(script.file_path)}</div>
                        </td>
                        <td>
                            <select class="form-select form-select-sm environment-select" data-action="change-environment" data-script-id="${script.id}">
                                ${buildEnvironmentOptions(state.environments, script.environment_id)}
                            </select>
                        </td>
                        <td>
                            <span class="status-pill status-${script.status}">${escapeHtml(script.status)}</span>
                            ${installingBadge}
                        </td>
                        <td>
                            <div class="action-cluster">
                                <button class="btn btn-sm btn-outline-success" data-action="start" data-script-id="${script.id}" ${isRunning ? "disabled" : ""}>启动</button>
                                <button class="btn btn-sm btn-outline-secondary" data-action="stop" data-script-id="${script.id}" ${isRunning ? "" : "disabled"}>停止</button>
                                <button class="btn btn-sm btn-outline-dark" data-action="restart" data-script-id="${script.id}">重启</button>
                                <button class="btn btn-sm btn-outline-warning" data-action="install" data-script-id="${script.id}">依赖</button>
                                <a class="btn btn-sm btn-outline-primary" href="/scripts/${script.id}">详情</a>
                                <button class="btn btn-sm btn-outline-danger" data-action="delete" data-script-id="${script.id}">删除</button>
                            </div>
                        </td>
                    </tr>
                `;
            }).join("");
        }

        function renderOperations(logs, pagination) {
            if (!logs.length) {
                elements.operationList.innerHTML = '<div class="empty-state">暂无操作记录</div>';
            } else {
                elements.operationList.innerHTML = logs.map((item) => `
                    <article class="operation-item">
                        <div class="operation-header">
                            <strong>${escapeHtml(item.action)}</strong>
                            <span class="operation-time">${escapeHtml(item.created_at)}</span>
                        </div>
                        <div>${escapeHtml(item.details || "-")}</div>
                        <div class="operation-time mt-2">${escapeHtml(item.username)} · ${escapeHtml(item.ip_address || "unknown")}</div>
                    </article>
                `).join("");
            }

            const totalPages = Math.max(1, Math.ceil((pagination.total || 0) / (pagination.pageSize || 1)));
            elements.operationPageInfo.textContent = `第 ${pagination.page} / ${totalPages} 页`;
            elements.operationPrev.disabled = !pagination.hasPrev;
            elements.operationNext.disabled = !pagination.hasNext;
        }

        async function loadEnvironments() {
            const data = await api("/api/environments");
            state.environments = data.environments || [];
        }

        async function loadScripts() {
            const data = await api("/api/scripts");
            state.scripts = data.scripts || [];
            renderScripts();
        }

        async function loadOperations(page = state.operationPagination.page) {
            const pageSize = state.operationPagination.pageSize;
            const data = await api(`/api/operation-logs?limit=${pageSize}&page=${page}`);
            const pagination = data.pagination || {};
            state.operationPagination = {
                page: Number(pagination.page || page),
                pageSize: Number(pagination.page_size || pageSize),
                total: Number(pagination.total || 0),
                hasPrev: Boolean(pagination.has_prev),
                hasNext: Boolean(pagination.has_next),
            };
            renderOperations(data.logs || [], state.operationPagination);
        }

        async function refreshAll() {
            try {
                await loadEnvironments();
                await Promise.all([loadScripts(), loadOperations(state.operationPagination.page)]);
                setPageNotice("");
            } catch (error) {
                setPageNotice(error.message, true);
            }
        }

        elements.uploadForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            const formData = new FormData(elements.uploadForm);
            try {
                const data = await api("/api/scripts/upload", { method: "POST", body: formData });
                elements.uploadForm.reset();
                setPageNotice(data.message || "上传成功");
                await refreshAll();
            } catch (error) {
                setPageNotice(error.message, true);
            }
        });

        elements.environmentForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            const formData = new FormData(elements.environmentForm);
            const payload = { name: formData.get("name") };
            try {
                const data = await api("/api/environments", { method: "POST", body: JSON.stringify(payload) });
                elements.environmentForm.reset();
                setPageNotice(data.message || "环境已创建");
                await refreshAll();
            } catch (error) {
                setPageNotice(error.message, true);
            }
        });

        elements.refreshButton.addEventListener("click", () => {
            void refreshAll();
        });

        elements.tableBody.addEventListener("click", async (event) => {
            const target = event.target;
            if (!(target instanceof Element)) {
                return;
            }

            const button = target.closest("button[data-action]");
            if (!button) {
                return;
            }

            event.preventDefault();
            const scriptId = Number(button.dataset.scriptId || 0);
            const action = button.dataset.action;
            if (!scriptId || !action) {
                return;
            }

            try {
                if (action === "start") {
                    const data = await api(`/api/scripts/${scriptId}/start`, { method: "POST", body: JSON.stringify({}) });
                    setPageNotice(data.message || "脚本已启动");
                }
                if (action === "stop") {
                    const data = await api(`/api/scripts/${scriptId}/stop`, { method: "POST", body: JSON.stringify({}) });
                    setPageNotice(data.message || "脚本已停止");
                }
                if (action === "restart") {
                    const data = await api(`/api/scripts/${scriptId}/restart`, { method: "POST", body: JSON.stringify({}) });
                    setPageNotice(data.message || "脚本已重启");
                }
                if (action === "install") {
                    await installDependencies(scriptId, setPageNotice);
                }
                if (action === "delete") {
                    const confirmed = window.confirm("删除脚本会同时移除日志文件，是否继续？");
                    if (!confirmed) {
                        return;
                    }
                    const data = await api(`/api/scripts/${scriptId}`, { method: "DELETE" });
                    setPageNotice(data.message || "脚本已删除");
                }

                await refreshAll();
            } catch (error) {
                setPageNotice(error.message, true);
            }
        });

        elements.tableBody.addEventListener("change", async (event) => {
            const target = event.target;
            if (!(target instanceof HTMLSelectElement) || target.dataset.action !== "change-environment") {
                return;
            }

            const scriptId = Number(target.dataset.scriptId || 0);
            const environmentId = Number(target.value);

            try {
                const data = await api(`/api/scripts/${scriptId}/environment`, {
                    method: "PATCH",
                    body: JSON.stringify({ environment_id: environmentId }),
                });
                setPageNotice(data.message || "环境已更新");
                await refreshAll();
            } catch (error) {
                setPageNotice(error.message, true);
                await refreshAll();
            }
        });

        elements.operationPrev.addEventListener("click", () => {
            if (!state.operationPagination.hasPrev) {
                return;
            }
            void loadOperations(state.operationPagination.page - 1).catch((error) => setPageNotice(error.message, true));
        });

        elements.operationNext.addEventListener("click", () => {
            if (!state.operationPagination.hasNext) {
                return;
            }
            void loadOperations(state.operationPagination.page + 1).catch((error) => setPageNotice(error.message, true));
        });

        void refreshAll();

        window.setInterval(() => {
            void loadScripts().catch((error) => setPageNotice(error.message, true));
            void loadOperations(state.operationPagination.page).catch((error) => setPageNotice(error.message, true));
        }, 4000);
    }

    function initScriptDetailPage(root) {
        const initialScriptId = Number(root.dataset.scriptId || 0);
        if (!initialScriptId) {
            return;
        }

        const state = {
            scriptId: initialScriptId,
            environments: [],
            scripts: [],
            logCursor: 0,
        };

        const elements = {
            notice: document.getElementById("notice-banner"),
            name: document.getElementById("detail-script-name"),
            meta: document.getElementById("detail-script-meta"),
            statusBadge: document.getElementById("detail-script-status"),
            installingBadge: document.getElementById("detail-script-installing"),
            environmentSelect: document.getElementById("detail-environment-select"),
            startButton: document.getElementById("detail-start-button"),
            stopButton: document.getElementById("detail-stop-button"),
            restartButton: document.getElementById("detail-restart-button"),
            installButton: document.getElementById("detail-install-button"),
            deleteButton: document.getElementById("detail-delete-button"),
            dependencyStatus: document.getElementById("dependency-status"),
            dependencyProgressFill: document.getElementById("dependency-progress-fill"),
            dependencyProgressMeta: document.getElementById("dependency-progress-meta"),
            dependencyList: document.getElementById("dependency-list"),
            logOutput: document.getElementById("log-output"),
        };

        function setPageNotice(message, isError = false) {
            setNotice(elements.notice, message, isError);
        }

        function currentScript() {
            return state.scripts.find((item) => item.id === state.scriptId) || null;
        }

        function renderMissingScript() {
            elements.name.textContent = "脚本不存在";
            elements.meta.textContent = "该脚本可能已被删除";
            elements.statusBadge.className = "status-pill status-error";
            elements.statusBadge.textContent = "missing";
            elements.installingBadge.hidden = true;
            elements.environmentSelect.innerHTML = "";
            elements.environmentSelect.disabled = true;
            elements.startButton.disabled = true;
            elements.stopButton.disabled = true;
            elements.restartButton.disabled = true;
            elements.installButton.disabled = true;
            elements.deleteButton.disabled = true;
            elements.logOutput.textContent = "脚本不存在";
            renderDependencies(elements, [], { installing: false });
            setPageNotice("脚本不存在或已被删除", true);
        }

        function renderScript(resetLog = false) {
            const script = currentScript();
            if (!script) {
                renderMissingScript();
                return false;
            }

            if (resetLog) {
                state.logCursor = 0;
                elements.logOutput.textContent = "日志加载中...";
            }

            elements.name.textContent = script.name;
            elements.meta.textContent = `${script.file_path} · ${script.environment_name || "未分配环境"}`;
            elements.statusBadge.className = `status-pill status-${script.status}`;
            elements.statusBadge.textContent = script.status;
            elements.installingBadge.hidden = !script.installing;
            elements.environmentSelect.innerHTML = buildEnvironmentOptions(state.environments, script.environment_id);
            elements.environmentSelect.disabled = script.status === "running";
            elements.startButton.disabled = script.status === "running";
            elements.stopButton.disabled = script.status !== "running";
            elements.restartButton.disabled = false;
            elements.installButton.disabled = false;
            elements.deleteButton.disabled = false;
            return true;
        }

        async function loadEnvironments() {
            const data = await api("/api/environments");
            state.environments = data.environments || [];
        }

        async function loadCurrentScript() {
            const data = await api("/api/scripts");
            state.scripts = data.scripts || [];
            return currentScript();
        }

        async function loadLogs({ reset = false } = {}) {
            const script = currentScript();
            if (!script) {
                return;
            }

            const requestedScriptId = script.id;
            const cursor = reset ? 0 : state.logCursor;
            const shouldStick = Math.abs(elements.logOutput.scrollHeight - elements.logOutput.scrollTop - elements.logOutput.clientHeight) < 32;
            const data = await api(`/api/logs/${script.id}?tail=400&after=${cursor}`);

            if (currentScript()?.id !== requestedScriptId) {
                return;
            }

            const incomingLines = data.lines || [];
            if (reset || data.truncated || cursor === 0) {
                elements.logOutput.textContent = incomingLines.join("\n") || "暂无日志";
            } else if (incomingLines.length) {
                const prefix = elements.logOutput.textContent && elements.logOutput.textContent !== "暂无日志" ? "\n" : "";
                elements.logOutput.textContent += `${prefix}${incomingLines.join("\n")}`;
            }

            state.logCursor = Number(data.cursor || 0);
            if (shouldStick || reset || data.truncated) {
                elements.logOutput.scrollTop = elements.logOutput.scrollHeight;
            }
        }

        async function loadDependencies() {
            const script = currentScript();
            if (!script) {
                return;
            }

            const requestedScriptId = script.id;
            const data = await api(`/api/scripts/${script.id}/dependencies`);
            if (currentScript()?.id !== requestedScriptId) {
                return;
            }

            renderDependencies(elements, data.logs || [], data.progress || { installing: Boolean(data.installing) });
        }

        async function refreshAll({ resetLogs = false } = {}) {
            try {
                await Promise.all([loadEnvironments(), loadCurrentScript()]);
                if (!renderScript(resetLogs)) {
                    return;
                }

                await Promise.all([
                    loadDependencies(),
                    loadLogs({ reset: resetLogs }),
                ]);
                setPageNotice("");
            } catch (error) {
                setPageNotice(error.message, true);
            }
        }

        async function handleAction(action) {
            try {
                if (action === "start") {
                    const data = await api(`/api/scripts/${state.scriptId}/start`, { method: "POST", body: JSON.stringify({}) });
                    setPageNotice(data.message || "脚本已启动");
                }
                if (action === "stop") {
                    const data = await api(`/api/scripts/${state.scriptId}/stop`, { method: "POST", body: JSON.stringify({}) });
                    setPageNotice(data.message || "脚本已停止");
                }
                if (action === "restart") {
                    const data = await api(`/api/scripts/${state.scriptId}/restart`, { method: "POST", body: JSON.stringify({}) });
                    setPageNotice(data.message || "脚本已重启");
                }
                if (action === "install") {
                    await installDependencies(state.scriptId, setPageNotice);
                }
                if (action === "delete") {
                    const confirmed = window.confirm("删除脚本会同时移除日志文件，是否继续？");
                    if (!confirmed) {
                        return;
                    }
                    const data = await api(`/api/scripts/${state.scriptId}`, { method: "DELETE" });
                    setPageNotice(data.message || "脚本已删除");
                    window.location.href = "/";
                    return;
                }

                await refreshAll({ resetLogs: action === "start" || action === "restart" });
            } catch (error) {
                setPageNotice(error.message, true);
            }
        }

        elements.startButton.addEventListener("click", () => {
            void handleAction("start");
        });

        elements.stopButton.addEventListener("click", () => {
            void handleAction("stop");
        });

        elements.restartButton.addEventListener("click", () => {
            void handleAction("restart");
        });

        elements.installButton.addEventListener("click", () => {
            void handleAction("install");
        });

        elements.deleteButton.addEventListener("click", () => {
            void handleAction("delete");
        });

        elements.environmentSelect.addEventListener("change", async (event) => {
            const target = event.target;
            if (!(target instanceof HTMLSelectElement)) {
                return;
            }

            try {
                const data = await api(`/api/scripts/${state.scriptId}/environment`, {
                    method: "PATCH",
                    body: JSON.stringify({ environment_id: Number(target.value) }),
                });
                setPageNotice(data.message || "环境已更新");
                await refreshAll();
            } catch (error) {
                setPageNotice(error.message, true);
                await refreshAll();
            }
        });

        void refreshAll({ resetLogs: true });

        window.setInterval(() => {
            void refreshAll().catch((error) => setPageNotice(error.message, true));
        }, 4000);
    }

    if (dashboardRoot) {
        initDashboardPage();
    }

    if (scriptDetailRoot) {
        initScriptDetailPage(scriptDetailRoot);
    }
}