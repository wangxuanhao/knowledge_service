"""
01 - 基础：本地模式要素提取
===============================
纯本地运行，无需 API Key。
使用 pattern/rule 方法进行实体识别、关系抽取、三元组生成。

适用场景：
- 快速原型验证
- 有明确模式的结构化文本
- 高吞吐、低成本需求
- 离线环境

运行: python 01_基础_本地模式提取.py
"""

import json
import sys
from pathlib import Path

# ============================================================
# 示例文本：三篇不同领域的短文本（测试通用性）
# ============================================================
TEXTS = {
    "科技新闻": """
2026-09-03 科技新闻推送（10条，精简去重，偏 AI/具身/智能体/芯片/国际）

英伟达×联发科：英伟达认购联发科35亿美元可转债，借 NVLink Fusion 共研机架级定制 XPU，卡位云厂自研 ASIC 替代 GPU 的入口。
黄仁勋 G20 布道 AI 经济学：称 Token 即新型计价单位，1GW AI 工厂耗资 500–600 亿美元，本十年末全球 AI 基建冲 100GW，算力升格为国家基建。
特斯拉 Cybercab 发布：得州奥斯汀亮相无方向盘/踏板无人驾驶出租车，依赖 FSD，目标单车 <3 万美元、每英里 0.1–0.2 美元，接本地出行网不零售。
腾讯 WorkBuddy 开放平台上线：开放 Agent 底座（Skill/Expert/Connector），接智能眼镜/耳机等 30+ 品牌，覆盖金融法律医疗等 20 域。
VAST Tripo P2.0 发布：原生直出四边面拓扑（四边面 2.5 万/三角面 5 万），基于 SIGGRAPH 2026 Nexus 扩散框架，免重拓扑直接绑定动画。
Grok Bot 被评“下个 ChatGPT 时刻”：零配置云端电脑优先，独立浏览器/文件系统/终端+跨轮记忆，同类任务缩至 7–12 秒，引爆 Agent Token 消耗。
马斯克 G20 连线：12–18 个月 AI 编程达 Stockfish 级，明年底接管无实物塑造类数字工作，10 年内全球人形机器人超 10 亿台，2027 年 AI 芯片或遇 15GW 电缺口。
中国人形机器人运动会收官+定标：发 2500 小时全量数据集，《国家人形机器人产业标准体系（2026 征求意见稿）》亮相，从“破纪录”转“赛事+数据+国际标准”卡生态。
它石智航 A1 进汽车产线：双臂协同插柔软线束，自研 AWE 具身大模型实时力控反馈，央视报道“组团进厂打工”，上半年国内具身融资 935 亿同比 5 倍。
英伟达市值破 5.46 万亿美元：单日涨超 3%（约 +2200 亿美元），市场押注 AI 基建资本开支持续放大，成全球股王风向标
""",
}


def print_header(title, char="═"):
    print(f"\n{char * 68}")
    print(f"  {title}")
    print(f"{char * 68}")


def section(title):
    print(f"\n┌─ {title} " + "─" * (60 - len(title)))


# ============================================================
# 1. 命名实体识别 (NER)
# ============================================================
def demo_ner(text, method="pattern"):
    section(f"NER 实体识别 (method={method})")
    
    from semantica.semantic_extract import NERExtractor
    
    ner = NERExtractor(method=method, confidence_threshold=0.5)
    entities = ner.extract(text)
    
    print(f"│ 提取到 {len(entities)} 个实体:")
    print(f"│")
    print(f"│ {'#':>3}  {'实体文本':<28} {'类型':<12} {'置信度':>6}  {'位置':>10}")
    print(f"│ {'─'*3}  {'─'*28} {'─'*12} {'─'*6}  {'─'*10}")
    
    for i, ent in enumerate(entities, 1):
        text_trunc = ent.text[:26] + (".." if len(ent.text) > 26 else "")
        print(f"│ {i:>3}  {text_trunc:<28} {ent.label:<12} {ent.confidence:>6.2f}  {ent.start_char:>5}-{ent.end_char:<5}")
    
    # 类型统计
    type_counts = {}
    for ent in entities:
        type_counts[ent.label] = type_counts.get(ent.label, 0) + 1
    print(f"│")
    print(f"│ 类型分布: {type_counts}")
    
    return entities


