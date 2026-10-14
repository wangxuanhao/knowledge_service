# 批量知识写入、断点续跑与 Markdown 原文溯源 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让系统能够以可控并发批量处理最多 1000 份文档，在 LLM、OCR 或服务异常后按检查点恢复，对 ZIP 内本地图片先做 OCR 再提取知识，并把 Markdown 表格、图片区域和最终图谱知识连接到固定版本原文。

**Architecture:** 新增专用批次控制面和单文档执行单元，PostgreSQL 保存可领取的批次条目与切片检查点，内容寻址对象仓库永久保存原文件和附件。现有三种提取模式、正式知识写入、审核和 assertion 证据模型保持权威；新增 source map 把现有 chunk 证据继续连接到 Markdown 块和原始来源。

**Tech Stack:** Python 3.12、FastAPI、Pydantic 2、PostgreSQL/psycopg、原生 JavaScript/CSS、Markdown token parser、HTML sanitizer、带坐标与置信度输出的本地 OCR adapter、pytest、Node test runner、Playwright。

**Design spec:** `docs/superpowers/specs/2026-10-14-batch-ingest-resume-markdown-provenance-design.md`

---

## 0. 执行规则

- 执行前先运行 `git status --short`。当前工作区已有其他文档和前端改动，不得覆盖、回滚或顺手格式化这些文件。
- Python 命令统一使用 `D:/anaconda/envs/llm_model/python.exe`。
- 代码发现先使用 codebase-memory-mcp；只有字符串、配置和非代码文档搜索再使用 `rg`。
- 每个任务先写失败测试，再做最小实现，再运行局部测试，最后提交独立 commit。
- 涉及同一文件的任务必须串行分配。尤其是 `service.py`、`workbench.js`、`index.html`、`core.py` 和迁移目录，禁止两个智能体同时修改。
- 不在本计划中实现 OWL 或完整视觉语义抽取；ZIP 本地图片 OCR 和基于 OCR 文字的信息提取属于本次必做范围。
- 不把“知识写入”改造成强制向导；新增 UI 是批次监控、错误处理和来源阅读器。

## 1. 文件结构

### 新增后端模块

- `knowledge_service/services/ingest_batches.py`：批次应用服务，创建、启动、暂停、恢复、重试和聚合状态。
- `knowledge_service/services/ingest_batch_runner.py`：领取批次条目并运行单文档处理。
- `knowledge_service/services/ingest_retry.py`：错误分类、退避策略、熔断判断和公开错误。
- `knowledge_service/services/ingest_checkpoints.py`：检查点指纹、复用判定和切片输出读写。
- `knowledge_service/services/source_documents.py`：原始来源、解析版本、附件和定位应用服务。
- `knowledge_service/integrations/source_object_store.py`：对象仓库协议和本地内容寻址实现。
- `knowledge_service/integrations/markdown_package.py`：ZIP 知识包检查、Markdown 发现和附件解析。
- `knowledge_service/integrations/markdown_parser.py`：Markdown token/AST、表格、图片引用和源码位置映射。
- `knowledge_service/integrations/image_ocr.py`：OCR adapter、引擎能力检查、图片预处理和标准化坐标输出。
- `knowledge_service/services/image_ocr.py`：OCR 检查点、质量门、人工忽略和 OCR 文字切片。
- `knowledge_service/resources/ocr/pp-ocr-v6-small.json`：受版本控制的模型文件名、SHA-256、来源、许可证和兼容运行时清单。
- `scripts/install_ocr_models.py`：把已确认权重安装到 `data/model/ocr/pp-ocr-v6-small`，不在业务请求中下载。
- `scripts/verify_ocr_models.py`：启动前校验模型目录、哈希、运行时和一次最小推理。
- `knowledge_service/repository/ingest_batch_store.py`：批次、条目、领取、租约和计数 SQL。
- `knowledge_service/repository/source_store.py`：来源对象元数据、文档版本、资产、解析块和 span SQL。
- `knowledge_service/repository/checkpoint_store.py`：切片检查点 SQL。

### 新增前端模块

- `knowledge_service/web/ingest-batches.js`：批次创建、分页监控、并发展示和批量操作。
- `knowledge_service/web/ingest-batches.css`：批次页面样式。
- `knowledge_service/web/source-reader.js`：阅读/解析/原始三个页签和定位。
- `knowledge_service/web/source-reader.css`：原文阅读器样式。

### 主要修改文件

- `knowledge_service/models.py`：批次设置、能力、重试请求和来源响应模型。
- `knowledge_service/parity.py`：路由、依赖装配、执行器生命周期和静态资源。
- `knowledge_service/services/service.py`：把长摄取方法拆出可检查点的文档阶段接口；保持现有 `ingest()` 兼容。
- `knowledge_service/services/job_handlers.py`：旧上传任务接入统一错误分类；保持旧接口行为。
- `knowledge_service/services/chunking.py`：接收结构块并输出来源 span。
- `knowledge_service/services/evidence.py`：把 chunk 证据扩展为原始来源定位信息。
- `knowledge_service/repository/core.py`：装配三个新 store 的薄代理，避免继续堆放 SQL。
- `knowledge_service/web/index.html`：并发选择器、批次容器、阅读器挂载点和资源引用。
- `knowledge_service/web/workbench.js`：旧上传入口切换到批次 API；通用任务错误卡增强。
- `knowledge_service/web/ingest-mode.js`：上传能力从服务端加载，移除硬编码 20/100 MB。
- `knowledge_service/web/evidence-inspector.js`：增加“在原文中打开”。
- `knowledge_service/web/sources-view.js`：文档列表打开新阅读器。
- `knowledge_service/web/workbench.css` 或已有对应 CSS：只做必要布局接线，具体样式放进新文件。
- `pyproject.toml`：加入 Markdown token parser、HTML sanitizer，以及独立 `ocr` optional dependency（RapidOCR/ONNX Runtime）的固定兼容范围。
- `docs/2026-10-01-本体工作台操作手册.md`、`scripts/build_knowledge_service_manual.py`：更新操作步骤并重建 HTML 手册。

