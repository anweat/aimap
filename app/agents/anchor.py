"""L2 分析 Agent:论文画像与锚定。

AnchorAgent —— 核心 Agent:
  输入:论文(标题+摘要)
  流程:
    1. 调用多层分类器(rules → stats → llm),收集各层结果;
    2. 集成层加权投票得到最终领域标签;
    3. 计算论文四元数位置(领域节点四元数 × 特征扰动);
    4. 产出:标签、置信度、位置、各层证据链。

ProfileAgent —— 画像 Agent(可选):
  调用 LLM Provider 提取关键词与一句话概括,写入产物。
"""
from __future__ import annotations

from sqlmodel import Session

from app.agents.base import AgentResult, AgentTask, BaseAgent
from app.classify.base import ClassifierResult
from app.classify.ensemble import EnsembleClassifier
from app.classify.llm import LlmClassifier
from app.classify.rules import RulesClassifier
from app.classify.stats import StatsClassifier
from app.domain.position import anchor_paper_position
from app.llm.base import BaseProvider, ChatMessage, extract_json
from app.llm.registry import get_provider
from app.models.entities import DomainNode


class AnchorAgent(BaseAgent):
    """锚定 Agent:多层分类 → 集成 → (AI 动态建领域)→ 四元数位置 + 增量 tag。"""

    name = "anchor"

    # LLM 建议新领域时的最低置信度
    NEW_DOMAIN_MIN_CONFIDENCE = 0.7

    def __init__(self, session: Session, provider: BaseProvider | None = None):
        self._session = session
        self._provider = provider or get_provider()
        self._ensemble = EnsembleClassifier()

    def run(self, task: AgentTask) -> AgentResult:
        paper = task.paper
        if not paper:
            return AgentResult(task_type=task.task_type, status="error", message="缺少论文数据")
        title = paper.get("title", "")
        abstract = paper.get("abstract", "")

        # 1. 多层分类
        classifiers = [
            RulesClassifier(self._session),
            StatsClassifier(self._session),
            LlmClassifier(self._provider),
        ]
        results: list[ClassifierResult | None] = [
            c.classify(title, abstract, metadata={"paper_id": paper.get("id")})
            for c in classifiers
        ]

        # 2. LLM 新领域建议 → 动态创建领域(基准规则见 domain/policy.py)
        created_domain = self._maybe_create_domain(results)
        if created_domain:
            # 用新领域替换 LLM 层结果参与集成
            llm_result = results[2]
            if llm_result:
                llm_result.domain_key = created_domain.key
                llm_result.domain_name = created_domain.name
                llm_result.evidence = (
                    f"LLM 创建新领域: {created_domain.name} (挂载于 {created_domain.parent_key})"
                )

        # 3. 集成
        ensemble = self._ensemble.combine(results)
        if ensemble is None:
            return AgentResult(
                task_type=task.task_type,
                status="skipped",
                message="所有分类层均弃权,论文未锚定",
                artifacts={"layers": [r.model_dump() if r else None for r in results]},
            )

        # 4. 四元数位置
        pos = anchor_paper_position(
            self._session, _paper_entity(task), ensemble.domain_key, ensemble.confidence
        )

        # 5. 增量 tag(各层领域 + LLM 关键词 + 学科维度)
        tags = self._collect_tags(results, ensemble.domain_key, paper)

        return AgentResult(
            task_type=task.task_type,
            status="ok",
            message=(
                f"锚定至 {ensemble.domain_key} (置信度 {ensemble.confidence:.2f})"
                + (f"; 新建领域 {created_domain.name}" if created_domain else "")
            ),
            artifacts={
                "domain_key": ensemble.domain_key,
                "domain_name": ensemble.domain_name,
                "confidence": ensemble.confidence,
                "position": {"qw": pos.qw, "qx": pos.qx, "qy": pos.qy, "qz": pos.qz},
                "layers": [r.model_dump() if r else None for r in results],
                "tags": tags,
                "created_domain": (
                    {"key": created_domain.key, "name": created_domain.name,
                     "parent": created_domain.parent_key, "description": created_domain.description}
                    if created_domain else None
                ),
            },
        )

    # -- 动态建领域 ------------------------------------------------------
    def _maybe_create_domain(self, results: list[ClassifierResult | None]) -> DomainNode | None:
        """LLM 层建议新领域且置信度达标 → 按基准规则创建;返回新节点或 None。"""
        llm_result = results[2] if len(results) > 2 else None
        if not llm_result or not llm_result.extra.get("create_new"):
            return None
        if llm_result.confidence < self.NEW_DOMAIN_MIN_CONFIDENCE:
            return None
        name = llm_result.domain_name.strip()
        if not name:
            return None
        from app.domain.policy import create_domain

        node = create_domain(
            self._session,
            name,
            parent_key=llm_result.extra.get("parent_key") or None,
            description=llm_result.extra.get("description", ""),
            keywords=llm_result.extra.get("keywords", []),
            created_by="ai",
        )
        return node

    # -- 增量 tag --------------------------------------------------------
    @staticmethod
    def _collect_tags(results: list[ClassifierResult | None], primary_key: str,
                      paper: dict | None = None) -> list[dict]:
        tags: list[dict] = []
        seen: set[str] = set()
        for r in results:
            if not r or not r.domain_key:
                continue
            tag = r.domain_key
            if tag in seen:
                continue
            seen.add(tag)
            tags.append(
                {"tag": tag, "domain_key": tag, "source": r.layer,
                 "confidence": round(r.confidence, 3), "primary": tag == primary_key}
            )
        # LLM 关键词作为自由词 tag
        llm_result = results[2] if len(results) > 2 else None
        for kw in (llm_result.extra.get("keywords") or []) if llm_result else []:
            kw = kw.strip()
            if not kw or kw in seen:
                continue
            seen.add(kw)
            tags.append({"tag": kw, "domain_key": None, "source": "llm",
                         "confidence": round(llm_result.confidence, 3), "primary": False})
        # 学科维度:arXiv 来源用元数据 categories;其他来源用 LLM 判断的 arxiv_category
        categories: list[str] = []
        if paper and paper.get("source") == "arxiv":
            from app.domain.arxiv_taxonomy import split_categories

            categories = split_categories(paper.get("categories") or "")
        elif llm_result and llm_result.extra.get("arxiv_category"):
            categories = [llm_result.extra["arxiv_category"]]
        for cat in categories:
            if cat in seen:
                continue
            seen.add(cat)
            tags.append({"tag": cat, "domain_key": None, "source": "category",
                         "confidence": 1.0, "primary": False})
        return tags


