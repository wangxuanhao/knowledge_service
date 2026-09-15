"""
03 - 本体 (Ontology) 设置与 SHACL 约束验证
==================================================
演示如何定义知识图谱的本体 Schema，以及如何用 SHACL 验证数据质量。

什么是本体？
  - 定义知识图谱中有哪些"类"（实体类型）
  - 定义类之间的层级关系（继承）
  - 定义每个类有哪些属性，属性的类型和约束
  - 定义实体之间有哪些"关系"及其定义域/值域

运行: python 03_本体Ontology设置.py
"""

import json
import sys
from pathlib import Path


def print_header(title, char="═"):
    print(f"\n{char * 70}")
    print(f"  {title}")
    print(f"{char * 70}")


def section(title):
    print(f"\n┌─ {title} " + "─" * (62 - len(title)))


# ============================================================
# Demo 1: 使用 Semantica 原生 API 定义本体
# ============================================================
def demo_semantica_ontology():
    section("方式一：Semantica 原生 OntologyEngine 定义本体")

    try:
        from semantica.ontology import OntologyEngine, OntologyConfig

        # 配置本体
        config = OntologyConfig(
            base_uri="http://example.com/ontology#",
            namespace_prefix="ex",
        )

        # 创建本体引擎
        engine = OntologyEngine(config=config)

        # 添加类
        engine.add_class("Organization")
        engine.add_class("Company", parent_class="Organization")
        engine.add_class("TechCompany", parent_class="Company")
        engine.add_class("Person")
        engine.add_class("Product")
        engine.add_class("AIModel", parent_class="Product")
        engine.add_class("Location")
        engine.add_class("City", parent_class="Location")
        engine.add_class("Date")

        # 添加对象属性（关系）
        engine.add_object_property(
            "founded_by",
            domain="Company",
            range="Person",
            description="公司由某人创立",
        )
        engine.add_object_property(
            "located_in",
            domain="Organization",
            range="Location",
            description="位于某地",
        )
        engine.add_object_property(
            "develops",
            domain="Company",
            range="Product",
            description="开发某产品",
        )
        engine.add_object_property(
            "released_on",
            domain="Product",
            range="Date",
            description="发布日期",
        )
        engine.add_object_property(
            "ceo_of",
            domain="Person",
            range="Company",
            description="担任某公司CEO",
        )
        engine.add_object_property(
            "competes_with",
            domain="Company",
            range="Company",
            symmetric=True,
            description="与...竞争（对称关系）",
        )

        print("│ ✅ 本体定义成功")
        print("│")

        # 获取类列表
        classes = engine.get_classes()
        print(f"│ 类数量: {len(classes)}")
        print(f"│ 类列表:")
        for cls in classes:
            name = cls if isinstance(cls, str) else getattr(cls, 'name', str(cls))
            print(f"│   • {name}")

        # 获取属性列表
        props = engine.get_properties()
        print(f"│")
        print(f"│ 对象属性数量: {len(props)}")
        print(f"│ 属性列表:")
        for prop in props[:8]:
            name = prop if isinstance(prop, str) else getattr(prop, 'name', str(prop))
            print(f"│   • {name}")
        if len(props) > 8:
            print(f"│   ... 还有 {len(props)-8} 个")

        # 导出本体
        output_path = Path(__file__).parent / "ontology_semantica.ttl"
        try:
            engine.export(str(output_path), format="turtle")
            print(f"│")
            print(f"│ 💾 本体已导出到: {output_path}")
        except Exception as e:
            print(f"│ 导出失败: {e}")

        return engine

    except Exception as e:
        print(f"│ Semantica 本体 API 调用失败: {e}")
        print("│")
        print("│ 改用 rdflib 手动构建本体:")
        return build_ontology_with_rdflib()