### 新增测试

- `tests/service/test_ingest_batch_store.py`
- `tests/service/test_ingest_batch_api.py`
- `tests/service/test_ingest_batch_runner.py`
- `tests/service/test_ingest_retry.py`
- `tests/service/test_ingest_checkpoints.py`
- `tests/service/test_source_object_store.py`
- `tests/service/test_markdown_package.py`
- `tests/service/test_markdown_parser.py`
- `tests/service/test_image_ocr.py`
- `tests/service/test_source_document_api.py`
- `tests/service/ingest_batches_ui.test.cjs`
- `tests/service/source_reader_ui.test.cjs`
- `tests/service/test_ingest_batch_browser.py`

## 2. 任务依赖图

```text
Task 1 能力契约
  ├─ Task 2 数据库与 store
  │    ├─ Task 3 批次 API
  │    │    ├─ Task 4 领取器与并发
  │    │    └─ Task 5 批次 UI
  │    └─ Task 6 检查点
  │         └─ Task 7 摄取阶段拆分与断点恢复
  └─ Task 8 来源对象仓库
       └─ Task 9 Markdown 知识包与解析
            └─ Task 10 本地图片 OCR 与文字提取
                 └─ Task 11 来源映射与原文 API
                      └─ Task 12 原文阅读器与图谱跳转

Task 13 自动重试与熔断依赖 Task 3、4、6、10
Task 14 容量与故障测试依赖 Task 5、7、10、12、13
Task 15 操作手册依赖全部 UI 定稿
```

## 3. 分阶段交付

### Phase A：批次与并发

完成 Task 1–5 后，用户已经可以创建 1000 条批次、选择并发、分页看进度和整文档手动重试。此阶段仍允许整份文档重跑，但页面必须准确说明。

### Phase B：切片级断点续跑

完成 Task 6、7、12 后，LLM 临时异常可自动退避，人工可以从检查点继续，服务重启不会丢失已完成切片。

### Phase C：Markdown 与原文溯源

完成 Task 8–12 后，Markdown 知识包、表格、相对图片 OCR、OCR 文字信息提取、原文阅读器和图谱来源跳转完整可用。

### Phase D：容量验收与文档

完成 Task 14、15 后，用真实或等价规则样本演练多个批次，并发布可操作手册。

---

### Task 1: 固化服务能力与批次 API 契约

**Files:**
- Modify: `knowledge_service/models.py`
- Modify: `knowledge_service/parity.py`
- Test: `tests/service/test_ingest_batch_api.py`
- Test: `tests/service/test_frontend_static_contract.py`

- [x] **Step 1: 为能力接口写失败测试**

测试 `GET /api/ingest/capabilities` 返回：支持格式、`max_batch_items`、文件和批次体积、`concurrency_choices=["auto",1,2,4,8]`、文档/LLM/OCR 服务端并发上限、OCR 引擎可用状态、支持图片格式/语言/最大像素、知识包和来源阅读器功能开关。

- [x] **Step 2: 运行测试确认失败**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_ingest_batch_api.py -q`

Expected: 404 或响应模型不存在。

- [x] **Step 3: 增加 Pydantic 契约**

至少定义：

```python
class IngestCapabilities(BaseModel):
    max_batch_items: int
    max_file_bytes: int
    max_batch_bytes: int
    concurrency_choices: list[str | int]
    server_concurrency_limit: int
    llm_concurrency_limit: int
    ocr_concurrency_limit: int
    ocr_available: bool
    ocr_languages: list[str]
    ocr_image_extensions: list[str]
    ocr_max_pixels: int
    supported_extensions: list[str]
    markdown_package: bool
    source_reader: bool

class IngestBatchSettings(BaseModel):
    requested_concurrency: Literal['auto', 1, 2, 4, 8] = 'auto'
    extraction_mode: Literal['ontology', 'discovery', 'documents']
    # 复用现有 IngestSettings 的解析/抽取字段，不复制语义。
```

- [x] **Step 4: 从环境配置加载服务端上限**

使用业务专用变量名，例如 `KNOWLEDGE_INGEST_MAX_ITEMS`、`KNOWLEDGE_INGEST_DOCUMENT_CONCURRENCY`、`KNOWLEDGE_LLM_CONCURRENCY`。默认值需保守，测试通过依赖注入覆盖，禁止在前端硬编码。

- [x] **Step 5: 实现能力路由并跑测试**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_ingest_batch_api.py tests/service/test_frontend_static_contract.py -q`

Expected: PASS。

- [x] **Step 6: Commit**

```bash
git add knowledge_service/models.py knowledge_service/parity.py tests/service/test_ingest_batch_api.py tests/service/test_frontend_static_contract.py
git commit -m "feat: expose ingest batch capabilities"
```

### Task 2: 新增批次、条目和检查点数据库结构

**Files:**
- Create: `knowledge_service/repository/migrations/<next>_ingest_batches_sources.sql`
- Create: `knowledge_service/repository/ingest_batch_store.py`
- Create: `knowledge_service/repository/checkpoint_store.py`
- Modify: `knowledge_service/repository/core.py`
- Test: `tests/service/test_ingest_batch_store.py`
- Test: `tests/service/test_ingest_checkpoints.py`

- [x] **Step 1: 执行时确认下一个迁移编号**

不能照抄文档中的编号。读取迁移目录和数据库迁移器，选择实际下一个编号，并确保 SQL 被 `pyproject.toml` package-data 收录。

- [x] **Step 2: 为批次创建、分页、状态聚合和租约领取写失败测试**

覆盖：项目隔离、1000 条分页、同一条目不可重复领取、租约过期可重新领取、暂停批次不可领取、批次计数从条目聚合。

- [x] **Step 3: 为检查点唯一性和幂等写失败测试**

同一 `batch_item_id + chunk_id + configuration_fingerprint` 重复完成应返回同一结果；不同输出冲突必须拒绝。

- [x] **Step 4: 创建表和约束**

