# 知识服务层

新入口是 `knowledge_service`，不再调用 15/16/17/18 脚本。旧脚本、旧数据原样保留，不自动迁移，也不保证旧 API 兼容。

## 启动

本机已创建 `.venv-service` 并验证 `data/model` 的 BGE-M3 能输出 1024 维向量。从仓库根运行：

```powershell
./.venv-service/Scripts/python.exe -m knowledge_service --port 8100
# 或：
./scripts/start-service.ps1 -Port 8100
```

工作台：http://127.0.0.1:8100/ 。交互 API 文档：http://127.0.0.1:8100/docs 。默认仅监听本机；Ctrl+C 停止。

新环境安装（已在 Python 3.13 验证）：

```powershell
py -3.13 -m venv .venv-service
./.venv-service/Scripts/python.exe -m pip install -e ".[local,semantica-runtime,test]"
./.venv-service/Scripts/python.exe -m pip install --no-deps semantica==0.6.7
```

Semantica 的完整发行包声明了大量音视频、可视化等依赖；这里按当前服务实际调用的抽取/ContextGraph 模块安装最小运行依赖，**不是完整 Semantica 环境**，`pip check` 会报告其未安装的可选使用模块依赖。启用其它 Semantica 模块前应单独安装并验证依赖。无模型时可用 `--demo` 验证流程；该模式只是字符哈希相似度，界面明确标为非语义，不会偷偷替代真实模型。

## 模型配置

本地检索无需 LLM 密钥。上传时关闭“Semantica 抽取”，即可切块、向量化和检索；实体/关系也可由结构化接口批量写入。

抽取和生成式问答需要显式配置 OpenAI 兼容服务，可能产生供应商费用：

```powershell
$env:KG_LLM_BASE_URL = 'https://your-provider.example/v1'
$env:KG_LLM_MODEL = 'your-model'
$env:KG_LLM_API_KEY = '在本机填写，不要提交'
```

`.env.example` 只是配置清单，不会自动读取。远程 embedding 使用 `KG_EMBEDDING_BACKEND=openai` 及 `KG_EMBEDDING_BASE_URL/MODEL/API_KEY`。本地路径可由 `KG_EMBEDDING_PATH` 指定。不要在同一个数据库中更换模型或覆盖同路径权重；目前模型身份/维度不一致会拒绝检索，没有在线重建向量接口。

## 服务结构

| 模块 | 职责 |
|---|---|
| `api.py` / `models.py` | FastAPI 路由、请求契约、输入限制 |
| `service.py` | 写入、文档处理、范围查询、检索、证据问答 |
| `repository.py` / `time.py` | SQLite 真值库、原子版本更正、双时态 |
| `filters.py` / `retrieval.py` | 嵌套 metadata 过滤、候选集内 FAISS 排序 |
| `ontology.py` | 版本化 RDF/OWL、本体摘要、SHACL、只读 SPARQL |
| `semantica_adapter.py` | Semantica LLM 抽取与业务时点 ContextGraph 快照 |
| `embeddings.py` / `web/` | 显式模型适配、管理工作台 |

SQLite 默认保存到 `data/service/knowledge.sqlite`，不使用旧 `graph.json`。FAISS 索引按过滤后的候选集构建，向量和模型身份持久化在 SQLite，重启无需依赖旧进程内存。默认单进程运行；不是分布式写入服务。

## 时间不是上传时间

- `metadata.uploaded_at`：服务收到文档的时间。
- `valid_from / valid_until`：事实在业务世界有效的区间，左闭右开；由调用者提供，不从上传时间推断。
- `recorded_at / superseded_at`：系统何时保存/更正该版本，只由服务生成。
- `valid_at`：查询某个业务时点的事实；`known_at`：只使用系统在该时刻已经知道的版本。两者省略时默认现在。
- 时间接受日期（按 UTC 零点）或带时区 ISO 8601，统一 UTC；拒绝无时区的日期时间。缺少生效起点视为未知且默认包含；`include_unknown=false` 排除。

同 ID 的 PUT 是**整条记录更正**，必须提交 `expected_version`；不会自动切分业务区间。业务上先后有效的两个事实应使用不同 ID 和各自区间。历史版本永不被覆盖，历史接口返回完整版本及区间。关系有效期必须包含在两端实体有效期内，查询时两端同样经过时间和 metadata 筛选，避免泄露范围外节点。

