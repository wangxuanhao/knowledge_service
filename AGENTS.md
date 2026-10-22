# AGENTS.md — 给编码助手的指路文件

> 面向在本仓库工作的 AI 编码助手（Hermes / Claude Code / Cursor 等）。
> **本文件只放"去哪找代码"和"哪里会踩坑"。**
> 领域词汇（实体实例/候选事实/本体草案…）见 `CONTEXT.md`；业务全貌见 `README.md`——需要时再读，不要为了猜结构而通读它们。

## 项目是什么

FastAPI + PostgreSQL 的知识图谱服务：双时态版本、本体/SHACL 治理、混合检索与证据问答，外加一套**原生 JS** 管理工作台。

## 技术栈与环境

| 项 | 值 |
|---|---|
| Python | **3.12**，conda env **`llm_model`**（**唯一**环境，**绝不要建 `.venv`**） |
| 解释器 | `D:/anaconda/envs/llm_model/python.exe` |
| 存储 | **PostgreSQL 是唯一权威存储**（SQLite 后端已移除；遗留 `KG_DATABASE` 被明确拒绝） |
| 检索 | 当前关键词/语义/混合使用项目显式构建的 Milvus 索引（`integrations/milvus_store.py`），不可用时明确失败 |
| 前端 | 原生 JS/CSS，**无构建工具、无 `node_modules`、无 `package.json`**；`web/` 挂载为 HTTP 路径 `/assets/` |
| Node | v26，仅用于跑前端契约测试（`node:test`） |

## 代码地图

依赖方向严格单向向下：`api → services → repository`（`core`/`utils` 是基础设施，`integrations` 是外部系统适配）。

| 路径 | 职责 |
|---|---|
| `knowledge_service/api/` | FastAPI 路由层（**只做参数校验与编排，无业务逻辑**）。前缀统一 `/api/`，健康检查 `GET /api/health` |
| `knowledge_service/services/` | 🧠 业务服务层（**无路由装饰器**）。`service.py` 是主门面，`answers.py` 是证据问答，`retrieval.py` 是检索，`ontology_*.py` 是本体系列 |
| `knowledge_service/repository/` | PostgreSQL 访问层。`connection.py` 的 `resolve_dsn()` 是唯一取连接入口；`core.py` 是主仓储；`migrations/` 是编号 SQL 迁移 |
| `knowledge_service/integrations/` | 外部系统适配：`semantica_adapter.py`（LLM 抽取）、`embeddings.py`、`milvus_store.py`、`neo4j_store.py`、`document_parser.py` |
| `knowledge_service/core/` | 配置（`config.py` 的 `load_environment`）、日志、HTTP 客户端（`net.py` 的 `external_client`）、时间 |
| `knowledge_service/utils/` | 工具：`diagnostics.py`、`filters.py`、`attributes.py` |
| `knowledge_service/models.py` | 全局 pydantic 模型（**想了解数据结构先读这里**） |
| `knowledge_service/web/` | 前端：`index.html` + 各功能 `*.js`/`*.css` 成对存在（如 `workspace.js`/`workspace.css`） |
| `tests/` | Python/Node 回归、浏览器检查和支持夹具；`tests/scripts/` 有运维安全回归。选择运行入口先读 `tests/README.md` |
| `scripts/` | 手动运维和专项验收；选择工具先读 `scripts/README.md`，其中明确区分当前入口、待适配专项和历史迁移演练 |
| `docs/` | 规划与手册（**读之前先按文件名筛，别整目录读**） |
| `data/rule_txt/` | ⚠️ 357 个业务规则 md——**这是测试数据，不是项目文档，不要读** |

## 怎么跑

### 起服务（端口 **8100**）
```bash
conda activate llm_model
python -u -m knowledge_service --port 8100
```
- **PostgreSQL 必须先跑**，否则服务拒绝启动：`docker compose up -d --wait`（仓库根，含 PG/Milvus/MinIO/etcd/Attu）
- **后端改动必须重启服务**（uvicorn 无 `--reload`）

### 迁移（应用进程不建表，启动只校验版本）
```bash
python -m knowledge_service.repository.migrate   # 幂等
```

### 跑测试 —— 两套，要分开跑
```bash
# ① Python 测试（含 PG 集成与浏览器契约）
conda activate llm_model
python -m pytest -q

# ② 前端契约测试（.cjs，pytest 不收集它们，必须单独跑）
node --test tests/service/*.test.cjs
```

