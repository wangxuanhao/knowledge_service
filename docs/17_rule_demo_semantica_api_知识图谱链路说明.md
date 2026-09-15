# 17 脚本「实体 → 关系 → 本体」链路 可解释说明

> 面向不熟悉"知识图谱/本体"的读者。先讲概念，再讲 17 现在怎么跑，
> 最后讲这套东西离"生产级"差什么、以及你要的两种场景怎么处理。
> 作者口径以代码为准；文档生成时已把 OWL 本体能力接入 17。

---

## 0. 三分钟版

**17 是"把一篇规则/协议文字，变成一张图 + 一份可检索数据"的脚本。** 图上每一个点叫**实体**（比如"快驴进货平台""押金"），点与点之间带箭头的线叫**关系**（比如"平台 禁止 某种违规"）。"线"不能乱连，得遵守一套说明书——这套说明书就是**本体（Ontology）**：它规定"世界上有哪些种类的点、哪些线允许从哪类点连到哪类点"。

17 现在做三件事：

1. 用 LLM 从原文**抽实体、抽关系**（受本体约束，只允许抽"说明书上有的类型"）；
2. 用**纯规则**把文档大纲（章/节/条）也变成点，挂上"属于"关系；
3. 抽完之后，**用本体当尺子量一遍**（domain/range 校验）把抽错的关系降权，并**写一份"本体总结报告"**，告诉你哪些新类型/新关系冒出来了、要不要补进说明书。

一句话：**本体管着抽取的"边界"，抽取反过来给本体提"增补建议"，两者构成闭环。**

---

## 1. 概念（用大白话）

| 词 | 大白话 | 例子（平台直播规则里） |
|---|---|---|
| 实体 Entity | 一句话里的"具体的人/事/物" | 大众点评、主播、直播带货、退款 |
| 实体类型 | 这个"东西"属于哪一类（标签） | 大众点评 → `Platform`；主播 → `Merchant` |
| 关系 Relation | 两个实体之间的"动词" | 平台 `publishes` 规则文档；规则 `prohibits` 某违规 |
| 本体 Ontology | "类型清单 + 类型父子关系 + 关系两边允许的类型"的**正式说明书** | `Merchant ⊂ Actor ⊂ Entity`；`commits` 只允许 `Merchant → Violation` |
| 结构化抽取 | 把文档自身的"章/节/条"结构抽出来 | `第一条【目的】` 属于 `第一章 总则` |
| TBox / A-Box | 说明书的"类与规则" / 说明书被"实例"填满后的数据 | TBox=`第X类允许连到第Y类`；A-Box=`"押金" 是 Payment 的一个实例` |

**为什么要有本体？** 因为没有本体，LLM 会随心所欲发明类型和动词（今天抽"处理措施"，明天抽"对策"），图就永远对不上、没法查。本体就是给图**上锁的规矩**。

---

### 1.5 类型 vs 本体 vs 实例 —— 最常被绕晕的三层

一句话：**"实体类型"（`ENTITY_TYPES` 里那些词）本来就是本体的"类"，只是本体比类型清单多写了"关系规矩"。**

| 层 | 叫什么 | 形式 | 例子 |
|---|---|---|---|
| ① 本体的"类" Class | 本体里的类声明 | `meituan_ontology.ttl` 里 `mt:Merchant a owl:Class` | 还带层级 `Merchant ⊂ Actor`、约束 `commits: Merchant→Violation` |
| ② 抽取类型名单 | 也叫"类型/label/类目" | 17 里 `ENTITY_TYPES=["Platform","Merchant",…]` | 只是把 ① 的**名字抄了一份**当 LLM 白名单 |
| ③ 抽出来的实例 | Entity 个体 | "合作商"这个点的 label = `Merchant` | **个体**，既不是类型也不是本体 |

要点：
- ①②是**同一批名字**。17 过去在代码里"手抄"了 ②（P3 的漂移问题）；现在已改为**启动时读 ① 的 ttl 自动生成 ②**，杜绝两处不一致。
- 本体 = ②的名字 ＋ 谁是谁的父类 ＋ 哪些关系允许连哪些名字（domain/range）。只给名字清单不叫本体，给了层级和约束才叫本体。
- 你抽出来的每个点（"合作商"）是 ③，它只是"Merchant 类的一个成员"，**不会因为被抽到就变成本体的一部分**；本体始终是那本"说明书"。

---

## 2. 17 的完整链路（图）

