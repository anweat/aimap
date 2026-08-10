"""FastAPI 入口:API 路由 + 前端静态托管。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import PROJECT_ROOT, settings
from app.db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    from app.db import count_domain_nodes, get_session, init_default_sources
    from app.domain.builder import build_domain_tree

    with get_session() as s:
        init_default_sources(s)
        if count_domain_nodes(s) == 0:
            build_domain_tree(s)
    yield


app = FastAPI(title="AIMap — AI 论文领域地图", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(router)

# 前端静态文件(开发/演示一体托管)
frontend_dir = PROJECT_ROOT / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
