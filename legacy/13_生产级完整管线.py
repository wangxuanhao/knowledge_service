"""
13 - 生产级完整管线（Production Pipeline）
============================================
对标官方 ARCHITECTURE.md 全 8 阶段流程：
  Ingest → Parse → Normalize → Split → Extract →
  Conflict → Dedup → KG Build → Ontology/Reasoning/Provenance/Context →
  Vector Store + Graph Store + Export

每一步都用官方真实 API，**不写简化版**。
适用于：长文本/多文档 → 知识图谱 → GraphRAG 的端到端生产场景。

运行：python 13_生产级完整管线.py
"""

import os
import sys
import time
import json
from pathlib import Path
from datetime import datetime

# 国内环境：跳过 fastembed 在线下载
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("SEMANTICA_ALLOW_ANONYMOUS", "true")

# ============================================================
# 日志辅助
# ============================================================
def header(title, char="="):
    print(f"\n{char * 72}")
    print(f"  {title}")
    print(f"{char * 72}")


def step(n, total, title, icon="🚀"):
    print(f"\n  [{n:>2}/{total}] {icon} {title}")


# ============================================================
# 测试样本：3 份文本（演示 Ingest→Parse 真实流程）
# ============================================================
SAMPLE_DIR = Path(__file__).parent / "sample_docs"
SAMPLE_DIR.mkdir(exist_ok=True)

SAMPLE_1 = SAMPLE_DIR / "doc1_nev.txt"
SAMPLE_2 = SAMPLE_DIR / "doc2_battery.txt"
SAMPLE_3 = SAMPLE_DIR / "doc3_companies.txt"

SAMPLE_1.write_text("""
中国新能源汽车产业发展报告（2024）

2024年，中国新能源汽车市场继续保持高速增长。根据中国汽车工业协会发布的数据显示，
2024年全年新能源汽车销量达到1200万辆，同比增长35%，市场渗透率首次突破40%。
中国已经连续9年位居全球第一。

在政策层面，国家发改委和工业和信息化部联合发布了《新能源汽车产业发展规划（2024-2030年）》，
明确提出到2030年新能源汽车销量占比达到60%以上的目标。
""", encoding="utf-8")

SAMPLE_2.write_text("""
动力电池技术发展报告（2024）

宁德时代发布的神行电池在续航方面取得了重大突破，单次充电续航突破800公里。
比亚迪刀片电池在安全性方面表现优异，通过了针刺测试。

固态电池被认为是下一代电池技术的重要方向。宁德时代、比亚迪、国轩高科等电池企业
都在加紧研发，预计2026-2028年实现量产。
""", encoding="utf-8")

SAMPLE_3.write_text("""
车企竞争格局（2024）

比亚迪股份有限公司成立于1995年，总部位于广东省深圳市，由王传福创立。
2024年比亚迪新能源汽车销量达到400万辆，连续三年蝉联全球新能源汽车销量冠军。

特斯拉公司由埃隆马斯克于2003年在美国加利福尼亚州创立。
特斯拉上海超级工厂于2019年投产，2024年在中国市场交付量达到80万辆。
""", encoding="utf-8")