# ============================================================
# 2. 关系抽取
# ============================================================
def demo_relations(text, entities, method="pattern"):
    section(f"关系抽取 (method={method})")
    
    from semantica.semantic_extract import RelationExtractor
    
    rel_extractor = RelationExtractor(method=method, confidence_threshold=0.5, bidirectional=True)
    relations = rel_extractor.extract(text, entities=entities)
    
    print(f"│ 提取到 {len(relations)} 条关系:")
    print(f"│")
    print(f"│ {'#':>3}  {'主语':<22} {'谓词':<18} {'宾语':<22} {'置信度':>6}")
    print(f"│ {'─'*3}  {'─'*22} {'─'*18} {'─'*22} {'─'*6}")
    
    for i, rel in enumerate(relations, 1):
        subj = (rel.subject.text if hasattr(rel.subject, 'text') else str(rel.subject))[:20]
        pred = rel.predicate[:16]
        obj = (rel.object.text if hasattr(rel.object, 'text') else str(rel.object))[:20]
        conf = rel.confidence
        print(f"│ {i:>3}  {subj:<22} {pred:<18} {obj:<22} {conf:>6.2f}")
    
    # 谓词统计
    pred_counts = {}
    for rel in relations:
        pred_counts[rel.predicate] = pred_counts.get(rel.predicate, 0) + 1
    print(f"│")
    print(f"│ 关系类型分布: {pred_counts}")
    
    return relations


# ============================================================
# 3. 三元组提取 + RDF 序列化
# ============================================================
def demo_triplets(text, method="pattern"):
    section(f"三元组提取 + RDF 序列化 (method={method})")
    
    from semantica.semantic_extract import TripletExtractor
    
    tri = TripletExtractor(
        method=method,
        include_temporal=True,
        include_provenance=True,
        confidence_threshold=0.5,
    )
    triplets = tri.extract_triplets(text)
    
    print(f"│ 提取到 {len(triplets)} 个三元组:")
    print(f"│")
    
    for i, t in enumerate(triplets[:15], 1):
        if hasattr(t, 'subject'):
            subj = str(t.subject)[:25]
            pred = str(t.predicate)[:20]
            obj = str(t.object)[:25]
            conf = t.confidence if hasattr(t, 'confidence') else 0
            print(f"│ {i:>2}. ({subj}, {pred}, {obj})  conf={conf:.2f}")
        else:
            print(f"│ {i:>2}. {t}")
    
    if len(triplets) > 15:
        print(f"│     ... 还有 {len(triplets) - 15} 个")
    
    # 验证 + 序列化
    try:
        valid = tri.validate_triplets(triplets)
        print(f"│")
        print(f"│ 验证通过: {len(valid)} / {len(triplets)}")
        
        # 序列化为 Turtle
        turtle = tri.serialize_triplets(valid, format="turtle")
        print(f"│")
        print(f"│ Turtle 格式 (RDF):")
        print(f"│ {'─'*60}")
        for line in turtle.strip().split('\n')[:10]:
            print(f"│   {line}")
        if len(turtle.strip().split('\n')) > 10:
            print(f"│   ...")
    except Exception as e:
        print(f"│ 序列化失败: {e}")
    
    return triplets


# ============================================================
# 4. 事件检测
# ============================================================
def demo_events(text):
    section("事件检测 (Event Detection)")
    
    try:
        from semantica.semantic_extract import EventDetector
        
        detector = EventDetector(
            extract_participants=True,
            extract_time=True,
            extract_amount=True,
            extract_location=True,
        )
        events = detector.detect_events(text)
        
        print(f"│ 检测到 {len(events)} 个事件:")
        print(f"│")
        
        for i, event in enumerate(events, 1):
            etype = getattr(event, 'type', getattr(event, 'event_type', 'unknown'))
            print(f"│ [{i}] 类型: {etype}")
            
            attrs = ['participants', 'time', 'date', 'amount', 'location', 'description']
            for attr in attrs:
                val = getattr(event, attr, None)
                if val:
                    print(f"│     {attr}: {val}")
            print(f"│")
        
        return events
        
    except Exception as e:
        print(f"│ 事件检测失败: {e}")
        return []


