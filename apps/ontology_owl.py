"""
ontology_owl —— 美团规则知识图谱的 OWL 本体 + SPARQL 能力
============================================================
单一来源：同目录 meituan_ontology.ttl（图谱设计.md §4 落成的 OWL 文件）。

能力：
  1. load_ontology() / get_ontology()
                              把 TTL 读成 rdflib Graph（TBox，进程级缓存）
  2. ontology_info()          输出类层级 / ObjectProperty / DataProperty 摘要
  3. build_dataset(nodes, edges)
                              graph.json 的节点/边 → RDF 个体断言（A-Box），
                              与 TBox 合并成可查询的完整数据集
  4. run_query(g, sparql)     执行 SELECT/ASK，返回列名 + 行
  5. validate(g)              domain/range 一致性检查（轻量替代 SHACL 引擎）
  6. ensure_ontology_file()   本体文件缺失时给明确提示（文件为单一来源，不内嵌副本）

依赖：rdflib（llm_model 环境已装 7.6.0）。全部惰性 import，接入 Web 服务不拖慢启动。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent          # kgcore/
# 本体单一来源：repo/ontology/meituan_ontology.ttl（可用环境变量 ONTO_TTL 覆盖）
ONTOLOGY_FILE = Path(
    os.environ.get("ONTO_TTL") or (ROOT.parent / "ontology" / "meituan_ontology.ttl"))
MT_BASE = "http://meituan.com/kg#"

RDF_TYPE_URI = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_SUBCLASS_URI = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
RDFS_COMMENT_URI = "http://www.w3.org/2000/01/rdf-schema#comment"
RDFS_DOMAIN_URI = "http://www.w3.org/2000/01/rdf-schema#domain"
RDFS_RANGE_URI = "http://www.w3.org/2000/01/rdf-schema#range"

# graph.json 里允许写进 RDF 的谓词（其余噪音边丢弃）
_KNOWN_OBJECT_PROPS = frozenset({
    "publishes", "hasLegalBasis", "appliesTo", "prohibits", "requiresBehavior",
    "collects", "sharesWith", "agreesTo",
    "commits", "detects", "triggers", "handles",
    "hasRight", "hasObligation",
    "purchases", "provides", "contains", "delivers", "refunds",
    "onboards", "reviews", "isAffiliate", "partOf",
})

# 文档结构类：非 NER 标注类型，派生 ENTITY_TYPES 时排除
STRUCTURAL_CLASSES = frozenset({"DocumentPart", "Chapter", "Section", "Article"})

# 结构类与文档大纲 level（_parse_heading 口径）的映射：level → 本体类
STRUCT_LEVEL_CLASS = {1: "Chapter", 2: "Section", 3: "Article"}
DOC_CLASS = "RuleDocument"


# ------------------------------------------------------------
# 基础工具
# ------------------------------------------------------------
def _new_graph():
    from rdflib import Graph, Namespace
    g = Graph()
    g.bind("mt", Namespace(MT_BASE))
    g.bind("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#")
    g.bind("rdfs", "http://www.w3.org/2000/01/rdf-schema#")
    g.bind("owl", "http://www.w3.org/2002/07/owl#")
    g.bind("xsd", "http://www.w3.org/2001/XMLSchema#")
    g.bind("sh", "http://www.w3.org/ns/shacl#")
    return g


def _iri(name: str) -> str:
    """把中文节点/类/谓词名变成安全 IRI（保留可读中文，剔除空白与 IRI 非法字符）"""
    import re
    s = re.sub(r'[\s"<>{}|\\^`#]+', "", (name or "").strip())
    return s or "anon"


def _mt(name: str):
    """MT 命名空间下的 URIRef"""
    from rdflib import URIRef
    return URIRef(MT_BASE + _iri(name))


def _local(u: Any) -> str:
    """URIRef → localName（取 # 后片段），便于展示"""
    return str(u).split("#", 1)[-1] if "#" in str(u) else str(u)


def ensure_ontology_file(path: Optional[Path] = None) -> Path:
    p = Path(path) if path else ONTOLOGY_FILE
    if not p.exists():
        raise FileNotFoundError(
            f"本体文件缺失: {p}\n请先恢复 meituan_ontology.ttl（图谱设计.md §4 的 OWL 落地）。")
    return p


