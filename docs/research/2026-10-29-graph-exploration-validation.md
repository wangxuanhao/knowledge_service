# 图谱探索工具验证（2026-10-29）

本轮完成已批准的 Semantica 第一轮能力：当前画布邻居列表、局部分支展开/收起、静态方向及 1/2 跳聚焦。继续使用现有局部及全量渲染器，未新增 npm 依赖、后端接口或持续动画。首次实现阶段未提交或推送；用户随后要求测试并合并 master，隔离发布验证见末节。

## 实现与保护

- `graph-exploration.js` 提供 typed-array 邻接索引和分支模型；全量图首次使用工具时才异步准备索引，每批最多 4,000 条。并发请求共享准备过程，追加只处理新记录。
- 邻居每页 30 条；聚焦最多 200 节点、400 关系，扫描最多 2,000 条边。全量聚焦复用 Canvas2D 标签层画有界覆盖层，普通 GL 绘制及概览缓存保持原路径。
- 分支从基础图求可达性，收起保留其他分支共享节点及后代；无根循环不能自我保活。收起、模式切换、范围清空、重启使迟到请求失效。
- 邻居和聚焦使用实际类型投影，避免通过隐藏类型绕行；名称关闭时聚焦不恢复名称。路径强调保留原有优先级。
- 画布上浮层不增加布局轨道；工具栏根据实际宽度换行，两模式仍同顶同高。全量分支展开明确进入局部，不改变冻结会话计数。
- 七个本轮新增/修改脚本及样式的资源版本统一为 `graph-exploration-20261029-1`，其他任务资源版本保留。

## 功能证据

Node 命令：

```powershell
node --test tests/service/graph-exploration.test.cjs tests/service/graph-explorer.test.cjs tests/service/full-graph-exploration.test.cjs tests/service/full-graph-renderer.test.cjs tests/service/full-graph-progressive.test.cjs tests/service/local-graph-neighborhood.test.cjs tests/service/graph-paths.test.cjs tests/service/graph-types.test.cjs tests/service/graph-interaction.test.cjs tests/service/graph-display-controls.test.cjs tests/service/retrieval-graph-view.test.cjs
```

结果：160 项通过，0 失败、0 跳过。包括 28 项纯模型、14 项控制器和 16 项全量探索测试，以及现有加载、类型、路径、交互和名称控制回归。

独立软件浏览器入口：

```powershell
& 'D:/anaconda/envs/llm_model/python.exe' -X utf8 tests/service/graph-exploration.browser.py
```

使用现有 HTTP 夹具，不连接业务数据库。验证共享后代、迟到请求丢弃、画布外实体入口、路径保留、聚焦退出恢复、关闭名称后聚焦点线仍可见、manifest 数量不变、重启及模式隔离。1440、1280、1024、768、390 五档宽度的局部/全量画布同顶同高，工具按钮文字未裁切；画布高度分别为 400、280、280、400、294px。测试采集的浏览器错误列表为空。

另运行 `tests/service/graph-fullscreen.browser.py`，退出码 0：原生/回退全屏、跨模式保持、详情栏可见、退出/Escape 以及尺寸恢复均通过。六个本轮脚本通过 `node --check`；目标文件 `git diff --check` 无空白错误。

截图保存在被 Git 忽略的 `.benchmarks/graph-exploration/`：`local-exploration.png`、`local-exploration-expanded.png`、`full-exploration.png`。这些是隔离夹具的交互及布局证据，不是业务数据视觉质量验收。右侧来源内容未提供完整业务数据，截图中的来源读取提示不作为生产原文功能验证。

## 纯 CPU 性能对照

使用系统 Edge，关闭硬件 GPU、加速 Canvas2D；CDP 实际报告 SwiftShader Device (Subzero)，`2d_canvas` 和 `gpu_compositing` 均为 `disabled_software`。固定合成数据 300,000 节点/600,000 关系、1000×500 画布、DPR 1。before 为本轮实施前工作区快照，而不是含其他差异的 Git HEAD。

首次原始短测 `cpu-performance.json`：改前/改后第一帧等待分别为 4006.7/3727.7ms，触发 500ms 单帧停止保护，各仅 1 样本，不能据此统计 P95 或声称流畅。聚焦工具耗时 5425.6ms 也混入了该首次合成等待。