按设计规格第 10 节建立表。所有 JSON 使用数据库 JSON 校验；所有项目关联建立外键；领取查询使用事务锁和 `SKIP LOCKED`。

- [x] **Step 5: 实现小而明确的 store 接口**

`IngestBatchStore` 建议只暴露：`create_batch`、`add_item`、`start`、`control`、`claim_next`、`renew_lease`、`transition_item`、`list_items`、`summary`。

`CheckpointStore` 建议只暴露：`get_compatible`、`begin`、`complete`、`fail`、`list_for_item`。

- [x] **Step 6: 运行 store 测试**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_ingest_batch_store.py tests/service/test_ingest_checkpoints.py -q`

Expected: PASS。

- [x] **Step 7: Commit**

```bash
git add knowledge_service/repository knowledge_service/repository/migrations tests/service/test_ingest_batch_store.py tests/service/test_ingest_checkpoints.py
git commit -m "feat: persist ingest batches and checkpoints"
```

### Task 3: 实现批次创建、上传、启动和控制 API

**Files:**
- Create: `knowledge_service/services/ingest_batches.py`
- Modify: `knowledge_service/parity.py`
- Modify: `knowledge_service/services/document_uploads.py`
- Test: `tests/service/test_ingest_batch_api.py`

- [x] **Step 1: 写完整 API 失败测试**

覆盖创建批次、逐文件上传、幂等键、达到文件数/体积上限、启动、暂停、恢复、取消未开始条目、分页筛选和重试请求。

- [x] **Step 2: 实现 `BatchIngestService`**

服务负责校验项目权限、能力上限、状态转换和批次汇总。路由不得直接拼 SQL，也不得自行修改计数。

- [x] **Step 3: 把上传分成批次会话**

推荐顺序：创建批次 → 浏览器以 3 并发逐文件调用 files 接口 → 全部上传完成后调用 start。每个文件接收 `Idempotency-Key`，重复上传返回原条目。

- [x] **Step 4: 保持旧接口兼容**

原 `/projects/{p}/documents/upload/jobs` 继续工作。新页面切换完成前，不删除旧 handler 和旧测试。

- [x] **Step 5: 运行 API 和旧上传回归测试**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_ingest_batch_api.py tests/service/test_document_upload_api.py -q`

Expected: PASS。

- [x] **Step 6: Commit**

```bash
git add knowledge_service/services/ingest_batches.py knowledge_service/services/document_uploads.py knowledge_service/parity.py tests/service/test_ingest_batch_api.py tests/service/test_document_upload_api.py
git commit -m "feat: add ingest batch API"
```

### Task 4: 实现批次领取器和服务端并发控制

**Files:**
- Create: `knowledge_service/services/ingest_batch_runner.py`
- Modify: `knowledge_service/parity.py`
- Test: `tests/service/test_ingest_batch_runner.py`

- [x] **Step 1: 写并发失败测试**

覆盖：申请 8、服务端上限 4 时最多 4 个运行条目；两个批次共享全局 LLM 上限；暂停后不再领取；关闭服务后线程可退出；租约丢失后不能提交结果。

- [x] **Step 2: 实现批次条目 worker pool**

全局 worker 数来自配置。每个 worker 通过 store 原子领取一个允许执行的条目。批次 `effective_concurrency` 在启动时记录，并在页面展示。

- [x] **Step 3: 增加 LLM 信号量**

所有 Semantica/LLM 调用通过共享限流器进入。不要仅增加 `ThreadPoolExecutor(max_workers=8)` 而绕过模型并发上限。

- [x] **Step 4: 装配生命周期**

应用启动时创建 runner，关闭时停止领取并等待短时间退出。重启后过期租约条目进入 `interrupted` 或重新领取。

- [x] **Step 5: 运行测试**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_ingest_batch_runner.py tests/service/test_durable_jobs.py -q`

Expected: PASS。

- [x] **Step 6: Commit**

```bash
git add knowledge_service/services/ingest_batch_runner.py knowledge_service/parity.py tests/service/test_ingest_batch_runner.py
git commit -m "feat: run ingest batches with bounded concurrency"
```

### Task 5: 实现批次上传与监控 UI

**Files:**
- Create: `knowledge_service/web/ingest-batches.js`
- Create: `knowledge_service/web/ingest-batches.css`
- Modify: `knowledge_service/web/index.html`
- Modify: `knowledge_service/web/ingest-mode.js`
- Modify: `knowledge_service/web/workbench.js`
- Test: `tests/service/ingest_batches_ui.test.cjs`
- Test: `tests/service/test_frontend_static_contract.py`

- [x] **Step 1: 写前端契约失败测试**

测试并发选项、服务端能力加载、3 路上传、批次汇总、分页、状态筛选、失败行错误信息和三种手动重试模式。

- [x] **Step 2: 移除前端固定 20/100 MB 常量作为权威值**

页面从 capabilities 渲染限制；能力接口失败时使用保守回退值并显示“未取得服务能力”，不能默默允许超限。

- [x] **Step 3: 实现上传队列**

浏览器最多同时上传 3 个文件。每个条目显示上传进度和失败原因；上传失败可只重传文件，不影响已成功条目。

- [x] **Step 4: 实现批次卡和分页文件列表**

批次卡展示总数、各状态计数、申请/实际并发和暂停原因。1000 行必须分页或窗口化。

- [x] **Step 5: 增加手动重试按钮和解释文本**

Phase A 如果尚未完成切片检查点，必须将按钮写为“整份重试”；Task 7 完成后再启用“从断点继续”和“只重试失败切片”。

- [x] **Step 6: 运行前端测试**

Run: `node --test tests/service/ingest_batches_ui.test.cjs`

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_frontend_static_contract.py -q`

Expected: PASS。

- [x] **Step 7: Commit**

```bash
git add knowledge_service/web/ingest-batches.js knowledge_service/web/ingest-batches.css knowledge_service/web/index.html knowledge_service/web/ingest-mode.js knowledge_service/web/workbench.js tests/service/ingest_batches_ui.test.cjs tests/service/test_frontend_static_contract.py
git commit -m "feat: add ingest batch workspace"
```

