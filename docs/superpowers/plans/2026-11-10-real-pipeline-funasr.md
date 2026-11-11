# 真实导入验收与本地视频 ASR 实施计划

> Agent workers use the writing-plans, test-driven-development and verification-before-completion skills. 用户已授权继续实施与实际验收。复用当前分支，不自动提交。

## 目标与设计

在隔离项目真实运行选定平台规则 ZIP，从来源原件、OCR/LLM 候选、本体审核发布，到检索索引和图谱展示，保存可恢复的验收记录。接入本机已验证的 SenseVoiceSmall 与 FSMN-VAD，CPU 单任务子进程运行，完成后退出释放模型内存。

原件与媒体使用现有 MinIO 来源对象。ASR runs 与 segments 使用 PostgreSQL，片段保存 asset_id、start_ms、end_ms 并加入 source spans。Markdown 图片继续 OCR，视频走 ASR。首期支持当前 25MB 限额内的 MP4/WebM，授权 Blob 播放与时间定位，长视频 Range 与 schema v2 独立列为后续。

## Task 1 本地 ASR 与视频主链

涉及 integrations/video_asr.py、integrations/video_asr_worker.py、services/video_asr.py、repository/migrations/0032*、repository/source_store.py、services/source_documents.py、services/source_ingest.py、integrations/markdown_parser.py、integrations/markdown_package.py、services/document_uploads.py、services/document_preview.py、API 能力与 web/source-content.js、web/source-reader.js。以实际文件名为准。

- [x] 写适配器契约、视频媒体来源、ASR 持久化与时间溯源失败测试，先确认失败。
- [x] 限定本地完整模型路径，禁止下载与远程代码。配置指纹覆盖模型文件身份与运行参数。FFmpeg 提取 16kHz 单声道，校验真实媒体与时长上限，超时、无语音、模型缺失均给明确状态。
- [x] 单进程并发门禁，使用当前解释器启动受限 worker，标准输出不承载结果协议，使用临时 JSON，退出清理与超时回收。
- [x] 迁移增加 ASR run、segments、source span 外键。幂等、重试、来源项目检查和同项目缓存参照现有 OCR。
- [x] 支持独立视频与 MD 引用 assets 视频。保持既有 ZIP manifest 与 Markdown 入包契约，预览与正式导入同口径。
- [x] 页面支持鉴权视频播放、分段文字与点击 seek，原文与切片独立，ASR 失败保留原件可读。
- [x] Python 定向测试串行，Node 契约可独立执行。更新静态资源版本。
- [x] 通过 owner migration CLI 迁移正常库，重启应用后真实视频转写与页面播放。

## Task 2 真实导入脚本

涉及 scripts/verify_platform_rule_pipeline.py、tests/scripts/test_verify_platform_rule_pipeline.py、scripts/README.md。

- [x] 实际 API 调用验证隔离空项目 discovery batch multipart 上传与轮询。
- [x] 从候选选择一条具有实体端点的关系，selection-preview、add-selection、草案提交、逐操作审批与发布。
- [x] 验证 accepted assertions、实体与关系、索引构建状态、关键词和语义检索、正式 graph-session page。
- [x] 持久化 state 和 evidence JSON，幂等键按阶段保存，支持恢复，失败返回非零并记录阶段。
- [x] 单元测试覆盖候选选择、状态恢复与错误，先红后绿。实际样本无模拟提取器。

## Task 3 页面验收与复核

- [x] 使用正常鉴权访问隔离项目，实测拖放 ZIP、图片原文、独立切片、本体候选及已发布图谱、检索溯源。
- [x] 实测本地视频播放、ASR 文字、时间定位，并保存截图与服务证据。
- [x] 独立规格复核后代码质量复核，修正重要问题。
- [x] 执行 git diff --check、py_compile、相关 Python 和完整 Node 契约。
- [x] 更新设计文档与运行报告，明确数据范围、模型误识别、边界及交付命令。

## 复核补充约束

- 新增 ASR source span 同步替换旧 CHECK，扩充为 block_id、ocr_block_id、asr_segment_id 至少一个非空。所有 ASR FK 绑定项目与来源版本，新表显式授予 knowledge_app DML 权限。
- 独立视频入口须同步修改 integrations/document_parser.py 的扩展名和空文本门槛，以及 models.py、ingest_batches.py 的实际能力返回和 services/ingest_retry.py 的 ASR 错误分类。
- Worker 默认 timeout 至少 300 秒，计入约 95 秒首次导入时间。服务内线程锁与跨进程文件锁共同限制本机 ASR 内存并发。
- 当前真实 OCR/LLM 批次终止后才迁移、重启服务。运行中不切换代码进程。

真实运行补充。快消样本在 structural 策略下因编号子句产生 60 片，真实抽取推进较慢。新验收脚本创建批次改用 fixed、10000 字符、200 重叠，现有来源映射与特殊表格窗口继续沿用，正在运行的旧批次保留其配置和检查点。


## 最终验收记录

2026-11-11 完成真实规则闭环、平台筛选、独立视频页面上传与转写定位、真实 ZIP 10 图预览，以及多媒体包 OCR/ASR 与来源映射验证。详细数据范围、回归结果、已修问题及交付命令见 docs/2026-11-11-平台规则导入与多媒体验收.md。
