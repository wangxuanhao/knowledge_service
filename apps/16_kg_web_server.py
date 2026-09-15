"""
16 - 知识图谱 Web 前端（对标 semantica Explorer）
====================================================
功能：
  1. 解析文件/目录 → 实体/关系/SPO（可走 LLM，也可用 --demo 内置样例）
  2. 展示图结构（ECharts 力导向图）
  3. 检索：区分展开 —— ③图链路 / ②原文命中段 / ①匹配实体
  4. 离线存储为 project，下次导入直接看结果

运行（conda env = llm_model）：
    D:/work/wangxuanhao/conda/envs/llm_model/python.exe 16_kg_web_server.py
    浏览器打开 http://127.0.0.1:8000/
"""
from __future__ import annotations
import os, sys, json, uuid, importlib.util, threading, traceback
from pathlib import Path

# 目录自举：仓库根 = apps 的上一级；kgcore 进 sys.path（供 kg_project / ontology_owl）
_APP_DIR = Path(__file__).resolve().parent
REPO = _APP_DIR.parent
CORE = REPO / "kgcore"
DATA = REPO / "data"
for _p in (str(_APP_DIR), str(CORE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 复用 15 脚本的解析/图/检索逻辑（按文件名加载模块）
_SCRIPT15 = Path(__file__).resolve().parent / "15_rule_demo_llm提取.py"
_spec = importlib.util.spec_from_file_location("kg15", _SCRIPT15)
kg15 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kg15)

# 17 脚本：semantica 原生 NERExtractor/RelationExtractor + 标题感知分块（抽取质量更优）
_SCRIPT17 = Path(__file__).resolve().parent / "17_rule_demo_semantica_api.py"
try:
    _spec17 = importlib.util.spec_from_file_location("kg17", _SCRIPT17)
    kg17 = importlib.util.module_from_spec(_spec17)
    _spec17.loader.exec_module(kg17)
except Exception as _e17:
    print(f"⚠️ 17 抽取器模块加载失败（将回退 15 DirectLLMExtractor）: {_e17}", flush=True)
    kg17 = None

import kg_project
# OWL 本体 + SPARQL（单一来源 meituan_ontology.ttl，见 18_ontology_owl_sparql.py）
try:
    import ontology_owl as _owl
except Exception as _eo:
    print(f"⚠️ ontology_owl 加载失败（/ontology declared、/sparql 端点将不可用）: {_eo}", flush=True)
    _owl = None
from fastapi import FastAPI, APIRouter, File, UploadFile, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from collections import Counter, defaultdict, deque
from datetime import datetime

app = FastAPI(title="知识图谱 Explorer")
ROOT = REPO
STATIC_DIR = ROOT / "data" / "kg_web" / "static"
PROJECTS_DIR = ROOT / "data" / "kg_web" / "projects"

# 尽量离线
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("SEMANTICA_ALLOW_ANONYMOUS", "true")

# 全局：已加载 project → 内存 store + graph
LOADED: dict[str, dict] = {}
JOBS: dict[str, dict] = {}
_api = APIRouter(prefix="/api")

# FAISS/embedder 非线程安全：后台 upsert 写入与前台检索并发读写会造成段错误级崩溃。
# 用进程级锁把所有 encode/index.add/index.search 串行化（量小，可接受）。
_STORE_LOCK = threading.RLock()

# ============================================================
# 日志：控制台 + 落盘（旋转 5×5MB）＋ 镜像 stdout/stderr ＋ 请求访问日志
# ============================================================
import logging as _logging
from logging.handlers import RotatingFileHandler as _Rot
LOG_DIR = ROOT / "data" / "kg_web" / "logs"
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
LOG = _logging.getLogger("kg16")
LOG.setLevel(_logging.INFO)
if not LOG.handlers:
    _rot = _Rot(str(LOG_DIR / "server.log"), maxBytes=5 * 1024 * 1024,
                backupCount=5, encoding="utf-8")
    _rot.setFormatter(_logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                         datefmt="%Y-%m-%d %H:%M:%S"))
    LOG.addHandler(_rot)
    _con = _logging.StreamHandler(sys.stdout)
    _con.setFormatter(_logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                         datefmt="%H:%M:%S"))
    LOG.addHandler(_con)


class _Tee:
    """把 stdout/stderr 同时写一份到 out.log，kg 脚本的 print 也能落盘"""
    def __init__(self, dst, fh):
        self.dst, self.fh = dst, fh

    def write(self, s):
        try:
            self.dst.write(s)
        except Exception:
            pass
        try:
            self.fh.write(s)
            self.fh.flush()
        except Exception:
            pass
        return len(s)

    def flush(self):
        try:
            self.dst.flush()
        except Exception:
            pass
        try:
            self.fh.flush()
        except Exception:
            pass

    def isatty(self):
        try:
            return self.dst.isatty()
        except Exception:
            return False


try:
    _mirror = open(LOG_DIR / "out.log", "a", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, _mirror)
    sys.stderr = _Tee(sys.__stderr__, _mirror)
except Exception as _me:
    LOG.error("日志镜像打开失败: %s", _me)


def _exc_hook(typ, val, tb):
    import traceback as _tb
    LOG.error("未捕获异常 %s: %s\n%s", typ.__name__, val,
             "".join(_tb.format_exception(typ, val, tb)))


sys.excepthook = _exc_hook

import time as _time


@app.middleware("http")
async def _access_log(request, call_next):
    st = _time.time()
    path = request.url.path
    qs = request.url.query
    try:
        resp = await call_next(request)
        if path.startswith("/api") and not path.startswith("/api/jobs"):
            LOG.info("%s %s%s -> %s (%.0f ms)", request.method, path,
                     ("?" + qs) if qs else "", resp.status_code,
                     (_time.time() - st) * 1000)
        return resp
    except Exception as e:
        import traceback as _tb
        LOG.error("接口异常 %s %s: %s\n%s", request.method, path, e, _tb.format_exc())
        raise

# ============================================================
# 本体/类目静态定义（与 15 脚本 schema、图谱设计.md 对齐）
# ============================================================
CLASS_GROUPS: list[tuple[str, list[str]]] = [
    ("主体", ["Platform", "User", "Merchant", "FulfillmentService", "ExternalOrg"]),
    ("规则", ["RuleDocument"]),
    ("治理", ["Violation", "Penalty", "Remedy"]),
    ("外部引用", ["Law", "Qualification"]),
    ("数据", ["PersonalData"]),
    ("交易客体", ["Product", "Order", "Payment", "Promotion"]),
    ("内容", ["Content", "Review"]),
]
CLASS_DESC: dict[str, str] = {
    "Platform": "平台（美团/大众点评/我们）",
    "User": "消费者/用户",
    "Merchant": "商家/商户（含主播、店铺经营者）",
    "FulfillmentService": "履约服务（客服/骑手/司机/代运营）",
    "ExternalOrg": "监管/外部组织（第三方/合作商/关联/供应商/支付/物流/监管/保险）",
    "RuleDocument": "规则/协议/政策",
    "Violation": "违规行为",
    "Penalty": "处理措施",
    "Remedy": "救济途径",
    "Law": "法律法规",
    "Qualification": "经营资质",
    "PersonalData": "个人信息",
    "Product": "商品/服务",
    "Order": "订单",
    "Payment": "资金结算",
    "Promotion": "营销权益",
    "Content": "信息内容",
    "Review": "评价/口碑",
}
PRED_DESC: dict[str, str] = {
    "publishes": "平台发布规则",
    "hasLegalBasis": "规则的法律依据",
    "appliesTo": "规则适用于…",
    "prohibits": "规则禁止…",
    "requiresBehavior": "规则要求…",
    "collects": "平台收集个人信息",
    "sharesWith": "平台共享给外部组织",
    "agreesTo": "用户同意规则",
    "commits": "商户实施违规",
    "detects": "平台发现违规",
    "triggers": "违规触发处罚",
    "handles": "平台处置处罚",
    "hasRight": "用户享有权利",
    "hasObligation": "商户负有义务",
    "purchases": "用户购买商品",
    "provides": "商户提供服务",
    "contains": "订单包含商品",
    "delivers": "履约配送订单",
    "refunds": "平台退款结算",
    "onboards": "商户入驻平台",
    "reviews": "平台审核资质",
    "isAffiliate": "平台关联组织",
    "partOf": "归属于…（章节/条款）",
}
_MANUAL_SRC = "[手动添加]"


def _class_order():
    """类目展示顺序：本体分组顺序，未收录的排后面"""
    order = []
    for _, cls in CLASS_GROUPS:
        order.extend(cls)
    return order


# ============================================================
# 可复用：从 all_meta 组装一个 project（含离线索引）
# ============================================================
SEGMENT_CACHE: dict = {}
_PASSAGE_CACHE: dict = {}
_RDF_CACHE: dict = {}          # project → OWL+SPARQL 数据集（含 TBox 与个体断言）
_RDF_VIOLATIONS: dict = {}     # project → domain/range 校验结果
_STRIP_RE = re.compile(r"[的在于之为对及并或和与、，。；：（）()《》\s\"“”'．…]") if False else None


def _strip_punct(s: str) -> str:
    import re as _re
    return _re.sub(r"[的在于之为对及并或和与、，。；：（）()《》\s\"“”'．…：]", "", s)


def full_segments(meta) -> list:
    """meta → 条款粒度段落列表（缓存）"""
    key = meta.get("file", "")
    if key not in SEGMENT_CACHE:
        SEGMENT_CACHE[key] = kg15.split_to_segments(meta.get("text", ""))
    return SEGMENT_CACHE[key]


def _best_segment(target: str, meta, min_chars: int = 2):
    """在条款段里找 target 最相关的段：精确子串 → 去虚词子串 → 词覆盖率最高®
    返回 (segment, score)"""
    segs = full_segments(meta)
    if not segs:
        return "", 0.0
    t = _strip_punct(target or "")
    if not t:
        return "", 0.0
    chars = set(t)
    if min(len(chars), 2) < min_chars:
        return "", 0.0
    # 1) 精确子串
    for seg in segs:
        if target in seg:
            return seg, 1.0
    # 2) 去虚词后子串
    for seg in segs:
        if t in _strip_punct(seg):
            return seg, 0.95
    # 3) 词覆盖率最高
    best, best_score = "", 0.0
    for seg in segs:
        s = _strip_punct(seg)
        if not s or len(s) < max(8, len(t) * 1.2):
            continue
        hit = sum(1 for c in chars if c in s)
        if len(chars):
            score = hit / len(chars)
        else:
            score = 0.0
        if score > best_score:
            best, best_score = seg, score
    return best, best_score


def passage_for_entity(target: str, meta) -> str:
    """实体：返回包含它的完整条款段落（而不是文件/±80 截断）"""
    cache_key = ("e", meta.get("file", ""), target)
    if cache_key in _PASSAGE_CACHE:
        return _PASSAGE_CACHE[cache_key]
    seg, score = _best_segment(target, meta)
    out = seg if score >= 0.5 else ""
    _PASSAGE_CACHE[cache_key] = out
    return out


def passage_for_relation(s: str, o: str, meta) -> str:
    """关系：优先取"主语+宾语捆绑出现"的条款段；
    否则取两者词覆盖率合计最高段。"""
    cache_key = ("r", meta.get("file", ""), s, o)
    if cache_key in _PASSAGE_CACHE:
        return _PASSAGE_CACHE[cache_key]
    segs = full_segments(meta)
    out = ""
    st, ot = _strip_punct(s), _strip_punct(o)
    if st and ot:
        allchars = set(st) | set(ot)
        best, best_score = "", 0.0
        for seg in segs:
            ss, osgm = _strip_punct(seg), _strip_punct(seg)
            if not ss or len(ss) < max(8, len(st), len(ot)):
                continue
            subj_hit = sum(1 for c in set(st) if c in ss)
            obj_hit = sum(1 for c in set(ot) if c in ss)
            subj_ratio = subj_hit / len(set(st)) if set(st) else 0
            obj_ratio = obj_hit / len(set(ot)) if set(ot) else 0
            score = 0.6 * subj_ratio + 0.4 * obj_ratio
            if score > best_score:
                best, best_score = seg, score
        out = best if best_score >= 0.4 else ""
    _PASSAGE_CACHE[cache_key] = out
    return out


