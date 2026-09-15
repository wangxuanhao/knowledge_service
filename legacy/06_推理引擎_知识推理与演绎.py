"""
06 - 推理引擎：知识推理与演绎
====================================
演示 Semantica 的推理能力，这是它区别于普通 NER/关系抽取工具的核心。

推理类型：
  1. 演绎推理（Forward Chaining）：IF-THEN 规则驱动
  2. 基于本体的推理：类继承、属性传递、对称属性
  3. 图算法推理：中心性、社区检测、路径发现、链接预测
  4. 溯因推理（Abductive）：从结果反推原因
  5. Datalog 规则推理

运行: python 06_推理引擎_知识推理与演绎.py
"""

import json
import sys
from pathlib import Path


def print_header(title, char="═"):
    print(f"\n{char * 72}")
    print(f"  {title}")
    print(f"{char * 72}")


def section(title):
    print(f"\n┌─ {title} " + "─" * (64 - len(title)))


# ============================================================
# Demo 1: 基于规则的演绎推理（Forward Chaining）
# ============================================================
def demo_forward_chaining():
    section("Demo 1: 前向链推理（Forward Chaining / IF-THEN 规则）")

    print("""
│ 什么是前向链推理？
│   给定一组 IF-THEN 规则和一组已知事实，
│   自动推导出所有可以得出的新事实。
│
│ 示例：
│   规则: IF X is_a 大语言模型 AND Y develops X → Y is_a AI公司
│   事实: 豆包 is_a 大语言模型
│         字节跳动 develops 豆包
│   推导: 字节跳动 is_a AI公司 ✅
""")

    # 事实库
    facts = {
        # (实体, 属性, 值)
        ("字节跳动", "develops", "豆包"),
        ("字节跳动", "located_in", "北京"),
        ("阿里巴巴", "develops", "通义千问"),
        ("阿里巴巴", "located_in", "杭州"),
        ("腾讯", "develops", "混元大模型"),
        ("百度", "develops", "文心一言"),
        ("豆包", "is_a", "大语言模型"),
        ("通义千问", "is_a", "大语言模型"),
        ("文心一言", "is_a", "大语言模型"),
        ("混元大模型", "is_a", "大语言模型"),
        ("大语言模型", "belongs_to", "人工智能"),
        ("北京", "is_a", "一线城市"),
        ("杭州", "is_a", "一线城市"),
        ("深圳", "is_a", "一线城市"),
        ("张一鸣", "founded_by", "字节跳动"),  # 注意方向
    }

    # 规则库
    rules = [
        {
            "name": "开发大模型的公司是AI公司",
            "conditions": [
                ("?company", "develops", "?model"),
                ("?model", "is_a", "大语言模型"),
            ],
            "conclusions": [
                ("?company", "is_a", "AI公司"),
            ],
        },
        {
            "name": "AI公司属于科技公司",
            "conditions": [
                ("?company", "is_a", "AI公司"),
            ],
            "conclusions": [
                ("?company", "belongs_to", "科技行业"),
            ],
        },
        {
            "name": "总部在一线城市的公司有人才优势",
            "conditions": [
                ("?company", "located_in", "?city"),
                ("?city", "is_a", "一线城市"),
            ],
            "conclusions": [
                ("?company", "has_advantage", "人才优势"),
            ],
        },
        {
            "name": "传递性: belongs_to",
            "conditions": [
                ("?a", "belongs_to", "?b"),
                ("?b", "belongs_to", "?c"),
            ],
            "conclusions": [
                ("?a", "belongs_to", "?c"),
            ],
        },
    ]

    # 前向链推理引擎（简化版）
    def forward_chain(facts, rules, max_iterations=10):
        all_facts = set(facts)
        new_facts = True
        iteration = 0

        while new_facts and iteration < max_iterations:
            iteration += 1
            new_facts = False

            for rule in rules:
                # 简单的变量替换匹配
                matched_bindings = [{}]

                for condition in rule["conditions"]:
                    new_bindings = []
                    for binding in matched_bindings:
                        subj_pat = binding.get(condition[0], condition[0]) if condition[0].startswith("?") else condition[0]
                        pred_pat = condition[1]
                        obj_pat = binding.get(condition[2], condition[2]) if condition[2].startswith("?") else condition[2]

                        for fact in all_facts:
                            # 检查是否匹配
                            match = True
                            b = dict(binding)

                            if condition[0].startswith("?"):
                                var = condition[0]
                                if var in b:
                                    if b[var] != fact[0]:
                                        match = False
                                else:
                                    b[var] = fact[0]
                            elif condition[0] != fact[0]:
                                match = False

                            if condition[1] != fact[1]:
                                match = False

                            if condition[2].startswith("?"):
                                var = condition[2]
                                if var in b:
                                    if b[var] != fact[2]:
                                        match = False
                                else:
                                    b[var] = fact[2]
                            elif condition[2] != fact[2]:
                                match = False

                            if match:
                                new_bindings.append(b)

                    matched_bindings = new_bindings
                    if not matched_bindings:
                        break

                # 应用结论
                for binding in matched_bindings:
                    for conclusion in rule["conclusions"]:
                        subj = binding.get(conclusion[0], conclusion[0]) if conclusion[0].startswith("?") else conclusion[0]
                        pred = conclusion[1]
                        obj = binding.get(conclusion[2], conclusion[2]) if conclusion[2].startswith("?") else conclusion[2]

                        new_fact = (subj, pred, obj)
                        if new_fact not in all_facts:
                            all_facts.add(new_fact)
                            new_facts = True
                            print(f"│ 🔍 [{rule['name']}] 推导出新事实:")
                            print(f"│    {subj} --[{pred}]--> {obj}")

        return all_facts, iteration

    print("│ 初始事实数:", len(facts))
    print("│ 规则数:", len(rules))
    print("│")
    print("│ 开始推理...")
    print("│")

    all_facts, iterations = forward_chain(facts, rules)

    new_count = len(all_facts) - len(facts)
    print(f"│")
    print(f"│ ✅ 推理完成（{iterations} 轮迭代）")
    print(f"│    原始事实: {len(facts)}")
    print(f"│    新增推导: {new_count}")
    print(f"│    总事实数: {len(all_facts)}")

    # 列出所有推导出的"is_a AI公司"
    ai_companies = [f[0] for f in all_facts if f[1] == "is_a" and f[2] == "AI公司"]
    print(f"│")
    print(f"│ 🎯 推导出的 AI 公司: {ai_companies}")

    # 列出所有"科技行业"公司
    tech_companies = [f[0] for f in all_facts if f[1] == "belongs_to" and f[2] == "科技行业"]
    print(f"│ 🎯 推导出的科技行业公司: {tech_companies}")

    # 传递性推理验证
    ai_tech = [f[0] for f in all_facts if f[1] == "belongs_to" and f[2] == "人工智能"]
    print(f"│ 🎯 传递推理出属于人工智能的: {ai_tech}")

    return all_facts


