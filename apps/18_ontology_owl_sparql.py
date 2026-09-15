"""
18 —— OWL 本体 + SPARQL 能力 CLI/演示
========================================
依赖本体模块 ontology_owl（单一来源 meituan_ontology.ttl）。

用法（conda env = llm_model）：
  python 18_ontology_owl_sparql.py info
       打印本体摘要：类层级 / ObjectProperty(domain/range) / DataProperty / SHACL
  python 18_ontology_owl_sparql.py export --graph <graph.json> [--out out.ttl]
       把某个项目的 graph.json 个体断言 + 本体(TBox) 导出成 RDF/Turtle
  python 18_ontology_owl_sparql.py query --graph <graph.json> --query 'SELECT ...'
       在"本体 + 该项目个体"上跑自定义 SPARQL
  python 18_ontology_owl_sparql.py demo --graph <graph.json>
       跑一组内置能力查询（类型计数 / 违规处罚链 / 规则适用 / 多跳 property path）
  python 18_ontology_owl_sparql.py validate --graph <graph.json>
       domain/range 一致性检查（识别抽取污染）

--graph 缺省时自动取 知识图谱/projects 下最新一个 graph.json。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent          # apps/
REPO = ROOT.parent                              # 仓库根
DATA = REPO / "data"
CORE = REPO / "kgcore"
for _p in (str(ROOT), str(CORE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ontology_owl as ow  # noqa: E402


def _find_latest_graph() -> Path:
    cands = sorted(glob.glob(str(DATA / "projects" / "*" / "graph.json")),
                   key=lambda p: Path(p).stat().st_mtime)
    if not cands:
        raise SystemExit("找不到 data/projects/*/graph.json，请用 --graph 指定")
    return Path(cands[-1])


def _load(graph_json: str):
    g, uri_of = ow.load_project_dataset(Path(graph_json))
    return g, uri_of


def _print_table(columns, rows, limit: int = 40):
    if not columns:
        print("  (无结果)")
        return
    data = []
    for r in rows[:limit]:
        data.append([str(r.get(c, ""))[:60] for c in columns])
    widths = [len(c) for c in columns]
    for row in data:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    def line(row):
        return "  " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
    print(line(columns))
    print("  " + "-+-".join("-" * w for w in widths))
    for row in data:
        print(line(row))
    if len(rows) > limit:
        print(f"  … 共 {len(rows)} 行（仅显示前 {limit}）")
    else:
        print(f"  （{len(rows)} 行）")


# ------------------------------------------------------------
# 内置能力查询（覆盖典型合规问答）
# ------------------------------------------------------------
DEMO_QUERIES = [
    ("① 各实体类型计数（个体分布）", """
PREFIX mt: <http://meituan.com/kg#>
SELECT ?type (COUNT(?e) AS ?n) WHERE {
  VALUES ?type { mt:Platform mt:User mt:Merchant mt:FulfillmentService mt:ExternalOrg
                 mt:RuleDocument mt:Violation mt:Penalty mt:Remedy
                 mt:Law mt:Qualification mt:PersonalData
                 mt:Product mt:Order mt:Payment mt:Promotion
                 mt:Content mt:Review }
  ?e a ?type .
} GROUP BY ?type ORDER BY DESC(?n)
"""),
    ("② 商户→违规→处罚 链条（治理路径）", """
PREFIX mt: <http://meituan.com/kg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?merchant ?violation ?penalty WHERE {
  ?merchant a mt:Merchant ; mt:commits ?violation .
  ?violation mt:triggers ?penalty .
  ?penalty a mt:Penalty .
} LIMIT 20
"""),
    ("③ 规则适用对象（含对象类型）", """
PREFIX mt: <http://meituan.com/kg#>
SELECT ?rule ?actor ?actorType WHERE {
  ?rule a mt:RuleDocument ; mt:appliesTo ?actor .
  ?actor a ?actorType .
  VALUES ?actorType { mt:Platform mt:User mt:Merchant mt:FulfillmentService mt:ExternalOrg }
} LIMIT 20
"""),
    ("④ 规则引用法律（hasLegalBasis）TOP", """
