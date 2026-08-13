/* ===== 主应用:API 调用、状态管理、事件绑定、日志与错误反馈(ES Module)===== */
import { QuaternionMap } from "./map.js";
import { DomainTree } from "./tree.js";
import { SourcePanel } from "./sources.js";

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// 日志系统:状态栏 + 可折叠日志面板 + console,初始化分步可见
// ---------------------------------------------------------------------------
const logPanel = $("logPanel");
const logBody = $("logBody");

function log(msg, level = "info") {
  const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const line = document.createElement("div");
  line.className = `log-line log-${level}`;
  line.textContent = `[${time}] ${msg}`;
  logBody.appendChild(line);
  logBody.scrollTop = logBody.scrollHeight;
  // 同步输出到控制台(浏览器 F12 可见)
  console[level === "error" ? "error" : level === "warn" ? "warn" : "log"](msg);
  // 状态栏展示最新信息(错误优先)
  if (level === "error" || level === "warn") $("statusBar").textContent = msg;
}

function setStatus(text) {
  $("statusBar").textContent = text;
  log(text);
}

function showError(msg) {
  const banner = $("errorBanner");
  banner.textContent = `⚠ ${msg}`;
  banner.classList.remove("hidden");
  log(msg, "error");
}

function hideError() {
  $("errorBanner").classList.add("hidden");
}

// 全局兜底:未捕获异常也显示出来,避免"卡在初始化"无反馈
const KNOWN_HARMLESS = ["ResizeObserver loop", "Script error."];
window.addEventListener("error", (e) => {
  if (KNOWN_HARMLESS.some((k) => (e.message || "").includes(k))) return;  // 已知无害警告
  showError(`脚本错误: ${e.message} (${e.filename?.split("/").pop() || "?"}:${e.lineno})`);
});
window.addEventListener("unhandledrejection", (e) => {
  const msg = e.reason?.message || e.reason || "未知";
  if (KNOWN_HARMLESS.some((k) => String(msg).includes(k))) return;
  showError(`异步错误: ${msg}`);
});

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------
const state = {
  tree: [],
  mapData: null,
  papers: [],
  selectedDomain: null,
  crawlCount: 20,
};