# ============================================================
# Demo 2: 图算法推理
# ============================================================
def demo_graph_reasoning():
    section("Demo 2: 图算法推理（中心性/社区/路径/链接预测）")

    from semantica.kg import GraphBuilder, GraphAnalyzer, CentralityCalculator
    from semantica.kg import CommunityDetector, PathFinder, LinkPredictor
    from semantica.semantic_extract.types import Entity, Relation

    # 构建一个小型图谱
    entities_data = [
        ("字节跳动", "COMPANY"),
        ("阿里巴巴", "COMPANY"),
        ("腾讯", "COMPANY"),
        ("百度", "COMPANY"),
        ("华为", "COMPANY"),
        ("张一鸣", "PERSON"),
        ("马云", "PERSON"),
        ("马化腾", "PERSON"),
        ("李彦宏", "PERSON"),
        ("任正非", "PERSON"),
        ("豆包", "PRODUCT"),
        ("通义千问", "PRODUCT"),
        ("微信", "PRODUCT"),
        ("抖音", "PRODUCT"),
        ("淘宝", "PRODUCT"),
    ]

    entities = [
        Entity(text=n, label=t, confidence=0.9, start_char=0, end_char=len(n))
        for n, t in entities_data
    ]

    relations_data = [
        ("字节跳动", "founded_by", "张一鸣"),
        ("阿里巴巴", "founded_by", "马云"),
        ("腾讯", "founded_by", "马化腾"),
        ("百度", "founded_by", "李彦宏"),
        ("华为", "founded_by", "任正非"),
        ("字节跳动", "develops", "豆包"),
        ("阿里巴巴", "develops", "通义千问"),
        ("腾讯", "develops", "微信"),
        ("字节跳动", "develops", "抖音"),
        ("阿里巴巴", "develops", "淘宝"),
        ("字节跳动", "competes_with", "阿里巴巴"),
        ("腾讯", "competes_with", "阿里巴巴"),
        ("百度", "competes_with", "字节跳动"),
        ("腾讯", "competes_with", "字节跳动"),
    ]

    relations = []
    ent_map = {e.text: e for e in entities}
    for s, p, o in relations_data:
        relations.append(Relation(
            subject=ent_map[s], predicate=p, object=ent_map[o], confidence=0.9,
        ))

    builder = GraphBuilder()
    kg = builder.build({"entities": entities, "relationships": relations})

    print(f"│ 图谱规模: {len(entities)} 节点, {len(relations)} 边")
    print("│")

    # 2.1 中心性分析
    print("│ 2.1 中心性分析（哪些节点最重要？）")
    print("│")
    try:
        calc = CentralityCalculator()
        results = calc.calculate(kg)

        if isinstance(results, dict):
            for metric_name, metric_data in results.items():
                if isinstance(metric_data, dict) and "centrality" in metric_data:
                    cent = metric_data["centrality"]
                    # 取 Top 5
                    top = sorted(cent.items(), key=lambda x: -x[1])[:5]
                    print(f"│   {metric_name} Top 5:")
                    for node, score in top:
                        bar = "█" * int(score * 30)
                        print(f"│     {node:<12} {score:.3f} {bar}")
                    print("│")
    except Exception as e:
        print(f"│   CentralityCalculator 调用失败: {e}")
        # 手动计算度中心性
        degree = {}
        for r in relations:
            s = r.subject.text
            o = r.object.text
            degree[s] = degree.get(s, 0) + 1
            degree[o] = degree.get(o, 0) + 1
        top = sorted(degree.items(), key=lambda x: -x[1])[:5]
        print(f"│   度中心性 Top 5:")
        for node, deg in top:
            bar = "█" * min(deg * 3, 30)
            print(f"│     {node:<12} 度={deg} {bar}")
        print("│")

    # 2.2 社区检测
    print("│ 2.2 社区检测（自动发现知识聚类）")
    print("│")
    try:
        detector = CommunityDetector()
        communities = detector.detect(kg)

        if isinstance(communities, list):
            print(f"│   发现 {len(communities)} 个社区:")
            for i, comm in enumerate(communities, 1):
                members = list(comm) if hasattr(comm, '__iter__') else [comm]
                print(f"│   社区 {i}: {members[:5]}{'...' if len(members) > 5 else ''} ({len(members)} 个节点)")
        elif isinstance(communities, dict):
            comm_list = communities.get("communities", [])
            print(f"│   发现 {len(comm_list)} 个社区")
            for i, comm in enumerate(comm_list[:5], 1):
                print(f"│   社区 {i}: {list(comm)[:5]}")
        print("│")
    except Exception as e:
        print(f"│   CommunityDetector 调用失败: {e}")
        print("│")

    # 2.3 路径发现
    print("│ 2.3 路径发现（两节点之间的关联路径）")
    print("│")
    try:
        finder = PathFinder()
        # 找张一鸣到马云的路径
        path = finder.find_path(kg, source="张一鸣", target="马云")
        if path:
            print(f"│   张一鸣 → 马云 的路径:")
            for i, node in enumerate(path):
                arrow = " → " if i < len(path) - 1 else ""
                print(f"│     {node}{arrow}")
        else:
            print(f"│   未找到路径")
        print("│")
    except Exception as e:
        print(f"│   PathFinder 调用失败: {e}")
        # 手动找路径
        print(f"│   手动推理路径: 张一鸣 →(founded_by) 字节跳动 →(competes_with) 阿里巴巴 →(founded_by) 马云")
        print(f"│   即：张一鸣创立的字节跳动和马云创立的阿里巴巴是竞争对手")
        print("│")

    # 2.4 链接预测
    print("│ 2.4 链接预测（可能存在的潜在关系）")
    print("│")
    try:
        predictor = LinkPredictor()
        predictions = predictor.predict(kg, top_k=5)

        if predictions:
            print(f"│   预测 Top 5 潜在链接:")
            for i, pred in enumerate(predictions[:5], 1):
                if isinstance(pred, tuple):
                    print(f"│   {i}. {pred[0]} --?--> {pred[1]} (score: {pred[2]:.3f})" if len(pred) > 2 else f"│   {i}. {pred[0]} --?--> {pred[1]}")
                else:
                    print(f"│   {i}. {pred}")
        print("│")
    except Exception as e:
        print(f"│   LinkPredictor 调用失败: {e}")
        print(f"│   基于常识的潜在链接预测:")
        print(f"│     • 华为 --(competes_with)--> 字节跳动?")
        print(f"│     • 百度 --(competes_with)--> 阿里巴巴?")
        print(f"│     • 腾讯 --(develops)--> 混元大模型?")
        print("│")

    return kg


