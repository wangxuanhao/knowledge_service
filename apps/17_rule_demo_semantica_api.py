"""
17 - LLM 模式批量解析美团规则文档 → 知识图谱（使用 semantica 原生 API）
=====================================================================
与 15 脚本同主题：批量读取规则 .txt，用 LLM 抽出 实体/关系，构建
向量库＋图谱＋检索。**核心区别**：15 因 semantica 自带提取器太慢而
绕过它、手写直连 HTTP（DirectLLMExtractor）；**本脚本则完整使用
semantica 的原生抽取 API**：

    ① 实体抽取    semantica.semantic_extract.NERExtractor(method="llm")
    ② 关系抽取    semantica.semantic_extract.RelationExtractor(method="llm")
    ③ 长文本分块  semantica.split.TextSplitter（entity_aware + recursive）
    ④ 文件解析    semantica.ingest.FileIngestor + semantica.parse.DocumentParser

领域配置（实体/关系 schema、角色别名归一、原文覆盖）与向量化/图谱/检索
逻辑已**直接搬迁自 15 脚本**（本脚本自包含，不依赖 15，也不 import 模块）。

LLM 走 OpenAI 兼容通道（同 15 CONFIG，可用 DASHSCOPE_API_KEY 覆盖）：
    provider = openai（OpenAIProvider + base_url → instructor Mode.JSON）
    llm_model = deepseek-v4-flash @ https://api.deepseek.com

运行环境：conda env = llm_model（Python 3.12，semantica 0.6.7 已装）
运行命令：
    D:/work/wangxuanhao/conda/envs/llm_model/python.exe 17_rule_demo_semantica_api.py [目录，默认 rule_demo]
可选参数：
    --parse-only        # 只跑 解析+分块，不调 LLM
    --demo              # 不调 LLM，用内置样例演示 向量化+图谱+检索

输出约定（与 15 一致，便于版本管理）：
    每次运行写入独立目录  rule_demo_output/run_<YYYYmmdd_HHMMSS>/
    其中含 run_info.json（记录：哪个脚本跑的 / 时间 / 参数 / LLM 配置 /
    汇总统计）。16 导入时会自动选择"最新"的 run 目录。
"""

import os
import re
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

# 国内环境：避免 fastembed 等在线下载卡死
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("SEMANTICA_ALLOW_ANONYMOUS", "true")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# semantica.vector_store 包顶层 import pinecone 会因包名冲突抛异常，
# 用 stub 蒙混过去；我们只用到自建的 SimpleStore（底层 faiss）
try:
    import pinecone  # noqa: F401
except Exception:
    import types
    _stub = types.ModuleType("pinecone")
    _stub.Pinecone = lambda *a, **k: None
    _stub.ServerlessSpec = type("ServerlessSpec", (), {})
    _stub.PodSpec = type("PodSpec", (), {})
    sys.modules["pinecone"] = _stub

ROOT = Path(__file__).resolve().parent          # apps/
REPO = ROOT.parent                              # 仓库根
DATA = REPO / "data"                            # 数据统一在 data/ 下
CORE = REPO / "kgcore"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

# 本体（OWL）能力：meituan_ontology.ttl 单一来源
#   · structure_class(level) 结构实体分层   · constraint_violations() domain/range 回接
#   · build_graph 末尾附加 instanceOf/subClassOf 层级边（EMIT_CLASS_EDGES 开关）
try:
    import ontology_owl as owl
    owl.ensure_ontology_file()
    ONTOLOGY_OK = True
except Exception as _eo:
    owl = None
    ONTOLOGY_OK = False
    print(f"  ⚠️ ontology_owl 未就绪（本体能力关闭，将回退扁平模式）: {_eo}", flush=True)

# 是否把本体类节点/层级边附加进输出图（关闭则保持原来纯实例图）
EMIT_CLASS_EDGES = True

# ============================================================
# CONFIG：DeepSeek —— OpenAI 兼容通道（同 15）
# ============================================================
CONFIG = {
    "llm_model": "deepseek-v4-flash",
    "base_url": "https://api.deepseek.com",
    "api_key": "sk-fdb69eb6de864dbc853f1e66cb7e909b",  # 固定使用（detect_api_key 不再读环境变量）
    # 思考模式（DeepSeek 思考 token 计入 max_tokens 预算，需给足上限）：
    #   "low"  = 轻思考（快、质量高，推荐）
    "reasoning_effort": "low",
}
QWEN_MODEL = CONFIG["llm_model"]
DASHSCOPE_API_URL = CONFIG["base_url"]
REASONING_EFFORT = CONFIG.get("reasoning_effort")

# ============================================================
# 实体类型 schema（锁定，与 图谱设计.md 1.1 一致；搬迁自 15）
# ============================================================
ENTITY_TYPES: list[str] = [
    # —— 主体（5）——
    "Platform",         # 平台（美团/大众点评/我们）
    "User",             # 消费者/用户
    "Merchant",         # 商家/商户
    "FulfillmentService",  # 履约（客服/骑手/司机/代运营）
    "ExternalOrg",      # 监管/外部组织（第三方/合作商/关联公司/供应商/支付机构/物流/监管部门/保险公司）
    # —— 规则（1）——
    "RuleDocument",     # 规则/协议/政策
    # —— 治理（3）——
    "Violation",        # 违规行为
    "Penalty",          # 处理措施
    "Remedy",           # 救济途径
    # —— 外部引用（2）——
    "Law",              # 法律法规
    "Qualification",    # 经营资质
    # —— 数据（1）——
    "PersonalData",     # 个人信息
    # —— 交易客体（4）——
    "Product",          # 商品/服务
    "Order",            # 订单
    "Payment",          # 资金结算
    "Promotion",        # 营销权益
    # —— 内容（2）——
    "Content",          # 信息内容
    "Review",           # 评价/口碑
]

# ============================================================
# 关系类型 schema（锁定 21 类，与 图谱设计.md 2.1 一致；搬迁自 15）
# ============================================================
RELATION_TYPES: list[str] = [
    # 规则类
    "publishes",          # Platform → RuleDocument
    "hasLegalBasis",      # RuleDocument → Law
    "appliesTo",          # RuleDocument → {Merchant, User, ...}
    "prohibits",          # RuleDocument → Violation
    "requiresBehavior",   # RuleDocument → Actor
    # 结构类（文档大纲：章节/条款归属）
    "partOf",             # 节点内容 → 其所属章节/序号（第一章/第二章/第一节/第N条）
    # 数据类
    "collects",           # Platform → PersonalData
    "sharesWith",         # Platform → ExternalOrg
    "agreesTo",           # User → RuleDocument
    # 治理类
    "commits",            # Merchant → Violation
    "detects",            # Platform → Violation
    "triggers",           # Violation → Penalty
    "handles",            # Platform → Penalty
    # 权利义务类
    "hasRight",           # User → Remedy
    "hasObligation",      # Merchant → Actor
    # 商业交易类
    "purchases",          # User → Product
    "provides",           # Merchant → Product
    "contains",           # Order → Product
    "delivers",           # FulfillmentService → Order
    "refunds",            # Platform → Payment
    # 入驻资质类
    "onboards",           # Merchant → Platform
    "reviews",            # Platform → Qualification
    "isAffiliate",        # Platform → ExternalOrg
]

# ---- P3 单一来源：启动时用 meituan_ontology.ttl 派生白名单（文件缺失/解析失败回退硬编码） ----
if owl is not None and ONTOLOGY_OK:
    try:
        _derived_e = set(owl.entity_type_whitelist())
        _derived_r = set(owl.relation_whitelist())
        # 保留原声明顺序，只增删到与本体一致；partOf/instanceOf 是结构边，不进 LLM 白名单
        ENTITY_TYPES = ([x for x in ENTITY_TYPES if x in _derived_e]
                        + [x for x in sorted(_derived_e) if x not in ENTITY_TYPES])
        RELATION_TYPES = [x for x in RELATION_TYPES if x in _derived_r]
        print(f"  🧬 本体单一来源: ENTITY_TYPES {len(ENTITY_TYPES)} 类 / "
              f"RELATION_TYPES {len(RELATION_TYPES)} 关系（meituan_ontology.ttl 派生）",
              flush=True)
    except Exception as _ed:
        print(f"  ⚠️ 白名单派生失败，回退硬编码列表: {_ed}", flush=True)

