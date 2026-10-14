# 批量知识写入、断点续跑与 Markdown 原文溯源设计

日期：2026-10-14
状态：可交付实施
目标版本：在现有知识写入链路上增量演进，不引入新的强制流程向导

## 1. 业务目标

本次改造服务于一个明确目标：让现有知识的入图、审核和维护更顺畅，尤其能够稳定处理后续约 5000 份规则知识脚本。

用户完成改造后应当能够：

1. 在“知识写入”页面一次建立最多 1000 份文档的上传批次，不需要逐个文件重复操作。
2. 为本批次选择“自动、1、2、4、8”解析并发；页面显示用户申请值、服务端实际采用值及限流原因。
3. 继续使用三种现有写入模式：本体约束提取、开放发现、仅文档。并发、重试和来源能力不能改变三种模式的业务语义。
4. 看见每个文件目前处于上传、解析、切片、模型抽取、融合校验、正式写入、待审核或失败中的哪一步。
5. LLM 超时、断网、429、502/503 时先由系统自动重试；自动重试耗尽后，前端明确显示错误并提供人工操作。
6. 人工选择“从断点继续”“只重试失败切片”或“整份重新处理”，不会因普通临时故障重复处理已经成功的切片。
7. 上传 Markdown 与本地图片组成的知识包；系统永久保存原始 Markdown、附件和内容哈希。
8. 对 ZIP 知识包中的本地图片执行 OCR，将带坐标和置信度的识别文字纳入切片与信息提取；OCR 结果可独立重试和审计。
9. 在原文阅读器中分别查看渲染视图、解析视图和原始源码；Markdown 表格正常渲染，图片引用、OCR 状态和识别文字清楚可见。
10. 点击图谱实体、关系或属性后，先看到支持该知识的解析证据，再打开固定版本的原始文档并定位到对应段落、表格或图片 OCR 区域。
11. 批次和文档失败不能产生半写入的正式知识；已成功提交的其他文件和历史知识不受影响。

## 2. 成功标准

### 2.1 功能标准

- 一个批次可包含 1–1000 个文档条目，具体文件和批次体积上限由服务端能力接口返回，前端不再硬编码。
- 页面并发选项完整可用；实际并发永远不超过服务端、模型供应商和批次三者的最小上限。
- 服务重启后，运行中的条目转为“可恢复”，重新领取后只继续未完成切片。
- 同一检查点重复提交不会重复产生实体、关系、属性或审核项。
- Markdown 相对路径图片在知识包中存在时可正常展示并进入 OCR；缺失时显示“附件缺失”，不能静默显示破图。
- OCR 输出保存引擎、版本、语言、置信度、文字块和图片坐标；重试相同图片和配置时可复用成功收据。
- 本体约束提取和开放发现模式会从 OCR 文本抽取知识；仅文档模式只索引 OCR 文本，不生成图谱知识。
- 网络图片首期只登记外部 URL 和替代文本，不主动下载，也不能作为已验证的知识证据。
- OCR 失败或结果低于最低质量门时不能从该图片生成知识断言，页面必须要求重试、忽略该图片或人工补充文字。
- 来自 OCR 的断言能回溯到固定图片资产和一个或多个坐标框。
- 原文、解析块、切片、抽取结果和最终断言之间存在稳定版本引用。

### 2.2 可操作性标准

- 用户无需打开日志即可理解失败原因和下一步操作。
- 批次首页能直接看到总数、上传中、排队、处理中、待审核、成功、失败、暂停数量。
- 1000 条文件记录使用分页或虚拟列表，不一次性创建 1000 张复杂 DOM 卡片。
- 所有重试按钮在点击前说明复用范围，点击后显示新的尝试次数和恢复起点。

### 2.3 性能与可靠性标准

- 1000 条批次列表首次响应只返回汇总和一页条目。
- 任务领取使用数据库租约；同一条目在同一时刻只有一个有效执行者。
- LLM 并发使用信号量和服务端上限，不因浏览器选择 8 而无限创建线程。
- 正式写入以单文档为事务边界；某个文件失败不会回滚其他已完成文件。
- 图谱继续以邻域和检索结果渲染，不能把 5000 份文档产生的全部节点一次性推给浏览器。