# ------------------------------------------------------------
# TBox：加载 + 摘要
# ------------------------------------------------------------
def load_ontology(path: Optional[Path] = None) -> Any:
    """读取 meituan_ontology.ttl → rdflib Graph（只含 TBox 声明）"""
    p = ensure_ontology_file(path)
    g = _new_graph()
    g.parse(str(p), format="turtle")
    return g


_TBOX_CACHE: Dict[str, Any] = {}


def get_ontology(path: Optional[Path] = None) -> Any:
    """进程级缓存的本体 Graph（避免每次请求重读文件）"""
    p = str(Path(path) if path else ONTOLOGY_FILE)
    if p not in _TBOX_CACHE:
        _TBOX_CACHE[p] = load_ontology(Path(p))
    return _TBOX_CACHE[p]


def _ancestors(g, cls_name: str) -> List[str]:
    """cls 的全部祖先类名（含自身），沿 rdfs:subClassOf 闭包"""
    acc: List[str] = []
    stack = [cls_name]
    while stack:
        cur = stack.pop()
        if cur in acc:
            continue
        acc.append(cur)
        for parent in g.objects(_mt(cur), __uri_of(RDFS_SUBCLASS_URI)):
            pn = _local(parent)
            if pn.startswith("http"):
                continue
            stack.append(pn)
    return acc


def __uri_of(uri: str):
    from rdflib import URIRef
    return URIRef(uri)


def _class_comment(g, name: str) -> str:
    return str(g.value(_mt(name), __uri_of(RDFS_COMMENT_URI)) or "")


def ontology_info(path: Optional[Path] = None) -> Dict[str, Any]:
    """类层级 / ObjectProperty / DataProperty 摘要（含注释、domain/range、SHACL 数量）"""
    from rdflib import Namespace
    g = get_ontology(path)

    class_names = set()
    for c in g.subjects(__uri_of(RDF_TYPE_URI), Namespace("http://www.w3.org/2002/07/owl#")["Class"]):
        if str(c).startswith(MT_BASE):
            class_names.add(_local(c))

    classes: Dict[str, dict] = {}
    for name in class_names:
        parents = [p for p in (_local(x) for x in g.objects(_mt(name), __uri_of(RDFS_SUBCLASS_URI)))
                   if p in class_names]
        classes[name] = {
            "name": name,
            "comment": _class_comment(g, name),
            "parents": sorted(parents),
        }
    for name in list(classes):
        classes[name]["children"] = sorted(
            ch for ch, d in classes.items() if name in d["parents"])

    def _read_props(type_uri: str):
        out = {}
        for p in g.subjects(__uri_of(RDF_TYPE_URI), __uri_of(type_uri)):
            pn = _local(p)
            if not str(p).startswith(MT_BASE):
                continue
            out[pn] = {
                "name": pn,
                "comment": _class_comment(g, pn),
                "domain": sorted({_local(x) for x in g.objects(p, __uri_of(RDFS_DOMAIN_URI))
                                  if str(x).startswith(MT_BASE)}),
                "range": sorted({_local(x) for x in g.objects(p, __uri_of(RDFS_RANGE_URI))
                                 if str(x).startswith(MT_BASE)}),
            }
        return out

    owl = "http://www.w3.org/2002/07/owl#"
    obj = _read_props(owl + "ObjectProperty")
    data = _read_props(owl + "DatatypeProperty")

    from rdflib import Namespace as _NS
    sh = _NS("http://www.w3.org/ns/shacl#")
    n_shapes = sum(1 for _ in g.subjects(__uri_of(RDF_TYPE_URI), sh.NodeShape))

    return {
        "base": MT_BASE,
        "ontology_file": str(ONTOLOGY_FILE),
        "class_count": len(classes),
        "classes": [classes[n] for n in sorted(classes)],
        "object_property_count": len(obj),
        "object_properties": [obj[n] for n in sorted(obj)],
        "data_property_count": len(data),
        "data_properties": [data[n] for n in sorted(data)],
        "shacl_shapes": n_shapes,
    }