// ---------------------------------------------------------------------------
// API(带日志)
// ---------------------------------------------------------------------------
async function api(path, options) {
  log(`API → ${path}`, "debug");
  const resp = await fetch(path, options);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 (${resp.status})`);
  }
  return resp.json();
}

const get = (path) => api(path);
const post = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });

// ---------------------------------------------------------------------------
// 初始化(分步,任一步失败均有明确日志)
// ---------------------------------------------------------------------------
async function loadAll() {
  try {
    // 第 1 步:确认 API 连通性
    setStatus("初始化 1/4: 连接后端 API…");
    const status = await get("/api/status");
    setStatus(
      `初始化 2/4: 数据源 ${status.sources.join(", ")} · LLM ${status.provider.active}` +
        `(${status.provider.model}) · 论文 ${status.papers} 篇`
    );

    // 第 2 步:加载领域树
    setStatus("初始化 2/4: 加载领域树…");
    const treeData = await get("/api/tree");
    state.tree = treeData.nodes;
    if (!state.tree.length) throw new Error("领域树为空,请先初始化数据库");
    log(`领域树加载完成: ${state.tree.length} 个节点`);

    // 第 3 步:加载地图数据与研究方向总览
    setStatus("初始化 3/4: 加载四元数地图…");
    const [mapData, overview] = await Promise.all([
      get("/api/map/nodes"),
      get("/api/overview"),
    ]);
    state.mapData = mapData;
    renderOverview(overview);
    log(`地图数据: ${mapData.domains.length} 领域 / ${mapData.papers.length} 论文点 | AI 新领域 ${overview.ai_domains} 个`);

    // 第 4 步:渲染
    setStatus("初始化 4/4: 渲染视图…");
    tree.render(state.tree);
    map.render(state.mapData);
    renderPaperStats(overview.stats);
    await loadPapers();
    sourcePanel.load();
    hideError();
    setStatus(
      `就绪 ✓ 数据源: ${status.sources.join(", ")} · LLM: ${status.provider.active} · ` +
        `论文 ${status.papers} 篇 · 领域 ${status.domain_nodes} 个`
    );
  } catch (e) {
    showError(`初始化失败: ${e.message} — 请确认后端已启动(python -m uvicorn app.main:app)且浏览器可访问 /api/status`);
    log(`初始化失败堆栈: ${e.stack || e}`, "error");
  }
}

async function loadPapers(domainKey) {
  const q = domainKey ? `?domain=${encodeURIComponent(domainKey)}` : "";
  try {
    const rows = await get(`/api/papers${q}`);
    state.papers = rows;
    $("papersCount").textContent = `${rows.length} 篇`;
    $("papersTitle").textContent = domainKey ? `论文列表 — ${domainKey}` : "论文列表";
    renderPapers(rows);
  } catch (e) {
    showError(`论文列表加载失败: ${e.message}`);
  }
}

function renderPapers(rows) {
  const list = $("papersList");
  list.innerHTML = "";
  if (!rows.length) {
    list.innerHTML = `<div style="color:var(--muted);font-size:12px;padding:10px">暂无论文,试试上方"采集 arXiv"</div>`;
    return;
  }
  for (const p of rows) {
    const row = document.createElement("div");
    row.className = "paper-row";
    row.innerHTML = `
      <span class="pd">${p.domain_name || "未锚定"}</span>
      <span class="pt">${escapeHtml(p.title)}</span>
      <span class="pu">${p.source}${p.published_at ? " · " + p.published_at.slice(0, 10) : ""}</span>
      <span class="arrow">→</span>`;
    row.addEventListener("click", () => openDetail(p.id));
    list.appendChild(row);
  }
}

// ---------------------------------------------------------------------------
// 详情
// ---------------------------------------------------------------------------
async function openDetail(id) {
  try {
    const p = await get(`/api/papers/${id}`);
    const a = p.anchored || {};
    const layers = (p.classifications || [])
      .map(
        (c) => `
        <div class="layer-row">
          <span class="lname">${c.layer}</span>
          <span class="conf-bar"><div style="width:${Math.round(c.confidence * 100)}%"></div></span>
          <span class="lev">${escapeHtml(c.domain_name || c.domain_key)} — ${escapeHtml(c.evidence || "")}</span>
        </div>`
      )
      .join("");
    $("modalBody").innerHTML = `
      <div class="detail">
        <h3>${escapeHtml(p.title)}</h3>
        <div class="meta">
          ${escapeHtml(p.authors || "未知作者")} · ${p.source} ·
          ${p.published_at ? p.published_at.slice(0, 10) : "未知日期"}
        </div>
        <div class="abstract">${escapeHtml(p.abstract || "无摘要")}</div>
        <div>
          <span class="tag">🎯 ${escapeHtml(a.domain_name || "未锚定")}</span>
          <span class="tag">置信度 ${a.confidence ? a.confidence.toFixed(2) : "-"}</span>
          ${p.categories ? `<span class="tag">${escapeHtml(p.categories)}</span>` : ""}
        </div>
        ${(p.tags || []).length ? `
        <div class="tags-line">
          ${p.tags.map((t) => `
            <span class="tag ${t.domain_key ? "tag-domain" : "tag-free"} ${t.source === "category" ? "tag-cat" : ""}" title="来源: ${t.source} · 置信度 ${t.confidence.toFixed(2)}">
              ${t.source === "category" ? "📚" : t.domain_key ? "🏷" : "#"} ${escapeHtml(t.tag)}
            </span>`).join("")}
        </div>` : ""}
        <div class="layers">
          <div style="font-size:12px;color:var(--muted);margin-bottom:4px">多层分类证据链</div>
          ${layers || '<div style="font-size:12px;color:var(--muted)">尚未分析</div>'}
        </div>
        <div class="btn-row">
          ${p.url ? `<a href="${escapeAttr(p.url)}" target="_blank"><button>打开原文 ↗</button></a>` : ""}
          <button class="btn-ghost" onclick="document.getElementById('detailModal').classList.add('hidden')">关闭</button>
        </div>
      </div>`;
    $("detailModal").classList.remove("hidden");
    if (a.domain_key) map.focusDomain(a.domain_key);
    if (a.position && a.position.qw) map.focusPaper(p.id);
  } catch (e) {
    showError(`详情加载失败: ${e.message}`);
  }
}

// ---------------------------------------------------------------------------
// 采集
// ---------------------------------------------------------------------------
async function crawl() {
  const query = $("crawlQuery").value.trim();
  if (!query) return toast("请输入 arXiv 检索式");
  const btn = $("crawlBtn");
  btn.disabled = true;
  btn.textContent = "启动中…";
  try {
    const r = await post("/api/crawl/arxiv", { query, max_results: state.crawlCount, analyze: true });
    if (r.status === "failed") throw new Error(r.last_error || "启动失败");
    log(`采集任务 #${r.id} 已启动(异步执行,进度见左侧数据源面板)`);
    toast(`任务 #${r.id} 采集中…`);
    _pollTopJob(r.id);
  } catch (e) {
    showError(`采集启动失败: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "采集 arXiv";
  }
}

// 顶部采集按钮的轻量进度轮询(完成后刷新)
function _pollTopJob(jobId) {
  let tries = 0;
  const timer = setInterval(async () => {
    tries++;
    try {
      const r = await fetch(`/api/crawl/jobs/${jobId}`);
      const job = await r.json();
      if (job.status === "done") {
        clearInterval(timer);
        toast(`任务 #${jobId} 完成: 入库 ${job.total_saved} · 重复 ${job.total_duplicates}`);
        log(`任务 #${jobId} 完成: 入库 ${job.total_saved} · 重复 ${job.total_duplicates}`);
        await loadAll();
      } else if (job.status === "failed" || tries > 120) {
        clearInterval(timer);
        if (job.status === "failed") {
          showError(`任务 #${jobId} 失败: ${job.last_error}`);
          log(`任务 #${jobId} 失败,5 分钟后可续爬(POST /api/crawl/jobs/${jobId}/resume)`, "warn");
          await loadAll();
        }
      }
    } catch { clearInterval(timer); }
  }, 2000);
}