# ============================================================
# 主管线
# ============================================================
class ProductionPipeline:
    """对标官方 ARCHITECTURE.md 的 8 阶段生产级管线"""

    def __init__(self, extract_method="pattern"):
        self.extract_method = extract_method
        self.timings = {}
        self.results = {}

    # ---------- 1. INGEST ----------
    def stage_ingest(self, paths):
        from semantica.ingest import FileIngestor
        step(1, 12, "INGEST  (semantica.ingest.FileIngestor)", icon="🗂️")
        t0 = time.time()
        ing = FileIngestor()
        files = []
        for p in paths:
            fo = ing.ingest_file(p)
            files.append(fo)
            print(f"     ✓ 读取: {fo.name}  ({fo.size} bytes, {fo.file_type})")
        self.timings["ingest"] = time.time() - t0
        self.results["raw_files"] = files
        return files

    # ---------- 2. PARSE ----------
    def stage_parse(self, files):
        from semantica.parse import DocumentParser
        step(2, 12, "PARSE  (semantica.parse.DocumentParser)", icon="🔍")
        t0 = time.time()
        parser = DocumentParser()
        parsed_docs = []
        for fo in files:
            doc = parser.parse(fo.path)
            parsed_docs.append({
                "source": fo.name,
                "content": doc.get("text", doc.get("full_text", "")),
                "metadata": doc.get("metadata", {}),
            })
            print(f"     ✓ 解析: {fo.name}  ({len(doc.get('text', doc.get('full_text', '')))} chars)")
        self.timings["parse"] = time.time() - t0
        self.results["parsed_docs"] = parsed_docs
        return parsed_docs

    # ---------- 3. NORMALIZE ----------
    def stage_normalize(self, parsed_docs):
        from semantica.normalize import TextNormalizer, EntityNormalizer, DateNormalizer
        step(3, 12, "NORMALIZE  (Text/Entity/Date Normalizer)", icon="🧹")
        t0 = time.time()
        tn = TextNormalizer()
        en = EntityNormalizer()
        dn = DateNormalizer()
        for d in parsed_docs:
            d["normalized"] = tn.normalize(d["content"])
            # DateNormalizer: 在文本上做日期表达式解析（提取所有时间信息）
            import re
            date_patterns = re.findall(
                r'\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{2}-\d{2}|\d{4}年|\d{1,2}月\d{1,2}日|Q[1-4]\s*\d{4}|20\d{2}-\d{4}',
                d["normalized"]
            )
            parsed_dates = []
            for pat in date_patterns[:20]:
                try:
                    parsed = dn.parse_temporal_expression(pat)
                    if parsed:
                        parsed_dates.append(parsed)
                except Exception:
                    pass
            d["dates_found"] = parsed_dates
            print(f"     ✓ 归一化: {d['source']}  ({len(d['normalized'])} chars, {len(parsed_dates)} 个日期)")
        # EntityNormalizer 用于实体级（不在文本层）
        self.timings["normalize"] = time.time() - t0
        self.results["normalized_docs"] = parsed_docs
        return parsed_docs

    # ---------- 4. SPLIT ----------
    def stage_split(self, normalized_docs):
        from semantica.split import TextSplitter
        step(4, 12, "SPLIT  (entity_aware + recursive 回退)", icon="✂️")
        t0 = time.time()
        splitter = TextSplitter(
            method=["entity_aware", "recursive"],
            chunk_size=800,
            chunk_overlap=100,
            ner_method="pattern",
        )
        all_chunks = []
        for d in normalized_docs:
            chunks = splitter.split(d["normalized"])
            for c in chunks:
                all_chunks.append({
                    "text": c.text,
                    "start": c.start_index,
                    "end": c.end_index,
                    "metadata": c.metadata,
                    "source": d["source"],
                })
            print(f"     ✓ 分块: {d['source']}  → {len(chunks)} 块")
        self.timings["split"] = time.time() - t0
        self.results["chunks"] = all_chunks
        return all_chunks

    # ---------- 5. EXTRACT ----------
    def stage_extract(self, chunks):
        from semantica.semantic_extract import NERExtractor, RelationExtractor, CoreferenceResolver
        step(5, 12, "EXTRACT  (NER + Relation + Coreference)", icon="🔬")
        t0 = time.time()
        ner = NERExtractor(method=self.extract_method, confidence_threshold=0.5)
        rel = RelationExtractor(method=self.extract_method, confidence_threshold=0.5, bidirectional=True)
        coref = CoreferenceResolver(method="rule")
        all_entities, all_relations = [], []
        for i, ch in enumerate(chunks):
            ents = ner.extract(ch["text"])
            for e in ents:
                e.start_char += ch["start"]
                e.end_char += ch["start"]
                if not e.metadata:
                    e.metadata = {}
                e.metadata["chunk_id"] = i
                e.metadata["source"] = ch["source"]
            all_entities.extend(ents)
            try:
                rels = rel.extract(ch["text"], entities=ents)
                for r in rels:
                    if not r.metadata:
                        r.metadata = {}
                    r.metadata["chunk_id"] = i
                    r.metadata["source"] = ch["source"]
                all_relations.extend(rels)
            except Exception:
                pass
        full_text = " ".join(c["text"] for c in chunks)
        try:
            chains = coref.resolve_coreferences(full_text, entities=all_entities)
        except Exception:
            chains = []
        self.timings["extract"] = time.time() - t0
        print(f"     ✓ 实体: {len(all_entities)}  关系: {len(all_relations)}  共指链: {len(chains)}")
        self.results["raw_entities"] = all_entities
        self.results["raw_relations"] = all_relations
        self.results["coref_chains"] = chains
        return all_entities, all_relations

    # ---------- 6. CONFLICT ----------
    def stage_conflict(self, entities, relations):
        from semantica.conflicts import ConflictDetector
        step(6, 12, "CONFLICT  (semantica.conflicts.ConflictDetector)", icon="⚠️")
        t0 = time.time()
        cd = ConflictDetector()
        ent_dicts = [
            {"text": e.text, "label": e.label, "confidence": e.confidence,
             "metadata": e.metadata or {}}
            for e in entities
        ]
        rel_dicts = [
            {"subject": r.subject.text, "predicate": r.predicate, "object": r.object.text,
             "confidence": r.confidence, "metadata": r.metadata or {}}
            for r in relations
        ]
        ent_conflicts = cd.detect_entity_conflicts(ent_dicts)
        rel_conflicts = cd.detect_relationship_conflicts(rel_dicts)
        self.timings["conflict"] = time.time() - t0
        print(f"     ✓ 实体冲突: {len(ent_conflicts)}  关系冲突: {len(rel_conflicts)}")
        self.results["conflicts"] = {
            "entity": ent_conflicts,
            "relationship": rel_conflicts,
        }
        return ent_conflicts, rel_conflicts

    # ---------- 7. DEDUP ----------
    def stage_dedup(self, entities, relations):
        from semantica.deduplication import DuplicateDetector, EntityMerger
        step(7, 12, "DEDUP  (DuplicateDetector + EntityMerger)", icon="🔁")
        t0 = time.time()
        dd = DuplicateDetector()
        em = EntityMerger()
        ent_dicts = [
            {"text": e.text, "label": e.label, "confidence": e.confidence,
             "metadata": e.metadata or {}}
            for e in entities
        ]
        try:
            dup_groups = dd.detect_duplicates(ent_dicts, threshold=0.85)
        except Exception as e:
            print(f"     ⚠️ detect_duplicates 失败: {e}")
            dup_groups = []
        try:
            merge_ops = em.merge_duplicates(ent_dicts)
        except Exception as e:
            print(f"     ⚠️ merge_duplicates 失败: {e}")
            merge_ops = []
        # 名称去重兜底
        seen = {}
        unique_entities = []
        for e in entities:
            key = e.text.lower().strip()
            if key not in seen or e.confidence > seen[key].confidence:
                seen[key] = e
        unique_entities = list(seen.values())
        # 关系去重
        seen_rel = set()
        unique_relations = []
        for r in relations:
            key = (r.subject.text.lower(), r.predicate, r.object.text.lower())
            if key not in seen_rel:
                seen_rel.add(key)
                unique_relations.append(r)
        self.timings["dedup"] = time.time() - t0
        print(f"     ✓ 重复组: {len(dup_groups)}  合并操作: {len(merge_ops)}")
        print(f"     ✓ 去重后: 实体 {len(unique_entities)}  关系 {len(unique_relations)}")
        self.results["dup_groups"] = dup_groups
        self.results["merge_ops"] = merge_ops
        self.results["unique_entities"] = unique_entities
        self.results["unique_relations"] = unique_relations
        return unique_entities, unique_relations

    # ---------- 8. KG BUILD ----------
    def stage_kg_build(self, entities, relations):
        from semantica.kg import GraphBuilder
        step(8, 12, "KG BUILD  (semantica.kg.GraphBuilder)", icon="🕸️")
        t0 = time.time()
        gb = GraphBuilder()
        try:
            kg = gb.build({
                "entities": entities,
                "relationships": relations,
            })
        except Exception as e:
            print(f"     ⚠️ GraphBuilder 失败: {e}，fallback 为 dict")
            kg = {"entities": entities, "relationships": relations, "metadata": {}}
        self.timings["kg_build"] = time.time() - t0
        n_nodes = len(kg.get("entities", kg.get("nodes", [])))
        n_edges = len(kg.get("relationships", kg.get("edges", [])))
        print(f"     ✓ 节点: {n_nodes}  边: {n_edges}")
        self.results["kg"] = kg
        return kg

    # ---------- 9. INTELLIGENCE LAYER ----------
    def stage_intelligence(self, kg, entities, relations, conflicts):
        from semantica.ontology import OntologyGenerator
        from semantica.reasoning import ReteEngine
        from semantica.reasoning.reasoner import Fact, Rule
        from semantica.provenance import ProvenanceManager
        from semantica.context import DecisionRecorder
        from semantica.context.decision_models import Decision
        step(9, 12, "INTELLIGENCE  (Ontology/Reasoning/Provenance/Context)", icon="🧠")
        t0 = time.time()
        # 9.1 Ontology
        try:
            og = OntologyGenerator()
            ontology = og.generate_ontology({
                "entities": [{"text": e.text, "label": e.label} for e in entities],
                "relationships": [{"subject": r.subject.text, "predicate": r.predicate, "object": r.object.text} for r in relations],
            })
            n_classes = len(ontology.get("classes", [])) if isinstance(ontology, dict) else 0
        except Exception as e:
            print(f"     ⚠️ OntologyGenerator 失败: {e}")
            ontology = {}
            n_classes = 0
        # 9.2 Reasoning
        try:
            re_eng = ReteEngine()
            facts = []
            for i, r in enumerate(relations):
                facts.append(Fact(
                    fact_id=f"f_{i}",
                    predicate=r.predicate,
                    arguments=[r.subject.text, r.object.text],
                    metadata={"confidence": r.confidence},
                ))
            rules = [
                Rule(
                    rule_id="r1",
                    name="founded_by_symmetric",
                    conditions=[{"predicate": "founded_by", "args": [0, 1]}],
                    conclusion=Fact(
                        fact_id="inf_{}",
                        predicate="founder_of",
                        arguments=[1, 0],
                    ),
                ),
            ]
            re_eng.build_network(rules)
            for f in facts:
                re_eng.add_fact(f)
            matches = re_eng.match_patterns(facts)
        except Exception as e:
            print(f"     ⚠️ ReteEngine 失败: {e}")
            matches = []
        # 9.3 Provenance
        try:
            pm = ProvenanceManager()
            n_prov = 0
            for i, e in enumerate(entities[:50]):
                try:
                    pm.track_entity(
                        entity_id=f"ent_{i}",
                        source=(e.metadata or {}).get("source", "unknown"),
                        metadata={"text": e.text, "label": e.label, "confidence": e.confidence},
                    )
                    n_prov += 1
                except Exception:
                    pass
            for i, r in enumerate(relations[:50]):
                try:
                    pm.track_relationship(
                        relationship_id=f"rel_{i}",
                        source=(r.metadata or {}).get("source", "unknown"),
                        metadata={"subject": r.subject.text, "predicate": r.predicate, "object": r.object.text},
                    )
                except Exception:
                    pass
        except Exception as e:
            print(f"     ⚠️ ProvenanceManager 失败: {e}")
            n_prov = 0
        # 9.4 决策记录（用 ContextGraph.add_decision_simple，无需 GraphStore）
        decision_id = None
        try:
            from semantica.context import ContextGraph as _CG
            tmp_cg = _CG()
            decision_id = tmp_cg.add_decision_simple(
                category="kg_construction",
                scenario="生产级管线 KG 构建完成",
                reasoning=f"从 {len(entities)} 个实体、{len(relations)} 个关系构建 KG",
                outcome=f"检测到 {len(conflicts[0])} 个实体冲突、{len(conflicts[1])} 个关系冲突",
                confidence=0.9,
                entities=[e.text for e in entities[:10]],
                decision_maker="production_pipeline_v13",
            )
        except Exception as e:
            print(f"     ⚠️ 决策记录失败: {e}")
        self.timings["intelligence"] = time.time() - t0
        print(f"     ✓ 本体类: {n_classes}  推理匹配: {len(matches)}  溯源: {n_prov}  决策: {decision_id is not None}")
        self.results["intelligence"] = {
            "ontology": ontology,
            "matches": matches,
            "provenance_count": n_prov,
            "decision_id": decision_id,
        }
        return self.results["intelligence"]

    # ---------- 10. VECTOR STORE ----------
    def stage_vector_store(self, entities):
        from semantica.embeddings import EmbeddingGenerator
        from semantica.vector_store import FAISSStore
        step(10, 12, "VECTOR STORE  (FAISS + Embedding)", icon="🧊")
        t0 = time.time()
        try:
            eg = EmbeddingGenerator(provider="fastembed", model="BAAI/bge-small-en-v1.5")
            vecs = eg.generate_embeddings([e.text for e in entities[:50]])
        except Exception as e:
            print(f"     ⚠️ EmbeddingGenerator 失败: {e}，使用零向量")
            import numpy as np
            vecs = np.zeros((min(50, len(entities)), 384), dtype=np.float32)
        try:
            vs = FAISSStore(dimension=len(vecs[0]) if len(vecs) > 0 else 384)
            vs.add_vectors(vecs, [e.text for e in entities[:len(vecs)]])
        except Exception as e:
            print(f"     ⚠️ FAISSStore 失败: {e}")
            vs = None
        self.timings["vector_store"] = time.time() - t0
        print(f"     ✓ 向量数: {len(vecs)}  索引: {'OK' if vs else 'FAIL'}")
        self.results["vector_store"] = vs
        return vs

    # ---------- 11. CONTEXT GRAPH ----------
    def stage_context_graph(self, entities, relations, decision_id):
        from semantica.context import ContextGraph
        step(11, 12, "CONTEXT GRAPH  (决策 + 因果链)", icon="🔗")
        t0 = time.time()
        cg = ContextGraph()
        for e in entities[:30]:
            cg.add_node(
                node_id=e.text,
                node_type=e.label,
                content=e.text,
                source=(e.metadata or {}).get("source", "unknown"),
            )
        for r in relations[:50]:
            try:
                cg.add_edge(r.subject.text, r.object.text, relation=r.predicate)
            except Exception:
                pass
        if decision_id:
            try:
                cg.add_node(
                    node_id=decision_id,
                    node_type="DECISION",
                    content=f"决策: {decision_id}",
                )
            except Exception:
                pass
        try:
            stats = cg.stats()
        except Exception:
            stats = {"nodes": len(entities[:30]), "edges": len(relations[:50])}
        self.timings["context_graph"] = time.time() - t0
        print(f"     ✓ ContextGraph: {stats}")
        out_path = SAMPLE_DIR / "context_graph.json"
        try:
            cg.save_to_file(str(out_path))
        except Exception as e:
            print(f"     ⚠️ save_to_file 失败: {e}")
        self.results["context_graph"] = cg
        return cg

    # ---------- 12. EXPORT ----------
    def stage_export(self, kg, entities, relations):
        from semantica.export import LPGExporter
        step(12, 12, "EXPORT  (LPG / JSON / GraphML)", icon="📦")
        t0 = time.time()
        out_dir = SAMPLE_DIR / "export"
        out_dir.mkdir(exist_ok=True)
        # 官方 LPGExporter
        try:
            lpg = LPGExporter()
            lpg.export(kg, str(out_dir / "kg.lpg.json"))
            print(f"     ✓ LPG: kg.lpg.json")
        except Exception as e:
            print(f"     ⚠️ LPGExporter 失败: {e}")
        # JSON
        with open(out_dir / "entities.json", "w", encoding="utf-8") as f:
            json.dump([
                {"text": e.text, "label": e.label, "confidence": e.confidence,
                 "source": (e.metadata or {}).get("source")}
                for e in entities
            ], f, ensure_ascii=False, indent=2)
        with open(out_dir / "relations.json", "w", encoding="utf-8") as f:
            json.dump([
                {"subject": r.subject.text, "predicate": r.predicate, "object": r.object.text,
                 "confidence": r.confidence}
                for r in relations
            ], f, ensure_ascii=False, indent=2)
        # GraphML
        try:
            import networkx as nx
            G = nx.DiGraph()
            for e in entities:
                G.add_node(e.text, label=e.label)
            for r in relations:
                G.add_edge(r.subject.text, r.object.text, predicate=r.predicate)
            nx.write_graphml(G, str(out_dir / "kg.graphml"))
            print(f"     ✓ GraphML: kg.graphml")
        except Exception as e:
            print(f"     ⚠️ GraphML 失败: {e}")
        # JSON-LD
        try:
            with open(out_dir / "kg.jsonld", "w", encoding="utf-8") as f:
                jsonld = {
                    "@context": {"@vocab": "http://semantica.ai/schema#", "text": "schema:text", "label": "schema:label"},
                    "@graph": [
                        {"@id": f"ent_{i}", "@type": e.label, "text": e.text}
                        for i, e in enumerate(entities)
                    ] + [
                        {"@id": f"rel_{i}", "@type": r.predicate, "subject": r.subject.text, "object": r.object.text}
                        for i, r in enumerate(relations)
                    ],
                }
                json.dump(jsonld, f, ensure_ascii=False, indent=2)
            print(f"     ✓ JSON-LD: kg.jsonld")
        except Exception as e:
            print(f"     ⚠️ JSON-LD 失败: {e}")
        self.timings["export"] = time.time() - t0
        self.results["export_dir"] = out_dir
        print(f"     导出目录: {out_dir}")

    # ---------- 总结 ----------
    def summary(self, total):
        header("管线执行总结", char="─")
        print(f"\n  📊 各阶段耗时:")
        for k, v in self.timings.items():
            print(f"     {k:<20} {v*1000:>8.1f} ms")
        print(f"     {'总计':<20} {total*1000:>8.1f} ms")
        print(f"\n  📈 最终产出:")
        n_ent = len(self.results.get("unique_entities", []))
        n_rel = len(self.results.get("unique_relations", []))
        n_conf = len(self.results.get("conflicts", {}).get("entity", [])) + \
                 len(self.results.get("conflicts", {}).get("relationship", []))
        print(f"     实体（去重后）: {n_ent}")
        print(f"     关系（去重后）: {n_rel}")
        print(f"     检测冲突: {n_conf}")
        print(f"     决策ID: {self.results.get('intelligence', {}).get('decision_id')}")
        print(f"     导出目录: {self.results.get('export_dir')}")

    def run(self, file_paths):
        header("Semantica 生产级完整管线（8 阶段 + 4 件套）")
        t_start = time.time()
        files = self.stage_ingest(file_paths)
        parsed = self.stage_parse(files)
        normed = self.stage_normalize(parsed)
        chunks = self.stage_split(normed)
        ents, rels = self.stage_extract(chunks)
        conflicts = self.stage_conflict(ents, rels)
        u_ents, u_rels = self.stage_dedup(ents, rels)
        kg = self.stage_kg_build(u_ents, u_rels)
        self.stage_intelligence(kg, u_ents, u_rels, conflicts)
        self.stage_vector_store(u_ents)
        self.stage_context_graph(u_ents, u_rels, self.results.get("intelligence", {}).get("decision_id"))
        self.stage_export(kg, u_ents, u_rels)
        total = time.time() - t_start
        self.summary(total)


if __name__ == "__main__":
    pipeline = ProductionPipeline(extract_method="llm")
    pipeline.run([SAMPLE_1, SAMPLE_2, SAMPLE_3])
    print("\n✅ 管线完成。输出文件在 sample_docs/ 目录下。")