## metadata 前置筛选

先按项目、系统时间、业务时间和 metadata 得出候选记录，再对候选集向量排序，不是取全局 top-k 后再丢掉不符合条件的结果。

```json
{
  "query": "退款条件",
  "k": 10,
  "valid_at": "2026-09-01T00:00:00+08:00",
  "filters": {
    "and": [
      {"field": "region.city", "op": "eq", "value": "北京"},
      {"field": "tags", "op": "contains", "value": "退款"},
      {"field": "confidence", "op": "gte", "value": 0.8}
    ]
  }
}
```

发往 `POST /api/projects/{project_id}/search`。字段默认从 metadata 寻址，也支持 `metadata.region.city`。运算符：`eq/ne/in/contains/gt/gte/lt/lte/exists`，逻辑组 `and/or`。类型严格区分（`true` 不等于 `1`）；不存在的字段与 JSON null 不同。上传的 metadata 继承到切块和抽取结果；同名键采用浅层覆盖。图谱、记录列表、问答、SPARQL 使用同一 Scope 契约。

## 本体与溯源

### 唯一真相源与两条输入链路

SQLite 中不可变的 `ontology_versions` 是正式本体的唯一真相源；草案、原子变更、审核决定和来源快照都是发布前的治理记录，不能绕过版本边界直接改变正式本体。项目第一次建立本体时允许可信 bootstrap；从已有版本开始，手工结构化编辑、Turtle、开放发现、候选建议和导入都必须创建或更新统一草案。

工作台保留两条输入链路，但在草案处合流：

1. **开放发现**：Semantica 从文档产生候选、聚类和来源证据，人工筛选后生成 discovery 草案。
2. **受控建模**：人工新增实体类、关系、属性，或提交 Turtle diff，直接形成 manual/turtle 草案的原子变更。

两条链路随后统一执行“设计 → 提交 → 逐项审核 → 校验 → 发布”。发布事务同时写入本体版本、草案状态、来源副作用和 provenance；Milvus 同步在提交后运行，失败留下可重试任务，不会制造半发布版本。发现草案获批的候选在同一事务中物化为正式记录，不再要求重新调用 LLM。

### 层级、多个父类与停用

类层级使用 `rdfs:subClassOf` DAG。一个类可以有零个、一个或多个直接父类：零父类就是有限层级中的独立根，并不意味着还需无限向上补父类；发布前会拒绝环。工作台只分页读取根和展开节点的直接子类，同一个 canonical IRI 在多个父级下显示为引用行，选择任意引用都会选中同一对象。

关系和属性可以有多个 domain/range，服务将它们编译为显式 OWL union（OR），不是隐式交集。新增类允许保持独立根；不能物理删除已发布术语，只能 `retire_term` 停用。停用会保留历史版本、定义和 provenance，并在操作前展示正式记录、约束、后代和待审核引用。`restore_term` 必须明确选择不可变来源版本及要恢复的字段，恢复同样需要审核。

### 草案状态、审核与并发字段

- `editing`：可追加命令；`submitted`：等待审核；`reviewed`：每个当前变更已有最终批准/拒绝；`published`/`closed`：终态。
- 基础版本或来源变化时进入 `stale_base` / `stale_source`，必须刷新、rebase 或重新生成，不能带着旧快照发布。
- 每次命令都带 `expected_revision`；提交决定和发布还必须带 `expected_ontology_id`、`validation_fingerprint` 和 `acknowledged_warning_codes`。
- 发布必须带客户端生成的 `idempotency_key`；重试同一请求返回同一版本，不会重复发布。
- 只有当前筛选中无警告的低风险操作可批量批准，单次最多 100 项。中高风险、停用、恢复和高级 RDF patch 必须逐项审核。
- 拒绝和“要求调整”必须填写理由；高风险批准或带警告批准也必须填写理由。`request_changes` 会把草案退回 `editing`。校验指纹变化后旧决定不会被静默沿用。

校验报告固定分为图结构完整性、前瞻新写入约束和历史数据影响三部分。历史影响只用于评估，不会回写或清洗旧记录。正式 provenance 可沿“版本 → 草案 → 原子变更 → 审核决定 → 文档/候选来源快照”查询。

### 时间与旧 artifact 策略

