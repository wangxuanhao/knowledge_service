# 依赖、循环导入与冷启动评估

日期：2026-10-28。环境：Windows、Python 3.12、既有 `llm_model`。检查基线为 `fbc7c15`，同时保留本轮检索/问答冷启动修复。未安装、升级、卸载共享环境中的包。

后续优化已落地：[专用推理依赖拆分](2026-10-28-embedding-runtime-optimization.md)。真实服务的依赖导入已从 55.70 秒降为 1.94 秒，整体模型预热从 62.38 秒降为 22.94 秒。下面的依赖数量、版本实验及慢导入剖析是拆分前的审计快照；现在有独立的 `local-onnx`/`local-export` 入口，锁文件也已补齐对应依赖声明。

## 结论与已落地优化

依赖导入约 64 秒属于需要优化的冷启动成本，不应由用户的第一条检索请求承担。独立进程只导入 SentenceTransformers、不导入本项目、不加载模型权重，也能复现数十秒耗时，因此没有证据将其归因为项目内部循环导入。权重加载另计，原服务实测 6.13 秒。

已将模型初始化移至启动预热，查询在模型初始化期间快速、明确地返回状态，避免排队等待模型锁；详情见[原故障与真实服务复测](2026-10-28-search-qa-cold-start.md)。这解决请求卡死，不能等同于将模型初始化缩短为几秒。

本轮另修复根包入口的提前加载：此前 `import knowledge_service`，甚至只导入时间/配置/脱敏工具，都会加载 API、服务、仓储等应用模块。现在四个公开导出通过 PEP 562 按需加载、缓存，保留原对象身份、`__all__`、星号导入和 `dir()` 可发现性。该优化减少轻量工具与测试的导入成本；真正启动 API 或模型时仍须支付相应依赖成本。

使用同一解释器的两个新进程，以 `-I -B` 分别加载未修改 HEAD 导出快照和当前工作区，只计 `import knowledge_service` 阶段：

| 包入口 | 导入耗时 | 已加载项目模块 | 全部已加载模块 |
| --- | ---: | ---: | ---: |
| 未修改 HEAD | 9.200274 秒 | 56 | 1536 |
| 当前工作区 | 0.006973 秒 | 1 | 84 |

这是单次对照，不是服务整体启动耗时。机器可读结果与探针在 `.benchmarks/package-boundary-result.json` 和 `benchmark-package-boundary.py`。

README 新部署命令补上 `ocr` extra，确保显式安装 ONNX Runtime；同时说明 `pip install -e` 不消费 `uv.lock`，就绪检查应使用 `/readyz`。

## 项目内部循环导入

覆盖 `knowledge_service/**/*.py` 的 148 个生产 Python 模块；全部成功解析。代码图已有索引，但其 IMPORTS 遍历没有返回有效边，且路径覆盖不完整，因此补用 AST 静态导入审计。测试、脚本与第三方源码不计入下列模块数量。

| 口径（优化前快照） | 导入记录 | 去重模块边 | 非平凡强连通分量 |
| --- | ---: | ---: | ---: |
| 仅显式顶层导入 | 314 | 304 | 0 |
| 顶层 + 函数内延迟导入 | 485 | 439 | 5，涉及 16 个模块 |

5 组相互依赖为：

1. `repository.core` 与 assertions / ingest / provenance / qa_conversations / review stores：仓储绑定子 store，子 store 又使用 core 的 `_json`。
2. `repository.generation_cleanup` / `projection_receipts` / `projection_store`：投影清理、回执及租约异常相互使用。
3. `services.governance` / `reconciliation` / `service`：业务门面与治理、融合编排相互使用。
4. `services.ontology` / `ontology_operations`：本体门面与操作、记录辅助函数相互使用。
5. `services.retrieval` / `scoped_ranking`：检索编排与 RRF 排名辅助函数相互使用。

每组环都至少需要一条函数内延迟导入边；没有包含 embedding 模块。不能说“项目完全没有循环依赖”，也没有发现启动时直接循环导入造成的死锁或部分初始化错误。Python 会缓存已导入的模块，这些延迟引用不意味着每个请求重新加载全部依赖。静态分析不保证覆盖任意动态导入。