随后在同一脚本副本中单独记录首次合成等待，并在两次 rAF 稳定后采样缓存平移，保留原始结果，未删除慢帧：

| 模式 | 首次合成等待（ms） | 缓存平移帧间隔 P95（ms，20样本） | JS 绘制 P95（ms） |
| --- | ---: | ---: | ---: |
| 改前，工具未使用 | 3894.8 | 18.0 | 0.3 |
| 改后，工具未使用 | 4075.3 | 18.3 | 0.4 |
| 改后，聚焦开启 | 4343.2 | 26.4 | 2.6 |

稳定后首次邻接准备加聚焦共 1360.7ms，223 次主动让出事件循环；准备期间 rAF 间隔 P95 16.7ms、最大 16.8ms。聚焦绘制 200 节点/396 关系。原始结果见 `cpu-performance-warm.json`，脚本为 `cpu-compare-warm.py`。每档仅一次，20 样本 P95 实际等于该档最大值；这是短测，不能排除长时间或复杂真实数据的尖峰。

另有 Node 索引实验：完整 typed arrays 额外约 18MiB（不含既有记录/ID Map/浏览器内存）；热邻居 1,000 次约 9.8–12.6ms，热聚焦 1,000 次约 26.7–36.2ms；追加 500 节点/500 边约 5–13ms。Node 异步索引实验 224 段、最长段 9.8ms，但使用 `setImmediate`，不可当成生产浏览器 `setTimeout(0)` 的实测等待。

## 结论与剩余边界

在本次相同输入短测中，工具未使用时缓存平移 18.0→18.3ms；聚焦有界叠绘带来额外开销，仍未触发稳定交互单帧停止保护。首次索引分段后没有复现同步准备约 1 秒的主线程长任务。

原有全量首次软件合成约 4 秒的等待仍存在，不能承诺 30 万节点首屏无卡顿。默认小图/渐进批次加载继续保留；本轮结果不覆盖真实数据库、网络、30 万完整加载、复杂关系拾取、深度放大、长时间运行或类型切换已知尖峰。未宣称生产 30 万全流程验收完成。

8100 业务服务本轮未启动；用户浏览器中 8193 为 Semantica 参考页，不能用于验证本轮业务应用变更。验收依靠隔离 HTTP 浏览器回归和实际软件渲染对照。

## 合并 master 验证

- 从 `fbc7c15` 建立独立工作树，整理为图谱提交 `0617aac`（30 文件），排除原混合工作区中的资料解析、时间筛选、脑图与检索超时改动。原 checkout 保留；只同步本轮资源版本断言、像素检查与 CSS 末尾空行修正。
- 全部前端契约在隔离图谱版本通过 776/776。首次运行发现 `fixed-retrieval-scope` 缓存版本断言过期，已同步实际版本；未修改范围语义断言。
- 软件浏览器的探索、全屏、五档视口和 DPR 1/1.25/2 像素检查全部通过。像素检查初次仍采样旧尺寸的白色外圈，在新白底下误报；改为实际节点尺寸的完整着色区域与其中心合成色比较，保持至少 8 个过渡像素的断言。三档节点过渡像素分别为 8/11/18，选中环 38/49/76，关系线 482/604/956，GL 错误均为 0。
- 独立合并审查未发现 Critical/Important/Minor 问题，并独立复跑新模型/控制器/全量探索的 58 项测试通过。
- 准备合并时 master 已更新至 `e21fe52`（本体属性编辑）。唯一冲突位于 `index.html` 长资源行；基于最新 master 保留全部 93 个非图谱资源版本，再插入图谱资源，没有覆盖本体属性编辑。
- 合并结果运行 `node --test --test-concurrency=4 tests/service/*.test.cjs`：793/793 通过，0 失败、0 跳过。合并后的图谱探索浏览器验证再次通过，页面错误列表为空；本体属性编辑浏览器的 1280/390 宽度创建、数据类型、说明、归属、继承、重试与只读检查通过。测试使用隔离 HTTP 夹具，无需业务数据库；未将本轮验证描述为完整后端 pytest 或生产容量验收。
- 本轮仅合并本地 master，不推送远程。软件渲染性能结论与首次显示约 4 秒的限制继续保留。