def build_project_from_meta(name: str, all_meta: list, model_note: str) -> dict:
    """把解析后的 all_meta 打包成离线 project。
    返回 manifest。之后检索都在 LOADED 内存里做，不依赖 15 脚本。
    """
    nodes, edges, entities, segments = [], [], [], []
    sources = {}
    for meta in all_meta:
        src = meta.get("file", "unknown")
        sources.setdefault(src, meta.get("text", ""))
        for e in meta.get("entities", []):
            nodes.append({
                "id": e.text, "label": e.label, "text": e.text,
                "confidence": round(e.confidence, 3),
                "source_file": src,
                "passage": passage_for_entity(e.text, meta),
            })
        for r in meta.get("relations", []):
            edges.append({
                "source": r.subject.text, "target": r.object.text,
                "predicate": r.predicate,
                "confidence": round(r.confidence, 3),
                "source_file": src,
                "passage": passage_for_relation(r.subject.text, r.object.text, meta),
            })
        for i, s in enumerate(kg15.split_to_segments(meta.get("text", ""))):
            segments.append({"seg_id": i, "text": s, "source_file": src})

    # 去重节点/边
    nid_seen, enodes = set(), []
    for n in nodes:
        if n["id"] not in nid_seen:
            nid_seen.add(n["id"]); enodes.append(n)
    eid_seen, eedges = set(), []
    for e in edges:
        k = (e["source"], e["predicate"], e["target"])
        if k not in eid_seen:
            eid_seen.add(k); eedges.append(e)
    nodes, edges = enodes, eedges

    # 实体向量库（corpus 与 index 行序一致 = 检索匹配的下标源）
    tmp_dir = ROOT / "data" / "kg_web" / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    store, corpus, dim = kg15.build_vector_store(all_meta, tmp_dir)
    ent_bytes = None
    if store is not None:
        import faiss
        ent_bytes = faiss.serialize_index(store.index)

    # 段向量库
    seg_bytes = None
    seg_store = None
    try:
        if segments:
            seg_store, _, _ = kg15.build_segment_store(all_meta, tmp_dir)
            if seg_store is not None and seg_store.index.ntotal:
                import faiss
                seg_bytes = faiss.serialize_index(seg_store.index)
    except Exception as e:
        print(f"⚠️ 段索引构建失败(可降级): {e}")

    manifest = {
        "name": name,
        "created": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stats": {"nodes": len(nodes), "edges": len(edges),
                  "entities": len(nodes), "relations": len(edges),
                  "segments": len(segments),
                  "sources": list(sources.keys())},
        "model": model_note,
        "embed_dim": dim,
        "sources": sources,
    }
    kg_project.save_project(name, manifest, nodes, edges,
                            corpus, segments,
                            ent_bytes if ent_bytes is not None else b"",
                            seg_bytes if seg_bytes is not None else b"")
    return manifest, nodes, edges, segments


# ============================================================
# 解析 job（子线程，避免阻塞）
# ============================================================
def _new_extractor():
    """解析/增量任务用的 LLM 抽取器。
    默认用 17 的 semantica 原生抽取（标题感知分块 + 偏移校正）；
    可用环境变量 KG_EXTRACTOR=15 强制回退旧直连抽取器。
    返回 (module, args) —— 按 module.process_file 签名调用。"""
    if os.environ.get("KG_EXTRACTOR", "17") == "15":
        from semantica.split import TextSplitter
        splitter = TextSplitter(method=["entity_aware", "recursive"],
                                chunk_size=8000, chunk_overlap=400,
                                ner_method="pattern")
        ner = rel = kg15.DirectLLMExtractor(shard_len=1000, workers=1,
                                            timeout=900, max_retries=3)
        return kg15, (ner, rel, splitter)
    if kg17 is not None:
        try:
            from types import SimpleNamespace as _NS
            splitter = _NS(chunk_size=8000, chunk_overlap=400)
            ner = kg17.SemanticaLLMExtractor(shard_len=8000, max_text_length=8000,
                                             min_confidence=0.5)
            return kg17, (ner, splitter)
        except Exception as e:
            print(f"⚠️ semantica(17) 抽取器初始化失败，回退 15: {e}", flush=True)
    from semantica.split import TextSplitter
    splitter = TextSplitter(method=["entity_aware", "recursive"],
                            chunk_size=8000, chunk_overlap=400,
                            ner_method="pattern")
    ner = rel = kg15.DirectLLMExtractor(shard_len=1000, workers=1,
                                        timeout=900, max_retries=3)
    return kg15, (ner, rel, splitter)


def _process_with_extractor(mod, fp, args) -> dict:
    """按模块签名调用 process_file 并 normalize，返回 meta"""
    if mod is kg17:
        ner, splitter = args
        meta = mod.process_file(fp, ner, splitter)
    else:
        ner, rel, splitter = args
        meta = mod.process_file(fp, ner, rel, splitter)
    meta["entities"], meta["relations"] = mod.normalize_result(
        meta["raw_entities"], meta["raw_relations"],
        meta.get("text", meta.get("full_text", "")))
    return meta


def run_parse_job(job_id: str, target_dir: str, demo: bool):
    job = JOBS[job_id]
    try:
        job["stage"] = "正在解析文本..."
        files = kg15.DATA / target_dir if target_dir else kg15.DATA / "rule_demo"
        if not Path(files).exists():
            raise FileNotFoundError(f"目录不存在: {files}")

        all_meta = []
        if demo:
            job["stage"] = "生成 demo 样例..."
            all_meta = kg15._build_demo_meta()
        else:
            mod, args = _new_extractor()
            files = sorted(p for p in Path(files).iterdir()
                           if p.suffix.lower() in (".txt", ".md"))
            if not files:
                raise FileNotFoundError(f"目录下没有 .txt/.md 文件: {target_dir}")
            job["total"] = len(files)
            for i, fp in enumerate(files):
                job["stage"] = f"处理 {fp.name} ({i+1}/{len(files)})..."
                all_meta.append(_process_with_extractor(mod, fp, args))
                job["done"] = i + 1

        # 打包 project
        safe_name = (Path(target_dir or "rule_demo").name
                     if not demo else "demo_商户直播行为规范")
        job["stage"] = "构建图 + 向量索引，写入离线 project..."
        name = f"{safe_name}_{datetime_now_ts()}"
        manifest, nodes, edges, segments = build_project_from_meta(name, all_meta, "qwen3.8-2.4t-a95b" if not demo else "demo")

        # 装载到内存
        proj = load_project_into_memory(name)
        job["stage"] = "完成"
        job["status"] = "success"
        job["project"] = name
        job["result"] = {"name": name, **manifest["stats"]}
    except Exception as e:
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        print(f"❌ job {job_id} 失败: {e}", flush=True)
        traceback.print_exc()
    finally:
        job["stage"] = job.get("stage", "") + ("（结束）" if job.get("status") else "")


# ============================================================
# 从 rule_demo_output 载入已有解析结果（JSON → project，不调 LLM）
# ============================================================
def _find_source_text(file_name: str) -> str:
    """按文件名找回原文 txt/md（供 passage / 段索引用）；找不到返回空串"""
    expected = Path(file_name).name
    cands = []
    for d in (ROOT / "data" / "rule_txt", ROOT / "data" / "rule_demo"):
        if not d.exists():
            continue
        for fp in d.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in (".txt", ".md"):
                if fp.name == expected:
                    return fp.read_text(encoding="utf-8", errors="ignore")
                if expected[:12] in fp.name or fp.name[:12] in expected:
                    cands.append(fp)
    if cands:
        cands.sort(key=lambda f: len(f.name), reverse=True)
        return cands[0].read_text(encoding="utf-8", errors="ignore")
    return ""


def collect_output_meta(folder) -> list:
    """扫描单个文件夹（含子目录）下的 *.json → all_meta（同文件取实体+关系最多的那次结果）。
    跳过 run_info.json / summary_*.json。"""
    if not folder.exists() or not folder.is_dir():
        return []
    # 递归收集 JSON（排除 run_info / summary）
    json_files = []
    for fp in sorted(folder.rglob("*.json")):
        if fp.name in ("run_info.json",) or fp.name.startswith("summary_"):
            continue
        json_files.append(fp)
    metas: dict[str, dict] = {}
    for fp in json_files:
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"⚠️ 跳过无法解析的 JSON {fp.name}: {e}", flush=True)
            continue
        items = d if isinstance(d, list) else [d]
        for m in items:
            if not isinstance(m, dict):
                continue
            fname = str(m.get("file") or fp.stem)
            ents, rels = [], []
            for e in m.get("entities", []):
                try:
                    ents.append(kg15._Ent(str(e.get("text", "")).strip() or "?",
                                          str(e.get("label", "Unknown")).strip(),
                                          float(e.get("confidence", 1.0))))
                except Exception:
                    pass
            for r in m.get("relations", []):
                try:
                    rels.append(kg15._Rel(str(r.get("subject", "")).strip(),
                                          str(r.get("predicate", "")).strip(),
                                          str(r.get("object", "")).strip(),
                                          float(r.get("confidence", 1.0))))
                except Exception:
                    pass
            if not ents and not rels:
                continue
            cur = metas.get(fname)
            if cur is None or len(ents) + len(rels) > len(cur["entities"]) + len(cur["relations"]):
                text = _find_source_text(fname)
                metas[fname] = {"file": fname, "text": text, "chars": len(text),
                                "entities": ents, "relations": rels}
    return list(metas.values())


def project_dirs(source: str = "rule_demo_output") -> list:
    """rule_demo_output 下的每个子文件夹 = 一个项目（返回 [(folder, project_name)]）。
    没有子文件夹时，根目录本身的 JSON 作为单个项目。"""
    base_dir = ROOT / "data" / source
    if not base_dir.exists() or not base_dir.is_dir():
        return []
    subdirs = sorted([d for d in base_dir.iterdir() if d.is_dir()], key=lambda d: d.name)
    if not subdirs:
        if collect_output_meta(base_dir):
            return [(base_dir, "rule_demo_output")]
        return []
    dirs = []
    for d in subdirs:
        if collect_output_meta(d):
            dirs.append((d, f"rule_demo_output_{d.name}"))
    return dirs


def run_import_job(job_id: str, source: str):
    job = JOBS[job_id]
    try:
        dirs = project_dirs(source)
        if not dirs:
            raise FileNotFoundError(f"{source} 下没有可用的 JSON 数据（请确认 JSON 已生成完毕）")
        job["total"] = len(dirs)
        built = []
        for i, (folder, name) in enumerate(dirs):
            try:
                all_meta = collect_output_meta(folder)
                total_e = sum(len(m["entities"]) for m in all_meta)
                total_r = sum(len(m["relations"]) for m in all_meta)
                job["stage"] = f"[{i + 1}/{len(dirs)}] {name}（实体 {total_e} / 关系 {total_r}）"
                job["done"] = i
                if name in LOADED:
                    del LOADED[name]
                job["stage"] = f"[{i + 1}/{len(dirs)}] 构建 {name}：图 + 向量索引..."
                kg_project.delete_project(name)
                manifest, nodes, edges, segments = build_project_from_meta(name, all_meta, folder.name)
                job["stage"] = f"[{i + 1}/{len(dirs)}] 装载 {name}..."
                load_project_into_memory(name)
                built.append({"name": name, **manifest["stats"]})
                job["done"] = i + 1
            except Exception as e:
                print(f"⚠️ 跳过 {name}: {e}", flush=True)
                traceback.print_exc()
        if not built:
            raise FileNotFoundError(f"{source} 下没有可用的 JSON 数据（请确认 JSON 已生成完毕）")
        job["status"] = "success"
        job["stage"] = f"完成：共导入 {len(built)} 个项目"
        job["project"] = built[0]["name"]
        job["result"] = {"projects": built, "name": built[0]["name"]}
    except Exception as e:
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        print(f"❌ job {job_id} 失败: {e}", flush=True)
        traceback.print_exc()
    finally:
        job["stage"] = job.get("stage", "") + ("（结束）" if job.get("status") else "")


