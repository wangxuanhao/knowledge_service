# Source Library Search Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development for independent UI work; preserve one pytest session at a time.

**Goal:** 资料库可按名称、原文件名和正文搜索全部符合当前范围的资料。

**Architecture:** API/Explorer/PageReads 透传 query，ScopedReads 为 document 添加名称/文件名匹配，复用 keyset 游标和 scope。原生 JS 搜索表单管理已应用词和请求身份。

**Tech Stack:** FastAPI/PostgreSQL/Python 3.12、原生 JS/CSS、pytest、node:test。

- [x] 在 tests/service/test_page_summary_reads.py 增加搜索与游标/隔离测试，先运行得到预期失败。
- [x] 修改 api/parity.py、services/explorer.py、repository/page_reads.py、repository/scoped_reads.py，实现查询、计数及分页传递；运行回归。
- [x] UI 在 web/sources-view.js、sources-library.css 添加搜索/清空与搜索身份，tests/service/sources-library-search.test.cjs 验证交互；更新 index.html 对应资源版本。
- [x] 独立检查规格与实现，修复问题；运行相关 Python 和前端测试，浏览器 smoke；重启服务验证实际脚本。
- [x] 记录验证及使用说明；保留其他任务未提交文件，不默认提交或推送。

## 验证记录

- 新增后端搜索场景先红后绿；目录分页与范围回归通过。
- 搜索、资料阅读及分页相关 Node 测试 43 项通过；完整 Node 检查遇到并行图谱任务的两个 beginNodeDrag 失败，未修改其文件或声称全量通过。
- 桌面 1440×950 与手机 390×844 的真实浏览器搜索、Enter、清空、计数及布局检查 2 项通过，截图在 .benchmarks/source-library-search/。
- 独立审查没有发现阻塞问题；语法与本次 diff 空白检查通过。
- 8100 服务已重启，未修改用户资料或提交其他任务文件。