新草案、操作、决定、版本和 provenance 时间统一由服务端写为 UTC `Z` 结构。按当前迁移策略，旧时间字段不做转换、修复或回填；部署方可以备份后删除旧库重新建立。治理功能不会把仅存在于旧 `ontology_discovery_draft` / `ontology_change_proposal` artifact 的历史待办自动转换成新草案，它们不出现在统一草案列表，也不能由统一发布接口发布。

### Semantica 边界

当前适配 Semantica 0.6.7 的严格 LLM 实体/关系抽取、开放本体发现和 `ContextGraph.state_at`。Semantica 层级输出的单值 `parent` 会映射为父类边；缺失或未知父类保留为独立根。多父类、双时态、草案并发、审核、持久化、发布事务和 provenance 由服务层负责。这里只实现明确的 RDFS/OWL-union/SHACL 能力，不宣称完整 OWL 推理机。SPARQL 仅支持本地 SELECT/ASK，不支持远程 SERVICE/FROM 或写操作。

实体身份不以本体版本号分区：只要稳定类型 IRI、名称/别名和业务有效期兼容，同一实体可以跨本体版本融合；规范实体记录会保留参与融合的 `ontology_versions` 和各次来源。相同主语、稳定关系 IRI、宾语及有效期的关系也会按确定性关系键融合并保留来源。类型 IRI 改变时不会自动融合，需通过受审核的本体变更或人工实体治理处理。

文档处理先保存上传回执，再处理切块与抽取；成功时知识和完成状态原子提交，失败保留回执及失败状态，不留下半批派生知识。证据包含 `source_id`、不可变 `source_version_id`、chunk/字符位置、抽取时间和版本信息。问答返回证据编号及版本，可以回查当时的原文。

## 主要接口

| 路径（项目内省略 `/api/projects/{id}`） | 功能 |
|---|---|
| `GET/POST /api/projects` | 项目列表/创建 |
| `POST /documents`、`/documents/upload` | 文本/UTF-8 TXT、MD 上传 |
| `POST /records`、`PUT /records/{id}` | 原子批量写入/版本更正 |
| `GET /records/{id}/history` | 不可变历史 |
| `POST /records/query`、`/search`、`/qa` | 范围列表、检索、证据问答 |
| `POST /graph`、`/graph/semantica` | 同一查询范围的图快照 |
| `GET/POST /ontologies`、`GET /ontology` | 本体历史、读取；已有版本后的 POST 只创建治理草案 |
| `GET/POST /ontology-drafts` | 统一草案列表、创建；返回 `revision`、状态和来源上下文 |
| `POST /ontology-drafts/{id}/commands` | 追加原子变更，要求 `expected_revision` |
| `POST /ontology-drafts/{id}/validate`、`/submit` | 生成校验指纹、提交审核 |
| `POST /ontology-drafts/{id}/decisions` | 保存逐项决定及警告确认 |
| `POST /ontology-drafts/{id}/rebase`、`/close`、`/publish` | 变基、关闭、幂等发布 |
| `GET /ontology-hierarchy/roots|children|search|neighborhood` | 分页读取多父类 DAG 和对象邻域 |
| `GET /ontology-matrix` | 分页读取关系/属性的 domain、range 和数据类型 |
| `GET /ontology-discovery/candidate-mindmap` | 聚合开放候选实体、关系、属性、生命周期与来源证据；只用于预览，不写正式图谱 |
| `POST /ontology/validate`、`/sparql` | 当前选定快照校验/只读查询 |

接口完整字段及示例以 `/docs` 为准。主要错误：422 输入或本体不合法，409 版本冲突，404 对象不存在，503 模型或依赖不可用。

## 验证与边界

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
./.venv-service/Scripts/python.exe -m pytest tests/service -q
./.venv-service/Scripts/python.exe -m pytest tests/test_web_ui_contract.py -q
# 服务启动后，创建一个标为“服务验收”的项目并验证真实 HTTP 流程：
./.venv-service/Scripts/python.exe scripts/smoke_service.py
```

服务面向本机/可信环境，尚无登录鉴权、租户安全边界、任务队列、分布式事务、全文规模索引或生产限流。项目 ID 隔离是数据组织能力，不替代鉴权。上传当前只支持文本，抽取同步执行；PDF/OCR、Neo4j 与其它向量库可后续通过适配器扩展。暴露到公网前必须补齐生产安全和容量治理。