## 3. 当前能力与确认缺口

### 3.1 已有能力

- 多文件选择和逐文件上传。
- PostgreSQL 持久任务、租约、取消、重试和恢复接口。
- 任务页已有失败文本和“重试任务”按钮。
- 上传失败时暂存文件保留，成功后删除。
- `ingest_stage_output` 可保存摄取阶段输出。
- assertion 已保存文档版本、切片、字符区间、quote 和 source hash。
- 前端能够打开冻结的解析文本并高亮证据。

### 3.2 确认缺口

- 当前前端单批最多 20 个文件、总计 100 MB，并逐文件串行提交。
- `Jobs` 默认固定 2 个工作者，页面不能选择批次并发。
- 当前手动重试会重新执行整份文档；切片级抽取输出没有形成完整检查点。
- `.md` 只是 UTF-8 普通文本，没有 Markdown AST、表格结构、图片资产和源码映射。
- 成功后暂存文件删除，没有独立永久原始文件仓库。
- Markdown 图片路径只作为文本进入解析器；系统看不到图片像素。
- 现有来源定位到解析后的文本，无法进一步定位到 Markdown 标题、表格单元格或图片附件。

## 4. 范围与非目标

### 4.1 本次范围

1. 批次上传和服务端能力协商。
2. 页面选择解析并发，服务端执行实际限流。
3. 批次/文件/切片三级状态和重试。
4. LLM 临时错误自动重试、熔断、人工恢复。
5. 原始文件和附件永久存储。
6. Markdown 知识包、AST 解析、表格和图片引用。
7. 本地图片 OCR、OCR 文字切片、信息提取和图片区域证据。
8. 原文阅读器与图谱证据定位。
9. 操作手册、接口契约和故障处置说明。

### 4.2 本次不做

- OWL 2 RL 或其他新推理器。
- 流程图连线、图标、几何布局和无文字视觉语义理解。首期图片知识只来自 OCR 识别文字，不声称理解完整画面。
- 自动抓取并永久保存任意网络图片。
- 将知识写入页面改造成必须逐步通关的工作流。
- 一次性在浏览器渲染整个大型图谱。
- 替换现有本体约束提取、开放发现和仅文档三种模式。

## 5. 推荐架构

采用“批次控制面 + 文档执行单元 + 切片检查点 + 来源对象仓库”的分层结构。

```text
知识写入页面
  ├─ 创建批次与选择并发
  ├─ 以小并发上传每个文件/知识包
  └─ 查看批次汇总、文件状态与人工操作
          │
          ▼
BatchIngestService（批次控制面）
  ├─ 批次设置和服务端限额
  ├─ 文件条目、状态与租约
  ├─ 自动重试、暂停和熔断
  └─ 汇总批次进度
          │
          ▼
DocumentIngestRunner（单文档执行单元）
  ├─ 固化原始文件
  ├─ 解析为结构化块
  ├─ OCR 本地图片并生成带坐标文字块
  ├─ 切片与检查点复用
  ├─ LLM 抽取
  ├─ 融合、本体校验和审核分流
  └─ 单文档事务提交
          │
          ├───────────────┐
          ▼               ▼
SourceObjectStore      PostgreSQL
原文件与附件字节       批次、条目、块、检查点、映射和引用
```

现有 `Jobs` 保留给其他后台任务。批量摄取新增专用的批次领取器，不把 1000 个文件直接平铺成任务页里的 1000 个通用任务。任务页只显示批次级卡片，批次详情页再分页展示文件。

## 6. 批次、并发与状态模型

### 6.1 并发定义

页面字段命名为“解析并发”，选项为：

- 自动推荐（默认）
- 1
- 2
- 4
- 8

需要同时区分四个并发：