# ------------------------------------------------------------
# A-Box：graph.json → RDF 个体断言，与 TBox 合并成数据集
# ------------------------------------------------------------
def _materialize_ancestor_types(g, uri_of, nodes, tbox):
    """给每个个体补写它所属类在 TBox 里的全部祖先类型（rdf:type 物化）。
    物化后，查询 ?e a mt:Actor（超类）无需推理器也能命中 Merchant/Platform 等实例。"""
    from rdflib import URIRef
    anc_cache: Dict[str, List[str]] = {}

    def ancestors(cls: str) -> List[str]:
        if cls not in anc_cache:
            anc_cache[cls] = _ancestors(tbox, cls)
        return anc_cache[cls]

    for n in nodes or []:
        nid = str(n.get("id") or n.get("text") or "").strip()
        if not nid:
            continue
        u = uri_of.get(nid)
        if not u:
            continue
        label = (str(n.get("label") or "")).strip() or "Unknown"
        for sup in ancestors(label):
            if sup == label:
                continue
            g.add((URIRef(u), URIRef(RDF_TYPE_URI), _mt(sup)))


def build_dataset(nodes: List[Dict], edges: List[Dict],
                  path: Optional[Path] = None,
                  materialize: bool = True,
                  add_instance_edges: bool = True) -> Tuple[Any, Dict[str, str]]:
    """graph.json → RDF（含 TBox）。

    node → mt:<id> 个体；rdf:type mt:<label>；rdfs:label/mt:name 原文；
           mt:confidence (xsd:float)；mt:sourceFile；mt:passage
    materialize=True     再补写 label 的全部祖先类型（超类查询免推理器）
    add_instance_edges=True   每个个体补 mt:instanceOf 其直接类（层级遍历/展示用）
    edge → mt:<s> mt:<predicate> mt:<o>（仅白名单谓词）
    返回 (完整 rdflib Graph, {node_id: IRI})"""
    from rdflib import RDFS, URIRef, Literal
    from rdflib.namespace import XSD
    g = _new_graph()
    tbox = get_ontology(path)
    for t in tbox:
        g.add(t)

    uri_of: Dict[str, str] = {}
    for n in nodes or []:
        nid = str(n.get("id") or n.get("text") or "").strip()
        if not nid:
            continue
        u = _mt(nid)
        uri_of[nid] = str(u)
        s = URIRef(u)
        label = (str(n.get("label") or "")).strip() or "Unknown"
        g.add((s, URIRef(RDF_TYPE_URI), _mt(label)))
        if add_instance_edges:
            g.add((s, _mt("instanceOf"), _mt(label)))
        text = str(n.get("text") or nid)
        g.add((s, URIRef(RDFS.label), Literal(text)))
        g.add((s, _mt("name"), Literal(text)))
        if n.get("confidence") is not None:
            g.add((s, _mt("confidence"), Literal(float(n["confidence"]), datatype=XSD.float)))
        if n.get("source_file"):
            g.add((s, _mt("sourceFile"), Literal(str(n["source_file"]))))
        if n.get("passage"):
            g.add((s, _mt("passage"), Literal(str(n["passage"]))))

    if materialize:
        _materialize_ancestor_types(g, uri_of, nodes, tbox)

    for e in edges or []:
        p = (str(e.get("predicate") or "")).strip()
        s = (str(e.get("source") or "")).strip()
        o = (str(e.get("target") or "")).strip()
        if not (p and s and o) or p not in _KNOWN_OBJECT_PROPS:
            continue
        g.add((_mt(s), _mt(p), _mt(o)))
    return g, uri_of


def load_project_dataset(graph_json: Path, path: Optional[Path] = None) -> Tuple[Any, Dict[str, str]]:
    """直接从一个项目 graph.json 建数据集"""
    import json
    d = json.loads(graph_json.read_text(encoding="utf-8"))
    return build_dataset(d.get("nodes", []), d.get("edges", []), path)