# ============================================================
# 用 rdflib 手动构建本体（备用方案）
# ============================================================
def build_ontology_with_rdflib():
    """使用 rdflib 构建本体"""
    try:
        from rdflib import Graph, Namespace, RDF, RDFS, OWL, URIRef, Literal

        g = Graph()
        EX = Namespace("http://example.com/ontology#")
        g.bind("ex", EX)
        g.bind("owl", OWL)
        g.bind("rdfs", RDFS)

        # 定义类
        classes = {
            "Organization": None,
            "Company": "Organization",
            "TechCompany": "Company",
            "Person": None,
            "Product": None,
            "AIModel": "Product",
            "Location": None,
            "City": "Location",
            "Date": None,
        }

        for cls_name, parent in classes.items():
            cls_uri = EX[cls_name]
            g.add((cls_uri, RDF.type, OWL.Class))
            g.add((cls_uri, RDFS.label, Literal(cls_name)))
            if parent:
                g.add((cls_uri, RDFS.subClassOf, EX[parent]))

        # 定义属性
        properties = [
            ("founded_by", "Company", "Person", "公司由某人创立"),
            ("located_in", "Organization", "Location", "位于某地"),
            ("develops", "Company", "Product", "开发某产品"),
            ("released_on", "Product", "Date", "发布日期"),
            ("ceo_of", "Person", "Company", "担任某公司CEO"),
            ("competes_with", "Company", "Company", "与...竞争"),
        ]

        for prop_name, domain, range_, desc in properties:
            prop_uri = EX[prop_name]
            g.add((prop_uri, RDF.type, OWL.ObjectProperty))
            g.add((prop_uri, RDFS.domain, EX[domain]))
            g.add((prop_uri, RDFS.range, EX[range_]))
            g.add((prop_uri, RDFS.comment, Literal(desc)))

        print("│ ✅ 使用 rdflib 构建本体成功")
        print(f"│")
        print(f"│ 类层级:")
        for cls_name, parent in classes.items():
            indent = "  " if parent else ""
            arrow = f"  ← {parent}" if parent else ""
            print(f"│   {indent}• {cls_name}{arrow}")

        print(f"│")
        print(f"│ 属性 (共 {len(properties)} 个):")
        for prop_name, domain, range_, desc in properties:
            print(f"│   • {prop_name} ({domain} → {range_}): {desc}")

        # 保存
        output_path = Path(__file__).parent / "ontology_rdflib.ttl"
        g.serialize(destination=str(output_path), format="turtle")
        print(f"│")
        print(f"│ 💾 本体已保存到: {output_path}")

        return g

    except ImportError:
        print("│ rdflib 未安装，跳过本体构建")
        print("│ 安装: pip install rdflib")
        return None


