# 知识服务

更新日期：2026-10-22。FastAPI + PostgreSQL + Milvus 的知识平台，提供资料解析、受控/开放抽取、本体草案治理、知识编辑与融合、图谱浏览、检索和证据问答。

先看这些入口：

- [完整图文操作手册](docs/知识平台完整操作手册.html) · [手册源文件](docs/2026-10-20-知识平台完整操作手册.md)
- [测试怎么跑](tests/README.md)：Python、Node、独立浏览器测试及环境要求。
- [脚本怎么选](scripts/README.md)：日常工具、容量验证、待适配专项和历史迁移演练。
- [文档索引](docs/README.md)：当前说明、当前规格、验收记录和历史设计。

## 当前存储与能力

| 组件 | 职责 |
| --- | --- |
| PostgreSQL | 唯一业务权威：记录、修订、本体发布、断言、来源、任务和审计 |
| Milvus | 共享 `knowledge_vectors` 的 dense/BM25 检索投影，按项目与构建代际隔离，可显式重建 |
| Neo4j | 可选图谱投影；不替代 PostgreSQL 正式写事务与版本权威 |
| etcd / MinIO | etcd 保存 Milvus 元数据；MinIO 分 bucket 保存 Milvus 文件与业务原件/图片，业务版本与引用以 PostgreSQL 为准 |

当前关键词、语义和混合检索都要求项目 Milvus 索引就绪；失败时明确报错，不自动切模式或本地向量降级。Metadata 资格由 PostgreSQL 完整判定，再由 Milvus 排名。主动指定系统历史时点时，仅支持 PostgreSQL 历史关键词查询。

处理批次管理资料与抽取结果；工作草案管理一轮本体结构变更和明确加入的候选依赖。事实写入、本体发布和索引覆盖分别核对。已有正式本体可以继续建草案修改；普通事实补充也可以只复用已发布术语，不必新建结构草案。

当前操作与能力边界以完整手册为准，社区 Global/DRIFT、项目成员 ACL 等尚未完成的能力不作为已交付功能。

## 项目目录

```text
knowledge_service/       应用包
  api/                   HTTP 路由、认证授权与装配
  services/              写入、本体治理、检索问答、图谱和任务编排
  repository/            PostgreSQL 仓储、投递回执与编号迁移
  domain/                业务与检索投影契约
  integrations/          Semantica、模型、Milvus、Neo4j、文件解析
  core/ utils/           配置、日志、时间和公共工具
  web/                   原生 JS/CSS/HTML，无前端构建工具
  resources/             默认资源与解析配置
scripts/                 手动工具和专项验收，先读 scripts/README.md
tests/                  自动化回归与支持夹具，先读 tests/README.md
docs/                   当前手册、规格及历史验收记录
docker/                 基础设施配置
data/                   本地模型、来源文件及样本数据，勿作缓存清理
```

外部入口：`knowledge_service`。依赖方向为 `api → services → repository`，`core/utils/domain/integrations` 按各自边界提供支持。开发提示见 [AGENTS.md](AGENTS.md)，领域词汇见 [CONTEXT.md](CONTEXT.md)。

## 启动

使用既有 conda 环境 `llm_model`、Python 3.12；不要另建 `.venv`。新部署按 [.env.example](.env.example) 填写本机配置，已有部署保留自己的 `.env`，不要提交凭据。

```powershell
conda activate llm_model
# 仅新环境需要安装依赖：
python -m pip install -e ".[local,milvus,neo4j,semantica-runtime,test]"
docker compose up -d --wait
python -m knowledge_service.repository.migrate
python -u -m knowledge_service --port 8100
```

工作台：[http://127.0.0.1:8100/](http://127.0.0.1:8100/)，API 文档：[http://127.0.0.1:8100/docs](http://127.0.0.1:8100/docs)。默认只监听本机；修改后端后需重启服务。

- 迁移器使用独立迁移凭据；应用账号不执行 DDL。不要删除或修改已经应用的编号 SQL。
- 首次登录与账号管理按手册第 2 章操作。生产保持认证授权开启。
- 当前部署使用 Docker Linux 命名卷。迁移/复制前先读 [数据卷迁移指南](docs/2026-10-19-Docker-Linux-volume-migration.md)，不要用新空卷覆盖现有挂载。
- 本地 embedding 模型、LLM/OCR 配置按手册和 `.env.example` 设置。首次模型加载与暖态查询分开计时。
- 首次检索由管理员明确发起项目向量化；已构建项目的普通修改通过增量同步追赶水位。

## 测试与手册

```powershell
python -m pytest -q
node --test tests/service/*.test.cjs
python scripts/build_knowledge_service_manual.py
```

Python 默认收集 `tests/service` 和 `tests/scripts`。Node 的 `.cjs` 必须单独运行；需要 Playwright 的独立浏览器布局测试另见测试指南。脚本不被 import 不能证明没用，手动容量和运维工具也不属于默认回归。

只运行一个 pytest 会话：测试使用隔离的 `knowledge_test` 数据库，会话结束会清理该库。测试进程不要加载生产 `.env`。PostgreSQL 不可用产生的 skip 不算通过；本机 HTTP 客户端应绕过系统代理。

本轮存储和检索专项回归结果是 [255 项通过](docs/research/2026-10-22-storage-regression-followup.md)，不是仓库全量测试结论。项目整理后的收集与验证结果见 [文件清理记录](docs/research/2026-10-22-project-file-cleanup.md)。

## 真实验收与容量

保留的“容量验收 · 15万实体 / 30万关系（保留测试）”项目可从项目管理进入，当前有 150001 实体、300000 关系。业务数据、模型、上传资料和 Docker 卷不属于本次文件清理范围。

本体编辑、实体融合/消歧/撤销、版本治理及现行回归均保留。当前 15 万节点向量检索性能、新的性能 P95 和有效规则问答质量仍有待验收，普通小项目或单项绘图测量不能替代。详见 [生产检查点](docs/research/2026-10-22-production-checkpoint.md) 和完整手册第 27 章。

历史方案和实测记录继续保留在 `docs/research` 与 `docs/superpowers`，按文档索引查找。已退役的一次性工具和旧部署说明可从 Git 历史恢复；不再保留看似可运行的空壳入口。