def datetime_now_ts() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def load_project_into_memory(name: str) -> dict:
    """把离线 project → 内存：重建 SimpleStore + networkx 图，供检索/展示"""
    if name in LOADED:
        return LOADED[name]
    data = kg_project.load_project(name)
    import faiss

    embedder = kg15.get_embedder()

    # 重建实体检索 store
    store = None
    if data["ent_index_path"]:
        p = data["ent_index_path"]
        try:
            if os.path.getsize(p) > 0:
                with open(p, "rb") as f:
                    ent_bytes = f.read()
                store = DiskStore(ent_bytes, data["entities"], embedder)
        except Exception as e:
            print(f"⚠️ 实体索引载入失败(跳过检索): {e}", flush=True)

    # 段 store
    seg_store = None
    if data["seg_index_path"]:
        p = data["seg_index_path"]
        try:
            if os.path.getsize(p) > 0:
                with open(p, "rb") as f:
                    seg_bytes = f.read()
                seg_store = DiskStore(seg_bytes, data["segments"], embedder)
        except Exception as e:
            print(f"⚠️ 段索引载入失败(跳过段检索): {e}", flush=True)

    # 图
    import networkx as nx
    G = nx.DiGraph()
    for n in data["nodes"]:
        G.add_node(n["id"], label=n["label"], confidence=n["confidence"],
                   source_file=n.get("source_file", ""), passage=n.get("passage", ""))
    for e in data["edges"]:
        G.add_edge(e["source"], e["target"], predicate=e["predicate"],
                   confidence=e["confidence"], source_file=e.get("source_file", ""),
                   passage=e.get("passage", ""))

    LOADED[name] = {
        "name": name,
        "manifest": data["manifest"],
        "G": G,
        "store": store,
        "seg_store": seg_store,
        "nodes": data["nodes"],
        "edges": data["edges"],
        "entities": data["entities"],
        "segments": data["segments"],
    }
    return LOADED[name]


def _ensure_loaded(name: str):
    """取内存项目；未加载则从磁盘装载（QA/消歧等可按项目名直接使用）"""
    proj = LOADED.get(name)
    if proj is None:
        try:
            proj = load_project_into_memory(name)
        except Exception as e:
            return JSONResponse({"error": f"项目载入失败: {e}"}, status_code=404)
    return proj


def graph_to_payload(G, hop_nodes=None) -> dict:
    """序列化 networkx 图（可选只保留 hop_nodes 子集）"""
    import networkx as nx
    nodes, edges = [], []
    node_ids = set()
    if hop_nodes is None:
        hop_nodes = set(G.nodes())
    for nid in hop_nodes:
        if nid not in G:
            continue
        d = G.nodes[nid]
        node_ids.add(nid)
        nodes.append({"id": nid, "label": d.get("label", "?"),
                      "confidence": d.get("confidence", 0),
                      "source_file": d.get("source_file", ""),
                      "passage": d.get("passage", "")})
    for u, v, d in G.edges(data=True):
        if u in node_ids and v in node_ids:
            edges.append({"source": u, "target": v,
                          "predicate": d.get("predicate", "?"),
                          "confidence": d.get("confidence", 0)})
    return {"nodes": nodes, "edges": edges}


def subgraph_around(G, seed, radius: int) -> set:
    """seed 为中心 radius 跳的节点集合（含双向边）"""
    import networkx as nx
    try:
        und = G.to_undirected()
        return set(nx.single_source_shortest_path_length(und, seed, cutoff=radius).keys())
    except Exception:
        return {seed}


class DiskStore:
    """从离线 FAISS 文件反序列化的只读检索 store（与 simple store 同接口）"""
    def __init__(self, index_bytes: bytes, corpus, embedder):
        import faiss, numpy as np
        blob = np.frombuffer(index_bytes, dtype=np.uint8)
        self.index = faiss.deserialize_index(blob)
        self.corpus = corpus
        self.dim = self.index.d
        self.embedder = embedder

    def search(self, query: str, k: int = 5):
        with _STORE_LOCK:
            import numpy as np
            v = self.embedder.encode([query], normalize_embeddings=True).astype(np.float32)
            scores, ids = self.index.search(v, k)
            results = []
            for s, i in zip(scores[0], ids[0]):
                if i < 0 or i >= len(self.corpus):
                    continue
                c = self.corpus[int(i)]
                results.append({
                    "score": float(s),
                    "text": c.get("text", c.get("id", "")),
                    "label": c.get("label", ""),
                    "passage": c.get("passage", ""),
                    "source_file": c.get("source_file", ""),
                    "confidence": c.get("confidence", 1.0),
                })
            return results

    def count(self):
        return self.index.ntotal


# ============================================================
# API
# ============================================================
@_api.get("/projects")
def api_projects():
    return kg_project.list_projects()


@_api.post("/parse")
def api_parse(payload: dict):
    """body: {dir: "rule_demo", demo: true} → 开一个后台 job"""
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "running", "stage": "排队中", "done": 0, "total": 0}
    t = threading.Thread(target=run_parse_job,
                         args=(job_id, payload.get("dir", ""), bool(payload.get("demo", False))),
                         daemon=True)
    t.start()
    return {"job_id": job_id}


def _resolve_run_dir(source: str) -> str:
    """把 import source 解析成实际 run 目录名（source=rule_demo_output → 最新 run）"""
    base_dir = ROOT / "data" / "rule_demo_output"
    if source == "rule_demo_output":
        run_dirs = sorted([d for d in base_dir.iterdir()
                           if d.is_dir() and d.name.startswith("run_")
                           and "_demo" not in d.name], reverse=True)
        return run_dirs[0].name if run_dirs else "rule_demo_output"
    return source


@_api.post("/import")
def api_import(payload: dict = None):
    """body: {source: "rule_demo_output"} → 从 rule_demo_output JSON 载入为项目"""
    payload = payload or {}
    source = payload.get("source", "rule_demo_output")
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "running", "stage": "排队中", "done": 0, "total": 0}
    t = threading.Thread(target=run_import_job, args=(job_id, source), daemon=True)
    t.start()
    return {"job_id": job_id}


@_api.get("/jobs/{job_id}")
def api_job(job_id: str):
    j = JOBS.get(job_id)
    if j is None:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return j


@_api.get("/project/{name}/load")
def api_load(name: str):
    try:
        proj = load_project_into_memory(name)
        return {"ok": True, "manifest": proj["manifest"],
                "stats": {"nodes": len(proj["nodes"]), "edges": len(proj["edges"]),
                          "segments": len(proj["segments"]),
                          "dup_nodes": len(proj["nodes"]),
                          "dup_edges": len(proj["edges"])}}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)


@_api.get("/graph/{name}")
def api_graph(name: str, hop: int = 0, node: str = ""):
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    G = proj["G"]
    if hop and node and node in G:
        sub = subgraph_around(G, node, int(hop))
    else:
        sub = None
    return graph_to_payload(G, sub)


@_api.get("/node/{name}/{node_id}")
def api_node(name: str, node_id: str):
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    G = proj["G"]
    if node_id not in G:
        return JSONResponse({"error": "节点不存在"}, status_code=404)
    d = G.nodes[node_id]
    out_edges, in_edges = [], []
    for _, v, e in G.out_edges(node_id, data=True):
        out_edges.append({"predicate": e["predicate"], "target": v,
                          "confidence": e.get("confidence", 0),
                          "passage": e.get("passage", "")})
    for u, _, e in G.in_edges(node_id, data=True):
        in_edges.append({"predicate": e["predicate"], "source": u,
                         "confidence": e.get("confidence", 0),
                         "passage": e.get("passage", "")})
    # 邻域 1-hop 子图
    sub = subgraph_around(G, node_id, 1)
    return {
        "id": node_id,
        "label": d.get("label", "?"),
        "confidence": d.get("confidence", 0),
        "source_file": d.get("source_file", ""),
        "passage": d.get("passage", ""),
        "in_degree": G.in_degree(node_id),
        "out_degree": G.out_degree(node_id),
        "out_edges": out_edges,
        "in_edges": in_edges,
        "neighborhood": graph_to_payload(G, sub),
    }


@_api.get("/search/{name}")
def api_search(name: str, q: str, k_ent: int = 5, k_seg: int = 5, hops: int = 2):
    """检索：返回三类展开
      matches  - 匹配到的实体
      segments - 命中的原文段落
      graph    - 由 seed 实体出发的图链路子图
    """
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj

    result = {"matches": [], "segments": [], "graph": {"nodes": [], "edges": []},
              "seed": None}

    # 1) 实体
    if proj["store"]:
        try:
            result["matches"] = proj["store"].search(q, k=int(k_ent))
        except Exception as e:
            print(f"⚠️ 实体检索: {e}")

    seed = None
    if result["matches"]:
        seed = result["matches"][0]["text"]

    # 2) 原文段
    if proj["seg_store"]:
        try:
            result["segments"] = proj["seg_store"].search(q, k=int(k_seg))
        except Exception as e:
            print(f"⚠️ 段检索: {e}")

    # 3) 图链路
    if seed and seed in proj["G"]:
        node_ids = subgraph_around(proj["G"], seed, int(hops))
        result["graph"] = graph_to_payload(proj["G"], node_ids)
        result["seed"] = seed

    return result


@_api.delete("/project/{name}")
def api_delete(name: str):
    kg_project.delete_project(name)
    LOADED.pop(name, None)
    return {"ok": True}


# ============================================================
# 实体类目（按 label 分组展示）
# ============================================================
@_api.get("/category/{name}")
def api_categories(name: str):
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    order = _class_order()
    bucket: dict[str, list] = defaultdict(list)
    for n in proj["nodes"]:
        bucket[n["label"]].append(n)

    def sort_key(label):
        return order.index(label) if label in order else len(order)

    cats = []
    for label in sorted(bucket.keys(), key=sort_key):
        ents = sorted(bucket[label], key=lambda x: -x.get("confidence", 0))
        group = next((g for g, cls in CLASS_GROUPS if label in cls), "其他")
        cats.append({
            "label": label,
            "cn": CLASS_DESC.get(label, ""),
            "group": group,
            "count": len(ents),
            "entities": ents,
        })
    return {"project": name, "total": len(proj["nodes"]), "categories": cats}


@_api.get("/ontology/{name}")
def api_ontology(name: str):
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    label_count = Counter(n["label"] for n in proj["nodes"])
    classes = []
    for group_name, cls_list in CLASS_GROUPS:
        for c in cls_list:
            classes.append({
                "label": c,
                "cn": CLASS_DESC.get(c, ""),
                "group": group_name,
                "count": label_count.get(c, 0),
            })
    # 未收录但实际出现的类型
    used_labels = {n["label"] for n in proj["nodes"]}
    for extra in sorted(used_labels - set(CLASS_DESC)):
        classes.append({
            "label": extra, "cn": "", "group": "其他",
            "count": label_count.get(extra, 0),
        })

    pred_count = Counter(e["predicate"] for e in proj["edges"])
    label_of = {n["id"]: n["label"] for n in proj["nodes"]}
    dr: dict = defaultdict(lambda: {"domains": set(), "ranges": set()})
    for e in proj["edges"]:
        d = dr[e["predicate"]]
        d["domains"].add(label_of.get(e["source"], "Unknown"))
        d["ranges"].add(label_of.get(e["target"], "Unknown"))

    predicates, seen = [], set()
    for pred in PRED_DESC:
        d = dr.get(pred)
        predicates.append({
            "predicate": pred, "cn": PRED_DESC[pred],
            "count": pred_count.get(pred, 0),
            "domains": sorted(d["domains"]) if d else [],
            "ranges": sorted(d["ranges"]) if d else [],
            "used": pred in dr,
        })
        seen.add(pred)
    for pred in sorted(dr.keys() - seen):
        d = dr[pred]
        predicates.append({
            "predicate": pred, "cn": "",
            "count": pred_count.get(pred, 0),
            "domains": sorted(d["domains"]), "ranges": sorted(d["ranges"]),
            "used": True,
        })
    return {
        "project": name,
        "groups": [{"name": g, "classes": [c["label"] for c in classes if c["group"] == g]}
                   for g, _ in CLASS_GROUPS],
        "classes": classes,
        "predicates": predicates,
        # 声明式本体（meituan_ontology.ttl）：类层级/关系 domain/range，区别于上面"数据反推"
        "declared": _owl.ontology_info() if _owl else None,
        "stats": {
            "nodes": len(proj["nodes"]),
            "edges": len(proj["edges"]),
            "labels_used": len(used_labels),
            "predicates_used": len(dr),
        },
    }


