# 知识图谱服务

> 更新日期：2026-09-20。基于 FastAPI 标准分层重构：`api / services / repository / core / utils / integrations`。历史旧版脚本（apps/legacy/kgcore/ontology）已移除，需要时可从 git 历史恢复。尚未接入的能力见下文，能力对标记录见 [能力对标与修正](docs/2026-09-09-能力对标与修正.md)。

独立服务层 `knowledge_service/`：双时态版本、本体/SHACL、嵌套 metadata 前置过滤、检索与证据问答，包含管理工作台。

可选 [Neo4j 接入](docs/neo4j接入.md)：PostgreSQL 是唯一业务真值库，按项目同步图谱与历史版本；不替换现有检索。

---

## 架构总览

服务采用 **FastAPI 标准分层**，依赖方向严格单向向下（`api → services → repository`，`core/utils` 为基础设施，`integrations` 为外部系统适配），无循环依赖：

```
                     ┌──────────────────────────────────────────────┐
                     │             api/（HTTP 路由层）               │
                     │  create_app 装配 · 按资源拆分：               │
                     │  projects/knowledge/workspace/parity/        │
                     │  evidence/reviews/ontology_changes/discovery │
                     └───────────────┬──────────────────────────────┘
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        ▼                            ▼                            ▼
 ┌──────────────┐          ┌──────────────────┐          ┌────────────────┐
 │ services/    │          │  integrations/   │          │ repository/    │
 │（业务服务层） │          │（外部系统适配）   │          │（PostgreSQL 存储层）│
 │ service      │          │  embeddings      │          │ core（主存储）  │
 │ retrieval    │          │  milvus_store    │          │ assertions_store│
 │ ontology     │          │  neo4j_store     │          │ ingest_store   │
 │ explorer     │          │  semantica_adapter│         │ review_store   │
 │ governance … │          └────────┬─────────┘          └───────┬────────┘
 └──────────────┘                   │                            │
        │                           │                            │
        └───────────────┬───────────┴────────────────────────────┘
                        ▼
              ┌────────────────────┐
              │  core/ · utils/    │  基础设施：config/logging/time
              │  （最底层，无依赖） │  + 工具：diagnostics/filters/
              └────────────────────┘  assertions/ingest_runs
```

**统一出口**：外部统一从 `knowledge_service` 导入，不关心内部路径。

```python
from knowledge_service import create_app, Repository, KnowledgeService
```

**存储职责**：

| 存储 | 位置 | 角色 |
|---|---|---|
| PostgreSQL | `repository/` | **唯一真相源**：双时态版本、本体版本链、制品、任务收据 |
| Milvus | `integrations/milvus_store.py` | 派生检索索引（dense + sparse），可经 `/indexes/rebuild` 重建 |
| Neo4j | `integrations/neo4j_store.py` | 可选图谱投影副本，按项目手动同步 |
| 编码器 | `integrations/embeddings.py` | 本地 bge-m3 / OpenAI 兼容 / demo 哈希 |

PostgreSQL 是真值，Milvus / Neo4j 是可重建副本——副本丢失或损坏不影响权威数据。
schema 由编号 SQL 迁移管理（`repository/migrations/` + `migrate.py`），应用进程**不建表**：
启动时只校验迁移版本，漏跑迁移会明确报错。

---

## 目录地图