```
源文件（.md / .txt）
   │  strip_page_markers 清洗（去页眉页脚噪声）
   ▼
structure_aware_split  标题感知分块（约 8000 字/块，400 字重叠）
   │  chunk0 ──┐       每块都带自己的标题上下文
   ▼          ▼
SemanticaLLMExtractor（LLM，受 ENTITY_TYPES / RELATION_TYPES 白名单约束）
   ① NERExtractor.extract           每块抽实体（text + label + 置信度）
   ② RelationExtractor.extract      每块抽关系（主语-谓词-宾语）
   ▼
normalize_result  别名归一（商户/卖家/店铺 → Merchant）+ 去重
   ▼
extract_structure  【纯规则，不烧 LLM】文档根=RuleDocument；
                   章/节/条 → Chapter/Section/Article；partOf 树
   ▼
constraint_backcheck 【本体当尺子】domain/range 违规的关系 置信度压到≤0.5 并打标
   ▼
build_vector_store / build_segment_store   本地 bge-m3 → FAISS 双索引（实体级/段落级）
   ▼
build_graph   实例图 + 类节点层级（instanceOf / subClassOf 边），导出 graph.graphml
   ▼
analyze_extraction → ontology_summary.md / .json  【本体总结报告】
```

**谁负责哪种抽取：**

| 抽取内容 | 用什么 | 快慢 | 决定权 |
|---|---|---|---|
| 业务实体、业务关系 | LLM（DeepSeek） | 慢，按块 | 语义理解，但**受本体白名单约束** |
| 文档大纲（章/节/条/归属） | 纯规则正则 | 快，稳定 | 结构固定，不需要 LLM |
| 归属实体→所属条 | 纯规则（标题覆盖区） | 快 | 位置关系，规则精确 |
| 违规关系是否"合理" | 本体 domain/range | 快 | 说明书说了算（校验） |

---

## 3. 你说"实体没提取时一起提取本体，实体提取后总结本体" —— 三种模式

这三个其实是**一套方法论的两个角色、三种启动方式**：

### 模式 A：本体先行（schema-first）——17 现在默认这样
**适用：领域清楚（比如美团规则域）。**
先把本体写成型（`meituan_ontology.ttl`，即说明书），17 从说明书里**推导抽取白名单**，LLM 只能照单抽取，抽完用说明书校验。
- 优点：输出可预测、图干净、能直接问"哪些规则禁止哪些违规"。
- 这正是你现在跑的链路。本体 = 人工定的 29 类 + 24 关系。

### 模式 B：没有实体也没本体时 —— 不要"裸跑"，先给个种子
"什么都没抽、还想一步到位把本体也抽出来"是最容易翻车的：LLM 能临时发明标签，但它**记不住自己的发明**，跑 10 篇就有 20 种同义词。
**建议做法（已在产物里支持）：**
1. 先放一个**最小种子本体**（哪怕只有 5~8 类：平台/商家/用户/违规/处罚/规则文档）；
2. 用模式 A 跑，LLM 抽出来的、种子本体里没有的类型，**会被记进候选清单**（`ontology_summary.md` 第 5 节"候选新类型"）；
3. 人工扫一眼 → 把高频、有意义的补进 `meituan_ontology.ttl` → 重跑。
这样是"**小本体起步、数据喂大本体**"，而不是让 LLM 一次性发明整个本体。

> **种子选类注意（重要，容易踩坑）：** 17 的 NER 是**闭集抽取**——白名单里没有的类型，LLM 不会标出来。
> 所以如果种子只给 5~8 类却漏了 `Payment`，文本里的"押金"就**没人认领**（漏抽或硬塞进别的类）。
> 因此：
> 1. 种子要挑**最高频、最确定**的那几类起步（平台/商家/用户/规则文档/违规/处罚 这类准没错）；
> 2. 发现"总抽不准/总漏"往往不是模型差，而是**类别没设全或两类边界不清**——回到 ttl 补类或给类的说明（`rdfs:comment`）加例子；
> 3. 真正"冒新概念"要靠**开放模式**（15 脚本）来扫，它的产物报告（`ontology_summary.md` 第 5 节）才会列出本体没有的类型/谓词供你补。**17 闭集模式下，这个候选清单通常是空的**——空不等于没问题，只是说明"新东西都被边界挡在外面了"。

### 模式 C：实体已经抽了 → 总结本体（schema-last / 归纳）
17 每次跑完会自动产出 `ontology_summary.md`，内容就是"给本体做的小结 + 提案"：
- 每个实体类型出现多少个、是业务还是结构、是不是本体已知；
- 每个谓词用了几次、是否已知、样例是什么；
- **约束体检**：多少关系违反 domain/range、违在哪；
- **孤儿边**：多少关系两端在实体里找不到（典型的抽取污染）；
- **候选新类型/新谓词**：冒出来的、本体没定义的东西 → 人工审阅后补 TTL。

这就是"实体提取后总结本体"的最小可用实现；要更强，需要上**评测集 + LLM 辅助归纳**（见第 5 节生产清单 P1/P5）。

---

## 4. 本体在这里到底"管"了什么（对照代码）

