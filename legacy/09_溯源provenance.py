"""
09 - 溯源追踪 (Provenance) 模块
====================================
Semantica 的 provenance 模块实现 W3C PROV-O 标准溯源，
让知识图谱中的每一个事实都能追溯到来源文档。

位置：KG 构建之后（对图谱中的实体/关系做溯源标注）

为什么需要溯源？
  官网原话：'No provenance — outputs can't be traced to source facts'
  在医疗/金融/法律场景，这是硬性合规要求：
  - 这个「市值100亿」的说法来自哪份文档？
  - 这个「药物相互作用」的结论依据是什么？
  - 审计时如何证明 AI 不是瞎编的？

PROV-O 核心三元组：
  Entity      实体（数据对象）
  Activity    活动（处理过程）
  Agent       代理（执行者）
  wasDerivedFrom  某实体来源于另一实体
  wasAttributedTo 某实体归属于某代理
  wasGeneratedBy  某实体由某活动生成

运行: python 09_溯源provenance.py
"""

from pathlib import Path
from semantica.provenance import (
    ProvenanceManager, InMemoryStorage, SQLiteStorage,
)


def print_header(title, char="═"):
    print(f"\n{char * 70}")
    print(f"  {title}")
    print(f"{char * 70}")


def section(title):
    print(f"\n┌─ {title} " + "─" * (62 - len(title)))


# ============================================================
# 1. 实体溯源
# ============================================================
def demo_entity_provenance():
    section("1. 实体溯源（track_entity）")

    mgr = ProvenanceManager(storage=InMemoryStorage())

    print("│")
    print("│ 场景：从 3 篇论文中提取到「苹果公司」这个实体")
    print("│")

    sources = [
        ("apple_inc", "DOI:10.1038/s41586-023-06000", 0.92, "论文A"),
        ("apple_inc", "DOI:10.1109/TKDE.2023.1000", 0.88, "论文B"),
        ("apple_inc", "arxiv:2305.12345", 0.95, "论文C"),
    ]

    for entity_id, source, conf, ctx in sources:
        entry = mgr.track_entity(entity_id, source, metadata={"confidence": conf, "context": ctx})
        print(f"│   记录来源: {source}  (置信度 {conf})")

    print(f"│")
    print(f"│ 查询实体 apple_inc 的所有来源:")
    all_sources = mgr.get_all_sources("apple_inc")
    if all_sources:
        for s in all_sources:
            print(f"│   {s}")
    else:
        print(f"│   (无来源记录)")

    return mgr


# ============================================================
# 2. 血缘追踪（派生关系）
# ============================================================
def demo_lineage():
    section("2. 血缘追踪（派生链）")

    mgr = ProvenanceManager(storage=InMemoryStorage())

    print("│")
    print("│ 场景：追踪「结论」如何从「原始数据」一步步推导出来")
    print("│")
    print("│ 派生链: 原始文档 → 抽取实体 → 知识图谱 → 推理结论")
    print("│")

    mgr.track_entity("raw_doc_1", "file://reports/annual_report.pdf",
                     metadata={"type": "document"})
    mgr.track_entity("entity_bytedance", "file://reports/annual_report.pdf",
                     metadata={"type": "entity", "derived_from": "raw_doc_1"})
    mgr.track_entity("fact_market_cap", "file://reports/annual_report.pdf",
                     metadata={"type": "fact", "derived_from": "entity_bytedance"})
    mgr.track_entity("conclusion_leader", "file://reports/annual_report.pdf",
                     metadata={"type": "conclusion", "derived_from": "fact_market_cap"})

    print(f"│ 查询 conclusion_leader 的血缘:")
    lineage = mgr.get_lineage("conclusion_leader")
    if lineage and isinstance(lineage, dict):
        for k, v in lineage.items():
            val_str = str(v)[:70]
            print(f"│   {k}: {val_str}")
    else:
        print(f"│   {lineage}")

    return mgr


