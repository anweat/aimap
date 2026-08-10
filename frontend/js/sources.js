/* ===== 数据源面板:来源状态/开关/采集(异步进度)/日志/编辑/探测 ===== */
export class SourcePanel {
  constructor(container, callbacks) {
    this.container = container;
    this.callbacks = callbacks || {};
    this.sources = [];
    this.pollers = new Map();   // jobId -> timer
  }

  async load() {
    try {
      const r = await fetch("/api/sources");
      this.sources = (await r.json()).sources || [];
      this.render();
    } catch (e) {
      this.container.innerHTML = `<div class="loading-hint">数据源加载失败: ${e.message}</div>`;
    }
  }

  render() {
    const el = this.container;
    el.innerHTML = "";
    if (!this.sources.length) {
      el.innerHTML = `<div class="loading-hint">暂无数据源,点"+ 添加"创建</div>`;
      return;
    }
    for (const src of this.sources) {
      const stats = src.last_crawl_stats || {};
      const isLib = src.source_type === "library";
      const sess = src.session || {};
      const sessText = isLib
        ? sess.has
          ? sess.expired ? "🔐 已过期" : "🔐 已登录"
          : "🔐 未登录"
        : "";
      const sessCls = sess.has && !sess.expired ? "src-sess-ok" : "src-sess-off";
      const row = document.createElement("div");
      row.className = "source-row";
      row.dataset.name = src.name;
      row.innerHTML = `
        <div class="src-head">
          <span class="src-light ${src.enabled ? "ok" : "off"}" title="${src.enabled ? "已启用" : "已禁用"}"></span>
          <span class="src-name">${escapeHtml(src.display_name || src.name)}</span>
          <span class="src-type">${isLib ? "🔐 需登录" : "🌐 公开"}${sessText ? ` <span class="${sessCls}" title="登录会话: ${sess.expires_at || ""}">${sessText}</span>` : ""}</span>
          <span class="src-actions">
            ${isLib ? `<button class="mini-btn" data-act="login" title="浏览器登录(复用 playwright)">🔑</button>` : ""}
            <button class="mini-btn" data-act="probe" title="自动探测并给出适配建议">🔍</button>
            <button class="mini-btn" data-act="edit" title="编辑配置">✏️</button>
            <button class="mini-btn" data-act="delete" title="删除来源">🗑</button>
          </span>
        </div>
        <div class="src-crawl">
          <input type="text" placeholder="检索式,如: large language model" />
          <button class="mini-btn" data-act="crawl" ${src.enabled ? "" : "disabled"}>采集</button>
        </div>
        <div class="progress-track"><div style="width:0%"></div></div>
        <div class="src-log"></div>
        <div class="src-stats">
          ${stats.saved !== undefined ? `上次采集: 入库 ${stats.saved} · 重复 ${stats.duplicates || 0} · 失败 ${stats.failed || 0}` : "尚未采集"}
          ${src.last_crawl_at ? ` · ${(src.last_crawl_at || "").slice(0, 16).replace("T", " ")}` : ""}
        </div>`;
      el.appendChild(row);
    }
    this._bind();
  }