// ---------------------------------------------------------------------------
// 搜索(跳转)
// ---------------------------------------------------------------------------
async function doSearch() {
  const q = $("searchInput").value.trim();
  if (!q) return;
  try {
    const r = await get(`/api/search?q=${encodeURIComponent(q)}&limit=30`);
    if (!r.hits.length) return toast(`未找到与 "${q}" 相关的论文`);
    const list = $("papersList");
    list.innerHTML = "";
    $("papersTitle").textContent = `搜索结果: "${q}"`;
    $("papersCount").textContent = `${r.hits.length} 篇`;
    for (const h of r.hits) {
      const row = document.createElement("div");
      row.className = "paper-row";
      row.innerHTML = `
        <span class="pd">${escapeHtml(h.domain_name || "未锚定")}</span>
        <span class="pt">${escapeHtml(h.title)}</span>
        <span class="pu">${h.source}</span>
        <span class="arrow">→</span>`;
      row.addEventListener("click", () => openDetail(h.id));
      list.appendChild(row);
    }
    // 跳转地图:有坐标的命中高亮
    const withPos = r.hits.filter((h) => h.qw);
    if (withPos.length) {
      map.highlightPapers(new Set(withPos.map((h) => h.id)));
      map.focusPaper(withPos[0].id);
      toast(`定位到 ${withPos.length} 篇,地图高亮中`);
    }
  } catch (e) {
    showError(`搜索失败: ${e.message}`);
  }
}

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------
function toast(msg) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