| 本体能力 | 在 17 的实现 | 关在哪个环节 |
|---|---|---|
| 抽取边界 | `ENTITY_TYPES`/`RELATION_TYPES` —— 已改为**启动时从 TTL 派生**（P3 已落地，回退硬编码） | NER/关系 prompt |
| 文档结构类 | `Chapter/Section/Article ⊂ DocumentPart` | `extract_structure` |
| 结构归属 | `partOf`（条→章→文档根） | `extract_structure` |
| 实例挂类型 | 图与 RDF 里每个点带 `rdf:type` + `instanceOf` 边 | `build_graph` / `ontology_owl.build_dataset` |
| 类型层级 | `subClassOf` 边 + RDF 物化（查 `Actor` 能命中 `Merchant` 实例） | `build_graph` / `build_dataset` |
| 抽取校验 | `domain/range` → 违规降权打标 | `constraint_backcheck` |
| 本体总结/提案 | `analyze_extraction` → md/json | 运行收尾 |

---

## 5. 离"生产级"还差什么？（诚实评估）

当前 17 更接近**可复现的演示/内测级**，不是生产级。逐条说：

| # | 缺口 | 影响 | 优先级 |
|---|---|---|---|
| P1 | **没有评测集/自动打分**（`图谱设计.md §7` 定了覆盖率/召回口径）→ **脚手架已加**（`18 … eval`，见 §8），还差一份人工 gold | 不知道新版本是变好还是变差 | 最高（只差数据） |
| P2 | 图在**内存**里、每跑一遍重建 | 不能增量更新，数据多了扛不住 | 高 |
| P3 | `ENTITY_TYPES/RELATION_TYPES` 与 TTL 两份维护 → **已解决**：17 启动时从 ttl 派生（缺失回退） | 改本体后自动同步 | 已解决 |
| P4 | 无**本体版本/变更审批日志**；候选要人工 copy 进 TTL | 协作和回溯难 | 中 |
| P5 | "总结本体"目前是**统计+候选建议**，无 LLM 归纳/合并同义类 | 候选还得人看 | 中 |
| P6 | 溯源只到"原文片段"，没精确到**句/词级 span**；跨块实体合并靠文本相等 | 举证与召回偏弱 | 中 |
| P7 | 无并发、无鉴权、单机 | 只适合本地/小规模 | 低（视用途） |

**建议的工程化顺序**：P1（先有尺子）→ P3（单一来源，成本最低）→ P2（落图库 Neo4j/FalkorDB）→ P4/P5（本体治理+LLM 归纳）→ P6（span 精化）。

---

## 6. 一次运行产出什么（out_dir 里）

| 文件 | 内容 |
|---|---|
| `graph.graphml` | 图（含类节点 + instanceOf/subClassOf），可被图工具打开 |
| `entities.json` / `*.json` | 单文件实体/关系明细（含置信度、来源、命中段落） |
| `summary_<时间>.json` | 全批汇总 |
| `faiss/entities.index`、`faiss/segments.index` | 实体级/段落级向量库 |
| `run_info.json` | 谁跑的、什么模型、什么参数 |
| **`ontology_summary.md`** | **本体总结报告：分布 + 约束体检 + 候选新类型/新关系（人工审阅入口）** |
| `ontology_summary.json` | 上面的机器可读版 |

---

## 7. 常用问题（FAQ）

- **为什么章/条也算实体？** 因为它们要能回答"这条处罚写在第几条"这类问题。它们属于结构类（Article/Chapter），不是业务实体，所以在统计里被单独分到"结构型"，不会污染业务类型计数。
- **置信度为什么之前全一样？** LLM 抽取时"置信度"是模型自己报的，模型偷懒就统一报 1.0（占位符，不是算出来的）。17 现在用**本体校验**和（后续建议的）**证据打分**让它有区分度。
- **改了 `meituan_ontology.ttl` 要重跑吗？** 要。17 只在**启动时**读本体（顺带自动重算抽取白名单），所以改完 ttl 需重跑才生效。这也正是 P4"本体版本化"要解决的事。
- **能不能让 LLM 自己把候选直接写进本体？** 不建议全自动——补类型/关系 = 改"业务规矩"，应该有**人工闸**。折中：LLM 出提案（`ontology_summary.md` 第 5 节），人点批准。

---

## 8. 关键文件分工 & 17 实操怎么用

### 8.1 那些文件分别干什么