# ------------------------------------------------------------
# SPARQL
# ------------------------------------------------------------
def run_query(g, sparql: str) -> Dict[str, Any]:
    """执行 SPARQL。
    SELECT → {columns, rows:[{var: 值字符串}], count}
    ASK   → {ask: bool}
    其它   → {graph_triples, turtle}（CONSTRUCT/DESCRIBE 结果）
    """
    from rdflib import Literal, URIRef, BNode, Graph as _Graph
    text = (sparql or "").strip()
    if not text:
        raise ValueError("SPARQL 为空")
    res = g.query(text)
    qtype = str(getattr(res, "type", "")).upper()
    if qtype == "SELECT":
        cols = [str(v) for v in res.vars]
        rows = []
        for r in res:
            rec = {}
            for i, v in enumerate(res.vars):
                val = r[i]
                if val is None:
                    rec[cols[i]] = ""
                elif isinstance(val, Literal):
                    rec[cols[i]] = str(val.toPython())
                else:
                    rec[cols[i]] = str(val)
            rows.append(rec)
        return {"columns": cols, "rows": rows, "count": len(rows)}
    if qtype == "ASK":
        return {"ask": bool(res.askAnswer)}
    out = _Graph()
    for t in res:
        out.add(t)
    return {"graph_triples": len(out),
            "turtle": out.serialize(format="turtle").decode("utf-8", errors="replace")}


# ------------------------------------------------------------
# 从本体派生抽取白名单（对齐 15/16/17 里的 ENTITY_TYPES / RELATION_TYPES）
# ------------------------------------------------------------
def entity_type_whitelist(path: Optional[Path] = None) -> List[str]:
    """本体"叶子类"（无子类、且非文档结构类）＝ LLM NER 可标注的类型清单"""
    info = ontology_info(path)
    return sorted(c["name"] for c in info["classes"]
                  if not c["children"] and c["name"] not in STRUCTURAL_CLASSES)


def structure_class(level: int) -> str:
    """文档大纲 level → 本体结构类（level: 1章/2节/3条）"""
    return STRUCT_LEVEL_CLASS.get(int(level or 0), DOC_CLASS)


def relation_whitelist(path: Optional[Path] = None) -> List[str]:
    """本体 ObjectProperty（排除 instanceOf/partOf 这类结构物化边）"""
    info = ontology_info(path)
    skip = {"instanceOf", "partOf"}
    return sorted(p["name"] for p in info["object_properties"] if p["name"] not in skip)


# ------------------------------------------------------------
# 抽取端约束检查（直接作用在 15/17 的 entities/relations 列表上）
# ------------------------------------------------------------
def constraint_violations(entities: List[Dict], relations: List[Dict],
                          path: Optional[Path] = None) -> List[Dict]:
    """对内存里的一组 (entities, relations) 做 domain/range 检查。

    entities 元素形如 {text,label}；relations 元素形如 {subject,predicate,object}。
    只查"本体已定义的 ObjectProperty"；partOf/instanceOf/结构边与未知自由谓词不查。
    类型判定含 subClassOf 祖先闭包（subject 是 Merchant，而属性 domain=Actor → 合规）。
    返回违规列表：[{subject, predicate, object, why, expect_domain/range, have_type}]。
    供 17 抽取后直接降权/打标（本体约束回接），也供 18 validate 复用思路。
    """
    from rdflib import URIRef
    info = ontology_info(path)
    dom = {p["name"]: set(p["domain"]) for p in info["object_properties"]}
    ran = {p["name"]: set(p["range"]) for p in info["object_properties"]}
    if not dom and not ran:
        return []

    tbox = get_ontology(path)
    anc_cache: Dict[str, List[str]] = {}
    anc_cache["Unknown"] = ["Unknown", "Entity"]

    def ancestors(cls: str) -> List[str]:
        if cls not in anc_cache:
            anc_cache[cls] = _ancestors(tbox, cls) or [cls]
        return anc_cache[cls]

    label_of: Dict[str, List[str]] = {}  # 归一文本 → 类型们
    for e in entities or []:
        t = str((e.get("text") or "")).strip().lower()
        lab = str((e.get("label") or "")).strip() or "Unknown"
        if not t:
            continue
        label_of.setdefault(t, [])
        if lab not in label_of[t]:
            label_of[t].append(lab)

    def types_of(name: str) -> List[str]:
        t = (name or "").strip().lower()
        labs = label_of.get(t)
        if not labs:
            return []
        acc: List[str] = []
        for lab in labs:
            for sup in ancestors(lab):
                if sup not in acc:
                    acc.append(sup)
        return acc

    skip = {"partOf", "instanceOf"}
    viols = []
    for r in relations or []:
        p = (str((r.get("predicate") or "")).strip())
        s = str((r.get("subject") or "")).strip()
        o = str((r.get("object") or "")).strip()
        if p in skip or p not in dom or not (s and o):
            continue
        s_types = types_of(s)
        o_types = types_of(o)
        if not s_types:
            viols.append({"subject": s, "predicate": p, "object": o,
                          "why": "主语未作为实体抽取（类型未知）",
                          "expect_domain": sorted(dom[p]) or None,
                          "have_type": []})
        elif dom[p] and not (dom[p] & set(s_types)):
            viols.append({"subject": s, "predicate": p, "object": o,
                          "why": "主语类型不在 domain 内",
                          "expect_domain": sorted(dom[p]),
                          "have_type": sorted(set(s_types) - set(STRUCTURAL_CLASSES))
                                         or sorted(s_types)})
        if not o_types:
            viols.append({"subject": s, "predicate": p, "object": o,
                          "why": "宾语未作为实体抽取（类型未知）",
                          "expect_range": sorted(ran[p]) or None,
                          "have_type": []})
        elif ran[p] and not (ran[p] & set(o_types)):
            viols.append({"subject": s, "predicate": p, "object": o,
                          "why": "宾语类型不在 range 内",
                          "expect_range": sorted(ran[p]),
                          "have_type": sorted(set(o_types) - set(STRUCTURAL_CLASSES))
                                         or sorted(o_types)})
    return viols


