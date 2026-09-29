# 候选证据解析与切片定位设计

## 背景

本体发现工作台当前把候选记录中的 `evidence` 截取前 500 字后直接嵌入候选脑图响应。`test_0928_开放` 的历史候选保存的是完整切片，而不是候选附近的精确引文，因此候选位于切片第 500 字以后时，右侧“来源证据”看不到候选内容，容易被误判为模型生成了无来源实体。

该项目的 683 条候选断言都保存了 `document_version_id`、`chunk_id` 和完整 `quote`。其中 429 条坐标为 `0–1800`，254 条为 `1600–2340`；这些是切片范围，不是候选精确范围。原始文档版本和切片没有丢失，问题在于候选工作台绕过了版本化证据解析链，且没有区分“精确定位”和“切片级定位”。

## 目标

- 候选来源始终解析到固定的历史文档版本，而不是当前可变文档。
- 点击候选时展示对应完整切片，并在能够可靠定位时高亮候选证据。
- 明确区分精确坐标、历史切片内恢复定位和仅切片定位，不能把恢复结果冒充原始精确坐标。
- 保留 `document version → chunk → assertion/candidate` 的可审计链路。
- 候选列表保持轻量，不为数百个候选预加载全部 1800 字切片。
- 不要求重新抽取或改写 `test_0928_开放` 的历史候选。

## 非目标

- 不迁移或重写历史 assertion 行。
- 不改变正式知识记录的审核、发布或物化语义。
- 不把 SQLite 之外的存储提升为 provenance 真值源。
- 不在候选列表接口中返回整篇文档或全部切片正文。
- 不推测无法由固定历史版本验证的来源位置。

## 方案选择

### 采用：断言级证据解析接口

候选脑图只返回候选与来源引用；用户点击候选后，前端按 assertion ID 请求证据解析接口。服务端读取 assertion 固定的 `document_version_id`、`chunk_id`、坐标和 quote，从历史文档版本还原完整切片和高亮位置。

该方案与现有正式记录证据服务一致，按需加载、版本可靠，并避免候选脑图响应随候选数量乘以切片大小膨胀。

### 不采用：候选脑图直接返回完整切片

实现简单，但一个实体聚合节点可包含多个来源，数百候选会重复返回大量 1800 字切片，增加后端序列化、传输和前端状态成本。

### 不采用：前端直接读取 chunk 记录

前端无法安全处理历史文档版本固定、坐标语义、source hash 校验和旧数据恢复规则，会把 provenance 业务逻辑散落到展示层。

## 不变量与定位等级

每条可展示来源必须包含：

- `assertion_id`
- `document_id`
- `document_version_id`
- `chunk_id`
- `chunk_start_char`、`chunk_end_char`
- `evidence_start_char`、`evidence_end_char`（只有精确或可验证恢复时存在）
- `location_mode`
- `source_hash_status`

`location_mode` 固定为：

- `exact`：写入时保存的区间与 quote 在固定文档版本中完全一致；这是权威精确位置。
- `recovered_in_chunk`：历史记录只保存切片范围；服务端在固定切片中唯一匹配候选内容后得到展示位置。返回恢复坐标，但明确标记不是原始抽取坐标。
- `chunk`：可以解析固定切片，但无法唯一定位候选内容；展示完整切片，不高亮精确内容。
- `unlocated`：固定文档版本或切片无法解析；不得回退到当前文档伪造位置。

`recovered_in_chunk` 只用于展示定位，不回写 assertion，也不提升为 `exact`。若候选文本在切片中出现多次，实体候选不任选第一次，而降级为 `chunk`。关系候选优先使用 assertion quote；历史整切片 quote 下只有在 subject/object 能形成唯一最小覆盖区间时恢复。属性候选优先使用 `attribute_evidence`，且必须在固定切片中唯一匹配。

## 服务边界

在 `services/evidence.py` 提取可复用的断言来源解析器，例如：

```python
resolve_assertion_evidence(repository, project_id, assertion_id) -> dict
```

解析流程：

1. 使用 `project_id + assertion_id` 读取 assertion，禁止跨项目访问。
2. 按 `document_version_id` 从文档历史读取不可变文档版本；不存在时返回 `unlocated` 和稳定 warning。
3. 通过 `chunk_id` 读取对应 chunk，并校验其 `source_id`、文档版本操作批次/记录时间和绝对范围。首期允许从固定文档版本按 assertion 保存的切片范围重建 chunk；不能解析时返回 `unlocated`。
4. 校验 `source_hash`（存在时）是否与固定文档正文一致，返回 `matched`、`mismatched` 或 `unavailable`。
5. 判断 assertion 的坐标与 quote 是否是精确证据。区间文本等于 quote 且区间小于完整切片时返回 `exact`。
6. 对历史切片级 assertion，在完整切片内部执行候选种类对应的唯一匹配，返回 `recovered_in_chunk` 或 `chunk`。
7. 返回完整切片、用于紧凑卡片的预览、highlight 及前后文。完整切片只由按需接口返回。

现有正式记录 `evidence()` 继续保留对 canonical record 的入口，但复用同一个断言解析器，避免候选证据与正式知识证据出现不同定位规则。

## HTTP 接口

新增只读接口：

```text
GET /api/projects/{project_id}/assertions/{assertion_id}/evidence
```

成功响应示例：