优化后再次扫描仍为 148 个生产模块、0 组顶层环、5 组延迟依赖环。显式顶层导入降为 311 条记录/301 条边；根包的静态顶层依赖闭包由 51 个模块降为 1 个。当前快照见 `.benchmarks/import-cycle-audit-20261028-current/results.json`；静态闭包数量与实际导入观测的模块数量采用不同口径。

本轮没有为消除统计数字而广泛改动仓储和治理事务。后续按模块重构时，可将序列化函数、投影公共异常、RRF 排名和本体记录辅助函数下沉到无上层依赖的叶子模块，再让调用双方共同依赖；需分别用事务、检索和本体回归验证。

审计证据：`.benchmarks/import-cycle-audit-20261028-readonly/results.json` 与同目录 `audit_imports.py`。其中把隐式包初始化也保守加入的扩大强连通组不代表实际存在同等数量的运行时循环。

## 第三方依赖与环境健康

| 范围 | 数量 | 元数据校验结果 |
| --- | ---: | --- |
| pyproject 基础与 extras 的直接声明 | 31 | 全部安装且满足声明 |
| 基础依赖闭包 | 35 | 无缺失、无版本冲突 |
| 所有运行能力依赖闭包 | 148 | 无缺失、无版本冲突 |
| 包含测试的依赖闭包 | 153 | 无缺失、无版本冲突 |
| 共享环境安装发行包 | 886 | 存在与项目无关的旧依赖冲突 |

闭包按当前 Windows/Python 3.12 环境标记与 extras 传播计算。元数据满足不等于所有功能都经过运行验收。

全环境 `pip check` 的冲突主要涉及旧 LangChain、Chromadb、Gradio、OpenTelemetry 等共享包，不属于上述项目选定依赖闭包的冲突。因此不能直接认定当前项目因缺包或版本冲突而卡住，也不能把全环境简单卸载重装。

14 个 `.pth` 文件中有 6 个注入无关 editable 项目路径，存在路径遮蔽与环境污染风险；尚未证明它们是本次耗时的原因。未更改这些共享配置。

应用直接加载重型组件的边界总体已延迟：SentenceTransformers 在本地编码器初始化，Docling/Semantica 解析器在对应解析路径，RapidOCR 在 OCR 初始化，Neo4j 在 driver 创建时加载。Milvus 适配模块自身顶层导入 pymilvus；不能因此认定每次查询都会重新导入它。

## 锁文件与可复现性

当前安装有 81 个发行包版本与 `uv.lock` 不同。核心差异如下：

| 依赖 | 实际 llm_model | uv.lock |
| --- | --- | --- |
| sentence-transformers | 5.2.3 | 6.0.1 |
| transformers | 4.55.0 | 5.17.0 |
| tokenizers | 0.21.4 | 0.23.2 |
| torch | 2.8.0 | 2.14.0 |
| huggingface-hub | 0.34.3 | 1.31.0 |

README 的 pip 安装方式不会使用 uv.lock。锁文件中的解析组合不是本次已运行验证的组合。应将“依赖约束允许安装”与“经过真实模型/解析回归的部署组合”分开看待，不直接对共享环境执行整体同步或升级。

当前 torchvision 0.23.0、torchaudio 2.8.0 精确依赖 Torch 2.8.0；Docling 需要 accelerate、torchvision，不能为了减少导入而直接删除它们。`datasets==4.0.0` 不在本项目闭包中，但安装后被 SentenceTransformers 的训练器路径条件加载，是推理进程额外负担的实例。

## 冷启动测量与候选实验

原真实服务：依赖导入 63.99 秒、权重加载 6.13 秒、预热合计 70.99 秒。独立导入实验约 40.20 秒；带 cProfile 的连续分段为 Torch 12.10 秒、Transformers AutoModel/AutoTokenizer 28.16 秒、SentenceTransformers 其余导入 8.01 秒。分段总计 48.28 秒，不是对原 63.99 秒的精确分账。