@_api.get("/ontology-editor/source")
def api_ontology_source():
    """读取声明式本体源文件，供本地治理页面小范围维护。"""
    if not _owl:
        return JSONResponse({"ok": False, "error": "ontology_owl 未加载"}, status_code=503)
    path = Path(_owl.ONTOLOGY_FILE).resolve()
    if not path.exists():
        return JSONResponse({"ok": False, "error": f"本体文件不存在: {path}"}, status_code=404)
    return {"ok": True, "path": str(path),
            "content": path.read_text(encoding="utf-8")}


@_api.post("/ontology-editor/source")
def api_save_ontology_source(payload: dict):
    """校验 Turtle 后原子保存；原文件先写入 ontology/backups。"""
    if not _owl:
        return JSONResponse({"ok": False, "error": "ontology_owl 未加载"}, status_code=503)
    content = str((payload or {}).get("content", ""))
    if not content.strip():
        return JSONResponse({"ok": False, "error": "本体内容不能为空"}, status_code=400)
    if len(content.encode("utf-8")) > 1024 * 1024:
        return JSONResponse({"ok": False, "error": "本体文件不能超过 1MB"}, status_code=400)

    try:
        from rdflib import Graph as RDFGraph, RDF, OWL
        parsed = RDFGraph()
        parsed.parse(data=content, format="turtle")
        class_count = sum(1 for _ in parsed.subjects(RDF.type, OWL.Class))
        property_count = sum(1 for _ in parsed.subjects(RDF.type, OWL.ObjectProperty))
        if class_count == 0:
            raise ValueError("没有找到 owl:Class 声明")
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Turtle 校验失败: {e}"}, status_code=400)

    import shutil
    path = Path(_owl.ONTOLOGY_FILE).resolve()
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = backup_dir / f"{path.stem}_{stamp}{path.suffix}"
    if path.exists():
        shutil.copy2(path, backup)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, path)
        cache = getattr(_owl, "_TBOX_CACHE", None)
        if isinstance(cache, dict):
            cache.clear()
        _RDF_CACHE.clear()
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)
    return {"ok": True, "path": str(path), "backup": str(backup),
            "classes": class_count, "object_properties": property_count,
            "restart_required": True}


# ============================================================
# OWL+SPARQL：项目个体 → RDF 数据集 + 查询 / 校验
# ============================================================
def _project_rdf(name: str):
    """把已加载项目的 nodes/edges 建成"本体 + 个体"数据集（进程内缓存）"""
    if name not in _RDF_CACHE:
        proj = _ensure_loaded(name)
        if isinstance(proj, JSONResponse):
            return proj, None
        g, _ = _owl.build_dataset(proj["nodes"], proj["edges"])
        _RDF_CACHE[name] = g
    return None, _RDF_CACHE[name]


def _run_sparql(name: str, query: str):
    if not _owl:
        return JSONResponse({"ok": False, "error": "ontology_owl 未加载"},
                            status_code=503)
    if not (query or "").strip():
        return JSONResponse({"ok": False, "error": "query 不能为空"}, status_code=400)
    err, g = _project_rdf(name)
    if err:
        return err
    try:
        res = _owl.run_query(g, query)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"},
                            status_code=400)
    res["ok"] = True
    res["project"] = name
    return res


@_api.get("/sparql/{name}")
def api_sparql_get(name: str, query: str = ""):
    """GET /api/sparql/{name}?query=SELECT...
    返回 {columns, rows, count} 或 {ask}"""
    return _run_sparql(name, query)


@_api.post("/sparql/{name}")
def api_sparql_post(name: str, body: dict):
    """POST /api/sparql/{name}  body: {"query": "..."}"""
    return _run_sparql(name, (body or {}).get("query", ""))


@_api.get("/sparql-check/{name}")
def api_sparql_check(name: str):
    """domain/range 一致性校验（识别抽取污染）"""
    if not _owl:
        return JSONResponse({"ok": False, "error": "ontology_owl 未加载"},
                            status_code=503)
    err, g = _project_rdf(name)
    if err:
        return err
    rep = _owl.validate(g)
    rep["ok"] = True
    rep["project"] = name
    return rep


def _group_of(label: str) -> str:
    for g, cls_list in CLASS_GROUPS:
        if label in cls_list:
            return g
    return "其他"


# ============================================================
# 看板（Dashboard）：总数、分布、Top 节点、来源一览
# ============================================================
@_api.get("/dashboard/{name}")
def api_dashboard(name: str):
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    G = proj["G"]
    label_count = Counter(n["label"] for n in proj["nodes"])
    pred_count = Counter(e["predicate"] for e in proj["edges"])
    by_file = Counter(n.get("source_file", "") for n in proj["nodes"])
    sources = [
        {"file": f or "(未知)", "chars": len(t),
         "entities": by_file.get(f or "", 0)}
        for f, t in proj["manifest"].get("sources", {}).items()
    ]
    sources.sort(key=lambda s: -s["entities"])
    top = sorted(G.degree, key=lambda x: (-x[1], x[0]))[:14]
    return {
        "project": name,
        "manifest": {
            "created": proj["manifest"].get("created"),
            "model": proj["manifest"].get("model"),
            "stats": proj["manifest"].get("stats", {}),
        },
        "totals": {
            "entities": len(proj["nodes"]),
            "relations": len(proj["edges"]),
            "segments": len(proj["segments"]),
            "labels": len(label_count),
            "predicates": len(pred_count),
            "sources": len(proj["manifest"].get("sources", {})),
        },
        "types": [
            {"label": c, "cn": CLASS_DESC.get(c, ""), "group": _group_of(c), "count": n}
            for c, n in label_count.most_common()
        ],
        "predicates": [
            {"predicate": p, "cn": PRED_DESC.get(p, ""), "count": n}
            for p, n in pred_count.most_common(12)
        ],
        "top_nodes": [
            {"id": n, "label": G.nodes[n].get("label", "?"), "degree": d}
            for n, d in top
        ],
        "sources": sources,
    }


# ============================================================
# 脑图（树形 mind map：以某节点为根，沿有向边展开）
# ============================================================
def build_mindmap_tree(G, root: str, depth: int, max_children: int = 40) -> dict:
    import networkx as nx
    root_node = {
        "name": root,
        "value": G.nodes[root].get("label", "?"),
        "depth": 0,
        "children": [],
    }
    visited = {root}
    queue = deque([(root_node, root, 0)])
    while queue:
        parent, nid, dep = queue.popleft()
        if dep >= depth:
            continue
        kept = 0
        for c in G.successors(nid):
            if kept >= max_children:
                break
            if c in visited:
                continue
            visited.add(c)
            cd = {
                "name": c,
                "value": G.nodes[c].get("label", "?"),
                "depth": dep + 1,
                "children": [],
            }
            parent["children"].append(cd)
            queue.append((cd, c, dep + 1))
            kept += 1
    return root_node


@_api.get("/mindmap/{name}")
def api_mindmap(name: str, root: str, depth: int = 3):
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    G = proj["G"]
    if not root or root not in G:
        roots_res = api_roots(name)
        if isinstance(roots_res, dict) and roots_res.get("roots"):
            root = roots_res["roots"][0]["id"]
    if root not in G:
        return JSONResponse({"error": f"根节点不存在: {root}"}, status_code=404)
    depth = max(1, min(int(depth), 6))
    tree = build_mindmap_tree(G, root, depth)
    return {"project": name, "root": root, "depth": depth, "tree": tree}


@_api.get("/roots/{name}")
def api_roots(name: str):
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    G = proj["G"]
    by_out = sorted(G.nodes, key=lambda n: (-G.out_degree(n), -G.degree(n)))
    roots = [n for n in by_out
             if not (G.nodes[n].get("label") == "RuleDocument" and G.out_degree(n) == 0)]
    return {"roots": [
        {"id": r, "label": G.nodes[r].get("label", "?"), "degree": G.degree(r)}
        for r in roots[:40]]}


# ============================================================
# 数据源：列出项目原始文档 + 原文内容（供"数据源"视图）
# ============================================================
@_api.get("/sources/{name}")
def api_sources(name: str):
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    texts = proj["manifest"].get("sources") or {}
    ent_c = Counter(); rel_c = Counter(); seg_c = Counter()
    for n in proj["nodes"]:
        ent_c[n.get("source_file", "")] += 1
    for e in proj["edges"]:
        rel_c[e.get("source_file", "")] += 1
    for s in proj["segments"]:
        seg_c[s.get("source_file", "")] += 1
    files = set(texts)
    files |= set(ent_c) | set(rel_c) | set(seg_c)
    files.discard("")
    files.discard("[手动添加]")
    out = []
    for f in sorted(files):
        t = texts.get(f, "")
        out.append({
            "file": f,
            "chars": len(t),
            "has_text": bool(t.strip()),
            "text": t,
            "entities": ent_c.get(f, 0),
            "relations": rel_c.get(f, 0),
            "segments": seg_c.get(f, 0),
        })
    return {"ok": True, "sources": out}


# ============================================================
# 脚本/文档上传
# ============================================================
UPLOAD_DIR = ROOT / "data" / "kg_web" / "uploads"


@_api.post("/upload")
async def api_upload(kind: str = Form("script"), file: UploadFile = File(...)):
    raw_name = (file.filename or "upload").strip()
    name = Path(raw_name).name  # 去掉路径部分
    if kind == "script":
        if not name.lower().endswith(".py"):
            return JSONResponse({"ok": False, "error": "脚本只支持 .py 文件"}, status_code=400)
        dest = UPLOAD_DIR / "scripts"
    else:
        if not name.lower().endswith((".txt", ".md", ".doc", ".docx", ".pdf", ".json")):
            return JSONResponse({"ok": False, "error": "文档支持 .txt/.md/.doc/.docx/.pdf/.json"}, status_code=400)
        dest = UPLOAD_DIR / "documents"
    dest.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    if not content:
        return JSONResponse({"ok": False, "error": "文件内容为空"}, status_code=400)
    target = dest / name
    if target.exists():
        target = dest / f"{Path(name).stem}_{datetime_now_ts()}{Path(name).suffix}"
    target.write_bytes(content)
    return {"ok": True, "kind": kind, "filename": target.name,
            "path": str(target), "size": len(content)}