function renderOverview(ov) {
  const box = $("overviewBox");
  const max = Math.max(1, ...(ov.roots || []).map((r) => r.paper_count));
  const rows = (ov.roots || [])
    .map(
      (r) => `
      <div class="ov-row" title="${escapeHtml(r.key)} (${r.children} 个子领域)">
        <span class="ov-name">${escapeHtml(r.name)}</span>
        <span class="ov-bar"><div style="width:${Math.round((r.paper_count / max) * 100)}%"></div></span>
        <span class="ov-num">${r.paper_count}</span>
      </div>`
    )
    .join("");
  const tags = (ov.hot_tags || []).slice(0, 10)
    .map((t) => `<span class="ov-tag">${escapeHtml(t.tag)} <b>${t.count}</b></span>`)
    .join("");
  const cats = (ov.categories || []).slice(0, 8)
    .map((c) => `<span class="ov-tag ov-cat" title="arXiv 学科">📚 ${escapeHtml(c.key)} <b>${c.count}</b></span>`)
    .join("");
  box.innerHTML =
    rows +
    (ov.ai_domains ? `<div class="ov-ai">✨ AI 增量领域 ${ov.ai_domains} 个</div>` : "") +
    (cats ? `<div style="margin-top:6px;color:var(--muted)">学科分布:</div><div class="ov-tags">${cats}</div>` : "") +
    (tags ? `<div style="margin-top:6px;color:var(--muted)">热门方向:</div><div class="ov-tags">${tags}</div>` : "");
  $("overviewInfo").textContent = `共 ${ov.papers} 篇论文`;
}

function renderPaperStats(st) {
  const box = $("statsBox");
  if (!st) { box.innerHTML = `<div class="loading-hint">暂无数据</div>`; return; }
  const maxSource = Math.max(1, ...Object.values(st.by_source || {}));
  const sourceRows = Object.entries(st.by_source || {})
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `
      <div class="stat-mini-row" title="${escapeHtml(k)}">
        <span class="sm-name">${escapeHtml(k)}</span>
        <span class="sm-bar"><div style="width:${Math.round((v / maxSource) * 100)}%"></div></span>
        <span class="sm-num">${v}</span>
      </div>`)
    .join("");
  const maxHeat = Math.max(1, ...Object.values(st.heat || {}));
  const heatRows = Object.entries(st.heat || {})
    .map(([k, v]) => `
      <div class="stat-mini-row">
        <span class="sm-name">${k === "hot" ? "🔥 热门" : k === "cold" ? "🧊 冷门" : "普通"}</span>
        <span class="sm-bar"><div style="width:${Math.round((v / maxHeat) * 100)}%"></div></span>
        <span class="sm-num">${v}</span>
      </div>`)
    .join("");
  const trend = (st.monthly_trend || []).slice(-6);
  const maxT = Math.max(1, ...trend.map((t) => t.count));
  const trendRow = trend.length
    ? `<div class="stat-mini-row" style="align-items:flex-end">
        ${trend.map((t) => `<span title="${t.month} ${t.count} 篇" style="flex:1;text-align:center">
          <div style="height:${Math.round((t.count / maxT) * 18)}px;background:var(--accent);border-radius:2px 2px 0 0;min-height:2px"></div>
          <div style="font-size:8px;color:var(--muted)">${t.month.slice(5)}</div></span>`).join("")}
      </div>`
    : "";
  const conf = st.confidence_bins || {};
  box.innerHTML = `
    <div class="stats-cards">
      <div class="stat-card"><div class="sv">${st.total}</div><div class="sl">论文总数</div></div>
      <div class="stat-card"><div class="sv">${st.anchored}</div><div class="sl">已锚定 ${Math.round((st.anchored_rate || 0) * 100)}%</div></div>
      <div class="stat-card"><div class="sv">${st.avg_confidence || "-"}</div><div class="sl">平均置信度</div></div>
      <div class="stat-card"><div class="sv">${st.by_source ? Object.keys(st.by_source).length : "-"}</div><div class="sl">数据源</div></div>
    </div>
    <div class="stats-sec">来源分布</div>${sourceRows}
    <div class="stats-sec">领域热度</div>${heatRows}
    ${trendRow ? `<div class="stats-sec">近 6 月发表趋势</div>${trendRow}` : ""}
    ${conf["0.5-0.7"] !== undefined ? `<div class="stats-sec">置信度: 低 ${conf["0.5-0.7"]} · 中 ${conf["0.7-0.9"]} · 高 ${conf["0.9-1.0"]}</div>` : ""}
  `;
  $("statsInfo").textContent = `${st.total} 篇`;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function escapeAttr(s) {
  return escapeHtml(s);
}

