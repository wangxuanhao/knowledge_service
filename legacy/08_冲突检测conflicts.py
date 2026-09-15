"""
08 - 冲突检测 (Conflicts) 模块
====================================
Semantica 的 conflicts 模块在知识抽取管线中位于 Extract 之后、
Deduplication 之前，负责检测多源数据之间的矛盾。

位置：Sources → Parse → Normalize → Split → Extract → 【Conflicts】
                                                        ↓
      → Deduplication → KG → Ontology/Reasoning/Provenance

为什么需要冲突检测？
  不同来源对同一实体的同一属性可能有不同说法：
  - 来源A说"某公司市值100亿"，来源B说"120亿"
  - 来源A说"某人是CEO"，来源B说"已离职"
  - 这些矛盾如果不检测，会污染知识图谱，导致推理出错

冲突类型：
  1. VALUE_CONFLICT       属性值冲突（同一属性值不同）
  2. TYPE_CONFLICT        类型冲突（同一实体被标为不同类型）
  3. RELATIONSHIP_CONFLICT 关系冲突
  4. TEMPORAL_CONFLICT    时间冲突
  5. LOGICAL_CONFLICT     逻辑冲突

冲突解析策略（ResolutionStrategy）：
  - voting              投票（多数值胜出）
  - credibility_weighted 可信度加权
  - most_recent         取最新
  - first_seen          取首次出现
  - highest_confidence  取最高置信度
  - manual_review       人工审核
  - expert_review       专家审核

运行: python 08_冲突检测conflicts.py
"""

from semantica.conflicts import (
    ConflictDetector, ConflictResolver, Conflict, ConflictType,
    ResolutionStrategy,
)


def print_header(title, char="═"):
    print(f"\n{char * 70}")
    print(f"  {title}")
    print(f"{char * 70}")


def section(title):
    print(f"\n┌─ {title} " + "─" * (62 - len(title)))


# ============================================================
# 1. 属性值冲突检测
# ============================================================
def demo_value_conflicts():
    section("1. 属性值冲突检测（Value Conflict）")

    # 模拟：3个来源对"字节跳动"的"市值"给出了不同说法
    flat_entities = [
        {"id": "bytedance", "market_cap": 100, "source": "财报2023", "confidence": 0.9},
        {"id": "bytedance", "market_cap": 120, "source": "新闻2024", "confidence": 0.85},
        {"id": "bytedance", "market_cap": 100, "source": "第三方机构", "confidence": 0.8},
    ]

    detector = ConflictDetector()
    conflicts = detector.detect_value_conflicts(flat_entities, "market_cap")

    print(f"│")
    print(f"│ 输入: 3个来源对 bytedance 的 market_cap 给出 3 条记录")
    print(f"│")
    print(f"│ 检测到 {len(conflicts)} 个冲突:")
    for c in conflicts:
        print(f"│   • 实体: {c.entity_id}")
        print(f"│     属性: {c.property_name}")
        print(f"│     冲突类型: {c.conflict_type}")
        print(f"│     冲突值: {c.conflicting_values}")
        print(f"│     严重度: {getattr(c, 'severity', 'N/A')}")
        print(f"│")

    return conflicts


# ============================================================
# 2. 类型冲突检测
# ============================================================
def demo_type_conflicts():
    section("2. 类型冲突检测（Type Conflict）")

    type_entities = [
        {"id": "e2", "type": "Person", "source": "来源A"},
        {"id": "e2", "type": "Organization", "source": "来源B"},
        {"id": "e3", "type": "Company", "source": "来源A"},
        {"id": "e3", "type": "Company", "source": "来源B"},  # 无冲突
    ]

    detector = ConflictDetector()
    conflicts = detector.detect_type_conflicts(type_entities)

    print(f"│")
    print(f"│ 检测到 {len(conflicts)} 个类型冲突:")
    for c in conflicts:
        print(f"│   • 实体 {c.entity_id} 被标为不同类型: {c.conflicting_values}")
        print(f"│     来源: {[s for s in c.sources] if hasattr(c, 'sources') else 'N/A'}")
        print(f"│")

    return conflicts


# ============================================================
# 3. 冲突解析（多种策略对比）
# ============================================================
def demo_conflict_resolution(value_conflicts):
    section("3. 冲突解析（多策略对比）")

    if not value_conflicts:
        print("│ 无冲突可解析")
        return

    resolver = ConflictResolver()

    # 构造一个更典型的冲突（带时间戳，演示 most_recent 策略）
    conflict = Conflict(
        conflict_id="c1",
        conflict_type=ConflictType.VALUE_CONFLICT,
        entity_id="bytedance",
        property_name="market_cap",
        conflicting_values=[100, 120],
        sources=[
            {"document": "财报2023", "confidence": 0.9, "metadata": {"timestamp": "2023-12-31"}},
            {"document": "新闻2024", "confidence": 0.85, "metadata": {"timestamp": "2024-06-01"}},
        ],
    )

    print(f"│")
    print(f"│ 冲突: bytedance 的 market_cap 有 [100, 120] 两个值")
    print(f"│")
    print(f"│ 各策略解析结果:")
    print(f"│ {'─'*62}")
    print(f"│ {'策略':<28} {'解析结果':<20} {'说明'}")
    print(f"│ {'─'*62}")

    strategies = [
        ("voting", "多数投票"),
        ("highest_confidence", "最高置信度"),
        ("most_recent", "取最新值"),
        ("first_seen", "取首次出现"),
        ("credibility_weighted", "可信度加权"),
    ]

    for strategy_name, desc in strategies:
        try:
            result = resolver.resolve_conflict(conflict, strategy=strategy_name)
            # 提取解析值
            if hasattr(result, 'resolved_value'):
                val = result.resolved_value
            elif hasattr(result, 'resolution'):
                val = result.resolution
            else:
                val = str(result)[:30]
            print(f"│ {strategy_name:<28} {str(val):<20} {desc}")
        except Exception as e:
            print(f"│ {strategy_name:<28} {'ERROR':<20} {e}")

    print(f"│ {'─'*62}")

    return conflict