剖析显示 Transformers 动态构造模型导出映射时大量扫描目录与源码；整体有 18,295 次文件状态查询、7,014 次最终路径解析和 3,712 次代码文件打开。SentenceTransformers 顶层还加载交叉编码器、稀疏编码器与多个 Trainer。原生 Torch DLL 加载约 0.7 秒，不能将数十秒全归因于 CUDA 或 DLL。

在不修改环境的情况下，将官方 wheel 校验 SHA256 后解压至 `.benchmarks/dependency-overlays`，仅为新子进程临时覆盖 Transformers/Tokenizers 路径，测量纯 SentenceTransformers 导入：

| 实验 | Transformers | Tokenizers | 导入耗时 |
| --- | --- | --- | ---: |
| 当前安装组合复测 | 4.55.0 | 0.21.4 | 53.66 秒 |
| 候选旧版 overlay | 4.51.3 | 0.21.4 | 63.30 秒 |
| 候选新版 overlay | 4.57.6 | 0.22.2 | 58.85 秒 |

这些是不同进程的单次实测，overlay 首次导入还包含生成字节码等差异，不能用于精确比较版本优劣；足以说明尚未得到可靠提速证据。候选未做真实模型、向量等价与解析验收，未替换生产环境、未写入版本约束。直接改成子模块导入仍会执行父包 `__init__`，也不是已验证的优化。

## 剩余优化方向与验收条件

1. 部署先以 `/readyz` 就绪结果接流量，保留一个常驻编码器实例，避免短生命周期进程重复加载。启动预热与依赖/权重分段日志已落地。
2. 将部署依赖限定为本项目实际运行能力，按服务职责拆出推理部署组合，避免顺带训练、数据分析与其他项目的包；需先记录实际依赖与 `.pth` 来源，不能在本共享 conda 环境中盲删。遵守本仓库不新建 `.venv` 的约束。
3. 在受控组合中比较冷启动中位数、多次运行及真实 BGE-M3 编码；通过向量维度、归一化、同文本相似度和已有索引检索回归后再固定版本。ONNX 或独立推理服务是进一步方案，当前没有预生成 ONNX 权重，也没有证据证明切换后仍与现有向量一致。
4. 随模块重构逐个拆开上述 5 组延迟依赖环，防止以后把延迟引用上移为顶层而引入循环错误。此项有架构收益，不声称能消除第三方导入的数十秒。

上述审计之后已完成专用推理路径：支持并已准备的 BGE-M3 在线不再导入完整模型栈。其他未准备的通用模型仍兼容旧路径；模型和制品全量校验、加载也仍有实际成本，不能将约 2 秒依赖导入等同于整个服务约 2 秒启动。

原始证据位于 `.benchmarks/dependency-manifest-audit.json`、`import-breakdown.json` / `.pstats`、`import-overlay-*.json`；临时 overlay 仅用于实验，不参与应用正常启动。

## 本轮验证范围

```powershell
python tests/service/test_package_import_boundary.py -v
python -m pytest -q tests/service/test_embedding_runtime_ready.py tests/service/test_runtime_capabilities.py tests/service/test_embedding_model_relocation.py
```

导入边界测试 4 项通过，初始化/健康状态/向量模型迁移专项 16 项通过。边界测试在旧实现上先因 `unexpected eager import: knowledge_service.api` 失败，再验证延迟导出实现通过。另经独立只读代码审查，未发现 P1/P2 问题。

实际服务已按原启动参数、工作目录及私有环境重启，新进程为 43880。预热阶段 `/readyz` 返回 503，完成后返回 200，database / migrations / embedding / semantic 均为 ready。本次日志为依赖导入 55.70 秒、权重加载 6.02 秒、预热总计 62.38 秒；与前次不同负载下的测量存在波动，不将该差值都归因于包入口优化。证据在 `.benchmarks/dependency-service-readiness.json` 和 `dependency-service.stderr.log`。

未宣称全量 Python 回归通过：前轮已确认 `test_vector_dimension.py` 的 7 项旧 Milvus 接口/夹具失败在未修改 HEAD 上同样存在，见[原排查记录](2026-10-28-search-qa-cold-start.md)。本轮未改动前端，也未重复运行此前已通过的 718 项 Node 契约。