1. **上传并发**：浏览器同时上传多少个文件，默认 3，不暴露高级配置。
2. **文档执行并发**：服务端同一批次同时处理多少份文档，由页面选择和服务端上限共同决定。
3. **LLM 请求并发**：所有批次共享的模型调用上限，由服务端配置控制；单批次不能突破它。
4. **OCR 并发**：本地图片 OCR 的全局上限，由服务端根据 CPU/GPU 和 OCR 引擎配置；页面的“解析并发”不能突破它。当前真机是 CPU Paddle，首期默认 1，中文规则图压测通过后最多先开放到 2。

实际文档并发计算：

```text
effective_concurrency = min(
  requested_or_auto_concurrency,
  server_document_concurrency_limit,
  provider_concurrency_limit,
  remaining_global_capacity
)
```

页面必须同时显示：

```text
申请并发：8
实际并发：4
原因：服务端当前上限为 4
```

“自动推荐”基于服务端能力和当前负载决定，浏览器不自行猜测。

### 6.2 状态

批次状态：

```text
draft → uploading → queued → running → completed
                         ↘ paused
                         ↘ completed_with_errors
                         ↘ cancelled
```

文档条目状态：

```text
uploading → staged → parsing → chunking → extracting
→ merging → validating → committing → completed
```

异常状态：

- `retry_wait`：系统将在指定时间自动重试。
- `needs_attention`：自动重试已耗尽，需要人工处理。
- `interrupted`：服务重启或租约丢失，可从检查点恢复。
- `skipped`：用户明确跳过。
- `cancelled`：用户取消且没有继续执行。

批次状态从条目聚合产生，不由前端自行维护。

## 7. 自动重试、手动重试与断点续跑

### 7.1 错误分类

服务端将内部异常映射为稳定错误码：

- `llm_timeout`
- `llm_rate_limited`
- `llm_unavailable`
- `llm_auth_failed`
- `llm_model_not_found`
- `document_parse_failed`
- `source_asset_missing`
- `ocr_engine_unavailable`
- `ocr_failed`
- `ocr_low_confidence`
- `checkpoint_incompatible`
- `ontology_version_changed`
- `commit_failed`
- `unknown_failure`

前端展示公开消息、失败阶段、失败切片、尝试次数和建议动作，不展示密钥、请求头或供应商完整响应。

### 7.2 自动重试

只对可恢复错误自动重试：网络超时、连接中断、429、502、503。默认退避为 5 秒、15 秒、45 秒、2 分钟、5 分钟，并加入少量随机抖动。

同一批次在短时间内出现高比例相同 LLM 错误时触发批次熔断：

- 停止领取新条目。
- 正在执行的请求在安全检查点结束。
- 批次变为 `paused`。
- 页面显示共同原因和“检测连接后继续”按钮。

认证失败、模型不存在、文档损坏等不可恢复错误不进行无意义自动重试，直接进入 `needs_attention`。

### 7.3 手动操作

失败文件提供：

- **从断点继续**：校验配置指纹后复用全部成功切片，继续失败和未完成切片。默认操作。
- **只重试失败切片**：只重新执行明确失败的切片；未开始切片仍保持待处理。
- **整份重新处理**：创建新尝试并重新解析、切片和抽取；旧尝试及证据保留。
- **跳过文件**：将条目标为 `skipped`，批次继续。

批次提供：

- 重试全部失败文件。
- 暂停。
- 继续。
- 取消未开始文件。
- 下载失败明细。

### 7.4 检查点指纹

每个切片检查点保存：

- 原始文件 SHA-256。
- 文档版本。
- 解析器及版本。
- 切片 ID、序号、文本 SHA-256。
- 提取模式。
- 本体版本。
- 模型标识。
- 提示词/提取器版本。
- 抽取设置。
- 实体、关系、属性原始结构化输出。
- 尝试次数、错误码、开始和完成时间。

以下字段任一变化时，旧检查点不自动复用：文件哈希、切片文本哈希、提取模式、本体版本、模型、提示词版本或关键抽取设置。页面说明不兼容原因，并提供“沿用旧配置继续”或“按新配置整份重跑”。

## 8. Markdown 知识包与永久来源存储

### 8.1 支持格式

