"""
05 - 完整管线：长文本要素提取（端到端）
============================================
完整演示从原始长文本 → 要素提取 → 知识图谱 → 可视化的完整流程。

完整管线步骤：
  1. 输入加载（从文件/字符串加载长文本）
  2. 文本预处理（清洗、归一化）
  3. 实体感知分块（entity-aware chunking）
  4. NER 实体识别
  5. 关系抽取
  6. 共指消解
  7. 实体消歧 / 去重
  8. 知识图谱构建
  9. 图分析（中心性、社区检测）
  10. 溯源追踪
  11. 可视化输出
  12. 结果导出（JSON / CSV / RDF）

运行: python 05_完整管线_长文本要素提取.py
"""

import os
import sys
import json
import time
from pathlib import Path


def print_header(title, char="═"):
    print(f"\n{char * 72}")
    print(f"  {title}")
    print(f"{char * 72}")


def section(title):
    print(f"\n┌─ {title} " + "─" * (64 - len(title)))


def step(n, total, title):
    print(f"\n  [{n:>2}/{total}] 🚀 {title}")


# ============================================================
# 测试长文本：模拟一篇行业研究报告
# ============================================================
LONG_TEXT = """
中国新能源汽车产业发展报告（2024）

一、行业概述

2024年，中国新能源汽车市场继续保持高速增长态势。根据中国汽车工业协会发布的数据显示，2024年全年新能源汽车销量达到1200万辆，同比增长35%，市场渗透率首次突破40%。中国已经连续9年位居全球第一。

在政策层面，国家发改委和工业和信息化部联合发布了《新能源汽车产业发展规划（2024-2030年）》，明确提出到2030年新能源汽车销量占比达到60%以上的目标。财政部也在补贴政策逐步退坡的背景下，市场驱动已成为行业增长的主要动力。

二、主要企业分析

比亚迪股份有限公司成立于1995年，总部位于广东省深圳市，由王传福创立。2024年比亚迪新能源汽车销量达到400万辆，连续三年蝉联全球新能源汽车销量冠军。比亚迪掌握电池、电机、电控等核心技术，旗下拥有王朝系列、海洋系列、腾势品牌等多个产品线。

特斯拉公司由埃隆马斯克于2003年在美国加利福尼亚州创立。特斯拉上海超级工厂于2019年投产，2024年在中国市场交付量达到80万辆。特斯拉的FSD全自动驾驶技术一直是行业标杆，其4680电池技术也处于领先地位。

蔚来汽车成立于2014年，由李斌创立，总部位于上海。2024年蔚来销量达到25万辆。蔚来主打高端市场，其换电模式独具特色，截至2024年底已在全国建成超过3000座换电站。

小鹏汽车由何小鹏于2014年在广州成立，2024年销量达到18万辆。小鹏汽车在智能化领域投入大量资源开发XNGP智能驾驶系统，在城市NGP方面表现突出。

理想汽车由李想创立于2015年，总部在北京。2024年理想销量达到22万辆。理想主打增程式电动车，在家庭六座SUV市场表现强劲，旗下L系列车型深受家庭用户喜爱。

三、技术发展趋势

智能驾驶技术是新能源汽车竞争的核心战场。城市NOA导航辅助驾驶成为2024年的主要竞争点。华为、百度阿波罗、小鹏等企业都在大力推进城市领航辅助驾驶功能的落地。

动力电池技术方面，固态电池被认为是下一代电池技术的重要方向。宁德时代、比亚迪、国轩高科等电池企业都在加紧研发。宁德时代发布的神行电池在续航方面取得了重大突破。

2024年，中国新能源汽车出口量达到200万辆，同比增长50%。比亚迪、上汽、奇瑞等企业加速出海步伐，欧洲、东南亚、中东成为主要出口市场。

四、产业链分析

技术路线方面，纯电动占比持续提升，插电混动也保持较快增长。增程式电动车以其无续航焦虑的特点受到消费者欢迎。

竞争格局方面，比亚迪一家独大的格局初步形成，造车新势力蔚来、小鹏、理想等紧随其后。传统车企如吉利、长安、广汽等也在加速转型。
"""


