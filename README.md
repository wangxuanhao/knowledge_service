# 知识服务

更新日期：2026-11-12。FastAPI + PostgreSQL + Milvus 的知识平台，提供资料解析、受控/开放抽取、本体草案治理、知识编辑与融合、图谱浏览、检索和证据问答。

先看这些入口：

- [知识平台完整操作手册](docs/知识平台完整操作手册.html) · [可编辑源文件](docs/知识平台操作手册.md)：2026-11-16 按使用流程重写，覆盖本体、资料、属性、合并、冲突、检索图谱与问答；附真实截图和示意图，HTML 图片内嵌，可离线阅读、搜索、放大和打印。
- [服务启动及运维手册](docs/服务启动及运维手册.html)：部署、启动、启停、健康检查、配置速查、模型准备、数据卷与备份、故障排查。
- [历史详细技术手册](docs/2026-10-20-知识平台完整操作手册.md)：保留早期技术说明，当前操作以新版流程手册为准。
- [资料上传与 ZIP 打包操作手册](docs/平台规则资料上传与ZIP打包操作手册.md)：独立图片和长图、单资料 ZIP、图片页序、部分规则选件、旧包拆分与页面追加上传。
- [测试怎么跑](tests/README.md)：Python、Node、独立浏览器测试及环境要求。
- [脚本怎么选](scripts/README.md)：日常工具、容量验证、待适配专项和历史迁移演练。
- [文档索引](docs/README.md)：当前说明、当前规格、验收记录和历史设计。

## 当前存储与能力

| 组件 | 职责 |
| --- | --- |
| PostgreSQL | 唯一业务权威：记录、修订、本体发布、断言、来源、OCR/ASR 结果与分块进度、任务和审计 |
| Milvus | 共享 `knowledge_vectors` 的 dense/BM25 检索投影，按项目与构建代际隔离，可显式重建 |
| Neo4j | 可选图谱投影；不替代 PostgreSQL 正式写事务与版本权威 |
| etcd / MinIO | etcd 保存 Milvus 元数据；MinIO 分 bucket 保存 Milvus 文件与业务原始 ZIP、原文件、图片和视频，业务版本与引用以 PostgreSQL 为准 |

当前关键词、语义和混合检索都要求项目 Milvus 索引就绪；失败时明确报错，不自动切模式或本地向量降级。Metadata 资格由 PostgreSQL 完整判定，再由 Milvus 排名。主动指定系统历史时点时，仅支持 PostgreSQL 历史关键词查询。

处理批次管理资料与抽取结果；工作草案管理一轮本体结构变更和明确加入的候选依赖。事实写入、本体发布和索引覆盖分别核对。已有正式本体可以继续建草案修改；普通事实补充也可以只复用已发布术语，不必新建结构草案。

当前操作与能力边界见完整手册；本轮上传格式、ZIP 规则和图片限额以资料上传与 ZIP 打包操作手册为准。社区 Global/DRIFT、项目成员 ACL 等尚未完成的能力不作为已交付功能。

资料可直接上传 TXT、MD、PDF、DOCX、HTML、静态 PNG/JPG/JPEG/WebP 和 MP4/WebM。单张图片限 10 MiB、4000 万像素，动画或多帧图片拒绝；单个上传文件或 ZIP 默认上限为 25,000,000 字节。长图自动分块 OCR 后仍是一份资料，Word 可提取的内嵌图片自动进入 OCR，复杂浮动布局不保证还原。视频只做语音 ASR，不做帧 OCR。

ZIP 用于唯一根目录 MD / HTML 主文档和其引用的 `assets/`，普通包不需要 `manifest.json`；PDF、Word、独立图片和视频直接上传。旧单资料清单也须满足根主文档要求；`document.pages` 只保留已入库历史来源读取，新上传的连续截图用一个根 MD 按顺序引用。旧多资料包须逐份整理。打包脚本可为每份选中的 MD 分别生成一个包。原件在对应文件行预览；正文与切片在明确开始解析、完成后检查，不再切换 ZIP 内多份文档。

添加文件采用“上传核对 → 配置资料 → 解析与检查 → 确认处理”四步。上传只暂存和验证；同批可逐文件配置不同平台、业务和时间，也可批量设置所选文件。明确点击“开始解析”后才执行 OCR/ASR、分块和切片向量预计算；草稿不进入正式检索。核对后按模式提交本体构建、知识抽取或文档索引任务，已提交文件从上传清单移出，去“处理批次”看进度。

资料详情的“重新处理 / 构建图谱”可复用固定来源，无需重新上传；资料 ID 不变，创建新处理代际并保留历史。“仅文档检索”不抽取实体关系；以后可选“开放发现”产生待审核候选，或“使用项目本体”按已发布本体抽取。单项 OCR 重试只更新识别结果，更新检索切片或图谱需整份重新处理。项目首次索引、旧版本重建与后续增量更新由后台自动处理；资料库、检索图谱、问答和台账均可打开“检索索引”查看进度、覆盖数量和错误，管理员可手动重建。

