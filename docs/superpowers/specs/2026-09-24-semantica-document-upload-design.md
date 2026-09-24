# Semantica 多格式文档上传与解析设计

日期：2026-09-24

## 背景与现状

当前工作台支持粘贴正文，或在浏览器中读取 UTF-8 `.txt`、`.md` 文件后，把纯文本 JSON 提交给后台任务。后端另有同步 multipart 上传接口，但同样只允许 `.txt`、`.md`，且工作台没有使用该接口。因此，现有知识摄取、切片、Semantica 实体关系抽取和落库链路只能处理调用方已经准备好的文本，不能直接解析 PDF、Word 或 HTML。

项目运行环境已安装 Semantica 0.6.8 和 Docling 2.125.0，但截至 2026-09-24，PyPI 最新正式版分别为 Semantica 0.7.0 和 Docling 2.130.0。Semantica 0.7.0 的 `parse-docling` extra 声明 `docling>=2.107.0`，并提供 `semantica.parse.DoclingParser`。本项目当前只使用 Semantica 的 `semantic_extract` 与 `context` 模块，没有使用其文档解析模块；`pyproject.toml` 也没有声明 Semantica 包本身，安装结果依赖人工准备的 conda 环境。

## 目标

1. 文件上传首批支持 `.txt`、`.md`、`.pdf`、`.docx`、`.html`、`.htm`。
2. PDF、DOCX 和 HTML 直接通过 Semantica `DoclingParser` 转换为结构化 Markdown，不在项目内重复实现 Docling 解析器。
3. 保留 Docling 输出中的页、表格和解析元数据，并把可序列化摘要写入文档来源 metadata。
4. 解析完成后继续复用现有切片、Semantica 知识抽取、本体校验、向量化和原子落库链路。
5. 支持 Docling 本地模型；运行时不要求远程文档解析服务。
6. 为扫描 PDF 的 OCR 保留配置和结果字段。首版允许启用 Semantica/Docling 本地 OCR，但不在项目中绑定某个独立 OCR 厂商或重写 OCR 流水线。
7. 重做上传区域的布局和交互，使格式范围、批量文件、校验、进度及错误清晰可见。

## 非目标

- 首批不开放 PPTX、XLSX、图片、音视频或旧版 `.doc`，即使 Docling 能处理其中部分格式。
- 不保存原始二进制文件；文档记录仍以解析后的正文和来源 metadata 为权威输入。
- 不实现跨服务分布式任务队列、断点续传或服务重启后的任务恢复。
- 不替换现有知识切片器、实体关系抽取器或本体治理流程。
- 不在首版提供 OCR 引擎选择界面；OCR 使用服务端配置。

## 依赖策略

`semantica-runtime` 可选依赖组应显式声明 `semantica[parse-docling]>=0.7,<0.8`，并显式约束 `docling>=2.130,<3`。锁文件在实现时解析到 Semantica 0.7.0 和 Docling 2.130.0。保留项目直接使用的其他依赖，不依靠 Semantica 的传递依赖满足项目自身导入。

升级后必须运行以下兼容验证：

- `semantica.context.context_graph.ContextGraph` 可导入并通过现有快照测试。
- `semantica.semantic_extract` 当前使用的 provider、NER 和 relation API 可导入并通过现有适配器测试。
- `semantica.parse.DoclingParser` 可导入，且最小 DOCX、HTML、文本层 PDF 能返回非空 `full_text`。
- Docling 模型缺失时产生可识别、可面向用户转换的失败，而不是服务启动失败。

本地模型由部署者提前下载。应用不得在上传请求内主动安装包或模型。服务端记录解析器及版本；离线模型目录沿用 Docling 官方缓存或部署配置。由于 Semantica 0.7.0 的 `DoclingParser` 只暴露有限 OCR 配置，项目首版不承诺在 API 内切换具体 OCR 引擎。

## 架构与组件

### Semantica 文档适配器

在 `knowledge_service/integrations` 中新增薄适配器，职责仅限：