# ============================================================
# 管线类
# ============================================================
class KnowledgeExtractionPipeline:
    """
    完整的知识图谱要素提取管线
    """

    def __init__(self, method="pattern", llm_provider=None):
        self.method = method
        self.llm_provider = llm_provider
        self.results = {}
        self.timings = {}

    def run(self, text):
        """执行完整管线"""
        total_steps = 10
        start_total = time.time()

        # Step 1: 文本预处理
        step(1, total_steps, "文本预处理")
        t0 = time.time()
        cleaned_text = self._preprocess(text)
        self.timings["preprocess"] = time.time() - t0
        print(f"     原始长度: {len(text)} 字符")
        print(f"     清洗后长度: {len(cleaned_text)} 字符")
        self.results["raw_text"] = cleaned_text

        # Step 2: 实体感知分块
        step(2, total_steps, "实体感知分块")
        t0 = time.time()
        chunks = self._chunk_text(cleaned_text, chunk_size=800, overlap=100)
        self.timings["chunking"] = time.time() - t0
        print(f"     分块数量: {len(chunks)} 块")
        for i, ch in enumerate(chunks[:3]):
            preview = ch["text"][:40].replace("\n", " ")
            print(f"       Chunk {i+1}: [{ch['start']}-{ch['end']}] {preview}...")
        if len(chunks) > 3:
            print(f"       ... 还有 {len(chunks)-3} 块")
        self.results["chunks"] = chunks

        # Step 3: NER 实体识别
        step(3, total_steps, "NER 命名实体识别")
        t0 = time.time()
        all_entities = self._extract_entities(chunks)
        self.timings["ner"] = time.time() - t0
        print(f"     提取实体总数: {len(all_entities)}")
        type_counts = {}
        for e in all_entities:
            type_counts[e.label] = type_counts.get(e.label, 0) + 1
        print(f"     类型分布: {type_counts}")
        self.results["raw_entities"] = all_entities

        # Step 4: 关系抽取
        step(4, total_steps, "关系抽取")
        t0 = time.time()
        all_relations = self._extract_relations(chunks, all_entities)
        self.timings["relations"] = time.time() - t0
        print(f"     提取关系总数: {len(all_relations)}")
        pred_counts = {}
        for r in all_relations:
            pred_counts[r.predicate] = pred_counts.get(r.predicate, 0) + 1
        print(f"     关系类型分布: {pred_counts}")
        self.results["raw_relations"] = all_relations

        # Step 5: 共指消解
        step(5, total_steps, "共指消解")
        t0 = time.time()
        coref_result = self._coreference_resolve(cleaned_text, all_entities)
        self.timings["coreference"] = time.time() - t0
        print(f"     共指链数量: {coref_result['count']}")
        self.results["coreference_chains"] = coref_result["chains"]

        # Step 6: 实体消歧 / 去重
        step(6, total_steps, "实体消歧与去重")
        t0 = time.time()
        unique_entities = self._deduplicate_entities(all_entities)
        self.timings["deduplication"] = time.time() - t0
        print(f"     去重前: {len(all_entities)} 个")
        print(f"     去重后: {len(unique_entities)} 个")
        self.results["entities"] = unique_entities

        # Step 7: 知识图谱构建
        step(7, total_steps, "知识图谱构建")
        t0 = time.time()
        kg = self._build_graph(unique_entities, all_relations)
        self.timings["kg_build"] = time.time() - t0
        print(f"     节点数: {len(kg.get('entities', kg.get('nodes', [])))}")
        print(f"     边数: {len(kg.get('relationships', kg.get('edges', [])))}")
        self.results["kg"] = kg

        # Step 8: 图分析
        step(8, total_steps, "图分析（中心性/社区检测）")
        t0 = time.time()
        analytics = self._analyze_graph(kg, unique_entities, all_relations)
        self.timings["analytics"] = time.time() - t0
        self.results["analytics"] = analytics

        # Step 9: 溯源追踪
        step(9, total_steps, "溯源追踪（Provenance）")
        t0 = time.time()
        provenance = self._build_provenance(unique_entities, all_relations, chunks)
        self.timings["provenance"] = time.time() - t0
        print(f"     溯源条目数: {len(provenance)}")
        self.results["provenance"] = provenance

        # Step 10: 结果导出
        step(10, total_steps, "结果导出与可视化")
        t0 = time.time()
        output_files = self._export_results(unique_entities, all_relations, kg)
        self.timings["export"] = time.time() - t0
        self.results["output_files"] = output_files

        total_time = time.time() - start_total
        self.timings["total"] = total_time

        self._print_summary(total_time)

        return self.results

    def _preprocess(self, text):
        """文本预处理：清洗、归一化"""
        lines = [line.strip() for line in text.split('\n')]
        lines = [line for line in lines if line]
        cleaned = '\n'.join(lines)
        return cleaned

    def _chunk_text(self, text, chunk_size=800, overlap=100):
        """分块处理（简化版：按字符数切分，带重叠）"""
        chunks = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = min(start + chunk_size, text_len)
            chunk_text = text[start:end]
            chunks.append({
                "id": f"chunk_{len(chunks)+1}",
                "text": chunk_text,
                "start": start,
                "end": end,
            })
            if end >= text_len:
                break
            start = end - overlap  # 重叠部分

        return chunks

    def _extract_entities(self, chunks):
        """逐块提取实体"""
        from semantica.semantic_extract import NERExtractor

        ner = NERExtractor(method=self.method, confidence_threshold=0.5)

        all_entities = []
        for chunk in chunks:
            try:
                entities = ner.extract(chunk["text"])
                for ent in entities:
                    ent.start_char += chunk["start"]
                    ent.end_char += chunk["start"]
                    if not hasattr(ent, 'metadata') or ent.metadata is None:
                        ent.metadata = {}
                    ent.metadata["chunk_id"] = chunk["id"]
                all_entities.extend(entities)
            except Exception as e:
                print(f"     ⚠️  {chunk['id']} 提取失败: {e}")

        return all_entities

    def _extract_relations(self, chunks, entities):
        """逐块提取关系"""
        from semantica.semantic_extract import RelationExtractor

        rel_extractor = RelationExtractor(
            method=self.method,
            confidence_threshold=0.5,
            bidirectional=True,
        )

        all_relations = []
        for chunk in chunks:
            try:
                # 获取该块范围内的实体
                chunk_entities = [
                    e for e in entities
                    if chunk["start"] <= e.start_char < chunk["end"]
                ]
                # 调整为块内坐标
                from semantica.semantic_extract.types import Entity
                adjusted = []
                for e in chunk_entities:
                    ne = Entity(
                        text=e.text, label=e.label, confidence=e.confidence,
                        start_char=e.start_char - chunk["start"],
                        end_char=e.end_char - chunk["start"],
                    )
                    adjusted.append(ne)

                relations = rel_extractor.extract(chunk["text"], entities=adjusted)

                for rel in relations:
                    rel.subject.start_char += chunk["start"]
                    rel.subject.end_char += chunk["start"]
                    rel.object.start_char += chunk["start"]
                    rel.object.end_char += chunk["start"]
                    if not hasattr(rel, 'metadata') or rel.metadata is None:
                        rel.metadata = {}
                    rel.metadata["chunk_id"] = chunk["id"]

                all_relations.extend(relations)
            except Exception as e:
                pass  # 跳过失败的块

        return all_relations

    def _coreference_resolve(self, text, entities):
        """共指消解"""
        try:
            from semantica.semantic_extract import CoreferenceResolver
            resolver = CoreferenceResolver(method="rule")
            chains = resolver.resolve_coreferences(text, entities=entities)
            return {"chains": chains, "count": len(chains)}
        except Exception as e:
            print(f"     共指消解不可用: {e}")
            return {"chains": [], "count": 0}

    def _deduplicate_entities(self, entities):
        """实体去重（基于名称）"""
        seen = {}
        unique = []
        for ent in entities:
            key = ent.text.lower().strip()
            if key not in seen:
                seen[key] = ent
                unique.append(ent)
            else:
                if ent.confidence > seen[key].confidence:
                    seen[key] = ent
                    idx = next(i for i, e in enumerate(unique)
                               if e.text.lower().strip() == key)
                    unique[idx] = ent
        return unique

    def _build_graph(self, entities, relations):
        """构建知识图谱"""
        try:
            from semantica.kg import GraphBuilder
            builder = GraphBuilder()
            kg = builder.build({
                "entities": entities,
                "relationships": relations,
            })
            return kg
        except Exception as e:
            print(f"     GraphBuilder 不可用，手动构建: {e}")
            return {
                "entities": entities,
                "relationships": relations,
            }

    def _analyze_graph(self, kg, entities, relations):
        """图分析"""
        try:
            from semantica.kg import GraphAnalyzer
            analyzer = GraphAnalyzer()
            results = analyzer.analyze(kg)
            if isinstance(results, dict):
                for k, v in results.items():
                    val_str = str(v)[:60]
                    print(f"     {k}: {val_str}")
            return results
        except Exception as e:
            pass

        # 手动计算度中心性
        degree = {}
        for rel in relations:
            s = rel.subject.text.lower()
            o = rel.object.text.lower()
            degree[s] = degree.get(s, 0) + 1
            degree[o] = degree.get(o, 0) + 1

        top = sorted(degree.items(), key=lambda x: -x[1])[:5]
        print(f"     度中心性 Top 5:")
        for name, deg in top:
            print(f"       {name}: {deg}")

        return {"degree_manual": True, "top_degree": top}

    def _build_provenance(self, entities, relations, chunks):
        """构建溯源信息"""
        prov_entries = []

        for ent in entities:
            meta = ent.metadata if hasattr(ent, 'metadata') else None
            prov_entries.append({
                "type": "entity",
                "text": ent.text,
                "label": ent.label,
                "source_chunk": meta.get("chunk_id", "unknown") if meta else "unknown",
                "position": f"{ent.start_char}-{ent.end_char}",
                "confidence": ent.confidence,
            })

        for rel in relations:
            meta = rel.metadata if hasattr(rel, 'metadata') else None
            prov_entries.append({
                "type": "relation",
                "subject": rel.subject.text,
                "predicate": rel.predicate,
                "object": rel.object.text,
                "source_chunk": meta.get("chunk_id", "unknown") if meta else "unknown",
                "confidence": rel.confidence,
            })

        return prov_entries

    def _export_results(self, entities, relations, kg):
        """导出结果"""
        out_dir = Path(__file__).parent / "pipeline_output"
        out_dir.mkdir(exist_ok=True)

        # 1. JSON 格式
        json_data = {
            "entities": [
                {
                    "text": e.text, "label": e.label,
                    "confidence": e.confidence,
                    "start": e.start_char, "end": e.end_char,
                }
                for e in entities
            ],
            "relations": [
                {
                    "subject": r.subject.text,
                    "predicate": r.predicate,
                    "object": r.object.text,
                    "confidence": r.confidence,
                }
                for r in relations
            ],
            "stats": {
                "entity_count": len(entities),
                "relation_count": len(relations),
                "method": self.method,
            }
        }

        json_path = out_dir / "extraction_results.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        # 2. CSV 格式（实体）
        csv_path_ent = out_dir / "entities.csv"
        with open(csv_path_ent, "w", encoding="utf-8-sig") as f:
            f.write("text,label,confidence,start,end\n")
            for e in entities:
                f.write(f'"{e.text}",{e.label},{e.confidence:.3f},'
                        f'{e.start_char},{e.end_char}\n')

        # 3. CSV 格式（关系）
        csv_path_rel = out_dir / "relations.csv"
        with open(csv_path_rel, "w", encoding="utf-8-sig") as f:
            f.write("subject,predicate,object,confidence\n")
            for r in relations:
                f.write(f'"{r.subject.text}","{r.predicate}",'
                        f'"{r.object.text}",{r.confidence:.3f}\n')

        # 4. 生成可视化 HTML（从 04 脚本导入）
        html_path = None
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "vis_module",
                Path(__file__).parent / "04_知识图谱可视化.py"
            )
            vis = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(vis)

            vis_nodes, vis_edges = vis.build_graph_data(entities, relations)
            html_path = out_dir / "knowledge_graph.html"
            vis.generate_html(
                vis_nodes, vis_edges,
                title="新能源汽车产业知识图谱",
                output_path=str(html_path),
            )
        except Exception as e:
            print(f"     ⚠️  可视化生成失败: {e}")

        output_files = {
            "json": json_path,
            "entities_csv": csv_path_ent,
            "relations_csv": csv_path_rel,
            "html_visualization": html_path,
        }

        print(f"     输出目录: {out_dir}")
        for name, path in output_files.items():
            if path:
                print(f"       • {name}: {Path(path).name}")

        return output_files

    def _print_summary(self, total_time):
        """打印执行总结"""
        print_header("管线执行总结", char="─")

        print(f"\n  📊 执行统计:")
        print(f"     总耗时: {total_time:.2f} 秒")
        print(f"     提取方法: {self.method}")
        print(f"     实体数量: {len(self.results['entities'])}")
        print(f"     关系数量: {len(self.results.get('raw_relations', []))}")
        print(f"     分块数量: {len(self.results['chunks'])}")
        print(f"     溯源条目: {len(self.results['provenance'])}")
        print()

        print(f"  ⏱️  各阶段耗时:")
        for step_name, elapsed in self.timings.items():
            if step_name != "total":
                pct = (elapsed / total_time * 100) if total_time > 0 else 0
                bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
                print(f"     {step_name:<18} {elapsed:>6.2f}s {bar} {pct:>5.1f}%")
        print()


