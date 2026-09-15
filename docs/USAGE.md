# USAGE —— 模块地图：什么时候用什么 / 能力 / 已实现

> 目录总览见根 `README.md`。这里是"哪个脚本、什么时候用、有什么能力、实现了什么"的速查。

## 1. 四个可运行脚本（apps/）

| 脚本 | 什么时候用 | 能力（已实现） | 输入 → 输出 |
|---|---|---|---|
| **15** `apps/15_rule_demo_llm提取.py` | 领域**不熟、想先扫概念**（开放抽取） | LLM 开放抽取实体/关系（不被 17 的闭集白名单卡住，能暴露"本体外新概念"） | `data/rule_demo|rule_txt` → `data/rule_demo_output` |
| **16** `apps/16_kg_web_server.py` | 想**看图 + 网页问答/检索** | ECharts 图、检索三类命中、`/ontology`(declared 本体 + observed 数据)、`/sparql` 端点、`/sparql-check` 质量体检、上传/增量 job | 启动 8000 端口 Web；项目存 `data/projects` |
| **17** `apps/17_rule_demo_semantica_api.py` | 领域清楚、**正式抽取（默认主线）** | 本体先行：启动从 TTL 派生白名单 → LLM 闭集抽实体/关系 + 规则抽章/节/条(Chapter/Section/Article + partOf) → 别名归一 → domain/range 约束降权 → bge-m3 双索引 → 建图(含 instanceOf/subClassOf 层级) → 本体总结报告 | `data/rule_demo` → `data/rule_demo_output/run_<时间>/`（含 graph.json / ontology_summary.md） |
| **18** `apps/18_ontology_owl_sparql.py` | 随时查本体/跑查询/做评测 | `info`(本体摘要) · `query`(SPARQL) · `demo`(内置 8 条能力问答) · `export`(RDF/Turtle) · `validate`(domain/range 体检) · `eval`(gold vs pred 尺子) | 读 `ontology/` 与任一 `graph.json` → 终端/md 报告 |

**怎么选一句话：** 探索新域 → 15；正式受控抽取 → 17；看结果/问答/体检 → 16 Web 或 18 CLI；改版本怕变差 → 18 `eval`。

## 2. 复用库（kgcore/，被上面 import，不单独运行）

| 模块 | 能力（已实现） |
|---|---|
| `ontology_owl.py` | 读 `ontology/meituan_ontology.ttl` → `ontology_info()`(类层级/属性/domain-range)；派生 `entity_type_whitelist()/relation_whitelist()`（17 用它，P3 单一来源）；`build_dataset()`(graph.json→RDF 个体+类型物化+instanceOf)；`run_query()`(SPARQL)；`constraint_violations()`(抽取端 domain/range 检查)；`analyze_extraction()/render_summary_md()`(本体总结报告)；`validate()` |
| `kg_eval.py` | `evaluate(gold, pred)` → 覆盖率/实体(级+文本级)/关系 的 精确率·召回·F1、噪声、长尾；`make_template()` 从一次结果生成可人工校正的 gold 模板 |
| `kg_project.py` | `list/save/load/delete_project`（`data/projects/<name>/`：manifest+graph.json+实体/段落 JSON+FAISS） |
| `paths.py` | 统一路径源：`REPO/APPS/CORE/ONTOLOGY/DATA/…`（改目录只改这里） |

## 3. 本体与数据

| 位置 | 说明 |
|---|---|
| `ontology/meituan_ontology.ttl` | **正式本体（唯一要手改）**：29 类(含 Actor/GovernanceEntity/文档结构 Chapter/Section/Article) + 24 ObjectProperty(domain/range) + 10 DataProperty + 2 SHACL |
| `data/model` | 本地 bge-m3（离线向量化，勿动） |
| `data/rule_demo` `data/rule_txt` | 输入文档（demo 用 .md/.txt） |
| `data/rule_demo_output` | 15/17 直接运行的产物（每次 `run_<时间>/`） |
| `data/projects` | Web 项目 / 可被 18 SPARQL 直接读的 `graph.json` |
| `data/gold` | 评测 gold 建议放这里 |

## 4. 已实现能力清单（对照你关心的链路）