继续支持单文件 `.md`。若 Markdown 引用了本地图片，推荐上传 ZIP 知识包：

```text
rules-batch.zip
├── rule-001.md
├── rule-001/
│   └── images/process.png
├── rule-002.md
└── shared/images/device.jpg
```

首期允许 ZIP 内包含多份 Markdown。可选 `manifest.json` 用于显式标题、文档路径和 metadata；没有 manifest 时自动发现 `.md`。所有相对图片路径必须解析到 ZIP 根目录以内。

### 8.2 图片类型

- 相对路径图片：从知识包提取、永久保存、计算哈希并建立资产引用。
- 网络图片：保存 URL、alt 和出现位置；首期不下载，阅读器显示“外部图片，未固化”。
- `data:` Base64 图片：允许解析但设定体积上限，解码后作为资产保存；不推荐生产大量使用。
- 缺失图片：文档仍可处理，但产生 `source_asset_missing` 警告，来源完整性标为不完整。

### 8.3 来源四层模型

1. **原始来源**：原文件/知识包字节、文件名、MIME、大小、SHA-256、版本和存储键。
2. **解析版本**：Markdown 标题、段落、列表、代码、表格、图片等块，以及源码行号、字符区间和标题路径。
3. **附件资产**：图片字节、MIME、SHA-256、尺寸、原始相对路径、alt 和所属文档版本。
4. **OCR 派生版本**：图片资产哈希、OCR 引擎和版本、语言配置、预处理指纹、文字块、坐标框、置信度和规范化全文。

对象字节保存在 `SourceObjectStore`，数据库只保存元数据和稳定存储键。首期实现本地内容寻址文件系统，接口为未来 S3/MinIO 保留替换空间。

### 8.4 OCR 模型目录与运行时

本项目采用 PaddleOCR 模型族的小模型权重，首期推荐 **PP-OCRv6 small ONNX + RapidOCR/ONNX Runtime**。这是 PaddleOCR 检测/识别模型的轻量本地推理方案，能返回文字、置信度和坐标框。

真机检查结果：

- `llm_model` 已安装 Paddle 3.3.0、PaddleOCR 3.7.0、RapidOCR 3.9.2 和 ONNX Runtime 1.20.1。
- Paddle 当前为 CPU 版本，没有 CUDA。
- 官方 `paddleocr` 包直接导入会经过 PaddleX、LangChain、Transformers 和 PyTorch；当前 PyTorch 的 `shm.dll` 依赖加载失败，因此不能作为本项目首期稳定运行时。
- RapidOCR/ONNX Runtime 可以正常初始化并运行 PP-OCRv6 small；现有检测、方向和识别模型合计约 31.7 MB。
- 在仓库现有 957×435 测试图片上，CPU 初始化约 0.57 秒、单图约 2.9 秒。该数字只用于初始并发判断，正式验收需使用中文规则图片重新基准测试。

当前已验证权重清单：

| 文件 | 大小 | SHA-256 |
| --- | ---: | --- |
| `PP-OCRv6_det_small.onnx` | 9,929,594 B | `090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f` |
| `ch_ppocr_mobile_v2.0_cls_mobile.onnx` | 585,532 B | `e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c` |
| `PP-OCRv6_rec_small.onnx` | 21,234,383 B | `6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884` |

模型目录与向量模型严格分开：

```text
data/model/
├── embedding/
│   └── bge-m3/
│       ├── config.json
│       ├── modules.json
│       └── ...
└── ocr/
    └── pp-ocr-v6-small/
        ├── det/PP-OCRv6_det_small.onnx
        ├── cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx
        ├── rec/PP-OCRv6_rec_small.onnx
        ├── rapidocr.yaml
        └── manifest.json
```

现有向量模型位于 `data/model` 根目录，实施时迁移到 `data/model/embedding/bge-m3`，并把 `KG_EMBEDDING_PATH` 一并更新。不能先移动权重再修改配置，以免服务启动时找不到向量模型。

新增配置：

