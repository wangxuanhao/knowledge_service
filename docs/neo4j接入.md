# Neo4j 接入（保留本地存储）

本次采用 **SQLite 主存储 + Neo4j 图谱副本**。不是替换数据库，也不是自动双写。
已有项目、全文、向量、本体和版本历史仍保存在本地；原有检索、时间条件、metadata 前置过滤继续走本地服务。
同步是按项目手动触发的完整快照，后续写入后需要再次同步。接口会显示是否存在待同步变更。

## 配置与启动

在 PowerShell 中执行：

```powershell
conda activate llm_model
python -m pip install 'neo4j>=6,<7'
$env:KG_NEO4J_URI = 'neo4j://127.0.0.1:7687'
$env:KG_NEO4J_USERNAME = 'neo4j' # 按实际用户名修改
$env:KG_NEO4J_DATABASE = 'neo4j' # 数据库名，不是 Desktop 实例 UUID
$securePassword = Read-Host 'Neo4j password' -AsSecureString
$env:KG_NEO4J_PASSWORD = [System.Net.NetworkCredential]::new('', $securePassword).Password
python -m knowledge_service --port 8100
```

环境变量只对该终端启动的进程生效，修改后需重启服务。已有 8100 服务需先正常停止，不要重复启动。
服务启动自动读取根目录 `.env`。为兼容现有配置，也读取 `.env.example` 中的 `KG_NEO4J_*` 非空值；终端环境变量优先，其次 `.env`。
示例文件中的模型和 LLM 配置不会自动加载。真实密码优先放 `.env`，不要提交到仓库。

## 使用

在「总览 / 本地项目」页的 Neo4j 面板检查连接、查看当前项目状态、启动同步。
任务进度和失败信息可在「后台任务」查看。

本机现有历史项目可通过以下命令快速载入并同步（不运行 LLM，不构建向量）：

```powershell
conda activate llm_model
python scripts/sync_local_neo4j.py --include-legacy --sync-all-legacy
```

上述命令只扫描 `data/projects`，并同步带有旧项目来源标记的本地服务项目。
单个项目可从后台同步；Neo4j 手工修改不会反向覆盖 SQLite。

- `GET /api/storage`：配置状态，不表示已连通。
- `POST /api/storage/neo4j/check`：实际连接和数据库读取校验。
- `GET /api/projects/{id}/storage/neo4j`：上次成功同步及本地待同步状态。
- `POST /api/projects/{id}/storage/neo4j/sync`：返回后台任务（202）。

需要创建约束和写入图数据的权限。每个项目在一个 Neo4j 事务中更新，失败可重试；本地记录不受影响。
Neo4j 提交后、本地收据保存前如果进程退出，再同步是幂等的。不同目标 URI / 数据库使用独立收据。

## 图模型与边界

- `KSProject`：本服务项目，包含本地存储 namespace，避免与其他数据来源混淆。
- `KSRecord`：实体、关系、文档或片段的最新系统版本；`kind` 区分类型。
- `KSVersion`：全部历史版本，含业务有效期和系统记录 / 替代时间。
- `KSOntology`：本体版本及 Turtle。
- `KS_FACT`：当前系统版本中的实体关系，`type` 保存原谓词，保留平行关系 ID。
- 嵌套 metadata / 属性保存为 JSON 字符串；向量不上传。后台任务和项目快照文件不复制。

最新系统版本不等于当前业务有效：Neo4j 浏览时需额外按 `valid_from` / `valid_until` 过滤；
软删除节点仍保留审计记录，但不生成活动关系。历史关系可通过 `KSVersion` 的端点 ID 查看。
目前不提供 Neo4j 反向导入、Neo4j 驱动的在线检索或双向冲突合并。在 Neo4j 手工修改副本，下次同步会被本地内容覆盖。
同步只重建当前 namespace + project_id 的 `KS_FACT`，不清空数据库或其他应用数据。

查看实体关系示例（将项目 ID 替换为服务项目 ID）：

```cypher
MATCH (a:KSRecord)-[r:KS_FACT]->(b:KSRecord)
WHERE r.project_id = $project_id
RETURN a, r, b LIMIT 200
```

实现采用 [Neo4j 官方 Python 驱动的连接与事务接口](https://neo4j.com/docs/python-manual/current/connect/)。