### Task 6: 实现配置指纹和切片检查点服务

**Files:**
- Create: `knowledge_service/services/ingest_checkpoints.py`
- Modify: `knowledge_service/repository/checkpoint_store.py`
- Test: `tests/service/test_ingest_checkpoints.py`

- [x] **Step 1: 写指纹稳定性失败测试**

相同文件、切片、模式、本体、模型和设置产生相同指纹；任一关键字段变化必须产生不同指纹。JSON 键顺序不能影响指纹。

- [x] **Step 2: 写复用判定失败测试**

完成检查点可复用；失败检查点不可当作成功；文本哈希或本体版本变化返回明确不兼容原因。

- [x] **Step 3: 实现规范化指纹**

使用稳定 JSON 序列化和 SHA-256。模型标识、提示词版本和提取器版本必须显式传入，不能从易变对象字符串推断。

- [x] **Step 4: 实现开始/完成/失败收据**

保存结构化实体、关系、属性输出和诊断；不保存密钥或完整供应商响应。

- [x] **Step 5: 运行测试并提交**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_ingest_checkpoints.py -q`

```bash
git add knowledge_service/services/ingest_checkpoints.py knowledge_service/repository/checkpoint_store.py tests/service/test_ingest_checkpoints.py
git commit -m "feat: persist reusable ingest chunk checkpoints"
```

### Task 7: 将现有摄取流程拆成可恢复阶段

**Files:**
- Modify: `knowledge_service/services/service.py`
- Create: `knowledge_service/services/document_ingest_runner.py`
- Modify: `knowledge_service/services/job_handlers.py`
- Modify: `knowledge_service/services/chunking.py`
- Test: `tests/service/test_ingest_batch_runner.py`
- Test: `tests/service/test_document_upload_api.py`
- Test: `tests/service/test_ingest_runs.py`

- [x] **Step 1: 为“第 28/30 个切片失败”写端到端失败测试**

第一次执行让第 28 个切片抛出临时异常；验证 1–27 的检查点完成。恢复后验证只调用 28–30，最终只产生一套正式知识。

- [x] **Step 2: 为服务重启恢复写失败测试**

模拟条目租约过期和新 runner 领取；验证成功检查点被复用。

- [x] **Step 3: 抽出 `DocumentIngestRunner`**

建议公开阶段：`prepare_source`、`parse`、`chunk`、`extract_chunks`、`merge`、`validate`、`commit`。每个阶段输入输出明确，`service.ingest()` 作为兼容包装调用 runner。

- [x] **Step 4: 在切片循环前查询检查点**

兼容检查点直接提供结构化输出；失败/未完成切片调用现有 extractor。每个切片完成后立即提交检查点事务。

- [x] **Step 5: 把正式写入留到所有切片成功之后**

融合、校验完成前不能部分创建正式记录。提交失败后重试可以复用抽取检查点，但必须重新执行最终一致性校验。

- [x] **Step 6: 保持三种提取模式语义**

`ontology`、`discovery`、`documents` 的正式入图/候选/仅文档行为必须通过现有回归测试。documents 模式不创建无意义 LLM 检查点。

- [x] **Step 7: 运行关键回归**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_ingest_batch_runner.py tests/service/test_document_upload_api.py tests/service/test_ingest_runs.py tests/service/test_assertion_evidence.py -q`

Expected: PASS。

- [x] **Step 8: Commit**

```bash
git add knowledge_service/services/service.py knowledge_service/services/document_ingest_runner.py knowledge_service/services/job_handlers.py knowledge_service/services/chunking.py tests/service
git commit -m "feat: resume document extraction from chunk checkpoints"
```

### Task 8: 实现内容寻址的来源对象仓库

**Files:**
- Create: `knowledge_service/integrations/source_object_store.py`
- Create: `knowledge_service/repository/source_store.py`
- Modify: `knowledge_service/repository/core.py`
- Modify: `knowledge_service/services/document_ingest_runner.py`
- Test: `tests/service/test_source_object_store.py`

- [x] **Step 1: 写原子保存、去重和读取失败测试**

相同字节只存一份对象；不同文档版本可以引用同一对象；写入中断不能留下可见半文件；存储键不能由用户文件名决定。

- [x] **Step 2: 实现 `SourceObjectStore` 协议**

接口至少包括 `put(stream, expected_sha256)`、`open(storage_key)`、`exists`、`delete_if_unreferenced`。本地实现按 SHA-256 分层目录保存。

- [x] **Step 3: 在解析前固化原始文件**

批次条目进入 parsing 前，先把 staged 文件复制到对象仓库并创建来源文档版本。成功后可安全清理临时文件。

- [x] **Step 4: 测试项目删除和对象回收边界**

删除业务引用不应误删仍被其他版本引用的相同对象。

