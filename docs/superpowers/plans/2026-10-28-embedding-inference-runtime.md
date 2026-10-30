# 本地 embedding 推理依赖拆分实施计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan. Steps use checkbox syntax for tracking.

**Goal:** 消除当前 BGE-M3 在线启动时约 56 秒的完整训练/模型框架导入，保持现有模型、分词、归一化与索引身份兼容。

**Architecture:** 离线准备阶段使用现有 SentenceTransformers/PyTorch 将确切的 XLMRoberta → CLS → Normalize 流水线导出为 FP32 ONNX。在线只使用 ONNX Runtime、Tokenizers、NumPy；不可变制品目录通过原子写入 active.json 启用。初始化时校验源模型、全部 external data 与兼容验证记录，health/query 不重复哈希。没有导出制品的通用模型保留原路径，存在无效制品时明确失败。

**Tech Stack:** 现有 Python 3.12/llm_model、ONNX 1.18、ONNX Runtime 1.20.1、Tokenizers；不创建 venv、不整体更换共享环境。

## Task 1: 证明可行性

- [x] 纯具体 HF 模型导入仍需 47.95 秒；较旧 HF 4.44.2 仍需 41.49 秒，不作为最终优化。
- [x] 纯 ORT/Tokenizers 推理依赖导入实测 1.38 秒。
- [x] 在 `.benchmarks` 导出真实模型，保留原始权重。
- [x] 比较原模型与 ONNX 的 token IDs/mask、动态批次、空白/Unicode/长短文本向量；预设 max_abs <= 1e-4、cosine >= 0.99999、有限值及单位范数门禁。

## Task 2: 轻量在线运行时（独立实施）

文件：`integrations/embedding_artifacts.py`、`integrations/onnx_embeddings.py`、`tests/service/test_onnx_embedding_runtime.py`。

- [x] 用真实微型 ONNX 图与 tokenizer 编写直接 unittest，先红后绿，不启动第二个 pytest。
- [x] 标准库清单校验：路径边界、版本契约、源模型哈希、制品哈希、FP32/CPU/opset/验证门禁。
- [x] 实现与 LocalEncoder 兼容的 encode/get_dimension；保留顺序、strip、8192 截断和动态 padding，长序列限制每批数量。
- [x] 子进程阻止 Torch/HF/ST 导入，验证仍可执行真实 ORT。

## Task 3: 离线准备与启用

文件：`integrations/embedding_export.py`、`scripts/prepare_embedding_runtime.py`、`tests/service/test_embedding_export_contract.py`。

- [x] 先用无重型模型的测试验证只允许已支持流水线、拒绝自定义投影/路由/提示配置与越界路径。
- [x] 导出到新版本目录；同原模型比较 token、短长批次/单条向量后，记录验证指标、全部源/制品 SHA256。
- [x] 以 os.replace 原子发布 active.json，失败不改旧活动清单，不覆盖原模型/旧版本。
- [x] CLI 仅准备模型，不连接业务库、不改 .env；重型导入只存在准备路径。

## Task 4: 接入、依赖声明与验收

文件：`integrations/embeddings.py`、`pyproject.toml`/`uv.lock`、README/脚本索引/审计文档、现有 readiness 测试。

- [x] LocalEncoder 在存在有效 active.json 时选择 ORT；无制品保留 ST；显式 ONNX 缺制品或制品过期明确报错。
- [x] 将在线 ONNX extra 与离线导出 extra 分开，不升级共享环境。
- [x] 单一 pytest 会话跑相关专项；独立审查后修复问题。
- [x] 新进程测依赖、哈希+session、首次推理总耗时；重启真实服务复测“未成年”、就绪状态及索引查询。
- [x] 报告实测改善、剩余限制和可回退配置；未获本轮提交指令前不提交工作区改动。

## 验证边界

当前机器可用内存约 6 GiB，真实模型本身约 2.27 GB，不并行运行多个大模型。8192 附近分词截断必须逐 token 对齐；超长序列的实际推理性能需单列，不冒充已做全长容量验收。禁止量化/FP16 来冒充等价 FP32 优化。
