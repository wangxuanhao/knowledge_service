"""
10 - 向量存储与 GraphRAG 混合检索
====================================
演示 Semantica 的向量存储能力，以及"向量 + 知识图谱"的
GraphRAG 混合检索架构。

位置：KG 构建之后（Enriched KG → Vector Store / Graph Store）

GraphRAG 核心思想：
  纯向量检索：擅长"语义相似"，不擅长"逻辑关联"和"多跳推理"
  纯图检索：  擅长"关系遍历"，不擅长"模糊语义匹配"
  混合检索：  两者结合，各取所长

向量存储支持的后端：
  FAISS（本地）、Qdrant、Weaviate、Milvus、Pinecone、PgVector

注意：
  本 demo 用「确定性哈希向量」演示（因为 embedding 模型需要从
  HuggingFace 下载，当前网络不通）。实际生产环境应使用：
  - fastembed（本地免费）
  - sentence-transformers（本地免费）
  - OpenAI embedding（需 API Key）

运行: python 10_向量存储与GraphRAG.py
"""

import numpy as np
import hashlib
from semantica.vector_store import FAISSStore


def print_header(title, char="═"):
    print(f"\n{char * 70}")
    print(f"  {title}")
    print(f"{char * 70}")


def section(title):
    print(f"\n┌─ {title} " + "─" * (62 - len(title)))


# ============================================================
# 简单哈希向量（确定性，无需模型，仅用于演示）
# ============================================================
def hash_embed(text, dim=128):
    """
    简单哈希向量：把文本哈希到 dim 维向量。
    语义相近的文本（共享词汇）会有相近的向量。
    仅用于演示流程，生产环境用真实 embedding。
    """
    vec = np.zeros(dim, dtype=np.float32)
    # 按词哈希
    for word in text.lower().split():
        h = hashlib.md5(word.encode()).digest()
        idx = int.from_bytes(h[:4], 'big') % dim
        vec[idx] += 1.0
    # 归一化
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec


# ============================================================
# 1. 向量存储基础操作
# ============================================================
def demo_vector_store_basic():
    section("1. 向量存储基础（add / search）")

    # 创建 FAISS 向量存储
    store = FAISSStore(dimension=128)

    # 准备文档
    documents = [
        ("doc_1", "字节跳动开发了豆包大模型，总部在北京"),
        ("doc_2", "阿里巴巴的通义千问是一款大语言模型"),
        ("doc_3", "腾讯的微信是一款社交软件"),
        ("doc_4", "字节跳动的抖音是一款短视频应用"),
        ("doc_5", "新能源汽车的电池技术正在快速发展"),
    ]

    print("│")
    print("│ 5 篇文档入库:")
    vectors = []
    ids = []
    metadata = []
    for doc_id, text in documents:
        vec = hash_embed(text)
        vectors.append(vec)
        ids.append(doc_id)
        metadata.append({"text": text})
        print(f"│   {doc_id}: {text[:30]}...")

    # 批量入库
    store.add_vectors(vectors, ids=ids, metadata=metadata)

    print("│")
    print("│ 向量库统计:")
    stats = store.get_stats()
    for k, v in stats.items():
        print(f"│   {k}: {v}")

    print("│")
    print("│ 查询：「字节跳动的大模型是什么」")
    query_vec = hash_embed("字节跳动的大模型是什么")
    results = store.search_similar(query_vec, k=3)

    print("│")
    print("│ Top 3 相似文档:")
    for i, r in enumerate(results, 1):
        doc_id = r.get('id', r.get('ids', ''))
        score = r.get('score', r.get('distance', 0))
        print(f"│   {i}. {doc_id}  (相似度 {score:.4f})")

    return store


# ============================================================
# 2. 向量检索 vs 图检索的差异
# ============================================================
def demo_vector_vs_graph():
    section("2. 向量检索 vs 图检索（核心差异）")

    print("│")
    print("│ 【向量检索擅长】语义相似")
    print("│   问：「哪家公司做短视频？」")
    print("│   向量能找到「抖音是短视频应用」这种语义相近的片段")
    print("│")
    print("│ 【向量检索不擅长】逻辑关联 / 多跳推理")
    print("│   问：「张一鸣创立的公司的竞争对手是谁？」")
    print("│   这需要：张一鸣 →(创立) 字节跳动 →(竞争) 阿里巴巴")
    print("│   向量检索只能找到「张一鸣」「字节跳动」「阿里巴巴」各自")
    print("│   的片段，但无法自动串联这个关系链条。")
    print("│")
    print("│ 【图检索擅长】多跳推理")
    print("│   图谱里有明确的三元组：")
    print("│   张一鸣 --founded_by--> 字节跳动")
    print("│   字节跳动 --competes_with--> 阿里巴巴")
    print("│   图遍历两步就能得出答案。")
    print("│")
    print("│ 【结论】GraphRAG = 向量(语义) + 图(逻辑) 混合检索")
    print("│")
    print("│   ┌─────────────┐      ┌─────────────┐")
    print("│   │ 向量检索     │      │ 图遍历       │")
    print("│   │ 找语义相近   │  +   │ 找逻辑关联   │")
    print("│   └─────────────┘      └─────────────┘")
    print("│          ↓ 融合 ↓")
    print("│   ┌─────────────────────────┐")
    print("│   │ 高质量上下文 → LLM 生成  │")
    print("│   └─────────────────────────┘")


