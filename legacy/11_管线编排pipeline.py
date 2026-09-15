"""
11 - 管线编排 (Pipeline) 模块
====================================
Semantica 的 pipeline 模块提供 PipelineBuilder DSL，
把前面的所有模块（ingest→parse→normalize→split→extract→...）
串成一个可执行、可验证、可重试的管线。

官网说明：
  "Pipeline DSL, parallel workers, retry policies, failure handling"

核心组件：
  PipelineBuilder  管线构建器（DSL）
  PipelineStep     管线步骤
  Pipeline         管线定义
  ExecutionEngine  执行引擎
  PipelineValidator 管线校验（启动时检查配置错误）
  FailureHandler   失败处理（指数退避 + 死信队列）
  ParallelExecutor 并行执行器

运行: python 11_管线编排pipeline.py
"""

from semantica.pipeline import (
    PipelineBuilder, ExecutionEngine, PipelineValidator,
)


def print_header(title, char="═"):
    print(f"\n{char * 70}")
    print(f"  {title}")
    print(f"{char * 70}")


def section(title):
    print(f"\n┌─ {title} " + "─" * (62 - len(title)))


# ============================================================
# 定义管线的步骤 handler（真实可执行的函数）
# 注意：handler 接收的第一个参数是累积的 data 字典
#       返回 dict 会被 merge 回 data
# ============================================================
def handler_normalize(data: dict, **kwargs) -> dict:
    """步骤1: 归一化"""
    from semantica.normalize import normalize_text
    text = data.get("text", "") if isinstance(data, dict) else str(data)
    return {"text": normalize_text(text)}


def handler_split(data: dict, **kwargs) -> dict:
    """步骤2: 分块"""
    chunk_size = kwargs.get("chunk_size", 300)
    text = data.get("text", "") if isinstance(data, dict) else str(data)
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    return {"chunks": chunks, "chunk_count": len(chunks)}


def handler_extract_entities(data: dict, **kwargs) -> dict:
    """步骤3: 实体抽取"""
    from semantica.semantic_extract import NERExtractor
    ner = NERExtractor(method="pattern")
    chunks = data.get("chunks", []) if isinstance(data, dict) else []
    all_entities = []
    for chunk in chunks:
        try:
            entities = ner.extract(chunk)
            all_entities.extend(entities)
        except Exception:
            pass
    return {"entities": all_entities, "entity_count": len(all_entities)}


def handler_build_graph(data: dict, **kwargs) -> dict:
    """步骤4: 构建知识图谱"""
    entities = data.get("entities", []) if isinstance(data, dict) else []
    return {"graph": {"entities": entities, "edges": []},
            "node_count": len(entities)}


# ============================================================
# 1. 用 PipelineBuilder 构建管线
# ============================================================
def demo_pipeline_builder():
    section("1. 用 PipelineBuilder 构建管线（DSL）")

    builder = PipelineBuilder()

    # 注册步骤 handler
    builder.register_step_handler("normalize", handler_normalize)
    builder.register_step_handler("split", handler_split)
    builder.register_step_handler("extract_entities", handler_extract_entities)
    builder.register_step_handler("build_graph", handler_build_graph)

    # 添加步骤（声明式定义管线）
    step1 = builder.add_step("normalize_text", "normalize")
    step2 = builder.add_step("split_text", "split", chunk_size=300)
    step3 = builder.add_step("extract", "extract_entities")
    step4 = builder.add_step("build_graph", "build_graph")

    # 声明依赖（DAG 拓扑顺序）
    step2.dependencies = ["normalize_text"]
    step3.dependencies = ["split_text"]
    step4.dependencies = ["extract"]

    print("│")
    print("│ 管线步骤（DAG）:")
    print("│   normalize_text ──→ split_text ──→ extract ──→ build_graph")
    print("│")

    # 校验管线
    print("│ 管线校验:")
    try:
        validator = PipelineValidator()
        valid = validator.validate(builder)
        print(f"│   ✅ 校验通过" if valid else f"│   ❌ 校验失败")
    except Exception as e:
        print(f"│   PipelineValidator 调用方式不同: {e}")

    # 构建管线
    pipeline = builder.build(name="知识抽取管线")
    print(f"│")
    print(f"│ 管线已构建: {pipeline}")

    return builder, pipeline


