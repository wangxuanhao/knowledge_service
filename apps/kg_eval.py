"""
kg_eval —— 抽取质量评测脚手架（"尺子"，对齐 图谱设计.md §7 口径）
=====================================================================
给"改版本不知好坏"的问题一个自动化答案：
拿一份 **gold（人工校正的答案）** 和一份 **pred（17/15 跑出来的结果）** 对比，
输出 覆盖率 / 实体(级/文本级) 精确率·召回·F1 / 关系 精确率·召回·F1 / 噪声 / 长尾 等指标。

两种输入文件都接受两种形状：
  1) graph.json 形状：{"nodes":[{id,label,text,source_file?...}],"edges":[{source,predicate,target,...}]}
  2) 明细形状：       {"entities":[{text,label,source_file?...}],"relations":[{subject,predicate,object,...}]}

gold 从哪来：
  - 没有现成 gold 时，先跑一次你"信得过"的 17，再用
    python 18_ontology_owl_sparql.py eval --gen-template --pred <结果.json> --out gold.json
    生成模板，人工删掉/改掉错的项，之后当作 gold 反复比对每次改动。

口径说明（与 图谱设计.md §7 对应）：
  coverage          有 source_file 时：gold 覆盖的文档里，pred 命中 ≥1 条正确实体的文档比例
  entity P/R/F1     实体(文本精确 + 类型一致)才算对
  entity_text_recall 只看文本(不管类型)的召回 —— 用来区分"没找到"vs"找对但打错类型"
  relation P/R/F1  (主语,谓词,宾语) 全等
  噪声 noise         pred 有而 gold 没有的关系占比（长尾/幻觉指标）
  长尾 long_tail     pred 里出现次数 ≤1 的关系占比（越小越稳）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ------------------------------------------------------------
# 读取：把任意输入归一成 (entities, relations) 原始 dict 列表
# ------------------------------------------------------------
def load_run(path: str) -> Tuple[List[Dict], List[Dict]]:
    d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(d, dict):
        raise ValueError(f"{path}: 期望 JSON 对象，得到 {type(d).__name__}")
    if "nodes" in d or "edges" in d:
        ents = []
        for n in d.get("nodes", []):
            ents.append({
                "text": str(n.get("text") or n.get("id") or "").strip(),
                "label": str(n.get("label") or "").strip() or "Unknown",
                "source_file": str(n.get("source_file") or "").strip() or None,
            })
        rels = []
        for e in d.get("edges", []):
            rels.append({
                "subject": str(e.get("source") or "").strip(),
                "predicate": str(e.get("predicate") or "").strip(),
                "object": str(e.get("target") or "").strip(),
                "source_file": str(e.get("source_file") or "").strip() or None,
            })
        return ents, rels
    ents = []
    for e in d.get("entities", []):
        ents.append({
            "text": str(e.get("text") or "").strip(),
            "label": str(e.get("label") or "").strip() or "Unknown",
            "source_file": str(e.get("source_file") or "").strip() or None,
        })
    rels = []
    for r in d.get("relations", []):
        rels.append({
            "subject": str(r.get("subject") or "").strip(),
            "predicate": str(r.get("predicate") or "").strip(),
            "object": str(r.get("object") or "").strip(),
            "source_file": str(r.get("source_file") or "").strip() or None,
        })
    return ents, rels


def _n(s: str) -> str:
    return (s or "").strip().lower()


def _prf(gold_n: int, pred_n: int, hit: int) -> Dict[str, float]:
    p = hit / pred_n if pred_n else 0.0
    r = hit / gold_n if gold_n else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


def _load_aliases(path: str) -> Dict[str, str]:
    """从文件读可选的同义组：{"aliases":[{"canonical":"客房","terms":["房源"]}, ...]}
    返回 term→canonical 的解析表（含 canonical 自身）。无则空表。"""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    out: Dict[str, str] = {}
    for g in d.get("aliases", []) or []:
        c = str(g.get("canonical") or "").strip()
        if not c:
            continue
        out[_n(c)] = _n(c)
        for t in g.get("terms", []) or []:
            t = str(t).strip()
            if t:
                out[_n(t)] = _n(c)
    return out


# ------------------------------------------------------------
# 评测主入口
# ------------------------------------------------------------
def evaluate(gold_path: str, pred_path: str) -> Dict[str, Any]:
    g_ents, g_rels = load_run(gold_path)
    p_ents, p_rels = load_run(pred_path)

    # 可选"同义别名档"：gold 里声明 aliases，两端文本先归并再比（缺省=严格文本全等）
    alias_map = _load_aliases(gold_path)

    def norm(name: str) -> str:
        n = _n(name)
        return alias_map.get(n, n)

    g_ent_keys = {(norm(e.get("text")), _n(e.get("label"))) for e in g_ents}
    p_ent_keys = {(norm(e.get("text")), _n(e.get("label"))) for e in p_ents}
    ent_hit = len(g_ent_keys & p_ent_keys)

    g_texts = {norm(e.get("text")) for e in g_ents}
    p_texts = {norm(e.get("text")) for e in p_ents}
    text_hit = len(g_texts & p_texts)

    def _rn(r):
        return (norm(r.get("subject")), _n(r.get("predicate")), norm(r.get("object")))

    g_rel_keys = {_rn(r) for r in g_rels}
    p_rel_keys = {_rn(r) for r in p_rels}
    rel_hit = len(g_rel_keys & p_rel_keys)

    # 覆盖率：按 source_file 分桶（仅当 gold/pred 都带来源时才有意义）
    coverage = None
    g_docs = {str(e.get("source_file")) for e in g_ents if e.get("source_file")}
    if g_docs:
        p_ent_by_doc: Dict[str, set] = {}
        for e in p_ents:
            sf = str(e.get("source_file"))
            if sf:
                p_ent_by_doc.setdefault(sf, set()).add(norm(e.get("text")))
        hit_docs = 0
        for doc in g_docs:
            g_doc_texts = {norm(e.get("text")) for e in g_ents
                           if str(e.get("source_file")) == doc}
            if g_doc_texts & p_ent_by_doc.get(doc, set()):
                hit_docs += 1
        coverage = round(hit_docs / len(g_docs), 4)

    # 噪声 / 长尾（对 pred 关系）
    pred_rel_freq: Dict[str, int] = {}
    for r in p_rels:
        k = _n(r.get("predicate"))
        pred_rel_freq[k] = pred_rel_freq.get(k, 0) + 1
    noise = len(p_rel_keys - g_rel_keys)
    long_tail = sum(1 for p, c in pred_rel_freq.items() if c <= 1)

    return {
        "gold": gold_path,
        "pred": pred_path,
        "counts": {
            "gold_entities": len(g_ent_keys), "pred_entities": len(p_ent_keys),
            "gold_relations": len(g_rel_keys), "pred_relations": len(p_rel_keys),
            "gold_docs": len(g_docs) if g_docs else None,
        },
        "coverage": coverage,
        "entity": _prf(len(g_ent_keys), len(p_ent_keys), ent_hit),
        "entity_text_recall": _prf(len(g_texts), len(p_texts), text_hit)["recall"],
        "relation": _prf(len(g_rel_keys), len(p_rel_keys), rel_hit),
        "noise": {"pred_not_in_gold": noise,
                  "ratio": round(noise / len(p_rel_keys), 4) if p_rel_keys else 0.0},
        "long_tail": {"predicates_le_1": long_tail,
                      "ratio": round(long_tail / len(pred_rel_freq), 4) if pred_rel_freq else 0.0},
    }


def render_eval_md(rep: Dict[str, Any]) -> str:
    L = ["# 抽取质量评测（gold vs pred）\n"]
    L.append(f"- gold: `{rep['gold']}`")
    L.append(f"- pred: `{rep['pred']}`\n")
    c = rep["counts"]
    L.append("| 集合 | gold | pred |")
    L.append("|---|---|---|")
    L.append(f"| 实体 | {c['gold_entities']} | {c['pred_entities']} |")
    L.append(f"| 关系 | {c['gold_relations']} | {c['pred_relations']} |")
    if c["gold_docs"] is not None:
        L.append(f"| 文档 | {c['gold_docs']} | — |")

    L.append("\n## 覆盖率 / 召回 / 精确率\n")
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    if rep["coverage"] is not None:
        L.append(f"| 文档覆盖率 | {rep['coverage']:.2%} |")
    e = rep["entity"]; r = rep["relation"]
    L.append(f"| 实体 精确率/召回/F1 | {e['precision']:.2%} / {e['recall']:.2%} / {e['f1']:.2%} |")
    L.append(f"| 实体文本级召回(不看类型) | {rep['entity_text_recall']:.2%} |")
    L.append(f"| 关系 精确率/召回/F1 | {r['precision']:.2%} / {r['recall']:.2%} / {r['f1']:.2%} |")

    L.append("\n## 噪声 / 稳定性\n")
    L.append(f"- 噪声关系（pred 有、gold 无）：{rep['noise']['pred_not_in_gold']} "
             f"（占 pred 关系 {rep['noise']['ratio']:.1%}）")
    L.append(f"- 长尾谓词（出现 ≤1 次）：{rep['long_tail']['predicates_le_1']} "
             f"（占谓词种数 {rep['long_tail']['ratio']:.1%}）\n")
    L.append("> 提示：gold 应为人工校正后的答案；对比同一批文档的两次改动即可判断版本好坏。")
    return "\n".join(L)


def make_template(pred_path: str, out: str):
    """从一次可信运行生成 gold 模板（人工删改错项后即可当 gold）。"""
    ents, rels = load_run(pred_path)
    out_obj = {
        "note": "人工校正模板：删掉抽错的、把漏的补进来，即可作为 gold 反复比对。",
        "entities": [{"text": e["text"], "label": e["label"],
                      "source_file": e.get("source_file")} for e in ents],
        "relations": [{"subject": r["subject"], "predicate": r["predicate"],
                       "object": r["object"], "source_file": r.get("source_file")}
                      for r in rels],
    }
    Path(out).write_text(json.dumps(out_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"out": out, "entities": len(out_obj["entities"]),
            "relations": len(out_obj["relations"])}
