# AIMap — AI 论文领域地图

爬取 AI 领域(参数模型 / 算法 / infra)论文 → 多层分类 + 多层 Agent 锚定标签与位置 →
构造四元数领域树与地图 → 可视化展示并支持跳转。

## 架构总览

```
┌─────────────────────────────── Web 前端 (three.js 3D 地图 + 领域树 + 论文列表) ───┐
│   /api/tree  /api/map/nodes  /api/papers  /api/search  /api/crawl/arxiv          │
└───────────────────────────────────────┬───────────────────────────────────────────┘
                                        │ FastAPI
┌───────────────────────────────────────▼───────────────────────────────────────────┐
│  L3 OrchestratorAgent —— 调度/持久化(agents/orchestrator.py)                      │
│  L2 AnchorAgent / ProfileAgent —— 锚定标签+位置、画像(agents/anchor.py)            │
│  L1 多层分类器: rules(关键词) → stats(TF-IDF) → llm(Provider) → ensemble(加权投票) │
│     爬虫: arxiv(可用) / ieee·acm·cnki(账号预留)                                    │
│  LLM Provider: mock(默认) / openai / deepseek(填 key 即启用)                       │
│  四元数内核: S3 球面坐标、树嵌入、立体投影(quaternion/core.py)                     │
└───────────────────────────────────────┬───────────────────────────────────────────┘
                                        │ SQLModel + SQLite (data/aimap.db)
```

## 快速开始

```bash
# 1. 安装依赖
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"      # Windows
# source .venv/bin/activate && pip install -e ".[dev]"   # Linux/macOS

# 2. 初始化 + 导入演示论文(含多层分析)
.venv/Scripts/python scripts/seed.py --with-demo

# 3. 启动服务
.venv/Scripts/uvicorn app.main:app --reload --port 8000
# 打开 http://localhost:8000
```

### 采集真实论文(任务化,支持断点续爬与多来源去重)

```bash
# 方式一:网页内输入检索式点击"采集 arXiv"
# 方式二:CLI
.venv/Scripts/python scripts/crawl.py "large language model" --max 20
```

每次采集生成一个持久化任务(`CrawlJob`):
- 状态机 `pending → running → done | failed | stopped`,失败自动记录 `next_retry_at`;
- **断点续爬**:网络中断/429 停止后,`POST /api/crawl/jobs/{id}/resume` 从上次游标继续;
- **多来源去重**:同源同 id → 更新;标题归一化(忽略大小写/标点/版本号)或 URL 相同 → 重复过滤,任务统计 `total_duplicates`;
- 任务查询:`GET /api/crawl/jobs` / `GET /api/crawl/jobs/{id}`。

**资源获取规范**(所有数据源统一,`.env` 可调):
- 每来源独立限流(默认 3s/次);
- 429 优先 `Retry-After`,否则指数退避+抖动;5xx/超时同样重试(默认 3 次);
- 连续失败熔断(默认 5 次失败 → 冷却 300s,期间不再打扰站点)。

## 配置

复制 `.env.example` 为 `.env` 后按需填写:

| 配置项 | 说明 |
|---|---|
| `AIMAP_LLM_PROVIDER` | `mock`(默认,无需 key)/ `openai` / `deepseek` |
| `AIMAP_LLM_MODEL` | 模型名(DeepSeek 实测可用 `deepseek-v4-flash` / `deepseek-v4-pro`) |
| `AIMAP_ARXIV_DELAY` | arXiv 限流间隔(秒) |
| `AIMAP_ARXIV_API` | arXiv API 端点(默认官方;网络受限时改用镜像) |
| `AIMAP_SESSION_TTL_HOURS` | 图书馆登录会话有效期(默认 168 小时) |

### API Key 安全导入(推荐)

**明文 key 不落盘、不写入 .env** —— 使用 SecretVault 加密存储:

```bash
# 从密钥文件导入(输出仅脱敏摘要;Windows 下主密钥由系统 DPAPI 保护,绑定当前用户)
.venv/Scripts/python scripts/import_secret.py --source F:/Desktop/token/deepseek-api.txt --name deepseek_api_key
# 或交互式粘贴
.venv/Scripts/python scripts/import_secret.py --prompt deepseek_api_key
```

- 密文存于 `data/secrets/`(Fernet 加密,主密钥不落明文);
- 运行时由 `DeepSeekProvider` 自动解密读取,日志不含明文;
- 跨机器/用户迁移需重新导入(见 `docs/deploy.md`);
- 验证连接:`.venv/Scripts/python scripts/test_llm.py --probe`(探测可用模型)或直接 `scripts/test_llm.py`。