# ============================================================
# 5. 共指消解
# ============================================================
def demo_coreference(text, entities):
    section("共指消解 (Coreference Resolution)")
    
    try:
        from semantica.semantic_extract import CoreferenceResolver
        
        resolver = CoreferenceResolver(method="rule")
        chains = resolver.resolve_coreferences(text, entities=entities)
        
        print(f"│ 发现 {len(chains)} 个共指链:")
        print(f"│")
        
        for i, chain in enumerate(chains, 1):
            rep = chain.representative.text if hasattr(chain.representative, 'text') else str(chain.representative)
            mentions = [m.text if hasattr(m, 'text') else str(m) for m in chain.mentions]
            unique_mentions = list(dict.fromkeys(mentions))  # 去重保序
            print(f"│ [{i}] 代表实体: '{rep}'")
            print(f"│     所有指代: {unique_mentions}")
        
        return chains
        
    except Exception as e:
        print(f"│ 共指消解失败: {e}")
        return []


# ============================================================
# 6. 实体去重 / 消歧
# ============================================================
def demo_deduplication(entities):
    section("实体消歧 / 去重 (Deduplication)")
    
    try:
        from semantica.deduplication import EntityDeduplicator
        
        dedup = EntityDeduplicator(
            method="v1",  # v1: 字符串相似度, v2: 向量相似度
            threshold=0.8,
            blocking=True,
        )
        clusters = dedup.deduplicate(entities)
        
        print(f"│ 原始实体数: {len(entities)}")
        print(f"│ 去重后簇数: {len(clusters)}")
        print(f"│")
        
        for i, cluster in enumerate(clusters, 1):
            canonical = cluster.canonical if hasattr(cluster, 'canonical') else cluster[0]
            canon_text = canonical.text if hasattr(canonical, 'text') else str(canonical)
            members = [e.text if hasattr(e, 'text') else str(e) for e in (cluster.members if hasattr(cluster, 'members') else cluster)]
            print(f"│ [{i}] 规范名: '{canon_text}'")
            print(f"│     成员: {members}")
        
        return clusters
        
    except Exception as e:
        print(f"│ 实体消歧模块调用方式不同，尝试手动去重...")
        # 简单手动去重
        seen = {}
        unique_entities = []
        for ent in entities:
            key = ent.text.lower().strip()
            if key not in seen:
                seen[key] = ent
                unique_entities.append(ent)
        
        print(f"│ 原始实体数: {len(entities)}")
        print(f"│ 简单去重后: {len(unique_entities)}")
        print(f"│")
        print(f"│ 去重后的实体:")
        for ent in unique_entities:
            print(f"│   • {ent.text} ({ent.label})")
        
        return unique_entities


# ============================================================
# 主函数
# ============================================================
def main():
    print_header("基础篇：本地模式要素提取完整演示")
    print("""
本脚本演示 Semantica 的纯本地提取能力，完全不需要 API Key。
包括: NER 实体识别、关系抽取、三元组生成、事件检测、共指消解、实体消歧。
    """)
    
    for domain, text in TEXTS.items():
        print_header(f"领域: {domain}", char="─")
        
        # 1. NER
        entities = demo_ner(text, method="pattern")
        
        # 2. 关系抽取
        relations = demo_relations(text, entities, method="pattern")
        
        # 3. 三元组
        triplets = demo_triplets(text, method="pattern")
        
        # 4. 事件检测
        events = demo_events(text)
        
        # 5. 共指消解
        chains = demo_coreference(text, entities)
        
        # 6. 实体去重
        deduped = demo_deduplication(entities)
    
    # 总结
    print_header("总结与说明", char="═")
    print("""
✅ 本地模式（pattern/rule）特点：
   • 速度极快，完全离线，零成本
   • 适合有明确模式的文本（日期、金额、已知实体名等）
   • 提取质量有限（只能匹配预定义规则）
   • 泛化能力弱，对新实体/新关系效果差

💡 如需更高质量的提取，请运行 02_进阶_LLM模式提取.py
   使用大语言模型驱动，语义理解能力强，适合开放域文本。

📌 三种提取方法对比：
   ┌──────────┬──────────┬─────────┬──────────┬──────────┐
   │ 方法     │ 速度     │ 成本    │ 准确率   │ 泛化能力 │
   ├──────────┼──────────┼─────────┼──────────┼──────────┤
   │ pattern  │ ⚡极快    │ 免费    │ ⭐⭐     │ ⭐       │
   │ rule     │ ⚡快      │ 免费    │ ⭐⭐⭐   │ ⭐⭐     │
   │ ml       │ 🐢中等    │ 免费    │ ⭐⭐⭐   │ ⭐⭐⭐   │
   │ llm      │ 🐢慢      │ 收费    │ ⭐⭐⭐⭐⭐│ ⭐⭐⭐⭐⭐│
   └──────────┴──────────┴─────────┴──────────┴──────────┘
""")


if __name__ == "__main__":
    main()