- `KG_IMAGE_OCR_BACKEND=rapidocr`
- `KG_IMAGE_OCR_MODEL_ROOT=data/model/ocr/pp-ocr-v6-small`
- `KG_IMAGE_OCR_CONFIG=data/model/ocr/pp-ocr-v6-small/rapidocr.yaml`
- `KG_IMAGE_OCR_DEVICE=cpu`
- `KG_IMAGE_OCR_CONCURRENCY=1`
- `KG_IMAGE_OCR_LANGUAGES=ch,en`

`KG_DOCUMENT_OCR_MODE` 继续只控制 Docling 的 PDF OCR；图片知识包使用新的 `KG_IMAGE_OCR_*` 配置，两者不能混用。

模型权重不提交 Git，继续由 `data/model/*` 忽略规则管理。仓库保存一份期望模型清单，部署脚本将权重复制/下载到目标目录后生成 `manifest.json`，其中记录模型家族、变体、来源、许可证、文件 SHA-256、运行时版本和语言。生产启动只校验本地文件，不在第一次业务请求时自动下载权重。

## 9. Markdown 解析与来源映射

使用支持 token map 的 Markdown 解析器生成块结构。不能仅靠正则处理 Markdown。

每个解析块至少保存：

- `block_id`
- `document_version_id`
- `type`：heading、paragraph、list、code、table、image 等
- `ordinal`
- `normalized_text`
- `source_start_char` / `source_end_char`
- `source_start_line` / `source_end_line`
- `heading_path`
- `payload`：表格行列、代码语言、图片资产引用等类型数据

切片器以块为边界优先，保存 chunk 与一个或多个块的映射。表格尽量整表切片；超过上限时按表头加数据行拆分，每个子切片重复表头并保存原始行号。

来源链最终为：

```text
正式知识记录版本
→ assertion
→ chunk version
→ chunk source span
→ parsed block
→ source document version
→ source object / source asset
```

本地图片在 OCR 成功且通过质量门后生成独立 OCR 文字块。OCR 文字按照阅读顺序组成一个或多个图片切片，使用现有本体约束提取或开放发现提取实体、关系和属性。每个 OCR 切片保存它引用的文字块 ID；最终断言通过这些 ID 回溯到图片坐标框。

OCR 不等于完整视觉理解。若图片是流程图、拓扑图或示意图，首期只能提取图中可识别文字及由这些文字明确表达的事实，不能仅凭线条或图标推断关系。页面将来源标记为“图片 OCR”，并显示平均置信度和原图区域。

本地图片默认使用 `required` 策略：OCR 引擎不可用、处理失败或全部文字低于质量门时，文档进入 `needs_attention`，不能静默跳过图片继续正式写入。用户可以重试 OCR，或明确选择“该图片不包含知识文字，忽略并继续”；忽略动作保存操作者、时间和原因。

## 10. 数据模型

使用正式关系表承载可分页、可领取、可约束的数据，避免把 1000 个条目和大量切片检查点放进单个 JSON artifact。

建议新增：

### `ingest_batches`

- `id`, `project_id`, `status`
- `requested_concurrency`, `effective_concurrency`
- `settings_json`, `settings_fingerprint`
- 各状态汇总计数
- `paused_reason`, `created_by`
- `created_at`, `updated_at`, `started_at`, `completed_at`

### `ingest_batch_items`

- `id`, `batch_id`, `project_id`
- `source_document_id`, `source_version_id`
- `original_name`, `file_sha256`
- `status`, `stage`, `progress`
- `attempt`, `auto_retry_count`, `next_retry_at`
- `error_code`, `public_error`, `failed_chunk_ordinal`
- `lease_owner`, `lease_token`, `lease_until`
- `created_at`, `updated_at`, `completed_at`

### `ingest_chunk_checkpoints`

- `id`, `batch_item_id`, `attempt`
- `chunk_id`, `chunk_ordinal`, `chunk_sha256`
- `configuration_fingerprint`
- `status`, `output_json`, `diagnostics_json`
- `error_code`, `public_error`
- `started_at`, `completed_at`

唯一键确保同一条目、切片、配置指纹只有一个有效完成检查点。

