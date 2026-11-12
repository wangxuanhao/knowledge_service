# 资料库关键词与向量正文切换

用户要求在资料库搜索增加向量/关键词开关并授权自行设计。扩展已有关键词能力，默认 keyword，可切 semantic（显示“向量正文”）。空关键词时两种模式均展示普通目录，不调用模型；证据定位清空搜索并恢复关键词模式。

## 后端

POST sources 新增 retrieval_mode: keyword|semantic，默认 keyword；保持 query、Scope 和现有分页参数。keyword 行为不变。semantic 非空查询使用真实 RetrievalEngine + Milvus，仅 content_channels=['chunk']，k=100，不使用实体/关系，不降级成本地向量或关键词。

增加服务端私有 `_source_document_scope`（含完整原始资料 Scope）用于 chunk 资格门禁。ScopedReads 通过 EXISTS 父资料版本将原始项目/时间/适用性/Metadata/id 等过滤应用到 document，chunk 本身仍按时间/适用性/可见性过滤，禁止将文档 id/title 过滤错用到 chunk。RetrievalEngine 私有范围强制 qualified-version batches，Milvus 接收精确 eligible_version_ids，再按 PG 版本回验。公共 Scope 禁止直接提交私有字段。私有 gate 不得改变普通检索行为。

向量召回最多100片段，按资料聚合，资料排名取最高片段得分，稳定次序用资料ID打破同分；返回一条代表命中的短片段（最多280字符）及 chunk id/version，文档原文仍按需读取。文档归属和最新可见版本必须按同一原始范围再核验，不信任向量投影的任意来源标记。失效/删除/越范围命中不能展示。索引/编码模型不可用或 known_at 历史语义检索不支持时明确失败，不能显示成零结果。所有片段来源失效时不把不完整结果声称成完整召回。

对召回资料再按 state 筛选，counts 是本次最多100片段召回资料的各状态计数，total 是该召回集下当前状态的资料数，不是全项目语义匹配总数。分页只遍历该排名集合；游标绑定 project/scope/query/mode/索引身份及排序资料版本集合，变化拒绝，不能跨 mode/scope 重用。可每页重新检索并核验签名，不持久化新真值。空查询及 document_id 直接定位走目录，不能让 query/mode 阻碍证据跳转。

可加 PageReads 有界来源摘要批量读取（最多100文档），不逐条查100次、不回传整篇正文。结果附 search_mode、result_limit=100、matched_chunk_count、total_is_exact=false（向量召回口径）、complete 等必要语义状态。关键词保持旧响应兼容。

## 前端

搜索栏增加有标签模式选择“关键词 / 向量正文”。mode 和 appliedQuery 都加入缓存/请求身份；切模式从头加载，保持输入和范围，输入有词时按当前词搜索；旧请求不能覆盖新模式。清空在当前模式显示普通目录；切项目和显式引用定位恢复默认关键词。向量列表标题显示语义召回口径，状态计数仅指本次召回集，提示最多100个片段；展示命中正文片段，文本必须转义。空结果不得声称“全项目没有相关正文”。保留移动端换行、IME、清空和刷新行为。

## 验证

测试先行：父资料 id/Metadata/权限范围门禁在 Milvus 排名之前生效；chunk-only、聚合去重与排名、片段有界、状态口径、分页签名、过期/外项目命中、索引失败显式暴露、历史视图、空查询/引用定位。前端切模式/慢请求/缓存/分页/摘要转义，现有关键词回归，桌面与手机真实浏览器检查。仅一个 pytest 会话。保留并行图谱任务文件，不发布或迁移真实资料。