```
knowledge_service/
├── api/                        # ⚡ HTTP 路由层（统一 install(app, service) 挂载）
│   ├── __init__.py             #   create_app 装配（lifespan/中间件/异常/路由挂载/静态）
│   ├── projects.py             #   项目管理（列表/创建/查看/改名/删除）
│   ├── knowledge.py            #   知识 CRUD + 摄取 + 检索/问答/图谱 + 本体/SPARQL
│   ├── workspace.py            #   工作台 API（探索/实体选项/时间线/本体维护/索引任务）
│   ├── parity.py               #   旧版兼容 API（本地项目导入/预览/快照）
│   ├── evidence.py             #   证据定位
│   ├── reviews.py              #   知识审核
│   ├── ontology_changes.py     #   本体变更草案
│   └── ontology_discovery.py   #   开放本体发现 API（候选/脑图/草案/发布）
├── services/                   # 🧠 业务服务层（无路由装饰器，纯业务逻辑）
│   ├── service.py              #   KnowledgeService 核心门面（写入/ingest/检索/问答）
│   ├── retrieval.py            #   检索引擎（Milvus 快路径 + 本地降级）
│   ├── ontology.py             #   本体引擎（RDF/OWL/SHACL 解释）+ 术语工具
│   ├── ontology_discovery.py   #   开放候选聚合/归纳/物化（业务函数）
│   ├── explorer.py             #   图谱探索/链接探索/快照/评估
│   ├── governance.py           #   实体治理（别名/合并/删除）
│   ├── answers.py              #   证据问答（SSE 流式）
│   ├── formal_writes.py        #   规范图写入事务门面
│   ├── jobs.py                 #   后台任务队列
│   ├── evidence.py             #   证据定位（业务）
│   ├── reviews.py              #   审核决策（业务）
│   ├── ontology_changes.py     #   本体变更（业务：apply/impact/revalidation）
│   ├── legacy_import.py        #   旧项目导入
│   ├── chunking.py             #   切片
│   ├── attribute_extraction.py #   属性抽取
│   ├── entity_resolution.py    #   实体消歧
│   ├── reconciliation.py       #   增量实体融合
│   └── review_validation.py    #   审核属性校验
├── repository/                 # 💾 PostgreSQL 存储层
│   ├── core.py                 #   主存储：项目/记录/本体/制品/FTS/查询
│   ├── connection.py           #   连接池 / unit-of-work · resolve_dsn（DSN 唯一出处）
│   ├── pg_engine.py            #   SQLite→PG 方言适配层（占位符/字面 % 转义 + 残留语法拦截）
│   ├── migrate.py              #   编号迁移执行器（校验和台账 + advisory lock）
│   ├── migrations/             #   编号 SQL 迁移（0001_core.sql …）
│   ├── assertions_store.py     #   断言（审核候选）生命周期
│   ├── ingest_store.py         #   摄取运行与阶段输出
│   └── review_store.py         #   消歧审核与合并账本
├── integrations/               # 🔌 外部系统适配
│   ├── embeddings.py           #   向量编码器
│   ├── milvus_store.py         #   Milvus 检索索引
│   ├── neo4j_store.py          #   Neo4j 图谱投影
│   └── semantica_adapter.py    #   Semantica 抽取适配
├── core/                       # ⚙️ 基础设施
│   ├── config.py               #   环境配置（load_environment）
│   ├── logging.py              #   统一日志配置（终端 + 文件落盘）
│   └── time.py                 #   时间工具
├── utils/                      # 🧰 工具
│   ├── diagnostics.py          #   诊断：任务阶段追踪 + 读路径计时
│   ├── filters.py              #   Metadata 过滤谓词
│   ├── assertions.py           #   断言常量/工具
│   └── ingest_runs.py          #   摄取就绪位工具
├── models.py                   # 📦 Pydantic 请求模型
├── web/                        #   前端静态资源（工作台）
├── resources/                  #   默认本体 default_ontology.ttl
├── __init__.py                 #   统一出口
└── __main__.py                 #   启动入口（python -m knowledge_service）
```

## 启动服务

在仓库根目录执行（当前使用 conda 环境 `model_agent`，Python 3.13，Semantica 0.7.x）：

```powershell
conda activate model_agent
python -u -m knowledge_service --port 8100
```

先确保基础设施已就绪（PostgreSQL 必须先在跑，否则服务拒绝启动）：

```powershell
docker compose up -d --wait
python -m knowledge_service.repository.migrate    # 应用待执行迁移（幂等）
```