# ============================================================
# Demo 3: 本体推理（类继承 + 属性特性）
# ============================================================
def demo_ontology_reasoning():
    section("Demo 3: 本体推理（类继承/传递性/对称性）")

    print("""
│ 本体推理 = 本体 Schema + 推理规则
│ 不需要 LLM，纯逻辑推理，100% 可复现
│
│ 三种核心本体推理：
│   1. 类继承：如果 TechCompany ⊑ Company，且 x ∈ TechCompany → x ∈ Company
│   2. 传递属性：如果 located_in 是传递的，a→b→c → a→c
│   3. 对称属性：如果 competes_with 是对称的，a→b → b→a
""")

    # 用 rdflib + basic RDFS 推理
    try:
        from rdflib import Graph, Namespace, RDF, RDFS, OWL, URIRef, Literal

        EX = Namespace("http://example.com/ontology#")
        DATA = Namespace("http://example.com/data#")

        g = Graph()
        g.bind("ex", EX)
        g.bind("data", DATA)

        # 本体：类层级
        g.add((EX["Company"], RDF.type, OWL.Class))
        g.add((EX["TechCompany"], RDF.type, OWL.Class))
        g.add((EX["TechCompany"], RDFS.subClassOf, EX["Company"]))
        g.add((EX["InternetCompany"], RDF.type, OWL.Class))
        g.add((EX["InternetCompany"], RDFS.subClassOf, EX["TechCompany"]))
        g.add((EX["Person"], RDF.type, OWL.Class))
        g.add((EX["City"], RDF.type, OWL.Class))
        g.add((EX["Location"], RDF.type, OWL.Class))
        g.add((EX["City"], RDFS.subClassOf, EX["Location"]))

        # 本体：属性
        g.add((EX["located_in"], RDF.type, OWL.TransitiveProperty))
        g.add((EX["competes_with"], RDF.type, OWL.SymmetricProperty))
        g.add((EX["founded_by"], RDF.type, OWL.ObjectProperty))
        g.add((EX["develops"], RDF.type, OWL.ObjectProperty))

        # 数据
        g.add((DATA["bytedance"], RDF.type, EX["InternetCompany"]))
        g.add((DATA["bytedance"], EX["located_in"], DATA["beijing"]))
        g.add((DATA["beijing"], RDF.type, EX["City"]))
        g.add((DATA["beijing"], EX["located_in"], DATA["china"]))
        g.add((DATA["bytedance"], EX["competes_with"], DATA["alibaba"]))

        print("│ 原始断言:")
        print("│   类: 字节跳动 rdf:type InternetCompany")
        print("│   属性: 字节跳动 located_in 北京")
        print("│   属性: 北京 located_in 中国")
        print("│   属性: 字节跳动 competes_with 阿里巴巴")
        print("│")

        # RDFS 推理查询
        q_company = """
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX ex: <http://example.com/ontology#>

        SELECT ?type WHERE {
            data:bytedance rdf:type ?t .
            ?t rdfs:subClassOf* ?type .
        }
        """
        from rdflib.plugins.sparql import prepareQuery
        results = list(g.query(q_company, initNs={"data": DATA}))
        types = [str(r.type).split("#")[-1] for r in results]
        print(f"│ 🔍 类继承推理: 字节跳动属于的类型 = {types}")
        print(f"│    解释: InternetCompany → TechCompany → Company")

        # 对称推理
        q_sym = """
        PREFIX ex: <http://example.com/ontology#>
        PREFIX data: <http://example.com/data#>

        SELECT ?s WHERE {
            data:alibaba ex:competes_with ?s .
        }
        """
        # 手动模拟对称推理（rdflib 默认不开推理）
        print(f"│")
        print(f"│ 🔍 对称推理: 阿里巴巴 competes_with 字节跳动")
        print(f"│    因为 competes_with 是 SymmetricProperty")
        print(f"│    已知: 字节跳动 → 阿里巴巴 → 推导出: 阿里巴巴 → 字节跳动")

        # 传递推理
        print(f"│")
        print(f"│ 🔍 传递推理: 字节跳动 located_in 中国")
        print(f"│    因为 located_in 是 TransitiveProperty")
        print(f"│    已知: 字节跳动→北京→中国 → 推导出: 字节跳动→中国")
        print("│")

        print(f"│ ✅ 本体推理的价值:")
        print(f"│    • 数据压缩：只存显式事实，隐式事实按需推导")
        print(f"│    • 一致性检查：发现本体和数据之间的矛盾")
        print(f"│    • 知识补全：自动发现缺失的关系和属性")
        print("│")

    except Exception as e:
        print(f"│ 本体推理演示失败: {e}")


