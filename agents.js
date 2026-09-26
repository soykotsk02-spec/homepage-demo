"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    authenticated: false, healthy: false, starting: false, refreshing: false,
    worker: null, activeJob: null, history: [], timer: null, epoch: 0,
    reportSignature: null, pendingJobId: null, pendingAccepted: false,
    retryRequest: null, connectionIssue: false, startingMode: null, pendingMode: null,
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
    $("action-feedback").textContent = message;
    $("action-feedback").dataset.tone = tone;
    $("action-feedback").hidden = !message || !state.authenticated;
  }

  function buttonFeedback(id, label, busy = false, caption) {
    const button = $(id);
    button.querySelector("[data-button-label]").textContent = label;
    button.setAttribute("aria-busy", String(busy));
    if (caption !== undefined) button.querySelector(".button-caption").textContent = caption;
  }

  function checkingFeedback(busy) {
    $("refresh-button").disabled = busy;
    $("retry-button").disabled = busy;
    buttonFeedback("refresh-button", busy ? "正在刷新…" : "刷新状态 ↻", busy);
    buttonFeedback("retry-button", busy ? "正在检查连接…" : "重新检查连接", busy);
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
    state.pendingMode = null;
    state.retryRequest = null;
    $("dashboard").hidden = true;
    for (const id of ["news-list", "local-list", "learning-list", "history-list"]) $(id).replaceChildren();
    for (const id of ["report-title", "report-overview", "report-time", "local-limitations", "source-note", "job-error"]) $(id).textContent = "";
    $("report-content").hidden = true;
    $("report-empty").hidden = false;
    $("job-error").hidden = true;
    $("action-feedback").textContent = "";
    $("action-feedback").hidden = true;
    $("worker-last-seen").textContent = "暂无记录";
    $("updated-time").textContent = "尚未更新";
    $("phase-label").textContent = "暂无正在运行的任务";
    $("progress-detail").textContent = "开始任务后，这里会显示电脑返回的实际进度。";
    $("preview-button").disabled = true;
    $("send-button").disabled = true;
  }

  function showSession(kind, message = "", heading) {
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
    $("session-heading").textContent = heading || (kind === "login" ? "登录 Agent 工作台" : kind === "checking" ? "正在连接 Agent" : "暂时无法连接");
    $("session-badge").textContent = kind === "login" ? "请先登录" : kind === "checking" ? "正在检查连接" : "尚未连接";
    $("session-badge").dataset.state = kind;
    $("retry-button").hidden = !["error", "checking"].includes(kind);
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
    $("availability-note").dataset.state = enabled ? "ready" : isBusy() ? "busy" : "unavailable";
    const mode = state.startingMode || state.activeJob?.mode || state.pendingMode;
    for (const candidate of ["preview", "send"]) {
      let label = candidate === "preview" ? "生成预览" : "启动并发送";
      let caption = candidate === "preview" ? "只看结果 · 不发邮件" : "生成简报并发到邮箱";
      let busy = false;
      if (mode === candidate && state.starting) {
        label = "正在提交…"; caption = "正在等待服务器确认"; busy = true;
      } else if (mode === candidate && (state.pendingAccepted || isActive(state.activeJob))) {
        const queued = state.pendingAccepted || ["queued", "pending"].includes(state.activeJob?.status);
        label = queued ? "已排队 · 等待接收" : "Agent 正在运行";
        caption = queued ? "电脑通常在 30 秒内接收" : (phaseNames[state.activeJob?.phase] || "正在等待电脑返回进度");
        busy = true;
      } else if (state.retryRequest?.mode === candidate) {
        label = "核对上次提交"; caption = "沿用原任务编号，避免重复";
      } else if (!online) caption = "电脑连接后可使用";
      buttonFeedback(candidate === "preview" ? "preview-button" : "send-button", label, busy, caption);
    }
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
        node("span", "freshness-label", ["recent", "fresh"].includes(item.freshness) ? "采集时近 3 天（72 小时内）" : "延伸阅读"));
      const regionLabel = item.region === "domestic" ? "国内" : item.region === "international" ? "国际" : "";
      if (regionLabel) meta.append(node("span", "news-source", regionLabel));
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
      state.pendingMode = null;
      state.pendingJobId = null;
      state.retryRequest = null;
      notice(successStatuses.has(finishedJob.status)
        ? finishedJob.mode === "preview" ? "预览已完成，简报已更新；本次没有发邮件。" : "任务已完成，邮件已发送，简报已更新。"
        : errorLabels[finishedJob.error] || "本次运行未完成，请查看运行记录。",
        successStatuses.has(finishedJob.status) ? "success" : "warning");
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

  async function refreshStatus(manual = false) {
    if (state.refreshing) return;
    state.refreshing = true;
    checkingFeedback(true);
    if (manual && !state.authenticated) {
      showSession("checking", "正在检查服务连接，请稍候。检查连接不会启动任务或发送邮件。");
      notice("正在检查 Agent 的连接状态…");
    }
    const epoch = state.epoch;
    try {
      const { data } = await api("status");
      if (epoch !== state.epoch) return;
      applyStatus(data);
      if (state.connectionIssue) notice("连接已恢复，当前显示最新同步的运行状态。");
      else if (manual) notice(data.worker.online ? "检查完成：电脑已连接，已更新运行状态。" : "检查完成：电脑尚未连接，请在电脑上启动连接程序。", data.worker.online ? "success" : "warning");
      state.connectionIssue = false;
    } catch (error) {
      if (epoch !== state.epoch) return;
      state.healthy = false;
      if (error.status === 401) {
        const wasSignedIn = state.authenticated;
        showSession("login");
        if (wasSignedIn) notice("登录已过期，请重新登录后查看私人内容。", "warning");
        else notice("服务已连接，请登录后使用 Agent。", "success");
      } else if (error.status === 503 && (error.data?.error === "not_configured" || !state.authenticated)) {
        const unconfigured = error.data?.error === "not_configured";
        showSession("error", unconfigured
          ? "网页已就绪，私有连接配置还未完成。完成配置后，才能登录并启动电脑上的 Agent。"
          : "Agent 服务暂时无法响应，请稍后重新检查。", unconfigured ? "等待完成连接配置" : "服务暂时不可用");
        notice(unconfigured ? "检查完成：连接配置尚未完成，Agent 未启动。" : "检查完成：服务暂不可用，尚未确认连接。", "warning");
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
      checkingFeedback(false);
      $("session-checked").textContent = `上次检查：${new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date())} · 北京时间`;
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
    state.startingMode = mode;
    const epoch = state.epoch;
    updateControls();
    notice("正在提交任务，请稍候…");
    try {
      const response = await api("start", "POST", request);
      if (epoch !== state.epoch) return;
      if (response.status !== 202) throw new Error("Task acceptance was not confirmed");
      const job = response.data && (response.data.job || response.data);
      state.pendingAccepted = true;
      state.pendingMode = mode;
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
        if (error.data?.error === "not_configured") {
          state.retryRequest = null;
          notice("执行服务尚未配置完成，暂时不能启动任务。", "warning");
        } else {
          notice("服务暂时不可用，提交结果尚未确认。已保留原请求编号，正在核对状态，请勿重复发送。", "warning");
        }
      } else if (error.status && error.status >= 400 && error.status < 500) {
        state.retryRequest = null;
        notice("服务器未接受这次任务，请刷新状态后重试。", "error");
      } else {
        state.healthy = false;
        notice("提交结果尚未确认，正在核对服务器状态。请勿换用新任务重复发送。", "warning");
      }
    } finally {
      state.starting = false;
      state.startingMode = null;
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
    buttonFeedback("login-button", "正在登录…", true);
    $("login-error").hidden = true;
    try {
      await api("login", "POST", { password });
      state.epoch += 1;
      notice("");
      await refreshStatus();
    } catch (error) {
      if (error.status === 503) showSession("error", error.data?.error === "not_configured"
        ? "工作台后台尚未配置完成，暂时无法登录。配置完成后可重新检查连接。"
        : "工作台服务暂时不可用，登录结果尚未确认，请稍后重新检查连接。");
      else {
        $("login-error").textContent = error.status === 401 ? "密码不正确，请重新输入。" : error.status === 429 ? "尝试次数较多，请稍后再试。" : "登录未能确认，请稍后重试。";
        $("login-error").hidden = false;
        $("password").focus();
      }
    } finally { $("login-button").disabled = false; buttonFeedback("login-button", "进入工作台"); }
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
  $("refresh-button").addEventListener("click", () => refreshStatus(true));
  $("retry-button").addEventListener("click", () => refreshStatus(true));
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopPolling();
    else if (state.authenticated) refreshStatus();
  });
  window.addEventListener("pagehide", stopPolling);
  refreshStatus();
})();

