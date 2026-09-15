"""
Semantica 要素提取 Demo
========================
从长文本中提取实体、关系、三元组，构建知识图谱。

运行方式:
    python semantica_extract_demo.py

不需要 API Key 也能跑通 pattern/rule 模式的提取。
"""

import json
import sys

# ============================================================
# 测试文本：模拟一篇科技领域长文本
# ============================================================
SAMPLE_TEXT = """
Apple Inc. was founded by Steve Jobs, Steve Wozniak, and Ronald Wayne in 1976 in Cupertino, California.
The company's first product was the Apple I computer, designed by Wozniak.
In 1984, Apple introduced the Macintosh, which popularized the graphical user interface.
Tim Cook became the CEO of Apple in 2011, succeeding Steve Jobs.
In 2023, Apple announced a $1 billion investment in artificial intelligence research.
The company is also working on the Apple Vision Pro, a mixed reality headset.
Apple's main competitor in the smartphone market is Samsung Electronics, based in Seoul, South Korea.
Google, headquartered in Mountain View, develops the Android operating system that powers many Samsung devices.
Microsoft, led by CEO Satya Nadella, invested $10 billion in OpenAI in 2023.
OpenAI released the GPT-4 language model in March 2023, which was trained on a large corpus of text data.
"""


def print_header(title):
    print(f"\n{'='*65}")
    print(f"  {title}")
    print(f"{'='*65}")


# ============================================================
# Demo 1: 命名实体识别 (NER)
# ============================================================
def demo_ner():
    print_header("Demo 1: Named Entity Recognition (命名实体识别)")
    
    from semantica.semantic_extract import NERExtractor
    
    # pattern 方法：纯正则/规则匹配，本地运行，无需 API
    ner = NERExtractor(method="pattern", confidence_threshold=0.5)
    entities = ner.extract(SAMPLE_TEXT)
    
    print(f"\n✅ 提取到 {len(entities)} 个实体\n")
    print(f"{'实体文本':<30} {'类型标签':<12} {'置信度':<8} {'位置':<12}")
    print("-" * 65)
    for ent in entities:
        text = ent.text[:28]
        label = ent.label[:10]
        conf = ent.confidence
        pos = f"{ent.start_char}-{ent.end_char}"
        print(f"{text:<30} {label:<12} {conf:<8.2f} {pos:<12}")
    
    # 按类型统计
    type_counts = {}
    for ent in entities:
        type_counts[ent.label] = type_counts.get(ent.label, 0) + 1
    print(f"\n📊 实体类型分布:")
    for label, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"   {label}: {count}")
    
    return entities


# ============================================================
# Demo 2: 关系抽取
# ============================================================
def demo_relation_extraction(entities):
    print_header("Demo 2: Relation Extraction (关系抽取)")
    
    from semantica.semantic_extract import RelationExtractor
    
    # pattern 方法：基于规则匹配关系
    rel_extractor = RelationExtractor(method="pattern", confidence_threshold=0.5)
    relations = rel_extractor.extract(SAMPLE_TEXT, entities=entities)
    
    print(f"\n✅ 提取到 {len(relations)} 条关系\n")
    print(f"{'主语':<22} {'谓词':<20} {'宾语':<22}")
    print("-" * 66)
    for rel in relations:
        subj = (rel.subject.text if hasattr(rel.subject, 'text') else str(rel.subject))[:20]
        pred = rel.predicate[:18]
        obj = (rel.object.text if hasattr(rel.object, 'text') else str(rel.object))[:20]
        print(f"{subj:<22} {pred:<20} {obj:<22}")
    
    # 按谓词统计
    pred_counts = {}
    for rel in relations:
        pred_counts[rel.predicate] = pred_counts.get(rel.predicate, 0) + 1
    print(f"\n📊 关系类型分布:")
    for pred, count in sorted(pred_counts.items(), key=lambda x: -x[1]):
        print(f"   {pred}: {count}")
    
    return relations


# ============================================================
# Demo 3: 三元组提取 + RDF 序列化
# ============================================================
def demo_triplet_extraction():
    print_header("Demo 3: Triplet Extraction & RDF Serialization (三元组提取)")
    
    from semantica.semantic_extract import TripletExtractor
    
    try:
        tri = TripletExtractor(method="pattern", include_temporal=True)
        triplets = tri.extract_triplets(SAMPLE_TEXT)
        
        print(f"\n✅ 提取到 {len(triplets)} 个三元组\n")
        
        # 打印前 10 个
        for i, t in enumerate(triplets[:10]):
            if isinstance(t, tuple):
                print(f"   {i+1:2d}. ({t[0]}, {t[1]}, {t[2]})")
            else:
                print(f"   {i+1:2d}. {t}")
        
        if len(triplets) > 10:
            print(f"   ... 还有 {len(triplets) - 10} 个")
        
        # 尝试序列化为 Turtle 格式
        try:
            valid = tri.validate_triplets(triplets)
            turtle = tri.serialize_triplets(valid, format="turtle")
            print(f"\n📦 Turtle 格式 (前 500 字符):")
            print("-" * 65)
            print(turtle[:500])
            if len(turtle) > 500:
                print(f"... (共 {len(turtle)} 字符)")
        except Exception as e:
            print(f"\n⚠️  序列化失败: {e}")
        
        return triplets
        
    except Exception as e:
        print(f"\n⚠️  TripletExtractor 调用失败: {e}")
        return []