# ============================================================
# Demo 2: SHACL 约束验证
# ============================================================
def demo_shacl_validation(ontology_graph):
    section("方式二：SHACL 约束验证（数据质量检查）")

    print("│")
    print("│ SHACL 作用：验证知识图谱中的数据是否符合预定义约束")
    print("│ 常见约束：类型约束、基数约束、属性值类型约束、模式约束")
    print("│")

    try:
        # 尝试使用 Semantica 的 SHACL
        from semantica.ontology import SHACLGraph, run_shacl_validation, NodeShape, PropertyShape

        print("│ 使用 Semantica SHACL API...")

        # 创建 SHACL 形状图
        shacl_graph = SHACLGraph()

        # 定义 Company 的 Shape：必须有 located_in 属性
        company_shape = NodeShape(
            target_class="Company",
            properties=[
                PropertyShape(
                    path="located_in",
                    min_count=1,
                    class_constraint="Location",
                    description="公司所在地（必填）",
                ),
                PropertyShape(
                    path="founded_by",
                    min_count=0,
                    class_constraint="Person",
                    description="公司创始人",
                ),
            ],
        )
        shacl_graph.add_shape(company_shape)

        # 测试数据（构造一些实例）
        from semantica.kg import GraphBuilder
        from semantica.semantic_extract.types import Entity, Relation

        entities = [
            Entity(text="字节跳动", label="Company", confidence=0.9, start_char=0, end_char=4),
            Entity(text="张一鸣", label="Person", confidence=0.9, start_char=5, end_char=8),
            Entity(text="北京", label="City", confidence=0.9, start_char=9, end_char=11),
            Entity(text="神秘公司", label="Company", confidence=0.9, start_char=12, end_char=16),
        ]

        relations = [
            Relation(
                subject=entities[0], predicate="founded_by",
                object=entities[1], confidence=0.9,
            ),
            Relation(
                subject=entities[0], predicate="located_in",
                object=entities[2], confidence=0.9,
            ),
            # 神秘公司没有 located_in，应该触发验证失败
        ]

        builder = GraphBuilder()
        data_graph = builder.build({"entities": entities, "relationships": relations})

        # 执行验证
        report = run_shacl_validation(data_graph, shacl_graph, ontology=ontology_graph)

        print(f"│ 验证结果: {'✅ 合规' if report.conforms else '❌ 违规'}")
        if hasattr(report, 'violations'):
            print(f"│ 违规数: {len(report.violations)}")
            for v in report.violations[:5]:
                print(f"│   • {v}")

        return report

    except Exception as e:
        print(f"│ Semantica SHACL API 不可用: {e}")
        print("│")

        # 备用：用 pyshacl + rdflib
        try:
            from rdflib import Graph, Namespace, RDF, RDFS, OWL, URIRef, Literal
            from rdflib.namespace import SH

            EX = Namespace("http://example.com/ontology#")
            EX_DATA = Namespace("http://example.com/data#")

            # SHACL Shapes
            shapes_graph = Graph()
            shapes_graph.bind("sh", SH)
            shapes_graph.bind("ex", EX)

            company_shape = EX["CompanyShape"]
            shapes_graph.add((company_shape, RDF.type, SH.NodeShape))
            shapes_graph.add((company_shape, SH.targetClass, EX["Company"]))

            # located_in 必填
            prop_uri = URIRef("http://example.com/shapes/located_in")
            shapes_graph.add((company_shape, SH.property, prop_uri))
            shapes_graph.add((prop_uri, SH.path, EX["located_in"]))
            shapes_graph.add((prop_uri, SH.minCount, Literal(1)))
            shapes_graph.add((prop_uri, SH["class"], EX["Location"]))

            # 数据
            data_graph = Graph()
            data_graph.bind("ex", EX)
            data_graph.bind("data", EX_DATA)

            # 合规：字节跳动
            data_graph.add((EX_DATA["bytedance"], RDF.type, EX["Company"]))
            data_graph.add((EX_DATA["bytedance"], EX["founded_by"], EX_DATA["zhang_yiming"]))
            data_graph.add((EX_DATA["bytedance"], EX["located_in"], EX_DATA["beijing"]))
            data_graph.add((EX_DATA["zhang_yiming"], RDF.type, EX["Person"]))
            data_graph.add((EX_DATA["beijing"], RDF.type, EX["City"]))

            # 违规：神秘公司（无 located_in）
            data_graph.add((EX_DATA["mystery_company"], RDF.type, EX["Company"]))

            print("│ 测试数据:")
            print("│   1. 字节跳动 → 有创始人，有地点 ✅ 合规")
            print("│   2. 神秘公司 → 无地点 ❌ 应违规")
            print("│")

            try:
                from pyshacl import validate
                conforms, results_graph, results_text = validate(
                    data_graph,
                    shacl_graph=shapes_graph,
                    ont_graph=ontology_graph if isinstance(ontology_graph, Graph) else None,
                    inference="rdfs",
                    abort_on_first=False,
                )

                print(f"│ 验证结果: {'✅ 合规' if conforms else '❌ 违规'}")
                print(f"│")
                print(f"│ 违规详情:")
                lines = results_text.strip().split("\n")
                for line in lines[:15]:
                    print(f"│   {line}")

                return conforms
            except ImportError:
                print("│ ⚠️  pyshacl 未安装")
                print("│    安装: pip install pyshacl")
                return None

        except Exception as e2:
            print(f"│ SHACL 验证失败: {e2}")
            return None