# ============================================================
# 角色别名归一表（alias → 中文 canonical 实例名）
# 策略（A）：只有"平台 / 用户 / 商家"这类**泛称**才做类归并，且归到中文规范名；
#           具体角色词（房东/主播/骑手/司机/客服…）与具名实例（美团/大众点评…）
#           一律保留中文原词，避免被顶成英文类名或错误并入 ExternalOrg。
# 例：商户/卖家/店铺/经营者 → "商家"；房东 → 房东（不再变 Merchant）。
# 注意：合作商 曾误归 ExternalOrg，实际是入驻商家（Merchant），已移除。
# ============================================================
CANONICAL: dict[str, str] = {
    # —— Platform（泛称 → 中文 canonical）——
    "平台": "平台", "我们": "平台", "平台运营方": "平台",
    # —— User（泛称 → 中文 canonical）——
    "用户": "用户", "消费者": "用户", "买家": "用户", "买方": "用户", "顾客": "用户",
    # —— Merchant（泛称 → 中文 canonical）——
    "商家": "商家", "商户": "商家", "卖家": "商家", "店铺": "商家",
    "经营者": "商家", "平台内经营者": "商家",
    # —— 具体角色词：保留中文实例名（不归并到类）——
    # 房东/主播/骑手/司机/配送员/代运营/配送司机/驾驶员/客服/第三方服务商
    # 乘客/旅客/房客/评审员/第三方/合作商/关联公司/供应商/支付机构/物流/监管部门/保险公司
}

# 自指/噪声 token 列表（appliesTo 关系里要剔除）
NOISE_OBJECT: set[str] = {"本规则", "本规范", "本协议", "本政策", "本公约",
                          "前述规则", "前述协议", "前述规定", "前述条款"}


def print_header(title, char="="):
    print(f"\n{char * 66}")
    print(f"  {title}")
    print(f"{char * 66}")


def detect_api_key() -> str:
    # 固定走代码 CONFIG，不读环境变量 DASHSCOPE_API_KEY：
    # 系统环境变量曾被无效 key（sk-sp-...JHUY2I）污染，按"环境变量优先"逻辑
    # 压过代码里配置的有效 key，导致 QA/抽取全部 401，故直接锁定 CONFIG。
    key = CONFIG["api_key"].strip()
    if not key:
        raise SystemExit(
            "\n❌ 未配置 API Key。请在 CONFIG 的 api_key 字段填写\n"
            f"（接口地址 {DASHSCOPE_API_URL}，模型 {QWEN_MODEL}）"
        )
    return key