# ============================================================
# 3. SQLite 持久化存储
# ============================================================
def demo_sqlite_storage():
    section("3. SQLite 持久化存储（SQLiteStorage）")

    db_path = Path(__file__).parent / "provenance.db"
    mgr = ProvenanceManager(storage=SQLiteStorage(str(db_path)))

    print("│")
    print(f"│ 存储位置: {db_path}")
    print(f"│")

    entities = [
        ("entity_1", "DOI:10.1000/xyz", 0.91),
        ("entity_2", "DOI:10.1000/abc", 0.85),
        ("entity_3", "arxiv:2301.00001", 0.79),
    ]
    for eid, src, conf in entities:
        mgr.track_entity(eid, src, metadata={"confidence": conf})

    stats = mgr.get_statistics()
    print(f"│ 溯源统计:")
    if isinstance(stats, dict):
        for k, v in stats.items():
            print(f"│   {k}: {v}")
    else:
        print(f"│   {stats}")

    print(f"│")
    print(f"│ 导出 W3C PROV-O Turtle 格式:")
    try:
        prov_rdf = mgr.export_prov(format="turtle")
        print(f"│ {'─'*60}")
        for line in prov_rdf.strip().split("\n")[:12]:
            print(f"│   {line}")
        if len(prov_rdf.strip().split("\n")) > 12:
            print(f"│   ...")
    except Exception as e:
        print(f"│   PROV-O 导出失败: {e}")

    return mgr


# ============================================================
# 4. 溯源 + 知识图谱整合
# ============================================================
def demo_provenance_with_kg():
    section("4. 溯源与知识图谱整合（完整链路）")

    print("│")
    print("│ 完整链路: 文档 → 实体 → 事实 → 溯源记录")
    print("│")

    mgr = ProvenanceManager(storage=InMemoryStorage())

    print("│ 1. 摄取文档:")
    mgr.track_entity("doc_1", "file://contracts/2024合同.pdf",
                     metadata={"type": "document", "checksum": "sha256:abc123"})
    print("│    ✅ 文档 doc_1 已溯源")
    print("│")
    print("│ 2. 抽取实体:")
    entities = [
        ("entity_甲方", "file://contracts/2024合同.pdf", "PARTY", 0.93),
        ("entity_乙方", "file://contracts/2024合同.pdf", "PARTY", 0.91),
        ("fact_金额", "file://contracts/2024合同.pdf", "AMOUNT", 0.96),
    ]
    for eid, src, etype, conf in entities:
        mgr.track_entity(eid, src, metadata={"type": etype, "confidence": conf})

    print("│    ✅ 3个实体已溯源（都指向合同PDF）")
    print("│")
    print("│ 3. 查询 fact_金额 的来源:")
    fact_sources = mgr.get_all_sources("fact_金额")
    for s in fact_sources:
        print(f"│      {s}")
    print("│")
    print("│ 4. 审计问答:")
    print("│    问：「合同金额500万」这个事实从哪来？")
    print("│    答：来自 file://contracts/2024合同.pdf")
    print("│        类型=AMOUNT, 置信度=0.96, 校验和=sha256:abc123")
    print("│")
    print("│ ✅ 这就是可问责 AI 的完整闭环")


# ============================================================
# 主函数
# ============================================================
def main():
    print_header("溯源篇：Provenance 模块完整演示")
    print("""
溯源 (Provenance) 是 Semantica「可问责 AI」的三大支柱之一
（另外两个是 Context Graphs 和 Decision Intelligence）。

核心价值：
  每一个事实 → 都能回答「从哪来、怎么来的、可信度多少」
""")

    demo_entity_provenance()
    demo_lineage()
    demo_sqlite_storage()
    demo_provenance_with_kg()

    print_header("溯源篇总结", char="═")
    print("""
📌 溯源的核心能力：
  1. track_entity / track_relationship — 记录实体/关系的来源
  2. get_lineage — 回溯完整的派生链（结论→事实→实体→文档）
  3. get_all_sources — 查看一个事实的所有来源
  4. export_prov — 导出 W3C PROV-O 标准格式（审计用）
  5. SQLiteStorage — 持久化存储，跨会话保留

📌 合规场景对照：
  ┌──────────────┬──────────────────────────────────────┐
  │ 法规          │ 溯源要求                              │
  ├──────────────┼──────────────────────────────────────┤
  │ HIPAA         │ 医疗决策必须能追溯到病历来源          │
  │ SOX           │ 财务数据必须能追溯到原始凭证          │
  │ GDPR          │ 数据主体必须能查到数据来源和用途      │
  │ FDA 21 CFR    │ 审批决策必须有完整审计链              │
  └──────────────┴──────────────────────────────────────┘

📌 与普通 RAG 的本质区别：
  普通 RAG: 检索到片段 → 生成答案（无溯源，无法审计）
  Semantica: 检索到片段 → 标注来源 → 生成答案 → 记录派生链（可审计）
""")


if __name__ == "__main__":
    main()