class ProfileAgent(BaseAgent):
    """画像 Agent:提取论文关键词与一句话概括(Mock/真实 Provider 均可用)。"""

    name = "profile"

    def __init__(self, provider: BaseProvider | None = None):
        self._provider = provider or get_provider()

    def run(self, task: AgentTask) -> AgentResult:
        paper = task.paper or {}
        title = paper.get("title", "")
        abstract = paper.get("abstract", "")
        prompt = (
            "请分析这篇 AI 论文,输出 JSON: "
            '{"keywords": ["..."], "summary": "一句话概括", "category": "模型/算法/基础设施之一"}\n'
            f"标题: {title}\n摘要: {abstract[:2000]}"
        )
        try:
            content = self._provider.chat_structured(
                [ChatMessage(role="user", content=prompt)], temperature=0.0
            )
            import json

            data = json.loads(extract_json(content))
        except Exception as e:
            return AgentResult(task_type=task.task_type, status="error", message=f"画像失败: {e}")
        return AgentResult(
            task_type=task.task_type,
            status="ok",
            message="画像完成",
            artifacts={"profile": data},
        )


def _paper_entity(task: AgentTask):
    """任务内 paper dict → 实体(供位置计算)。"""
    from app.models.entities import Paper

    paper = task.paper or {}
    return Paper(
        id=paper.get("id"),
        source=paper.get("source", "unknown"),
        source_id=paper.get("source_id", ""),
        title=paper.get("title", ""),
        abstract=paper.get("abstract", ""),
    )