页面混合检索默认“限定关键词范围”（关键词筛选后语义排序），也可选择“扩展相近含义”（关键词与语义分别召回后融合）。最低语义相似度默认 0.35，可调整；语义一路先按阈值过滤，融合分不是相似概率。标题、上传文件名与切片正文共同参与索引。详见[检索范围与策略](docs/library-retrieval.md)及[自动索引生命周期](docs/search-index-lifecycle.md)。

## 项目目录

```text
knowledge_service/       应用包
  api/                   HTTP 路由、认证授权与装配
  services/              写入、本体治理、检索问答、图谱和任务编排
  repository/            PostgreSQL 仓储、投递回执与编号迁移
  domain/                业务与检索投影契约
  integrations/          Semantica、模型、Milvus、Neo4j、文件解析
  core/ utils/           配置、日志、时间和公共工具
  web/                   原生 JS/CSS/HTML，无前端构建工具
  resources/             默认资源与解析配置
scripts/                 手动工具和专项验收，先读 scripts/README.md
tests/                  自动化回归与支持夹具，先读 tests/README.md
docs/                   当前手册、规格及历史验收记录
docker/                 基础设施配置
data/                   本地模型、来源文件及样本数据，勿作缓存清理
```

外部入口：`knowledge_service`。依赖方向为 `api → services → repository`，`core/utils/domain/integrations` 按各自边界提供支持。开发提示见 [AGENTS.md](AGENTS.md)，领域词汇见 [CONTEXT.md](CONTEXT.md)。

## 启动

使用既有 conda 环境 `llm_model`、Python 3.12；不要另建 `.venv`。新部署按 [.env.example](.env.example) 填写本机配置，已有部署保留自己的 `.env`，不要提交凭据。

```powershell
conda activate llm_model
# 仅新环境需要安装依赖：
python -m pip install -e ".[local-export,milvus,neo4j,ocr,asr,semantica-runtime,test]"
docker compose up -d --wait
python -m knowledge_service.repository.migrate
# 本地 BGE-M3 已放好后，首次或更换权重时准备一次；保留原始权重：
python scripts/prepare_embedding_runtime.py --model-dir data/model/embedding/bge-m3
python -u -m knowledge_service --port 8100
```

