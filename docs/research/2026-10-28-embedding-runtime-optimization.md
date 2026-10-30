# 本地 embedding 推理依赖拆分与真实验收

日期：2026-10-28。使用既有 Windows/Python 3.12 `llm_model`，没有新建环境、安装、卸载或升级共享包。此记录更新了[前轮依赖审计](2026-10-28-dependency-audit.md)中“模型依赖导入仍需数十秒”的状态。

## 已落地的边界

问题来自完整模型库的导入链。具体 HF 模型类仍需约 47.95 秒导入，较旧的 Transformers 4.44.2 组合仍需约 41.49 秒。继续修改 import 语句或仅换版本没有解决主要开销。

现在将准备与推理分开：

- 离线准备：使用原 SentenceTransformers/PyTorch，读取本地 BGE-M3 原始权重，导出 FP32 ONNX；与原模型对照分词和向量，验证后原子启用。
- 在线推理：只加载 NumPy、ONNX Runtime、Tokenizers 和经过验证的制品，不导入 Torch、Transformers、SentenceTransformers。原始权重保留，embedding identity 不变。
- 通用兼容：无制品的其他本地模型仍可使用原路径；强制 `onnx` 时缺制品明确失败。存在无效制品时不会静默加载旧路径，避免掩盖源模型变化。

`pyproject.toml` 已增加 `local-onnx` 与 `local-export` extras，`uv.lock` 已同步声明并通过离线一致性检查。锁定的解析版本仍不是现有共享环境的安装快照；这次实测为 ORT 1.20.1、Tokenizers 0.21.4、NumPy 2.2.6，准备使用 ST 5.2.3、Transformers 4.55.0、Torch 2.8.0、ONNX 1.18.0。

## 模型与索引兼容门禁

只支持已确认的 XLMRobertaModel → CLS Pooling → Normalize 流水线。本轮没有换模型、量化、FP16 或变更向量维度，也没有重建业务索引。

正式准备验证了 10 条样本，包含“未成年”“美团”“直播限制时段是什么时间”、空串、纯空白、前后空白、中英混排、emoji、重复文本和较长文本。对照单条、双条、三条批次，并检查原始顺序。

| 验证项 | 结果 | 门禁 |
| --- | ---: | ---: |
| 输出维度 | 1024 | 与原模型/索引一致 |
| 最大逐元素绝对误差 | 3.9488 × 10⁻⁷ | ≤ 1 × 10⁻⁴ |
| 最小余弦相似度（float64 统计） | 0.999999999996 | ≥ 0.99999 |
| 最大单位范数误差 | 4.0285 × 10⁻⁷ | ≤ 1 × 10⁻⁵ |
| token IDs / attention mask | 逐项一致 | 必须一致 |
| 分词截断检查 | 最大 8192 tokens | 保留原上限和特殊 token |

实际向量对照样本最长为 267 tokens。8192 边界验证的是分词截断，不代表已经验收全长推理的内存或吞吐。运行时对超过 1024 tokens 的输入每批只处理一条，保持原截断上限；长文档容量仍需专项验证。

## 制品完整性与失败行为

制品在原模型下的 `onnx-runtime/<不可变版本>/` 中，约增加 2.27 GB。`active.json` 绑定契约版本、FP32、CPU provider、opset 17、维度、分词配置、验证指标、源模型及全部制品 SHA256。大模型包含 external tensor data，全部纳入清单；路径必须处于指定目录内。

初始化只校验一次：源权重或已有配置内容变动、额外出现替代权重/adapter/added tokens，或可选配置从无变有，均拒绝旧导出。health 与正常查询不重新读盘或哈希。

新版本先在 staging 中完整导出和验证，再保留为不可变版本，最后以 `os.replace` 原子切换活动清单。验证或切换失败保留旧 active；成功也保留旧版本。原权重不删除，不修改 `.env` 或业务数据。

## 新进程与真实服务计时

独立新进程通过 import blocker 禁止三个重型框架导入，仍正常产出 1024 维单位向量：