```json
{
  "assertion_id": "candidate-id",
  "assertion_status": "pending",
  "candidate_kind": "entity",
  "document": {
    "id": "document-id",
    "version_id": "document-version-id",
    "version": 1,
    "title": "主播头像与昵称使用规范.md",
    "source_content": "original"
  },
  "chunk": {
    "id": "document-id:chunk:0",
    "start_char": 0,
    "end_char": 1800,
    "text": "完整切片正文"
  },
  "location": {
    "mode": "recovered_in_chunk",
    "start_char": 1062,
    "end_char": 1068,
    "before": "前文",
    "highlight": "不文明用语",
    "after": "后文",
    "reason": "历史断言仅保存切片范围，已在固定切片内唯一恢复展示位置"
  },
  "integrity": {
    "source_hash_status": "matched",
    "warnings": []
  }
}
```

assertion 不存在或不属于项目时返回稳定 404。文档版本缺失、hash 不匹配或切片无法重建时仍返回 200，并通过 `location.mode=unlocated`、`integrity.warnings` 和 `integrity.complete=false` 表达历史缺口。

## 候选脑图响应

`/ontology-discovery/candidate-mindmap` 的每个 source 改为轻量引用：

- 增加 `assertion_id`；
- 保留文档标题、文档 ID、文档版本 ID、chunk ID、坐标、置信度和 evidence status；
- 不再把截断 `evidence` 当作可核验原文；兼容期可保留短 `evidence_preview`，但前端不得将其标记为完整来源证据。

聚合节点的 `candidate_ids` 与 `sources[].assertion_id` 必须一一对应，来源排序稳定。最多 10 个来源的现有限制保留，并明确返回 `source_count` 与 `sources_truncated`，不能静默让用户误以为这是全部来源。

## 前端交互

候选右侧检查器调整为：

1. 点击候选后立即展示候选结构信息和来源列表骨架。
2. 对可见来源按 assertion ID 请求证据接口；使用独立 `AbortController`，切换候选或项目时取消旧请求。
3. 每张来源卡显示文档标题、历史版本、chunk ID、切片绝对范围、定位等级和完整切片。
4. `exact` 与 `recovered_in_chunk` 高亮目标文本；`recovered_in_chunk` 显示“历史切片内恢复定位”，不得显示“精确抽取位置”。
5. `chunk` 显示完整切片并提示“该历史候选只保存了切片级位置”。
6. 提供“查看完整历史原文”按钮，复用现有 frozen source viewer，并定位到固定文档版本的 chunk 范围或高亮范围。
7. 多来源独立加载；单个来源失败不清空其他来源。
8. 所有正文使用 `textContent` 渲染，禁止作为 HTML 注入。

## 新写入数据

当前摄取链会优先使用模型返回的 exact evidence，并计算其在 chunk 中的局部位置后转换为文档绝对坐标。继续保持：

- assertion `quote` 保存精确原文证据；
- `start_char/end_char` 保存文档绝对精确区间；
- `chunk_id` 保存容器切片；
- `document_version_id` 固定原始文档版本；
- `source_hash` 用于完整性校验。

若模型证据无法在 chunk 中定位，新写入不得把整个切片伪装成 exact。应产生 evidence exception，或以 `chunk` 等级保存并进入人工核对；具体沿用当前异常隔离规则。

## 性能与安全

- 候选脑图不读取历史正文，保持当前聚合复杂度。
- 证据接口每次只解析一个 assertion；前端只请求当前选中候选的最多 10 个可见来源。
- 文档版本和 chunk 可在单次检查器生命周期内按 version/chunk ID 缓存，避免同一聚合候选重复读取。
- 所有 repository 查询必须带项目 ID。
- 不返回 API key、供应商响应或模型隐藏推理。
- 完整切片正文属于项目业务数据，沿用现有项目访问边界。

## 测试策略

### 服务测试

- 精确 assertion 从固定历史文档版本返回 `exact` 和正确 highlight。
- 历史 `0–1800` 切片级实体候选位于第 500 字之后时，返回完整切片并以 `recovered_in_chunk` 高亮。
- 同一实体文本在切片出现多次时降级为 `chunk`，不伪造精确位置。
- 关系两端能唯一形成覆盖区间时恢复；不能唯一恢复时降级。
- 属性 exact evidence 正确定位；不存在时返回 warning。
- 文档当前版本修改后仍读取 assertion 固定的历史版本。
- 文档版本、chunk 或 hash 缺失/不一致时返回部分结果和稳定 warning。
- 跨项目 assertion 返回 404。
- 正式记录 evidence 与 assertion evidence 对同一 assertion 返回一致的版本和定位等级。

### API 测试

- 新接口响应字段、404 和 partial integrity 契约。
- candidate mindmap source 包含 assertion ID、document version、chunk 范围和 truncation 元数据，不把 preview 宣称为完整证据。
- `test_0928_开放` 同形 fixture 中，候选词位于 500 字以后仍可通过点击接口看到。

### 前端契约测试

- 点击候选才请求 assertion evidence；初始脑图加载不下载完整切片。
- 来源卡显示版本、chunk ID、范围和定位等级。
- exact/recovered 高亮、chunk 降级提示、unlocated warning。
- 切换候选和项目取消旧请求，旧响应不能覆盖新检查器。
- 完整历史原文入口携带固定 version ID 和定位范围。
- 证据正文不经 `innerHTML` 注入。

## 完成标准

- `test_0928_开放` 中候选点击后展示其对应完整切片，候选内容可见；历史切片级位置有明确标识。
- 候选来源能够进入固定历史文档版本并显示 chunk ID 与绝对范围。
- 候选脑图不再将前 500 字摘要冒充完整证据。
- 现有正式知识证据接口与候选证据使用同一定位规则。
- 新数据保持精确坐标，旧数据无需重抽取即可可靠展示可恢复的证据。
- 相关服务、API、前端契约测试通过，且不引入跨项目来源读取。