- [x] **Step 5: 运行测试并提交**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_source_object_store.py tests/service/test_document_upload_api.py -q`

```bash
git add knowledge_service/integrations/source_object_store.py knowledge_service/repository/source_store.py knowledge_service/repository/core.py knowledge_service/services/document_ingest_runner.py tests/service/test_source_object_store.py
git commit -m "feat: preserve immutable source objects"
```

### Task 9: 实现 Markdown 知识包、表格和图片附件解析

**Files:**
- Create: `knowledge_service/integrations/markdown_package.py`
- Create: `knowledge_service/integrations/markdown_parser.py`
- Modify: `knowledge_service/integrations/document_parser.py`
- Modify: `knowledge_service/services/document_uploads.py`
- Modify: `pyproject.toml`
- Test: `tests/service/test_markdown_package.py`
- Test: `tests/service/test_markdown_parser.py`
- Test: `tests/service/test_document_parser.py`

- [x] **Step 1: 选定并固定 Markdown 库**

优先选择能返回 token 行号映射的 `markdown-it-py`，表格使用其受支持插件；HTML 输出使用维护中的 sanitizer。先在 `llm_model` 环境验证版本兼容再修改依赖范围。

- [x] **Step 2: 写 Markdown 块失败测试**

固定样例包含两级标题、段落、列表、代码块、表格、相对图片、网络图片和 Base64 图片。断言块顺序、源码行号、标题路径、表格行列和图片引用。

- [x] **Step 3: 写 ZIP 安全和发现失败测试**

覆盖多 Markdown、可选 manifest、相对附件、缺失附件、`../`、绝对路径、符号链接、展开体积和文件数上限。

- [x] **Step 4: 实现知识包读取**

ZIP 只作为知识包格式，不作为任意压缩包浏览器。没有 manifest 时自动发现 `.md`；每份文档分别创建批次条目并共享已去重资产。

- [x] **Step 5: 实现结构化 Markdown 解析**

保留 Markdown 原始源码，同时输出 normalized text 和 blocks。表格进入切片时保留表头和行号；图片只保存附件引用和 alt，不调用 LLM 看图。

- [x] **Step 6: 保持旧 `.md` 兼容**

单 `.md` 无附件仍可上传。Markdown 声明了相对图片但知识包缺少该资产时，条目进入 `needs_attention`；用户补传正确知识包，或明确标记该引用不参与知识提取后才能继续。

- [x] **Step 7: 运行测试**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_markdown_package.py tests/service/test_markdown_parser.py tests/service/test_document_parser.py -q`

Expected: PASS。

- [x] **Step 8: Commit**

```bash
git add knowledge_service/integrations/markdown_package.py knowledge_service/integrations/markdown_parser.py knowledge_service/integrations/document_parser.py knowledge_service/services/document_uploads.py pyproject.toml tests/service
git commit -m "feat: parse markdown knowledge packages and assets"
```

### Task 10: 对 ZIP 本地图片执行 OCR 并提取知识

**Files:**
- Create: `knowledge_service/integrations/image_ocr.py`
- Create: `knowledge_service/services/image_ocr.py`
- Modify: `knowledge_service/repository/source_store.py`
- Modify: `knowledge_service/services/document_ingest_runner.py`
- Modify: `knowledge_service/services/ingest_checkpoints.py`
- Modify: `knowledge_service/models.py`
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `README.md`
- Create: `knowledge_service/resources/ocr/pp-ocr-v6-small.json`
- Create: `scripts/install_ocr_models.py`
- Create: `scripts/verify_ocr_models.py`
- Test: `tests/service/test_image_ocr.py`
- Test: `tests/service/test_ingest_batch_runner.py`
- Test: `tests/service/test_assertion_evidence.py`

- [x] **Step 1: 固定已验证的 PP-OCR small 运行方案**

采用 PP-OCRv6 small 检测/识别权重、移动方向分类权重和 RapidOCR/ONNX Runtime。当前真机已验证 RapidOCR 3.9.2 + ONNX Runtime 1.20.1 可运行；官方 PaddleOCR 3.7.0 导入会因 PaddleX 间接加载当前损坏的 PyTorch DLL 而失败，首期不能将它作为默认 adapter。禁止在运行时静默切换到结果语义不同的引擎。

补充中文、英文、中英混排、旋转文字、小字号表格截图和无文字装饰图基准。记录引擎名、版本、模型文件来源、支持语言、首次模型加载时间、单图耗时、内存占用和限制。实现层只依赖统一 adapter，不把 RapidOCR 对象传进业务服务。

- [x] **Step 2: 分离向量模型和 OCR 模型目录**

按以下结构准备：

```text
data/model/embedding/bge-m3/
data/model/ocr/pp-ocr-v6-small/{det,cls,rec}/
```

先复制并校验现有 `data/model` 根目录中的向量模型到新目录，再把 `.env`/`.env.example` 的 `KG_EMBEDDING_PATH` 改为 `data/model/embedding/bge-m3`，验证向量编码可加载后才清理旧位置。新增 `KG_IMAGE_OCR_BACKEND`、`KG_IMAGE_OCR_MODEL_ROOT`、`KG_IMAGE_OCR_CONFIG`、`KG_IMAGE_OCR_DEVICE`、`KG_IMAGE_OCR_CONCURRENCY` 和 `KG_IMAGE_OCR_LANGUAGES`。当前 CPU 环境默认并发 1。

- [x] **Step 3: 建立权重清单和离线安装/校验脚本**

受版本控制的清单记录三个权重文件的 SHA-256、模型家族/变体、来源、许可证、支持语言和兼容 RapidOCR/ONNX Runtime 版本。安装脚本接收本地源目录或明确下载参数并写入 `data/model/ocr/pp-ocr-v6-small`；校验脚本禁止自动下载，缺文件、哈希不符或最小推理失败时退出非零。

- [x] **Step 4: 为 OCR 标准契约写失败测试**

统一输出：

```python
class OcrBlock(BaseModel):
    text: str
    confidence: float
    x: float
    y: float
    width: float
    height: float

class OcrResult(BaseModel):
    engine: str
    engine_version: str
    languages: list[str]
    width: int
    height: int
    blocks: list[OcrBlock]
    normalized_text: str
    average_confidence: float
```

坐标统一归一化为 0–1，按人类阅读顺序输出。测试拒绝坐标越界、空文本块、NaN 置信度和未知图片格式。

- [x] **Step 5: 增加 OCR 运行和文字块表**

在 Task 2 创建的来源迁移中加入 `source_asset_ocr_runs` 和 `source_asset_ocr_blocks`，或者如果 Task 2 已合并则创建下一个独立迁移。成功收据按 `asset sha256 + engine/version + languages + preprocessing fingerprint` 唯一复用；失败和人工忽略保留独立 attempt/decision。

- [x] **Step 6: 实现确定性图片预处理**

处理 EXIF 方向、透明背景、过大图片缩放和必要的灰度/对比度步骤。每个预处理参数进入 fingerprint。保留原图字节，OCR 使用派生像素，坐标最终映射回原图尺寸。

- [x] **Step 7: 实现 OCR 质量门**

至少判断：是否有非空文字、平均置信度、低置信文字占比和文本长度。阈值从服务端配置读取并写入配置指纹。低于质量门时返回 `ocr_low_confidence`，不得把不可靠文字送入正式知识抽取。

