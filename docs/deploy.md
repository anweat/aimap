# AIMap 云端部署指南(爬虫/分析服务跑在远程云)

> 待用户提供 SSH 信息后,按本指南执行部署。当前为模板与说明。

## 部署架构

```
┌────────────── 本地(开发/可视化) ──────────────┐   ┌────────────── 远程云服务器 ──────────────┐
│  AIMap 主服务 (uvicorn :8000)                 │   │  AIMap 服务 (systemd 常驻)              │
│  前端可视化 + 查询 + 地图跳转                  │   │  爬虫采集(arXiv/图书馆)+ 多层分析       │
│  SQLite (data/aimap.db)                       │◄──┤  SQLite (data/aimap.db)                 │
│  data/exports/*.json ◄── 数据迁移: backup.py ─┼──►│  data/exports/*.json                    │
└───────────────────────────────────────────────┘   └─────────────────────────────────────────┘
```

两种工作模式:
- **模式 A(推荐)**:云上跑完整服务(采集+分析+API),本地浏览器直接访问云 IP:8000;
- **模式 B**:云上仅跑采集与分析(cron/定时),数据通过 `scripts/backup.py --export/--import` 同步回本地可视化。

## 部署步骤(SSH 信息提供后执行)

### 1. 初始化服务器环境

```bash
# 在远程服务器上
sudo apt update && sudo apt install -y python3-venv python3-pip git
git clone <你的仓库地址> aimap && cd aimap
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium --with-deps   # 图书馆模拟浏览器登录需要
```

### 2. 配置与密钥

```bash
cp .env.example .env
# 编辑 .env: AIMAP_DB_PATH / AIMAP_ARXIV_DELAY 等
# 密钥导入(云上 SecretVault 用 Linux 主密钥文件模式,chmod 600):
.venv/bin/python scripts/import_secret.py --prompt deepseek_api_key
```

### 3. systemd 常驻服务

编辑 `deploy/aimap.service`,按实际路径修改 `WorkingDirectory` / `ExecStart` 后:

```bash
sudo cp deploy/aimap.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now aimap
sudo systemctl status aimap        # 查看状态
journalctl -u aimap -f             # 查看日志
```

### 4. 定时采集(可选)

```bash
crontab -e
# 每 6 小时采集一轮热门方向
0 */6 * * * cd /opt/aimap && .venv/bin/python scripts/crawl.py "large language model" --max 50 >> data/cron.log 2>&1
```

### 5. 数据迁移(云 ↔ 本地)

```bash
# 云上导出
cd aimap && .venv/bin/python scripts/backup.py --export
# 拉到本地
scp user@host:/opt/aimap/data/exports/aimap_*.json data/exports/
# 本地导入(幂等合并,不覆盖已有锚定)
.venv/Scripts/python scripts/backup.py --import data/exports/aimap_xxx.json
```

## 注意事项

- **密钥不随代码走**:`data/secrets/` 中的凭据库绑定创建它的机器/用户(Windows DPAPI / Linux 文件权限),
  云上需重新运行 `import_secret.py` 导入;
- **图书馆会话同理**:`data/sessions/` 中的登录态建议在云上重新登录
  (`scripts/library_login.py --source ieee`);
- **SSH 信息提供后**,将实际连接命令与服务器路径记录到 `docs/ssh.md`(不入库);
- 数据库备份建议 cron 每日 `backup.py --snapshot` + 定期 rsync 到本地。