# ============================================================
# 4. 冲突报告
# ============================================================
def demo_conflict_report():
    section("4. 冲突报告（Conflict Report）")

    # 一次性检测多个属性的冲突
    entities = [
        # 市值冲突
        {"id": "e1", "market_cap": 100, "revenue": 10, "source": "s1", "confidence": 0.9},
        {"id": "e1", "market_cap": 120, "revenue": 10, "source": "s2", "confidence": 0.8},
        # 营收冲突
        {"id": "e2", "market_cap": 50, "revenue": 20, "source": "s1", "confidence": 0.9},
        {"id": "e2", "market_cap": 50, "revenue": 25, "source": "s3", "confidence": 0.7},
    ]

    detector = ConflictDetector()
    # 检测所有实体的所有属性冲突
    all_conflicts = detector.detect_entity_conflicts(entities)

    print(f"│")
    print(f"│ 检测到 {len(all_conflicts)} 个冲突")
    print(f"│")

    # 生成报告
    report = detector.get_conflict_report()
    if isinstance(report, dict):
        for k, v in report.items():
            val_str = str(v)[:50]
            print(f"│   {k}: {val_str}")
    else:
        print(f"│   报告: {report}")

    return all_conflicts


# ============================================================
# 5. 现实场景：合同条款冲突
# ============================================================
def demo_real_world_conflict():
    section("5. 现实场景：合同金额冲突（多源比对）")

    print("│")
    print("│ 场景：从3份文件中提取同一份合同的金额，发现不一致")
    print("│")

    contract_records = [
        {"id": "contract_2024_001", "amount": 500000, "source": "合同扫描件", "confidence": 0.95},
        {"id": "contract_2024_001", "amount": 500000, "source": "财务系统", "confidence": 0.98},
        {"id": "contract_2024_001", "amount": 550000, "source": "业务邮件", "confidence": 0.6},
    ]

    detector = ConflictDetector()
    conflicts = detector.detect_value_conflicts(contract_records, "amount")

    print(f"│ 3个来源: 合同扫描件(50万) / 财务系统(50万) / 业务邮件(55万)")
    print(f"│")

    if conflicts:
        c = conflicts[0]
        print(f"│ 检测到冲突:")
        print(f"│   冲突值: {c.conflicting_values}")
        print(f"│")
        print(f"│ 解析建议:")
        resolver = ConflictResolver()
        # 投票：50万出现2次，胜出
        voting_result = resolver.resolve_conflict(c, strategy="voting")
        val = getattr(voting_result, 'resolved_value', voting_result)
        print(f"│   投票结果: {val} (50万出现2次)")
        print(f"│")
        # 可信度加权：财务系统(0.98)权重最高
        print(f"│   可信度加权: 财务系统置信度0.98最高，应采信50万")
        print(f"│")
        print(f"│ ✅ 结论: 合同金额应为 50万，业务邮件的55万是笔误")
    else:
        print(f"│ 未检测到冲突（可能格式问题）")


# ============================================================
# 主函数
# ============================================================
def main():
    print_header("冲突检测篇：Conflicts 模块完整演示")
    print("""
冲突检测是 Semantica "可问责 AI" 的核心能力之一。
官网把它列为五个结构性盲点之一：
  "No conflict detection — contradictory facts silently coexist in vector stores"

位置：Extract 之后、Deduplication 之前
""")

    # 1. 属性值冲突
    value_conflicts = demo_value_conflicts()

    # 2. 类型冲突
    demo_type_conflicts()

    # 3. 冲突解析
    demo_conflict_resolution(value_conflicts)

    # 4. 冲突报告
    demo_conflict_report()

    # 5. 现实场景
    demo_real_world_conflict()

    # 总结
    print_header("冲突检测篇总结", char="═")
    print("""
📌 冲突检测的价值：
  1. 数据质量保障：矛盾数据在入库前就被拦截
  2. 合规要求：金融/医疗场景必须能证明"数据一致性被检查过"
  3. 避免推理污染：矛盾事实会导致下游推理得出错误结论

📌 解析策略选择指南：
  ┌─────────────────────┬──────────────────────────────────────┐
  │ 策略                │ 适用场景                              │
  ├─────────────────────┼──────────────────────────────────────┤
  │ voting              │ 来源数量多，多数可信                  │
  │ credibility_weighted│ 来源权威性差异大（官方>第三方）       │
  │ most_recent         │ 时间敏感数据（股价/汇率/人事）        │
  │ highest_confidence  │ 抽取置信度差异大                     │
  │ manual_review       │ 高风险决策（合同金额/医疗诊断）       │
  └─────────────────────┴──────────────────────────────────────┘

📌 关键设计：冲突不是自动抹平，而是"标记+可追溯"
  即使自动解析了，原始冲突记录也保留，随时可回溯。
""")


if __name__ == "__main__":
    main()