# ------------------------------------------------------------
# 本体总结 / 归纳（"实体提取后总结本体"的一环）
# ------------------------------------------------------------
def analyze_extraction(entity_dicts: List[Dict], relation_dicts: List[Dict],
                       path: Optional[Path] = None) -> Dict[str, Any]:
    """把一次(或一批)抽取结果归纳成对本体建设有用的统计与候选建议。

    输入与 constraint_violations 兼容：
      entity_dicts:   [{text, label, source_file?, confidence?}]
      relation_dicts: [{subject, predicate, object, source_file?}]
    产出：
      overview / by_label / by_predicate / constraint(digest)
      business_vs_structure
      unknown_candidates.new_classes  —— 抽取里出现、但本体没有的"新类型"（建议入本体）
      unknown_candidates.new_predicates —— 出现的"自由谓词"（建议转 ObjectProperty）
      endpoint_coverage —— 关系端点能否在实体里解析（孤儿边比例）
    """
    from collections import Counter
    info = ontology_info(path)
    cls_comments = {c["name"]: c["comment"] for c in info["classes"]}
    known_classes = set(cls_comments)
    obj_props = {p["name"]: p["comment"] for p in info["object_properties"]}
    known_props = set(obj_props)

    label_cnt = Counter()
    label_src = {}
    for e in entity_dicts or []:
        lab = str((e.get("label") or "")).strip() or "Unknown"
        label_cnt[lab] += 1
        if lab not in label_src:
            label_src[lab] = str(e.get("source_file") or "")

    pred_cnt = Counter()
    pred_sample = {}
    for r in relation_dicts or []:
        p = str((r.get("predicate") or "")).strip()
        pred_cnt[p] += 1
        if p not in pred_sample:
            pred_sample[p] = {"subject": r.get("subject", ""),
                              "object": r.get("object", ""),
                              "source_file": r.get("source_file", "")}

    by_label = [{
        "label": lab, "count": n, "known": lab in known_classes,
        "structural": lab in STRUCTURAL_CLASSES,
        "comment": cls_comments.get(lab, ""),
        "source": label_src.get(lab, ""),
    } for lab, n in label_cnt.most_common()]
    by_predicate = [{
        "predicate": p, "count": n, "known": p in known_props,
        "structural": p == "partOf",
        "comment": obj_props.get(p, ""),
        "sample": pred_sample.get(p, {}),
    } for p, n in pred_cnt.most_common()]

    struct_cnt = sum(v["count"] for v in by_label if v["structural"])
    biz_cnt = sum(v["count"] for v in by_label if not v["structural"])
    biz_known = sum(v["count"] for v in by_label
                    if (not v["structural"]) and v["known"] and v["label"] != "Unknown")

    # 约束摘要
    viols = constraint_violations(entity_dicts, relation_dicts, path)
    by_reason = Counter(v.get("why", "") for v in viols)

    # 候选新类：出现、非结构、非 Unknown、本体没有
    new_classes = []
    for v in by_label:
        if v["structural"] or v["label"] == "Unknown" or v["known"]:
            continue
        new_classes.append({"label": v["label"], "count": v["count"],
                            "source": v["source"], "comment": "（候选：本体未收录的类型）"})
    new_preds = []
    for v in by_predicate:
        if v["structural"] or v["known"]:
            continue
        new_preds.append({"predicate": v["predicate"], "count": v["count"],
                          "sample": v["sample"],
                          "comment": "（候选：自由谓词，建议人工审阅后转 ObjectProperty）"})

    # 端点解析覆盖率
    ent_keys = {(str(e.get("text") or "")).strip().lower()
                for e in (entity_dicts or []) if e.get("text")}
    ok = miss = 0
    orphan_sample = []
    for r in relation_dicts or []:
        s_ok = (str(r.get("subject") or "")).strip().lower() in ent_keys
        o_ok = (str(r.get("object") or "")).strip().lower() in ent_keys
        if s_ok and o_ok:
            ok += 1
        else:
            miss += 1
            if len(orphan_sample) < 5:
                orphan_sample.append({"subject": r.get("subject", ""),
                                      "predicate": r.get("predicate", ""),
                                      "object": r.get("object", "")})

    total_rel = sum(pred_cnt.values())
    return {
        "overview": {
            "entities": len(entity_dicts or []),
            "relations": total_rel,
            "labels": len(label_cnt),
            "predicates": len(pred_cnt),
        },
        "by_label": by_label,
        "by_predicate": by_predicate,
        "business_vs_structure": {
            "business_total": biz_cnt,
            "structure_total": struct_cnt,
            "business_known_ratio": round(biz_known / biz_cnt, 3) if biz_cnt else 0.0,
        },
        "constraint": {
            "violations": len(viols),
            "by_reason": dict(by_reason),
            "sample": viols[:10],
        },
        "unknown_candidates": {"new_classes": new_classes, "new_predicates": new_preds},
        "endpoint_coverage": {
            "resolved": ok, "orphan": miss,
            "orphan_ratio": round(miss / total_rel, 3) if total_rel else 0.0,
            "sample": orphan_sample,
        },
    }