@_api.get("/scripts")
def api_scripts():
    out = []
    for kind, sub in (("script", "scripts"), ("document", "documents")):
        d = UPLOAD_DIR / sub
        if not d.exists():
            continue
        for f in sorted(d.iterdir(), key=lambda x: -x.stat().st_mtime):
            out.append({
                "kind": kind, "filename": f.name, "path": str(f),
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
    return out


# ============================================================
# 手动添加实体 / 关系（写进内存图 + 落盘，向量库增量更新）
# ============================================================
def _persist_graph(proj):
    p = kg_project.project_path(proj["name"])
    (p / "graph.json").write_text(
        json.dumps({"nodes": proj["nodes"], "edges": proj["edges"]},
                   ensure_ascii=False, indent=2), encoding="utf-8")


def _persist_entities(proj):
    p = kg_project.project_path(proj["name"])
    (p / "entities.json").write_text(
        json.dumps(proj["entities"], ensure_ascii=False, indent=2), encoding="utf-8")
    emb = p / "embeddings"
    store = proj.get("store")
    if store is not None and getattr(store, "index", None) is not None:
        import faiss
        with _STORE_LOCK:
            ent_bytes = faiss.serialize_index(store.index)
        (emb / "ent.index").write_bytes(ent_bytes)
        (emb / "ent.corpus.json").write_text(
            json.dumps(proj["entities"], ensure_ascii=False, indent=2), encoding="utf-8")


def _ensure_node(proj, text: str, label: str = "Unknown"):
    """确保节点存在（关系可挂到未抽到的实体名上）"""
    if text in proj["G"]:
        return None
    node = {"id": text, "label": label, "text": text, "confidence": 0.0,
            "source_file": _MANUAL_SRC, "passage": ""}
    proj["nodes"].append(node)
    proj["G"].add_node(text, label=label, confidence=0.0, source_file=_MANUAL_SRC, passage="")
    return node


@_api.post("/add-entity/{name}")
def api_add_entity(name: str, payload: dict):
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    text = str(payload.get("text", "")).strip()
    label = str(payload.get("label", "")).strip() or "Unknown"
    if not text:
        return JSONResponse({"ok": False, "error": "实体名称不能为空"}, status_code=400)
    canon = _canonical_of(proj, text)
    if canon and canon != text and canon in proj["G"]:
        return JSONResponse({"ok": False, "code": "alias", "canonical": canon,
                             "error": f"“{text}” 是 “{canon}” 的别名（已归一），请直接用规范名或前往消歧面板合并。"},
                            status_code=409)
    if text in proj["G"]:
        return JSONResponse({"ok": False, "error": f"实体已存在: {text}"}, status_code=409)
    try:
        conf = min(1.0, max(0.0, float(payload.get("confidence", 1.0) or 1.0)))
    except Exception:
        conf = 1.0
    source_file = str(payload.get("source_file", "")).strip() or _MANUAL_SRC
    passage = str(payload.get("passage", "")).strip()

    node = {"id": text, "label": label, "text": text, "confidence": round(conf, 3),
            "source_file": source_file, "passage": passage}
    proj["nodes"].append(node)
    proj["G"].add_node(text, label=label, confidence=conf,
                       source_file=source_file, passage=passage)

    # 向量库增量：实体宿主体也可被检索
    store = proj.get("store")
    if store is not None:
        corpus_ent = dict(node)
        corpus_ent["chunk_id"] = 0
        corpus_ent["embed_input"] = (f"{text} ({label}) | {passage[:60]}"
                                     if passage else f"{text} ({label})")
        if text not in {x.get("text") for x in proj["entities"]}:
            proj["entities"].append(corpus_ent)
        try:
            with _STORE_LOCK:
                import numpy as np
                vec = store.embedder.encode([corpus_ent["embed_input"]],
                                            normalize_embeddings=True).astype(np.float32)
                store.index.add(vec)
        except Exception as e:
            print(f"⚠️ 实体向量增长失败(可降级): {e}")

    _persist_graph(proj)
    _persist_entities(proj)
    return {"ok": True, "node": node,
            "stats": {"nodes": len(proj["nodes"]), "edges": len(proj["edges"])}}


@_api.post("/relabel-entity/{name}")
def api_relabel_entity(name: str, payload: dict = None):
    """修正已有实体类型；不改实体名称和关系，并保留可回滚快照。"""
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    body = payload if isinstance(payload, dict) else {}
    entity_id = str(body.get("id", "")).strip()
    label = str(body.get("label", "")).strip()
    if not entity_id or not label:
        return JSONResponse({"ok": False, "error": "实体和目标类型不能为空"}, status_code=400)
    if entity_id not in proj["G"]:
        return JSONResponse({"ok": False, "error": f"实体不存在: {entity_id}"}, status_code=404)

    allowed = set(CLASS_DESC)
    if _owl:
        try:
            allowed.update(_owl.entity_type_whitelist())
        except Exception:
            pass
    if label not in allowed:
        return JSONResponse({"ok": False, "error": f"目标类型不在当前本体中: {label}"}, status_code=400)

    old_label = str(proj["G"].nodes[entity_id].get("label", "Unknown"))
    if old_label == label:
        return {"ok": True, "id": entity_id, "old_label": old_label,
                "label": label, "changed": False}
    version = _snapshot_project(name)
    proj["G"].nodes[entity_id]["label"] = label
    for node in proj["nodes"]:
        if node.get("id") == entity_id:
            node["label"] = label
    for entity in proj["entities"]:
        if (entity.get("text") or entity.get("id")) == entity_id:
            entity["label"] = label
            passage = str(entity.get("passage", ""))
            entity["embed_input"] = (f"{entity_id} ({label}) | {passage[:60]}"
                                     if passage else f"{entity_id} ({label})")
    _rebuild_entity_index(proj)
    _persist_graph(proj)
    _persist_entities(proj)
    _RDF_CACHE.pop(name, None)
    return {"ok": True, "id": entity_id, "old_label": old_label,
            "label": label, "changed": True, "version": version}


@_api.post("/add-relation/{name}")
def api_add_relation(name: str, payload: dict):
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    source = str(payload.get("source", "")).strip()
    predicate = str(payload.get("predicate", "")).strip()
    target = str(payload.get("target", "")).strip()
    if not source or not predicate or not target:
        return JSONResponse({"ok": False, "error": "主语/谓词/宾语不能为空"}, status_code=400)
    try:
        conf = min(1.0, max(0.0, float(payload.get("confidence", 1.0) or 1.0)))
    except Exception:
        conf = 1.0
    for t in (source, target):
        if t and t not in proj["G"]:
            _ensure_node(proj, t)
    if proj["G"].has_edge(source, target):
        # 同一 (s,o) 只保留一条谓词，避免前端重复
        proj["G"].remove_edge(source, target)
        proj["edges"] = [e for e in proj["edges"]
                         if not (e["source"] == source and e["target"] == target)]
    proj["G"].add_edge(source, target, predicate=predicate, confidence=conf,
                       source_file=_MANUAL_SRC, passage="")
    proj["edges"].append({"source": source, "target": target, "predicate": predicate,
                          "confidence": round(conf, 3),
                          "source_file": _MANUAL_SRC, "passage": ""})
    _persist_graph(proj)
    return {"ok": True, "edge": {"source": source, "predicate": predicate, "target": target},
            "stats": {"nodes": len(proj["nodes"]), "edges": len(proj["edges"])}}


# ============================================================
# 删除实体（连带删边 + 重建向量索引，落盘）
# ============================================================
@_api.post("/delete-entity/{name}")
def api_delete_entity(name: str, payload: dict = None):
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    body = payload if isinstance(payload, dict) else {}
    eid = str(body.get("id", "")).strip()
    if not eid:
        return JSONResponse({"ok": False, "error": "缺少实体 id"}, status_code=400)
    if eid not in proj["G"]:
        return JSONResponse({"ok": False, "error": f"实体不存在: {eid}"}, status_code=404)
    G = proj["G"]

    # 1) 图：删节点与关联边
    G.remove_node(eid)
    proj["nodes"] = [n for n in proj["nodes"] if n.get("id") != eid]
    proj["edges"] = [e for e in proj["edges"]
                     if e.get("source") != eid and e.get("target") != eid]

    # 2) 向量库：从 corpus 移除并重建索引（保持 index 与 corpus 对齐）
    before = len(proj["entities"])
    proj["entities"] = [c for c in proj["entities"]
                        if (c.get("text") or c.get("id")) != eid]
    store = proj.get("store")
    if store is not None and getattr(store, "index", None) is not None \
            and len(proj["entities"]) != before:
        try:
            import faiss
            import numpy as np
            texts = [c.get("embed_input")
                     or f"{c.get('text', c.get('id', '?'))} ({c.get('label', '')})"
                     for c in proj["entities"]]
            vecs = store.embedder.encode(texts, normalize_embeddings=True).astype(np.float32)
            nb = faiss.IndexFlatIP(vecs.shape[1])
            if vecs.shape[0]:
                nb.add(vecs)
            store.index = nb
        except Exception as e:
            print(f"⚠️ 实体向量索引重建失败(搜索可能不同步): {e}", flush=True)

    _persist_graph(proj)
    _persist_entities(proj)
    return {"ok": True, "deleted": eid,
            "stats": {"nodes": len(proj["nodes"]), "edges": len(proj["edges"]),
                      "entities": len(proj["entities"])}}


# ============================================================
# Phase A1 —— 实体消歧 / 别名归一 / 合并
# ============================================================
_RESOLVE_SIM = 0.78   # “疑似重复”建议阈值（余弦）

def _alias_map(proj) -> dict:
    m = proj["manifest"]
    if not isinstance(m.get("aliases"), dict):
        m["aliases"] = {}
    return m["aliases"]


def _canonical_of(proj, text: str) -> str:
    """别名 → 规范名（未命中则返回原文）"""
    t = (text or "").strip()
    if not t:
        return ""
    for canon, al in _alias_map(proj).items():
        for a in al:
            if a.strip().lower() == t.lower():
                return canon
    return t


def _rebuild_entity_index(proj):
    """按 proj["entities"] 全量重建实体 FAISS 索引（merge/delete 后保持一致）"""
    store = proj.get("store")
    if store is None or getattr(store, "index", None) is None:
        return
    try:
        with _STORE_LOCK:
            import faiss
            import numpy as np
            texts = [c.get("embed_input")
                     or f"{c.get('text', c.get('id', '?'))} ({c.get('label', '')})"
                     for c in proj["entities"]]
            if not texts:
                return
            vecs = store.embedder.encode(texts, normalize_embeddings=True).astype(np.float32)
            nb = faiss.IndexFlatIP(vecs.shape[1])
            nb.add(vecs)
            store.index = nb
    except Exception as e:
        print(f"⚠️ 实体索引重建失败(搜索可能不同步): {e}", flush=True)


def _persist_manifest(proj):
    p = kg_project.project_path(proj["name"])
    (p / "manifest.json").write_text(
        json.dumps(proj["manifest"], ensure_ascii=False, indent=2), encoding="utf-8")


def _sync_manifest_stats(proj):
    m = proj["manifest"]
    m.setdefault("stats", {})
    m["stats"]["nodes"] = len(proj["nodes"])
    m["stats"]["edges"] = len(proj["edges"])
    m["stats"]["entities"] = len(proj["entities"])
    m["stats"]["relations"] = len(proj["edges"])
    m["stats"]["segments"] = len(proj["segments"])


@_api.get("/aliases/{name}")
def api_aliases(name: str):
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    am = _alias_map(proj)
    return {"ok": True, "aliases": {k: v for k, v in am.items() if v}}


@_api.post("/aliases/{name}")
def api_add_alias(name: str, payload: dict = None):
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    payload = payload or {}
    entity = str(payload.get("entity", "")).strip()
    alias = str(payload.get("alias", "")).strip()
    remove = bool(payload.get("remove", False))
    if not entity or entity not in proj["G"]:
        return JSONResponse({"ok": False, "error": f"实体不存在: {entity}"}, status_code=404)
    am = _alias_map(proj)
    if remove:
        al = am.get(entity, [])
        if alias in al:
            al.remove(alias)
        if not am.get(entity):
            am.pop(entity, None)
    else:
        if not alias:
            return JSONResponse({"ok": False, "error": "别名不能为空"}, status_code=400)
        if alias.strip().lower() == entity.strip().lower():
            return JSONResponse({"ok": False, "error": "别名不能与实体同名"}, status_code=400)
        am.setdefault(entity, [])
        if alias not in am[entity]:
            am[entity].append(alias)
    _persist_manifest(proj)
    return {"ok": True, "aliases": {k: v for k, v in am.items() if v}}


@_api.post("/merge-entities/{name}")
def api_merge_entities(name: str, payload: dict = None):
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    payload = payload or {}
    keep = str(payload.get("keep", "")).strip()
    drop = str(payload.get("drop", "")).strip()
    G = proj["G"]
    if keep == drop:
        return JSONResponse({"ok": False, "error": "两个实体不能相同"}, status_code=400)
    if keep not in G:
        return JSONResponse({"ok": False, "error": f"保留实体不存在: {keep}"}, status_code=404)
    if drop not in G:
        return JSONResponse({"ok": False, "error": f"被合并实体不存在: {drop}"}, status_code=404)

    # 1) 图：迁移 drop 的出/入边到 keep，删除重复 (keep,keep) 自环
    for _, v, ed in list(G.out_edges(drop, data=True)):
        if v == keep:
            continue
        if not G.has_edge(keep, v):
            G.add_edge(keep, v, **ed)
    for u, _, ed in list(G.in_edges(drop, data=True)):
        if u == keep:
            continue
        if not G.has_edge(u, keep):
            G.add_edge(u, keep, **ed)
    G.remove_node(drop)

    # 2) 从 G 重建 nodes / edges（保证一致）
    proj["nodes"] = [{"id": nid, "text": nid, "label": d.get("label", "Unknown"),
                      "confidence": round(d.get("confidence", 0), 3),
                      "source_file": d.get("source_file", ""),
                      "passage": d.get("passage", "")}
                     for nid, d in G.nodes(data=True)]
    proj["edges"] = [{"source": u, "target": v, "predicate": ed.get("predicate", "?"),
                      "confidence": round(ed.get("confidence", 0), 3),
                      "source_file": ed.get("source_file", ""),
                      "passage": ed.get("passage", "")}
                     for u, v, ed in G.edges(data=True)]

    # 3) 向量库：删掉 drop 的 corpus 行，重建索引
    before = len(proj["entities"])
    proj["entities"] = [c for c in proj["entities"] if (c.get("text") or c.get("id")) != drop]
    if len(proj["entities"]) != before:
        _rebuild_entity_index(proj)
    _persist_graph(proj)
    _persist_entities(proj)

    # 4) 别名：drop 及其别名整体并入 keep
    am = _alias_map(proj)
    drop_al = am.pop(drop, [])
    keep_al = am.setdefault(keep, [])
    if drop != keep and drop.lower() != keep.lower() and drop not in keep_al:
        keep_al.append(drop)
    for a in drop_al:
        a = a.strip()
        if a and a != keep and a.lower() != keep.lower() and a not in keep_al:
            keep_al.append(a)
    if not keep_al:
        am.pop(keep, None)
    _sync_manifest_stats(proj)
    _persist_manifest(proj)
    return {"ok": True, "keep": keep, "drop": drop,
            "aliases": {k: v for k, v in am.items() if v},
            "stats": {"nodes": len(proj["nodes"]), "edges": len(proj["edges"]),
                      "entities": len(proj["entities"])}}


@_api.post("/resolve/{name}")
def api_resolve(name: str, payload: dict = None):
    """消歧：输入文本 → 是否命中别名 / 已存在 / 疑似重复候选"""
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    payload = payload or {}
    text = str(payload.get("text", "")).strip()
    if not text:
        return JSONResponse({"ok": False, "error": "文本不能为空"}, status_code=400)
    canon = _canonical_of(proj, text)
    if canon and canon != text and canon in proj["G"]:
        return {"status": "alias", "text": text, "canonical": canon}
    if text in proj["G"]:
        return {"status": "exists", "text": text,
                "label": proj["G"].nodes[text].get("label", "?")}
    candidates = []
    if proj.get("store"):
        try:
            hits = proj["store"].search(text, k=12)
            seen = set()
            for h in hits:
                hid = h.get("text")
                if not hid or hid == text or hid in seen:
                    continue
                seen.add(hid)
                if float(h.get("score", 0)) >= _RESOLVE_SIM:
                    candidates.append({"id": hid, "label": h.get("label", "?"),
                                       "score": round(float(h.get("score", 0)), 3),
                                       "source_file": h.get("source_file", "")})
        except Exception as e:
            print(f"⚠️ resolve 检索失败: {e}")
    return {"status": "new", "text": text, "candidates": candidates[:6]}


# ============================================================
# Phase A3 —— 本地端到端问答（Local QA：向量+图证据 → LLM 答案）
# ============================================================
def _llm_chat(system: str, user: str, max_tokens: int = 8192, timeout: int = 180) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=kg15.detect_api_key(), base_url=kg15.DASHSCOPE_API_URL,
                    timeout=timeout, max_retries=0)
    kwargs = dict(model=kg15.QWEN_MODEL,
                  messages=[{"role": "system", "content": system},
                            {"role": "user", "content": user}],
                  temperature=0.2, max_tokens=max_tokens)
    if kg15.REASONING_EFFORT:
        kwargs["reasoning_effort"] = kg15.REASONING_EFFORT
    import time as _t
    last = None
    for attempt in range(2):
        try:
            r = client.chat.completions.create(**kwargs)
            out = (r.choices[0].message.content or "").strip()
            if out:
                return out
            last = RuntimeError("空响应")
        except Exception as e:
            last = e
            print(f"⚠️ QA LLM 调用失败(第{attempt + 1}次): {type(e).__name__}: {str(e)[:160]}", flush=True)
            _t.sleep(1.5)
    raise RuntimeError(f"LLM 调用失败: {last}")


def _qa_entity_in_segments(proj, segs, max_seeds=8):
    """把原文命中段里出现的图谱实体挖出来当 seed（补充向量检索漏掉的关键实体）"""
    G = proj["G"]
    if not G or not segs:
        return []
    found = []
    for s in segs:
        txt = s.get("text") or ""
        for nid in G.nodes():
            if not nid or len(nid) < 2:
                continue
            if nid in txt and nid not in found:
                found.append(nid)
                if len(found) >= max_seeds:
                    return found
    return found


def _build_qa_evidence(proj, question, k_ent=5, k_seg=5, hops=2):
    """组装本地问答证据：多通道召回
    1) 实体向量 top-k（命中即记 citation，进 G 的前 max_seeds 作 seed）
    2) 问题里直接提到/命中的实体（子串）与别名归一
    3) 原文段向量命中 → 把段中出现的图谱实体也当 seed（补漏关键边）
    4) 由全部 seed 分向扩展（出+入各 hops 层）得到关系证据
    """
    G = proj["G"]
    cit, seeds = [], []
    seg_hits = []

    def add_cit(kind, title, text, label, source_file, score):
        cit.append({"kind": kind, "title": title, "text": text or title,
                    "label": label, "source_file": source_file,
                    "score": round(float(score), 3)})

    # 1) 实体向量
    if proj.get("store"):
        try:
            for m in proj["store"].search(question, k=int(k_ent) + 4):
                t = m.get("text", "")
                canon = _canonical_of(proj, t) if t else t
                if canon and canon != t and canon in G:
                    t = canon
                if t in G and len(seeds) < 5:
                    seeds.append(t)
                add_cit("实体", t, m.get("passage") or t, m.get("label", ""),
                        m.get("source_file", ""), m.get("score", 0))
        except Exception as e:
            print(f"⚠️ QA 实体检索: {e}")

    # 2) 问题直接提及（子串）+ 别名归一命中
    qn = question.lower()
    for nid in G.nodes():
        if len(nid) >= 2 and nid in qn and nid not in seeds:
            seeds.append(nid)
        if len(seeds) >= 6:
            break

    # 3) 原文段 + 段内实体回灌
    if proj.get("seg_store"):
        try:
            for m in proj["seg_store"].search(question, k=int(k_seg)):
                seg_hits.append(m)
                add_cit("原文", "原文片段", m.get("text", ""), "", m.get("source_file", ""),
                        m.get("score", 0))
        except Exception as e:
            print(f"⚠️ QA 段检索: {e}")
    for nid in _qa_entity_in_segments(proj, seg_hits, max_seeds=8):
        if nid not in seeds:
            seeds.append(nid)

    # 4) 由 seed 分向扩展（出边 hops 层 + 入边 hops 层）
    seeds = seeds[:8]
    col = set(seeds)
    for s in seeds:
        try:
            cur = {s}
            for _ in range(int(hops)):
                nxt = set()
                for nd in cur:
                    nxt |= set(G.successors(nd)) | set(G.predecessors(nd))
                col |= nxt
                cur = nxt
        except Exception:
            pass
    if len(col) > 140:
        col = set(seeds)
        for s in seeds:
            col |= set(G.successors(s)) | set(G.predecessors(s))
    for u, v, ed in G.edges(data=True):
        if u in col and v in col:
            pred = ed.get("predicate", "?")
            add_cit("关系", f"{u} → {v}", f"{u} {pred} {v}", pred,
                    ed.get("source_file", "") or G.nodes[u].get("source_file", ""), 1.0)

    # 排序：实体 > 关系 > 原文；关系按被引用 seed 就近不排序（保原序）
    order = {"实体": 0, "关系": 1, "原文": 2}
    cit.sort(key=lambda c: order.get(c["kind"], 9))
    seen, out = set(), []
    for c in cit:
        key = (c["kind"], c["title"], c["source_file"])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    out = out[:30]
    seed = seeds[0] if seeds else None
    return out, seed, graph_to_payload(G, col)


@_api.post("/qa/{name}")
def api_qa(name: str, payload: dict = None):
    """body: {question, k_ent?, k_seg?, hops?} → {answer, citations, seed, graph}"""
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    payload = payload or {}
    question = str(payload.get("question", "")).strip()
    if not question:
        return JSONResponse({"ok": False, "error": "问题不能为空"}, status_code=400)
    try:
        k_ent = max(1, min(int(payload.get("k_ent", 5)), 10))
        k_seg = max(1, min(int(payload.get("k_seg", 5)), 10))
        hops = max(1, min(int(payload.get("hops", 2)), 3))
    except Exception:
        k_ent, k_seg, hops = 5, 5, 2

    citations, seed, graph = _build_qa_evidence(proj, question, k_ent, k_seg, hops)
    if not citations:
        return JSONResponse({"ok": False, "error": "未在图谱与原文中找到与问题相关的证据，无法作答。请换个问法或换项目。"}, status_code=404)
    system, user = _compose_qa_prompt(question, citations)
    answer = _llm_chat(system, user)
    return {"ok": True, "question": question, "answer": answer,
            "citations": citations, "seed": seed, "graph": graph,
            "stats": {"k_ent": k_ent, "k_seg": k_seg, "hops": hops}}


def _compose_qa_prompt(question: str, citations: list) -> tuple:
    lines = []
    for i, c in enumerate(citations, 1):
        body = (c.get("text") or c.get("title") or "").strip()
        if len(body) > 340:
            body = body[:340] + "…"
        src = c.get("source_file") or "未知出处"
        label = f"（{c.get('label')}）" if c.get("label") else ""
        lines.append(f"[{i}] [{c['kind']}{label}] {src}\n{body}")
    system = (
        "你是企业平台审核规则领域的知识图谱问答助手。回答要求：\n"
        "1) 只依据用户提供的“可用证据”，不得编造图谱中不存在的事实；\n"
        "2) 给出明确结论后，对每个关键论点标注证据编号，如【证据3】；\n"
        "3) 若证据不足以回答，明确说“证据不足”，并给出能找到的最接近信息；\n"
        "4) 语言精炼、条理清晰，面向合规审核人员。"
    )
    user = f"问题：{question}\n\n可用证据（编号引用时写 [n] 即可）：\n" + "\n".join(lines)
    return system, user


@_api.post("/qa/stream/{name}")
def api_qa_stream(name: str, payload: dict = None):
    """SSE 流式本地问答：先回证据(start) → 逐 token 输出 → done"""
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        return proj
    payload = payload or {}
    question = str(payload.get("question", "")).strip()
    if not question:
        return JSONResponse({"ok": False, "error": "问题不能为空"}, status_code=400)
    try:
        k_ent = max(1, min(int(payload.get("k_ent", 5)), 10))
        k_seg = max(1, min(int(payload.get("k_seg", 5)), 10))
        hops = max(1, min(int(payload.get("hops", 2)), 3))
    except Exception:
        k_ent, k_seg, hops = 5, 5, 2

    citations, seed, graph = _build_qa_evidence(proj, question, k_ent, k_seg, hops)
    import json as _json

    def gen():
        if not citations:
            yield "data: " + _json.dumps(
                {"type": "error", "message": "未在图谱与原文中找到与问题相关的证据，请换个问法或换项目。"},
                ensure_ascii=False) + "\n\n"
            return
        yield "data: " + _json.dumps(
            {"type": "start", "question": question, "citations": citations,
             "seed": seed, "graph": graph}, ensure_ascii=False) + "\n\n"
        system, user = _compose_qa_prompt(question, citations)
        from openai import OpenAI
        import time as _t
        client = OpenAI(api_key=kg15.detect_api_key(), base_url=kg15.DASHSCOPE_API_URL,
                        timeout=300, max_retries=0)
        kwargs = dict(model=kg15.QWEN_MODEL,
                      messages=[{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                      temperature=0.2, max_tokens=8192, stream=True)
        if kg15.REASONING_EFFORT:
            kwargs["reasoning_effort"] = kg15.REASONING_EFFORT
        last = None
        for attempt in range(2):
            try:
                stream = client.chat.completions.create(**kwargs)
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    d = chunk.choices[0].delta
                    rc = getattr(d, "reasoning_content", None) or ""
                    cc = getattr(d, "content", None) or ""
                    if rc:
                        yield "data: " + _json.dumps({"type": "reason", "text": rc}, ensure_ascii=False) + "\n\n"
                    if cc:
                        yield "data: " + _json.dumps({"type": "token", "text": cc}, ensure_ascii=False) + "\n\n"
                yield "data: " + _json.dumps({"type": "done"}, ensure_ascii=False) + "\n\n"
                return
            except Exception as e:
                last = e
                print(f"⚠️ QA 流式失败(第{attempt + 1}次): {type(e).__name__}: {str(e)[:160]}", flush=True)
                _t.sleep(1.5)
        yield "data: " + _json.dumps({"type": "error", "message": f"LLM 调用失败: {last}"},
                                     ensure_ascii=False) + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ============================================================
# Phase A2 —— 增量 upsert：新文档 解析→消歧→写入现有项目
# ============================================================
_UPSERT_DIR = ROOT / "data" / "kg_web" / "_tmp" / "upsert"


def _add_entity_row(proj, text: str, label: str, confidence: float,
                    source_file: str, passage: str) -> int:
    """向项目追加一个实体节点 + 可检索 corpus 行；返回 1 表示新增。"""
    if text in proj["G"]:
        return 0
    node = {"id": text, "text": text, "label": label, "confidence": round(confidence, 3),
            "source_file": source_file, "passage": passage}
    proj["nodes"].append(node)
    proj["G"].add_node(text, label=label, confidence=confidence,
                       source_file=source_file, passage=passage)
    store = proj.get("store")
    if store is not None:
        corpus_ent = {"id": text, "text": text, "label": label, "confidence": round(confidence, 3),
                      "source_file": source_file, "passage": passage, "chunk_id": 0,
                      "embed_input": f"{text} ({label}) | {passage[:60]}" if passage else f"{text} ({label})"}
        if text not in {x.get("text") for x in proj["entities"]}:
            proj["entities"].append(corpus_ent)
        try:
            with _STORE_LOCK:
                import numpy as np
                vec = store.embedder.encode([corpus_ent["embed_input"]],
                                            normalize_embeddings=True).astype(np.float32)
                store.index.add(vec)
        except Exception as e:
            print(f"⚠️ 实体向量增长失败(可降级): {e}")
    return 1


def _append_segments(proj, text: str, source_file: str) -> int:
    """把新文档原文分块后追加到段索引；返回新增段数（项目无段库则跳过）"""
    ss = proj.get("seg_store")
    if ss is None or getattr(ss, "index", None) is None:
        return 0
    segs = kg15.split_to_segments(text or "")
    if not segs:
        return 0
    base = len(proj["segments"])
    rows = [{"text": s, "source_file": source_file, "seg_id": base + i, "embed_input": s}
            for i, s in enumerate(segs)]
    try:
        with _STORE_LOCK:
            import numpy as np
            vecs = ss.embedder.encode([r["embed_input"] for r in rows],
                                      normalize_embeddings=True).astype(np.float32)
            ss.index.add(vecs)
    except Exception as e:
        print(f"⚠️ 段索引增长失败(可降级): {e}")
        return 0
    proj["segments"].extend(rows)
    return len(rows)


def _persist_segments(proj):
    p = kg_project.project_path(proj["name"])
    (p / "segments.json").write_text(
        json.dumps(proj["segments"], ensure_ascii=False, indent=2), encoding="utf-8")
    ss = proj.get("seg_store")
    if ss is not None and getattr(ss, "index", None) is not None:
        try:
            import faiss
            with _STORE_LOCK:
                seg_bytes = faiss.serialize_index(ss.index)
            (p / "embeddings" / "seg.index").write_bytes(seg_bytes)
            (p / "embeddings" / "seg.corpus.json").write_text(
                json.dumps(proj["segments"], ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"⚠️ 段索引落盘失败: {e}", flush=True)


def _fuzzy_resolve(proj, text: str, label: str, threshold: float = 0.88):
    """向量相似度消歧：找到与 text 高度相似且类目一致的现有节点（返回其 id，找不到 None）。
    仅作保守自动合并；类目不同或低于阈值一律不并。"""
    store = proj.get("store")
    if store is None or not text or text in proj["G"]:
        return None
    try:
        hits = store.search(text, k=6)
    except Exception as e:
        print(f"⚠️ 模糊消歧检索失败: {e}", flush=True)
        return None
    for h in hits:
        hid = h.get("text")
        if not hid or hid == text or hid not in proj["G"]:
            continue
        sc = float(h.get("score", 0))
        if sc < threshold:
            break  # 已按相似度降序，其后更低
        hl = proj["G"].nodes[hid].get("label", "")
        if label and hl and hl != label and hl != "Unknown" and label != "Unknown":
            continue
        return hid
    return None


def _upsert_meta_into_project(proj, meta: dict, fuzzy_threshold: float = 0.88) -> dict:
    """把一份解析结果（meta）消歧对齐后增量写入项目。返回计数。"""
    G = proj["G"]
    am = _alias_map(proj)
    cnt = {"entities_added": 0, "aliases": 0, "relations_added": 0,
           "segments_added": 0, "skipped_dup": 0, "fuzzy_merged": 0}
    src = meta.get("file", "unknown")
    doc_label = {e.text: e.label for e in meta.get("entities", [])}

    def auto_merge(raw: str, label: str) -> str:
        """返回归并到的现有节点 id；无则返回 raw"""
        canon = _canonical_of(proj, raw)
        if canon != raw and canon in G:
            if raw not in am.get(canon, []):
                am.setdefault(canon, []).append(raw)
                cnt["aliases"] += 1
            return canon
        if raw in G:
            cnt["skipped_dup"] += 1
            return raw
        tgt = _fuzzy_resolve(proj, raw, label, fuzzy_threshold)
        if tgt:
            if raw not in am.get(tgt, []):
                am.setdefault(tgt, []).append(raw)
                cnt["aliases"] += 1
            cnt["fuzzy_merged"] += 1
            print(f"  ➕ 向量消歧合并: 「{raw}」→「{tgt}」(label {label})", flush=True)
            return tgt
        return raw

    # 1) 实体（精确别名 → 跳过 → 向量模糊 → 新增）
    for e in meta.get("entities", []):
        raw = e.text
        tgt = auto_merge(raw, e.label)
        if tgt != raw:
            continue  # 已归并到现有实体
        cnt["entities_added"] += _add_entity_row(
            proj, raw, e.label, e.confidence, src,
            passage_for_entity(raw, meta))

    # 2) 关系（端点在现有图谱则复用；否则补齐节点）
    for r in meta.get("relations", []):
        s = auto_merge(r.subject.text, doc_label.get(r.subject.text, "Unknown"))
        o = auto_merge(r.object.text, doc_label.get(r.object.text, "Unknown"))
        if s not in G:
            cnt["entities_added"] += _add_entity_row(
                proj, s, doc_label.get(s, "Unknown"), 1.0, src, "")
        if o not in G:
            cnt["entities_added"] += _add_entity_row(
                proj, o, doc_label.get(o, "Unknown"), 1.0, src, "")
        if G.has_edge(s, o):
            continue
        conf = round(min(1.0, max(0.0, r.confidence or 1.0)), 3)
        passage = passage_for_relation(s, o, meta)
        G.add_edge(s, o, predicate=r.predicate, confidence=conf,
                   source_file=src, passage=passage)
        proj["edges"].append({"source": s, "target": o, "predicate": r.predicate,
                              "confidence": conf, "source_file": src, "passage": passage})
        cnt["relations_added"] += 1

    # 3) 原文段
    cnt["segments_added"] = _append_segments(proj, meta.get("text", ""), src)

    # 4) 落盘 + manifest
    sources = proj["manifest"].setdefault("sources", {})
    if src and src not in sources:
        sources[src] = meta.get("text", "")
    slist = proj["manifest"].setdefault("stats", {}).setdefault("sources", [])
    if isinstance(slist, list) and src not in slist:
        slist.append(src)
    _sync_manifest_stats(proj)
    return cnt


def _snapshot_project(name: str, keep: int = 15) -> str:
    """upsert/恢复前给项目打一份带时间戳的完整快照（含 embeddings），返回版本号"""
    import shutil
    p = kg_project.project_path(name)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    hd = p / "history" / ts
    hd.mkdir(parents=True, exist_ok=True)
    for fn in ("manifest.json", "graph.json", "entities.json", "segments.json"):
        sp = p / fn
        if sp.exists():
            shutil.copy2(sp, hd / fn)
    emb = p / "embeddings"
    if emb.exists():
        shutil.copytree(emb, hd / "embeddings", dirs_exist_ok=True)
    hp = p / "history"
    try:
        vers = sorted([d.name for d in hp.iterdir() if d.is_dir()], reverse=True)
        for v in vers[keep:]:
            shutil.rmtree(hp / v, ignore_errors=True)
    except Exception:
        pass
    return ts


@_api.get("/history/{name}")
def api_history(name: str):
    hp = kg_project.project_path(name) / "history"
    if not hp.exists():
        return {"ok": True, "versions": []}
    out = []
    for d in sorted(hp.iterdir(), key=lambda x: x.name, reverse=True):
        if not d.is_dir():
            continue
        files = sorted(f.name for f in d.iterdir() if f.suffix == ".json")
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        out.append({"version": d.name, "files": files,
                    "size": size,
                    "created": d.name.replace("_", " ")})
    return {"ok": True, "versions": out}


@_api.post("/restore/{name}")
def api_restore(name: str, payload: dict = None):
    payload = payload or {}
    ver = str(payload.get("version", "")).strip()
    if not ver:
        return JSONResponse({"ok": False, "error": "缺少 version"}, status_code=400)
    import shutil
    p = kg_project.project_path(name)
    if not (p / "manifest.json").exists():
        return JSONResponse({"ok": False, "error": f"项目不存在: {name}"}, status_code=404)
    vdir = p / "history" / ver
    if not vdir.is_dir():
        return JSONResponse({"ok": False, "error": f"版本不存在: {ver}"}, status_code=404)
    _snapshot_project(name)  # 覆盖前先存档当前状态，保证可再回滚
    for fn in ("manifest.json", "graph.json", "entities.json", "segments.json"):
        if (vdir / fn).exists():
            shutil.copy2(vdir / fn, p / fn)
    emb_src = vdir / "embeddings"
    emb_dst = p / "embeddings"
    if emb_src.exists():
        if emb_dst.exists():
            shutil.rmtree(emb_dst, ignore_errors=True)
        shutil.copytree(emb_src, emb_dst)
    LOADED.pop(name, None)
    try:
        proj = load_project_into_memory(name)
        return {"ok": True, "version": ver,
                "stats": {"nodes": len(proj["nodes"]), "edges": len(proj["edges"]),
                          "segments": len(proj["segments"])}}
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"恢复后重载失败: {e}"}, status_code=500)


def _jlog(job, msg: str):
    """往 job 里追加一行带时间的日志（供前端轮询展示）"""
    import datetime as _dt
    ts = _dt.datetime.now().strftime("%H:%M:%S")
    logs = job.setdefault("logs", [])
    logs.append(f"{ts}  {msg}")
    if len(logs) > 120:
        job["logs"] = logs[-120:]


def _parse_upsert_doc(job, mod, args, file_path: str) -> dict:
    """解析单篇 .txt/.md 用于 upsert。
    17 引擎：标题感知分块，逐分块调 LLM 并把进度写入 job.logs；
    15 引擎：退化为原 process_file（仅阶段日志）。"""
    import time as _t
    path = Path(file_path)
    raw = path.read_text(encoding="utf-8", errors="ignore")
    text = mod.strip_page_markers(raw)
    _jlog(job, f"读取 {path.name}：{len(raw)} 字符（清洗后 {len(text)}）")
    if mod is kg17:
        ner, splitter = args
        chunk_size = getattr(splitter, "chunk_size", 8000)
        overlap = getattr(splitter, "chunk_overlap", 400)
        chunks = kg17.structure_aware_split(text, chunk_size, overlap)
        job["total"] = len(chunks)
        job["done"] = 0
        _jlog(job, f"标题感知分块：{len(chunks)} 块（size~{chunk_size}, overlap~{overlap}）")
        all_entities, all_relations = [], []
        for i, ch in enumerate(chunks):
            _jlog(job, f"[分块 {i + 1}/{len(chunks)}] 抽取实体（{len(getattr(ch, 'text', ''))} 字）…")
            t0 = _t.time()
            try:
                ents = ner.extract(ch.text)
            except Exception as e:
                ents = []
                _jlog(job, f"  ⚠️ 分块 {i + 1} NER 失败: {str(e)[:160]}")
            for e in ents:
                try:
                    e.start_char += ch.start_index
                    e.end_char += ch.start_index
                except Exception:
                    pass
                if getattr(e, "metadata", None) is None:
                    e.metadata = {}
                e.metadata["chunk_id"] = i
            all_entities += ents
            rels = []
            if ents:
                try:
                    rels = ner.extract_relations(ch.text, ents)
                except Exception as e:
                    _jlog(job, f"  ⚠️ 分块 {i + 1} 关系抽取失败: {str(e)[:160]}")
                for r in rels:
                    if getattr(r, "metadata", None) is None:
                        r.metadata = {}
                    r.metadata["chunk_id"] = i
            all_relations += rels
            _jlog(job, f"  分块 {i + 1}/{len(chunks)}：实体 {len(ents)} / 关系 {len(rels)}  "
                        f"（累计 {len(all_entities)} / {len(all_relations)}，{_t.time() - t0:.0f}s）")
            job["done"] = i + 1
        # 去重（与 process_file 一致）
        seen_e, uniq_ents = {}, []
        for e in all_entities:
            key = (e.text.strip().lower(), getattr(e, "label", ""))
            if key not in seen_e:
                seen_e[key] = e
                uniq_ents.append(e)
        seen_r, uniq_rels = set(), []
        for r in all_relations:
            key = (r.subject.text.strip().lower(), r.predicate, r.object.text.strip().lower())
            if key not in seen_r:
                seen_r.add(key)
                uniq_rels.append(r)
        try:
            struct_ents, struct_rels = mod.extract_structure(text, all_entities, all_relations)
            for se in struct_ents:
                uniq_ents.append(se)
            for sr in struct_rels:
                key = (sr.subject.text.strip().lower(), "partOf", sr.object.text.strip().lower())
                if key not in seen_r:
                    seen_r.add(key)
                    uniq_rels.append(sr)
        except Exception as e:
            print(f"⚠️ 段落结构抽取跳过: {e}", flush=True)
        meta = {"file": path.name, "raw_chars": len(raw), "chars": len(text),
                "text": text, "num_chunks": len(chunks),
                "raw_entities": all_entities, "raw_relations": all_relations,
                "entities": uniq_ents, "relations": uniq_rels}
        meta["entities"], meta["relations"] = mod.normalize_result(
            meta["raw_entities"], meta["raw_relations"], text)
        _jlog(job, f"解析完成：实体 {len(meta['entities'])} / 关系 {len(meta['relations'])}")
        return meta
    # 15 回退
    meta = _process_with_extractor(mod, path, args)
    _jlog(job, f"解析完成：实体 {len(meta['entities'])} / 关系 {len(meta['relations'])}")
    return meta


def run_upsert_job(job_id: str, name: str, file_path: str, demo: bool = False):
    job = JOBS[job_id]
    job.setdefault("logs", [])
    try:
        _jlog(job, f"装载项目 {name}…")
        job["stage"] = f"装载项目 {name}..."
        proj = load_project_into_memory(name)
        if demo:
            _jlog(job, "生成内置样例文档…")
            all_meta = kg15._build_demo_meta()
        else:
            mod, args = _new_extractor()
            _jlog(job, "准备 LLM 解析器…")
            all_meta = [_parse_upsert_doc(job, mod, args, file_path)]
        added = {"entities_added": 0, "aliases": 0, "relations_added": 0,
                 "segments_added": 0, "skipped_dup": 0, "fuzzy_merged": 0}
        ver = None
        for i, meta in enumerate(all_meta):
            _jlog(job, f"对齐写入（{i + 1}/{len(all_meta)}）…")
            if ver is None:
                _jlog(job, "版本快照…")
                ver = _snapshot_project(name)
                _jlog(job, f"快照版本 {ver}")
            c = _upsert_meta_into_project(proj, meta)
            _jlog(job, f"  本批：新增实体 {c['entities_added']} / 关系 {c['relations_added']} / "
                       f"段 {c['segments_added']} / 别名 {c['aliases']} / 模糊合并 {c['fuzzy_merged']} / "
                       f"去重 {c['skipped_dup']}")
            for k in added:
                added[k] += c[k]
        _jlog(job, "写盘：graph / 向量索引 / 段索引 / manifest…")
        _persist_graph(proj)
        _persist_entities(proj)
        _persist_segments(proj)
        _persist_manifest(proj)
        job["status"] = "success"
        job["stage"] = (f"完成：新增实体 {added['entities_added']} / 关系 {added['relations_added']} / "
                        f"段 {added['segments_added']} / 别名 {added['aliases']} / 模糊合并 {added['fuzzy_merged']}")
        _jlog(job, job["stage"])
        job["result"] = {"project": name, "version": ver, **added}
    except Exception as e:
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        _jlog(job, f"❌ 失败：{type(e).__name__}: {str(e)[:200]}")
        print(f"❌ upsert job {job_id} 失败: {e}", flush=True)
        traceback.print_exc()
    finally:
        job["stage"] = job.get("stage", "") + ("（结束）" if job.get("status") else "")


@_api.post("/append-doc/{name}")
async def api_append_doc(name: str, file: UploadFile = File(...)):
    if not (kg_project.project_path(name) / "manifest.json").exists():
        return JSONResponse({"ok": False, "error": f"项目不存在: {name}"}, status_code=404)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".txt", ".md"):
        return JSONResponse({"ok": False, "error": "仅支持 .txt / .md 文档"}, status_code=400)
    d = _UPSERT_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    fpath = d / Path(file.filename).name
    content = await file.read()
    try:
        fpath.write_bytes(content)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"写盘失败: {e}"}, status_code=400)
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "running", "stage": "排队中", "done": 0, "total": 0, "logs": []}
    t = threading.Thread(target=run_upsert_job,
                         args=(job_id, name, str(fpath), False), daemon=True)
    t.start()
    return {"job_id": job_id, "file": Path(file.filename).name}