工作台：[http://127.0.0.1:8100/](http://127.0.0.1:8100/)，API 文档：[http://127.0.0.1:8100/docs](http://127.0.0.1:8100/docs)。默认只监听本机；修改后端后需重启服务。

- 迁移器使用独立迁移凭据；应用账号不执行 DDL。不要删除或修改已经应用的编号 SQL。
- 首次登录与账号管理按手册第 2 章操作。生产保持认证授权开启。
- 当前部署使用 Docker Linux 命名卷。迁移/复制前先读 [数据卷迁移指南](docs/2026-10-19-Docker-Linux-volume-migration.md)，不要用新空卷覆盖现有挂载。
- 本地 embedding 模型、LLM/OCR 配置按手册和 `.env.example` 设置。首次模型加载与暖态查询分开计时。

超级管理员可在「管理 → 模型管理」保存多套 OpenAI 兼容 LLM 配置，测试连接并设置全局默认模型。新开始的抽取和问答使用当前默认模型，运行中的任务及其重试沿用已固定的配置。尚未启用平台模型时使用既有 `KG_LLM_BASE_URL`、`KG_LLM_MODEL`、`KG_LLM_API_KEY`。

平台配置和任务快照中的密钥加密保存。部署时可配置固定的 `KG_LLM_ENCRYPTION_KEY`，留空则首次使用时生成受限文件 `data/runtime/llm.key`，也可通过 `KG_LLM_KEY_FILE` 指定路径。请将密钥与数据库配套备份，多实例共享同一密钥。恢复数据库后须恢复原密钥，丢失密钥会导致已保存的模型配置和任务快照无法解密。
- 本地 embedding 在启动时后台预热；语义/混合查询在模型准备好前会明确返回初始化状态。部署探针使用 `/readyz` 判断就绪，不能只看进程启动或 `/livez` 存活。
- 确认提交资料后自动建立项目索引，正式知识变化后自动同步；在“检索索引”确认可用，模型或共享集合结构异常按错误恢复。

上述 `pip install -e` 按 `pyproject.toml` 解析依赖，不读取 `uv.lock`。锁文件与既有 `llm_model` 环境不是同一版本快照，不能据此声称部署已复现；更换模型依赖组合前需验证实际本地模型、向量兼容性和文档解析。当前依赖、循环导入与冷启动评估见 [依赖审计](docs/research/2026-10-28-dependency-audit.md)。

本地推理分为两个依赖入口：`local-export` 用于离线准备和向量对照，包含完整模型框架；`local-onnx` 只需要 ONNX Runtime 与 Tokenizers。已准备的 BGE-M3 在线默认使用轻量运行时，不导入 Torch/Transformers/SentenceTransformers。部署时保留原模型与整个 `onnx-runtime` 目录，初始化会核对源文件和全部制品。普通服务启动只读取制品，不重复导出。

视频 ASR 使用 `asr` 可选依赖。本机已验证 FunASR 1.3.22、Torch / Torchaudio 2.8.0 与 CPU 双线程，运行主机须在 PATH 提供 FFmpeg / ffprobe。部署时复制完整的 `iic/SenseVoiceSmall` 与 `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch` 本地模型目录，分别配置 `KG_VIDEO_ASR_MODEL_ROOT` 与 `KG_VIDEO_ASR_VAD_ROOT`。两份模型使用 Apache-2.0 许可证，复制时保留各自许可证与说明。Worker 禁用网络、模型更新和远程代码，每个任务独立启动并退出释放模型内存，默认超时 600 秒，首次导入模型耗时计入该预算。

ASR 模型统一放在项目目录，不能只保留 `model.pt`；配置、词表和说明须一起复制：

```text
data/model/asr/
  SenseVoiceSmall/
  speech_fsmn_vad_zh-cn-16k-common-pytorch/
```

```dotenv
KG_VIDEO_ASR_MODEL_ROOT=data/model/asr/SenseVoiceSmall
KG_VIDEO_ASR_VAD_ROOT=data/model/asr/speech_fsmn_vad_zh-cn-16k-common-pytorch
```

从仓库根目录启动服务。修改模型路径后重启，先检查 `/api/ingest/capabilities` 中 `asr_available`，再上传含语音的本地短视频核对真实转写和播放；可用性检查不能替代实际推理。模型不随 Git 提交，迁移机器时须单独复制。此次校验和离线实测见 [ASR 模型迁移与项目清理](docs/2026-11-12-ASR模型迁移与项目清理.md)。

首期视频上限为 25 MB、10 分钟，支持 H.264 / AAC MP4 与 VP8、VP9、AV1 配 Opus / Vorbis WebM。独立视频、Markdown 的 `<video src="assets/clip.mp4">` 与指向本地附件（例如 `assets/clip.webm`）的 Markdown 超链接共用上传和 ZIP 附件链路。本地视频原件进入来源对象存储，ASR 文字与真实 VAD 时段进入 PostgreSQL，阅读器通过鉴权 Blob 播放并按转写段定位。预览展示原件，正式处理执行转写。外部视频链接标记为待固化，由提交者提供本地附件。

`KG_LOCAL_EMBEDDING_RUNTIME=auto` 自动选择有效制品；没有制品的其他模型兼容原 ST 路径。`onnx` 强制要求已准备制品；`sentence-transformers` 可显式回到原运行时。已存在但过期或损坏的制品会明确失败，需要重新准备，避免向既有索引写入不同语义的向量。实测、兼容范围与恢复方式见 [推理依赖拆分](docs/research/2026-10-28-embedding-runtime-optimization.md)。

## 测试与手册

```powershell
python -m pytest -q
node --test tests/service/*.test.cjs
python scripts/build_knowledge_service_manual.py
```

Python 默认收集 `tests/service` 和 `tests/scripts`。Node 的 `.cjs` 必须单独运行；需要 Playwright 的独立浏览器布局测试另见测试指南。脚本不被 import 不能证明没用，手动容量和运维工具也不属于默认回归。

只运行一个 pytest 会话：测试使用隔离的 `knowledge_test` 数据库，会话结束会清理该库。测试进程不要加载生产 `.env`。PostgreSQL 不可用产生的 skip 不算通过；本机 HTTP 客户端应绕过系统代理。

本轮存储和检索专项回归结果是 [255 项通过](docs/research/2026-10-22-storage-regression-followup.md)，不是仓库全量测试结论。项目整理后的收集与验证结果见 [文件清理记录](docs/research/2026-10-22-project-file-cleanup.md)。

## 真实验收与容量

2026-11-10 按用户要求备份并清理历史项目，当前保留“案例演示 · 平台规则知识图谱”，包含 2 份原文、85 个实体和 69 条关系。原容量验收项目已纳入清理前备份，具体备份、核验及保留范围见[项目整理记录](docs/2026-11-10-项目整理记录.md)。

本体编辑、实体融合/消歧/撤销、版本治理及现行回归均保留。当前 15 万节点向量检索性能、新的性能 P95 和有效规则问答质量仍有待验收，普通小项目或单项绘图测量不能替代。详见 [生产检查点](docs/research/2026-10-22-production-checkpoint.md) 和完整手册第 27 章。

历史方案和实测记录继续保留在 `docs/research` 与 `docs/superpowers`，按文档索引查找。已退役的一次性工具和旧部署说明可从 Git 历史恢复；不再保留看似可运行的空壳入口。

> AI生成