  _bind() {
    this.container.querySelectorAll("[data-act]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const row = btn.closest(".source-row");
        const name = row.dataset.name;
        const act = btn.dataset.act;
        if (act === "crawl") this._crawl(row, name);
        else if (act === "probe") this._probe(name);
        else if (act === "login") this._login(name);
        else if (act === "edit") this.callbacks.onEdit(name);
        else if (act === "delete") this._delete(name);
      });
    });
  }

  // ---- 图书馆登录(复用 playwright,弹出浏览器)----
  async _login(name) {
    const row = this.container.querySelector(`.source-row[data-name="${name}"]`);
    const logBox = row.querySelector(".src-log");
    const btn = row.querySelector('[data-act="login"]');
    btn.disabled = true;
    btn.textContent = "…";
    logBox.innerHTML = "";
    this._appendLog(logBox, "🔑 正在弹出浏览器(playwright)…请在浏览器中完成登录");
    try {
      const r = await fetch(`/api/sources/${name}/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ timeout: 600 }),
      });
      const body = await r.json().catch(() => ({}));
      logBox.innerHTML = "";
      if (!r.ok) {
        this._appendLog(logBox, `登录失败: ${body.detail || r.status}`, "err");
        if (body.diagnostics) {
          this._appendLog(logBox, `当前 URL: ${body.diagnostics.url || "?"}`, "err");
          this._appendLog(logBox, `Cookie: ${body.diagnostics.total_cookies || 0} 个(会话类 ${body.diagnostics.auth_cookies || 0})`, "err");
          this._appendLog(logBox, "建议:确认登录页已完成,或检查账号配置", "err");
        }
        return;
      }
      this._appendLog(logBox, `✓ 登录成功!会话有效期至 ${(body.expires_at || "").slice(0, 16).replace("T", " ")}`);
      this._appendLog(logBox, body.verified ? "✓ 会话校验通过" : "⚠ 会话校验未通过(见上方警告)");
    } catch (e) {
      this._appendLog(logBox, `请求异常: ${e.message}`, "err");
    } finally {
      btn.disabled = false;
      btn.textContent = "🔑";
      this.load();  // 刷新会话状态
    }
  }

  // ---- 采集(异步 + 轮询进度)----
  async _crawl(row, name) {
    const input = row.querySelector(".src-crawl input");
    const query = input.value.trim();
    if (!query) return;
    const btn = row.querySelector('[data-act="crawl"]');
    btn.disabled = true;
    btn.textContent = "采集中";
    const logBox = row.querySelector(".src-log");
    logBox.innerHTML = "";
    try {
      const r = await fetch("/api/crawl/arxiv", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, max_results: 20, analyze: true }),
      });
      const job = await r.json();
      if (job.status === "failed") {
        this._appendLog(logBox, `启动失败: ${job.last_error}`, "err");
        btn.disabled = false;
        btn.textContent = "采集";
        return;
      }
      this._poll(row, job.id);
    } catch (e) {
      this._appendLog(logBox, `请求失败: ${e.message}`, "err");
      btn.disabled = false;
      btn.textContent = "采集";
    }
  }

  _poll(row, jobId) {
    const btn = row.querySelector('[data-act="crawl"]');
    const progress = row.querySelector(".progress-track > div");
    const logBox = row.querySelector(".src-log");
    const timer = setInterval(async () => {
      try {
        const r = await fetch(`/api/crawl/jobs/${jobId}`);
        const job = await r.json();
        const pct = job.max_pages ? Math.round((job.cursor / job.max_pages) * 100) : 0;
        progress.style.width = `${pct}%`;
        // 日志
        const lr = await fetch(`/api/crawl/jobs/${jobId}/logs?limit=8`);
        const logs = (await lr.json()).logs || [];
        logBox.innerHTML = "";
        for (const l of logs.slice(-6)) {
          this._appendLog(logBox, `${(l.ts || "").slice(11, 19)} ${l.message}`, l.level === "error" ? "err" : "");
        }
        if (job.status === "done" || job.status === "failed" || job.status === "stopped") {
          clearInterval(timer);
          this.pollers.delete(jobId);
          progress.style.width = job.status === "done" ? "100%" : pct;
          btn.disabled = false;
          btn.textContent = "采集";
          this._appendLog(logBox,
            job.status === "done"
              ? `完成 ✓ 入库 ${job.total_saved} · 重复 ${job.total_duplicates} · 失败 ${job.total_failed}`
              : `失败 ✗ ${job.last_error || ""}`, job.status === "done" ? "" : "err");
          if (job.status === "done" && this.callbacks.onCrawlDone) this.callbacks.onCrawlDone(job);
          if (job.status === "failed" && this.callbacks.onCrawlDone) this.callbacks.onCrawlDone(job);
        }
      } catch (e) {
        clearInterval(timer);
        btn.disabled = false;
        btn.textContent = "采集";
      }
    }, 1500);
    this.pollers.set(jobId, timer);
  }

  _appendLog(box, text, cls) {
    const div = document.createElement("div");
    div.className = cls === "err" ? "lg-err" : "";
    div.textContent = text;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }

  // ---- 探测(agent 自动查询 + 适配建议)----
  async _probe(name) {
    const row = this.container.querySelector(`.source-row[data-name="${name}"]`);
    const logBox = row.querySelector(".src-log");
    logBox.innerHTML = "";
    this._appendLog(logBox, "🔍 自动探测中…");
    try {
      const r = await fetch(`/api/sources/${name}/probe`, { method: "POST" });
      const report = await r.json();
      logBox.innerHTML = "";
      const ok = report.reachable;
      this._appendLog(logBox, `${ok ? "✓ 可达" : "✗ 不可达"}${report.details?.ms ? ` (${report.details.ms}ms)` : ""}`, ok ? "" : "err");
      if (report.details?.status) this._appendLog(logBox, `HTTP ${report.details.status}`);
      for (const s of report.suggestions || []) this._appendLog(logBox, `→ ${s}`, "err");
      if (report.last_crawl) {
        this._appendLog(logBox, `上次任务: ${report.last_crawl.status} · 入库 ${report.last_crawl.saved}`);
      }
    } catch (e) {
      this._appendLog(logBox, `探测失败: ${e.message}`, "err");
    }
  }

  async _delete(name) {
    if (!confirm(`删除数据源 ${name}?`)) return;
    await fetch(`/api/sources/${name}`, { method: "DELETE" });
    this.load();
  }

  refresh() { this.load(); }
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
