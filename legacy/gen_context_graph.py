"""
生成 Explorer 可用的 ContextGraph 数据
"""
from pathlib import Path
from semantica.context import ContextGraph

cg = ContextGraph()

# 添加节点
nodes = [
    ("bytedance", "字节跳动", "COMPANY", {"cn_name": "字节跳动"}),
    ("alibaba", "阿里巴巴", "COMPANY", {"cn_name": "阿里巴巴"}),
    ("tencent", "腾讯", "COMPANY", {"cn_name": "腾讯"}),
    ("baidu", "百度", "COMPANY", {"cn_name": "百度"}),
    ("huawei", "华为", "COMPANY", {"cn_name": "华为"}),
    ("zhang_yiming", "张一鸣", "PERSON", {"cn_name": "张一鸣"}),
    ("ma_yun", "马云", "PERSON", {"cn_name": "马云"}),
    ("ma_huateng", "马化腾", "PERSON", {"cn_name": "马化腾"}),
    ("li_yanhong", "李彦宏", "PERSON", {"cn_name": "李彦宏"}),
    ("doubao", "豆包", "AIMODEL", {"cn_name": "豆包"}),
    ("tongyi_qianwen", "通义千问", "AIMODEL", {"cn_name": "通义千问"}),
    ("wenxin_yiyan", "文心一言", "AIMODEL", {"cn_name": "文心一言"}),
    ("beijing", "北京", "CITY", {"cn_name": "北京"}),
    ("hangzhou", "杭州", "CITY", {"cn_name": "杭州"}),
    ("shenzhen", "深圳", "CITY", {"cn_name": "深圳"}),
]

for node_id, label, ntype, attrs in nodes:
    cg.add_node(node_id, label=label, node_type=ntype, properties=attrs)

# 添加边
edges = [
    ("bytedance", "founded_by", "zhang_yiming"),
    ("alibaba", "founded_by", "ma_yun"),
    ("tencent", "founded_by", "ma_huateng"),
    ("baidu", "founded_by", "li_yanhong"),
    ("bytedance", "develops", "doubao"),
    ("alibaba", "develops", "tongyi_qianwen"),
    ("baidu", "develops", "wenxin_yiyan"),
    ("bytedance", "located_in", "beijing"),
    ("alibaba", "located_in", "hangzhou"),
    ("tencent", "located_in", "shenzhen"),
    ("baidu", "located_in", "beijing"),
    ("bytedance", "competes_with", "alibaba"),
    ("tencent", "competes_with", "alibaba"),
    ("baidu", "competes_with", "bytedance"),
    ("huawei", "competes_with", "bytedance"),
]

for src, pred, dst in edges:
    cg.add_edge(src, dst, edge_type=pred, properties={"confidence": 0.9})

# 保存
output_path = Path(__file__).parent / "context_graph.json"
cg.save_to_file(str(output_path))

print(f"✅ ContextGraph 已保存: {output_path}")
print(f"   节点数: {len(nodes)}")
print(f"   边数: {len(edges)}")
print()

# 打印摘要
summary = cg.get_graph_summary()
print(f"图谱摘要: {summary}")
