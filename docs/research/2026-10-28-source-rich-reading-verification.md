# 原件存储与资料库图文阅读验证记录

实现日期：2026-10-28；最终部署与验收：2026-10-29。以下结果区分测试夹具、真实解析和实际服务。

## 当前部署结果

- 当前业务原件存储已切换为 S3，使用现有 MinIO 中的私有 `knowledge-sources` bucket。正文、块顺序、来源版本及图片元数据保存在 PostgreSQL，图片字节和原件保存在 MinIO。
- 主库已应用编号迁移 0029、0030，schema 为 30。原有 15 个登记对象和 16 个旧队列项完成迁移；缺失的一个本地对象通过 SHA-256 完全匹配的历史字节恢复。备份、逐对象迁移报告保存在忽略的 `.benchmarks/source-rich-reading/`，其中原始表备份包含资料内容，不应提交或公开。
- 首次移出本地目录后核查 28 个登记对象、25 个队列项；本轮独立合并验收后再次直接核查 MinIO，29 个登记对象全部通过大小和完整 SHA-256，35 个队列项均有对象引用，旧 BYTEA 项为 0。
- `data/sources` 的 32 个文件先整体移到验收备份目录。其中 14 个有登记引用，迁移后均与 MinIO 校验一致；另 18 个没有当前数据库登记。无本地目录的验收通过后，用户明确不再保留旧文件，备份目录已移入 Windows 回收站，原位置已清理。
- 原目录不存在时，旧资料读取、原件下载、后续新上传和图片访问通过，未重新产生本地来源文件。S3 工厂不构造本地存储，也不在失败时回退本地；保留 local 后端代码用于其他显式选择 local 的部署。
- 服务已用既有 `llm_model` 环境重启在 `http://127.0.0.1:8100/`，鉴权保持启用。验收项目为 `图文阅读验收 · 2026-10-28`，ID `690fb993-bdc0-4648-bcdd-e6c6c8aec6e3`。

## 修改前基线

- 来源对象协议、来源仓储/API、文档解析和 Markdown 模块：29 passed，1 个第三方 Starlette/AnyIO 弃用警告。
- Node 全量：755 tests，751 passed，4 failed。四个失败属于已有图谱渲染修改：full-graph-progressive 的稀疏关系背景色，以及 full-graph-renderer 的节点精灵边界、3/5001 关系选择色。它们在本轮资料库前端修改前即已存在。
- 业务库存储只读核查：15 个对象，78,256 字节；16 个来源版本（Markdown 15、TXT 1）；16 个批次项仍保存 24,775 字节 BYTEA。14 个登记对象本地存在且校验通过，1 个缺失对象可从旧批次原字节和冻结 Markdown 分别还原到相同 SHA-256。
- 真实生成的 DOCX/PDF 基线均没有保存图片资产。初次 PDF 断网解析缺少 Docling layout 模型；模型准备后 PDF 可断网解析，但旧输出仍有 0 个图片标记、0 个资产。

## 已验证的基础设施

- 既有 MinIO 实例新增私有 knowledge-sources bucket；专用账号通过对象写入/读取/删除验证，并被拒绝访问 Milvus 的 a-bucket。
- KG_SOURCE_S3_* 配置写入被忽略的本机 .env，业务 backend 已切换为 s3。报告和代码不记录凭据。
- 真实 S3 adapter：流式上传、预期 SHA-256、相同内容去重、完整读取、引用保护与无引用删除通过。证据：忽略目录 .benchmarks/source-rich-reading/storage-smoke.json。
- Docling 本地模型已提前准备到 data/model/document-parser/docling（48 个文件，765,214,208 字节）；离线 PDF 解析通过。本地准备脚本不在业务请求里运行。
- 真实样本在 .benchmarks/source-rich-reading/fixtures，包括带图 Word、带图 PDF、Markdown+图片 ZIP、内嵌 Base64、公开外链、缺图，以及无文字配图。

## 实现验证进度

