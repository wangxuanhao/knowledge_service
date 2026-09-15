"""
生成 Explorer 可用的图谱数据
"""
import json
from pathlib import Path
from semantica.semantic_extract.types import Entity, Relation
from semantica.kg import GraphBuilder

# 构造测试数据（中文科技公司图谱）
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
    ("豆包", "AIMODEL"),
    ("通义千问", "AIMODEL"),
    ("混元大模型", "AIMODEL"),
    ("文心一言", "AIMODEL"),
    ("盘古大模型", "AIMODEL"),
    ("抖音", "PRODUCT"),
    ("淘宝", "PRODUCT"),
    ("微信", "PRODUCT"),
    ("北京", "CITY"),
    ("杭州", "CITY"),
    ("深圳", "CITY"),
    ("人工智能", "TECH"),
    ("大语言模型", "TECH"),
    ("智能驾驶", "TECH"),
]

entities = []
for name, etype in entities_data:
    entities.append(Entity(
        text=name, label=etype, confidence=0.95,
        start_char=0, end_char=len(name),
    ))

relations_data = [
    ("字节跳动", "founded_by", "张一鸣"),
    ("阿里巴巴", "founded_by", "马云"),
    ("腾讯", "founded_by", "马化腾"),
    ("百度", "founded_by", "李彦宏"),
    ("华为", "founded_by", "任正非"),
    ("字节跳动", "develops", "豆包"),
    ("阿里巴巴", "develops", "通义千问"),
    ("腾讯", "develops", "混元大模型"),
    ("百度", "develops", "文心一言"),
    ("华为", "develops", "盘古大模型"),
    ("字节跳动", "develops", "抖音"),
    ("阿里巴巴", "develops", "淘宝"),
    ("腾讯", "develops", "微信"),
    ("字节跳动", "located_in", "北京"),
    ("阿里巴巴", "located_in", "杭州"),
    ("腾讯", "located_in", "深圳"),
    ("百度", "located_in", "北京"),
    ("华为", "located_in", "深圳"),
    ("豆包", "is_a", "大语言模型"),
    ("通义千问", "is_a", "大语言模型"),
    ("文心一言", "is_a", "大语言模型"),
    ("混元大模型", "is_a", "大语言模型"),
    ("盘古大模型", "is_a", "大语言模型"),
    ("大语言模型", "belongs_to", "人工智能"),
    ("智能驾驶", "belongs_to", "人工智能"),
    ("字节跳动", "competes_with", "阿里巴巴"),
    ("字节跳动", "competes_with", "腾讯"),
    ("百度", "competes_with", "字节跳动"),
    ("腾讯", "competes_with", "阿里巴巴"),
]

relations = []
for subj, pred, obj in relations_data:
    subj_ent = next(e for e in entities if e.text == subj)
    obj_ent = next(e for e in entities if e.text == obj)
    relations.append(Relation(
        subject=subj_ent, predicate=pred, object=obj_ent, confidence=0.9,
    ))

# 构建图谱
builder = GraphBuilder()
kg = builder.build({"entities": entities, "relationships": relations})

# 导出为 JSON（Explorer 可读）
output_path = Path(__file__).parent / "explorer_graph.json"

# 转换为 Explorer 可理解的格式
graph_data = {
    "nodes": [
        {
            "id": e.text,
            "label": e.text,
            "type": e.label,
            "group": e.label,
            "confidence": e.confidence,
        }
        for e in entities
    ],
    "edges": [
        {
            "from": r.subject.text,
            "to": r.object.text,
            "label": r.predicate,
            "confidence": r.confidence,
        }
        for r in relations
    ],
    "metadata": {
        "total_nodes": len(entities),
        "total_edges": len(relations),
        "description": "中国科技公司知识图谱（示例数据）",
    }
}

with open(output_path, "w", encoding="utf-8") as f:
    json.dump(graph_data, f, ensure_ascii=False, indent=2)

print(f"✅ 图谱数据已生成: {output_path}")
print(f"   节点数: {len(entities)}")
print(f"   边数: {len(relations)}")