> **网络说明**:爬虫基于 httpx,默认读取系统代理环境变量(`HTTPS_PROXY` 等)。
> 若 `export.arxiv.org` 无法直连(常见于部分网络环境),配置代理即可:
> `export HTTPS_PROXY=http://127.0.0.1:7890`(Windows PowerShell: `$env:HTTPS_PROXY="http://127.0.0.1:7890"`),
> 或在 `.env` 中将 `AIMAP_ARXIV_API` 指向可用的镜像端点。

## 核心机制

### 1. 多层分类(anchoring)
- **L1 rules**:领域树关键词子串匹配,精确、可解释;
- **L2 stats**:TF-IDF + 余弦相似度(领域关键词伪文档),覆盖广、零依赖;
- **L3 llm**:LLM 结构化输出领域标签(真实 provider 时权重最高);
- **ensemble**:按层加权投票,输出最终标签 + 置信度 + 完整证据链(可追溯)。

### 2. 多层 Agent
- **L1 采集 Agent**:`crawler/` 下每个数据源一个实现,统一 `search/fetch_by_id` 接口;
- **L2 分析 Agent**:`AnchorAgent`(分类+锚定位置)、`ProfileAgent`(画像);
- **L3 编排 Agent**:`OrchestratorAgent` 调度 L2、落库、刷新领域计数。

### 3. 四元数地图
- 领域树每个节点 = 单位四元数(**S3 球面上的点**),根为恒等四元数,
  子节点 = 父四元数 × 兄弟旋转增量(四元数乘法即旋转复合);
- 论文位置 = 锚定领域四元数 × 小角度特征扰动,再归一化 → 确定性、聚簇性;
- 4D → 3D 采用立体投影(stereographic projection),前端 three.js 渲染;
- 领域/论文间的测地距离 = `arccos(|⟨q₁,q₂⟩|)`,支持"地图上多近 = 领域多近"的语义。

### 3. 动态领域生长(AI 增量研究方向)
领域树不是写死的:种子领域(三大根 + 细分)只是**基准规则**,分析论文时:

- LLM 层发现现有领域无法覆盖的新研究方向 → `create_new=true`(置信度 ≥ 0.7),
  给出新领域名称、挂载父领域与描述;
- `domain/policy.py` 按基准规则创建领域节点:
  稳定 key(`父.slug`)、去重(同父同名复用)、**四元数坐标自动分配**
  (父四元数 × 兄弟旋转,与种子同一嵌入规则,新领域自然落在父领域附近的世界坐标上);
- 新领域在树中以 `✨` 标记、地图自动出现,`/api/domains/recent` 可查增量,
  `/api/overview` 展示整体研究方向(根领域分布 + AI 增量数 + 热门 tag);
- 每篇论文同时打**增量 tag**(主标签定地图位置,附加标签 + LLM 关键词丰富画像),
  详情弹窗可见全部 tags。

实测:分析类脑计算论文时,DeepSeek 自动创建
`infra.gpu.neuromorphic-computing`(Neuromorphic Computing,置信度 0.95)并锚定。

### 3.5 领域演化(论文数量驱动)
领域树的内容随论文数量动态更新(`domain/evolution.py`):

- **热度分级**:每领域(含子树)论文数 → `hot`(≥12 篇)/ `normal` / `cold`(≤1 篇),
  树中 🔥 热门、冷门淡化显示;
- **热门细分**:点击"🧬 领域演化"按钮,LLM 对最热的领域聚类论文标题,
  建议 2~5 个细分方向,自动创建子领域(去重复用已有);
- **演化报告**:每次演化输出建议明细 / 新建 / 复用 / 冷门清单;
- API:`GET /api/domains/stats`(热度)、`POST /api/domains/evolve`(触发演化)。

实测(models.llm 60 篇):LLM 聚类出 LLM Ensemble & Routing(复用)、
LLM-Aided Hardware Design、LLM Compression & Quantization、
LLM Interpretability & Safety(新建 3 个子领域)。

### 3.6 地图渲染(关联度与可用性)
- 领域球半径 ∝ 论文数,hot 领域发光;论文点 → 所属领域**关联连线**;
- hover **tooltip**(论文标题/领域热度);点击论文打开详情;
- **着色切换**:🎨 视角(根领域色)/ 学科(arXiv 分类色)一键切换;
- **子领域导航**:双击领域球进入子领域视图——以该领域为投影中心,
  子树节点与论文**局部重新展开坐标**(中心白球标记),可连续深入,
  面包屑逐级返回;⬆ 返回 / ⏺ 零点(回根并对齐)按钮;
- **旋转控制**:⏸ 暂停/▶ 自动旋转开关(进入子领域自动暂停);
- 图例说明;WebGL 不可用时自动降级 Canvas 2D(功能等价)。