# ============================================================
# Demo 4: 构建知识图谱 + 图分析
# ============================================================
def demo_knowledge_graph(entities, relations):
    print_header("Demo 4: Knowledge Graph Construction (知识图谱构建)")
    
    from semantica.kg import GraphBuilder, GraphAnalyzer
    
    # 构建图谱
    builder = GraphBuilder()
    kg = builder.build({"entities": entities, "relationships": relations})
    
    # 统计节点和边
    nodes = kg.get("entities", kg.get("nodes", []))
    edges = kg.get("relationships", kg.get("edges", []))
    
    print(f"\n✅ 知识图谱构建完成")
    print(f"   节点数: {len(nodes)}")
    print(f"   边数:   {len(edges)}")
    
    # 图谱摘要
    try:
        summary = builder.get_graph_summary()
        print(f"\n📊 图谱摘要:")
        if isinstance(summary, dict):
            for k, v in summary.items():
                print(f"   {k}: {v}")
        else:
            print(f"   {summary}")
    except Exception:
        pass
    
    # 图分析
    print(f"\n{'─'*65}")
    print("  图分析 (Graph Analytics)")
    print(f"{'─'*65}")
    
    try:
        analyzer = GraphAnalyzer()
        results = analyzer.analyze(kg)
        
        if isinstance(results, dict):
            for k, v in results.items():
                val_str = str(v)[:60]
                print(f"   {k}: {val_str}")
        else:
            print(f"   {results}")
    except Exception as e:
        print(f"   GraphAnalyzer 不可用: {e}")
        print("   手动计算简单统计...")
        
        # 手动计算度分布
        degree = {}
        for rel in relations:
            s = rel.subject.text if hasattr(rel.subject, 'text') else str(rel.subject)
            o = rel.object.text if hasattr(rel.object, 'text') else str(rel.object)
            degree[s] = degree.get(s, 0) + 1
            degree[o] = degree.get(o, 0) + 1
        
        # 按度排序
        top_nodes = sorted(degree.items(), key=lambda x: -x[1])[:5]
        print(f"\n   🔝 度最高的 Top 5 节点:")
        for node, deg in top_nodes:
            print(f"      {node}: 度 = {deg}")
    
    return kg


# ============================================================
# Demo 5: 事件检测
# ============================================================
def demo_event_detection():
    print_header("Demo 5: Event Detection (事件检测)")
    
    try:
        from semantica.semantic_extract import EventDetector
        
        detector = EventDetector(
            extract_participants=True,
            extract_time=True,
            extract_amount=True,
        )
        events = detector.detect_events(SAMPLE_TEXT)
        
        print(f"\n✅ 检测到 {len(events)} 个事件\n")
        
        for i, event in enumerate(events):
            etype = getattr(event, 'type', getattr(event, 'event_type', 'unknown'))
            print(f"   事件 {i+1}: {etype}")
            
            # 尝试打印各种属性
            for attr in ['participants', 'time', 'date', 'amount', 'location', 'description']:
                val = getattr(event, attr, None)
                if val:
                    print(f"     {attr}: {val}")
            print()
            
        return events
        
    except Exception as e:
        print(f"\n⚠️  EventDetector 调用失败: {e}")
        return []


# ============================================================
# Demo 6: 共指消解
# ============================================================
def demo_coreference(entities):
    print_header("Demo 6: Coreference Resolution (共指消解)")
    
    try:
        from semantica.semantic_extract import CoreferenceResolver
        
        resolver = CoreferenceResolver(method="rule")
        chains = resolver.resolve_coreferences(SAMPLE_TEXT, entities=entities)
        
        print(f"\n✅ 发现 {len(chains)} 个共指链\n")
        for i, chain in enumerate(chains):
            rep = chain.representative.text if hasattr(chain.representative, 'text') else str(chain.representative)
            mentions = [m.text if hasattr(m, 'text') else str(m) for m in chain.mentions]
            print(f"   链 {i+1}: 代表 = '{rep}'")
            print(f"          别名 = {mentions}")
        
        return chains
        
    except Exception as e:
        print(f"\n⚠️  CoreferenceResolver 调用失败: {e}")
        return []


# ============================================================
# 主函数
# ============================================================
def main():
    print("\n" + "=" * 65)
    print("  🧠 Semantica 要素提取完整 Demo")
    print("=" * 65)
    print(f"\n输入文本长度: {len(SAMPLE_TEXT)} 字符")
    print(f"输入文本示例: {SAMPLE_TEXT[:100]}...")
    
    # 1. NER
    entities = demo_ner()
    
    # 2. 关系抽取
    relations = demo_relation_extraction(entities)
    
    # 3. 三元组提取
    triplets = demo_triplet_extraction()
    
    # 4. 知识图谱构建 + 分析
    kg = demo_knowledge_graph(entities, relations)
    
    # 5. 事件检测
    demo_event_detection()
    
    # 6. 共指消解
    demo_coreference(entities)
    
    # 总结
    print_header("Demo 完成总结")
    print(f"""
✅ 已完成的提取模块:
   ├── NER 命名实体识别         ({len(entities)} 个实体)
   ├── 关系抽取                 ({len(relations)} 条关系)
   ├── 三元组提取               ({len(triplets)} 个三元组)
   ├── 知识图谱构建             ({len(entities)} 节点, {len(relations)} 边)
   ├── 图分析                   (度分布/中心性等)
   ├── 事件检测                 (已尝试)
   └── 共指消解                 (已尝试)

💡 下一步可以做:
   1. 配置 LLM API Key，用 method="llm" 提升提取质量
   2. 用自己的长文本替换 SAMPLE_TEXT 测试
   3. 接入图数据库 (Neo4j / FalkorDB) 存储
   4. 启用 Ontology + SHACL 约束验证
   5. 启用 Provenance 溯源追踪
""")


if __name__ == "__main__":
    main()