// ---------------------------------------------------------------------------
// 事件与启动
// ---------------------------------------------------------------------------
const tree = new DomainTree($("treeContainer"), {
  onSelect: (node) => {
    state.selectedDomain = node.key;
    map.focusDomain(node.key);
    loadPapers(node.key);
  },
});

const map = new QuaternionMap($("mapContainer"), {
  onPaperClick: (id) => openDetail(id),
  onDomainClick: (key) => {
    const node = state.tree.find((n) => n.key === key);
    if (node) {
      tree.select(key);
      state.selectedDomain = key;
      loadPapers(key);
    }
  },
  onEnterDomain: (key) => enterDomain(key),
  onHover: (hit) => {
    if (hit.type === "domain") {
      map.container.setAttribute("title", `${hit.name} (${hit.paper_count} 篇)`);
    }
  },
  onMode: (mode) => {
    log(`地图渲染模式: ${mode === "3d" ? "WebGL 3D" : "Canvas 2D(WebGL 不可用,已自动降级)"}`);
    if (mode === "2d") $("rotateBtn").classList.add("hidden");  // 2D 无自动旋转
  },
});

// ---------------------------------------------------------------------------
// 左侧栏分页
// ---------------------------------------------------------------------------
document.querySelectorAll(".sidebar-tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".sidebar-tabs button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    document.querySelectorAll(".sidebar-page").forEach((p) => p.classList.add("hidden"));
    const page = document.getElementById("page-" + btn.dataset.page);
    if (page) page.classList.remove("hidden");
  });
});

// ---------------------------------------------------------------------------
// 地图子领域导航(双击进入 / 面包屑 / 返回 / 零点)与旋转控制
// ---------------------------------------------------------------------------
let focusChain = [];
let autoRotate = true;

function enterDomain(key) {
  // 由 key 回溯到根,构建导航链
  const chain = [];
  let k = key;
  while (k) {
    chain.unshift(k);
    const node = state.tree.find((n) => n.key === k);
    k = node ? node.parent : null;
  }
  focusChain = chain;
  map.setFocus(focusChain);
  renderBreadcrumb();
  log(`进入子领域视图: ${key}(局部坐标展开)`);
  const node = state.tree.find((n) => n.key === key);
  if (node) loadPapers(node.key);
}

function renderBreadcrumb() {
  const bc = $("breadcrumb");
  if (!focusChain.length) {
    bc.classList.add("hidden");
    bc.innerHTML = "";
    $("upBtn").classList.add("hidden");
    $("mapSub").textContent = "拖动旋转 · 滚轮缩放 · 单击论文详情 · 双击领域进入子领域";
    return;
  }
  bc.classList.remove("hidden");
  $("upBtn").classList.remove("hidden");
  const cur = focusChain[focusChain.length - 1];
  const curNode = state.tree.find((n) => n.key === cur);
  $("mapSub").textContent = `当前: ${curNode ? curNode.name : cur} · 共 ${focusChain.length} 层 · 双击继续深入`;
  bc.innerHTML = focusChain
    .map((k, i) => {
      const node = state.tree.find((n) => n.key === k);
      const name = node ? node.name : k;
      if (i === focusChain.length - 1) return `<span class="bc-cur">${escapeHtml(name)}</span>`;
      return `<span class="bc-item" data-key="${k}">${escapeHtml(name)}</span><span class="bc-sep">›</span>`;
    })
    .join("");
  bc.querySelectorAll(".bc-item").forEach((el) => {
    el.addEventListener("click", () => {
      const idx = focusChain.indexOf(el.dataset.key);
      if (idx >= 0) {
        focusChain = focusChain.slice(0, idx + 1);
        map.setFocus(focusChain);
        renderBreadcrumb();
      }
    });
  });
}