本地图片默认策略为 `required`：OCR 引擎不可用、运行失败或质量不达标时，文档进入 `needs_attention`。用户可以“重试 OCR”或明确执行“该图片无知识文字，忽略并继续”；忽略必须保存操作者、时间和原因。

- [x] **Step 8: 把 OCR 作为图片级检查点接入文档 runner**

Markdown 解析完成后收集所有本地资产，按资产哈希复用成功 OCR。未处理资产通过独立 OCR 信号量执行，避免图片数很多时占满 LLM worker。服务重启后只继续失败或未完成图片。

- [x] **Step 9: 将 OCR 文字转换为来源明确的切片**

每个图片切片携带 `source_kind=image_ocr`、`asset_id`、`ocr_run_id` 和 `ocr_block_ids`。长图片按 OCR 块边界拆分，不能把坐标映射丢掉。图片 alt、所在 Markdown 标题路径和相邻说明文字可以作为上下文，但必须和 OCR 原文分字段保存。

- [x] **Step 10: 使用现有三种提取模式处理 OCR 文字**

- `ontology`：OCR 文本进入现有本体引导抽取、校验和审核分流。
- `discovery`：OCR 文本抽取结果全部进入候选区。
- `documents`：OCR 文本进入 chunk/索引，不创建实体、关系或属性。

LLM 输入明确标注“以下文字来自图片 OCR，可能存在识别错误”，并保留 OCR block 引用。OCR 文字不得和正文拼成无法区分来源的单一字符串。

- [x] **Step 11: 为失败、恢复和幂等写测试**

覆盖 OCR 引擎异常、低置信度、同图去重、不同 OCR 配置不复用、服务重启、人工忽略、OCR 成功后 LLM 第一次失败、重试时 OCR 不重复执行，以及最终 assertion 能回溯到 asset 和 block boxes。

- [x] **Step 12: 运行测试并提交**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_image_ocr.py tests/service/test_ingest_batch_runner.py tests/service/test_assertion_evidence.py -q`

Expected: PASS。

```bash
git add knowledge_service/integrations/image_ocr.py knowledge_service/services/image_ocr.py knowledge_service/repository/source_store.py knowledge_service/services/document_ingest_runner.py knowledge_service/services/ingest_checkpoints.py knowledge_service/models.py knowledge_service/resources/ocr/pp-ocr-v6-small.json scripts/install_ocr_models.py scripts/verify_ocr_models.py pyproject.toml .env.example README.md tests/service/test_image_ocr.py tests/service/test_ingest_batch_runner.py tests/service/test_assertion_evidence.py
git commit -m "feat: extract knowledge from local image OCR"
```

### Task 11: 保存 source map 并提供原文定位 API

**Files:**
- Create: `knowledge_service/services/source_documents.py`
- Modify: `knowledge_service/repository/source_store.py`
- Modify: `knowledge_service/services/chunking.py`
- Modify: `knowledge_service/services/evidence.py`
- Modify: `knowledge_service/parity.py`
- Test: `tests/service/test_source_document_api.py`
- Test: `tests/service/test_assertion_evidence.py`

- [x] **Step 1: 写 chunk 到原文定位失败测试**

覆盖普通段落、跨两个块的切片、拆分表格行、图片 OCR 块、OCR 坐标框和固定文档版本。旧数据没有 source map 时必须显式返回 `source_mapping_unavailable`，不能定位到当前新版文档。

- [x] **Step 2: 保存 parsed blocks 和 chunk spans**

解析成功后在同一文档版本下保存块。切片落库时保存 chunk version 到 block 的 span。不要仅把这些数据塞进自由 metadata。

- [x] **Step 3: 实现来源读取接口**

接口分别返回文档元数据、经过清洗的渲染 HTML、原始源码和附件流。下载响应保留固定版本文件名和内容哈希。

- [x] **Step 4: 实现 locate 接口**

输入 assertion 或 chunk version，返回阅读器需要的 `document_version_id`、tab、block ID、行号、字符区间、表格行列、asset ID、OCR run/block ID 和归一化坐标框。

- [x] **Step 5: 扩展 evidence 响应**

在现有证据字段上追加 `source_location`；不得删除已有 `quote`、start/end、hash 和冻结版本字段。

- [x] **Step 6: 运行测试并提交**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_source_document_api.py tests/service/test_assertion_evidence.py tests/service/test_answer_evidence.py -q`

```bash
git add knowledge_service/services/source_documents.py knowledge_service/repository/source_store.py knowledge_service/services/chunking.py knowledge_service/services/evidence.py knowledge_service/parity.py tests/service
git commit -m "feat: map graph evidence to immutable source documents"
```

### Task 12: 实现原文阅读器和图谱来源跳转

**Files:**
- Create: `knowledge_service/web/source-reader.js`
- Create: `knowledge_service/web/source-reader.css`
- Modify: `knowledge_service/web/index.html`
- Modify: `knowledge_service/web/evidence-inspector.js`
- Modify: `knowledge_service/web/sources-view.js`
- Test: `tests/service/source_reader_ui.test.cjs`
- Test: `tests/service/test_ingest_batch_browser.py`

- [x] **Step 1: 写阅读器前端失败测试**

覆盖三个页签、加载状态、解析失败、缺失附件、外部图片状态、OCR 处理中/成功/低置信/失败状态、OCR 文字、图片坐标框、段落高亮、表格行定位和多次快速打开时旧请求取消。

- [x] **Step 2: 新增抽屉或右侧阅读器**

阅读视图插入服务端已清洗 HTML；解析视图由结构化 blocks 渲染；原始视图使用 `textContent`，不能用 `innerHTML`。

- [x] **Step 3: 支持稳定定位**

优先按 block ID 定位，字符区间用于块内高亮。表格使用行列 metadata 标记单元格。图片 OCR 证据在原图上覆盖半透明坐标框，并在侧栏显示 OCR 原文、规范化文字、引擎版本和置信度。找不到映射时显示明确原因并仍允许查看固定版本原文。

