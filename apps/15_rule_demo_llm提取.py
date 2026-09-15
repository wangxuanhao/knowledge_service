"""
15 - LLM 模式批量解析美团规则文档 → 知识图谱要素
============================================================
批量读取指定目录下的 .txt 规则文档（如《大众点评网商户直播行为规范》），
用 LLM（阿里云 MaaS OpenAI 兼容接口）解析出：
    实体：平台 / 商户 / 主播 / 违规行为 / 处罚 / 要求 / 协议 / 法律 ...
    关系：禁止 / 要求 / 适用于 / 施加处罚 / 引用 / 归类 ...

接口配置（阿里云百炼 MaaS 专用实例，OpenAI 兼容）：
    provider = "openai"
    llm_model = qwen3.8-2.4t-a95b
    base_url  = https://llm-7mhou02rps8uxmu0.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
    api_key   固定取下方 CONFIG["api_key"]（不再读环境变量，避免被系统里的污染 key 覆盖）

运行环境：conda env = llm_model（Python 3.12，semantica 0.6.7 已装）
运行命令：
    D:/work/wangxuanhao/conda/envs/llm_model/python.exe 15_rule_demo_llm提取.py [目录，默认 rule_demo]
可选参数：
    --parse-only               # 只跑 解析+分块，不调 LLM（自检用）
示例：
    ... 15_rule_demo_llm提取.py rule_demo          # 解析演示文件夹
    ... 15_rule_demo_llm提取.py rule_txt           # 批量解析全部已转 txt 的规则（几百份）
"""

import os
import re
import sys
import json
import argparse
from typing import Optional
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
# 用 stub 蒙混过去；我们只用到 FAISSStore
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

# ============================================================
# CONFIG：阿里云 MaaS 专用实例（OpenAI 兼容通道）
# ============================================================
# CONFIG = {
#     "llm_model": "qwen3.8-27b",
#     "base_url": "https://llm-7mhou02rps8uxmu0.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
#     "api_key": "sk-3471c72141c54f9fa0ddf0a3f3e12ca3",  # 可被环境变量 DASHSCOPE_API_KEY 覆盖
# }

CONFIG = {
    "llm_model": "deepseek-v4-flash",
    "base_url": "https://api.deepseek.com",
    "api_key": "sk-fdb69eb6de864dbc853f1e66cb7e909b",  # 固定使用（detect_api_key 不再读环境变量）
    # 思考模式（DeepSeek 思考 token 计入 max_tokens 预算，需给足上限）：
    #   "low"    = 轻思考（快、质量高，推荐）
    #   "medium" = 中度思考
    #   None     = 开启完整思考（慢，需配大 max_tokens）
    #   "none"   = 关闭思考（最快，但实体易发散）
    "reasoning_effort": "low",
}
QWEN_MODEL = CONFIG["llm_model"]
DASHSCOPE_API_URL = CONFIG["base_url"]
REASONING_EFFORT = CONFIG.get("reasoning_effort")

# ============================================================
# 实体类型 schema（锁定，与 图谱设计.md 1.1 一致）
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
# 关系类型 schema（锁定 21 类，与 图谱设计.md 2.1 一致）
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