PREFIX mt: <http://meituan.com/kg#>
SELECT ?law (COUNT(?rule) AS ?usedBy) WHERE {
  ?rule mt:hasLegalBasis ?law .
} GROUP BY ?law ORDER BY DESC(?usedBy) LIMIT 10
"""),
    ("⑤ 多跳 property path：商户违规后平台如何处置（commits/triggers/handles）", """
PREFIX mt: <http://meituan.com/kg#>
SELECT DISTINCT ?merchant ?platform ?penalty WHERE {
  ?merchant a mt:Merchant .
  ?merchant mt:commits/mt:triggers ?penalty .
  ?platform mt:handles ?penalty .
  ?penalty a mt:Penalty .
} LIMIT 20
"""),
    ("⑥ 高置信度(≥0.9) 的商户节点", """
PREFIX mt: <http://meituan.com/kg#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?m ?name ?conf WHERE {
  ?m a mt:Merchant ; mt:name ?name ; mt:confidence ?conf .
  FILTER(?conf >= 0.9)
} LIMIT 20
"""),
    ("⑦ 超类物化查询：属于 Actor（主体）的全部个体（含子类，免推理器）", """
PREFIX mt: <http://meituan.com/kg#>
SELECT (COUNT(DISTINCT ?e) AS ?n) WHERE { ?e a mt:Actor }
"""),
    ("⑧ 层级遍历：instanceOf/subClassOf* 上卷（如 Merchant 的所有祖先类）", """