- [x] **Step 4: 接入图谱和台账来源**

在现有 `evidence-inspector` 增加“在原文中打开”，不复制一套来源解析逻辑。文档列表点击同样打开阅读器。

- [x] **Step 5: 真机验收**

使用本地 `http://127.0.0.1:8100/` 和一份包含表格、中文图片、低置信图片及缺失图片的 Markdown 知识包。确认 OCR 状态、识别文字、图片框选、页面视觉、键盘操作、错误文案和跳转位置。

- [x] **Step 6: 运行测试并提交**

Run: `node --test tests/service/source_reader_ui.test.cjs`

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_ingest_batch_browser.py -q`

```bash
git add knowledge_service/web/source-reader.js knowledge_service/web/source-reader.css knowledge_service/web/index.html knowledge_service/web/evidence-inspector.js knowledge_service/web/sources-view.js tests/service
git commit -m "feat: open graph evidence in source reader"
```

### Task 13: 实现自动重试、前端错误与批次熔断

**Files:**
- Create: `knowledge_service/services/ingest_retry.py`
- Modify: `knowledge_service/services/ingest_batch_runner.py`
- Modify: `knowledge_service/services/ingest_batches.py`
- Modify: `knowledge_service/web/ingest-batches.js`
- Modify: `knowledge_service/web/workbench.js`
- Test: `tests/service/test_ingest_retry.py`
- Test: `tests/service/ingest_batches_ui.test.cjs`

- [x] **Step 1: 写错误分类失败测试**

将 LLM/OCR timeout、连接错误、429、502/503 和可恢复 OCR runtime 错误映射为临时错误；认证、模型不存在、OCR 引擎未安装和文档损坏映射为不可自动恢复错误。`ocr_low_confidence` 进入人工处理，不反复自动调用。内部异常文本必须经过公开消息过滤。

- [x] **Step 2: 写退避和熔断失败测试**

用注入时钟验证 5 秒、15 秒、45 秒、2 分钟、5 分钟顺序；验证同批次达到配置比例后暂停领取，用户恢复后继续。

- [x] **Step 3: 实现自动重试收据**

条目记录 `auto_retry_count`、`next_retry_at`、`error_code` 和 `failed_chunk_ordinal`。页面轮询或事件更新时能显示倒计时和当前次数。

- [x] **Step 4: 完善前端错误卡**

卡片必须显示文件、失败阶段、切片、公开原因、自动重试次数、最后时间，并根据错误类型显示可用按钮。

- [x] **Step 5: 为三个手动重试模式接线**

`resume_checkpoint`、`retry_failed_chunks`、`restart_document` 分别调用后端明确模式。重试后增加 attempt，不覆盖旧失败收据。

- [x] **Step 6: 运行测试并提交**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_ingest_retry.py tests/service/test_ingest_batch_runner.py -q`

Run: `node --test tests/service/ingest_batches_ui.test.cjs`

```bash
git add knowledge_service/services/ingest_retry.py knowledge_service/services/ingest_batch_runner.py knowledge_service/services/ingest_batches.py knowledge_service/web/ingest-batches.js knowledge_service/web/workbench.js tests/service
git commit -m "feat: expose recoverable ingest failures and retries"
```

### Task 14: 做 1000 文件容量、重启与幂等验收

**Files:**
- Create: `tests/service/test_ingest_batch_capacity.py`
- Create: `scripts/generate_ingest_fixture.py`
- Create: `scripts/verify_ingest_batch.py`
- Modify: `docs/解析日志定位.md`

- [x] **Step 1: 生成确定性测试知识包**

生成 1000 个小 Markdown、若干共享中文/英文图片、表格、低置信图片、缺失附件、重复文件和可控失败标记。容量测试使用确定性 fake OCR 和 fake LLM。

- [x] **Step 2: 验证列表和数据库容量**

创建 1000 条批次，验证分页、汇总和领取查询耗时不会随页面返回全部对象。

- [x] **Step 3: 验证并发上限**

用可观测假 extractor 记录同时执行数，断言申请 8、服务端 4 时峰值为 4，LLM 全局上限更低时以全局值为准。

- [x] **Step 4: 验证重启与重复请求**

在中间切片停止 runner，再启动新 runner；重复 start、retry 和 complete 请求，断言无重复知识和重复审核项。

- [x] **Step 5: 用真实 LLM 做小批真机验收**

真实模型只用 5–20 份代表性文件，避免容量测试造成无意义成本。检查自动重试文案、人工继续和来源定位。