# ============================================================
# 2. 执行管线
# ============================================================
def demo_pipeline_execution(builder, pipeline):
    section("2. 执行管线（ExecutionEngine）")

    test_text = """
Apple Inc. was founded by Steve Jobs in 1976 in Cupertino, California.
Tim Cook became the CEO of Apple in 2011.
Google, headquartered in Mountain View, develops the Android operating system.
Microsoft invested in OpenAI in 2023.
"""

    print("│")
    print(f"│ 输入文本: {len(test_text)} 字符")
    print("│")

    engine = ExecutionEngine()

    try:
        result = engine.execute_pipeline(pipeline, data={"text": test_text})
        print(f"│ 执行结果:")
        print(f"│   success: {getattr(result, 'success', result)}")
        if hasattr(result, 'output'):
            print(f"│   output: {result.output}")
        if hasattr(result, 'metrics'):
            print(f"│   metrics: {result.metrics}")
        if hasattr(result, 'errors') and result.errors:
            print(f"│   errors: {result.errors}")
    except Exception as e:
        print(f"│ ExecutionEngine 调用方式不同: {type(e).__name__}: {e}")
        print(f"│")
        print(f"│ 改用手动顺序执行演示:")

        # 手动模拟管线执行
        print(f"│")
        print(f"│ 步骤1 normalize → 步骤2 split → 步骤3 extract → 步骤4 build_graph")
        print(f"│")
        data = {"text": test_text}
        data.update(handler_normalize(data["text"]))
        print(f"│   ① normalize 完成: 文本 {len(data['text'])} 字符")
        data.update(handler_split(data["text"]))
        print(f"│   ② split 完成: {data['count']} 块")
        data.update(handler_extract_entities(data["chunks"]))
        print(f"│   ③ extract 完成: {data['count']} 个实体")
        data.update(handler_build_graph(data["entities"]))
        print(f"│   ④ build_graph 完成: {data['node_count']} 个节点")
        print(f"│")
        print(f"│ ✅ 管线执行成功（手动串联）")


# ============================================================
# 3. 完整 8 步知识抽取管线
# ============================================================
def demo_full_pipeline():
    section("3. 完整知识抽取管线（8 步全景）")

    print("│")
    print("│ 这是 Semantica 官方架构的完整管线（ARCHITECTURE.md）：")
    print("│")
    steps = [
        ("1. Ingest",    "摄取源数据（文件/网页/数据库）", "semantica.ingest"),
        ("2. Parse",     "解析文档结构（PDF/DOCX/HTML）", "semantica.parse"),
        ("3. Normalize", "归一化（文本/实体/日期/数字）", "semantica.normalize"),
        ("4. Split",     "分块（实体感知/语义边界）",    "semantica.split"),
        ("5. Extract",   "抽取（NER/关系/事件/三元组）", "semantica.semantic_extract"),
        ("6. Conflicts", "冲突检测（多源矛盾）",         "semantica.conflicts"),
        ("7. Dedup",     "实体消歧去重",                 "semantica.deduplication"),
        ("8. Build KG",  "构建知识图谱",                 "semantica.kg"),
    ]

    for name, desc, module in steps:
        print(f"│   {name:<12} {desc:<28} {module}")

    print("│")
    print("│ 之后还有: Ontology / Reasoning / Provenance / Context（智能层）")
    print("│           Vector Store / Graph Store（存储层）")
    print("│           Export / Visualize / Services（输出层）")
    print("│")
    print("│ 💡 PipelineBuilder 可以把这 8 步串成一个 DAG，")
    print("│    支持并行执行、失败重试、断点续跑。")


# ============================================================
# 主函数
# ============================================================
def main():
    print_header("管线编排篇：Pipeline 模块完整演示")
    print("""
PipelineBuilder 是 Semantica 的"管线 DSL"，
把零散的能力模块编排成可执行、可验证、可重试的生产级管线。
""")

    builder, pipeline = demo_pipeline_builder()
    demo_pipeline_execution(builder, pipeline)
    demo_full_pipeline()

    print_header("管线编排篇总结", char="═")
    print("""
📌 PipelineBuilder 核心能力：
  1. 声明式定义管线（add_step + dependencies）
  2. 步骤 handler 注册（register_step_handler）
  3. DAG 拓扑排序（自动处理依赖顺序）
  4. 并行执行（set_parallelism）
  5. 失败处理（FailureHandler + 指数退避 + 死信队列）
  6. 启动校验（PipelineValidator 捕获配置错误）

📌 完整 8 步管线（官方 ARCHITECTURE.md）：
  Sources → Parse → Normalize → Split → Extract
  → Conflicts → Dedup → Build KG
  → Ontology / Reasoning / Provenance / Context（智能层）
  → Vector Store / Graph Store（存储层）
  → Export / Visualize / Services（输出层）

📌 与手写管线的区别：
  手写: 顺序调用，出错难恢复，难并行，难复用
  PipelineBuilder: DAG 定义，自动排序，失败重试，断点续跑
""")


if __name__ == "__main__":
    main()