- 存储新增测试：适配/引用先出现 7 个预期失败后 8 passed；迁移 5 个预期失败后，与前项合计 13 passed。
- 存储/来源/批次相关回归：55 passed，1 个第三方弃用警告；追加只读流边界检查 4 passed。
- 依赖锁按现有环境更新，新增 boto3/botocore/jmespath/s3transfer；离线 uv lock --check 通过。
- 存储规格和代码质量复核均通过。复核发现的 ZIP restart_document 原包定位、迁移 CLI 构造器恢复 DML 两项，先出现 3 个预期失败，再以 33 passed 验证修正。新增检查覆盖错项目/错原包哈希拒绝、dry-run/apply 不改变无关运行状态、应用正常启动恢复行为不变。
- 图文解析首批 16 个预期失败后 16 passed；后续针对事务/超时/尺寸/重试等检查 46 passed。综合回归 128 passed、2 个异步轮询 5 秒预算超时；改为有界 15 秒轮询后，最后一次重点 4 文件回归为 53 passed。该结果对应后续质量复核之前的状态，不能代表全部最新修正均已通过。
- 解析规格和代码质量复核通过。最终媒体修正 17 passed、摄取与解析相关 27 passed；去除无图注图片的生成占位词另经 1 个预期失败后 1 passed，实际图注保持。结构归属处理覆盖原生/OCR 重复值、规范化引号、失准/重叠/缺口 charspan；OCR 表格及段落均保留像素，避免依赖字符串匹配或丢失阅读内容。图片准备采用按路径的引用队列，并在抓取/解码前限制数量。
- 真实离线原生解析最终 4/4 通过：DOCX、数字 PDF、扫描 PDF、原生文字与扫描表格混合 PDF。分别保留 720×260、499×180、720×260、501×250 图片；原生文字/图片顺序正确。证据为 `native-media-smoke.json`，已人工检查提取图片。
- 完整 Node 契约最后一次串行运行 843/843 通过，0 失败、0 跳过，证据为 `node-full-serial.log`。前序执行曾出现无关图谱计时失败，最终串行运行已通过。
- 最终来源相关 Node 复验 41/41 通过，证据为 `node-source-final.log`。
- 前端规格、代码质量及最终独立存储/媒体/阅读静态复核均无未解决的阻断问题。

## 真实服务与正式页面验收

- 正常登录后，通过实际批次/API 完成 8 个文件、9 篇资料：Word、数字 PDF、扫描 PDF、混合表格 PDF、Markdown 图片 ZIP（含无文字配图）、Base64 Markdown、公开外链 Markdown、缺图 Markdown。全部完成，外链图片已实际导入固化；缺图保留明确提示。
- 逐篇校验下载原件 SHA-256、图片 SHA-256、图文先后顺序和固定版本定位；不同项目读取来源版本被拒绝。旧版本及原 ZIP 保留。证据为 `live-acceptance.json`；新上传本地文件增量为 0。
- 正式主页面使用真实鉴权与真实数据，在 1440、768、390 宽度验证默认图文阅读、图片实际解码、放大/Escape、已保存正文切换、关闭清理及无横向溢出；桌面下载 Word 的字节哈希与原件一致，缺图状态可见。浏览器外部图片请求 0，JavaScript 页面异常 0。
- 正式页面证据为 `live-browser.json`、`live-library-{1440,768,390}.png`、`live-library-zoom-*.png` 和 `live-library-missing.png`。已人工查看桌面和手机截图；此前 `library-rich-*.png` 仅属于最小夹具，不能替代正式页面截图。
- 最终 Python 阅读/浏览器/静态契约回归 67 passed，0 failed、0 skipped，耗时 263.26 秒，证据为 `pytest-reader-final.log`。复跑定位并修正两处测试时序：快速 404 隐藏图片后改滚动可见容器；原生 dialog 的 Escape 后等待异步 close 清理。最终进程设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 排除本机无关第三方 pytest 插件，保留仓库 conftest、真实 PG 隔离夹具与 Edge；未加载生产 `.env`。

