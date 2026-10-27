# 测试使用指南

这里保留现行功能回归、支持夹具和独立浏览器检查。文件数量不代表重复：同一个功能通常分别有服务规则、PG 事务、API、前端状态和浏览器操作的测试。

## 默认回归

在仓库根目录、既有 `llm_model` 环境执行，Python 和 Node 分开运行：

```powershell
python -m pytest -q
node --test tests/service/*.test.cjs
```

| 范围 | 入口 | 说明 |
| --- | --- | --- |
| Python 服务、API、仓储和浏览器回归 | `tests/service/test_*.py` | pytest 自动收集；其中不少需要 PostgreSQL，部分需要系统 Edge/Playwright |
| 运维脚本安全回归 | `tests/scripts/test_*.py` | 已纳入 pytest 默认 `testpaths`，含迁移/停止写入/归档安全检查 |
| Node 前端契约 | `tests/service/*.test.cjs` | Node 自动展开该 glob；pytest 不收集，也不要用 Node 目录形式运行 |
| 独立浏览器布局 | `tests/service/qa-viewport-layout.browser.cjs` | 单独运行；需要 Node 可加载 `playwright`、系统浏览器或 `KG_BROWSER_EXECUTABLE` |
| 会话浏览器交互 | `tests/service/qa-conversations.browser.cjs` | 完整工作台 + HTTP 夹具；验证跨浏览器恢复、失败重试、导入、手机菜单与输入框边界，不调用模型 |
| 共享夹具 | `pg_support.py`、`ontology_seed.py`、`fixtures/*` | 被其他测试导入，不能因自己没有 test 函数而删除 |

独立浏览器布局入口：

```powershell
node --test tests/service/qa-viewport-layout.browser.cjs
```

该入口不是普通 Node 契约 glob 的一部分。它使用实际 HTML/CSS 验证移动端展开引用、异步侧栏变化后输入框仍可见，不访问业务 API 或模型。使用已有 Playwright 安装，通过 `NODE_PATH` 提供其模块；不要在应用目录新增前端构建依赖。

纯 Docker 迁移安全测试也可以绕开 pytest 的全局 PG fixture 独立运行：

```powershell
python -m unittest discover -s tests/scripts -p "test_*.py"
```

## 运行约束

- **只能有一个 pytest 会话。** `pg_support` 使用并清理独立的 `knowledge_test` 库，并行会互相删库。不要连接正常业务库。
- pytest 已通过 `KG_SKIP_DOTENV` 隔离本机配置，运行前不要把生产 `.env` 导入进程。应用真实服务与密闭测试使用不同配置。
- PG 不可用时，当前全局 fixture 会 skip 包括脚本安全测试在内的依赖用例。看见 skip 需要记录原因，不能当作验证成功。
- strict xfail 用于明确记录尚未修复的缺陷，例如来源读取的 MVCC 一致性问题；不属于可删除的历史垃圾。
- 测试文件之间存在共享导入和 `runpy` 依赖，不能直接按功能批量搬目录。
- 截图和临时验收输出写入被 Git 忽略的 `.benchmarks/`；当前手册使用的正式截图在 `docs/manual-redesign/`。

## 专项与手动工具

存储与检索专项、真实模型和容量验收的范围不同。前者可以使用有界模拟外部系统验证事务/版本规则；后者必须核对真实模型、来源、索引覆盖及资源环境。两者不能互相冒充。

当前 31 项存储失败的更新和 255 项专项复跑见 [补齐记录](../docs/research/2026-10-22-storage-regression-followup.md)。全量用例数以 `python -m pytest --collect-only -q` 的实际输出为准，不把该专项数字当作全量通过。

手动 CLI、需要正常服务/真实模型的操作与历史迁移演练请看 [脚本索引](../scripts/README.md)。

## 退役原则

只删除指向已移除实现且已有现行替代覆盖的测试，不按文件日期、失败、skip 或未被 import 直接判死。

本次移除的旧静态页面测试指向不存在的 `data/kg_web/static`，现行主入口由 `test_frontend_static_contract.py` 覆盖；旧 Milvus reset 专属测试随退役工具移除，当前共享集合的构建、投递、清理与回执测试继续保留。细目见 [文件清理记录](../docs/research/2026-10-22-project-file-cleanup.md)。