- 根据允许的扩展名选择纯文本路径或 Semantica `DoclingParser`。
- 为 Docling 创建并复用延迟初始化的解析器实例。
- 把 Semantica 返回值规范化为项目内部 `ParsedDocument`。
- 把 Semantica/Docling 异常转换成稳定的项目异常，不泄漏本机绝对路径或模型内部堆栈。

`ParsedDocument` 至少包含：

- `text`：用于后续切片和知识抽取的正文；Docling 路径使用 Markdown。
- `format`：规范化源格式。
- `parser` 与 `parser_version`。
- `page_count`。
- `pages`：只保留页码和可序列化的文本摘要/位置信息，不保存页面图片。
- `tables`：表格数量及页码摘要，不在每条文档 metadata 中复制大表格内容。
- `used_ocr` 或 `ocr_mode`。
- `warnings`。

`.txt`、`.md` 继续按 UTF-8 with BOM 容错解码，保证原文不被 Docling 改写。`.pdf`、`.docx`、`.html`、`.htm` 使用 `DoclingParser(export_format="markdown")`。

### 上传暂存与后台任务

新增 multipart 后台任务接口：

`POST /api/projects/{project_id}/documents/upload/jobs`

字段：

- `file`：单个文件。
- `options`：现有 `Ingest` 字段中除 `title`、`text` 外的 JSON 对象；允许可选 `title` 覆盖单文件标题。

请求阶段完成项目存在性、扩展名、单文件大小和 options 校验，并把文件写入受控临时目录。返回 202 及普通 job 收据。后台任务执行：

1. 记录“开始解析文件”阶段。
2. 调用 Semantica 文档适配器。
3. 校验解析正文非空且不超过 `Ingest.text` 的 1,000,000 字符限制。
4. 合并用户 metadata 与系统解析 metadata。
5. 调用现有 `service.ingest`。
6. 无论成功失败，都删除该任务的临时文件。

服务重启时，现有任务机制会把 queued/running 标记为 interrupted；启动清理只处理本应用专用暂存目录中超过保留期的孤儿文件，不扫描或删除其他目录。

保留现有 JSON `/documents/jobs` 给粘贴正文及已有 API 客户端。原同步 `/documents/upload` 改为复用同一个解析适配器，维持兼容，但工作台统一使用新的后台上传接口。

## 文件边界与安全

- 允许扩展名：`.txt`、`.md`、`.pdf`、`.docx`、`.html`、`.htm`。
- 每个文件最大 25 MB；每次前端选择最多 20 个文件；前端批次总量最大 100 MB。
- 后端只信任规范化扩展名并对 ZIP 容器、PDF/HTML 的解析异常做失败处理；客户端 MIME 仅用于提示，不能作为唯一判断。
- 原始文件名只作为 metadata 和默认标题，不参与临时路径拼接。
- HTML 作为上传内容解析，不抓取远程 URL；不执行脚本。
- 解析正文为空、加密 PDF、损坏容器、模型缺失、超出大小或正文长度，都返回明确的文件级错误。
- 上传日志和错误不包含临时绝对路径、原始二进制内容或密钥。

## Metadata 与来源追踪

用户 metadata 保持原样，并增加系统字段：

- `source_file`：原始文件名。
- `source_format`：规范化格式。
- `source_size_bytes`。
- `source_sha256`：用于排查重复上传，不在首版自动拒绝内容重复的文档。
- `document_parser`：`plain-text` 或 `semantica-docling`。
- `document_parser_version` 与 `docling_version`。
- `page_count`、`table_count`。
- `ocr_mode`。
- `parse_warnings`：有界列表。

这些字段由服务端覆盖同名用户字段，避免伪造系统来源信息。完整表格内容已经进入 Markdown 正文时，不额外复制到 metadata。

## 上传页面布局

“添加文档”改为清晰的三段式布局：

