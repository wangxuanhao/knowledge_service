# Source Library Progressive Search Implementation Plan

> **For agentic workers:** REQUIRED: Use subagent-driven-development; independent plan/spec and code review. One pytest process only.

**Goal:** 支持最多8步关键词/向量在命中资料全部正文中递进检索，合入master推送。

**Architecture:** 来源检索编排服务重放 search_steps，PG关键词谓词不枚举全量ID；语义前缀产生有界父资料ID范围，最后一步复用现有目录或来源语义服务。前端保留此前条件与当前条件，显式追加、删除及退回。

**Tech Stack:** FastAPI/Pydantic/PostgreSQL/Milvus、原生JS/CSS、pytest/Node/Playwright。

- [x] 后端：先在 tests/service/test_source_progressive_search.py 写失败用例，覆盖跨页关键词AND、关键词→语义父门禁、语义→关键词→语义搜索其他片段、零结果不扩范围、游标/参数/状态/错误。新增 services/source_progressive_search.py，修改 api/parity.py、services/explorer.py、repository/scoped_reads.py；不改UI。运行单一pytest回归。
- [x] 前端：先写 tests/service/source-progressive-search.test.cjs 并RED；修改 sources-view.js、sources-library.css、index.html及必要夹具/版本断言；保留单次行为，验证追加/删除/退回/IME/缓存/竞态/错误/计数/分页。运行Node套件。
- [x] 独立审查：先审规格/计划，再分别审实现的规格和质量，修复后复验。
- [x] 集成：扩展 tests/service/test_source_library_search_browser.py 桌面手机流程，运行后端+浏览器回归和Node套件，检查截图；更新操作说明；重启8100健康检查。
- [x] 集成master：仅暂存资料库搜索相关文件，保留其他任务图谱改动；检查远端与分支，按授权合入master（若已在master直接提交），正常推送不强推，确认远端提交。

验证：最终单pytest会话合并回归 59 passed（含6个桌面/手机浏览器用例）；完整Node套件1083 passed。独立规格和质量复审均通过；已复现并修正父资料私有检索条件误用于派生内容统计的问题。截图在 `.benchmarks/source-library-search/progressive-1440.png` 与 `progressive-390.png`。

交付：当前checkout已在master，采用直接提交并正常推送origin/master；本地8100服务已重启，status=ok、semantic=ready，静态资源包含递进检索控件与请求。其他资料文件不纳入提交。
