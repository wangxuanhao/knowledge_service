# Semantica 要素提取完整教程

> 基于 semantica 0.6.7，从零构建知识图谱要素提取管线

## 📁 目录结构

```
semantica_demo/
├── README.md                       # 本文件，完整教程
├── 01_基础_本地模式提取.py          # 纯本地规则提取（无需 API Key）
├── 02_进阶_LLM模式提取.py          # LLM 驱动的高质量提取
├── 03_本体Ontology设置.py          # 本体定义 + SHACL 约束验证
├── 04_知识图谱可视化.py            # 生成可交互 HTML 图谱
├── 05_完整管线_长文本要素提取.py   # 手写端到端流程（初版）
├── 06_推理引擎_知识推理与演绎.py   # 规则/本体/图算法推理
├── 07_归一化normalize.py          # 文本/实体/日期/数字/语言/编码归一化
├── 08_冲突检测conflicts.py        # 多源冲突检测 + 解析策略
├── 09_溯源provenance.py           # W3C PROV-O 完整溯源
├── 10_向量存储与GraphRAG.py       # FAISS 向量 + 图混合检索
├── 11_管线编排pipeline.py         # PipelineBuilder DSL 编排
├── 12_全流程串联.py                 # 8步端到端管线（全真实模块）
├── 13_生产级完整管线.py             # ⭐ 对标 ARCHITECTURE.md 12阶段生产级
├── gen_context_graph.py            # 生成 Explorer 用的 ContextGraph
├── gen_explorer_data.py            # 生成 Explorer 数据（备用）
├── probe_api.py                    # API 探针（验证真实接口）
├── context_graph.json              # Explorer 图谱数据
├── ontology_rdflib.ttl             # 本体文件（Turtle 格式）
├── graph_basic.html                # 基础知识图谱可视化
├── graph_chinese_tech.html         # 中文科技公司图谱
├── graph_hierarchical.html         # 层级结构图谱
├── sample_docs/                    # 13 脚本的输入+输出
│   ├── doc1_nev.txt, doc2_battery.txt, doc3_companies.txt
│   ├── context_graph.json          # ContextGraph 持久化
│   └── export/                     # LPG / JSON / GraphML / JSON-LD
└── pipeline_output/                # 05 完整管线输出
    ├── extraction_results.json
    ├── entities.csv
    ├── relations.csv
    └── knowledge_graph.html
```

---

## 🔄 Semantica 完整处理流程

```
原始长文本
    │
    ▼
┌─────────────┐
│   Ingest    │  摄取：PDF/网页/数据库/纯文本 → 标准化文本
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Parse/Split│  解析与分块：文档解析 + 实体感知分块
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Normalize   │  归一化：文本清洗 + 实体规范化
└──────┬──────┘
       │
       ▼
┌───────────────────────────────────────┐
│          Semantic Extract             │  语义提取（核心）
│  ┌─────────┬──────────┬───────────┐  │
│  │   NER   │ Relation │  Events   │  │
│  │  实体   │  关系    │  事件     │  │
│  └────┬────┴────┬─────┴─────┬─────┘  │
│       │         │           │        │
│  ┌────▼─────────▼───────────▼─────┐  │
│  │    Coreference Resolution      │  │  共指消解
│  │    （同一实体不同名称合并）     │  │
│  └──────────────┬─────────────────┘  │
│                 │                    │
│  ┌──────────────▼─────────────────┐  │
│  │    Deduplication / 实体消歧    │  │
│  └──────────────┬─────────────────┘  │
│                 │                    │
│  ┌──────────────▼─────────────────┐  │
│  │    Triplet / RDF 序列化        │  │
│  └──────────────┬─────────────────┘  │
└─────────────────┼────────────────────┘
                  │
                  ▼
         ┌──────────────────┐
         │  Knowledge Graph │  知识图谱构建
         └────────┬─────────┘
                  │
         ┌────────▼────────┐
         │    Ontology     │  本体约束 + SHACL 验证
         └────────┬────────┘
                  │
         ┌────────▼────────┐
         │    Reasoning    │  规则推理 / Datalog / 图算法
         └────────┬────────┘
                  │
         ┌────────▼────────┐
         │   Provenance    │  溯源追踪（W3C PROV-O）
         └────────┬────────┘
                  │
         ┌────────▼────────┐
         │   Visualization │  可视化 / Explorer
         └─────────────────┘
```

---

## 🧩 提取方式对比：本地模式 vs LLM 模式

| 维度 | 本地模式 (pattern/rule) | LLM 模式 (llm) |
|------|------------------------|----------------|
| **原理** | 正则表达式 + 语法规则 | 大语言模型理解语义 |
| **API Key** | ❌ 不需要 | ✅ 需要 |
| **速度** | ⚡ 极快（毫秒级） | 🐢 较慢（秒级，取决于模型） |
| **成本** | 免费 | 按 token 计费 |
| **准确率** | 中等（受限于规则覆盖） | 高（能理解上下文和歧义） |
| **泛化能力** | 弱（只能匹配预定义模式） | 强（能处理任意领域实体） |
| **可复现性** | ✅ 100% 确定 | ❌ 有随机性 |
| **适用场景** | 已知模式、高吞吐、结构化数据 | 复杂文本、开放域、高质量要求 |

### 推荐策略：混合使用
- 先用本地模式快速提取高置信度实体
- 再用 LLM 模式补充复杂关系和事件
- 最后用规则做后处理和校验

---

## 🏗️ 如何设置本体 (Ontology)

本体是知识图谱的"Schema"，定义：
1. **实体类型**（类）及其层级关系
2. **属性**及其类型约束
3. **关系类型**及其定义域/值域

### 在 Semantica 中设置本体的三种方式：