- ✅ **本体先行（schema-first）**：TTL → 启动派生白名单 → 闭集抽取 → 校验 → 建图
- ✅ **结构分层抽取**：RuleDocument/Chapter/Section/Article + partOf 树（文档→章→节/条）
- ✅ **抽取端约束回接**：domain/range 违规 → 关系降权 + metadata 打标（`constraint_backcheck`）
- ✅ **图谱输出层级**：类节点 + `instanceOf`/`subClassOf` 边（graph.json/graphml）
- ✅ **OWL+SPARQL**：RDF 数据集（TBox+A-Box，超类物化）、SELECT/ASK、property-path 上卷
- ✅ **本体总结报告**：每次运行自动出 `ontology_summary.md/.json`（分布+约束体检+候选新类/新关系）
- ✅ **评测尺子**：`eval`（gold vs pred）脚手架，可 `--gen-template` 建 gold
- ✅ **Schema 单一来源(P3)**：17 启动自动同步 ttl，改本体不再两份漂移
- ✅ **闭集/开放双轨**：17 闭集受控；15 开放扫新概念喂候选（模式 B 种子法）

## 5. 已知边界 / 下一步（诚实）

- 图在内存、每次重建，无增量落库（**P2**：Neo4j/FalkorDB 未做）
- 本体无版本/审批日志（**P4** 未做）；候选需人工 copy 进 TTL
- 评测"有尺子没数据"——`data/gold/` 需先人工校正一份
- 溯源只到原文片段，未到句/词级 span（**P6**）

## 6. 16 Web 使用指南（一页）

### 6.1 启动
```powershell
$py = "D:\work\wangxuanhao\conda\envs\llm_model\python.exe"
& $py apps/16_kg_web_server.py        # 或双击 apps/start_kg_server.cmd
```
浏览器打开 `http://127.0.0.1:8000`，左上角选一个**项目**（历史项目在 `data/projects`）。

### 6.2 左侧菜单速查
| 菜单 | 看什么 |
|---|---|
| 看板 | 总数、类型/谓词分布、Top 节点 |
| 图谱 | 力导向图：点节点展开邻居、搜索 |
| 实体 | 按类型分组列实体；手工加别名/关系 |
| 本体 | **三个子面板（见 6.3）** |
| 脑图 | 以某实体为根的分层树 |
| 问答 | 本地问答（实体+关系+原文） |
| 脚本 | 上传 .py/.md 文档 → 触发抽取 job |

### 6.3 本体页三个子面板（往下滚可见）
1. **📖 声明式本体**：`ontology/meituan_ontology.ttl` 的类层级 + 关系 `domain→range`（规矩原文，与"数据反推"分开看）。
2. **🔎 SPARQL 查询**：先选好项目 → 粘贴查询 → "运行"或 `Ctrl+Enter`。示例：
   ```sparql
   PREFIX mt: <http://meituan.com/kg#>
   SELECT ?m ?name WHERE { ?m a mt:Merchant ; mt:name ?name } LIMIT 20
   ```
   链式路径：`?m a mt:Merchant; mt:commits/mt:triggers ?p`。
3. **🩺 质量体检**：点"运行体检" → 列出按本体 domain/range 查出"连错类型"的关系（脏数据一目了然）。

### 6.4 让"新跑的 17 结果"进 Web
```powershell
& $py apps/17_rule_demo_semantica_api.py          # 1) 先跑 17（写 data/rule_demo_output/run_<时间>/）
# 2) 起服务后触发导入（把 rule_demo_output 下 run 建成 Web 项目）：
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/import" `
  -ContentType "application/json" -Body '{"source":"rule_demo_output"}'
# 3) 刷新左上项目下拉即可看到；进"本体"页跑体检/SPARQL
```

### 6.5 一句话记法
- 看数据长啥样 → 图谱 / 实体 / 看板；
- 看规矩合不合 → 本体页"体检 + 声明式"；
- 自定义查询 → 本体页 SPARQL 框；
- 跑完新批次 → `POST /api/import` 让它出现在列表。

## 7. 15（开放抽取）简明用法

```powershell
$py = "D:\work\wangxuanhao\conda\envs\llm_model\python.exe"
& $py apps/15_rule_demo_llm提取.py --parse-only                 # 只解析+分块自检（不调 LLM）
& $py apps/15_rule_demo_llm提取.py                              # 开放抽取默认 data/rule_demo
& $py apps/15_rule_demo_llm提取.py data/rule_txt                # 换目录批量抽
```
- 定位：**不被 17 闭集白名单卡住**，用来扫出"本体外的新类型/新谓词"，喂给本体候选（模式 B）。
- 产物：`data/rule_demo_output/`（可再导入 16 看图，见 §6.4）。

## 8. 18（本体/评测 CLI）简明用法

```powershell
$py = "D:\work\wangxuanhao\conda\envs\llm_model\python.exe"
# 任意 graph.json 都行（data/rule_demo_output/run_*/graph.json 或 data/projects/*/graph.json）
$G = "data/rule_demo_output/run_<时间>/graph.json"