@_api.post("/append-demo/{name}")
def api_append_demo(name: str):
    """把内置 demo 样例增量写入指定项目（免 LLM，便于测试 upsert 全链路）"""
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "running", "stage": "排队中", "done": 0, "total": 0, "logs": []}
    t = threading.Thread(target=run_upsert_job,
                         args=(job_id, name, "", True), daemon=True)
    t.start()
    return {"job_id": job_id}


# ============================================================
# 抽取质量评测（kg_eval：gold vs 项目）+ gold 查看
# 与 18 脚本同一评测引擎同一口径（覆盖率/实体·关系 P/R/F1/噪声/长尾）
# ============================================================
import kg_eval  # noqa: E402  （kgcore 已在 sys.path）

GOLD_DIR = ROOT / "data" / "gold"
try:
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


def _gold_path(fname: str) -> Path:
    """gold 文件名 → 安全路径（只允许 data/gold 下的具体文件）"""
    name = Path(fname or "").name
    p = (GOLD_DIR / name).resolve()
    if GOLD_DIR.resolve() not in p.parents or not p.is_file():
        raise FileNotFoundError(f"gold 不存在: {name}")
    return p


def _project_as_pred(name: str) -> str:
    """把已加载项目的节点/边序列化成 graph.json 形状的临时 pred 文件（kg_eval.load_run 可读）"""
    proj = _ensure_loaded(name)
    if isinstance(proj, JSONResponse):
        raise RuntimeError("项目不可用")
    pred = {"nodes": proj["nodes"], "edges": proj["edges"]}
    tmp = ROOT / "data" / "kg_web" / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40] or "project"
    fp = tmp / f"eval_pred_{safe}_{datetime_now_ts()}.json"
    fp.write_text(json.dumps(pred, ensure_ascii=False), encoding="utf-8")
    return str(fp)