# ============================================================
# 3. GraphRAG 混合检索完整流程
# ============================================================
def demo_graphrag_hybrid():
    section("3. GraphRAG 混合检索完整流程")

    print("│")
    print("│ 场景：回答「张一鸣创立的公司的主要竞争对手是谁？」")
    print("│")

    # 步骤1：向量检索定位入口实体
    print("│ 步骤1【向量检索】：定位问题中的关键实体")
    print("│   问题 → 提取实体：「张一鸣」「竞争对手」")
    print("│   向量匹配 → 找到「张一鸣」对应图谱节点")
    print("│")

    # 步骤2：图遍历
    print("│ 步骤2【图遍历】：沿关系边多跳推理")
    print("│   张一鸣 --founded_by--> 字节跳动")
    print("│   字节跳动 --competes_with--> 阿里巴巴")
    print("│   字节跳动 --competes_with--> 腾讯")
    print("│")
    print("│   两跳遍历，找到：阿里巴巴、腾讯")
    print("│")

    # 步骤3：上下文组装
    print("│ 步骤3【上下文组装】：合并图结果 + 向量片段")
    print("│   图结果: 阿里巴巴、腾讯（结构化关系）")
    print("│   向量片段: 两家公司的详细介绍（语义相关文本）")
    print("│")
    print("│ 步骤4【LLM 生成】：基于完整上下文回答")
    print("│   答案：字节跳动（张一鸣创立）的主要竞争对手是")
    print("│         阿里巴巴和腾讯。")
    print("│")
    print("│ ✅ 这是纯向量检索做不到的（需要跨实体多跳推理）")
    print("│   这是纯图检索做不好的（需要语义理解问题意图）")


# ============================================================
# 4. 用真实 FAISSStore 做混合检索
# ============================================================
def demo_faiss_hybrid():
    section("4. FAISS 向量存储 + 图谱节点（真实混合检索）")

    # 向量存储：存实体描述的向量
    store = FAISSStore(dimension=128)

    # 图谱实体（节点）及其描述
    entities_desc = [
        ("bytedance", "字节跳动 短视频 大模型 北京"),
        ("alibaba", "阿里巴巴 电商 大模型 杭州"),
        ("tencent", "腾讯 社交 游戏 深圳"),
        ("baidu", "百度 搜索 大模型 北京"),
        ("doubao", "豆包 大模型 AI助手"),
    ]

    vectors = []
    ids = []
    metadata = []
    for eid, desc in entities_desc:
        vec = hash_embed(desc)
        vectors.append(vec)
        ids.append(eid)
        metadata.append({"description": desc})
    store.add_vectors(vectors, ids=ids, metadata=metadata)

    print("│")
    print("│ 图谱节点已向量化入库")
    print("│")
    print("│ 混合检索演示：")
    print("│")
    print("│ 查询1：「做短视频和AI的公司」（语义模糊查询）")
    q1 = hash_embed("短视频 人工智能 大模型")
    r1 = store.search_similar(q1, k=3)
    print("│   向量检索 Top3:")
    for i, r in enumerate(r1, 1):
        eid = r.get('id', '')
        score = r.get('score', 0)
        print(f"│     {i}. {eid} (相似度 {score:.4f})")
    print("│   → 找到 bytedance（字节跳动：短视频+大模型）")
    print("│")
    print("│ 查询2：「电商平台」（精确语义）")
    q2 = hash_embed("电商 购物 平台")
    r2 = store.search_similar(q2, k=2)
    print("│   向量检索 Top2:")
    for i, r in enumerate(r2, 1):
        eid = r.get('id', '')
        score = r.get('score', 0)
        print(f"│     {i}. {eid} (相似度 {score:.4f})")
    print("│   → 找到 alibaba（阿里巴巴：电商）")
    print("│")
    print("│ 然后在这些向量命中的节点上做图遍历，就得到完整答案")


# ============================================================
# 主函数
# ============================================================
def main():
    print_header("向量存储篇：Vector Store + GraphRAG 混合检索")
    print("""
Semantica 的向量存储是 GraphRAG 架构的"语义层"。
官网支持 6 种后端：FAISS / Pinecone / Weaviate / Qdrant / Milvus / PgVector

⚠️ 说明：本 demo 用确定性哈希向量演示（embedding 模型需从
   HuggingFace 下载，当前网络不通）。生产环境替换为：
   from semantica.embeddings import EmbeddingGenerator
   gen = EmbeddingGenerator(provider='fastembed')  # 本地免费
""")

    demo_vector_store_basic()
    demo_vector_vs_graph()
    demo_graphrag_hybrid()
    demo_faiss_hybrid()

    print_header("向量存储篇总结", char="═")
    print("""
📌 GraphRAG 混合检索架构：

  用户提问
     │
     ├──→【向量检索】找到语义相近的实体/片段
     │
     ├──→【图遍历】沿关系边多跳推理
     │
     └──→【融合】结构化关系 + 语义文本 = 完整上下文
                        │
                        ▼
                  LLM 生成答案

📌 向量 vs 图 对比：
  ┌──────────────┬──────────────────┬──────────────────┐
  │ 能力          │ 向量检索          │ 图遍历            │
  ├──────────────┼──────────────────┼──────────────────┤
  │ 语义相似      │ ✅ 强             │ ❌ 弱             │
  │ 逻辑关联      │ ❌ 弱             │ ✅ 强             │
  │ 多跳推理      │ ❌ 不支持         │ ✅ 支持           │
  │ 模糊匹配      │ ✅ 强             │ ❌ 弱             │
  │ 精确关系      │ ❌ 弱             │ ✅ 强             │
  │ 可解释性      │ ⭐ 中            │ ✅ 强（路径可见）  │
  └──────────────┴──────────────────┴──────────────────┘

📌 生产环境 embedding 选择：
  fastembed / sentence-transformers：本地免费，中文用 bge-m3
  OpenAI embedding：质量高，需 API Key，按 token 计费
""")


if __name__ == "__main__":
    main()