def strip_page_markers(text: str) -> str:
    """去掉 PDF 解析时插入的 ========== Page X/9 ========== 标记"""
    text = re.sub(r"==========\s*Page\s+\d+/\d+\s*==========", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# 实例级同义归并（可选，中文↔中文）：
# 从 data/alias.json 读取同义组（格式同 gold 的 "aliases"），把"房源/客房"这类
# 同义实例归一到中文 canonical，避免与英文类名(CANONICAL)混用、也避免同义不同名。
# 缺省空 → 行为与原来一致。该表是业务侧决策：放进去的合并会作用到抽取结果。
CONCEPT_ALIAS: dict = {}
try:
    _af = DATA / "alias.json"
    if _af.exists():
        for _g in json.loads(_af.read_text(encoding="utf-8-sig")).get("aliases", []):
            _c = str(_g.get("canonical") or "").strip()
            if not _c:
                continue
            CONCEPT_ALIAS.setdefault(_c, _c)
            for _t in _g.get("terms", []) or []:
                _t = str(_t).strip()
                if _t:
                    CONCEPT_ALIAS.setdefault(_t, _c)
except Exception:
    pass


def normalize_alias(text: str) -> str:
    """对 actor / violation / penalty 名称做 alias→canonical 归一
    优先匹配 CANONICAL 表（最长前缀匹配），未命中保留原文。
    再叠加 CONCEPT_ALIAS（实例同义，中文→中文）做兜底归并。
    """
    t = (text or "").strip()
    if not t:
        return t
    # 优先查最长的别名（"平台内经营者" 优先于 "平台"）
    for alias in sorted(CANONICAL.keys(), key=len, reverse=True):
        if t == alias or t.startswith(alias + "（") or t.startswith(alias + "("):
            return CANONICAL[alias]
    return CONCEPT_ALIAS.get(t, t)


def is_noise_object(text: str) -> bool:
    """appliesTo 等关系的尾实体是否噪声（"本规则"等自指）"""
    t = (text or "").strip()
    return t in NOISE_OBJECT or t.startswith("本规则") or t.startswith("本规范")


# ------------------------------------------------------------
# 轻量实体 / 关系（demo 用；字段兼容 semantica 抽取结果）
# ------------------------------------------------------------
class _Ent:
    __slots__ = ("text", "label", "confidence", "start_char", "end_char", "metadata")
    def __init__(self, text, label, confidence=1.0, start=0, end=0):
        self.text = text; self.label = label; self.confidence = confidence
        self.start_char = start; self.end_char = end
        self.metadata = {}


class _Rel:
    __slots__ = ("subject", "predicate", "object", "confidence", "metadata")
    def __init__(self, s, p, o, confidence=1.0):
        self.subject = _Ent(s, s); self.predicate = p
        self.object = _Ent(o, o); self.confidence = confidence
        self.metadata = {}


DEMO_TEXT = """第一条 为规范大众点评网商户直播行为，维护平台良好经营秩序，保障消费者合法权益，制定本规范。
第二条 本规范适用于通过大众点评网开展直播经营活动的商户（包括主播、店铺经营者，以下统称"商户"）。
第三条 商户在直播过程中应当遵守下列行为规范：
　（一）不得直播穿着暴露、低俗或涉及明显性暗示的服饰；
　（二）不得在直播中展示、出售国家法律法规禁止流通的违禁品；
　（三）不得夸大宣传、虚假承诺商品功效或服务质量；
　（四）不得以任何形式收集、存储消费者个人信息，除非获得消费者明确授权；
　（五）不得引导消费者站外交易，逃避平台监管。
第四条 平台有权对违规直播行为依据情节轻重采取下列处理措施：
　（一）警告并限期整改；
　（二）限制直播功能、下架违规直播；
　（三）暂停店铺经营，关闭违规直播间；
　（四）情节严重的，永久封禁账号并依法追究法律责任。
第五条 商户对平台处理措施有异议的，可依照《大众点评网商户申诉规则》申请申诉。
第六条 本规范依据《中华人民共和国电子商务法》《中华人民共和国网络安全法》等法律法规制定。
第七条 本规范自发布之日起施行，平台有权根据法律法规及业务需要适时修订。"""


def _build_demo_meta():
    """不调 LLM 的演示数据：用与 rule_demo 同主题的样例文本，
    跑通 normalize → 向量化 → 图构建 → 检索 全流程。"""
    e = _Ent
    rels = [
        # 规则发布/适用
        _Rel("平台", "publishes", "商户直播行为规范"),
        _Rel("商户直播行为规范", "appliesTo", "商户"),
        _Rel("商户直播行为规范", "appliesTo", "主播"),
        _Rel("商户直播行为规范", "appliesTo", "店铺经营者"),
        _Rel("商户直播行为规范", "hasLegalBasis", "中华人民共和国电子商务法"),
        _Rel("商户直播行为规范", "hasLegalBasis", "中华人民共和国网络安全法"),
        # 平台边界
        _Rel("平台", "isAffiliate", "大众点评网"),
        _Rel("平台", "reviews", "主播"),
        # 违规
        _Rel("商户", "commits", "直播穿着暴露服饰"),
        _Rel("商户", "commits", "直播展示违禁品"),
        _Rel("商户", "commits", "夸大宣传"),
        _Rel("商户", "commits", "擅自收集消费者个人信息"),
        _Rel("商户", "commits", "引导站外交易"),
        # 处罚
        _Rel("直播穿着暴露服饰", "triggers", "警告限期整改"),
        _Rel("直播展示违禁品", "triggers", "下架违规直播"),
        _Rel("夸大宣传", "triggers", "限制直播功能"),
        _Rel("擅自收集消费者个人信息", "triggers", "关闭违规直播间"),
        _Rel("引导站外交易", "triggers", "封禁账号"),
        _Rel("平台", "detects", "直播穿着暴露服饰"),
        _Rel("平台", "detects", "直播展示违禁品"),
        _Rel("平台", "handles", "警告限期整改"),
        _Rel("平台", "handles", "下架违规直播"),
        _Rel("平台", "handles", "限制直播功能"),
        _Rel("平台", "handles", "关闭违规直播间"),
        _Rel("平台", "handles", "封禁账号"),
        # 消费者权益
        _Rel("消费者", "hasRight", "申诉"),
        _Rel("商户直播行为规范", "provides", "申诉"),
    ]
    ents = [
        e("大众点评网", "Platform"), e("平台", "Platform"),
        e("商户", "Merchant"), e("主播", "Merchant"), e("店铺经营者", "Merchant"),
        e("消费者", "User"),
        e("商户直播行为规范", "RuleDocument"),
        e("直播穿着暴露服饰", "Violation"), e("直播展示违禁品", "Violation"),
        e("夸大宣传", "Violation"), e("擅自收集消费者个人信息", "Violation"),
        e("引导站外交易", "Violation"),
        e("警告限期整改", "Penalty"), e("下架违规直播", "Penalty"),
        e("限制直播功能", "Penalty"), e("关闭违规直播间", "Penalty"),
        e("封禁账号", "Penalty"),
        e("申诉", "Remedy"),
        e("中华人民共和国电子商务法", "Law"), e("中华人民共和国网络安全法", "Law"),
        e("消费者个人信息", "PersonalData"),
        e("大众点评网商户申诉规则", "RuleDocument"),
    ]
    return [{
        "file": "[demo] 商户直播行为规范.txt",
        "chars": len(DEMO_TEXT),
        "text": DEMO_TEXT,
        "num_chunks": 1,
        "entities": ents, "relations": rels,
    }]


# ============================================================
# 基于 semantica 原生 API 的 LLM 抽取器（17 的核心，区别于 15）
# ============================================================
class SemanticaLLMExtractor:
    """用 semantica 原生 NERExtractor / RelationExtractor 完成的抽取器。

    内部完全走 semantica 的抽取 API：
      · NERExtractor(method="llm", entity_types=[...], provider="openai",
                     llm_model=..., base_url=..., api_key=...)
      · RelationExtractor(method="llm", relation_types=[...], ...)

    semantica 会按 max_text_length 自动分块（_extract_entities_chunked /
    _extract_relations_chunked）并自动校正偏移；返回的 Entity/Relation 字段
    与 _Ent/_Rel 兼容。direct=False 表示 process_file 走"逐 chunk"路径。
    """

    direct = False  # process_file 据此进入逐 chunk 循环

    def __init__(self, shard_len: int = 8000, max_text_length: int = 8000,
                 min_confidence: float = 0.5):
        # 长文本：交给 semantica 自动分块（max_text_length 即每块上限）
        self.max_text_length = max_text_length
        self.ner = self._build_ner()
        self.rel = self._build_rel()

    def _provider_kwargs(self):
        return {
            "provider": "openai",          # OpenAIProvider（OpenAI 兼容）
            "llm_model": QWEN_MODEL,       # extractor 里 llm_model→model
            "base_url": DASHSCOPE_API_URL, # 触发 instructor Mode.JSON
            "api_key": detect_api_key(),
        }

    def _build_ner(self):
        from semantica.semantic_extract import NERExtractor
        return NERExtractor(
            method="llm",
            entity_types=ENTITY_TYPES,     # 领域实体类型名单
            min_confidence=0.5,
            max_text_length=self.max_text_length,
            **self._provider_kwargs(),
        )

    def _build_rel(self):
        from semantica.semantic_extract import RelationExtractor
        return RelationExtractor(
            method="llm",
            relation_types=RELATION_TYPES, # 领域关系类型名单
            confidence_threshold=0.6,
            max_text_length=self.max_text_length,
            **self._provider_kwargs(),
        )

    # ---------- 实体 ----------
    def extract(self, text: str) -> list:
        """调用 semantica 原生 NERExtractor.extract，返回 Entity 列表。
        若实体名不在原文（LLM 幻觉/归一噪声），做一次后置过滤。"""
        try:
            ents = self.ner.extract(text)
        except Exception as e:
            print(f"    ⚠️ semantica NER 失败: {str(e)[:180]}", flush=True)
            return []
        out = []
        for e in ents or []:
            if not getattr(e, "text", "") or not getattr(e, "label", ""):
                continue
            if e.text not in text and e.text.strip() not in text:
                continue
            out.append(e)
        return out

    # ---------- 关系 ----------
    def extract_relations(self, text: str, entities: list) -> list:
        """调用 semantica 原生 RelationExtractor.extract，返回 Relation 列表。"""
        if not entities:
            return []
        try:
            rels = self.rel.extract(text, entities=entities)
        except Exception as e:
            print(f"    ⚠️ semantica 关系抽取失败: {str(e)[:180]}", flush=True)
            return []
        out = []
        for r in rels or []:
            if not getattr(r, "predicate", ""):
                continue
            if r.predicate not in RELATION_TYPES:
                continue
            out.append(r)
        return out


# ------------------------------------------------------------
# 单文件处理（semantica FileIngestor + DocumentParser + TextSplitter；
# 逐 chunk 调 semantica 原生 LLM 抽取）
# ------------------------------------------------------------
def process_file(path: Path, extractor, splitter) -> dict:
    from semantica.ingest import FileIngestor
    from semantica.parse import DocumentParser

    ingestor = FileIngestor()
    parser = DocumentParser()

    print_header(f"📄 {path.name}", char="─")

    # 1. INGEST + PARSE（semantica 不支持 .md，直接按纯文本读取）
    if path.suffix.lower() == ".md":
        raw_text = path.read_text(encoding="utf-8", errors="ignore")
        print(f"  读取 markdown: {len(raw_text)} bytes")
    else:
        fo = ingestor.ingest_file(str(path))
        doc = parser.parse(fo.path)
        raw_text = doc.get("text", doc.get("full_text", ""))
        print(f"  读取 {fo.file_type}: {fo.size} bytes")

    text = strip_page_markers(raw_text)
    print(f"  解析文本: {len(text)} 字符")

    # 2. SPLIT（标题感知结构分块：标题与内容同块；超大块内部再按行切）
    chunks = structure_aware_split(text, splitter.chunk_size,
                                   getattr(splitter, "chunk_overlap", 0))
    print(f"  结构分块: {len(chunks)} 块 "
          f"(size~{splitter.chunk_size}, overlap~{getattr(splitter, 'chunk_overlap', 0)})")

    # 3. EXTRACT（semantica 原生 LLM NER + 关系）；parse-only 跳过
    import time as _t
    all_entities, all_relations = [], []
    if extractor is not None:
        for i, ch in enumerate(chunks):
            print(f"  >> 块{i} 调 semantica NER (chars={len(ch.text)}) ...", flush=True)
            t0 = _t.time()
            try:
                ents = extractor.extract(ch.text)
            except Exception as e:
                print(f"    ⚠️ 块{i} NER 失败: {type(e).__name__}: {str(e)[:200]}", flush=True)
                continue
            ner_dt = _t.time() - t0
            for e in ents:
                try:
                    e.start_char += ch.start_index
                    e.end_char += ch.start_index
                except Exception:
                    pass
                if e.metadata is None:
                    e.metadata = {}
                e.metadata["chunk_id"] = i
            all_entities.extend(ents)

            t1 = _t.time()
            rels = []
            try:
                rels = extractor.extract_relations(ch.text, ents)
                for r in rels:
                    if r.metadata is None:
                        r.metadata = {}
                    r.metadata["chunk_id"] = i
            except Exception as e:
                print(f"    ⚠️ 块{i} 关系抽取失败: {type(e).__name__}: {str(e)[:200]}", flush=True)
            rel_dt = _t.time() - t1
            all_relations.extend(rels)
            print(f"  块{i}: {len(ents)} 实体 / {len(rels)} 关系  "
                  f"(NER {ner_dt:.1f}s, Rel {rel_dt:.1f}s)", flush=True)
    else:
        print(f"  跳过 LLM 抽取（--parse-only）")

    # 4. 去重（便于展示），与 15 相同
    seen_e, uniq_ents = {}, []
    for e in all_entities:
        key = (e.text.strip().lower(), e.label)
        if key not in seen_e:
            seen_e[key] = e
            uniq_ents.append(e)
    seen_r, uniq_rels = set(), []
    for r in all_relations:
        key = (r.subject.text.strip().lower(), r.predicate, r.object.text.strip().lower())
        if key not in seen_r:
            seen_r.add(key)
            uniq_rels.append(r)

    # 4.5 段落结构抽取（规则，不调 LLM）：章节/条标题实体 + partOf 归属关系
    struct_ents, struct_rels = extract_structure(text, all_entities, all_relations)
    for se in struct_ents:
        uniq_ents.append(se)
    for sr in struct_rels:
        key = (sr.subject.text.strip().lower(), "partOf", sr.object.text.strip().lower())
        if key not in seen_r:
            seen_r.add(key)
            uniq_rels.append(sr)

    return {
        "file": path.name,
        "raw_chars": len(raw_text),
        "chars": len(text),
        "text": text,           # 清洗后原文，用于 passage / coverage
        "num_chunks": len(chunks),
        "raw_entities": all_entities,
        "raw_relations": all_relations,
        "entities": uniq_ents,
        "relations": uniq_rels,
    }


def normalize_result(raw_ents, raw_rels, text: str = ""):
    """对原始 entities / relations 做 alias 归一 + 噪声过滤 + 合并
    返回 (ents, rels) — 都是 normalized 后的 list
    改动文本时若新名仍是原文子串，则更新 start/end；否则回退原名（保证位置有效）。
    """
    seen_e = {}
    for e in raw_ents:
        name = normalize_alias(e.text)
        if not name or is_noise_object(name):
            continue
        key = (name, e.label)
        if key not in seen_e or e.confidence > seen_e[key].confidence:
            seen_e[key] = e
    ents = list(seen_e.values())
    for e in ents:
        new = normalize_alias(e.text)
        if new and new != e.text:
            pos = text.find(new) if text else -1
            if pos >= 0:
                e.start_char, e.end_char = pos, pos + len(new)
                e.text = new
            else:
                # 新名在原文中不存在 → 保留原名，避免文本与位置脱节
                e.text = e.text

    seen_r = {}
    for r in raw_rels:
        s = normalize_alias(r.subject.text)
        o = normalize_alias(r.object.text)
        if not s or not o:
            continue
        if is_noise_object(s) or is_noise_object(o):
            continue
        key = (s, r.predicate, o)
        if key not in seen_r or r.confidence > seen_r[key].confidence:
            seen_r[key] = r
    rels = list(seen_r.values())
    for r in rels:
        # 存储一律用原文写法（保证与实体节点同名字），仅作别名归一时才映射；
        # 若原文子串存在则直接用原文，否则用归一后的名字兜底
        rs = r.subject.text if r.subject.text and r.subject.text in text else normalize_alias(r.subject.text)
        ro = r.object.text if r.object.text and r.object.text in text else normalize_alias(r.object.text)
        r.subject.text = rs
        r.object.text = ro
    return ents, rels


def _parse_heading(ln: str):
    """识别 章/节/条 标题行 → (level, 规范名)；不是标题返回 (None, None)。
    level: 1=章, 2=节, 3=条（规范名形如"第一章 总则""第N条【标题】"）。
    兼容 markdown：剥掉 #~###### 与 ** 粗体标记后匹配。"""
    cn_num = "一二三四五六七八九十百"
    ln = (ln or "").strip()
    ln = re.sub(r"^#{1,6}\s*", "", ln).replace("**", "").strip()
    m = re.match(rf"^第([{cn_num}]+)章", ln)
    if m:
        rest = ln[len(m.group(0)):].strip()
        return 1, f"第{m.group(1)}章" + (f" {rest}" if rest else "")
    m = re.match(rf"^第([{cn_num}]+)节", ln)
    if m:
        rest = ln[len(m.group(0)):].strip()
        return 2, f"第{m.group(1)}节" + (f" {rest}" if rest else "")
    m = re.match(r"^第([一二三四五六七八九十百]+)条", ln)
    if m:
        b = re.search(r"【([^】]+)】", ln)
        base = f"第{m.group(1)}条"
        return 3, base + (f"【{b.group(1)}】" if b else "")
    # 协议类：中文数字大标题（"一、押金的支付"）→ level 2
    m = re.match(r"^([一二三四五六七八九十]+)、", ln)
    if m:
        rest = ln[len(m.group(0)):].strip()
        return 2, (f"{m.group(1)}、{rest}" if rest else f"{m.group(1)}、")
    # 协议类：数字小节（"1.1 xxx"）→ level 3（整行较短且不含多个句号，避免正文行误判）
    m = re.match(r"^(\d+\.\d+)\b(.+)$", ln)
    if m and len(m.group(2)) <= 80 and m.group(2).count("。") <= 1:
        return 3, m.group(1)
    return None, None


def _heading_spans(text: str) -> list:
    """扫描全文，返回 章/节/条 标题的有序列表 [(level, 规范名, start, end)]。
    结构分块、标题块段索引、段落结构抽取三处共用，保证标题口径一致。"""
    lines = []
    pos = 0
    for raw_ln in text.split("\n"):
        ln = raw_ln.rstrip("\r")
        s = text.find(ln, pos)
        if s < 0:
            s = pos
        lines.append((ln, s, s + len(ln)))
        pos = s + len(ln) + 1
    heads = []
    for ln, s, e in lines:
        lvl, nm = _parse_heading(ln)
        if lvl is not None:
            heads.append((lvl, nm, s, e))
    heads.sort(key=lambda x: x[2])
    return heads


def extract_structure(text: str, entities: list, rels: list) -> tuple:
    """段落结构抽取（纯规则，不调 LLM）：
    识别文档大纲层级：章/节/条（含【标题】），生成——
      1) 层级实体：文档根 = RuleDocument；章/节/条标题分别归入本体结构类
         Chapter/Section/Article（ontology_owl.structure_class），metadata 记录 level
      2) partOf 关系：条/节 → 章；章 → 文档根；标题覆盖区内的正文实体 → 最近标题
    """
    heads = _heading_spans(text)

    # 文档根标题：取正文开头第一条不像"章/节/条"的短标题行（兼容 markdown '# **名**'）
    doc_title = None
    for raw_ln in text.split("\n")[:12]:
        ln = raw_ln.replace("*", "").strip()
        ln = ln.lstrip("#").strip() if ln.startswith("#") else ln
        if not ln:
            continue
        if _parse_heading(ln)[0] is not None:
            break
        if len(ln) <= 60 and not ln[0].isdigit():
            doc_title = ln
            break
        break

    struct_ents, partof, seen_struct = [], [], set()
    ent_sorted = sorted(entities, key=lambda x: getattr(x, "start_char", 0) or 0)
    idx = 0
    stack = []  # (level, 名称)；level0 = 文档根
    if doc_title:
        stack.append((0, doc_title))
        if doc_title not in seen_struct:
            seen_struct.add(doc_title)
            ent = _Ent(doc_title, "RuleDocument", 1.0, 0, len(doc_title))
            ent.metadata["structure"] = True
            ent.metadata["heading_level"] = 0
            struct_ents.append(ent)
    for i, (lvl, nm, s, e) in enumerate(heads):
        end = heads[i + 1][2] if i + 1 < len(heads) else len(text)
        if nm not in seen_struct:
            seen_struct.add(nm)
            label = owl.structure_class(lvl) if owl else "RuleDocument"
            ent = _Ent(nm, label, 1.0, s, e)
            ent.metadata["structure"] = True
            ent.metadata["heading_level"] = lvl
            struct_ents.append(ent)
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        if stack:
            partof.append((nm, stack[-1][1]))
        stack.append((lvl, nm))
        while idx < len(ent_sorted) and (getattr(ent_sorted[idx], "start_char", 0) or 0) < end:
            en = ent_sorted[idx]
            st = getattr(en, "start_char", -1) or -1
            if st >= s and en.text not in seen_struct:
                partof.append((en.text, nm))
            idx += 1
    seen_p, rels = set(), []
    for s_text, o_text in partof:
        k = (s_text, o_text)
        if k not in seen_p:
            seen_p.add(k)
            rels.append(_Rel(s_text, "partOf", o_text, 1.0))
    return struct_ents, rels


def constraint_backcheck(meta: dict) -> int:
    """本体 domain/range 回接：对归一后的 relations 做约束检查。

    对违规三元组：confidence 压到 ≤0.5（< 0.8 高置信档），metadata['constraint']
    写明原因（domain/range 不符 / 端点类型未知）。返回违规条数（供 main 打印）。"""
    if not (owl and ONTOLOGY_OK):
        return 0
    ents = [{"text": getattr(e, "text", ""), "label": getattr(e, "label", "")}
            for e in meta.get("entities", [])]
    rels = [{"subject": getattr(r.subject, "text", ""),
             "predicate": getattr(r, "predicate", ""),
             "object": getattr(r.object, "text", "")}
            for r in meta.get("relations", [])]
    try:
        viols = owl.constraint_violations(ents, rels)
    except Exception as e:
        print(f"    ⚠️ 约束检查失败: {e}", flush=True)
        return 0
    meta["constraint_violations"] = viols
    n = 0
    for v in viols:
        for r in meta.get("relations", []):
            if (getattr(r.subject, "text", "") == v["subject"]
                    and getattr(r, "predicate", "") == v["predicate"]
                    and getattr(r.object, "text", "") == v["object"]):
                r.confidence = min(float(getattr(r, "confidence", 1.0) or 1.0), 0.5)
                md = getattr(r, "metadata", None)
                if md is None:
                    r.metadata = {}
                r.metadata["constraint"] = (v.get("why", "")
                                            + " expect=" + ",".join(v.get("expect_domain")
                                                                   or v.get("expect_range")
                                                                   or []))
                n += 1
                break
    return n


def preview(meta: dict):
    """终端打印实体/关系预览（按置信度降序，限 30 条）"""
    ent_counts: dict = {}
    for e in meta["entities"]:
        ent_counts[e.label] = ent_counts.get(e.label, 0) + 1

    ents_sorted = sorted(meta["entities"], key=lambda x: -x.confidence)[:30]
    print(f"\n  🏷️ 实体共 {len(meta['entities'])} 个，类型分布: {ent_counts}")
    print(f"   （按置信度降序展示前 {len(ents_sorted)} 条）")
    print(f"   {'#':>3}  {'实体':<28} {'类型':<14} {'置信度':>6}")
    print(f"   {'─'*3}  {'─'*28} {'─'*14} {'─'*6}")
    for i, e in enumerate(ents_sorted, 1):
        label = (e.label or "")[:14]
        print(f"   {i:>3}  {e.text[:28]:<28} {label:<14} {e.confidence:>6.2f}")

    rel_counts: dict = {}
    for r in meta["relations"]:
        rel_counts[r.predicate] = rel_counts.get(r.predicate, 0) + 1
    rels_sorted = sorted(meta["relations"], key=lambda x: -x.confidence)[:30]
    print(f"\n  🔗 关系共 {len(meta['relations'])} 条，类型分布: {rel_counts}")
    print(f"   （按置信度降序展示前 {len(rels_sorted)} 条）")
    print(f"   {'#':>3}  {'主语':<20} {'谓词':<16} {'宾语':<20}")
    print(f"   {'─'*3}  {'─'*20} {'─'*16} {'─'*20}")
    for i, r in enumerate(rels_sorted, 1):
        print(f"   {i:>3}  {r.subject.text[:20]:<20} {r.predicate[:16]:<16} {r.object.text[:20]:<20}")


def dump_result(meta: dict, out_dir: Path, run_info: dict) -> dict:
    """保存单文件 JSON（内嵌 run_info，记录是哪个脚本跑的），返回可序列化结构体"""
    ents = [{
        "text": e.text, "label": e.label, "confidence": round(e.confidence, 3),
        "start": getattr(e, "start_char", None), "end": getattr(e, "end_char", None),
        "metadata": getattr(e, "metadata", {}) or {},
    } for e in meta["entities"]]
    rels = [{
        "subject": r.subject.text, "predicate": r.predicate,
        "object": r.object.text, "confidence": round(r.confidence, 3),
        "metadata": getattr(r, "metadata", {}) or {},
    } for r in meta["relations"]]

    label_count: dict = {}
    for e in meta["entities"]:
        label_count[e.label] = label_count.get(e.label, 0) + 1
    pred_count: dict = {}
    for r in meta["relations"]:
        pred_count[r.predicate] = pred_count.get(r.predicate, 0) + 1

    stem = Path(meta["file"]).stem
    json_path = out_dir / f"{stem}.json"
    json.dump({
        **run_info,
        "file": meta["file"],
        "stats": {"entities": len(ents), "relations": len(rels),
                  "label_count": label_count, "pred_count": pred_count},
        "entities": ents,
        "relations": rels,
    }, open(json_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return {"file": meta["file"],
            "stats": {"entities": len(ents), "relations": len(rels),
                      "label_count": label_count, "pred_count": pred_count},
            "entities": ents, "relations": rels, "json": str(json_path)}


# ------------------------------------------------------------
# 原文覆盖率报告：每段是否被实体/SPO 覆盖
# ------------------------------------------------------------
def coverage_report(meta):
    """对单文件做覆盖率分析：按段（换行/章节）切，每段统计实体出现数。
    同时给出"低覆盖"段（实体数 0 的）。
    """
    full_text = meta.get("text", "")
    if not full_text:
        return
    # 把原文按行切（每行 ≈ 一条/一款），合并太短的碎片
    raw_paras = [p.strip() for p in re.split(r"\n+", full_text) if p.strip()]
    if not raw_paras:
        return
    paragraphs = []
    for p in raw_paras:
        if len(p) < 15 and paragraphs and len(paragraphs[-1]) + len(p) < 200:
            paragraphs[-1] = paragraphs[-1] + " " + p
        else:
            paragraphs.append(p)
    # 收集所有 entity 文本（用于 substring 匹配）
    ent_texts = {e.text for e in meta["entities"]}
    rel_pairs = {(r.subject.text, r.object.text, r.predicate) for r in meta["relations"]}

    print(f"\n  📐 原文覆盖分析（共 {len(paragraphs)} 段）", flush=True)
    para_stats = []
    for i, p in enumerate(paragraphs):
        # 段里出现了哪些 entity
        ent_hits = [e for e in ent_texts if e in p]
        # 段里出现了哪些 spo
        spo_hits = [(s, o, pr) for (s, o, pr) in rel_pairs if s in p and o in p]
        para_stats.append((i, p, ent_hits, spo_hits))

    # 输出每段
    print(f"   {'#':>3}  {'字符':>4}  {'实体数':>6}  {'SPO数':>5}  {'首句':<60}", flush=True)
    print(f"   {'─'*3}  {'─'*4}  {'─'*6}  {'─'*5}  {'─'*60}", flush=True)
    low_cov = []
    for i, p, ent_hits, spo_hits in para_stats:
        first_sent = p.split("。")[0][:58] if "。" in p else p[:58]
        marker = ""
        if len(ent_hits) == 0:
            marker = " ⚠️ 无实体"
            low_cov.append((i, p))
        print(f"   {i:>3}  {len(p):>4}  {len(ent_hits):>6}  {len(spo_hits):>5}  "
              f"{first_sent}{marker}", flush=True)
    if low_cov:
        print(f"\n   ⚠️ {len(low_cov)} 段无任何实体覆盖：", flush=True)
        for i, p in low_cov[:3]:
            print(f"     [{i}] {p[:100]}...", flush=True)


# ------------------------------------------------------------
# 实体富化：给每个实体补一段"上下文 passage"（用于向量检索时回看原文）
# ------------------------------------------------------------
def enrich_with_passage(ent, full_text: str, window: int = 80) -> str:
    """在原文中搜 entity.text 第一次出现位置，取 ±window 字符作 passage。
    找不到则用空串。"""
    needle = ent.text
    idx = full_text.find(needle)
    if idx < 0:
        return ""
    start = max(0, idx - window)
    end = min(len(full_text), idx + len(needle) + window)
    snippet = full_text[start:end].replace("\n", " ").strip()
    return snippet


# ------------------------------------------------------------
# 向量化：把全部实体嵌入 + 入 FAISS 库
# 模型：本地 BAAI/bge-m3（多语种，1024 维）— D:\work\wangxuanhao\知识图谱\model
# 备选：sentence-transformers BAAI/bge-small-zh-v1.5（中文，512 维，~25MB 缓存）
# ------------------------------------------------------------
_EMBED_CACHE: dict = {}


def get_embedder():
    """加载本地 bge-m3（模块级缓存，供向量化与段索引复用）"""
    if "_m" not in _EMBED_CACHE:
        from sentence_transformers import SentenceTransformer
        model_path = str(EMBEDDING_MODEL_PATH) if EMBEDDING_MODEL_PATH.exists() else "BAAI/bge-small-zh-v1.5"
        print(f"     模型: {model_path}", flush=True)
        _EMBED_CACHE["_m"] = SentenceTransformer(model_path, device="cpu")
    return _EMBED_CACHE["_m"]


class _TextEmbedderAdapter:
    """把本地 SentenceTransformer 包成 semantica 期望的 embed_batch 接口，
    让 semantica 内部关系去重/相似度计算复用同一个 bge-m3，避免二次加载 + fastembed 离线报错。"""
    def __init__(self, model):
        self._m = model

    def embed_batch(self, texts):
        import numpy as np
        vecs = self._m.encode(list(texts), normalize_embeddings=True,
                              batch_size=32, show_progress_bar=False)
        return [np.asarray(v, dtype="float32") for v in vecs]

    def embed_one(self, text):
        import numpy as np
        return np.asarray(self._m.encode([text], normalize_embeddings=True)[0], dtype="float32")


def patch_semantica_text_embedder():
    """重定向 semantica 内部的 get_text_embedder → 复用本地 bge-m3。
    semantica 关系匹配（find_best_match_index）默认尝试加载 fastembed 的
    BAAI/bge-small-en-v1.5，离线会 ERROR；这里让它直接用我们已加载的本地模型，
    既消噪又省一次加载。"""
    try:
        from semantica.semantic_extract import methods as sem_methods
        emb = get_embedder()
        sem_methods._embedder_cache = _TextEmbedderAdapter(emb)
    except Exception as e:
        print(f"    ⚠️ patch semantica text embedder 失败: {e}", flush=True)


EMBEDDING_MODEL_PATH = DATA / "model"   # bge-m3 本地路径


def build_vector_store(all_meta, out_dir: Path):
    """把每份文件 normalize 后的实体嵌入并写入 FAISS
    返回 (store, corpus, dim) — corpus 是 [{text, label, passage, source_file, confidence, chunk_id}, ...]
    """
    # 1. 准备 corpus（每个实体一行，结构 = 名称 + 类型 + passage）
    corpus = []
    for meta in all_meta:
        src = meta["file"]
        full_text = meta.get("text", "")
        for e in meta["entities"]:
            passage = enrich_with_passage(e, full_text)
            corpus.append({
                "text": e.text,
                "label": e.label,
                "passage": passage,
                "source_file": src,
                "confidence": round(e.confidence, 3),
                "chunk_id": (e.metadata or {}).get("chunk_id", 0),
                "embed_input": f"{e.text} ({e.label}) | {passage[:60]}" if passage else f"{e.text} ({e.label})",
            })
    if not corpus:
        return None, [], 0

    print(f"\n  🧊 向量化 {len(corpus)} 个实体...", flush=True)
    import time as _t
    t0 = _t.time()
    embedder = get_embedder()
    inputs = [c["embed_input"] for c in corpus]
    vecs = embedder.encode(inputs, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
    dim = vecs.shape[1]
    print(f"     嵌入完成 {len(corpus)} 维 {dim}，耗时 {_t.time()-t0:.1f}s", flush=True)

    # 2. 写入 FAISS
    t0 = _t.time()
    import faiss
    index = faiss.IndexFlatIP(dim)  # 用内积（已归一化=余弦）
    index.add(vecs.astype("float32"))
    print(f"     FAISS IndexFlatIP 建索引 {index.ntotal} 条，耗时 {_t.time()-t0:.1f}s", flush=True)

    # 3. 存盘 + metadata
    #   faiss.write_index 用 C fopen，不支持中文路径 → 先 serialize 成 bytes 再 py 写
    faiss_dir = out_dir / "faiss"
    faiss_dir.mkdir(exist_ok=True)
    with open(faiss_dir / "entities.index", "wb") as f:
        f.write(faiss.serialize_index(index))
    with open(faiss_dir / "corpus.json", "w", encoding="utf-8") as f:
        json.dump(corpus, f, ensure_ascii=False, indent=2)
    print(f"     索引写入 {faiss_dir}/", flush=True)

    # 4. 简易封装（仿 semantica FAISSStore 接口）
    class SimpleStore:
        def __init__(self, index, corpus, dim, embedder):
            self.index = index
            self.corpus = corpus
            self.dim = dim
            self.embedder = embedder
        def search(self, query: str, k: int = 5):
            v = self.embedder.encode([query], normalize_embeddings=True).astype("float32")
            scores, ids = self.index.search(v, k)
            results = []
            for s, i in zip(scores[0], ids[0]):
                if i < 0 or i >= len(self.corpus):
                    continue
                c = self.corpus[int(i)]
                results.append({
                    "score": float(s),
                    "text": c["text"],
                    "label": c.get("label", ""),
                    "passage": c.get("passage", ""),
                    "source_file": c.get("source_file", ""),
                    "confidence": c.get("confidence", 1.0),
                })
            return results
        def count(self):
            return self.index.ntotal
    return SimpleStore(index, corpus, dim, embedder), corpus, dim


def retrieval_demo(store, queries: list[str], k: int = 3, title: str = "实体向量检索"):
    print(f"\n  🔍 {title}", flush=True)
    for q in queries:
        res = store.search(q, k=k)
        if not res:
            continue
        print(f"\n  ── Q: {q}", flush=True)
        for r in res:
            label = r.get("label") or "?"
            passage = (r.get("passage") or "").strip()
            line = f"    · {r['text']}  [{label}]  sim={r['score']:.3f}"
            if passage:
                line += f"  ‖ {passage[:70]}"
            print(line, flush=True)


# ------------------------------------------------------------
# 全文分块级索引：覆盖"未被抽成实体的原文剩余内容"
# semantica 只提供实体/文档向量化，正文里检不出的部分就丢了。
# 这里把每份文件原文切成 段（按空白段，超长再滑窗），
# 整段嵌入成独立 FAISS 库——任何 query 都能召回命中段落。
# ------------------------------------------------------------
def split_to_segments(text: str, max_len: int = 500, overlap: int = 50) -> list[str]:
    """把文本切成检索友好段落：
    优先按行/空行分隔（章条款粒度）；超长段再按 max_len 滑窗。
    """
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    if not paras:
        paras = re.split(r"(?<=[。；])\s*", text)
        paras = [p.strip() for p in paras if p.strip()]
    segs: list[str] = []
    for p in paras:
        if len(p) <= max_len:
            segs.append(p)
            continue
        # 超长滑窗
        start = 0
        while start < len(p):
            segs.append(p[start:start + max_len])
            start += max_len - overlap
    # 相邻段落太短（如单句）则与上一段合并，减少碎片
    merged: list[str] = []
    for s in segs:
        if merged and len(s) < 40 and len(merged[-1]) + len(s) < max_len * 2:
            merged[-1] = merged[-1] + " " + s
        else:
            merged.append(s)
    return merged


def _split_long_region(text: str, s: int, e: int, max_len: int, overlap: int = 0) -> list:
    """把 [s,e) 连续文本按行边界切成若干 ≤ max_len 的子区间 [(sub_s, sub_e)]，
    优先断在空行/换行，避免拦腰切断条款内容。"""
    subs = []
    cur = s
    guard = 0
    while cur < e and guard < 100000:
        guard += 1
        target = min(cur + max_len, e)
        if target <= cur:
            break
        cut = target
        nl = text.rfind("\n\n", cur, target)
        if nl <= cur:
            nl = text.rfind("\n", cur, target)
        if nl > cur:
            cut = min(nl, target) + 1
        cut = max(cut, cur + 1)
        subs.append((cur, cut))
        if cut >= e:
            break
        cur = cut - overlap if overlap else cut
    return subs


def structure_aware_split(text: str, chunk_size: int, chunk_overlap: int = 0):
    """标题感知分块（17 的结构优化核心）：
    以 章/节/条 标题行（含 md 的 "## " 前缀）作天然边界 → 标题与其下方内容始终同块，
    LLM 抽取时能看到"这个条款在约束什么"，partOf 归属关系才自然成立。
    每个 chunk 都是原文的连续切片，实体 start_char 叠加偏移依然精确。
    返回 list[semantica Chunk]（text/start_index/end_index 与原生一致）。"""
    from semantica.split.semantic_chunker import Chunk
    spans = _heading_spans(text)
    sections = []
    if spans:
        for i, (lvl, nm, s, e) in enumerate(spans):
            end = spans[i + 1][2] if i + 1 < len(spans) else len(text)
            sections.append((nm, s, end, True))
    if not sections:
        sections = [(None, 0, len(text), False)]
    chunks = []
    for nm, s, e, structured in sections:
        if e - s <= chunk_size:
            chunks.append(Chunk(text=text[s:e], start_index=s, end_index=e,
                                metadata={"heading": nm or "", "structure": structured}))
        else:
            for sub_s, sub_e in _split_long_region(text, s, e, chunk_size, chunk_overlap):
                chunks.append(Chunk(text=text[sub_s:sub_e], start_index=sub_s, end_index=sub_e,
                                    metadata={"heading": nm or "", "structure": structured}))
    return chunks


def build_structural_segments(text: str, max_len: int = 500) -> list[str]:
    """标题块级切片：检索单元 = 标题 + 其下内容（一条完整条款）。
    超长节切子块时给每个子块带上标题前缀，向量检索才能召回"标题+内容"一体。
    无标题结构时退回 split_to_segments。"""
    spans = _heading_spans(text)
    if not spans:
        return split_to_segments(text, max_len)
    segs = []
    for i, (lvl, nm, s, e) in enumerate(spans):
        end = spans[i + 1][2] if i + 1 < len(spans) else len(text)
        if end - s <= max_len:
            segs.append(text[s:end])
            continue
        for sub_s, sub_e in _split_long_region(text, s, end, max_len, overlap=0):
            sub = text[sub_s:sub_e]
            segs.append(sub if sub.startswith(nm) else nm + (" " if sub.strip() else "") + sub)
    segs = [sg for sg in segs if sg.strip()]
    return segs


def build_segment_store(all_meta, out_dir: Path):
    """把每份文件的原文整段嵌入进独立 FAISS 库。
    与实体库互补：实体库定位"命名实体"，段库召回"任意原文片段"。
    返回 (SimpleStore, corpus, dim)，corpus[i] = {text(段落), source_file, seg_id}
    """
    corpus = []
    for meta in all_meta:
        src = meta["file"]
        segs = build_structural_segments(meta.get("text", ""))
        for i, s in enumerate(segs):
            corpus.append({
                "text": s,
                "source_file": src,
                "seg_id": i,
                "embed_input": s,
            })
    if not corpus:
        return None, [], 0

    print(f"\n  🧩 全文分块级索引: {len(corpus)} 段", flush=True)
    import time as _t
    t0 = _t.time()
    embedder = get_embedder()
    inputs = [c["embed_input"] for c in corpus]
    vecs = embedder.encode(inputs, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
    dim = vecs.shape[1]
    print(f"     嵌入 {len(corpus)} 段 维 {dim}，耗时 {_t.time()-t0:.1f}s", flush=True)

    import faiss
    index = faiss.IndexFlatIP(dim)
    index.add(vecs.astype("float32"))
    faiss_dir = out_dir / "faiss"
    faiss_dir.mkdir(exist_ok=True)
    with open(faiss_dir / "segments.index", "wb") as f:
        f.write(faiss.serialize_index(index))
    with open(faiss_dir / "segments.json", "w", encoding="utf-8") as f:
        json.dump(corpus, f, ensure_ascii=False, indent=2)

    class SimpleStore:
        def __init__(self, index, corpus, dim, embedder):
            self.index = index
            self.corpus = corpus
            self.dim = dim
            self.embedder = embedder
        def search(self, query: str, k: int = 5):
            v = self.embedder.encode([query], normalize_embeddings=True).astype("float32")
            scores, ids = self.index.search(v, k)
            results = []
            for s, i in zip(scores[0], ids[0]):
                if i < 0 or i >= len(self.corpus):
                    continue
                c = self.corpus[int(i)]
                results.append({
                    "score": float(s),
                    "text": c["text"],
                    "label": c.get("label", ""),
                    "passage": c.get("passage", ""),
                    "source_file": c.get("source_file", ""),
                    "confidence": c.get("confidence", 1.0),
                })
            return results
        def count(self):
            return self.index.ntotal
    return SimpleStore(index, corpus, dim, embedder), corpus, dim


# ------------------------------------------------------------
# 图构建 + 图检索（GraphRAG 核心）
# ------------------------------------------------------------
def build_graph(all_meta, out_dir: Path):
    """把全部实体的 SPO 构造成 networkx.DiGraph
    节点属性：text, label, source_file, confidence, passage
    边属性：predicate, source_file, confidence
    返回 (graph, node_index) — node_index: {entity_text: node_id}
    """
    import networkx as nx
    G = nx.DiGraph()
    node_index: dict = {}  # canonical_name -> node_id
    node_meta: dict = {}   # node_id -> {text, label, source_file, ...}

    def add_node(text, label, source_file, confidence=1.0, passage=""):
        if not text or is_noise_object(text):
            return None
        key = (text, label)
        if key in node_meta:
            return text
        if text not in G:
            G.add_node(text, label=label, source_file=source_file,
                       confidence=confidence, passage=passage)
        node_index[text] = text
        node_meta[key] = {"text": text, "label": label, "source_file": source_file,
                          "confidence": confidence, "passage": passage}
        return text

    for meta in all_meta:
        src = meta["file"]
        full_text = meta.get("text", "")
        # 节点：从实体
        for e in meta["entities"]:
            passage = enrich_with_passage(e, full_text)
            add_node(e.text, e.label, src, e.confidence, passage)
        # 边：从关系
        for r in meta["relations"]:
            s = r.subject.text
            o = r.object.text
            if not s or not o or is_noise_object(s) or is_noise_object(o):
                continue
            # 确保两端节点都存在（如果 LLM 抽了关系但没抽实体）
            if s not in G:
                G.add_node(s, label="Unknown", source_file=src, confidence=0.0, passage="")
            if o not in G:
                G.add_node(o, label="Unknown", source_file=src, confidence=0.0, passage="")
            if G.has_edge(s, o):
                # 同一对 (s,o) 允许多 predicate；用 multigraph 模式
                continue
            G.add_edge(s, o, predicate=r.predicate, source_file=src,
                       confidence=r.confidence)

    # ---- 本体层级边：类节点 + instanceOf/subClassOf（让输出图自带层级） ----
    if EMIT_CLASS_EDGES and owl is not None and ONTOLOGY_OK:
        classes = {c["name"]: c for c in owl.ontology_info()["classes"]}
        added_n = added_e = 0
        for name in classes:
            if name not in G:
                G.add_node(name, label=name, source_file="(ontology)",
                           confidence=1.0, passage="", role="class")
                added_n += 1
        for name, d in classes.items():
            for par in d["parents"]:
                if par in classes and not G.has_edge(name, par):
                    G.add_edge(name, par, predicate="subClassOf",
                               source_file="(ontology)", confidence=1.0)
                    added_e += 1
        for meta in all_meta:
            for e in meta["entities"]:
                if (e.text in G and e.label in classes and e.text != e.label
                        and not G.has_edge(e.text, e.label)):
                    G.add_edge(e.text, e.label, predicate="instanceOf",
                               source_file=meta["file"], confidence=1.0)
                    added_e += 1
        if added_n or added_e:
            print(f"  🧬  本体层级附加: +{added_n} 类节点 / +{added_e} 边 "
                  f"(instanceOf/subClassOf)", flush=True)

    print(f"\n  🕸️  图构建: {G.number_of_nodes()} 节点 / {G.number_of_edges()} 边", flush=True)
    # 持久化 GraphML
    graph_path = out_dir / "graph.graphml"
    nx.write_graphml(G, str(graph_path))
    # 节点邻居统计
    deg = sorted(G.degree, key=lambda x: -x[1])[:8]
    print(f"     度 Top-8: {[(n, d) for n, d in deg]}", flush=True)
    pred_count: dict = {}
    for _, _, d in G.edges(data=True):
        p = d.get("predicate", "?")
        pred_count[p] = pred_count.get(p, 0) + 1
    print(f"     边谓词分布: {pred_count}", flush=True)
    print(f"     图写入: {graph_path}", flush=True)
    # 兼容 18/16：再落一份 graph.json（与 Web 项目 kg_project 同形）
    try:
        gjson = {
            "nodes": [{"id": n, "label": d.get("label", "?"), "text": n,
                       "confidence": float(d.get("confidence", 0) or 0),
                       "source_file": d.get("source_file", ""),
                       "passage": d.get("passage", "")}
                      for n, d in G.nodes(data=True)],
            "edges": [{"source": s, "target": o,
                       "predicate": dd.get("predicate", ""),
                       "confidence": float(dd.get("confidence", 0) or 0),
                       "source_file": dd.get("source_file", "")}
                      for s, o, dd in G.edges(data=True)],
        }
        gj = out_dir / "graph.json"
        json.dump(gjson, open(gj, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"     图 JSON: {gj}", flush=True)
    except Exception as _ge:
        print(f"     ⚠️ graph.json 落盘失败: {_ge}", flush=True)
    return G


def graph_search_demo(G, store, queries: list[dict]):
    """
    多种图检索模式：
    1. seed_neighbors  - 找起点 + N 跳邻居
    2. outgoing        - 起点出发的边（"X 触发什么？"）
    3. incoming        - 指向起点的边（"什么触发 X？"）
    4. multi_hop_path  - A → ? → B 的最短路径
    5. subgraph        - 起点为中心 k-hop 子图
    """
    if G is None or G.number_of_nodes() == 0:
        return
    import networkx as nx
    print(f"\n  🔎 图检索 demo", flush=True)

    for q in queries:
        mode = q["mode"]
        query = q["query"]
        print(f"\n  ── Q: {query}   (mode={mode})", flush=True)

        # 1) 向量召回候选 seed 节点
        seeds_via_vec = store.search(query, k=q.get("seed_k", 3))
        seed_names = [r["text"] for r in seeds_via_vec
                      if r["text"] in G and r["score"] > q.get("seed_thr", 0.0)]
        if not seed_names:
            print(f"     ⚠️ 无 seed 命中（图谱中找不到与 query 相关的节点）", flush=True)
            continue
        seed = seed_names[0]
        print(f"     [向量召回] seed='{seed}'  "
              f"(来自 {G.nodes[seed].get('label','?')}, "
              f"sim={seeds_via_vec[0]['score']:.3f})", flush=True)

        # 2) 按 mode 走图
        if mode == "outgoing":
            hops = q.get("hops", 1)
            paths = list(nx.bfs_edges(G, source=seed, depth_limit=hops))
            if not paths:
                print(f"     → {seed} 无出边", flush=True)
                continue
            for u, v in paths[:10]:
                pred = G[u][v].get("predicate", "?")
                print(f"        {u} ──[{pred}]──▶ {v}", flush=True)

        elif mode == "incoming":
            preds = list(G.predecessors(seed))
            if not preds:
                print(f"     → 无节点指向 {seed}", flush=True)
                continue
            for p in preds[:10]:
                pred = G[p][seed].get("predicate", "?")
                print(f"        {p} ──[{pred}]──▶ {seed}", flush=True)

        elif mode == "neighbors":
            hops = q.get("hops", 2)
            subgraph = nx.ego_graph(G, seed, radius=hops)
            print(f"     {seed} 的 {hops}-hop 子图: {subgraph.number_of_nodes()} 节点 / {subgraph.number_of_edges()} 边")
            for n in list(subgraph.nodes())[:12]:
                lbl = G.nodes[n].get("label", "?")
                if n == seed:
                    print(f"        ★ {n}  [{lbl}]", flush=True)
                else:
                    # 找一条到 seed 的边
                    if subgraph.has_edge(n, seed):
                        p = subgraph[n][seed].get("predicate", "?")
                        print(f"          ← {n}  [{lbl}]  --[{p}]-->", flush=True)
                    elif subgraph.has_edge(seed, n):
                        p = subgraph[seed][n].get("predicate", "?")
                        print(f"          → {n}  [{lbl}]  --[{p}]-->", flush=True)

        elif mode == "path":
            target_text = q.get("target")
            target_node = None
            for n in G.nodes():
                if target_text in n or n in target_text:
                    target_node = n
                    break
            if not target_node:
                print(f"     ⚠️ 找不到 target='{target_text}'", flush=True)
                continue
            try:
                path = nx.shortest_path(G, seed, target_node)
                print(f"     路径 ({len(path)-1} 跳): {' → '.join(path)}", flush=True)
                for u, v in zip(path[:-1], path[1:]):
                    p = G[u][v].get("predicate", "?")
                    print(f"        {u} ──[{p}]──▶ {v}", flush=True)
            except nx.NetworkXNoPath:
                print(f"     ⚠️ {seed} → {target_node} 无路径", flush=True)


# ------------------------------------------------------------
# 主流程
# ------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="17 - 用 semantica 原生 API 批量解析美团规则 txt → 知识图谱")
    ap.add_argument("dir", nargs="?", default=str(DATA / "rule_demo"),
                    help="包含 .txt/.md 的目录（默认 rule_demo，批量可指 rule_txt）")
    ap.add_argument("--parse-only", action="store_true",
                    help="只跑 解析+分块，不调 LLM（自检用）")
    ap.add_argument("--demo", action="store_true",
                    help="demo 模式：不调 LLM，用内置样例演示 向量化+图谱+检索 全流程")
    args = ap.parse_args()

    parse_only = args.parse_only
    demo_only = args.demo
    print_header("17 - semantica 原生 API 批量解析（美团业务规则 → 知识图谱）"
                 if not parse_only else "17 - 解析管道自检（parse-only，不调 LLM）")

    src_dir = Path(args.dir)
    if not src_dir.is_absolute():
        src_dir = DATA / src_dir
    files = sorted(p for p in src_dir.iterdir()
                   if p.suffix.lower() in (".txt", ".md"))
    if not files and not demo_only:
        print(f"❌ {src_dir} 下没有 .txt/.md 文件")
        sys.exit(1)
    txt_total = sum(f.stat().st_size for f in files) if files else 0
    print(f"  目录: {src_dir}")
    print(f"  待处理文件: {len(files)} 个，共 {txt_total/1024:.1f} KB" if files
          else "  [demo] 内置样例文本，无需目录文件")

    out_dir = DATA / "rule_demo_output"
    if not parse_only:
        # 每次运行独立一个时间戳目录，便于维护对比不同版本；demo 模式显式标注
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = out_dir / (f"run_{stamp}_demo" if demo_only else f"run_{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # run_info：记录"是哪个脚本跑的 / 时间 / 参数 / LLM 配置"
    run_info = {
        "source": "17_rule_demo_semantica_api.py",   # ← 脚本标识：区分 15 / 17
        "run_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user_args": vars(args),
        "llm": {"model": QWEN_MODEL, "base_url": DASHSCOPE_API_URL,
                "reasoning_effort": REASONING_EFFORT},
    }

    key = None if (parse_only or demo_only) else detect_api_key()

    # ---- 初始化（semantica 原生抽取器 + semantica 分块器） ----
    extractor = None
    if parse_only or demo_only:
        splitter = None
        if parse_only:
            from semantica.split import TextSplitter
            splitter = TextSplitter(
                method=["entity_aware", "recursive"],
                chunk_size=1200, chunk_overlap=200, ner_method="pattern",
            )
    else:
        from semantica.split import TextSplitter
        print(f"\n  ✅ LLM: {QWEN_MODEL} @ {DASHSCOPE_API_URL}（semantica provider=openai）")
        print(f"  实体类型约束: {ENTITY_TYPES}")
        print(f"  关系类型约束: {RELATION_TYPES}")
        extractor = SemanticaLLMExtractor(max_text_length=8000)
        splitter = TextSplitter(
            method=["entity_aware", "recursive"],
            chunk_size=8000, chunk_overlap=400,
            ner_method="pattern",
        )

    # ---- 逐文件处理 ----
    all_results = []
    all_meta = []   # 包含每文件 chars/num_chunks/text 统计
    if demo_only:
        # 不调 LLM，用内置的样本数据演示 vectorize / graph / retrieval 全流程
        all_meta = _build_demo_meta()
        for meta in all_meta:
            print_header(f"📄 [demo] {meta['file']}", char="─")
            print(f"  文本: {len(meta['text'])} 字符（demo fixture）")
            print(f"  实体: {len(meta['entities'])}  关系: {len(meta['relations'])}")
            coverage_report(meta)
            all_results.append(dump_result(meta, out_dir, run_info))  # demo 也落 content json
    for fp in files:
        if demo_only:
            break
        try:
            meta = process_file(fp, extractor, splitter)
        except Exception as e:
            print(f"\n❌ 处理失败 {fp.name}: {type(e).__name__}: {e}")
            continue
        all_meta.append(meta)
        if not parse_only:
            # 抽取后：alias 归一 + 噪声过滤 + 合并
            meta["entities"], meta["relations"] = normalize_result(
                meta["raw_entities"], meta["raw_relations"], meta["text"]
            )
            # 本体约束回接：domain/range 违规 → 降权 + metadata 打标
            n_viol = constraint_backcheck(meta)
            if n_viol:
                print(f"    ⚠️ 本体约束回接: {n_viol} 条关系降权（domain/range 不符）", flush=True)
            preview(meta)
            coverage_report(meta)   # ← 原文覆盖率
            all_results.append(dump_result(meta, out_dir, run_info))

    # ---- 汇总 ----
    print_header("📊 汇总", char="─")
    if not parse_only:
        # ---- 向量化（实体级）+ 全文段级 双索引 ----
        store, corpus, dim = build_vector_store(all_meta, out_dir)
        seg_store, seg_corpus, seg_dim = build_segment_store(all_meta, out_dir)

        # 实体级检索 demo：命中"命名实体"
        if store is not None:
            retrieval_demo(store, [
                "商户直播穿着违规",
                "擅自收集消费者个人信息",
                "严重违规的处罚",
            ], k=3, title="① 实体级向量检索")

        # 段级检索 demo：命中"原文任意段落"（含未被抽成实体的正文）
        if seg_store is not None:
            retrieval_demo(seg_store, [
                "直播不能穿什么",
                "收集个人信息需要什么前提",
                "如何处理违规的直播间",
            ], k=2, title="② 全文段级向量检索（覆盖剩余正文）")

        # ---- 图构建 + 图检索 demo ----
        G = build_graph(all_meta, out_dir) if (store is not None) else None
        if G is not None:
            graph_search_demo(G, store, [
                {"mode": "outgoing",  "query": "商户直播穿着",   "hops": 1, "seed_k": 3, "seed_thr": 0.0},
                {"mode": "incoming",  "query": "下架违规直播",   "seed_k": 3, "seed_thr": 0.0},
                {"mode": "neighbors", "query": "商户直播行为规范", "hops": 1, "seed_k": 2, "seed_thr": 0.0},
            ])

        summary_path = out_dir / f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        json.dump(all_results, open(summary_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        run_info["summary"] = str(summary_path)
        run_info["stats_total"] = {
            "entities": sum(len(r["entities"]) for r in all_meta),
            "relations": sum(len(r["relations"]) for r in all_meta),
        }
        json.dump(run_info, open(out_dir / "run_info.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)

        # ---- 本体总结：把本次抽取结果"归纳"成本体建设报告 + 候选建议 ----
        if owl is not None and ONTOLOGY_OK:
            try:
                ent_dicts, rel_dicts = [], []
                for meta in all_meta:
                    src = meta.get("file", "")
                    for e in meta["entities"]:
                        ent_dicts.append({"text": e.text, "label": e.label,
                                          "source_file": src,
                                          "confidence": float(getattr(e, "confidence", 1.0) or 1.0)})
                    for r in meta["relations"]:
                        rel_dicts.append({"subject": r.subject.text,
                                          "predicate": r.predicate,
                                          "object": r.object.text,
                                          "source_file": src})
                report = owl.analyze_extraction(ent_dicts, rel_dicts)
                (out_dir / "ontology_summary.md").write_text(
                    owl.render_summary_md(report), encoding="utf-8")
                json.dump(report, open(out_dir / "ontology_summary.json", "w", encoding="utf-8"),
                          ensure_ascii=False, indent=2)
                n_cls = len(report["unknown_candidates"]["new_classes"])
                n_rel = len(report["unknown_candidates"]["new_predicates"])
                print(f"  📋 本体总结: {out_dir / 'ontology_summary.md'} "
                      f"(候选新类 {n_cls} / 候选新关系 {n_rel})", flush=True)
            except Exception as e:
                print(f"  ⚠️ 本体总结生成失败: {e}", flush=True)

        total_e = sum(len(r["entities"]) for r in all_meta)
        total_r = sum(len(r["relations"]) for r in all_meta)
        print(f"\n  处理: {len(all_meta)} 份")
        print(f"  实体总计: {total_e}    关系总计: {total_r}")
        if store is not None:
            print(f"  实体向量库: {store.count()} 条 / {dim} 维 (faiss/entities.index)")
        if seg_store is not None:
            print(f"  段向量库:   {seg_store.count()} 段 / {seg_dim} 维 (faiss/segments.index)")
        if G is not None:
            print(f"  图谱: {G.number_of_nodes()} 节点 / {G.number_of_edges()} 边 (graph.graphml)")
        print(f"  单文件 JSON: {out_dir}/")
        print(f"  汇总 JSON: {summary_path}")
        print(f"  运行元信息: {out_dir / 'run_info.json'}（source=run_info 记录脚本来源）")
    else:
        total_chars = sum(r["chars"] for r in all_meta)
        total_chunks = sum(r["num_chunks"] for r in all_meta)
        print(f"  已解析 {len(all_meta)}/{len(files)} 个文件，"
              f"共 {total_chars} 字符 / {total_chunks} 块")
        print(f"  ✅ 解析→分块管道自检通过。去掉 --parse-only 即可跑 LLM 提取。")


if __name__ == "__main__":
    main()