#### 方式一：编程式定义（代码中直接创建）
```python
from semantica.ontology import OntologyManager

ont = OntologyManager()
ont.add_class("Company", parent="Organization")
ont.add_class("Person")
ont.add_property("founder", domain="Company", range="Person")
```

#### 方式二：从 OWL/RDF 文件导入
```python
ont = OntologyManager()
ont.import_ontology("my_ontology.owl", format="xml")
```

#### 方式三：自动生成（从提取结果反推）
```python
ont = OntologyManager()
ont.auto_generate_from_entities(entities)
```

### SHACL 约束验证
SHACL (Shapes Constraint Language) 用于验证图谱数据是否符合本体约束：
```python
from semantica.ontology import SHACLValidator

validator = SHACLValidator(shapes_graph=my_shapes)
results = validator.validate(kg)
print(f"违规数量: {len(results.violations)}")
```

---

## 📊 知识图谱可视化方式

### 方式一：内置 Explorer（推荐）
```bash
pip install "semantica[explorer]"
semantica-explorer --graph my_graph.json
# 浏览器打开 http://127.0.0.1:8000
```

### 方式二：导出 + 第三方工具
- **GraphML** → Gephi / Cytoscape 桌面版
- **DOT** → Graphviz
- **JSON** → 自定义前端（Sigma.js / D3.js / vis.js）

### 方式三：Python 直接生成 HTML（本教程用的方式）
使用 Sigma.js 或 vis-network 生成单文件 HTML，双击即可查看。

---

## 🧩 Semantica 完整模块覆盖情况（已全量验证真实 API）

| 模块 | 覆盖状态 | 所在脚本 | 说明 |
|------|---------|---------|------|
| `semantic_extract` | ✅ 完整覆盖 | 01, 02, 05, 12 | NER/关系/事件/共指/三元组 |
| `normalize` | ✅ 完整覆盖 | 07, 12 | 文本/实体/日期/数字/语言/编码归一化 |
| `split` | ✅ 完整覆盖 | 12 | TextSplitter 递归/语义分块 |
| `conflicts` | ✅ 完整覆盖 | 08, 12 | 多源冲突检测 + 5种解析策略 |
| `deduplication` | ✅ 覆盖 | 01, 05, 12 | 实体消歧去重 |
| `kg` | ✅ 完整覆盖 | 04, 05, 06, 12 | 图谱构建、图分析、社区检测 |
| `provenance` | ✅ 完整覆盖 | 09, 12 | W3C PROV-O 溯源 + SQLite 持久化 |
| `reasoning` | ✅ 完整覆盖 | 06 | 前向链/本体/图算法推理 |
| `ontology` | ⚠️ 部分覆盖 | 03 | 原生 API 参数差异大，用 rdflib 演示了核心概念 |
| `context` (ContextGraph) | ✅ 覆盖 | Explorer | 官方核心数据结构 + 决策智能 |
| `vector_store` | ✅ 完整覆盖 | 10 | FAISS + GraphRAG 混合检索 |
| `pipeline` | ✅ 完整覆盖 | 11, 12 | PipelineBuilder DSL + ExecutionEngine |
| `embeddings` | ⚠️ 部分覆盖 | 10 | fastembed 需下载模型，用哈希向量演示 |
| `visualization` | ⚠️ 自实现 | 04 | 用 vis-network 自实现（官方 visualization 模块未用） |
| `graph_store` | ❌ 未覆盖 | - | Neo4j/FalkorDB 需本地服务，未演示 |
| `triplet_store` | ❌ 未覆盖 | - | RDF 三元组存储 + SPARQL，未演示 |
| `ingest / parse` | ❌ 未覆盖 | - | 多源摄取/文档解析，未演示 |
| `explorer` | ✅ 可用 | 官方服务 | http://127.0.0.1:8765 |

### 关键坑（国内环境）

**RelationExtractor 会隐式加载 fastembed embedding**，即使 `method="pattern"`。
国内连不上 HuggingFace 时会卡在下载重试（sleep 3/9/27 秒 × 3 次）。
解决：设置环境变量 `HF_HUB_OFFLINE=1`，让 fastembed 立即 fallback。
```python
import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
```

---

## 🎯 要素提取质量控制清单

| 控制手段 | 作用 | 位置 |
|----------|------|------|
| `confidence_threshold` | 过滤低置信度提取结果 | NERExtractor / RelationExtractor |
| 实体消歧 (Deduplication) | 合并同一实体的不同名称 | `semantica.deduplication` |
| 共指消解 (Coreference) | 合并代词/别名到同一实体 | `CoreferenceResolver` |
| SHACL 验证 | 确保数据符合本体约束 | `semantica.ontology.SHACLValidator` |
| 冲突检测 | 发现不同来源的矛盾事实 | `semantica.conflicts` |
| Provenance 溯源 | 每个事实都能追溯到来源 | `semantica.provenance` |

---

## 💡 常见问题

**Q: pattern 模式提取质量太差怎么办？**
A: 这是正常的，pattern 模式只是基于规则的基线。换用 `method="llm"` 可以大幅提升质量，或者使用 spaCy 的 ML 模型 (`method="ml"`)。

**Q: 长文本处理太慢怎么办？**
A: 1) 使用 entity-aware chunking 分块处理；2) 先用 pattern 模式快速过滤，再用 LLM 精修；3) 启用 batch 并行处理。

**Q: 中文文本支持怎么样？**
A: pattern 模式对中文支持有限（规则主要是英文的）。LLM 模式支持中文，选择中文模型即可。也可以自定义中文规则。

**Q: 和 Microsoft GraphRAG 有什么区别？**
A: Semantica 是更底层的知识图谱构建框架，支持确定性推理和溯源，适合需要可问责的场景；MS GraphRAG 更偏向检索增强生成，主打社区摘要和全局搜索。