| 阶段 | 实测 |
| --- | ---: |
| 推理依赖导入 | 0.701 秒 |
| 完整哈希验证与 ORT/session/tokenizer 加载 | 18.557 秒 |
| 从进程内开始导入门面到首条向量 | 20.505 秒 |

真实服务使用原启动目录和私有环境重启，进程 49320，确认选用 `OnnxSentenceEncoder`：

| 模型初始化阶段 | 原 ST 路径（前轮） | 新在线路径 |
| --- | ---: | ---: |
| 依赖导入 | 55.70 秒 | 1.94 秒 |
| 校验/权重加载 | 6.02 秒（仅原加载） | 20.87 秒（含源及制品全量校验） |
| 模型预热合计 | 62.38 秒 | 22.94 秒 |

新的全量校验增加了加载阶段的读盘成本；整体模型预热仍缩短约 63%，依赖导入缩短约 97%。这是不同进程的单次真实观测，不作为稳定 P95。API 装配等其他启动阶段另计，本次从验收探针开始到模型 ready 为 38.984 秒，不将依赖耗时冒充完整服务启动时间。

## 真实请求复测

项目 `test_1022_开放`，问题“未成年”，沿用既有 Milvus 索引：

- 初始化期间混合查询 0.109 秒返回 503，明确说明初始化；关键词查询 2.844 秒返回 200。
- 就绪后关键词、语义、混合各测扩展关闭/开启，共六次，全部 200，耗时 0.391–0.516 秒。
- 问答 SSE 2.937 秒完成，收到 done，无 error。由于来源有效期未维护，回答仍为“未找到在该时点确认有效的依据。”；这证明流正常完成，不代表规则内容质量已另行验收。

## 使用与恢复

只需在首次或源模型变更后准备一次：

```powershell
# 仅缺准备依赖的新环境需要安装；本次没有修改已有环境：
python -m pip install -e ".[local-export]"
python scripts/prepare_embedding_runtime.py --model-dir data/model/embedding/bge-m3
```

已有有效制品默认直接复用；显式 `--rebuild` 创建新版本。部署保留原模型和完整 `onnx-runtime` 目录，在线依赖可选 `local-onnx`，不需要为了推理安装准备工具所需的完整模型框架。

`KG_LOCAL_EMBEDDING_RUNTIME=auto` 是默认值；`onnx` 强制已准备制品；`sentence-transformers` 可显式恢复原运行时，重启后生效。制品过期/损坏时重新准备，通过验证后再启用。回到原运行时会重新承担原导入成本。

## 回归与复核

```powershell
python -m pytest -q tests/service/test_onnx_embedding_runtime.py tests/service/test_embedding_export_contract.py tests/service/test_embedding_runtime_selection.py tests/service/test_embedding_runtime_ready.py tests/service/test_runtime_capabilities.py tests/service/test_embedding_model_relocation.py tests/service/test_package_import_boundary.py
```

最终专项 51 项通过，耗时 52.48 秒；一条已知 Starlette/AnyIO 废弃提示。新用例均先观察失败再实现，覆盖真实微型 ONNX、external tensor、重型导入阻断、动态批次、源文件变动、坏制品拒绝、原子激活失败保留旧版本与回退选择。最终 `/readyz` 为 200，database/migrations/embedding/semantic 均 ready。

独立审查发现的源文件集合变化缺口已修复并再次复核，未剩余 P1/P2。新增三个生产模块后共 151 个模块，静态复核仍为 0 组顶层环、5 组已有延迟依赖环，没有新增循环；证据在 `.benchmarks/import-cycle-audit-onnx/results.json`。

这是专项验证，不是全量 Python 回归通过声明；前轮确认的 7 个旧 Milvus 基线失败没有在本轮改动。前端本轮未变，未重复此前 Node 回归。

证据：`.benchmarks/prepared-runtime-result.json`、`onnx-equivalence-probe.json`、`prepare-embedding-runtime.log`、`onnx-service.stderr.log`、`onnx-service-verification.json`。这些是本地忽略的验收产物，不进入代码提交。
