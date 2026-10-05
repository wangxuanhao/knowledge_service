# AGENTS.md — 给编码助手的指路文件

> 面向在本仓库工作的 AI 编码助手（Hermes / Claude Code / Cursor 等）。
> **本文件只放"去哪找代码"和"哪里会踩坑"。**
> 领域词汇（实体实例/候选事实/本体草案…）见 `CONTEXT.md`；业务全貌见 `README.md`——需要时再读，不要为了猜结构而通读它们。

## 项目是什么

FastAPI + PostgreSQL 的知识图谱服务：双时态版本、本体/SHACL 治理、混合检索与证据问答，外加一套**原生 JS** 管理工作台。

## 技术栈与环境

| 项 | 值 |
|---|---|
| Python | **3.13**，conda env **`model_agent`**（**唯一**环境，**绝不要建 `.venv`**） |
| 解释器 | `D:/Anaconda3/envs/model_agent/python.exe` |
| 存储 | **PostgreSQL 是唯一权威存储**（SQLite 后端已移除；遗留 `KG_DATABASE` 被明确拒绝） |
| 向量 | 可选 Milvus（`integrations/milvus_store.py`） |
| 前端 | 原生 JS/CSS，**无构建工具、无 `node_modules`、无 `package.json`**；`web/` 挂载为 HTTP 路径 `/assets/` |
| Node | v26，仅用于跑前端契约测试（`node:test`） |

## 代码地图

依赖方向严格单向向下：`api → services → repository`（`core`/`utils` 是基础设施，`integrations` 是外部系统适配）。

| 路径 | 职责 |
|---|---|
| `knowledge_service/api/` | FastAPI 路由层（**只做参数校验与编排，无业务逻辑**）。前缀统一 `/api/`，健康检查 `GET /api/health` |
| `knowledge_service/services/` | 🧠 业务服务层（**无路由装饰器**，28 个模块）。`service.py` 是主门面，`answers.py` 是证据问答，`retrieval.py` 是混合检索，`ontology_*.py` 是本体系列，`chunking.py` 是切片 |
| `knowledge_service/repository/` | PostgreSQL 访问层。`connection.py` 的 `resolve_dsn()` 是唯一取连接入口；`core.py` 是主仓储；`migrations/` 是编号 SQL 迁移 |
| `knowledge_service/integrations/` | 外部系统适配：`semantica_adapter.py`（LLM 抽取）、`embeddings.py`、`milvus_store.py`、`neo4j_store.py`、`document_parser.py` |
| `knowledge_service/core/` | 配置（`config.py` 的 `load_environment`）、日志、HTTP 客户端（`net.py` 的 `external_client`）、时间 |
| `knowledge_service/utils/` | 工具：`diagnostics.py`、`filters.py`、`attributes.py` |
| `knowledge_service/models.py` | 全局 pydantic 模型（**想了解数据结构先读这里**） |
| `knowledge_service/web/` | 前端：`index.html` + 各功能 `*.js`/`*.css` 成对存在（如 `workspace.js`/`workspace.css`） |
| `tests/service/` | 全部测试（含 `pg_support.py` 与 `.cjs` 前端契约测试） |
| `scripts/` | 冒烟与验证脚本（`smoke_service.py`、`verify_*.py`） |
| `docs/` | 规划与手册（**读之前先按文件名筛，别整目录读**） |
| `data/rule_txt/` | ⚠️ 357 个业务规则 md——**这是测试数据，不是项目文档，不要读** |

## 怎么跑

### 起服务（端口 **8100**）
```bash
conda activate model_agent
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
conda activate model_agent
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

## 巨型文件：读之前先规划

以下文件单文件就足以占满相当比例的上下文窗口，**不要无条件整读**（用 `read_file` 的 `offset`/`limit` 分段，或先用 `search_files` 定位）：

| 文件 | 行数 | 约 token |
|---|---|---|
| `web/ontology-workbench.js` | 2752 | ~62k |
| `tests/service/test_ontology_discovery.py` | 2757 | ~40k |
| `services/ontology_drafts.py` | 2438 | ~35k |
| `tests/service/test_ontology_workbench_ui.py` | 1907 | ~30k |
| `repository/core.py` | 1776 | ~27k |
| `web/ontology-design.js` | 1453 | ~25k |

> 💡 **省 token 的杠杆是"文件粒度"，不是语言。** 改一个巨型文件的成本不是读一次，而是**读入后每轮对话都要重新携带**。需要在大文件里做探索性查找时，优先交给子代理（隔离上下文，只回摘要）。

## 修改前请确认

- 前端契约测试（`tests/service/*.test.cjs`）是 UI 的**活文档**：改了 UI 文案/结构/资源版本号，就要同步更新对应 `.cjs` 断言，否则它们会失败。
- 本仓库前端文案统一用「本体审核台」（不是「本体工作台」），改名时注意测试与 UI 同步。