# ============================================================
# Demo 3: 本体推理
# ============================================================
def demo_ontology_reasoning(ontology_graph):
    section("方式三：基于本体的推理（类继承/属性传递）")

    print("│")
    print("│ 本体推理能做什么？")
    print("│   1. 类继承推理：A是TechCompany → A也是Company")
    print("│   2. 传递属性推理：A located_in B, B located_in C → A located_in C")
    print("│   3. 对称属性推理：A competes_with B → B competes_with A")
    print("│")

    try:
        from rdflib import Graph, Namespace, RDF, RDFS, OWL, URIRef, Literal

        EX = Namespace("http://example.com/ontology#")
        EX_DATA = Namespace("http://example.com/data#")

        # 推理示例图
        g = Graph()
        g.bind("ex", EX)
        g.bind("data", EX_DATA)

        # 本体
        g.add((EX["Company"], RDF.type, OWL.Class))
        g.add((EX["TechCompany"], RDF.type, OWL.Class))
        g.add((EX["TechCompany"], RDFS.subClassOf, EX["Company"]))
        g.add((EX["Person"], RDF.type, OWL.Class))

        # 传递属性
        g.add((EX["located_in"], RDF.type, OWL.TransitiveProperty))

        # 对称属性
        g.add((EX["competes_with"], RDF.type, OWL.SymmetricProperty))

        # 数据
        g.add((EX_DATA["bytedance"], RDF.type, EX["TechCompany"]))
        g.add((EX_DATA["bytedance"], EX["located_in"], EX_DATA["beijing"]))
        g.add((EX_DATA["beijing"], EX["located_in"], EX_DATA["china"]))
        g.add((EX_DATA["bytedance"], EX["competes_with"], EX_DATA["tencent"]))

        print("│ 原始断言（显式知识）:")
        print("│   1. 字节跳动 rdf:type TechCompany")
        print("│   2. 字节跳动 located_in 北京")
        print("│   3. 北京 located_in 中国")
        print("│   4. 字节跳动 competes_with 腾讯")
        print("│")
        print("│ 推理可得出（隐式知识）:")
        print("│   1. 字节跳动 rdf:type Company     ← 类继承推理")
        print("│   2. 字节跳动 located_in 中国     ← 传递属性推理")
        print("│   3. 腾讯 competes_with 字节跳动  ← 对称属性推理")
        print("│")

        # RDFS 查询验证类继承
        q = """
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX ex: <http://example.com/ontology#>

        SELECT ?instance WHERE {
            ?instance rdf:type ?type .
            ?type rdfs:subClassOf* ex:Company .
        }
        """
        results = list(g.query(q))
        print(f"│ RDFS 查询验证: Company 类的实例（含子类）= {len(results)} 个")
        for row in results:
            name = str(row.instance).split("#")[-1]
            print(f"│   - {name}")

        print("│")
        print("│ 💡 本体价值：显式知识 + 推理规则 = 更多隐式知识")

    except Exception as e:
        print(f"│ 推理演示失败: {e}")