1. **选择内容**：顶部使用“上传文件 / 粘贴正文”分段切换。上传模式显示可点击、可拖放的文件区，明确列出支持格式、单文件和批次限制。
2. **文件清单**：每个文件一行显示格式图标、名称、大小、校验状态和移除按钮；提供“清空全部”。重复选择同一浏览器文件对象时去重，但允许内容相同、文件名不同的文件进入服务端。
3. **处理选项**：常用项显示项目模式和主要提交按钮；发布时间、Metadata、切片、实体融合等高级设置放入层级清楚的折叠区。单文件显示可编辑标题，多文件明确使用各自文件名并隐藏公共标题输入。

桌面宽屏中“内容/文件清单”和“处理选项”采用双栏；窄屏回落为单栏。主提交区显示文件数、总大小和将创建的任务数。提交期间禁用被提交文件的移除及重复提交，逐文件显示“上传中、已排队、失败”。所有文件提交完成后再跳到后台任务页；失败文件保留在清单中供修正或重试，成功文件不重复发送。

文件选择和拖放共用同一个状态模型及验证函数，避免两套逻辑。粘贴正文继续使用现有 JSON 后台任务。首份文档切片预览：粘贴正文沿用同步预览；上传文件通过专用 multipart 预览接口解析首份文件并返回前 20 个切片，不写入知识。预览按钮显示忙碌状态并可展示解析错误。

前端不得继续用 `TextDecoder` 读取 PDF、DOCX 或 HTML；所有上传格式的权威解析结果来自后端。

## 错误与状态

文件级错误分为：

- `unsupported_format`
- `file_too_large`
- `invalid_options`
- `parse_failed`
- `encrypted_document`
- `model_unavailable`
- `empty_document`
- `text_too_large`

HTTP 请求期错误返回 4xx；后台解析错误写入现有 job 的 `error_type`、`failed_stage` 和经过脱敏的中文信息。一个文件失败不阻止其他文件提交。解析成功后，后台日志明确区分“文件解析”“知识切片”“实体关系抽取”“向量化与落库”。

## 测试策略

### 后端

- 解析适配器契约测试：TXT、MD、HTML，以及通过替身返回的 PDF/DOCX Semantica 结果。
- 环境集成测试：在安装 `semantica-runtime` extra 时，以最小 DOCX、HTML、文本层 PDF 验证 Semantica 0.7.0 + Docling 2.130.0 的真实解析。
- multipart 上传任务测试：合法格式、非法扩展名、超限、空正文、解析异常、metadata 合并和临时文件清理。
- 回归测试：现有 JSON 文档任务、粘贴正文、切片、抽取和失败收据不改变。
- 安全测试：恶意文件名不能逃逸暂存目录，系统 metadata 不能被用户覆盖，错误不得泄漏临时路径。

### 前端

- 文件选择与拖放走同一验证逻辑。
- 支持格式、大小、数量、总量和重复选择提示。
- 单文件标题与多文件标题行为。
- 成功文件不会重复提交，失败文件可重试。
- 文件级状态、按钮禁用、折叠设置和响应式布局的静态契约。
- TXT/MD、PDF、DOCX、HTML 均使用 multipart 后台任务接口；粘贴正文仍使用 JSON 接口。

## 验收标准

1. 用户可从工作台批量提交 TXT、MD、PDF、DOCX、HTML，且每个文件产生独立后台任务。
2. PDF、DOCX、HTML 的解析日志明确显示 Semantica/Docling，正文、标题和表格结构进入现有知识处理链路。
3. 扫描 PDF 在本地模型就绪时可走 OCR；模型缺失时给出可操作错误，不产生空文档。
4. 来源记录显示源格式、文件大小、哈希、页数、表格数和解析器版本。
5. 现有粘贴正文和 TXT/MD 行为保持兼容。
6. 页面在桌面和窄屏上均能清楚展示文件列表、设置和逐文件状态。
7. 全部自动化测试通过，依赖锁定到已验证的 Semantica 0.7.0 与 Docling 2.130.0。

