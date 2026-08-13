"""来源探测 Agent:自动查询数据源状态并输出适配建议。

每次探测(source_probe)输出:
  - 连通性:endpoint HTTP 探测(open 源)或会话/凭据状态(library 源);
  - 适配建议:不可达 → 建议代理/镜像;缺凭据 → 面板填写;缺会话 → 运行登录;
  - 数据佐证:最近采集统计、历史任务失败率。

前端"数据源面板"的 🔍 探测按钮调用 POST /api/sources/{name}/probe。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
from sqlmodel import Session, select

from app.config import settings
from app.models.entities import CrawlJob, SourceConfig


def _probe_http(url: str, timeout: float = 6.0) -> dict:
    """HTTP 连通性探测(遵循系统代理)。"""
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                         headers={"User-Agent": "aimap-probe/0.1"})
        return {"reachable": True, "status": resp.status_code, "ms": int(resp.elapsed.total_seconds() * 1000)}
    except httpx.TimeoutException:
        return {"reachable": False, "error": "连接超时(6s),目标站点无响应"}
    except httpx.ConnectError:
        return {"reachable": False, "error": "连接失败:网络不可达(检查网络/代理 HTTPS_PROXY)"}
    except httpx.HTTPError as e:
        return {"reachable": False, "error": f"HTTP 错误: {e}"}


def _probe_library(session: Session, source: SourceConfig) -> dict:
    """图书馆源:凭据 + 登录会话状态。"""
    from app.crawler.auth import PlaywrightLoginManager, SessionExpired, PlaywrightUnavailable

    cred = settings.library_credentials.get(source.name, {})
    has_credentials = bool(cred.get("account"))
    result: dict = {
        "credentials": has_credentials,
        "session": False,
        "suggestions": [],
    }
    try:
        manager = PlaywrightLoginManager(source.name)
        result["session"] = manager.has_session()
        if result["session"]:
            meta = manager.session_meta() or {}
            result["session_expired"] = meta.get("expired", False)
            result["session_cookies"] = meta.get("cookie_count", 0)
    except PlaywrightUnavailable:
        result["playwright"] = False
    except Exception:
        result["session"] = False

    if not has_credentials:
        result["suggestions"].append("未配置账号:请在数据源面板(📡 数据源 → ✏️ 编辑)填写账号密码")
    if not result["session"]:
        result["suggestions"].append("无登录会话:运行 python scripts/library_login.py "
                                     f"--source {source.name} 完成浏览器登录")
    elif result.get("session_expired"):
        result["suggestions"].append("登录会话已过期,请重新登录")
    if not result.get("playwright", True):
        result["suggestions"].append("playwright 未安装: pip install -e '.[library]' && playwright install chromium")
    return result


def probe_source(session: Session, source: SourceConfig) -> dict:
    """探测单一来源,附最近采集统计与适配建议。"""
    report: dict = {
        "name": source.name,
        "display_name": source.display_name,
        "source_type": source.source_type,
        "enabled": source.enabled,
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "reachable": False,
        "details": {},
        "suggestions": [],
        "last_crawl": None,
    }

    # 最近采集统计
    job = session.exec(
        select(CrawlJob).where(CrawlJob.source == source.name)
        .order_by(CrawlJob.id.desc()).limit(1)
    ).first()
    if job:
        report["last_crawl"] = {
            "id": job.id, "status": job.status, "saved": job.total_saved,
            "duplicates": job.total_duplicates, "failed": job.total_failed,
            "at": job.updated_at.isoformat() if job.updated_at else None,
            "error": job.last_error[:120],
        }

    try:
        cfg = json.loads(source.config or "{}")
    except json.JSONDecodeError:
        cfg = {}

    if source.source_type == "open":
        url = cfg.get("api_url", "")
        if url:
            probe = _probe_http(url)
            report["reachable"] = probe["reachable"]
            report["details"] = probe
            if not probe["reachable"]:
                report["suggestions"].append(
                    f"端点 {url} 不可达:配置 HTTPS_PROXY 代理,或在来源面板更换镜像端点"
                )
        else:
            report["suggestions"].append("未配置 api_url,请在来源面板编辑")
    else:
        lib = _probe_library(session, source)
        report["details"] = {k: v for k, v in lib.items() if k != "suggestions"}
        report["reachable"] = bool(lib.get("session"))
        report["suggestions"] = lib.get("suggestions", [])

    if not source.enabled:
        report["suggestions"].append("该来源当前已禁用(可在面板启用)")
    return report