最终 Python 命令（既有解释器，唯一 pytest 会话）：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
& D:/anaconda/envs/llm_model/python.exe -u -m pytest -x -vv -o faulthandler_timeout=120 tests/service/test_source_rich_reading_browser.py tests/service/test_source_reader_location_browser.py tests/service/test_ingest_batch_browser.py tests/service/test_rendered_image_dimensions.py tests/service/test_source_processing_status_browser.py tests/service/test_frontend_static_contract.py
```

正式服务 `GET /api/health` 最终返回 200。本轮相关 diff 的 `git diff --check` 通过；未提交或覆盖其他同时进行的图谱、本体及嵌入改动。这里没有宣称全仓 Python 测试套件已跑完。

## 已知边界

合并前另在独立检出中核对了仅包含本次功能的提交内容：来源存储/迁移/媒体/上传/批次后端 89 passed，前端 Node 722 passed，固定解释器的离线锁检查通过。该检出没有本机模型目录，暴露并修正了 PDF 参数测试对下载模型的隐式依赖；参数测试现在创建临时预检文件，真实模型转换继续由独立的实际验收覆盖。

### 独立合并验收（2026-10-29）

- 功能提交 `06490a7` 与 `e21fe52` 的本体属性编辑集成后，Node 739 passed，阅读/浏览器/本体 Python 72 passed。唯一入口文件冲突保留双方资源，独立复核确认加载次序、路由及 54 个功能文件范围完整。
- 复核发现单文件严格模式的准备阶段异常未回写资料失败状态。先以 `processing != failed` 复现，再保留来源版本、使用回执版本 CAS 回写标准公开错误并继续抛出；覆盖同步/持久任务与 discovery/ontology 的 4 个回归，以及相关摄取/API，最终 27 passed。
- 使用独立检出的真实代码启动服务，在新项目 `图文阅读合并验收 · 2026-10-29`（`107ed908-499f-4c2d-bc24-a75464e00e1b`）重新完成 8 文件/9 资料，验证上传、原件/图片固化、图文顺序、下载哈希、版本定位及跨项目拒绝。公开外链第一次未固化，保留缺图版本；新导入版本随后成功，阅读不重抓外链。
- 重启加载状态修正后，真实异步严格模式缺图任务的 job 与资料均为 failed，公开错误为 source_asset_missing，固定版本原件下载 SHA-256 一致，避免资料永久显示处理中。
- 正式页面再次在 1440/768/390 宽度通过图片、放大、下载、纯文本、缺图和关闭检查，浏览器外部图片请求和页面 JavaScript 异常均为 0。全部来源对象的 MinIO 复核通过，两个工作目录均未生成 data/sources。
- 证据保存到主工作区忽略目录 `.benchmarks/source-rich-reading/merge-validation/`；本轮未将模型、凭据、旧来源备份或无关未提交改动纳入功能提交。
- 随后集成验证期间更新的 `master` 图谱探索提交 `20878e3`。该次新增集成没有修改后端 Python；入口资源冲突按双方意图合并。最终 Node 全量 804 passed，正式页面三个宽度再次通过，独立差异核查相对最新 master 仍为 54 个来源功能文件。

- 页面展示固定版本的解析结果；原 PDF/Word 提供下载，没有内嵌 Office/PDF 原版排版预览。
- Markdown 无法凭空恢复缺失图片。相对图片须随 ZIP 提供，Base64 可固化，公开外链在导入时抓取；私网、需登录或已失效的链接保留缺图提示。阅读时不再访问外链。
- 旧资料不会因升级自动重新解析出图片；需要以新来源版本重新导入/解析，旧证据继续引用原版本。
- 原件与图片保留不等于图形语义理解。扫描/OCR 仍受识别质量影响；仅文档模式保留图文阅读，抽取模式沿用质量门禁。

## 安全交付约定

生产迁移前停止旧后端，避免旧进程读取已清空的 BYTEA。所有目标对象须完整大小/哈希校验后才清除旧队列字节并切换 S3；本地原件副本保留，现有 Milvus bucket 与索引不改动。