| 位置/文件 | 角色 | 一句话 |
|---|---|---|
| `apps/17_rule_demo_semantica_api.py` | **抽取主脚本** | 读文件 → LLM 抽实体/关系 + 规则抽结构 → 校验 → 建图/建向量 → 出报告 |
| `apps/18_ontology_owl_sparql.py` | **本体/评测命令行** | `info`/`query`/`demo`/`validate`/`eval` 的入口 |
| `apps/16_kg_web_server.py` | **Web 服务** | 可视化 + `/sparql`、`/ontology`(declared+observed)、`/sparql-check` |
| `apps/15_rule_demo_llm提取.py` | **开放抽取脚本** | 可扫出本体外的新类型/新谓词（喂候选，配合模式 B） |
| `kgcore/ontology_owl.py` | **本体引擎（库）** | 读 ttl、派生白名单、结果变 RDF、SPARQL、domain/range 校验、本体总结 |
| `kgcore/kg_eval.py` | **评测库（"尺子"）** | gold vs pred → 覆盖率/精确率/召回/F1/噪声 |
| `kgcore/kg_project.py` + `kgcore/paths.py` | 项目存取 / 统一路径源 | Web 项目与各 app 都从这里取目录 |
| `ontology/meituan_ontology.ttl` | **我们的本体（单一来源，唯一该改的 ttl）** | 类层级 + 关系约束 + 文档结构类；改它 = 改业务规矩 |
| `legacy/ontology_rdflib.ttl` | ⚠️ **早期教学示例，不是美团本体** | `ex:AIModel/City/ceo_of`；和主线无关，仅作参考 |
| `docs/图谱设计.md` | 设计稿 | 本体 §4 从这里来；§7 是评测口径 |
| `legacy/01~14` | 早期教学脚本 | 与本体闭环无关，已归档 |

> **注意区分两个 ttl**：`meituan_ontology.ttl`（新、正式、mt: 前缀）负责你所有的抽取/校验/查询；`ontology_rdflib.ttl`（旧、示例、ex: 前缀）不要拿去当本体用。

### 8.2 17 实操步骤（跑通一次本体闭环）

```powershell
# 环境（在仓库根 知识图谱/ 下执行；脚本都在 apps/，数据在 data/）
$py = "D:\work\wangxuanhao\conda\envs\llm_model\python.exe"

# 0) 先看本体 & 白名单（确认启动会派生哪 18 类 / 22 关系）
& $py apps/18_ontology_owl_sparql.py info

# 1) 跑 17 抽取（默认读 data/rule_demo，输出到 data/rule_demo_output/run_<时间>/）
& $py apps/17_rule_demo_semantica_api.py

# 2) 打开本体总结报告（看分布/约束体检/候选建议）
#    data/rule_demo_output/run_<时间>/ontology_summary.md

# 3) 在 RDF+SPARQL 上做能力问答（用同一 run 新落盘的 graph.json）
& $py apps/18_ontology_owl_sparql.py demo  --graph "data/rule_demo_output/run_<时间>/graph.json"
& $py apps/18_ontology_owl_sparql.py query --graph "data/rule_demo_output/run_<时间>/graph.json" `
    --query "PREFIX mt: <http://meituan.com/kg#> SELECT ?m ?v ?p WHERE { ?m a mt:Merchant; mt:commits ?v. ?v mt:triggers ?p }"
& $py apps/18_ontology_owl_sparql.py validate --graph "data/rule_demo_output/run_<时间>/graph.json"

# 4) 当"尺子"用：先给这次可信结果生成 gold 模板，人工删改错项存为 data/gold/gold.json
& $py apps/18_ontology_owl_sparql.py eval --gen-template `
    --pred "data/rule_demo_output/run_<时间>/graph.json" --out "data/gold/gold.json"
# 以后每次改动后和 gold 比分数：
& $py apps/18_ontology_owl_sparql.py eval --gold "data/gold/gold.json" --pred "data/rule_demo_output/run_<时间>/graph.json"
```

### 8.3 改本体 → 重跑的最小循环

```
编辑 meituan_ontology.ttl（加类/关系/约束）
   ↓ 重跑 17（启动自动派生新白名单）
   ↓ 看 ontology_summary.md（体检）
   ↓ 用 eval 对 gold 打分（确认没变差）
   ↓ 需要更细？→ 18 query / 16 Web 看结果
```

---

## 9. 一页速览（决策版）

- **现在：** 本体先行模式已闭环 —— 说明书(29类/24关系，启动自动派生白名单) → 引导抽取 → 结构树 → 校验降权 → 层级图 → 本体总结报告；"尺子"脚手架（`eval`）已就位。
- **你要的两种场景：**
  - 还没实体 → 先放种子本体 → 跑 → 用"候选新类型"迭代扩本体（注意闭集抽取的选类坑）；
  - 已抽实体 → 看 `ontology_summary.md` 做本体小结与扩本体提案。
- **距离生产：** 单来源(P3)已解决、"尺子"(P1)脚手架已加但缺一份人工 gold；还差"增量落图库(P2)"与"本体版本化(P4)"。P1 补齐数据 + P2/P4 落地后，即可算"内测可用级"。
