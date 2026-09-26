"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    authenticated: false, healthy: false, starting: false, refreshing: false,
    worker: null, activeJob: null, history: [], timer: null, epoch: 0,
    reportSignature: null, pendingJobId: null, pendingAccepted: false,
    retryRequest: null, connectionIssue: false,
  };
  const finalStatuses = new Set(["completed", "complete", "succeeded", "success", "sent", "preview_ready", "failed", "error", "cancelled", "canceled", "uncertain"]);
  const successStatuses = new Set(["completed", "complete", "succeeded", "success", "sent", "preview_ready"]);
  const errorLabels = {
    agent_busy: "电脑正在执行另一项任务，请稍后重试。",
    agent_failed: "电脑上的任务未完成，请查看本地运行记录。",
    agent_start_failed: "电脑未能启动程序，请检查连接程序。",
    report_unavailable: "任务结果暂时无法读取，需在电脑上检查。",
    queue_expired: "等待电脑接收超时，本次没有执行。",
    worker_interrupted_check_local: "执行期间连接程序中断，请先核对电脑上的结果。",
    delivery_unknown_check_local: "发信结果尚未确认，请先核对邮箱，避免重复发送。",
    run_failed: "本次运行未完成，请查看电脑上的记录。",
  };
  const phaseNames = {
    queued: "已排队，等待电脑接收", pending: "等待电脑接收", starting: "正在启动", start: "正在启动",
    collect: "正在采集新闻", collecting: "正在采集新闻", "local-read": "正在读取本地资料",
    local_read: "正在读取本地资料", local: "正在读取本地资料", analyze: "正在分析新闻与资料",
    analyzing: "正在分析新闻与资料", render: "正在生成简报", rendering: "正在生成简报",
    send: "正在发送邮件", sending: "正在发送邮件", complete: "执行完成", completed: "执行完成",
    failed: "本次运行失败", error: "本次运行失败", uncertain: "发送结果待核实", idle: "等待新任务",
  };
  const phaseIndex = { collect: 0, collecting: 0, "local-read": 1, local_read: 1, local: 1, analyze: 2, analyzing: 2, render: 3, rendering: 3, send: 4, sending: 4 };

  function node(tag, className, value) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined && value !== null) element.textContent = String(value);
    return element;
  }

  function text(value, fallback = "") {
    return typeof value === "string" && value.trim() ? value : fallback;
  }

  function dateLabel(value, fallback = "暂无记录") {
    if (!value) return fallback;
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return fallback;
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(date);
  }

  function safeSourceUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" && url.hostname && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  }

  function notice(message, tone = "info") {
    $("notice").textContent = message;
    $("notice").dataset.tone = tone;
    $("notice").hidden = !message;
  }

  function stopPolling() {
    if (state.timer) window.clearTimeout(state.timer);
    state.timer = null;
  }

  function schedulePolling() {
    stopPolling();
    if (state.authenticated && !document.hidden) {
      state.timer = window.setTimeout(() => refreshStatus(), 15000);
    }
  }

  function clearPrivateContent() {
    state.worker = null;
    state.activeJob = null;
    state.history = [];
    state.reportSignature = null;
    state.pendingJobId = null;
    state.pendingAccepted = false;
    state.retryRequest = null;
    $("dashboard").hidden = true;
    for (const id of ["news-list", "local-list", "learning-list", "history-list"]) $(id).replaceChildren();
    for (const id of ["report-title", "report-overview", "report-time", "local-limitations", "source-note", "job-error"]) $(id).textContent = "";
    $("report-content").hidden = true;
    $("report-empty").hidden = false;
    $("job-error").hidden = true;
    $("worker-last-seen").textContent = "暂无记录";
    $("updated-time").textContent = "尚未更新";
    $("phase-label").textContent = "暂无正在运行的任务";
    $("progress-detail").textContent = "开始任务后，这里会显示电脑返回的实际进度。";
    $("preview-button").disabled = true;
    $("send-button").disabled = true;
  }

  function showSession(kind, message = "") {
    state.epoch += 1;
    state.authenticated = false;
    state.healthy = false;
    stopPolling();
    clearPrivateContent();
    $("session-panel").hidden = false;
    $("logout-button").hidden = true;
    $("login-form").hidden = kind !== "login";
    $("session-state").hidden = kind === "login";
    $("session-state").textContent = message;
    $("retry-button").hidden = kind !== "error";
    $("password").value = "";
    $("login-error").textContent = "";
    $("login-error").hidden = true;
  }

  async function api(action, method = "GET", body) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    try {
      const options = { method, credentials: "same-origin", cache: "no-store", signal: controller.signal };
      if (method === "POST") {
        options.headers = { "Content-Type": "application/json" };
        options.body = JSON.stringify(body || {});
      }
      const response = await fetch(`/api/agent?action=${encodeURIComponent(action)}`, options);
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const error = new Error("API request failed");
        error.status = response.status;
        error.data = data;
        throw error;
      }
      return { data, status: response.status };
    } finally { window.clearTimeout(timeout); }
  }

  function isActive(job) {
    return Boolean(job && !finalStatuses.has(job.status));
  }

  function isBusy() {
    return state.starting || state.pendingAccepted || isActive(state.activeJob) || Boolean(state.worker && state.worker.activeJobId);
  }

  function updateControls() {
    const online = state.worker && state.worker.online === true;
    const enabled = state.authenticated && state.healthy && online && !isBusy();
    $("preview-button").disabled = !enabled || Boolean(state.retryRequest && state.retryRequest.mode !== "preview");
    $("send-button").disabled = !enabled || Boolean(state.retryRequest && state.retryRequest.mode !== "send");
    let message = "电脑在线，可以开始。提交后通常会在 30 秒内由电脑接收。";
    if (!state.healthy) message = "连接暂未确认，启动按钮已暂停。请刷新状态后再试。";
    else if (state.starting || state.pendingAccepted) message = "正在等待服务器和电脑同步这次任务，请勿重复提交。";
    else if (isBusy()) message = "已有任务正在排队或执行，完成后可以开始下一次。";
    else if (!online) message = "电脑离线：请保持电脑开机、联网，并启动连接程序。";
    else if (state.retryRequest) message = "上次提交结果待确认。再次点击同一按钮会复用原请求编号。";
    $("availability-note").textContent = message;
  }

  function renderWorker(worker) {
    const online = worker.online === true;
    $("worker-status").dataset.state = online ? "online" : "offline";
    $("worker-label").textContent = online ? "电脑在线" : "电脑离线";
    $("worker-heading").textContent = online ? "已连接执行电脑" : "等待电脑重新连接";
    $("worker-description").textContent = online
      ? "任务会交给这台电脑处理。本地资料读取、分析与发信都在电脑执行。"
      : "最近没有收到电脑的连接消息。电脑恢复连接后，才能从这里启动任务。";
    $("worker-last-seen").textContent = dateLabel(worker.lastSeenAt);
    $("updated-time").textContent = dateLabel(new Date().toISOString());
  }

  function renderProgress(job, worker) {
    const effectiveJob = job || (worker.activeJobId ? { id: worker.activeJobId, phase: worker.phase, status: "running" } : null);
    const status = effectiveJob ? text(effectiveJob.status) : "";
    const phase = effectiveJob ? text(effectiveJob.phase) : "";
    const successful = successStatuses.has(status);
    const failed = ["failed", "error", "uncertain", "cancelled", "canceled"].includes(status);
    const preview = effectiveJob && effectiveJob.mode === "preview";
    let label = "暂无正在运行的任务";
    if (effectiveJob) {
      label = successful ? (preview ? "预览完成 · 未发邮件" : "本次任务已完成")
        : failed ? (status === "uncertain" ? "结果待核实，勿重复发送" : "本次任务未完成")
          : phaseNames[phase] || phaseNames[status] || "任务已接收，等待进度";
    }
    $("phase-label").textContent = label;
    const index = Object.hasOwn(phaseIndex, phase) ? phaseIndex[phase] : -1;
    document.querySelectorAll("#progress-list li").forEach((step, position) => {
      step.removeAttribute("aria-current");
      let stepState = "waiting";
      if (preview && position === 4) stepState = "skipped";
      else if (successful || (index >= 0 && position < index)) stepState = "done";
      else if (effectiveJob && !failed && position === index) stepState = "current";
      step.dataset.state = stepState;
      if (stepState === "current") step.setAttribute("aria-current", "step");
      step.querySelector(".step-dot").textContent = stepState === "done" ? "✓" : String(position + 1);
    });
    $("progress-detail").textContent = effectiveJob
      ? `${effectiveJob.mode === "send" ? "运行并发送" : preview ? "生成预览（不发信）" : "电脑任务"} · ${dateLabel(effectiveJob.createdAt, "启动时间待同步")} · 进度以电脑回传为准。`
      : "开始任务后，这里会显示电脑返回的实际进度。";
    const error = effectiveJob && typeof effectiveJob.error === "string" ? effectiveJob.error : "";
    $("job-error").textContent = error ? (errorLabels[error] || "本次运行需要在电脑上检查。") : (failed ? "请查看执行电脑上的运行记录。发送结果不确定时，请先核对邮箱。" : "");
    $("job-error").hidden = !failed && !error;
  }

  function renderReport(report) {
    const signature = JSON.stringify(report || null);
    if (state.reportSignature === signature) return;
    state.reportSignature = signature;
    const usable = report && Array.isArray(report.items) && report.items.length > 0;
    $("report-empty").hidden = Boolean(usable);
    $("report-content").hidden = !usable;
    $("report-time").textContent = usable ? `${dateLabel(report.generatedAtUtc || report.generatedAt, "时间待同步")} · 北京时间` : "";
    if (!usable) {
      $("news-list").replaceChildren();
      $("local-list").replaceChildren();
      $("learning-list").replaceChildren();
      return;
    }
    $("report-title").textContent = text(report.title, "本次科技简报");
    $("report-overview").textContent = text(report.overview);
    const news = document.createDocumentFragment();
    for (const item of report.items) {
      if (!item || typeof item !== "object") continue;
      const card = node("article", "news-card");
      const meta = node("div", "news-meta");
      meta.append(node("span", "news-source", text(item.source || item.sourceName, "来源待同步")),
        node("time", "", dateLabel(item.publishedAtUtc || item.publishedAt, "发布日期不明")),
        node("span", "freshness-label", item.freshness === "recent" ? "采集时 48 小时内" : "延伸阅读"));
      card.append(meta, node("h3", "", text(item.title, "未命名新闻")), node("p", "news-summary", text(item.summary)));
      if (item.whyItMatters) card.append(node("p", "news-meaning", `为什么值得看：${text(item.whyItMatters)}`));
      const href = safeSourceUrl(item.url);
      if (href) {
        const link = node("a", "news-link", "阅读原文 ↗");
        link.href = href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        card.append(link);
      } else card.append(node("p", "invalid-link", "原文链接暂不可用"));
      news.append(card);
    }
    $("news-list").replaceChildren(news);
    const locals = document.createDocumentFragment();
    const connections = Array.isArray(report.localConnections) ? report.localConnections : [];
    for (const connection of connections) {
      if (!connection || typeof connection !== "object") continue;
      const card = node("article", "local-card");
      card.append(node("h4", "local-news-title", text(connection.newsTitle, "与新闻的联系")),
        node("p", "local-source", `${text(connection.sourceTitle, "资料标题待同步")} · ${text(connection.anchor, "段落位置待同步")}`),
        node("blockquote", "", text(connection.quote, "原句尚未同步")),
        node("p", "local-relationship", text(connection.relationship)));
      const action = node("p", "local-action");
      action.append(node("strong", "", "可以接着做："), document.createTextNode(text(connection.nextStep, "本次暂无具体行动建议。")));
      card.append(action);
      locals.append(card);
    }
    if (connections.length === 0) locals.append(node("p", "local-no-match", "本次没有可展示的可靠资料关联。具体范围与原因以下方说明为准。"));
    $("local-list").replaceChildren(locals);
    $("local-limitations").textContent = text(report.localContext, "本次资料范围与限制尚未同步。") ;
    const suggestions = Array.isArray(report.learningSuggestions) ? report.learningSuggestions.filter((value) => typeof value === "string") : [];
    $("learning-list").replaceChildren(...suggestions.map((value) => node("li", "", value)));
    $("learning-panel").hidden = suggestions.length === 0;
    $("source-note").textContent = text(report.sourceNote);
  }

  function renderHistory(history) {
    const fragment = document.createDocumentFragment();
    for (const job of history.slice(0, 20)) {
      if (!job || typeof job !== "object") continue;
      const succeeded = successStatuses.has(job.status);
      const failed = ["failed", "error", "uncertain", "cancelled", "canceled"].includes(job.status);
      const row = node("li", "history-row");
      const info = node("div", "history-info");
      info.append(node("p", "history-title", job.mode === "send" ? "运行并发送" : job.mode === "preview" ? "生成预览 · 不发信" : "电脑任务"),
        node("p", "history-detail", `${dateLabel(job.createdAt)}${job.updatedAt ? ` · 最近更新 ${dateLabel(job.updatedAt)}` : ""}`));
      if (typeof job.error === "string" && job.error) info.append(node("p", "history-error", errorLabels[job.error] || "本次运行需要在电脑上检查。"));
      const label = succeeded ? (job.mode === "preview" ? "预览完成" : "已完成")
        : job.status === "uncertain" ? "待核实" : failed ? "未完成" : ["queued", "pending"].includes(job.status) ? "排队中" : job.status === "running" ? "执行中" : "状态待同步";
      const badge = node("span", "history-badge", label);
      badge.dataset.state = succeeded ? "success" : failed ? "failure" : "active";
      row.append(node("span", "history-symbol", succeeded ? "✓" : failed ? "!" : "◷"), info, badge);
      fragment.append(row);
    }
    $("history-list").replaceChildren(fragment);
    $("history-empty").hidden = $("history-list").childElementCount > 0;
  }

  function applyStatus(data) {
    if (!data || typeof data !== "object" || !data.worker || typeof data.worker !== "object") throw new Error("Invalid status response");
    state.authenticated = true;
    state.healthy = true;
    state.worker = data.worker;
    state.activeJob = data.activeJob || null;
    state.history = Array.isArray(data.history) ? data.history : [];
    const finishedJob = state.pendingJobId && state.history.find((job) => job.id === state.pendingJobId);
    if (finishedJob) {
      state.pendingAccepted = false;
      state.pendingJobId = null;
      state.retryRequest = null;
      notice(successStatuses.has(finishedJob.status)
        ? finishedJob.mode === "preview" ? "预览已完成，简报已更新；本次没有发邮件。" : "任务已完成，邮件已发送，简报已更新。"
        : errorLabels[finishedJob.error] || "本次运行未完成，请查看运行记录。",
        successStatuses.has(finishedJob.status) ? "info" : "warning");
    } else if (state.activeJob) {
      state.pendingAccepted = false;
      state.retryRequest = null;
    }
    $("session-panel").hidden = true;
    $("dashboard").hidden = false;
    $("logout-button").hidden = false;
    const schedule = data.schedule;
    $("schedule-label").textContent = schedule && schedule.time
      ? `每日 ${text(schedule.time)} · ${schedule.timezone === "Asia/Shanghai" ? "北京时间" : text(schedule.timezone, "时区待同步")}`
      : "每日计划尚未同步";
    renderWorker(state.worker);
    renderProgress(state.activeJob, state.worker);
    renderReport(data.latestReport || null);
    renderHistory(state.history);
    updateControls();
  }

  async function refreshStatus() {
    if (state.refreshing) return;
    state.refreshing = true;
    $("refresh-button").disabled = true;
    const epoch = state.epoch;
    try {
      const { data } = await api("status");
      if (epoch !== state.epoch) return;
      applyStatus(data);
      if (state.connectionIssue) notice("连接已恢复，当前显示最新同步的运行状态。");
      state.connectionIssue = false;
    } catch (error) {
      if (epoch !== state.epoch) return;
      state.healthy = false;
      if (error.status === 401) {
        const wasSignedIn = state.authenticated;
        showSession("login");
        if (wasSignedIn) notice("登录已过期，请重新登录后查看私人内容。", "warning");
      } else if (error.status === 503) {
        showSession("error", "工作台后台尚未配置完成，暂时无法登录或启动任务。配置完成后可重新检查连接。");
        notice("服务尚未就绪，当前没有启动任何任务。", "warning");
      } else if (!state.authenticated) {
        showSession("error", "暂时无法连接工作台服务，请稍后重试。尚未读取私人数据或启动任务。");
      } else {
        state.connectionIssue = true;
        $("worker-status").dataset.state = "unknown";
        $("worker-label").textContent = "状态待核实";
        $("worker-heading").textContent = "连接暂未确认";
        notice("暂时无法更新状态，以下为上次同步的内容。启动按钮已暂停，页面会继续尝试连接。", "warning");
        updateControls();
      }
    } finally {
      state.refreshing = false;
      $("refresh-button").disabled = false;
      schedulePolling();
    }
  }

  async function startJob(mode) {
    const button = mode === "send" ? $("send-button") : $("preview-button");
    if (button.disabled || state.starting) return;
    if (!window.crypto || typeof window.crypto.randomUUID !== "function") {
      notice("浏览器暂不支持安全的任务编号，请使用支持 HTTPS 的较新浏览器。", "error");
      return;
    }
    const request = state.retryRequest && state.retryRequest.mode === mode ? state.retryRequest : { requestId: window.crypto.randomUUID(), mode };
    state.retryRequest = request;
    state.starting = true;
    const epoch = state.epoch;
    updateControls();
    notice("正在提交任务，请稍候…");
    try {
      const response = await api("start", "POST", request);
      if (epoch !== state.epoch) return;
      if (response.status !== 202) throw new Error("Task acceptance was not confirmed");
      const job = response.data && (response.data.job || response.data);
      state.pendingAccepted = true;
      state.pendingJobId = job && typeof job.id === "string" ? job.id : null;
      state.retryRequest = null;
      notice(mode === "send" ? "任务已受理，等待电脑接收；完成生成后会真实发信。此时尚未表示邮件已发出。" : "预览任务已受理，等待电脑接收；本次不会发邮件。");
    } catch (error) {
      if (epoch !== state.epoch) return;
      if (error.status === 401) {
        showSession("login");
        notice("请重新登录，核对是否已有任务后再操作。", "warning");
      } else if (error.status === 409) {
        state.retryRequest = null;
        notice("已有任务或此请求已被受理，未创建重复任务。正在核对实际状态。", "warning");
      } else if (error.status === 503) {
        state.healthy = false;
        state.retryRequest = null;
        notice("执行服务尚未配置完成，暂时不能启动任务。", "warning");
      } else if (error.status && error.status >= 400 && error.status < 500) {
        state.retryRequest = null;
        notice("服务器未接受这次任务，请刷新状态后重试。", "error");
      } else {
        state.healthy = false;
        notice("提交结果尚未确认，正在核对服务器状态。请勿换用新任务重复发送。", "warning");
      }
    } finally {
      state.starting = false;
      updateControls();
      if (state.authenticated) await refreshStatus();
    }
  }

  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if ($("login-button").disabled) return;
    const password = $("password").value;
    if (!password) return;
    $("password").value = "";
    $("login-button").disabled = true;
    $("login-error").hidden = true;
    try {
      await api("login", "POST", { password });
      state.epoch += 1;
      notice("");
      await refreshStatus();
    } catch (error) {
      if (error.status === 503) showSession("error", "工作台后台尚未配置完成，暂时无法登录。配置完成后可重新检查连接。");
      else {
        $("login-error").textContent = error.status === 401 ? "密码不正确，请重新输入。" : error.status === 429 ? "尝试次数较多，请稍后再试。" : "登录未能确认，请稍后重试。";
        $("login-error").hidden = false;
        $("password").focus();
      }
    } finally { $("login-button").disabled = false; }
  });

  $("logout-button").addEventListener("click", async () => {
    $("logout-button").disabled = true;
    showSession("login");
    notice("");
    try {
      await api("logout", "POST");
      notice("已退出登录，页面中的私人内容已清除。");
    } catch (error) {
      if (error.status !== 401) {
        notice("页面内容已清除，但退出请求尚未确认。请重新点击退出登录。", "warning");
        $("logout-button").hidden = false;
      }
    } finally { $("logout-button").disabled = false; }
  });

  $("preview-button").addEventListener("click", () => startJob("preview"));
  $("send-button").addEventListener("click", () => startJob("send"));
  $("refresh-button").addEventListener("click", () => refreshStatus());
  $("retry-button").addEventListener("click", () => refreshStatus());
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopPolling();
    else if (state.authenticated) refreshStatus();
  });
  window.addEventListener("pagehide", stopPolling);
  refreshStatus();
})();
