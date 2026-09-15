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

项目创建时可加载内置美团本体，也可关闭默认本体后上传自己的 Turtle。每次保存生成不可变本体版本；实体/关系写入绑定具体版本。类型白名单、关系 domain/range、类继承和 SHACL 都参与写入校验，按事实共存的业务时间段执行，失败整批不落库。SPARQL 仅支持本地 SELECT/ASK，不支持远程 SERVICE/FROM 或写操作。这里只实现明确的 RDFS/SHACL 能力，不宣称完整 OWL 推理机。

未知领域可先反复使用开放本体发现。每批文档的候选保留在来源文档中，项目级发现页聚合全部有效候选；新草案以当前已发布本体为父版本，复用按 IRI、标签或局部名匹配到的稳定术语 IRI，并记录类、关系和属性的 added/retained/removed 差异。发布草案只创建不可变本体版本和候选快照，不直接写入正式实体或关系。随后必须明确选择该本体版本重解析原文，正式抽取结果才经过约束校验和融合进入图谱。

实体身份不以本体版本号分区：只要稳定类型 IRI、名称/别名和业务有效期兼容，同一实体可以跨本体版本融合；规范实体记录会保留参与融合的 `ontology_versions` 和各次来源。相同主语、稳定关系 IRI、宾语及有效期的关系也会按确定性关系键融合并保留来源。类型 IRI 改变时不会自动融合，需通过受审核的本体变更或人工实体治理处理。

文档处理先保存上传回执，再处理切块与抽取；成功时知识和完成状态原子提交，失败保留回执及失败状态，不留下半批派生知识。证据包含 `source_id`、不可变 `source_version_id`、chunk/字符位置、抽取时间和版本信息。问答返回证据编号及版本，可以回查当时的原文。

Semantica 实际参与两处：严格 LLM 实体/关系提取，以及 `graph/semantica` 的 `ContextGraph.state_at`。双时态历史、持久化和过滤由服务层补齐；没有把上传时间冒充 Semantica 时间能力。**当前未实现持久化“决策事件/决策审计日志”**，证据问答不等于完整业务决策审计系统。

## 主要接口

| 路径（项目内省略 `/api/projects/{id}`） | 功能 |
|---|---|
| `GET/POST /api/projects` | 项目列表/创建 |
| `POST /documents`、`/documents/upload` | 文本/UTF-8 TXT、MD 上传 |
| `POST /records`、`PUT /records/{id}` | 原子批量写入/版本更正 |
| `GET /records/{id}/history` | 不可变历史 |
| `POST /records/query`、`/search`、`/qa` | 范围列表、检索、证据问答 |
| `POST /graph`、`/graph/semantica` | 同一查询范围的图快照 |
| `GET/POST /ontologies`、`GET /ontology` | 本体历史、发布、读取 |
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