PREFIX mt: <http://meituan.com/kg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT DISTINCT ?merchant ?class WHERE {
  ?merchant a mt:Merchant .
  ?merchant mt:instanceOf ?start .
  ?start rdfs:subClassOf* ?class .
} LIMIT 20
"""),
]


def _pretty(g, sparql: str) -> str:
    res = ow.run_query(g, sparql)
    if "ask" in res:
        return f"ASK → {res['ask']}"
    if "columns" in res:
        return res["columns"]
    return res.get("turtle", "")


# ------------------------------------------------------------
# subcommands
# ------------------------------------------------------------
def cmd_info(args):
    info = ow.ontology_info()
    print(f"本体: {info['ontology_file']}  base={info['base']}")
    print(f"类 {info['class_count']} / ObjectProperty {info['object_property_count']} / "
          f"DataProperty {info['data_property_count']} / SHACL shapes {info['shacl_shapes']}")
    print("\n== 类层级（parents→class）==")
    for c in info["classes"]:
        par = ",".join(c["parents"]) or "Entity(top)"
        print(f"  {par:<28} └ {c['name']:<22} {c['comment']}")
    print("\n== ObjectProperty（domain → predicate → range）==")
    for p in info["object_properties"]:
        d = ",".join(p["domain"]) or "?"
        r = ",".join(p["range"]) or "?"
        print(f"  {d:<18} --{p['name']}--> {r:<18}  {p['comment']}")
    print("\n== DataProperty ==")
    for p in info["data_properties"]:
        print(f"  {p['name']:<14} domain={','.join(p['domain']) or '?'}  {p['comment']}")
    print("\n== 派生白名单（单一来源，对齐 ENTITY_TYPES/RELATION_TYPES）==")
    print("  ENTITY_TYPES（叶子类）:", ", ".join(ow.entity_type_whitelist()))
    print("  RELATION_TYPES（ObjectProperty）:", ", ".join(ow.relation_whitelist()))


def cmd_export(args):
    g, _ = _load(args.graph)
    out = Path(args.out) if args.out else Path(args.graph).with_suffix(".rdf.ttl")
    out.write_bytes(g.serialize(format="turtle").encode("utf-8"))
    print(f"导出 {len(g)} 三元组 → {out}")


def cmd_query(args):
    g, _ = _load(args.graph)
    res = ow.run_query(g, args.query)
    if "ask" in res:
        print(f"ASK → {res['ask']}")
    elif "columns" in res:
        _print_table(res["columns"], res["rows"])
    else:
        print(res.get("turtle", "(无结果)"))


def cmd_demo(args):
    g, _ = _load(args.graph)
    print(f"数据集三元组: {len(g)}（本体 + 个体）")
    print(f"图来源: {args.graph}\n")
    for title, q in DEMO_QUERIES:
        print("=" * 70)
        print(title)
        print("-" * 70)
        try:
            res = ow.run_query(g, q)
        except Exception as e:
            print(f"  ❌ 查询失败: {e}")
            continue
        if "columns" in res:
            _print_table(res["columns"], res["rows"])
        elif "ask" in res:
            print(f"  ASK → {res['ask']}")
        else:
            print(f"  {res.get('graph_triples', 0)} 条三元组")


def cmd_validate(args):
    g, _ = _load(args.graph)
    rep = ow.validate(g)
    print(f"domain/range 违规: {rep['violation_count']}")
    for v in rep["violations"]:
        if "subject" in v and v.get("issue", "").startswith("subject"):
            print(f"  [{v['predicate']}] 主语 '{v['subject']}' 类型 {v['subject_types']} "
                  f"∉ domain {v['expect_domain']}")
        else:
            print(f"  [{v['predicate']}] 宾语 '{v['object']}' 类型 {v['object_types']} "
                  f"∉ range {v['expect_range']}")
    if not rep["violations"]:
        print("  ✓ 全部三元组符合 domain/range 约束")


def cmd_eval(args):
    """抽取质量评测（尺子）：gold vs pred。口径见 kg_eval.py 头注释。"""
    import kg_eval
    if args.gen_template:
        res = kg_eval.make_template(args.pred, args.out)
        print(f"gold 模板 → {res['out']}（实体 {res['entities']} / 关系 {res['relations']}，"
              f"人工删改错项后即可当 gold）")
        return
    if not args.gold:
        raise SystemExit("--eval 需要 --gold；没有 gold 先用 --gen-template 生成模板")
    rep = kg_eval.evaluate(args.gold, args.pred)
    md = kg_eval.render_eval_md(rep)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"评测报告 → {args.out}\n")
    print(md)


def main():
    ap = argparse.ArgumentParser(description="OWL 本体 + SPARQL 工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="打印本体摘要").set_defaults(fn=cmd_info)
    p = sub.add_parser("export", help="graph.json → RDF/Turtle")
    p.add_argument("--graph", default=None)
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("query", help="跑自定义 SPARQL")
    p.add_argument("--graph", default=None)
    p.add_argument("--query", required=True)
    p.set_defaults(fn=cmd_query)
    p = sub.add_parser("demo", help="跑内置能力查询")
    p.add_argument("--graph", default=None)
    p.set_defaults(fn=cmd_demo)
    p = sub.add_parser("validate", help="domain/range 校验")
    p.add_argument("--graph", default=None)
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("eval", help="抽取质量评测（gold vs pred，需 gold）")
    p.add_argument("--gold", default=None, help="人工校正的答案 JSON")
    p.add_argument("--pred", default=None, help="17/15 跑出来的结果 JSON（graph.json 或明细 JSON）")
    p.add_argument("--out", default=None, help="报告 md 输出路径")
    p.add_argument("--gen-template", action="store_true",
                   help="从 --pred 生成 gold 模板到 --out")
    p.set_defaults(fn=cmd_eval)

    args = ap.parse_args()
    if getattr(args, "cmd", None) == "eval":
        if not args.pred:
            raise SystemExit("eval 需要 --pred")
        args.fn(args)
        return
    if hasattr(args, "graph") and not args.graph:
        args.graph = str(_find_latest_graph())
    args.fn(args)


if __name__ == "__main__":
    main()