@_api.get("/eval/golds")
def api_eval_golds():
    """列出 data/gold 下可用 gold 文件（文件名、实体/关系数、别名组数、来源文档数、修改时间）"""
    out = []
    if GOLD_DIR.is_dir():
        for f in sorted(GOLD_DIR.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8-sig"))
            except Exception as e:
                out.append({"file": f.name, "error": f"无法解析: {e}"})
                continue
            ents = d.get("entities", []) if isinstance(d, dict) else []
            rels = d.get("relations", []) if isinstance(d, dict) else []
            alis = d.get("aliases", []) if isinstance(d, dict) else []
            ents = ents if isinstance(ents, list) else []
            rels = rels if isinstance(rels, list) else []
            srcs = {str(e.get("source_file") or "") for e in ents if isinstance(e, dict)}
            srcs.discard("")
            out.append({
                "file": f.name,
                "note": str((d.get("note") if isinstance(d, dict) else "") or ""),
                "entities": len(ents),
                "relations": len(rels),
                "aliases": len(alis) if isinstance(alis, list) else 0,
                "sources": len(srcs),
                "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "size": f.stat().st_size,
            })
    return {"ok": True, "golds": out}


@_api.get("/eval/gold/{fname}")
def api_eval_gold(fname: str):
    """查看 gold 全文：note / aliases / entities / relations（只读）"""
    try:
        p = _gold_path(fname)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"gold 解析失败: {e}"}, status_code=400)
    if not isinstance(d, dict):
        return JSONResponse({"ok": False, "error": "gold 应为 JSON 对象"}, status_code=400)
    return {"ok": True, "file": p.name, "note": d.get("note", ""),
            "aliases": d.get("aliases", []) if isinstance(d.get("aliases"), list) else [],
            "entities": d.get("entities", []) if isinstance(d.get("entities"), list) else [],
            "relations": d.get("relations", []) if isinstance(d.get("relations"), list) else []}


