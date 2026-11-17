# 文件上传、解析与确认入库

页面操作按四步进行：上传核对、配置资料、解析与检查、确认入库。上传和原件预览不会启动全文解析；只有“开始解析”启动后台任务。解析完成还没有入库，确认入库后在处理批次查看知识处理与检索进度。

同批文件可以属于不同平台和业务。单份设置在对应文档卡片下方展开，可折叠并保留未提交内容；批量设置在批量工具栏下、清单上方展开。逐文件设置范围、标题和时间，也可以勾选多份批量设置；批量只修改勾选字段，支持填空或覆盖。解析方式、时间与切片参数放在展开设置中。调整范围或时间不重复 OCR/转写；调整切片参数复用正文。

ZIP 只用于一个 Markdown/HTML 主文档及其相对资源，例如 `main.md + assets/`，不要求 manifest.json；允许统一外层目录。PDF、Word、独立视频分别上传。当前支持 DOCX，旧二进制 DOC 需要先通过 Word 等工具另存为 DOCX，修改文件后缀无效。Word 原件预览按文档结构（标题层级、加粗斜体、表格、内嵌图片）在线渲染，分页、页眉页脚与自动编号以 Word 打开为准，也可下载原件核对。

## API 顺序

以下所有路径前缀为 `/api/projects/{project_id}/preparations`，使用现有 Bearer 认证。会话属于当前用户和项目，默认保留七天；会话到期后无法继续操作。客户端保存会话 ID 即可跨页面恢复。

1. `POST /`，请求 `{"defaults":{"document_time_mode":"auto"}}`，返回会话 `id`。默认配置只用于新加入的文件。
2. `POST /{id}/files`，multipart 字段 `file`、可选 `options` JSON 字符串，上传重试携带同一个 `Idempotency-Key`。返回文件项 `id`、`metadata_version`、`chunk_version`、校验和重复提示，状态为 `staged`。不启动全文解析。
3. `PATCH /{id}/items/{item_id}`，请求 `{"expected_version":1,"options":{...}}` 修改单文件；`POST /{id}/bulk` 修改所选文件，示例见下文。`PATCH /{id}/settings` 请求 `{"expected_version":0,"settings":{"extraction_mode":"documents"}}` 保存共同入库处理方式，返回递增的 `settings_version`；GET 会话的 `publication_settings` 用于恢复页面选择，不影响正文解析。
4. `POST /{id}/parse`，请求 `{"item_ids":["a","b"],"versions":{"a":2,"b":1},"idempotency_key":"parse-unique-key"}`，返回 202 与会话状态。先校验本次所选项，校验失败不部分启动。
5. `GET /{id}` 轮询各项状态；完成后 `GET /{id}/items/{item_id}/result` 读取正文、切片、警告和结果版本。`GET /{id}/items/{item_id}/original` 返回原件；MD/HTML/资源 ZIP 的 `/preview` 返回自带本地资源的预览 HTML。
6. 核对后 `POST /{id}/publish`，请求所选 `item_ids`、`versions`、`result_versions`、确认时所见的 `settings_version`、入库 `settings` 与独立的 `idempotency_key`，返回 202 和正式入库 `batch_id`，在已有 ingest-batches 接口查询后续进度。`settings={}` 使用会话中已保存的处理方式；显式提交字段会覆盖保存值。其他页面修改了处理方式时，旧确认请求返回 409，需要刷新核对，不能静默换用新模式。

上传、解析、发布的幂等键相互独立。同操作重试使用原键，同键不同内容返回 409。对同一文件项的同一发布快照更换键重试仍返回原批次。平台等设置修改后不能用旧版本确认入库；已发布资料的修订通过资料详情入口明确操作。

文件项的 `existing_documents` 列出项目内内容相同的已有资料。确认页默认跳过重复资料，可以查看已有资料进行复用或修订。确需保留独立资料时，在 publish 请求的 `independent_item_ids` 中明确列出该文件项；同一次选择中第二份及以后的相同内容也需要这一选择。服务端会重新检查，未经明确选择返回 409，不能只依赖页面提示防重。解析缓存仍可复用，不会因为独立入库再次 OCR。

## 混合平台与批量配置

例如 A.pdf 属于美团到店、B.pdf 属于抖音电商：在同会话分别上传，在每份文件的 options 内填写自己的 applicability，再一次解析两份。不需要人为拆成两个上传批次。

文件 options 示例：

```json
{
  "title": "美团到店规则",
  "document_time_mode": "auto",
  "applicability": {
    "platforms": {"mode": "specific", "values": ["美团"]},
    "businesses": {"mode": "specific", "values": ["到店"]},
    "audiences": {"mode": "all", "values": []}
  },
  "chunk_size": 1800,
  "chunk_overlap": 200
}
```

批量只填写尚未确定的业务，不覆盖已有平台或受众：

```json
{
  "item_ids": ["a", "b"],
  "versions": {"a": 2, "b": 1},
  "mode": "fill_missing",
  "fields": {
    "applicability": {
      "businesses": {"mode": "specific", "values": ["到店"]}
    }
  }
}
```

`mode=overwrite` 也只覆盖 fields 中明确提交的维度。范围 `unknown` 表示待确定，`all` 表示不限，`specific` 必须有值。解析选项可通过 `parse_config.ocr_mode` 设置 `auto` 或 `disabled`，空对象表示沿用系统默认；修改后需要明确重新解析。

确认入库请求示例：

```json
{
  "item_ids": ["a", "b"],
  "versions": {"a": 2, "b": 1},
  "result_versions": {"a": "返回的切片版本", "b": "返回的切片版本"},
  "settings_version": 1,
  "settings": {"extraction_mode": "documents"},
  "idempotency_key": "publish-unique-key"
}
```

`documents` 用于仅文档检索；本体抽取需提交有效的处理模式及已发布本体版本，沿用现有入库规则。发布设置决定知识处理方式，每份文件仍保留自己的范围、时间和切片设置。

## 进度与恢复

上传显示字节进度；解析显示排队、运行、完成、复用、失败或取消，以及实际阶段、开始时间与心跳。解析器尚未提供页级计数时 `progress=null`，页面使用不定进度条与耗时，不生成百分比。整体完成数量不是按计算耗时估计的百分比。

`POST /{id}/cancel` 请求 `{"item_ids":["a"]}` 取消选中项；共享底层任务还有其他使用项时继续运行。`DELETE /{id}/items/{item_id}` 移除草稿项。失败或取消后用 `/retry`，请求结构同 `/parse`。修改解析方式导致结果过期时用 `/parse`；修改切片配置可以利用已识别正文生成新切片。

`GET /` 列出本人在当前项目下的有效会话。关闭页面不会取消已明确启动的任务；服务重启后通过租约恢复。无人值守 API 客户端可以自行连续调用上传、解析、查询和发布，并按自己的核对策略处理警告；上传接口不会隐式完成这些步骤。原有 ingest-batches 的 start 语义保持兼容。

> AI生成