### 3.8 左侧栏分页
左侧栏分三个页面(顶部 tab):📊 总览(论文统计 + 研究方向总览)、
📡 数据源(来源管理面板)、🌳 领域树(热度标记),每页独立滚动。

### 4. 图书馆账号(模拟浏览器登录)
IEEE / ACM / CNKI 站点通常需要浏览器登录态(账号 + 验证码 / SSO / 二次验证)。
使用 playwright 模拟浏览器完成登录并复用会话(cookie):

### 3.7 数据源管理与论文统计(左侧栏)
- **数据源面板**:每个来源一行(状态灯/启用开关/采集/探测/登录/编辑/删除);
  - 采集为**异步执行**,面板内显示进度条(分页游标)与逐条任务日志;
  - 🔍 探测按钮 = 自动 agent:查询端点连通性(HTTP)/ 凭据 / 登录会话,
    并输出适配建议(如"配置 HTTPS_PROXY 代理或更换镜像"、"运行 library_login 登录");
  - **🔑 登录按钮**(library 来源):一键触发 playwright 浏览器登录
    (复用 `PlaywrightLoginManager`,弹出有头浏览器完成验证码/SSO,
    自动检测登录成功后保存会话并回访校验);面板显示 🔐 已登录/未登录/已过期;
  - "+ 添加"可注册自定义来源(open 类型配 api_url,library 类型后续实现检索);
  - API:`GET/POST /api/sources`、`PUT/DELETE /api/sources/{name}`、
    `POST /api/sources/{name}/probe`、`POST /api/sources/{name}/login`、
    `GET /api/crawl/jobs/{id}/logs`;
- **论文统计面板**:总数/已锚定率/平均置信度/数据源数数字卡,
  来源分布、领域热度分布、近 6 月发表趋势、置信度分布;
- 采集失败任务保留游标,`POST /api/crawl/jobs/{id}/resume` 断点续爬。

```bash
# 安装模拟浏览器(一次)
.venv/Scripts/pip install -e ".[library]" && .venv/Scripts/playwright install chromium

# 登录(弹出浏览器,手动完成登录;处理验证码/SSO 最稳)
.venv/Scripts/python scripts/library_login.py --source ieee
# 查看会话状态 / 实测校验
.venv/Scripts/python scripts/library_login.py --source cnki --status
.venv/Scripts/python scripts/library_login.py --source ieee --verify
```

- **登录成功为全自动检测**(站内 URL + 会话类 cookie + 离开登录页),无需回车确认;
  超时会输出诊断(当前 URL / cookie 清单 / localStorage);
- 登录态(cookie)保存于 `data/sessions/<source>.json`(默认有效期 7 天,可配),
  保存后自动回访目标站校验有效性;
- 账号密码来自 `.env`(`LIB_*_ACCOUNT/PASSWORD`),未配置也可纯人工登录;
- 会话就绪后,`crawler/library.py` 中实现各库 `_search_impl`,经
  `manager.authenticated_browser()` 复用登录态发起检索。

### 5. 云端部署与存储管理
爬虫服务可部署到远程云(SSH 信息提供后按 `docs/deploy.md` 执行):
- systemd 常驻服务模板:`deploy/aimap.service`;
- 数据迁移(云 ↔ 本地):`scripts/backup.py --export/--import`(JSON 幂等合并,保留锚定);
- 快照备份:`scripts/backup.py --snapshot`;规模统计:`--info`;
- 密钥与会话绑定部署机器,云上需重新导入/登录(详见部署文档)。

### 6. 前端验证与排障
```bash
.venv/Scripts/python scripts/verify_frontend.py   # playwright 真实浏览器检查渲染/日志/错误
```
前端自带运行日志面板(页面上方可展开)与全局错误横幅;初始化分 4 步
(API 连通 → 领域树 → 地图数据 → 渲染),任一步失败都会在状态栏与横幅给出明确原因。

## 测试

```bash
.venv/Scripts/python -m pytest -v
```

覆盖:四元数运算正确性、arXiv XML 解析、图书馆凭据门禁、多层分类各层、
端到端锚定管线(确定性)、全部 API 路由、前端静态托管。

## 目录结构

```
app/
  config.py            # 配置(.env)
  db.py                # SQLite 存储
  models/entities.py   # 数据模型
  crawler/             # arxiv + 图书馆预留
  llm/                 # Provider 抽象 + mock/openai/deepseek
  classify/            # 多层分类器 + 集成
  agents/              # 多层 Agent 框架
  quaternion/core.py   # 四元数数学内核
  domain/              # 领域树种子/构建/位置锚定
  api/routes.py        # REST API
frontend/              # 纯静态前端(three.js + d3,CDN)
scripts/               # seed / crawl CLI
tests/                 # pytest 全链路
```
