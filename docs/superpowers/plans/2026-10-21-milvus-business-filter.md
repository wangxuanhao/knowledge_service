# 页面范围筛选与向量化实施清单（用户纠正后）

使用subagent-driven-development及独立审查，唯一conda llm_model，一个pytest会话，沿用当前checkout保留无关dirty改动。规格：docs/superpowers/specs/2026-10-21-milvus-business-filter-design.md。

- [x] 仅清空本应用Milvus派生索引及对应PG派生状态，业务表计数保持不变。
- [x] 停止126列实验并清理全部自有UUID集合；不把废弃实验当最终验收。
- [x] 移除服务启动/重连自动创建集合、worker自动全项目重建。
- [x] 撤回新增的业务字段配置表/目录CAS/API扩张（UI收尾进行中）。
- [ ] 收敛19个业务/关联固定列+2个共享集合构建隔离列+页面实际业务metadata列；无端点整套复制，无业务JSON/dynamic，无每列presence布尔。
- [ ] 唯一纯投影构造器与PG收集器对齐收敛字段；当前正式可见行、Decimal原文、微秒时间、类型和字节限制。
- [ ] 按项目页面现有筛选能力产生字段清单，存已有PG构建状态；不做全属性自动扩张或独立配置系统。
- [ ] 共用knowledge_vectors主集合，命名不拼参数；project_id+index_version条件和vector_id隔离所选项目重建及迟到写，删除常驻control集合与旧回滚副本。
- [ ] 事件不存dense；未建项目CRUD零编码，ready增量；selected项目显式构建，禁止自动处理容量项目。
- [ ] 当前keyword/semantic/hybrid直接Milvus条件后排名，删除PG全候选ID预筛、透明降级和本地向量排序；有限PG回填/关系端点复核/补取。
- [ ] 项目页面只展示索引状态和向量化入口，不新增字段配置系统；仅可见构建任务需要时轮询。
- [ ] 修完浏览器旧fixture/抽屉使用/异步等待等回归，保留真实无重复读取修复。
- [ ] 收敛后生产schema和三模式真实Milvus验证、PG回归、Node全套、compileall/diffcheck、无测试/重建负载的页面耗时验收。
- [ ] 应用必要迁移（实际PG仍23，0024待应用；已撤回未上线0025目录迁移），启动8100，新建只选普通项目，保存脱敏最终证据。

环境：D:/anaconda/envs/llm_model/python.exe -X utf8；不建venv，不source .env，不输出密钥，本机HTTP trust_env=False。当前8100停着，不能声称在线。