def render_summary_md(report: Dict[str, Any]) -> str:
    """把 analyze_extraction 的结果渲染成给业务/本体管理员看的 markdown 报告。"""
    L = ["# 本体总结报告（17 抽取运行）\n"]
    ov = report["overview"]
    bs = report["business_vs_structure"]
    L.append("## 1. 概览\n")
    L.append(f"- 实体 {ov['entities']} 个（业务型 {bs['business_total']} / "
             f"结构型 {bs['structure_total']}），类型标签 {ov['labels']} 种")
    L.append(f"- 关系 {ov['relations']} 条，谓词 {ov['predicates']} 种")
    L.append(f"- 业务实体归属本体已知类比例：{bs['business_known_ratio'] * 100:.1f}%\n")

    L.append("## 2. 实体类型分布\n")
    L.append("| 类型 | 数量 | 是否本体已知 | 结构/业务 | 说明 | 来源 |")
    L.append("|---|---|---|---|---|---|")
    for v in report["by_label"]:
        kind = "结构" if v["structural"] else "业务"
        known = "✓" if v["known"] else ("✗ 未知" if v["label"] != "Unknown" else "未定义")
        comment = (v["comment"] or "").replace("|", "／")
        L.append(f"| {v['label']} | {v['count']} | {known} | {kind} | {comment} | {v['source']} |")

    L.append("\n## 3. 关系谓词分布\n")
    L.append("| 谓词 | 数量 | 已知 | 说明 | 样例(主语→宾语) |")
    L.append("|---|---|---|---|---|")
    for v in report["by_predicate"]:
        known = "✓" if v["known"] else "✗ 未知"
        comment = (v["comment"] or "").replace("|", "／")
        s = v.get("sample", {})
        L.append(f"| {v['predicate']} | {v['count']} | {known} | {comment} | "
                 f"{s.get('subject','')}→{s.get('object','')} |")

    L.append("\n## 4. 约束与数据质量\n")
    ec = report["endpoint_coverage"]
    L.append(f"- 关系端点可解析：{ec['resolved']} / 孤儿边 {ec['orphan']} "
             f"（比例 {ec['orphan_ratio'] * 100:.1f}%）")
    cons = report["constraint"]
    L.append(f"- domain/range 违规：{cons['violations']} 条")
    if cons["by_reason"]:
        L.append("  - " + "；".join(f"{k}: {n}" for k, n in cons["by_reason"].items()))
    for v in cons["sample"]:
        L.append(f"  - `{v['subject']} {v['predicate']} {v['object']}` → {v.get('why','')}")

    cand = report["unknown_candidates"]
    L.append("\n## 5. 建议纳入本体的候选（人工审阅）\n")
    if cand["new_classes"]:
        L.append("### 候选新类型（抽取中出现、本体未收录）\n")
        L.append("| 类型 | 数量 | 出现文件 |")
        L.append("|---|---|---|")
        for c in cand["new_classes"]:
            L.append(f"| {c['label']} | {c['count']} | {c['source']} |")
    else:
        L.append("（无 —— 所有业务类型都在本体内）\n")
    if cand["new_predicates"]:
        L.append("### 候选新关系（自由谓词，建议转为 ObjectProperty 并定 domain/range）\n")
        L.append("| 谓词 | 数量 | 样例 |")
        L.append("|---|---|---|")
        for p in cand["new_predicates"]:
            s = p["sample"]
            L.append(f"| {p['predicate']} | {p['count']} | {s.get('subject','')}→{s.get('object','')} |")
    else:
        L.append("（无 —— 所有谓词都是已定义关系）\n")

    L.append("\n---\n"
             "说明：本体以 `meituan_ontology.ttl` 为单一来源；审阅通过后把候选"
             "类型/关系补进 TTL，再重跑 17 即完成一轮『抽取→校验→归纳→扩本体』闭环。")
    return "\n".join(L)


