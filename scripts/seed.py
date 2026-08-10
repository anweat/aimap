"""种子脚本:初始化数据库、构建领域树、可选导入演示论文。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import count_domain_nodes, count_papers, get_session, init_db, upsert_paper
from app.domain.builder import build_domain_tree
from app.models.entities import Paper

DEMO_PAPERS = [
    {
        "source": "demo",
        "source_id": "demo-1",
        "title": "Attention Is All You Need",
        "abstract": "We propose the Transformer, a model architecture eschewing recurrence and relying entirely on attention.",
        "authors": "Vaswani et al.",
        "categories": "cs.CL cs.LG",
        "url": "https://arxiv.org/abs/1706.03762",
    },
    {
        "source": "demo",
        "source_id": "demo-2",
        "title": "DeepSpeed: System Optimizations Enable Training Deep Learning Models",
        "abstract": "We enable extreme-scale model training with ZeRO, a memory optimization technology.",
        "authors": "Rasley et al.",
        "categories": "cs.LG cs.DC",
        "url": "https://arxiv.org/abs/2207.00032",
    },
    {
        "source": "demo",
        "source_id": "demo-3",
        "title": "vLLM: Easy, Fast, and Cheap LLM Serving with PagedAttention",
        "abstract": "We present vLLM, a high-throughput LLM serving system with PagedAttention for efficient KV cache management.",
        "authors": "Kwon et al.",
        "categories": "cs.LG cs.DC",
        "url": "https://arxiv.org/abs/2309.06180",
    },
    {
        "source": "demo",
        "source_id": "demo-4",
        "title": "Scaling Laws for Neural Language Models",
        "abstract": "We study empirical scaling laws for language model performance as a function of model size, dataset size and compute.",
        "authors": "Kaplan et al.",
        "categories": "cs.LG cs.CL",
        "url": "https://arxiv.org/abs/2001.08361",
    },
    {
        "source": "demo",
        "source_id": "demo-5",
        "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
        "abstract": "We explore retrieval-augmented generation combining parametric memory with non-parametric memory.",
        "authors": "Lewis et al.",
        "categories": "cs.CL",
        "url": "https://arxiv.org/abs/2005.11401",
    },
    {
        "source": "demo",
        "source_id": "demo-6",
        "title": "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models",
        "abstract": "We explore how chain-of-thought prompting improves reasoning abilities of language models.",
        "authors": "Wei et al.",
        "categories": "cs.CL",
        "url": "https://arxiv.org/abs/2201.11903",
    },
]


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="AIMap 初始化/演示数据")
    parser.add_argument("--with-demo", action="store_true", help="导入演示论文并执行多层分析")
    args = parser.parse_args()

    init_db()
    with get_session() as s:
        nodes = build_domain_tree(s)
        print(f"[ok] 领域树: {len(nodes)} 个节点,{count_domain_nodes(s)} 已入库")

        if args.with_demo:
            from app.agents.orchestrator import OrchestratorAgent

            saved = [upsert_paper(s, Paper(**p)) for p in DEMO_PAPERS]
            orchestrator = OrchestratorAgent(s)
            ok = 0
            for p in saved:
                r = orchestrator.analyze_paper(p.id)
                status = "ok" if r.status == "ok" else r.status
                print(f"  - [{status}] {p.title[:50]} → {r.artifacts.get('domain_key', '')}")
                ok += r.status == "ok"
            print(f"[ok] 演示论文: {len(saved)} 篇,锚定成功 {ok} 篇")
        print(f"[ok] 论文总数: {count_papers(s)}")


if __name__ == "__main__":
    main()