# ============================================================
# Demo 4: 从提取结果自动生成本体
# ============================================================
def demo_auto_ontology():
    section("方式四：从提取结果自动生成本体（反向工程）")

    print("│")
    print("│ 场景：已有实体/关系提取结果，但还没有本体")
    print("│ 方法：从数据中反向推导出类、属性、层级关系")
    print("│")

    # 模拟提取结果
    entities = [
        {"text": "字节跳动", "type": "COMPANY"},
        {"text": "阿里巴巴", "type": "COMPANY"},
        {"text": "张一鸣", "type": "PERSON"},
        {"text": "马云", "type": "PERSON"},
        {"text": "豆包4.0", "type": "MODEL"},
        {"text": "GPT-4", "type": "MODEL"},
        {"text": "北京", "type": "LOCATION"},
        {"text": "杭州", "type": "LOCATION"},
        {"text": "2024年3月", "type": "DATE"},
    ]

    relations = [
        {"subject": "字节跳动", "predicate": "founded_by", "object": "张一鸣"},
        {"subject": "阿里巴巴", "predicate": "founded_by", "object": "马云"},
        {"subject": "字节跳动", "predicate": "develops", "object": "豆包4.0"},
        {"subject": "字节跳动", "predicate": "located_in", "object": "北京"},
        {"subject": "阿里巴巴", "predicate": "located_in", "object": "杭州"},
        {"subject": "字节跳动", "predicate": "released_on", "object": "2024年3月"},
    ]

    print(f"│ 输入: {len(entities)} 个实体, {len(relations)} 条关系")
    print(f"│")

    # 推导类
    entity_types = set(e["type"] for e in entities)
    print(f"│ 推导出的类（实体类型）:")
    for t in sorted(entity_types):
        print(f"│   • {t}")
    print(f"│")

    # 推导属性（domain/range）
    prop_domains = {}
    prop_ranges = {}
    for rel in relations:
        pred = rel["predicate"]
        subj_type = next((e["type"] for e in entities if e["text"] == rel["subject"]), "Unknown")
        obj_type = next((e["type"] for e in entities if e["text"] == rel["object"]), "Unknown")
        if pred not in prop_domains:
            prop_domains[pred] = set()
            prop_ranges[pred] = set()
        prop_domains[pred].add(subj_type)
        prop_ranges[pred].add(obj_type)

    print(f"│ 推导出的属性（关系）:")
    for pred in sorted(prop_domains.keys()):
        domains = ", ".join(sorted(prop_domains[pred]))
        ranges = ", ".join(sorted(prop_ranges[pred]))
        print(f"│   • {pred:<15} domain: {domains:<10} range: {ranges}")

    print(f"│")
    print(f"│ 💡 自动生成的本体可作为起点，再人工微调完善")

    # 尝试用 Semantica 的 LLMOntologyGenerator
    try:
        from semantica.ontology import LLMOntologyGenerator
        print(f"│")
        print(f"│ Semantica 还支持 LLMOntologyGenerator（用LLM生成本体）")
        print(f"│ 适合已有文本但还不知道该怎么设计本体的场景")
    except ImportError:
        pass


# ============================================================
# 主函数
# ============================================================
def main():
    print_header("本体篇：Ontology 设置与 SHACL 约束验证")
    print("""
本脚本演示知识图谱本体 (Ontology) 的定义、验证和推理。

为什么需要本体？
  • 没有本体的知识图谱就像没有表结构的数据库——数据混乱
  • 本体 = 知识图谱的 Schema，定义实体类型和关系类型
  • 本体支持推理，能从显式知识推导出更多隐式知识

内容：
  1. Semantica 原生 OntologyEngine 定义本体
  2. SHACL 约束验证（数据质量检查）
  3. 基于本体的推理（类继承、属性传递、对称属性）
  4. 从提取结果自动生成本体（反向工程）
""")

    # Demo 1: 定义本体
    ontology_graph = demo_semantica_ontology()

    # Demo 2: SHACL 验证
    demo_shacl_validation(ontology_graph)

    # Demo 3: 本体推理
    demo_ontology_reasoning(ontology_graph)

    # Demo 4: 自动生成本体
    demo_auto_ontology()

    # 总结
    print_header("本体篇总结", char="═")
    print("""
📌 本体设计的核心原则：
  1. 先粗后细：先定义顶层类，再逐步细化子类
  2. 够用就好：不要过度设计，根据实际需要定义
  3. 持续迭代：本体不是一次性设计完的
  4. 复用优先：尽量复用已有本体（Schema.org、DBpedia 等）

📌 SHACL 验证的使用时机：
  • 数据入库前：校验提取结果是否符合规范
  • 定期巡检：检查图谱数据质量
  • 新源接入：验证新数据源格式是否正确

📌 本体推理的价值：
  • 数据压缩：只存显式事实，隐式事实按需推导
  • 一致性检查：发现本体和数据之间的矛盾
  • 知识补全：自动发现缺失的关系和属性

💡 实际项目中的建议：
  • 初期可以先不搞本体，先把数据跑起来
  • 实体类型超10种、关系类型超20种时，考虑引入本体
  • 用 SHACL 做关键数据的质量门禁，不要试图验证所有东西
""")


if __name__ == "__main__":
    main()