### `source_objects`

- `id`, `sha256`, `size_bytes`, `mime_type`, `storage_key`
- `created_at`

内容哈希唯一，相同字节可复用对象；业务引用仍按文档版本区分。

### `source_document_versions`

- `id`, `project_id`, `document_id`, `version`
- `source_object_id`, `original_name`, `format`
- `parser_name`, `parser_version`, `parse_status`
- `created_at`

### `source_assets`

- `id`, `document_version_id`, `source_object_id`
- `original_path`, `resolved_path`, `alt_text`
- `width`, `height`, `status`
- `source_start_line`, `source_end_line`

### `source_asset_ocr_runs`

- `id`, `asset_id`, `status`, `attempt`
- `engine`, `engine_version`, `language_config`
- `preprocess_fingerprint`, `configuration_fingerprint`
- `average_confidence`, `text_sha256`
- `error_code`, `public_error`
- `started_at`, `completed_at`

同一资产哈希和 OCR 配置指纹的成功运行可复用；失败运行保留为新 attempt。

### `source_asset_ocr_blocks`

- `id`, `ocr_run_id`, `ordinal`, `text`, `normalized_text`
- `confidence`
- `x`, `y`, `width`, `height`，均保存为相对于图片宽高的 0–1 坐标
- `page_or_frame`，首期静态图片固定为 1

OCR 块通过派生 chunk/source span 参与现有 assertion 证据链。

### `parsed_blocks`

- 本节第 9 节所列字段。

### `chunk_source_spans`

- `chunk_version_id`, `block_id`, `ordinal`
- `chunk_start_char`, `chunk_end_char`
- `block_start_char`, `block_end_char`
- 可选表格行列范围。

所有表都带项目和版本约束；删除项目时级联清理数据库引用。对象字节垃圾回收只删除已无任何引用的内容哈希对象。

## 11. HTTP 接口

### 服务能力

`GET /api/ingest/capabilities`

返回支持格式、最大文件数、文件/批次体积、并发选项、文档/LLM/OCR 服务端上限、Markdown 知识包能力、OCR 引擎可用状态、支持的图片格式/语言/最大像素和功能开关。OCR 引擎不可用时，前端在提交前直接阻止包含本地图片的知识包。

### 批次

- `POST /api/projects/{project_id}/ingest-batches`
- `GET /api/projects/{project_id}/ingest-batches/{batch_id}`
- `GET /api/projects/{project_id}/ingest-batches/{batch_id}/items?status=&cursor=&limit=`
- `POST /api/projects/{project_id}/ingest-batches/{batch_id}/files`
- `POST /api/projects/{project_id}/ingest-batches/{batch_id}/start`
- `POST /api/projects/{project_id}/ingest-batches/{batch_id}/pause`
- `POST /api/projects/{project_id}/ingest-batches/{batch_id}/resume`
- `POST /api/projects/{project_id}/ingest-batches/{batch_id}/cancel`
- `POST /api/projects/{project_id}/ingest-batches/{batch_id}/retry-failed`

### 文件操作

`POST /api/projects/{project_id}/ingest-batches/{batch_id}/items/{item_id}/retry`

请求：

```json
{"mode":"resume_checkpoint"}
```

`mode` 可为 `resume_checkpoint`、`retry_failed_chunks`、`restart_document`。

### 原文与附件

- `GET /api/projects/{project_id}/source-documents/{version_id}`
- `GET /api/projects/{project_id}/source-documents/{version_id}/rendered`
- `GET /api/projects/{project_id}/source-documents/{version_id}/source`
- `GET /api/projects/{project_id}/source-documents/{version_id}/assets/{asset_id}`
- `GET /api/projects/{project_id}/source-documents/{version_id}/assets/{asset_id}/ocr`
- `POST /api/projects/{project_id}/source-documents/{version_id}/assets/{asset_id}/ocr/retry`
- `POST /api/projects/{project_id}/source-documents/{version_id}/assets/{asset_id}/ocr/ignore`
- `GET /api/projects/{project_id}/source-documents/{version_id}/locate?chunk_version_id=&assertion_id=`