// Public delivery is deliberately independent of the owner's login and data.
(() => {
  const $ = (id) => document.getElementById(id);
  if (!$("public-form")) return;
  const storageKey = "public-brief-request-id";
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const publicState = { requestId: null, stage: "idle", submitting: false, checking: false, timer: null, failures: 0 };
  const phases = {
    queued: ["已提交，等待电脑接收", "任务已记录。电脑接收后会自动开始，请保留此页面查看进度。", -1],
    starting: ["电脑已接收，正在启动", "正在启动公开新闻任务，本次不会读取站主本地资料。", -1],
    collect: ["正在采集公开新闻", "正在读取公开新闻来源，保留原文链接。", 0],
    collecting: ["正在采集公开新闻", "正在读取公开新闻来源，保留原文链接。", 0],
    analyze: ["正在整理新闻", "正在整理公开新闻中的科技动态。", 1],
    analyzing: ["正在整理新闻", "正在整理公开新闻中的科技动态。", 1],
    render: ["正在生成公开简报", "正在把公开新闻整理成可阅读的邮件。", 1],
    rendering: ["正在生成公开简报", "正在把公开新闻整理成可阅读的邮件。", 1],
    send: ["正在发送到你的邮箱", "正在提交邮件，请等待电脑返回实际发送结果。", 2],
    sending: ["正在发送到你的邮箱", "正在提交邮件，请等待电脑返回实际发送结果。", 2],
  };

  function feedback(title, detail, tone = "info", step = -1, done = false) {
    $("public-feedback-title").textContent = title;
    $("public-feedback-detail").textContent = detail;
    $("public-feedback").dataset.tone = tone;
    $("public-progress").hidden = step < 0 && !done;
    $("public-progress").querySelectorAll("li").forEach((item, index) => {
      item.dataset.state = done || index < step ? "done" : index === step ? "current" : "waiting";
    });
  }

  function controls() {
    const busy = publicState.submitting || publicState.checking;
    const canSubmit = ["idle", "rejected", "notfound"].includes(publicState.stage);
    $("public-submit").disabled = busy || !canSubmit;
    $("public-submit").setAttribute("aria-busy", String(publicState.submitting));
    const labels = {
      idle: "发送公开简报到我的邮箱", rejected: "重试本次公开简报", notfound: "继续本次请求",
      active: "Agent 正在运行", unknown: "请先核对本次请求", uncertain: "发送结果待核实",
      completed: "本次公开简报已发送", failed: "本次任务未完成",
    };
    $("public-submit").querySelector("[data-button-label]").textContent = publicState.submitting ? "正在提交，请稍候…" : labels[publicState.stage] || "检查本次请求中…";
    $("public-email").disabled = busy || !canSubmit;
    $("public-consent").disabled = busy || !canSubmit;
    $("public-check").hidden = !publicState.requestId || !["active", "unknown", "uncertain", "notfound"].includes(publicState.stage);
    $("public-check").disabled = busy;
    $("public-check").setAttribute("aria-busy", String(publicState.checking));
    $("public-check").querySelector("[data-button-label]").textContent = publicState.checking ? "正在核对状态…" : "检查本次请求状态";
  }

  function saveRequestId() {
    // Never store the visitor's email, consent, report, or delivery contents.
    try { window.sessionStorage.setItem(storageKey, publicState.requestId); } catch { /* Polling still works in this page. */ }
  }

  function stopPolling() {
    if (publicState.timer) window.clearTimeout(publicState.timer);
    publicState.timer = null;
  }

  function schedulePolling() {
    stopPolling();
    if (!document.hidden && ["active", "unknown"].includes(publicState.stage) && publicState.failures < 3) {
      publicState.timer = window.setTimeout(checkStatus, publicState.failures ? 10000 : 5000);
    }
  }

  async function publicApi(action, body) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(`/api/agent?action=${action}`, {
        method: "POST", credentials: "omit", cache: "no-store", signal: controller.signal,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok || !data || !data.job) {
        const error = new Error("Public request could not be confirmed");
        error.status = response.status;
        error.data = data;
        throw error;
      }
      return data.job;
    } finally { window.clearTimeout(timeout); }
  }

  function showJob(job) {
    publicState.failures = 0;
    if (job.updatedAt) {
      const updated = new Date(job.updatedAt);
      if (Number.isFinite(updated.getTime())) {
        $("public-updated").textContent = `最近进度：${new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(updated)}（北京时间）`;
        $("public-updated").hidden = false;
      }
    }
    if (job.status === "completed" && job.mailSent === true) {
      publicState.stage = "completed";
      feedback("公开简报已发送", "电脑已确认邮件发送成功，请查看你的收件箱；如暂未看到，也可检查垃圾邮件。内容仅包含公开科技新闻。", "success", 2, true);
    } else if (job.status === "completed" || job.status === "uncertain") {
      publicState.stage = "uncertain";
      feedback("任务已结束，发信结果待核实", "尚未收到明确的发信成功确认。请先查看邮箱，再检查本次请求状态；页面不会自动再次发信。", "warning");
    } else if (job.status === "failed") {
      publicState.stage = "failed";
      feedback("本次任务未完成", job.error === "queue_expired" ? "电脑未能及时接收，本次任务已过期，没有开始执行。" : "电脑返回本次运行未完成。请先核对邮箱；页面不会自动再次提交，避免重复发信。", "error");
    } else if (["queued", "running"].includes(job.status)) {
      publicState.stage = "active";
      const phase = phases[job.phase] || (job.status === "queued" ? phases.queued : ["Agent 正在执行", "任务已由电脑接收，等待下一条实际进度。", -1]);
      feedback(phase[0], phase[1], "info", phase[2]);
    } else {
      publicState.stage = "unknown";
      feedback("正在核对执行结果", "暂未收到可确认的运行状态。将继续查询同一请求，不会重新提交任务。", "warning");
    }
    controls();
    schedulePolling();
  }

  async function checkStatus() {
    if (!publicState.requestId || publicState.submitting || publicState.checking) return;
    stopPolling();
    publicState.checking = true;
    controls();
    try {
      showJob(await publicApi("public-status", { requestId: publicState.requestId }));
    } catch (error) {
      if (error.status === 404 && error.data?.error === "not_found") {
        publicState.stage = "notfound";
        feedback("暂未查到本次任务", "任务可能尚未被接受，或记录已过期。可先检查邮箱；如需继续，请使用原邮箱提交，页面会保留同一个请求编号。", "warning");
      } else {
        publicState.failures += 1;
        if (publicState.stage !== "uncertain") publicState.stage = "unknown";
        feedback("暂时无法确认执行状态", publicState.failures < 3 ? "正在重新查询同一请求。网络恢复前不会创建新任务，请勿重复发送。" : "多次查询仍未成功。请稍后点击“检查本次请求状态”；原请求编号已保留，不会自动再次发信。", "warning");
      }
    } finally {
      publicState.checking = false;
      controls();
      schedulePolling();
    }
  }

  function showStartError(error) {
    const code = error.data?.error;
    const retrySeconds = Number(error.data?.retryAfterSeconds);
    const retryWait = Number.isFinite(retrySeconds) && retrySeconds > 0 ? `约 ${Math.max(1, Math.ceil(retrySeconds / 60))} 分钟后` : "稍后";
    const rejected = {
      worker_offline: ["执行电脑暂未在线", "本次任务没有开始。请等站主电脑恢复连接后，再点击重试。"],
      job_active: ["Agent 正在忙", "电脑正在执行另一项任务，本次尚未开始。请稍后重试。"],
      public_email_limit: ["这个邮箱暂时达到体验次数限制", `请${retryWait}再试。本次请求不会自动重发。`],
      public_ip_limit: ["当前网络请求较多", `请${retryWait}再试。本次请求不会自动重发。`],
      public_daily_limit: ["今天的公开体验额度已用完", `请${retryWait}再来体验。本次没有新增任务。`],
      public_rate_limited: ["公开体验请求较多", `请${retryWait}再试。本次请求不会自动重发。`],
      not_configured: ["公开体验还在准备中", "服务尚未完成连接配置，目前无法接收任务。请稍后再来。"],
      invalid_email: ["请检查邮箱地址", "请输入你自己的完整邮箱地址，再提交本次请求。"],
      invalid_request: ["本次请求未被接受", "请检查邮箱后重试；页面会继续使用同一个请求编号。"],
      origin_mismatch: ["请求来源未通过验证", "请从本站正式页面提交；本次请求没有被接受。"],
    };
    if (rejected[code]) {
      publicState.stage = "rejected";
      feedback(rejected[code][0], rejected[code][1], "warning");
    } else if (["request_conflict", "request_already_used"].includes(code)) {
      publicState.stage = "unknown";
      feedback("本次请求已有记录", "请先检查本次请求状态，并核对原来填写的邮箱。页面不会换用新编号再次发信。", "warning");
    } else {
      publicState.stage = "unknown";
      feedback("提交结果尚未确认", "正在查询同一请求的结果。原请求编号已保留，页面不会自动重复提交。", "warning");
    }
  }

  $("public-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if ($("public-submit").disabled || !$("public-form").reportValidity()) return;
    const email = $("public-email").value.trim();
    $("public-email").value = email;
    if (!publicState.requestId) {
      if (!window.crypto || typeof window.crypto.randomUUID !== "function") {
        feedback("浏览器暂不支持安全提交", "请使用新版浏览器打开本站，以生成可防止重复发信的请求编号。", "error");
        return;
      }
      publicState.requestId = window.crypto.randomUUID();
      saveRequestId();
    }
    stopPolling();
    publicState.submitting = true;
    publicState.failures = 0;
    controls();
    feedback("正在提交本次任务", "请稍候，正在确认服务器是否接受本次请求。此时不会显示为发送成功。");
    try {
      showJob(await publicApi("public-start", { requestId: publicState.requestId, email }));
    } catch (error) {
      showStartError(error);
    } finally {
      publicState.submitting = false;
      controls();
      if (publicState.stage === "unknown") await checkStatus();
      else schedulePolling();
    }
  });

  $("public-check").addEventListener("click", () => { publicState.failures = 0; checkStatus(); });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopPolling();
    else if (["active", "unknown"].includes(publicState.stage)) checkStatus();
  });
  window.addEventListener("pagehide", stopPolling);
  try {
    const saved = window.sessionStorage.getItem(storageKey);
    if (saved && uuidPattern.test(saved)) publicState.requestId = saved;
  } catch { /* Storage is optional; no email is saved. */ }
  if (publicState.requestId) {
    publicState.stage = "unknown";
    feedback("正在恢复上次请求", "只查询上次请求的进度，不会重新发送。邮箱地址没有保存在浏览器存储中。");
    controls();
    checkStatus();
  } else controls();
})();