& $py apps/18_ontology_owl_sparql.py info                       # 本体摘要 + 派生白名单
& $py apps/18_ontology_owl_sparql.py demo  --graph $G           # 内置 8 条能力问答
& $py apps/18_ontology_owl_sparql.py query --graph $G --query "PREFIX mt: <http://meituan.com/kg#> SELECT * WHERE { ?s ?p ?o } LIMIT 10"
& $py apps/18_ontology_owl_sparql.py validate --graph $G        # domain/range 体检
& $py apps/18_ontology_owl_sparql.py export  --graph $G --out a.ttl   # 导出 RDF
# 评测（尺子）：
& $py apps/18_ontology_owl_sparql.py eval --gen-template --pred $G --out data/gold/gold.json   # 首建 gold 模板（人工删改错项）
& $py apps/18_ontology_owl_sparql.py eval --gold data/gold/gold.json --pred $G                 # 每次改版后打分
```
- `--graph` 省略时自动取 `data/projects/` 里最新的 graph.json。
- 同一套能力在 Web「本体」页也有图形界面（§6.3）。

## 9. 16 的 REST 接口清单（常用）

`http://127.0.0.1:8000`，所有 JSON 需 `Content-Type: application/json`（{name} 用项目名，含中文需 URL 编码）。

| 方法与路径 | body/query | 用途 |
|---|---|---|
| GET `/api/projects` | — | 列出项目 |
| POST `/api/import` | `{"source":"rule_demo_output"}` | 把 run 导入为 Web 项目 |
| GET `/api/project/{name}/load` | — | 加载项目 |
| GET `/api/ontology/{name}` | — | 本体：observed + `declared` |
| GET/POST `/api/sparql/{name}` | POST `{"query":"…"}` | 跑 SPARQL |
| GET `/api/sparql-check/{name}` | — | domain/range 质量体检 |
| GET `/api/search/{name}?q=…` | — | 语义检索 |
| GET `/api/qa/{name}` / POST `/api/qa/stream/{name}` | `{"question":"…"}` | 问答 / 流式问答 |
| POST `/api/append-doc/{name}` | multipart 文件 | 追加文档触发增量抽取 |
| POST `/api/upload` | multipart | 上传脚本/文档目录 |
| POST `/api/parse` | `{"dir":"rule_demo","demo":true}` | 前台建项目 job |
| GET `/api/jobs/{job_id}` | — | 查异步 job 进度 |
| DELETE `/api/project/{name}` | — | 删除项目 |

> 其余（实体增删改/别名/合并/恢复/历史/脑图等）在 `apps/16_kg_web_server.py` 里以 `@_api` 标注，需要时 grep。

## 10. 常见坑 / FAQ

1. **17 是闭集抽取**：白名单没有的类型 LLM 不标。发现"押金"没人认领 → 多半是没设 `Payment` 类，回 TTL 补类 + 给类写中文说明（rdfs:comment）。
2. **"候选新类型/新关系"为什么经常是空的？** 因为 17 闭集挡掉了外部概念；要用 **15（开放）** 去扫，扫出来的新东西才会上报告。
3. **gold 怎么校？** `eval --gen-template` 生成模板后**人工删/改错项**（把抽错删除、漏的补上），改完才是 gold；gold 放在 `data/gold/`。
4. **改 `ontology/meituan_ontology.ttl` 后要重跑**：17 只在启动时读本体（并自动重算白名单）。重跑后再看 ontology_summary / 体检 / eval。
5. **`legacy/ontology_rdflib.ttl` 不是本体**：内容是 ex: 玩具样例；别拿去配 17/16/18。
6. **别混淆两个"projects"**：`data/rule_demo_output`（15/17 直接跑的输出，含 run 与 graph.json）；`data/projects`（Web 项目，18 `--graph` 与 16 都用）。18 的 `--graph` 指向任意一个 graph.json 即可。
7. **16 端口被占**：设置 `KG_WEB_PORT` 换端口再起。
8. **`data/model` 别删**：本地 bge-m3，删了会退化到在线下载（还可能被墙卡死）。
9. **Excel 打开文件时**：16 导入或移动数据可能被占用报错；先关掉 Excel。