## 陷阱清单（每条都真实踩过）

1. **`pytest` 必须密闭运行**：直接 `python -m pytest`，**绝不要 `source .env`**。`conftest` 靠 `KG_SKIP_DOTENV` 隔离；一旦 source 过 `.env`，`KG_VECTOR_BACKEND=milvus` / `KG_MILVUS_DIM=1024` / `KG_EMBEDDING_BACKEND=local` 会渗进测试进程，与测试用的 **256 维 demo 哈希编码器**错配，表现为十几个「向量维度 256 != 预期 1024」的**假失败**。补救：`unset KG_VECTOR_BACKEND KG_EMBEDDING_BACKEND KG_MILVUS_DIM KG_MILVUS_HOST KG_MILVUS_PORT KG_EMBEDDING_MODEL KG_EMBEDDING_BASE_URL` 再跑。
2. **切勿并行开两个 `pytest` 会话**：`pg_support.teardown` 会 `DROP DATABASE knowledge_test`，两个会话互相删库。
3. **改前端文件必须同步 bump `index.html` 里该文件的 `?v=` 版本号**（如 `workspace.js?v=fix32-inbox-only-1`），否则浏览器一直用缓存的旧 JS。**注意：部分 `.cjs` 契约测试把具体版本号字符串写死了**（例如 `evidence-inspector-frozen-source.test.cjs` 断言 `index.html` 含 `/assets/provenance-drawer.js?v=3`）——版本号一改，必须手动同步这些断言，它们不会自动跟随。
4. **`node --test` 不能用目录形式**：`node --test tests/service/` 在 Node 26 下会报 `Cannot find module`。必须用显式 glob `node --test tests/service/*.test.cjs`。
5. **`.env` 里的值是真实密钥，绝不打印**：只打印键名。连接串一律经 `knowledge_service.utils.diagnostics.redact()` 脱敏（它同时掩蔽 URL 形式和 libpq 关键字形式）——手写切片会泄漏。
6. **调用本机 HTTP 需要绕过系统代理**：`NO_PROXY='127.0.0.1,localhost'`，否则本地请求返回 502。
7. **应用连库用角色 `knowledge_app`（无 DDL 权限）**；建表/索引只能用迁移器（它持 owner 凭据）。应用进程读不到超级用户口令。
8. **前端 404 找不到资源时**：`web/` 映射到 `/assets/`，`web/vendor/` 映射到 `/vendor/`。
9. **派生知识的来源在 `assertions` 表，不在记录顶层 `source_id`**：一条实体/关系/属性可能被多个文档提到（多对多），`FormalFactWriter._canonical_record()` 落库前会主动 `pop('source_id')`，所以读到 `source_id=None` 是设计、不是丢数据。查「某文档抽出哪些知识」要用 `GET /projects/{p}/assertions?document_id=...`（带 kind/status/payload/quote），**不要靠记录 ID 前缀猜**。待确认（pending）断言的 `payload` 字段与 accepted 规范记录不同：pending 实体用 `proposed_type`、pending 关系用 `subject/predicate/object` 名字、pending 属性用 `subject/proposed_type/value`，前端渲染要分支处理，否则会显示"—"。相关坑：NER 可能误把标量值（金额/状态/时长）建成 `DataEntity` 实体；`semantica_adapter` 把属性改判关系必须三条全满足（值唯一命中实体 + 谓词是本体**对象属性** + 命中实体非 `DataEntity`），否则标量属性会被错误卷进关系组。

## 代码定位

优先用 codebase-memory 的 `search_graph`、`trace_path` 和 `get_code_snippet` 定位定义与调用关系，复杂关系用 `query_graph`。索引不足或陈旧时回退磁盘源码；文档、配置和字符串用 `rg`。读取大文件先定位符号再按范围读取，当前路径以实际目录为准。

## 修改前请确认

- 前端契约测试（`tests/service/*.test.cjs`）是 UI 的**活文档**：改了 UI 文案/结构/资源版本号，就要同步更新对应 `.cjs` 断言，否则它们会失败。
- 前端文案以现行导航和完整操作手册为准；历史设计中的已退役页面名称不反向覆盖当前界面。
