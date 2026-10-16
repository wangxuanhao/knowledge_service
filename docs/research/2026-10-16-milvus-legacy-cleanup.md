# Milvus collection 核对与旧结构清理

日期：2026-10-16。执行者：主 agent。用户授权核对并删除无用 collection，随后提供 `knowledge_records is not V2` 启动警告。

## 原因与实际用途

本机默认 Milvus 数据库中有 15 个 collection：7 个当前项目索引代、7 个同名 `__control` 控制 collection、1 个旧共享 `knowledge_records`。`__p_` 后是项目 ID 的 SHA-256 前 16 位，`__g_` 后是重建代标识。项目别名在重建校验完成后切换，旧代保留回滚窗口（当前 7 天）后由后台维护清理。`__control` 保存同步水位/围栏校验信息，并不是额外业务文档。

用户举出的 `knowledge_records__p_2b7f7c93010cd7e0__g_b7420b6d_affd_40e6_916b_bc32ab3c` 属于项目“提升后建的项目-7767”（`463f2e99-ec8b-4ca2-9c0d-e469bc4d1411`）。虽然业务行数为 0，PostgreSQL 的 active_destination 与 Milvus 项目别名均指向它，不能按空表删除。

所有 7 个项目游标均 ready；没有待清理项目、待回滚旧代或失去引用的项目索引代。旧 `knowledge_records` 缺少 V2 字段和索引，是本次警告的来源；警告并非这些当前索引代逐个损坏。

## 清理前核对

- 逐条读取旧表的项目 ID/版本 ID：59 条；7 个当前索引代共 64 条。
- 旧表的 59 条精确版本全部包含在当前索引代中，没有未知项目，也没有遗漏版本。
- 旧表没有任何别名，未被活动游标、回滚引用或重建目标引用。
- 变更前在事务中锁定 Milvus 游标，再次确认游标 ready、项目别名未变、旧表内容未变；不按名称通配删除 collection。

## 执行动作

仅重建 `knowledge_records`：移除已被新代完整覆盖的 59 条旧索引副本，按当前格式建立空的 V2 默认 collection。当前应用启动仍检查默认 collection，因此保留一个兼容的空默认 collection，而不让下次启动重新创建旧壳。

重建后默认表行数为 0，V2 字段/索引校验通过；7 个活动索引代及其控制 collection 保留，所有项目别名不变，PostgreSQL 原文、实体和版本数据未修改。总 collection 数仍为 15；数量本身不意味着垃圾积累。

本次仅处理外部派生索引和重启验证，没有修改生产 Python 逻辑。审计原始结果保存在本地 `.benchmarks/milvus-collection-audit.json`、`milvus-legacy-coverage.json`、`milvus-legacy-repair.json`；其中无密钥或文档正文。

## 重启后验证

服务已重启（本轮启动 PID 37964）。日志出现 `Application startup complete`，没有 `is not V2` 或维度不匹配警告。健康检查 HTTP 200；真实跨平台证据问答仍返回 `_backend=milvus`、`degraded=false`，两平台各自的有效证据及历史/未来参考均保留。验证结果保存为 `.benchmarks/milvus-post-repair-verification.json`。