@_api.post("/eval/run")
def api_eval_run(payload: dict = None):
    """body: {gold: 文件名, project: 项目名} → 项目节点/边当 pred，跑 kg_eval.evaluate
    返回 {report(JSON结果), md(Markdown报告)}"""
    payload = payload or {}
    gold = str(payload.get("gold", "")).strip()
    project = str(payload.get("project", "")).strip()
    if not gold:
        return JSONResponse({"ok": False, "error": "请选择 gold"}, status_code=400)
    if not project:
        return JSONResponse({"ok": False, "error": "请选择要评测的项目（pred）"}, status_code=400)
    proj = _ensure_loaded(project)
    if isinstance(proj, JSONResponse):
        return proj
    try:
        gp = _gold_path(gold)
        pred_path = _project_as_pred(project)
        rep = kg_eval.evaluate(str(gp), pred_path)
        md = kg_eval.render_eval_md(rep)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"评测失败: {type(e).__name__}: {e}"},
                            status_code=500)
    return {"ok": True, "gold": gold, "project": project, "report": rep, "md": md}


app.include_router(_api)

# 静态文件
app.mount("/assets", StaticFiles(directory=str(STATIC_DIR)), name="assets")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("KG_WEB_PORT", "8000"))
    mode = "15(DirectLLMExtractor)" if os.environ.get("KG_EXTRACTOR", "17") == "15" \
        else ("17(semantica)" if kg17 is not None else "15(回退)")
    print(f"\n  🖥️  知识图谱 Explorer  http://127.0.0.1:{port}\n")
    LOG.info("启动：KG_EXTRACTOR=%s | 项目目录 %s | 日志 %s",
             mode, PROJECTS_DIR, LOG_DIR / "server.log")
    LOG.info("前端静态 %s", STATIC_DIR)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
