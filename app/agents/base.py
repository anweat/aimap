"""多层 Agent 框架:Agent 基类与消息协议。

层次设计(从下到上):
  L1 采集 Agent    : 数据源交互(爬虫封装,见 crawler/)
  L2 分析 Agent    : 单篇论文的画像 / 分类 / 锚定(anchor.py)
  L3 编排 Agent    : 调度 L2 Agent、管理持久化与批处理(orchestrator.py)

所有 Agent 统一输入 AgentTask、输出 AgentResult,便于替换与测试。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class AgentTask(BaseModel):
    """Agent 任务单元。"""

    task_type: str                       # profile | anchor | orchestrate
    paper_id: int | None = None
    paper: dict[str, Any] | None = None  # 论文快照(标题/摘要等)
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    """Agent 输出:status + 结构化产物 + 文本说明。"""

    task_type: str
    status: str = "ok"                   # ok | skipped | error
    message: str = ""
    artifacts: dict[str, Any] = Field(default_factory=dict)


class BaseAgent(ABC):
    """所有 Agent 的基类:实现 run() 即完成一个 L2/L3 能力。"""

    name: str = "base"

    @abstractmethod
    def run(self, task: AgentTask) -> AgentResult:
        ...