- [x] **Step 6: 运行完整相关测试**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_ingest_batch_capacity.py tests/service/test_ingest_batch_api.py tests/service/test_ingest_batch_runner.py tests/service/test_ingest_checkpoints.py tests/service/test_markdown_package.py tests/service/test_markdown_parser.py tests/service/test_image_ocr.py tests/service/test_source_document_api.py -q`

Expected: PASS。

- [x] **Step 7: Commit**

```bash
git add tests/service/test_ingest_batch_capacity.py scripts/generate_ingest_fixture.py scripts/verify_ingest_batch.py docs/解析日志定位.md
git commit -m "test: verify large resumable ingest batches"
```

### Task 15: 更新操作手册和交付检查表

**Files:**
- Modify: `docs/2026-10-01-本体工作台操作手册.md`
- Modify: `scripts/build_knowledge_service_manual.py`
- Modify: `docs/知识图谱操作手册.html`
- Create: `docs/2026-10-14-批量知识写入与断点续跑运维说明.md`
- Test: `tests/service/test_docs_contract.py`

- [x] **Step 1: 先写手册契约失败测试**

要求手册明确包含：并发选择、申请/实际并发、自动重试、三种手动重试、暂停/继续、Markdown 知识包、本地图片 OCR、低置信处理、人工忽略、图片区域来源、三个来源页签和图谱打开原文。

- [x] **Step 2: 用操作语言更新手册**

每一节回答：在哪里操作、点击什么、看到什么表示成功、失败时如何处理。不要只解释实现原理。

- [x] **Step 3: 增加格式选择建议**

明确规则脚本推荐 Markdown + 相对附件 ZIP；Word 适合人工办公文档；网络图片未固化；本地图片先 OCR 再抽取；OCR 只能识别文字，不能理解流程线和图标语义。

- [x] **Step 4: 重建 HTML 手册**

Run: `D:/anaconda/envs/llm_model/python.exe scripts/build_knowledge_service_manual.py`

- [x] **Step 5: 运行文档和全量测试**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_docs_contract.py -q`

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest -q`

Run: `node --test tests/service/*.test.cjs`

Expected: PASS。

- [x] **Step 6: 真机最终验收**

按手册完整执行：创建项目 → 选择本体约束/开放发现/仅文档之一 → 上传含本地图片的 Markdown 知识包 → 选择并发 → 查看 OCR 文字 → 制造一次 OCR/模型异常 → 自动重试 → 手动从断点继续 → 查看结果 → 点击来自 OCR 的图谱知识 → 打开原图并高亮文字区域。保存关键页面截图到 `docs/assets/`。

- [x] **Step 7: Commit**

```bash
git add docs scripts/build_knowledge_service_manual.py tests/service/test_docs_contract.py
git commit -m "docs: explain resumable batch knowledge ingestion"
```

## 4. 建议的智能体分工

这些任务不能全部同时开始。按依赖关系分波次分配：

### 波次 1

- 智能体 A：Task 1，能力契约。
- 智能体 B：Task 2，数据库和 store。
- 智能体 C：Task 8，对象仓库接口和测试，可先使用内存 fake，待 Task 2 迁移合并后接数据库。

### 波次 2

- 智能体 A：Task 3、4，批次 API 与 runner，必须串行。
- 智能体 B：Task 6，检查点服务。
- 智能体 C：Task 9，Markdown 包和解析。

### 波次 3

- 智能体 A：Task 5，批次 UI。
- 智能体 B：Task 7，摄取拆分与检查点恢复。
- 智能体 C：Task 10，本地图片 OCR、检查点和 OCR 文字提取。

### 波次 4

- 智能体 A：Task 11，source map 与 API。
- 智能体 B：Task 13，自动重试与熔断。
- 智能体 C：等待 Task 11 完成后执行 Task 12，阅读器和图谱跳转。

### 波次 5

- 智能体 A：Task 14，容量、OCR、重启和幂等验收。
- 智能体 B：Task 15，手册；只能在 UI 文案稳定后开始。

每个智能体开始前都必须读取本规格、当前 `AGENTS.md` 和它要修改文件的最新状态。共享工作区时，协调者要在每波开始前重新分配文件所有权；若使用独立 worktree，则每个波次结束后先合并底层数据模型，再让依赖任务继续。

## 5. 产品验收清单

- [x] 页面可选自动、1、2、4、8 并发。
- [x] 页面显示申请并发和实际并发。
- [x] 1000 个文件组成一个批次，详情分页显示。
- [x] LLM 临时失败有自动重试次数和下一次时间。
- [x] 自动重试耗尽后前端明确报错。
- [x] 失败文件支持从断点继续、只重试失败切片、整份重跑。
- [x] 服务重启后成功切片不重新调用 LLM。
- [x] 重试不会重复写入正式知识。
- [x] Markdown 表格在阅读视图正确展示，在解析视图保留表头和行号。
- [x] ZIP 内相对图片可展示并有固定哈希。
- [x] ZIP 内本地图片自动执行 OCR，成功收据可按资产哈希和配置复用。
- [x] OCR 文字按三种提取模式进入知识抽取或仅文档索引。
- [x] OCR 失败或低置信度在前端明确报错，并支持手动重试。
- [x] 用户明确忽略无知识文字图片时保存操作者、时间和原因。
- [x] 来自 OCR 的知识能定位到原图文字坐标框。
- [x] 缺失图片显示明确状态。
- [x] 网络图片被标记为外部链接，没有被当作已固化证据。
- [x] 图谱实体、关系、属性都能打开解析证据和固定版本原文。
- [x] 多来源事实能查看所有来源分支。
- [x] 大图仍按检索/邻域加载，没有一次渲染全部节点。
- [x] 操作手册能由不熟悉代码的用户独立完成一次完整流程。



## 6. 实施与验收记录（2026-10-14）

Task 1–15 的功能及产品验收已完成。数据库已迁移至 12，实际应用角色拥有新表的数据读写权限；本机服务运行在 http://127.0.0.1:8100/。

- 首轮全量 Python：1684 passed、5 failed、10 skipped、1 xfailed。失败涉及来源模块分层、动态控件静态检查和旧台账列名；修复后集中复测 21 个文件，178 passed，覆盖这 5 项及最新代码。
- 最终前端 Node：68 passed；HTML 手册重建后文档契约再次验证，9 passed。
- 1000 条分页、文档/LLM 并发、30 片中断续跑、租约 fencing、幂等、配置漂移及仓储关闭后迟到响应，由确定性测试验证。
- 真实模型文档批次完成 5/5。真实 OCR ZIP 批次正常识别、空白图拦截、缺失引用拦截及审计处理后完成 3/3。
- 新版服务另完成仅图片退款规则抽取：5 个实体、1 条关系；4 个图谱证据分支返回固定原图 OCR 框。真实 Edge 中选择实体、点击原文链接并高亮成功。
- 固定源码、ZIP 下载、历史切片及逐切片来源定位均通过鉴权 API 核对。截图及真实验收结果保存在 docs/assets/batch-ingest-20261014/ 和 docs/research/2026-10-14-batch-live-acceptance.json。
- 向量模型复制到 data/model/embedding/bge-m3，逐文件 SHA 校验和真实 1024 维编码通过；旧权重保留，原向量 identity 保持兼容。OCR 权重与向量权重分目录存放。

共享工作区原有修改保留。提交采用按归属文件和补丁分组的方式，避免混入既有未提交内容；规划中的单任务提交统一对应来源/OCR、后端、工作台与交付文档三组实现记录。