$("upBtn").addEventListener("click", () => {
  focusChain.pop();
  map.setFocus(focusChain);
  renderBreadcrumb();
});

$("homeBtn").addEventListener("click", () => {
  focusChain = [];
  map.goHome();
  autoRotate = true;
  $("rotateBtn").textContent = "⏸ 暂停旋转";
  renderBreadcrumb();
  log("视图已对齐零点并回到根");
});

$("rotateBtn").addEventListener("click", () => {
  autoRotate = !autoRotate;
  map.setAutoRotate(autoRotate);
  $("rotateBtn").textContent = autoRotate ? "⏸ 暂停旋转" : "▶ 自动旋转";
  log(`自动旋转: ${autoRotate ? "开启" : "暂停"}`);
});

// 拖动模式:旋转 ⇄ 平移视图中心
let panMode = false;
$("panBtn").addEventListener("click", () => {
  panMode = !panMode;
  map.setPanMode(panMode);
  $("panBtn").textContent = panMode ? "✋ 平移" : "🔄 旋转";
  $("panBtn").classList.toggle("active", panMode);
  log(`拖动模式: ${panMode ? "平移(左键拖动移动视图中心)" : "旋转"}`);
});

$("searchBtn").addEventListener("click", doSearch);
$("searchInput").addEventListener("keydown", (e) => e.key === "Enter" && doSearch());
$("crawlBtn").addEventListener("click", crawl);
$("crawlQuery").addEventListener("keydown", (e) => e.key === "Enter" && crawl());
$("crawlCount").addEventListener("click", () => {
  state.crawlCount = state.crawlCount === 20 ? 50 : state.crawlCount === 50 ? 10 : 20;
  $("crawlCount").textContent = state.crawlCount;
});
$("modalClose").addEventListener("click", () => $("detailModal").classList.add("hidden"));
$("detailModal").addEventListener("click", (e) => {
  if (e.target === $("detailModal")) $("detailModal").classList.add("hidden");
});

// 日志面板折叠
$("logToggle").addEventListener("click", () => {
  const hidden = logPanel.classList.toggle("hidden");
  $("logToggle").textContent = hidden ? "展开" : "收起";
});

// 着色模式切换:视角(根领域色)/ 学科(arXiv 分类色)
let colorMode = "view";
$("colorModeBtn").addEventListener("click", () => {
  colorMode = colorMode === "view" ? "category" : "view";
  $("colorModeBtn").textContent = colorMode === "view" ? "🎨 视角着色" : "🎨 学科着色";
  $("colorModeBtn").classList.toggle("active", colorMode === "view");
  map.setColorMode(colorMode);
  log(`论文着色模式: ${colorMode === "view" ? "视角(根领域)" : "学科(arXiv 分类)"}`);
});