# ------------------------------------------------------------
# domain/range 一致性校验
# ------------------------------------------------------------
def validate(g, path: Optional[Path] = None) -> Dict[str, Any]:
    """对 A-Box 里每条 ObjectProperty 三元组，按本体 domain/range 检查。

    类型判定含 subClassOf 祖先闭包：如 subject rdf:type Merchant，而属性 domain=Actor，
    Merchant ⊂ Actor → 视为合规。返回不合规清单（受污染数据一目了然）。"""
    from rdflib import URIRef
    info = ontology_info(path)
    dom = {p["name"]: set(p["domain"]) for p in info["object_properties"]}
    ran = {p["name"]: set(p["range"]) for p in info["object_properties"]}

    # type → 祖先闭包索引（惰性）
    type_cache: Dict[str, List[str]] = {}

    def _types(uri_str: str) -> List[str]:
        if uri_str in type_cache:
            return type_cache[uri_str]
        tnames = [_local(t) for t in g.objects(URIRef(uri_str), URIRef(RDF_TYPE_URI))]
        acc: List[str] = []
        for t in tnames:
            for sup in _ancestors(g, t):
                if sup not in acc:
                    acc.append(sup)
        type_cache[uri_str] = acc
        return acc

    violations: List[dict] = []
    obj_prop_names = set(dom) | set(ran)
    for s, p, o in g:
        pn = _local(p)
        if pn not in obj_prop_names:
            continue
        if pn in ("instanceOf", "partOf"):
            continue  # 物化/结构边不参与 domain/range 校验（instanceOf 指向类节点，partOf 两端任意实体）
        if not (str(s).startswith(MT_BASE) and str(o).startswith(MT_BASE)):
            continue
        s_types = set(_types(str(s)))
        o_types = set(_types(str(o)))
        if dom[pn] and not (dom[pn] & s_types):
            violations.append({
                "predicate": pn,
                "subject": _local(s), "object": _local(o),
                "subject_types": sorted(s_types),
                "expect_domain": sorted(dom[pn]),
                "issue": "subject 类型不在属性 domain 内",
            })
        if ran[pn] and not (ran[pn] & o_types):
            violations.append({
                "predicate": pn,
                "subject": _local(s), "object": _local(o),
                "object_types": sorted(o_types),
                "expect_range": sorted(ran[pn]),
                "issue": "object 类型不在属性 range 内",
            })
    return {"violation_count": len(violations), "violations": violations}
