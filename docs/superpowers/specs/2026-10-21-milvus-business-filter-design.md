# 页面筛选与 Milvus 向量检索（收敛版）

> 本文中的自动 Metadata 字段发现与扩列已被用户后续决定取代。当前实施依据为 [固定 Milvus 字段与写入性能设计](2026-10-21-fixed-milvus-and-write-performance-design.md)：固定 21 列、取消 bf_ 扩展，需要新的具体业务字段时再显式变更结构。本文保留为历史设计记录。

用户于2026-10-21纠正：只实现当前页面已有的筛选能力，不把本体schema业务属性全部变成检索列；不要126列、关系两端全量展开和额外字段配置系统。本版取代前一版设计。PostgreSQL业务数据保留，旧Milvus索引已按授权清空，无旧格式迁移/兼容读取。

## 唯一流程

页面选项目并显式向量化 → 读取当前可检索记录及页面实际业务筛选字段 → 文本编码和原生列写入 → 当前三种检索先Milvus条件筛选再排名 → PostgreSQL只按有限命中回填、核验。未构建项目的PG读写正常，零自动编码；缺索引明确提示向量化。服务启动/打开页面不自动重建。

本体schema定义结构，通常不改变文本编码；页面本体版本选择仅通过ontology_id影响候选范围。本体查询扩展是现有显式功能，不能误当作全量属性列生成。

## 列只服务于现有页面

固定关联列：project_id、record_id、version_id。排名列：text、embedding、sparse(BM25)。页面筛选列：kind、type、ontology_id；platform/business/audience各自mode+values；valid_from_us、valid_until_us；chunk_owner_id（公开source_id）。另一个filter_present数组只用于现有exists/null条件的区别，不再为每个字段生成presence列。

业务与关联固定19列（下文共享集合的构建隔离再加2列）。当前页面提供的业务metadata叶子字段按实际字段清单平铺，每路径仅一列；排除技术调试字段，不凭空固定title/status/confidence等默认项，不新增字段配置页面/目录表/CAS系统。字段映射保存在已有PG构建状态，SDK只接收列条件，不在Milvus保存业务JSON/dynamic。新增字段/不兼容类型明确要求当前项目重新向量化，不透明走全候选PG扫描。原生类型、数字精度、字符串转义和UTF8/数组容量校验保留。

不保存subject_/object_资格副本、active/visible、序号/水位、schema/model/fingerprint等控制列。只写当前可检索行，删除/不可见用删除事件处理。PG控制状态保存模型、字段schema、同步水位和任务信息。

关系端点按PG有限命中做权威检查，有界补取。命中剔除导致预算不足时明确返回未完成/需追平，不能伪装为全部候选已穷尽。历史语义/混合明确不支持；历史关键词SQL过滤排名LIMIT。图谱和台账继续走PG结构/分页接口。

## 共用一个主集合与清晰命名

当前各项目使用同一向量模型/维度和页面筛选能力，统一主集合knowledge_vectors。project_id必须进入每一个查询、删除和重建条件，可用作Milvus partition key。项目维度不同才另开模型集合；本次不做预留架构。

不使用项目哈希/UUID/日期/维度/模型参数拼接集合名，不按项目常驻不同集合，也不新建control集合。索引明确命名dense、bm25、filter_<列名>；细节参数保存在配置。

显式重建只替换所选项目。为防旧RPC污染重建结果，增加两个必要内部列：vector_id作为包含项目/记录版本/构建版本的唯一主键，index_version为项目构建版本；这两个有具体隔离作用，不是审计副本。固定列因此21个。PG现有projection_cursors保存本项目激活index_version，查询强制project_id+index_version条件，新构建验证后PG原子激活，确认无引用后仅删除本项目旧版本行。构建中向量和ready增量使用同一项目版本，不清整张集合，不影响其他项目。

## 本次范围

继续完成原登录前闪页、隐藏页面请求、候选全量读取、重复渲染修复。新方案只围绕页面实际筛选、显式项目向量化和直接Milvus检索，避免扩大为新的配置平台。所有证明必须基于收敛后的生产代码；126列实验仅为废弃诊断，已清理测试集合。