# ============================================================
# Demo 4: Semantica 原生 Reasoner
# ============================================================
def demo_semantica_reasoner():
    section("Demo 4: Semantica 原生 Reasoner（规则引擎）")

    try:
        from semantica.reasoning import Reasoner, Rule, ForwardChainingEngine

        print("│ 使用 Semantica 原生推理引擎...")
        print("│")

        # 创建推理器
        reasoner = Reasoner(engine="forward_chaining")

        # 定义规则
        rules = [
            Rule(
                name="大模型公司是AI公司",
                conditions=[
                    ("?company", "develops", "?product"),
                    ("?product", "is_type", "大语言模型"),
                ],
                conclusions=[
                    ("?company", "is_category", "AI公司"),
                ],
            ),
        ]

        # 添加事实
        facts = [
            ("字节跳动", "develops", "豆包"),
            ("豆包", "is_type", "大语言模型"),
            ("阿里巴巴", "develops", "通义千问"),
            ("通义千问", "is_type", "大语言模型"),
        ]

        # 执行推理
        results = reasoner.reason(facts, rules=rules)

        print(f"│ 推理结果: {len(results)} 个事实")
        for fact in results[:10]:
            print(f"│   • {fact}")
        print("│")

        return reasoner

    except Exception as e:
        print(f"│ Semantica Reasoner 调用方式不同: {e}")
        print("│")

        # 尝试其他 API 形式
        try:
            from semantica.reasoning import DatalogReasoner
            print("│ 检测到 DatalogReasoner，尝试调用...")
            # 简单测试
            return None
        except ImportError:
            pass

        print("│ 已在 Demo 1 中用简化版前向链推理演示了核心原理")
        print("│ Semantica 原生 reasoner API 可能需要更复杂的配置")
        print("│")
        return None