工作台：[http://127.0.0.1:8100/](http://127.0.0.1:8100/)；[交互 API 文档](http://127.0.0.1:8100/docs)。默认只监听本机，启动终端按 `Ctrl+C` 停止。修改后端代码或 `.env` 后需要重启；前端修改需要刷新页面，刷新前请保留未提交内容。

> **注意**：后端 Python 改动需重启服务（uvicorn 无 --reload）；前端静态文件改动后需同步 bump `index.html` 里该文件的 `?v=` 版本号，否则浏览器用缓存旧 JS。

### 配置与存储

- 模型配置写入仓库根目录 `.env`；启动时读取 `KG_` 变量，已设置的 shell 环境变量优先。不要提交或分享真实密钥。
- 抽取和生成式证据问答使用 `KG_LLM_BASE_URL`、`KG_LLM_MODEL`、`KG_LLM_API_KEY`，支持 DeepSeek 的 OpenAI 兼容接入，可能产生模型调用费用。
- 本地向量模型使用 `KG_EMBEDDING_BACKEND=local`、`KG_EMBEDDING_PATH`（默认 `data/model`）。本地模式不需要远程 embedding 的 URL / API Key。
- `--demo` 使用非语义哈希向量，仅用于流程验证，不代表真实语义检索，也不会自动关闭 LLM 抽取。
- 权威存储是 PostgreSQL（容器 `knowledge-postgres`，端口 5432）：项目、原文、知识版本、任务收据等都在库里；schema 由编号迁移管理（`repository/migrations/`），应用启动只校验版本、不建表。
- 应用以**应用角色** `knowledge_app` 连库（无 DDL 权限）；建表/建索引必须用迁移器 `python -m knowledge_service.repository.migrate`，它使用 owner 凭据。应用进程读不到超级用户口令（最小权限）。
- 向量检索必须显式 `KG_VECTOR_BACKEND=milvus` 才开启（Docker 容器 `milvus-standalone`，端口 19530）。向量已不再存于关系库的本地列；未开启 Milvus 时**没有**语义通道，检索自动降级为关键词（PostgreSQL `pg_trgm` + 全文）。
- 基础设施用仓库根目录 `docker-compose.yml` 一条命令拉起：`docker compose up -d --wait`。包含 PostgreSQL、独立 etcd、minio、Milvus standalone 与 Attu（<http://127.0.0.1:30001>）五个容器，全部端口只绑 `127.0.0.1`，持久化数据统一落在 `./volumes`。
- Milvus **不使用内嵌 etcd**：元数据由独立的 `milvus-etcd` 承担，避免 Milvus 与 etcd 争抢同一数据目录与 2379 端口。维护与迁移见 `docs/2026-10-01-Docker基础设施维护手册.md`，一键验证 `bash scripts/verify-compose-stack.sh`。
- Neo4j 是按项目手动同步的图谱副本，检索仍走本地。多个项目可共用一个 database，通过项目标识隔离；切换页面项目不会自动同步。
- 配置只从 `.env` 加载（全部 `KG_` 前缀键）；`.env.example` 仅是参考模板，不作为配置源。shell 环境变量优先级最高，已设置的键不会被 `.env` 覆盖。

### 环境变量一览

| 变量 | 用途 | 默认 |
|---|---|---|
| `KG_DATABASE_URL` | 应用连接串（PostgreSQL）；留空则由下两行组装 | 空 |
| `POSTGRES_DB` / `POSTGRES_APP_USER` / `POSTGRES_APP_PASSWORD` | 应用角色连库信息（组装 DSN 用，**不含**超级用户口令） | `knowledge` / `knowledge_app` / — |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | **仅迁移器**使用的 owner 凭据（应用进程读不到） | — |
| `KG_DATABASE_HOST` / `KG_DATABASE_PORT` | PostgreSQL 地址 / 端口 | `127.0.0.1` / `5432` |
| `KG_MIGRATION_DATABASE_URL` | 迁移器连接串（建表需 DDL 权限）；留空回退 `POSTGRES_USER/PASSWORD` | 空 |
| `KG_LLM_BASE_URL` / `KG_LLM_MODEL` / `KG_LLM_API_KEY` | LLM 抽取与问答 | — |
| `KG_EMBEDDING_BACKEND` | `local` / `openai` / `demo` | `local` |
| `KG_EMBEDDING_PATH` | 本地 bge-m3 模型目录（backend=local） | `data/model` |
| `KG_EMBEDDING_BATCH_SIZE` | 编码批大小 1..64（backend=local） | `4` |
| `KG_EMBEDDING_BASE_URL` / `KG_EMBEDDING_MODEL` / `KG_EMBEDDING_API_KEY` | OpenAI 兼容 embedding 提供商（backend=openai） | — |
| `KG_VECTOR_BACKEND` | `milvus` 开启 Milvus 向量索引 | 关闭（无向量后端） |
| `KG_MILVUS_HOST` / `KG_MILVUS_PORT` / `KG_MILVUS_DIM` | 本地 Milvus 地址 / 端口 / 向量维度 | `localhost` / `19530` / `1024` |
| `KG_LOG_LEVEL` | 终端日志级别（文件恒为 DEBUG） | `INFO` |
| `KG_SLOW_MS` | 只打印慢于该毫秒的读路径阶段 | `0` |
| `KG_DOCUMENT_OCR_MODE` | 文档 OCR：`auto` / `disabled` | `auto` |
| `KG_NEO4J_URI` / `KG_NEO4J_USERNAME` / `KG_NEO4J_PASSWORD` / `KG_NEO4J_DATABASE` | Neo4j 投影 | — |

---

## 知识写入与批量解析

1. 选择项目，并根据知识成熟度选择“项目本体”“开放本体发现”或“仅文档检索”。
2. 在“知识写入”上传 `.txt`、`.md`、`.pdf`、`.docx`、`.html` / `.htm` 文件，或粘贴正文，二选一。TXT / Markdown 必须为 UTF-8；其余格式由 Semantica 的 `DoclingParser` 转为 Markdown。
3. 支持拖放与多选文件。单文件最大 25 MB，单次最多 20 个、合计不超过 100 MB。单文件可填写标题；多文件分别以安全文件名为标题，保留独立来源。发布时间和 Metadata 是本批文件共用的，不同时请分批上传。
4. 展开“解析设置”，选择切片和消歧参数；可先预览切片。
5. 提交后在“后台任务”查看解析过程；开放发现结果到“本体发现”审阅，不直接污染正式图谱。

**批量边界：**前端逐文件调用 multipart 单文档任务接口，每个文件是独立任务，后端单工作线程依次执行。不是多文件拼接抽取，也不是并行解析；尚无后端统一批次 ID、批次级恢复或硬性总任务超时。已提交成功的行不会重复发送，失败行可重试；已被后端接收的任务不依赖页面继续运行，但页面关闭前尚未提交的文件不会自动续传。

安装文档解析依赖：

```powershell
D:\Anaconda3\envs\model_agent\python.exe -m pip install -e ".[semantica-runtime]"
```

当前锁定 Semantica `>=0.7,<0.8` 与 Docling `>=2.130,<3`。Docling 首次解析某些 PDF 时可能需要本机已有的模型缓存；模型权重由部署者本地安装和管理。`KG_DOCUMENT_OCR_MODE=auto`（默认）为扫描 PDF 保留 OCR 路径，设为 `disabled` 可关闭。上传的原始二进制只进入临时目录，任务结束即删除；异常中断遗留文件会在下次启动时清理（24 小时阈值）。

文件预览使用 `POST /api/projects/{project_id}/documents/upload/preview`，后台解析使用 `POST /api/projects/{project_id}/documents/upload/jobs`，均为 `multipart/form-data`，包含 `file` 与 JSON 字符串 `options`。原有 JSON `/documents/preview`、`/documents/jobs` 和同步 `/documents/upload` 保持兼容。

### 实际处理链路

- **项目本体模式**：原文收据 → 切片 → 逐片实体／关系抽取 → 项目内消歧融合 → 本体与关系校验 → 向量化 → 原子落库。
- **开放本体发现**：原文收据 → 切片 → Semantica 开放实体／关系候选（可选业务属性候选）→ 多批次候选累计 → 基于当前本体版本归纳差异草案 → 人工核对并发布本体版本 → 使用该版本受控重解析原文 → 实体消歧／关系校验后进入正式图谱。开放候选始终与正式知识隔离，不参加图谱检索。
- **仅文档检索**：原文收据 → 切片 → 向量化 → 原子落库；不调用图谱抽取模型。

“候选脑图”用于在发布前观察开放候选的结构：同类型且同名称的实体候选聚合为一个预览节点，重复三元组聚合为候选边，并展示生命周期、出现次数、属性和来源证据。该聚合不创建正式实体 ID，也不参与正式检索；“正式脑图”继续只展示已按本体解析和融合的知识。侧栏按“项目、探索与展示、建模与治理、运行与质量”组织为层级菜单。

项目本体模式会把类的稳定名称、中文标签、定义和父类作为独立的本体指导交给模型；关系抽取同样接收关系定义和 domain/range。指导信息与原文分字段传递，不会作为证据写入图谱。开放模式不使用项目白名单：实体类型和关系名称要求采用原文语言及领域词汇，再由 Semantica `OntologyGenerator` 归纳草案。每个 chunk 单独交给模型，不会在最后拼成整篇再做一次联合抽取。抽取失败会保留原文收据，派生知识不会部分提交。

两种流程的边界如下：约束模式适合本体较稳定的生产写入，无法映射或违反约束的结果进入知识审核；开放模式适合未知领域探索，所有结果先作为候选持续聚合，草案页面按实体类、关系和属性展示名称、定义、实例及相对父版本的差异。人工确认只发布本体契约，不直接发布候选实例。IRI 作为跨版本稳定内部标识，界面默认显示业务标签和局部技术名称。

“发布本体”和“构建实例图谱”是两个阶段：开放候选保留来源文档、片段位置和候选 ID；发布草案时系统只创建带父版本、候选快照和术语差异的新本体版本，不把候选直接写成正式实体或关系。随后用明确选择的本体版本重解析原文，经过实体消歧、关系约束和审核后才写入正式图谱。已经使用旧本体写入的知识继续绑定旧版本，不自动迁移；如需迁移，应另建可审计的迁移任务，不能静默改写历史。

### 可选解析设置

| 选项 | 当前行为 |
|---|---|
| 固定长度＋重叠 | 默认最大 1800 字符、重叠 200；兼容原流程 |
| 段落／句子边界 | 长度窗口内优先寻找段落或句子边界；不是完整递归拆分与合并 |
| 标题／中文条款 | 即使全文未超长也按结构拆分；识别 Markdown 标题、“第一条”“一、”等；有中文上层条款时，数字子清单保留在条款内 |
| 超长条款 | 条款内按最大长度续片并重叠；独立条款之间不重叠 |
| 长度限制 | 最大字符数 100–10000；重叠 0–2000 且必须小于最大字符数；单位不是 token |
| 同名／别名消歧 | 默认开启，要求类型、本体版本、有效期等匹配，保留来源信息 |
| 语义相似自动合并 | 默认关闭；开启需先启用消歧，阈值默认 0.88；存在误合并风险 |
| 关系两端约束 | 默认“审核”：domain/range 冲突转为候选，其余知识继续保存；也可选提示、严格或关闭 |
| 切片预览 | 与实际写入共用后端切片逻辑；多文件预览首份，最多显示前 20 片；不调用 LLM、不写入知识 |

片段保留原文起止字符位置和切片参数，可用于证据定位。语义切片、完整递归切片、自动拼接上级标题、跨片关系补抽尚未接入。

### 关系 domain/range 策略

文档抽取会通过 Semantica Provider 把关系名称、说明和 domain/range 作为独立的本体指导交给模型，原文则放在独立的 `source_document` 字段；本体指导不能作为事实证据。模型必须返回原文证据片段，服务只把原文或由原文定位出的窗口保存为关系证据，并在返回后逐条执行确定性的 domain/range 检查。OWL/RDFS 的 domain/range 本身用于表达语义和推理；是否阻止数据写入由本服务的处理策略决定：

| 模式 | 冲突关系 | 其他合法知识 |
|---|---|---|
| 审核（默认） | 不入图，保存实际/期望端点类型与原文证据，进入知识审核 | 正常提交 |
| 提示 | 允许入图，在关系 metadata 的 `constraint_warning` 留痕 | 正常提交 |
| 严格 | 任一冲突导致该文档处理失败 | 本次派生知识不提交，保留失败收据 |
| 关闭 | 不检查端点类型组合；仍要求关系类型存在且端点实体存在 | 正常提交 |

“提示”和“关闭”并不关闭 SHACL；显式 SHACL 约束仍按本体校验。结构化知识批量写入 API 继续默认严格，避免外部系统静默写入不合规事实。审核批准时也会重新执行严格校验；可映射到合适关系，或先在知识编辑中纠正实体类型后再批准。

---

## 时间与筛选

| 时间 | 含义 |
|---|---|
| `metadata.published_at` | 上传表单的“发布时间”，选填，表示原文对外发布的时间 |
| `metadata.uploaded_at` | 系统接收文档时自动记录的添加时间 |
| `valid_from / valid_until` | 事实的业务有效期，不等于发布时间或添加时间 |
| `recorded_at / superseded_at` | 系统记录与更正知识版本的时间 |

知识写入表单不再统一填写生效／失效时间，发布时间不会自动赋给实体或关系作为有效期。关系抽取会识别原文明示的事实有效期并保存时间证据；没有明确时间信号时保持为空，不会用发布时间猜测。旧接口仍保留有效期字段。文件级更新时间目前也没有统一接入，不能将版本记录时间等同于文件更新时间。

图谱和检索支持项目范围、业务有效时点、系统已知时点及 Metadata 前置筛选。发布时间／添加时间可以作为 Metadata 条件使用，但专用的多时间维度筛选界面、按来源汇总筛选融合实体尚未完整接入。

---

## 日志与问题定位

后台任务按当前项目、每个文档一张卡片展示；切片、实体抽取、关系抽取、审核、校验、向量化等维度各保留最新摘要。重复心跳、完整步骤和异常位置保留在折叠详情中，不会删除底层日志。

“后台任务”和启动终端都能查看：文档信息、切片配置与数量、第几片及原文范围、实体／关系抽取开始与结束、耗时和数量、消歧融合、本体校验、向量化和 PostgreSQL 落库。

- 每条日志有 UTC 时间戳和累计运行秒数；终端以 `[task:任务ID]` 标识。
- 每 30 秒记录等待阶段与工作线程的代码位置。心跳不表示任务有实际进展，也不是自动超时取消。
- 进度百分比是阶段估计，不是 LLM token 处理比例；失败保留失败阶段与异常位置，不再标为 100%。
- 数据库保留每个任务最近 2000 条日志；不记录完整提示词、模型响应或 API Key。
- 重启后原 queued/running 任务标为 interrupted，不自动重试。强制停服前的原文收据可能仍显示 processing；重传前先核对，避免重复数据。
- 尚未逐次记录 Semantica 内部 HTTP 重试和 token 用量；长时间等待需要结合执行位置定位。

### 日志落盘

统一日志配置在 `core/logging.py`：终端 + 文件双通道，文件按天滚动、保留 30 天、存全量 DEBUG：

- 终端级别由 `KG_LOG_LEVEL` 控制（默认 `INFO`）；
- 文件落盘 `data/log/service.log`，任何时候都写 DEBUG（排查问题看完整链路）；
- 幂等配置：测试多次 build app 不会叠加 handler。

### 读接口的阶段耗时日志

写入任务走任务日志；读接口（检索、交互图谱、候选脑图、原文数据源）不属于任何任务，耗时走另一条通道，**默认开启**，直接打印在启动终端：

```
10:33:31 INFO knowledge_service.timing · repository.query · 38.3 ms · embeddings=True kinds=0 filtered=False rows=2401 kept=2401
10:33:31 INFO knowledge_service.timing · service.scoped · 42.3 ms · embeddings=True kinds=0 raw=2401 live=2401 kept=2401
10:33:31 INFO knowledge_service.timing · explorer.graph · 43.5 ms · seeded=True hops=1 rows=2401 graph_nodes=1100 graph_edges=1000 selected=3 returned_nodes=3 returned_edges=2
```

- `KG_LOG_LEVEL`（默认 `INFO`）控制级别；`KG_SLOW_MS=200` 只打印慢于 200 ms 的阶段。
- 单次读取达到 2000 行会额外打 `WARNING`：说明这次请求为**整个项目**付了钱，而不是为返回结果付钱。
- 第三行是判断「慢在哪」的关键：返回 3 个节点、读了 2401 行，说明耗时全在扫描而不是图谱计算。
- 分不清服务端还是浏览器时：`POST /subgraph` 的响应带 `timing_ms.total`，图谱摘要会显示「服务 N ms」；浏览器侧用 `localStorage.kgDebug='1'` 或访问 `?debug=1` 打开 `[kg]` 前缀的前端计时（默认静默），可看到 `subgraph`／`graph-layout`／`fit-graph` 各占多少毫秒。

日志里能直接看到的已知读放大：

- `repository.query` 只按系统时间下推，`kinds`、业务有效期和 metadata 都在 Python 侧过滤，成本与项目全量版本历史成正比。`/subgraph`、`/entity-options`、`/dashboard`、`/sources`、`/records/query` 共用这条路径。
- 候选脑图与 `/ontology-discovery` 每次请求把「当前记录」扫两遍（`_candidates` 一次、`_candidate_lifecycle` 一次）。
- `repository.list_artifacts` 先解码所有项目的草案、再按项目过滤；草案里嵌了完整候选快照，所以这一步的解码量远大于返回值（日志的 `decoded=` 与 `kept=` 对比可见）。

详见 [解析日志定位](docs/解析日志定位.md)。数据契约与更多 API 说明见 [服务使用说明](docs/KNOWLEDGE_SERVICE.md)；其中早期 `.venv-service` 安装记录供参考，当前环境与配置以上述 conda 说明为准。

---

## 知识审核与本体变更

独立菜单“知识审核”按实体类型、关系、实体属性筛选；“后台任务”只展示进度、日志与审核入口。Semantica 输出本体未定义或名称歧义的实体类型/关系时，保存为候选，合法知识继续校验提交。依赖待审核实体的关系也暂存，即使关系类型已经定义也不提前入图。无法关联到实体结果的孤立关系会逐条跳过并在任务日志汇总，不会拖垮整份文档。

“知识写入 → 解析设置”可选**抽取实体业务属性**（默认关闭）：每个有实体的切片额外通过 Semantica provider 调用一次 LLM，要求返回实体索引、属性名、标量值与原文证据；属性值全部进入审核，包括本体已定义属性。此能力由服务层组织提示词和结果 schema，不是原生 NER 调用自动返回属性。文档上传时间、标题等 metadata 不属于业务属性审核。

先批准实体类型，依赖它的关系和属性才能继续批准；实体被拒绝后，依赖候选保留并提示阻塞，不会自动通过或级联删除。属性批准检查本体属性 domain、值的数据类型和实体版本，保存实体新版本及属性来源证据；实体已有不同值时拒绝覆盖，需要通过知识编辑核对。本轮仅支持实体标量属性，不含关系属性、集合/嵌套属性或单位转换。

| 操作 | 对图谱的影响 |
|---|---|
| 不审核 | 持久保存为 pending，不自动通过，不创建该边；合法知识照常使用。原文及片段仍可检索、用于问答，不是内容隔离区 |
| 批准 | 选择对应的当前本体定义并填写理由；实体新增节点，关系新增边，属性保存实体新版本，不替换全图 |
| 拒绝 | 保存理由和审核时间，不添加边；可在已审核记录、文档版本历史查看 |
| 申请本体变更 | 从候选创建新增/调整类、关系或属性的草案；批准后生成新本体版本并重新校验关联候选，但不自动写入知识 |
| 后续文档 | 默认使用最新本体抽取；新本体已定义的同名关系可以直接通过正常校验。一次审核映射不构成永久别名规则，模型仍输出未知名字时会再次进入审核 |

审核决定与批准边在同一个 PostgreSQL 事务提交；文档版本冲突、原文变更、已审核、端点删除或本体版本冲突会拦截，模型向量化/校验失败不会留下半条批准记录。审核保存抽取时的原文片段，不把修改后的正文冒充旧证据；融合端点会跟随保留实体。

本体变更草案绑定来源文档、知识候选、文档版本和本体基准版本。批准前展示现有知识引用、约束引用和关联候选数量；调整被引用的术语属于高影响变更，必须额外确认。批准发布本体版本、草案决定和候选重校验结果采用同一个 PostgreSQL 事务。本体或候选已变化时返回版本冲突，要求重新评估，不会覆盖新版。

批准后重新检索/加载图谱查看，时间及 metadata 筛选仍然生效；Neo4j 副本需要手动重新同步。当前是本机服务的审核留痕，不包含审核员身份认证、角色权限、多级审批或批量批准。拒绝/批准没有专用撤销按钮；已入图关系可通过现有知识治理功能处理。

知识审核接口：`GET /api/projects/{p}/reviews`，`POST /api/projects/{p}/reviews/{document_id}/{candidate_id}`。本体草案接口：`GET/POST /api/projects/{p}/ontology-change-proposals`，`GET /api/projects/{p}/ontology-change-proposals/{id}`，`POST /api/projects/{p}/ontology-change-proposals/{id}/decision`。候选 `kind` 为 entity/relation/attribute；旧版无 kind 的候选按 relation 兼容。属性批准后的实体版本使用审核时本体，其他知识不自动迁移。

升级前失败的任务未保存模型关系响应，不能从日志恢复审核候选。需要人工核对后重新上传解析，可能再次产生模型费用；服务不会自动重跑。

---

## 本体维护

“本体管理”支持实体类、关系、实体属性的新增、修改与停用。维护遵循版本化 RDF/OWL 模型：

- IRI 是跨本体版本和知识记录使用的稳定标识，创建后不直接改名；只修改显示名称 `rdfs:label` 不改变 IRI。
- 修改可维护显示名称、说明、类的父类、关系的 domain/range、属性的 domain 和字面量数据类型，每次保存为新本体版本。
- 属性值类型支持任意字面量、字符串、布尔值、整数、小数、浮点数、日期和日期时间。
- 停用前自动分析当前知识引用、继承/domain/range 约束和待审核候选；必须勾选确认。停用只从新本体版本移除术语，旧版本及引用旧版本的知识继续保留。
- IRI 更名采用“新增新术语 → 迁移知识 → 停用旧术语”。当前没有自动知识迁移功能。
- 类继承成环、无效属性数据类型会被拒绝。术语被 SHACL 或其他复杂三元组引用时，快捷停用会阻止操作，需要在 Turtle 编辑器中显式迁移引用并保存新版本。

快捷维护适合常见 OWL 类、对象属性和数据属性；等价类、逆关系、基数、属性链、复杂 SHACL 等高级语义继续通过 Turtle 编辑器维护。保存新本体后，后续抽取使用最新版本；已有知识不会自动重分类或重校验迁移。

---

## 项目删除的行为

`DELETE /api/projects/{id}` 会**物理删除**该项目的全部数据，无逻辑标记：

- PostgreSQL：`record_versions` / `ontologies` / `artifacts` / `projects` 四张主表 + 7 张关联表（断言/摄取/消歧等，经外键级联）+ FTS 全文索引；
- Milvus：清空该项目分区向量；
- Neo4j：删除该项目节点与关系（若配置过）。

任一外部存储清理失败不阻断 PostgreSQL 删除，只记录告警（它们都是可重建副本）。返回体带 `cleaned` 字段说明各副本清理结果。

---

## 测试

回归测试在 `tests/service/`（39 个文件，约 200 用例），全量约 2–4 分钟：

```bash
conda activate model_agent
python -m pytest tests/service -q
```

- 需要**真实 PostgreSQL**：测试用 `tests/service/pg_support.py` 在独立的 `knowledge_test` 库上建隔离 schema（每个 `Repository(路径)` 映射到一个槽位），跑完自动清理。数据库不可用时相关用例**自动 skip**，不会假绿。
- 服务层测试用 `model_agent` 解释器（含 Semantica）；前端契约测试（`test_frontend_retrieval_flow.py`）需 `.venv` 的 playwright + 系统 Edge。
- 真浏览器契约测试解释器：`.venv/Scripts/python.exe -m pytest tests/service/test_frontend_retrieval_flow.py -q`。

---

## 快速开始（仓库根执行）

```powershell
conda activate model_agent
docker compose up -d --wait
python -m knowledge_service.repository.migrate
python -u -m knowledge_service --port 8100
```

- 工作台：[http://127.0.0.1:8100/](http://127.0.0.1:8100/)；[交互 API 文档](http://127.0.0.1:8100/docs)。
- 服务验收：`python scripts/smoke_service.py`（针对运行中的 8100 服务）。

> 环境：conda env `model_agent`（Python 3.13，另含 rdflib、sentence-transformers）。
> 权威存储是 PostgreSQL（`docker compose` 的 `knowledge-postgres`），**启动前必须可用**。
> 向量模型 `data/model`（bge-m3）与本体文件可本地读取；LLM 抽取和生成式问答是否联网取决于供应商配置，并非整个流程默认全离线。

---

## 文档索引

| 文档 | 内容 |
|---|---|
| `docs/KNOWLEDGE_SERVICE.md` | 数据契约、API 与更多服务说明 |
| `docs/2026-09-09-能力对标与修正.md` | 新版服务能力对标记录 |
| `docs/2026-09-09-同屏工作台与加载优化.md` | 工作台性能优化记录 |
| `docs/2026-09-10-Semantica抽取与本体约束机制.md` | 抽取与本体约束机制 |
| `docs/milvus-向量化改造设计.md` | Milvus 向量检索设计 |
| `docs/2026-10-01-Docker基础设施维护手册.md` | PostgreSQL / Milvus / etcd / minio / Attu 容器的启动、迁移与故障处置 |
| `docs/neo4j接入.md` | Neo4j 投影接入说明 |
| `docs/解析日志定位.md` | 读路径耗时日志定位 |
| `docs/图谱设计.md` | 图谱设计稿（§4 本体来源、§7 评测口径） |