// 领域演化:LLM 按论文数聚类热门领域,建议/创建细分方向
async function evolveDomains() {
  const btn = $("evolveBtn");
  btn.disabled = true;
  btn.textContent = "演化中…";
  try {
    const r = await post("/api/domains/evolve", { auto_create: true, limit: 3 });
    log(`领域演化完成: 建议 ${r.suggested.length} 个热门领域细分`);
    for (const item of r.suggested) {
      log(`  ${item.domain} (${item.paper_count} 篇): ${item.suggestions.map((s) => s.name).join(" / ")}`);
    }
    if (r.created.length) log(`新建子领域 ${r.created.length} 个: ${r.created.map((c) => c.key).join(", ")}`, "warn");
    if (r.reused.length) log(`复用已有领域 ${r.reused.length} 个`);
    if (r.cold.length) log(`冷门领域 ${r.cold.length} 个(展示层已淡化)`);
    if (r.errors.length) log(`演化错误: ${r.errors.join("; ")}`, "error");
    toast(`领域演化完成: 新建 ${r.created.length} · 复用 ${r.reused.length} · 冷门 ${r.cold.length}`);
    await loadAll();
  } catch (e) {
    showError(`领域演化失败: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "🧬 领域演化";
  }
}
$("evolveBtn").addEventListener("click", evolveDomains);

// ---------------------------------------------------------------------------
// 数据源面板与编辑弹窗
// ---------------------------------------------------------------------------
let editingSource = null;

const sourcePanel = new SourcePanel($("sourceList"), {
  onEdit: (name) => openSourceModal(name),
  onCrawlDone: () => { loadAll(); sourcePanel.load(); },
});

function openSourceModal(name) {
  editingSource = name;
  const src = name ? sourcePanel.sources.find((s) => s.name === name) : null;
  $("srcModalTitle").textContent = src ? `编辑数据源: ${src.name}` : "添加数据源";
  $("srcName").value = src ? src.name : "";
  $("srcName").disabled = !!src;
  $("srcDisplay").value = src ? src.display_name || "" : "";
  $("srcType").value = src ? src.source_type : "open";
  $("srcApiUrl").value = src && src.config ? src.config.api_url || "" : "";
  $("srcAccount").value = "";
  $("srcPassword").value = "";
  const cred = src && src.credential;
  $("srcAccount").placeholder = cred && cred.account_mask
    ? `已配置: ${cred.account_mask}(留空不修改)`
    : "图书馆账号(学号/用户名),留空不修改";
  $("srcEnabled").checked = src ? src.enabled : true;
  _syncCredBlock();
  $("sourceModal").classList.remove("hidden");
}

function _syncCredBlock() {
  const isLib = $("srcType").value === "library";
  $("srcCredBlock").classList.toggle("hidden", !isLib);
  const apiLabel = $("srcApiUrl").closest("label");
  if (apiLabel) apiLabel.classList.toggle("hidden", isLib);
}

async function saveSource() {
  const name = $("srcName").value.trim();
  if (!name) return toast("请输入来源名称");
  const payload = {
    name,
    display_name: $("srcDisplay").value.trim(),
    source_type: $("srcType").value,
    enabled: $("srcEnabled").checked,
    config: { api_url: $("srcApiUrl").value.trim() },
  };
  // library 类型:账号/密码留空 = 不修改(避免误清空);填写才随 payload 提交
  if (payload.source_type === "library") {
    const account = $("srcAccount").value.trim();
    const password = $("srcPassword").value;
    if (account) payload.account = account;
    if (password) payload.password = password;
  }
  try {
    const url = editingSource ? `/api/sources/${editingSource}` : "/api/sources";
    const method = editingSource ? "PUT" : "POST";
    const r = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || r.status);
    }
    toast(editingSource ? "来源已更新" : "来源已添加");
    $("sourceModal").classList.add("hidden");
    sourcePanel.load();
    if (editingSource) await loadAll();
  } catch (e) {
    showError(`保存失败: ${e.message}`);
  }
}

$("addSourceBtn").addEventListener("click", () => openSourceModal(null));
$("srcType").addEventListener("change", _syncCredBlock);
$("srcSaveBtn").addEventListener("click", saveSource);
$("srcCancelBtn").addEventListener("click", () => $("sourceModal").classList.add("hidden"));
$("srcModalClose").addEventListener("click", () => $("sourceModal").classList.add("hidden"));
$("sourceModal").addEventListener("click", (e) => {
  if (e.target === $("sourceModal")) $("sourceModal").classList.add("hidden");
});

log("前端模块加载完成,开始初始化");
loadAll();