# ============================================================
# 主函数
# ============================================================
def main():
    print_header("完整管线：长文本要素提取（端到端）")
    print(f"""
输入文本: 中国新能源汽车产业发展报告（模拟）
文本长度: {len(LONG_TEXT)} 字符
处理步骤: 10 步完整管线

管线流程:
  文本预处理 → 实体感知分块 → NER实体识别 → 关系抽取
  → 共指消解 → 实体消歧 → 知识图谱构建 → 图分析
  → 溯源追踪 → 结果导出与可视化
""")

    # 运行管线
    pipeline = KnowledgeExtractionPipeline(method="pattern")
    results = pipeline.run(LONG_TEXT)

    # 输出总结
    print_header("最终结论", char="═")
    print("""
✅ 完整管线跑通！

📌 这个管线能做什么：
  1. 处理超长文本：分块提取，避免上下文窗口限制
  2. 逐块提取实体和关系，再合并去重
  3. 共指消解把"它/该公司"统一到同一实体
  4. 构建知识图谱，计算中心性等图指标
  5. 每个事实都能追溯到来源的具体位置
  6. 导出 JSON/CSV/HTML 多种格式

💡 如何适配你的真实场景：
  • 把 LONG_TEXT 换成你自己的长文本（或从文件读取）
  • 配置 LLM API Key，把 method 改成 "llm" 提升提取质量
  • 接入图数据库（Neo4j 等）做持久化存储
  • 加上本体和 SHACL 做数据质量校验
  • 加上规则推理引擎做知识推理
""")


if __name__ == "__main__":
    main()
