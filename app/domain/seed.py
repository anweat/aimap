"""AI 领域树种子数据。

三大根:参数模型(models)、算法(algorithm)、基础设施(infra),
向下逐层展开,叶子节点携带关键词,用于多层分类第一层(规则层)的锚定。

每个节点:
  key     : 稳定标识(用于数据库主键)
  name    : 展示名
  parent  : 父节点 key(根节点为 None)
  keywords: 规则层匹配关键词(命中即锚定到该领域)
"""
DOMAIN_TREE_SEED: list[dict] = [
    # ============ 根:参数模型 ============
    {"key": "models", "name": "参数模型", "parent": None, "keywords": []},
    {"key": "models.llm", "name": "大语言模型", "parent": "models", "keywords": ["large language model", "llm", "foundation model", "pretrain", "预训练", "大语言模型", "chatgpt", "gpt"]},
    {"key": "models.llm.arch", "name": "模型架构", "parent": "models.llm", "keywords": ["transformer", "attention", "mixture of experts", "moe", "state space model", "ssm", "mamba", "hybrid architecture"]},
    {"key": "models.llm.scaling", "name": "规模法则与扩展", "parent": "models.llm", "keywords": ["scaling law", "emergent ability", "compute-optimal", "chinchilla", "scaling"]},
    {"key": "models.llm.align", "name": "对齐与安全", "parent": "models.llm", "keywords": ["alignment", "rlhf", "dpo", "safety", "jailbreak", "red team", "constitutional ai"]},
    {"key": "models.vlm", "name": "多模态模型", "parent": "models", "keywords": ["vision-language", "multimodal", "vlm", "clip", "diffusion model", "text-to-image", "audio model", "多模态"]},
    {"key": "models.slm", "name": "小模型与蒸馏", "parent": "models", "keywords": ["distillation", "small language model", "slm", "quantization", "pruning", "model compression", "蒸馏", "量化"]},
    {"key": "models.rl", "name": "强化学习模型", "parent": "models", "keywords": ["reinforcement learning", "reward model", "policy gradient", "ppo", "world model", "强化学习"]},
    {"key": "models.reasoning", "name": "推理与思维链", "parent": "models", "keywords": ["chain-of-thought", "reasoning", "test-time compute", "o1", "self-consistency", "思维链", "推理"]},

    # ============ 根:算法 ============
    {"key": "algorithm", "name": "算法", "parent": None, "keywords": []},
    {"key": "algorithm.opt", "name": "优化算法", "parent": "algorithm", "keywords": ["optimization", "adam", "sgd", "gradient descent", "learning rate schedule", "warmup"]},
    {"key": "algorithm.training", "name": "训练策略", "parent": "algorithm", "keywords": ["fine-tuning", "sft", "instruction tuning", "curriculum learning", "few-shot", "in-context learning", "微调"]},
    {"key": "algorithm.sampling", "name": "采样与解码", "parent": "algorithm", "keywords": ["beam search", "top-k", "top-p", "temperature", "nucleus sampling", "speculative decoding", "解码"]},
    {"key": "algorithm.retrieval", "name": "检索与知识增强", "parent": "algorithm", "keywords": ["retrieval-augmented", "rag", "vector database", "embedding retrieval", "knowledge graph", "检索增强"]},
    {"key": "algorithm.eval", "name": "评测基准", "parent": "algorithm", "keywords": ["benchmark", "evaluation", "mmmu", "mmlu", "human-eval", "metric", "评测"]},
    {"key": "algorithm.agent", "name": "智能体算法", "parent": "algorithm", "keywords": ["agent", "tool use", "function calling", "planning", "multi-agent", "autonomous", "智能体", "工具调用"]},
    {"key": "algorithm.gen", "name": "生成与扩散", "parent": "algorithm", "keywords": ["generative model", "diffusion", "flow matching", "gan", "vae", "autoregressive", "生成模型", "扩散模型"]},

    # ============ 根:基础设施 ============
    {"key": "infra", "name": "基础设施", "parent": None, "keywords": []},
    {"key": "infra.training", "name": "训练系统", "parent": "infra", "keywords": ["distributed training", "data parallelism", "tensor parallelism", "pipeline parallelism", "fsdp", "deepspeed", "megatron", "训练系统"]},
    {"key": "infra.inference", "name": "推理系统", "parent": "infra", "keywords": ["inference serving", "vllm", "tensorrt", "pagedattention", "continuous batching", "kv cache", "推理加速"]},
    {"key": "infra.gpu", "name": "异构计算与硬件", "parent": "infra", "keywords": ["gpu", "tpu", "npu", "cuda", "rocm", "cxl", "hbm", "accelerator", "异构计算"]},
    {"key": "infra.scheduling", "name": "调度与资源管理", "parent": "infra", "keywords": ["scheduler", "cluster", "kubernetes", "job scheduling", "auto-scaling", "调度"]},
    {"key": "infra.data", "name": "数据工程", "parent": "infra", "keywords": ["data pipeline", "data curation", "deduplication", "data quality", "data lake", "dataloader", "数据清洗"]},
    {"key": "infra.net", "name": "网络与存储", "parent": "infra", "keywords": ["rdma", "nvlink", "infiniband", "ethernet", "distributed filesystem", "checkpoint", "网络"]},
    {"key": "infra.serving", "name": "服务化与 MLOps", "parent": "infra", "keywords": ["mlops", "model serving", "api gateway", "observability", "experiment tracking", "模型服务"]},
]