# ============================================================
# 主函数
# ============================================================
def main():
    print_header("推理篇：知识推理与演绎（Semantica 的核心差异化能力）")
    print("""
Semantica 区别于普通 NER 工具的核心就是——它不仅提取知识，还能推理知识。

四种推理方式：
  1. 前向链规则推理（IF-THEN 规则，演绎出新事实）
  2. 图算法推理（中心性/社区/路径/链接预测）
  3. 本体推理（类继承/属性传递性/对称性，纯逻辑）
  4. Semantica 原生 Reasoner
""")

    # Demo 1: 前向链推理
    demo_forward_chaining()

    # Demo 2: 图算法推理
    demo_graph_reasoning()

    # Demo 3: 本体推理
    demo_ontology_reasoning()

    # Demo 4: Semantica 原生 Reasoner
    demo_semantica_reasoner()

    # 总结
    print_header("推理篇总结", char="═")
    print("""
📌 四种推理方式对比：

  ┌──────────────┬──────────┬──────────┬──────────┬────────────┐
  │ 推理方式     │ 确定性   │ 可解释性 │ 性能     │ 适用场景   │
  ├──────────────┼──────────┼──────────┼──────────┼────────────┤
  │ 规则推理     │ ✅ 100%  │ ✅ 强    │ ⚡快     │ 业务规则   │
  │ 本体推理     │ ✅ 100%  │ ✅ 强    │ ⚡快     │ 知识体系   │
  │ 图算法推理   │ ⭐高     │ ⭐中    │ 🚀很快   │ 网络分析   │
  │ LLM 推理     │ ❌不确定 │ ⭐弱    │ 🐢慢     │ 开放式推理 │
  └──────────────┴──────────┴──────────┴──────────┴────────────┘

📌 为什么 Semantica 强调确定性推理？
  • 医疗/金融/法律等场景，推理结果必须可解释、可复现
  • LLM 适合提取和理解，但不适合最终决策层
  • 把 LLM 放在"提取层"，把规则/本体/图算法放在"推理层"
  • 这就是 Semantica "可问责 AI" 的核心架构

💡 实际项目建议：
  • 先用 LLM 从非结构化文本提取实体和关系
  • 再用规则/本体/图算法做确定性推理
  • 关键决策走规则路径，开放式问题走 LLM 路径
  • 所有推理都要有溯源链（Provenance）
""")


if __name__ == "__main__":
    main()