旧 `/documents/upload/jobs` 和通用 `/jobs/{id}/{action}` 保持兼容，现有单文件调用方无需立即迁移。

## 12. UI 设计

### 12.1 知识写入页面

高级解析设置增加：

```text
解析并发  [自动推荐 ▼]
系统会根据模型限额和当前负载决定实际并发。
```

提交后显示一个批次卡：

```text
规则知识批次 · 1000 个文件
完成 426  处理中 4  排队 548  待审核 17  失败 5
申请并发 8 · 实际并发 4

[暂停] [重试全部失败] [下载失败明细]
```

展开后分页显示文件行。失败行展示失败阶段、错误原因、自动重试次数和人工按钮。

### 12.2 任务页

通用任务页继续存在。摄取批次显示一张批次任务卡，点击进入批次详情，不把子文件全部平铺。

### 12.3 原文阅读器

三个页签：

- 阅读视图：安全渲染 Markdown、表格和已固化图片；图片显示 OCR 状态。
- 解析视图：显示块、切片边界、图片 OCR 文字、置信度、切片 ID 和交给模型的文本。
- 原始视图：只读源码并允许下载固定版本原文件。

阅读视图禁止执行原始 HTML 和脚本。外部图片显示为链接状态，不直接作为可信附件。

### 12.4 图谱来源

复用现有节点/关系详情栏和 `evidence-inspector`：

1. 点击实体、关系或属性。
2. 来源证据先展示解析后的 quote 和版本。
3. 点击“在原文中打开”。
4. 原文阅读器定位标题、段落、表格行或图片位置并高亮。
5. 多来源断言展示全部来源分支，不压缩成单一来源。

来自图片 OCR 的证据打开后，阅读器显示原图，并在文字对应区域覆盖半透明坐标框；右侧列出 OCR 原文、规范化文字、引擎、版本和置信度。

这只是详情中的来源页签和快捷动作，不增加必须按顺序完成的工作流状态。

## 13. 安全与一致性规则

- ZIP 解压拒绝绝对路径、`..` 路径逃逸、符号链接和超过配置值的展开体积/文件数。
- Markdown 渲染使用白名单清洗，禁止脚本、事件属性和危险 URL scheme。
- 附件读取必须同时校验项目、文档版本和资产归属。
- 原始文件和检查点不可由前端路径直接访问，必须通过授权接口。
- 批次重试使用幂等键；重复点击不能产生重复条目或正式知识。
- 正式知识提交前再次核对本体版本和设置指纹。

## 14. 发布顺序

1. 批次表、能力接口、批次页面和并发控制。
2. 错误分类、自动重试、人工重试和批次熔断。
3. 切片检查点和真正断点续跑。
4. 来源对象仓库、Markdown 知识包和结构化解析。
5. 本地图片 OCR、OCR 检查点和 OCR 文字信息提取。
6. 原文阅读器、source map、图片区域高亮和图谱来源跳转。
7. 1000 文件容量测试、5000 规则分批演练和操作手册更新。

每一步都必须可以单独上线和回滚。旧上传接口在全部迁移完成前保留。

## 15. 关键产品决定

- 默认使用“自动推荐”并发。
- 用户可手动选 1/2/4/8，但服务端拥有最终上限。
- 自动重试过程必须在前端可见。
- 手动重试长期保留，默认动作是“从断点继续”。
- 5000 份规则按多个最多 1000 条的批次处理，不建议单个无限大批次。
- Markdown + 相对附件 ZIP 是规则脚本的推荐格式；Word 继续支持，但不作为批量规则首选。
- 网络图片首期只登记链接，不自动抓取。
- ZIP 内本地图片必须经过 OCR，OCR 文字再进入切片和信息提取。
- OCR 只证明识别到了图片文字；流程线、图标和空间结构不在首期理解范围。
- OCR 失败默认阻止该文档正式提交，用户可重试或明确标记图片无知识文字后继续。
- 图谱显示继续采用检索/邻域加载，来源文档数量与图谱一次渲染节点数量解耦。