# ============================================================
# 角色别名归一表（alias → canonical），与 图谱设计.md 1.3 一致
# 抽取后对 actor / violation / penalty / law 字段都做归一，
# 避免"商户 / 卖家 / 店铺 / 主播"在图谱里成 6 个不同节点
# ============================================================
CANONICAL: dict[str, str] = {
    # —— Platform ——
    "平台": "Platform", "美团": "Platform", "大众点评": "Platform",
    "我们": "Platform", "平台运营方": "Platform",
    # —— User ——
    "用户": "User", "消费者": "User", "买家": "User", "买方": "User",
    "乘客": "User", "旅客": "User", "房客": "User", "顾客": "User",
    "评审员": "User",
    # —— Merchant ——
    "商家": "Merchant", "商户": "Merchant", "卖家": "Merchant",
    "经营者": "Merchant", "店铺": "Merchant", "主播": "Merchant",
    "房东": "Merchant", "平台内经营者": "Merchant",
    # —— FulfillmentService ——
    "客服": "FulfillmentService", "骑手": "FulfillmentService",
    "司机": "FulfillmentService", "配送员": "FulfillmentService",
    "代运营": "FulfillmentService", "配送司机": "FulfillmentService",
    "驾驶员": "FulfillmentService", "第三方服务商": "FulfillmentService",
    # —— ExternalOrg ——
    "第三方": "ExternalOrg", "合作商": "ExternalOrg",
    "关联公司": "ExternalOrg", "供应商": "ExternalOrg",
    "支付机构": "ExternalOrg", "物流": "ExternalOrg",
    "监管部门": "ExternalOrg", "保险公司": "ExternalOrg",
    "关联公司": "ExternalOrg",  # 幂等
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


def normalize_alias(text: str) -> str:
    """对 actor / violation / penalty 名称做 alias→canonical 归一
    优先匹配 CANONICAL 表（最长前缀匹配），未命中保留原文。
    """
    t = (text or "").strip()
    if not t:
        return t
    # 优先查最长的别名（"平台内经营者" 优先于 "平台"）
    for alias in sorted(CANONICAL.keys(), key=len, reverse=True):
        if t == alias or t.startswith(alias + "（") or t.startswith(alias + "("):
            return CANONICAL[alias]
    return t


def is_noise_object(text: str) -> bool:
    """appliesTo 等关系的尾实体是否噪声（"本规则"等自指）"""
    t = (text or "").strip()
    return t in NOISE_OBJECT or t.startswith("本规则") or t.startswith("本规范")


# ------------------------------------------------------------
# 直连 LLM 抽取器（并发分片，绕过 semantica 慢包装）
# ------------------------------------------------------------
class DirectLLMExtractor:
    """直连 OpenAI 兼容端点的 NER / 关系抽取器。

    semantica 的 NERExtractor/RelationExtractor 对长文本内部多次调用、
    无有效超时，整文件 4578 字会挂 10 分钟以上；这里直接 POST
    /chat/completions，短超时 + 有限重试 + 线程并发分片，可全部跑完。
    """

    direct = True

    def __init__(self, shard_len: int = 900, workers: int = 3,
                 timeout: float = 180.0, max_retries: int = 3):
        self.shard_len = shard_len
        self.workers = workers
        self.timeout = timeout
        self.max_retries = max_retries

    # ---------- 分片（按行打包，尽量不切断条款） ----------
    def _shards(self, text: str):
        """按行打包成彼此不重叠、合起来覆盖全文的连续区间。
        每个 shard 直接是 text 的精确切片（base=切片起点），
        因此 base + shard.find(name) 就等于实体在 text 中的绝对位置。
        边界对不齐的行行首（尽量落在条款边界），超长时截断点取行首。
        """
        starts = []
        cursor = 0
        for ln in text.split("\n"):
            l = ln.rstrip("\r")
            s = text.find(l, cursor)
            starts.append(s if s >= 0 else cursor)
            cursor = starts[-1] + len(l) + 1
        starts.append(len(text))
        shards, cur_start = [], 0
        for i in range(len(starts) - 1):
            if starts[i] - cur_start > self.shard_len:
                if starts[i] > cur_start:
                    shards.append((text[cur_start:starts[i]].rstrip("\r"), cur_start))
                cur_start = starts[i]
        if cur_start < len(text):
            shards.append((text[cur_start:].rstrip("\r"), cur_start))
        return shards

    # ---------- 单次 HTTP 调用（OpenAI 兼容） ----------
    def _post(self, content: str, system: str, max_tokens: Optional[int] = None,
              tag: str = "") -> str:
        """流式 LLM 调用（OpenAI 兼容，stream=True）。
        实时打印每个返回片段：思考(前缀 思考) / 正式输出(前缀 输出)，
        切换阶段时打标记。既能看到模型在动，也能区分两阶段。
        """
        import time as _t
        from openai import OpenAI
        client = getattr(self, "_client", None)
        if client is None:
            client = OpenAI(api_key=detect_api_key(),
                            base_url=DASHSCOPE_API_URL,
                            timeout=self.timeout + 30, max_retries=0)
            self._client = client
        msgs = ([{"role": "system", "content": system}] if system else []) + \
            [{"role": "user", "content": content}]
        t0 = _t.time()
        buf_r, buf_c = [], []
        last_kind = ""
        print(f"  ── {tag or 'llm'} start @0s ──", flush=True)
        last = None
        for attempt in range(self.max_retries):
            try:
                kwargs = dict(model=QWEN_MODEL, messages=msgs,
                              temperature=0.1, stream=True)
                # 思考模式：None=开启（不传该参数，走模型默认）/"low|medium"/"none"=关闭
                if REASONING_EFFORT:
                    kwargs["reasoning_effort"] = REASONING_EFFORT
                # max_tokens：思考+输出共用预算，给足空间避免思考耗尽
                kwargs["max_tokens"] = max_tokens or 32768
                # 注意：buf_r/buf_c 在重试间清空，避免污染统计
                buf_r, buf_c = [], []
                last_kind = ""
                line_buf = ""   # 输出按整行整理打印，避免内容挤成一坨
                stream = client.chat.completions.create(**kwargs)
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    d = chunk.choices[0].delta
                    rc = getattr(d, "reasoning_content", None) or ""
                    cc = getattr(d, "content", None) or ""
                    if rc:
                        buf_r.append(rc)
                    if cc:
                        buf_c.append(cc)
                    kind = "思考" if rc else ("输出" if cc else "")
                    if kind and kind != last_kind:
                        print(f"\n  [{tag or 'llm'}] {kind} "
                              f"@{_t.time()-t0:.0f}s:", flush=True)
                        last_kind = kind
                    # 思考：流式限长打印（防刷屏）
                    if rc:
                        print(rc, end="", flush=True)
                        if sum(len(x) for x in buf_r) > 1500:
                            print("…", flush=True)
                    # 输出：按行整理打印，每行一条（保留 tab 分隔）
                    if cc:
                        line_buf += cc
                        while "\n" in line_buf:
                            ln, line_buf = line_buf.split("\n", 1)
                            ln = ln.strip()
                            if ln:
                                print(f"  · {ln}", flush=True)
                if line_buf.strip():
                    print(f"  · {line_buf.strip()}", flush=True)
                print(f"\n  ✓ [{tag or 'llm'}] 流结束 "
                      f"思考{sum(len(x) for x in buf_r)}字 "
                      f"输出{sum(len(x) for x in buf_c)}字", flush=True)
                full = "".join(buf_c).strip()
                if full:
                    return full
                print(f"    ⚠️ 空响应（仅思考未输出），第{attempt+1}次重试", flush=True)
                last = RuntimeError("empty response (reasoning only)")
                _t.sleep(2 * (attempt + 1))
            except Exception as e:
                last = e
                print(f"    ⚠️ LLM 调用失败(第{attempt+1}次): "
                      f"{type(e).__name__}: {str(e)[:140]}", flush=True)
                buf_r, buf_c = [], []
                _t.sleep(2 * (attempt + 1))
        raise RuntimeError(f"LLM 调用重试{self.max_retries}次仍失败: {last}")

    @staticmethod
    def _parse_array(s: str):
        s = re.sub(r"^```[A-Za-z]*\s*", "", (s or "").strip())
        s = s.rstrip("`").strip()
        try:
            return json.loads(s)
        except Exception:
            m = re.search(r"\[.*\]", s, re.S)
            if not m:
                return []
            try:
                return json.loads(m.group(0))
            except Exception:
                return [json.loads(x + "}") for x in re.findall(r"\{[^{}]*}", m.group(0))] or []

    # ---------- NER ----------
    def extract(self, text: str) -> list:
        """并发分片 NER → list[_Ent]（纯文本行格式 + 严苛约束 + 后置过滤）"""
        from concurrent.futures import ThreadPoolExecutor
        sys_msg = ("你是中国本地生活平台规则文本抽取助手。"
                   "严格要求：只抽取『原文中逐字出现』的具体名词短语作为实体姓名，"
                   "实体名必须一字不差地是原文里的连续字符，禁止凭空编造、禁止总结概括、"
                   "禁止只取原文中的一个字、禁止抽取单个动词（如：禁止/要求/给予/删除）。"
                   "用纯文本行输出，每行：实体名<制表符>英文类型。不要JSON、不要解释。")
        shards = self._shards(text)

        def one(shard, base):
            out = []
            try:
                txt = self._post(
                    "从下文中抽取实体，每行一个，格式：实体名<制表符>英文类型。\n"
                    "实体名必须是原文中逐字出现的连续片段（≥2字），可以是专有名词或行业短语；\n"
                    "禁止编造原文中没有的词、禁止只抽单个汉字、禁止把动词当实体。\n"
                    "英文类型只能取: " + ", ".join(ENTITY_TYPES) +
                    "\n\n文本：\n" + shard, sys_msg, tag=f"NER shard@{base}")
                for ln in (txt or "").splitlines():
                    ln = ln.strip()
                    if not ln:
                        continue
                    if "\t" in ln:
                        name, _, label = ln.partition("\t")
                    elif "|" in ln:
                        name, _, label = ln.partition("|")
                    else:
                        continue
                    name = name.strip()
                    label = (label or "").strip()
                    if label not in ENTITY_TYPES or not name:
                        continue
                    pos = shard.find(name)
                    # 后置过滤：非原文子串 / 单字 直接丢弃
                    if pos < 0 or len(name) < 2:
                        continue
                    start = base + pos
                    ent = _Ent(name, label, 1.0, start, start + len(name))
                    ent.metadata["shard"] = base
                    out.append(ent)
            except Exception as e:
                print(f"    ⚠️ NER 分片失败: {str(e)[:120]}", flush=True)
            return out

        all_e = []
        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as ex:
            futs = [ex.submit(one, sh, base) for sh, base in shards]
            for f in futs:
                all_e.extend(f.result())
        return all_e

    # ---------- 关系抽取 ----------
    def extract_relations(self, text: str, entities: list) -> list:
        """并发分片关系抽取 → list[_Rel]。纯文本行格式: 主语<tab>谓词<tab>宾语"""
        from concurrent.futures import ThreadPoolExecutor
        sys_msg = ("你是中国本地生活平台规则文本抽取助手。"
                   "严格要求：关系的主语、宾语必须是本文档中真实存在的实体（来自给定名单或原文），"
                   "严禁编造实体名、严禁拼接缩写，谓词必须是给定的关系类型之一。"
                   "用纯文本行输出：主语<制表符>关系类型<制表符>宾语。不要JSON、不要解释。")
        shards = self._shards(text)
        name_set = {e.text for e in entities}

        def one(shard, base, ents_in_shard):
            names = [e.text for e in ents_in_shard]
            prompt = ("从下文中抽取实体间关系，每行一个三元组，格式：主语<制表符>关系类型<制表符>宾语。\n"
                      "主语和宾语必须一字不差使用名单里的表态（或原文写法），禁止编造：\n"
                      "关系类型只能取: " + ", ".join(RELATION_TYPES) + "\n"
                      "候选实体名单：\n"
                      + ("、".join(names) if names else "（无预设备选，按原文抽取）")
                      + "\n\n文本：\n" + shard)
            try:
                txt = self._post(prompt, sys_msg, tag=f"REL shard@{base}")
            except Exception as e:
                print(f"    ⚠️ 关系分片失败: {str(e)[:120]}", flush=True)
                return []
            out = []
            for ln in (txt or "").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                parts = re.split(r"[\t|]", ln)
                if len(parts) < 3:
                    continue
                s, p, o = parts[0].strip(), parts[1].strip(), parts[2].strip()
                if p not in RELATION_TYPES or not s or not o:
                    continue
                # 后置过滤：主语/宾语必须是原文本子串，且（要么在候选名单要么原文出现）
                if len(s) < 2 or len(o) < 2:
                    continue
                if s not in text or o not in text:
                    continue
                if (s not in name_set and s not in shard) or (o not in name_set and o not in shard):
                    continue
                out.append(_Rel(s, p, o, 1.0))
            return out

        all_r = []
        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as ex:
            futs = []
            for sh, base in shards:
                ents_in_shard = [e for e in entities
                                 if base <= getattr(e, "start_char", -1) < base + len(sh)]
                futs.append(ex.submit(one, sh, base, ents_in_shard))
            for f in futs:
                all_r.extend(f.result())
        return all_r


# ------------------------------------------------------------
# 单文件处理
# ------------------------------------------------------------
class _Ent:
    """轻量实体（demo 用，字段兼容 semantica 抽取结果）"""
    __slots__ = ("text", "label", "confidence", "start_char", "end_char", "metadata")
    def __init__(self, text, label, confidence=1.0, start=0, end=0):
        self.text = text; self.label = label; self.confidence = confidence
        self.start_char = start; self.end_char = end
        self.metadata = {}


class _Rel:
    """轻量关系（demo 用）"""
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


def process_file(path: Path, ner, rel, splitter) -> dict:
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

    # 2. SPLIT（实体感知优先，递归回退；分块 NER 用本地 pattern，不额外调 LLM）
    chunks = splitter.split(text)
    print(f"  分块: {len(chunks)} 块 (size~{splitter.chunk_size}, overlap~{splitter.chunk_overlap})")

    # 3. EXTRACT（LLM NER + LLM 关系）；parse-only 模式跳过
    all_entities, all_relations = [], []
    import time as _t
    if ner is not None:
        if getattr(ner, "direct", False):
            print(f"  直连并发分片抽取 (shard~{getattr(ner,'shard_len',1600)}, "
                  f"workers={getattr(ner,'workers',2)}) ...", flush=True)
            t0 = _t.time()
            all_entities = ner.extract(text)
            ner_dt = _t.time() - t0
            for e in all_entities:
                if e.metadata is None:
                    e.metadata = {}
                e.metadata["chunk_id"] = 0
            t1 = _t.time()
            all_relations = ner.extract_relations(text, all_entities)
            rel_dt = _t.time() - t1
            print(f"  直连抽取完成: {len(all_entities)} 实体 / {len(all_relations)} 关系  "
                  f"(NER {ner_dt:.1f}s, Rel {rel_dt:.1f}s)", flush=True)
        else:
            for i, ch in enumerate(chunks):
                print(f"  >> 块{i} 调 LLM NER (chars={len(ch.text)}) ...", flush=True)
                t0 = _t.time()
                try:
                    ents = ner.extract(ch.text)
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
                    rels = rel.extract(ch.text, entities=ents)
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

    # 4. 去重（便于展示）
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


def extract_structure(text: str, entities: list, rels: list) -> tuple:
    """段落结构抽取（纯规则，不调 LLM）：
    识别文档大纲层级：章/节/条（含【标题】），生成两类产物——
      1) 层级实体：章节点 / 节节点 / 条目标题（如"第一章 概述""第一条【适用范围】"）
      2) partOf 关系：条目标题 → 其所属节/章；节 → 所属章；
                      节条内的实体内容 → 所属层（用覆盖范围把实体挂到最近的标题下）
    在 process_file 末尾调用，把结构实体合并进 all_entities，
    把 partOf 关系合并进 all_relations。
    """
    cn_num = "一二三四五六七八九十百"
    # —— 确定每个"标题行"的切片范围 ——
    # 行 = (line_text, start, end)
    lines = []
    pos = 0
    for raw_ln in text.split("\n"):
        ln = raw_ln.rstrip("\r")
        s = text.find(ln, pos)
        if s < 0:
            s = pos
        lines.append((ln, s, s + len(ln)))
        pos = s + len(ln) + 1

    # 识别章节标题行：第X章 / 第X节 / 第N条(可带【标题】)
    # 兼容 markdown：剥掉开头 #~###### 与 ** 粗体标记后匹配
    def md_clean(ln: str) -> str:
        t = ln.strip()
        t = re.sub(r"^#{1,6}\s*", "", t)
        t = t.replace("**", "").strip()
        return t

    def parse_level(ln: str):
        t = md_clean(ln)
        m = re.match(rf"^第([{cn_num}]+)章", t)
        if m:
            rest = t[len(m.group(0)):].strip()
            return 1, f"第{m.group(1)}章" + (f" {rest}" if rest else "")
        m = re.match(rf"^第([{cn_num}]+)节", t)
        if m:
            rest = t[len(m.group(0)):].strip()
            return 2, f"第{m.group(1)}节" + (f" {rest}" if rest else "")
        m = re.match(r"^第([一二三四五六七八九十百]+)条", t)
        if m:
            b = re.search(r"【([^】]+)】", t)
            base = f"第{m.group(1)}条"
            return 3, base + (f"【{b.group(1)}】" if b else "")
        # 协议类：中文数字大标题（"一、押金的支付"）→ level 2
        m = re.match(r"^([一二三四五六七八九十]+)、", t)
        if m:
            rest = t[len(m.group(0)):].strip()
            return 2, f"{m.group(1)}、{rest}" if rest else f"{m.group(1)}、"
        # 协议类：数字小节（"1.1 xxx"）→ level 3（整行较短且不含多个句号，避免正文行误判）
        m = re.match(r"^(\d+\.\d+)\b(.+)$", t)
        if m and len(m.group(2)) <= 80 and m.group(2).count("。") <= 1:
            return 3, m.group(1)
        return None, None

    heads = []
    for ln, s, e in lines:
        lvl, nm = parse_level(ln)
        if lvl is not None:
            heads.append((lvl, nm, s, e))

    # —— 构建层级树，生成 partOf ——
    # 按 start 排序；维护当前 栈(章→节→条)，把正文里出现的实体挂到最近的条/节/章
    heads_sorted = sorted(heads, key=lambda x: x[2])
    stack = []  # (lvl, name)
    partof = []  # (subject_text, object_text)
    struct_ents = []
    seen_struct = set()

    def add_ent(nm, label, s, e):
        if nm and nm not in seen_struct:
            seen_struct.add(nm)
            ent = _Ent(nm, label, 1.0, s, e)
            ent.metadata["structure"] = True
            struct_ents.append(ent)
        return nm

    # 为标题覆盖范围建索引（用二分或用当前循环）
    ent_sorted = sorted(entities, key=lambda x: getattr(x, "start_char", 0) or 0)
    idx = 0
    # 遍历每个标题，把"落在其范围下一条标题前"的实体归属给它
    for i, (lvl, nm, s, e) in enumerate(heads_sorted):
        end = heads_sorted[i + 1][2] if i + 1 < len(heads_sorted) else len(text)
        # 标题本体做实体（章/节/条）
        add_ent(nm, "RuleDocument", s, e)
        # 条/节 → 上级 partOf
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        if stack:
            parent = stack[-1][1]
            partof.append((nm, parent))
        stack.append((lvl, nm))
        # 正文实体（在此标题与下一标题之间）→ partOf 到本标题
        while idx < len(ent_sorted) and (getattr(ent_sorted[idx], "start_char", 0) or 0) < end:
            en = ent_sorted[idx]
            st = getattr(en, "start_char", -1) or -1
            if st >= s and en.text not in seen_struct:
                partof.append((en.text, nm))
            idx += 1
    # 打包成 _Rel（partOf）并去重
    seen_p, rels = set(), []
    for s_text, o_text in partof:
        k = (s_text, o_text)
        if k not in seen_p:
            seen_p.add(k)
            rels.append(_Rel(s_text, "partOf", o_text, 1.0))
    return struct_ents, rels


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


def dump_result(meta: dict, out_dir: Path, run_info: dict) -> dict:
    """保存单文件 JSON，返回可 JSON 序列化的结构体"""
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
_EMBED_CACHE: dict = {}


def get_embedder():
    """加载本地 bge-m3（模块级缓存，供向量化与段索引复用）"""
    if "_m" not in _EMBED_CACHE:
        from sentence_transformers import SentenceTransformer
        model_path = str(EMBEDDING_MODEL_PATH) if EMBEDDING_MODEL_PATH.exists() else "BAAI/bge-small-zh-v1.5"
        print(f"     模型: {model_path}", flush=True)
        _EMBED_CACHE["_m"] = SentenceTransformer(model_path, device="cpu")
    return _EMBED_CACHE["_m"]


def split_to_segments(text: str, max_len: int = 300, overlap: int = 50) -> list[str]:
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


def build_segment_store(all_meta, out_dir: Path):
    """把每份文件的原文整段嵌入进独立 FAISS 库。
    与实体库互补：实体库定位"命名实体"，段库召回"任意原文片段"。
    返回 (SimpleStore, corpus, dim)，corpus[i] = {text(段落), source_file, seg_id}
    """
    corpus = []
    for meta in all_meta:
        src = meta["file"]
        segs = split_to_segments(meta.get("text", ""))
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
# 原文覆盖率报告：每段是否被实体/SPO 覆盖
# ------------------------------------------------------------
# 原文覆盖率报告：每段是否被实体/SPO 覆盖
# ------------------------------------------------------------
def coverage_report(meta):
    """对单文件做覆盖率分析：按段（换行/章节）切，每段统计实体出现数。
    同时给出"低覆盖"段（实体数 0-1 的）。
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


def main():
    ap = argparse.ArgumentParser(description="LLM 模式批量解析美团规则 txt")
    ap.add_argument("dir", nargs="?", default=str(DATA / "rule_demo"),
                    help="包含 .txt/.md 的目录（默认 rule_demo，批量可指 rule_txt）")
    ap.add_argument("--parse-only", action="store_true",
                    help="只跑 解析+分块，不调 LLM（自检用）")
    ap.add_argument("--demo", action="store_true",
                    help="demo 模式：不调 LLM，用内置样例演示 向量化+图谱+检索 全流程")
    args = ap.parse_args()

    parse_only = args.parse_only
    print_header("15 - LLM 模式批量解析（美团业务规则 → 知识图谱）" if not parse_only
                 else "15 - 解析管道自检（parse-only，不调 LLM）")

    src_dir = Path(args.dir)
    if not src_dir.is_absolute():
        src_dir = ROOT / src_dir
    files = sorted(p for p in src_dir.iterdir()
                   if p.suffix.lower() in (".txt", ".md"))
    if not files and not args.demo:
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
        out_dir = out_dir / (f"run_{stamp}_demo" if args.demo else f"run_{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    run_info = {
        "source": "15_rule_demo_llm提取.py",
        "run_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user_args": vars(args),
        "llm": {"model": QWEN_MODEL, "base_url": DASHSCOPE_API_URL,
                "reasoning_effort": REASONING_EFFORT},
    }

    key = None if (parse_only or args.demo) else detect_api_key()

    # ---- 初始化提取器（LLM 模式，provider="openai" 走 OpenAI 兼容通道） ----
    ner = rel = None
    from semantica.split import TextSplitter

    if parse_only or args.demo:
        splitter = None
        if parse_only:
            from semantica.split import TextSplitter
            splitter = TextSplitter(
                method=["entity_aware", "recursive"],
                chunk_size=1200, chunk_overlap=200, ner_method="pattern",
            )
    else:
        from semantica.split import TextSplitter
        print(f"\n  ✅ LLM: {QWEN_MODEL} @ {DASHSCOPE_API_URL}")
        print(f"  实体类型约束: {ENTITY_TYPES}")
        print(f"  关系类型约束: {RELATION_TYPES}")
        ner = rel = DirectLLMExtractor(shard_len=1000, workers=1, timeout=900,
                                       max_retries=3)
        splitter = TextSplitter(
            method=["entity_aware", "recursive"],
            chunk_size=8000, chunk_overlap=400,
            ner_method="pattern",
        )

    # ---- 逐文件处理 ----
    all_results = []
    all_meta = []   # 包含每文件 chars/num_chunks/text 统计
    demo_only = getattr(args, "demo", False)
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
            meta = process_file(fp, ner, rel, splitter)
        except Exception as e:
            print(f"\n❌ 处理失败 {fp.name}: {type(e).__name__}: {e}")
            continue
        all_meta.append(meta)
        if not parse_only:
            # 抽取后：alias 归一 + 噪声过滤 + 合并
            meta["entities"], meta["relations"] = normalize_result(
                meta["raw_entities"], meta["raw_relations"], meta["text"]
            )
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
                "消费者可以怎么申诉",
            ], k=2, title="② 全文段级向量检索（覆盖剩余正文）")

        # ---- 图构建 + 图检索 demo ----
        G = build_graph(all_meta, out_dir) if (store is not None) else None
        if G is not None:
            graph_queries = [
                {"mode": "outgoing",   "query": "商户直播穿着",   "hops": 1, "seed_k": 3, "seed_thr": 0.0},
                {"mode": "incoming",   "query": "下架违规直播",   "seed_k": 3, "seed_thr": 0.0},
                {"mode": "neighbors",  "query": "商户直播行为规范", "hops": 1, "seed_k": 2, "seed_thr": 0.0},
                {"mode": "outgoing",   "query": "商户",          "hops": 1, "seed_k": 3, "seed_thr": 0.0},
            ]
            graph_search_demo(G, store, graph_queries)

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