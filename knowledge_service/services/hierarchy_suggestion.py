"""候选类层级的 LLM 建议（P0-3）：**只产出建议，绝不自动改本体**。

## 为什么需要它

开放发现（`SemanticaExtractor.discover()`）只抽实体/关系/属性，**不包含父子层级**；
而 Semantica 自己的层级推断只看类名（通用父类名单 Entity/Thing/Resource + 英文词前缀），
我们的机器名是哈希串（`EntityType_5b0d46251aa1`）——实测它一个父类都推断不出来。
结果就是：发现出来的本体**永远是平的**，"查父类能命中子类实体"这类层级语义无从谈起。

所以这里补一次 LLM 判断：给它候选类清单，让它回答"哪些类是哪些类的父类"。

## 边界（和"扩展只进审核、不自动改本体"一致）

* 只在**生成发现草案**时跑一次，结果存进草案 provenance（`hierarchy_suggestions`）；
* 建议被写进生成的 Turtle，走**既有**的 `add_parent` 操作 → 审核 → 发布，没有旁路，
  也不会绕过发布门禁；
* LLM 没配置、超时、返回垃圾 —— 一律**降级为空建议**（发现照常完成），
  因为"层级"是增强而不是正确性前提；
* LLM 输出是**不可信输入**：只认清单里的类名、去掉自环、去掉会成环的边、
  限制宽度与总量，并按确定性顺序输出（同一份输入两次跑出同样的结构）。
"""
import json
import logging
import os
import re
from copy import deepcopy

from ..core.net import external_client

LOG = logging.getLogger('knowledge_service.hierarchy')

# 提示词版本：改了提示词就升它，便于把"某次建议质量变差"归因到提示词而不是模型。
PROMPT_VERSION = 'hierarchy-suggestion/1'
MAX_PARENTS_PER_CLASS = 3
MAX_EDGES = 200

SYSTEM_PROMPT = (
    '你是本体工程师。只依据给出的候选类清单判断它们之间的父子（is-a）关系。'
    '规则：① 只能使用清单里出现过的类名，不要新造类；② 子类比父类更具体'
    '（例如"签约主播"的父类是"合作方"）；③ 没有可靠依据时宁可留空；'
    '④ 只输出 JSON，形如 {"子类": ["父类"]}，不要解释、不要 Markdown 代码块。'
)


class HierarchySuggestionsUnavailable(RuntimeError):
    """LLM 通道未配置（缺 ``KG_LLM_*``）。

    调用方据此**降级**（本次发现按"无层级"处理），而不是把发现流程搞挂。
    """


# 进程内缓存：同一批候选类名（且同一草案名、同一提示词版本）只问一次 LLM。
# 为什么需要：生成草案会被重复触发（同一 fingerprint 会复用已有 run，但结果要先算出来），
# 没有缓存就会为同一次发现白花一次外部调用。键里带上类名集合与草案名，所以
# 候选变了/草案名变了自然换键，不会拿到过期建议。
_SUGGESTION_CACHE = {}
_CACHE_LIMIT = 64


def clear_cache():
    """清空建议缓存（仅测试需要；服务运行期不必手动清）。"""
    _SUGGESTION_CACHE.clear()


def _llm_settings():
    url = os.environ.get('KG_LLM_BASE_URL', '').strip().rstrip('/')
    key = os.environ.get('KG_LLM_API_KEY', '').strip()
    model = os.environ.get('KG_LLM_MODEL', '').strip()
    if not all((url, key, model)):
        raise HierarchySuggestionsUnavailable(
            '未配置 KG_LLM_BASE_URL、KG_LLM_API_KEY、KG_LLM_MODEL，跳过层级建议')
    return url, key, model


def strip_code_fence(text):
    """模型常把 JSON 包在 ```json 里；这里只剥外壳，不做"聪明"的修补。"""
    if not isinstance(text, str):
        return ''
    fence = re.match(r'\s*```(?:json)?\s*(.*?)\s*```\s*$', text, flags=re.S)
    return (fence.group(1) if fence else text).strip()


def parse_suggestions(payload, class_names):
    """把**不可信**的 LLM 输出规整成可用的建议字典。

    输入 ``payload`` 可以是 JSON 字符串、已解析的 dict，或模型返回的 list 包装。
    输出始终是 ``{父类名: [...]}`` 形态的规范化结果：

    * 只认清单里的类名（大小写/空白差异一律归一到清单里的写法）；
    * 丢掉自环、重复边，每个子类最多 ``MAX_PARENTS_PER_CLASS`` 个父类；
    * **丢掉会构成环的边** —— 环会让本体自相矛盾（`A ⊑ B ⊑ A`）；
    * 边的总数上限 ``MAX_EDGES``，并按"子类名 → 父类名"确定性排序。
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(strip_code_fence(payload) or '{}')
        except (ValueError, TypeError):
            LOG.warning('层级建议不是合法 JSON，按空建议处理')
            return {}
    if isinstance(payload, list):
        # 有些模型会返回 [{"子类": [...]}, ...]；合并成一份。
        merged = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            for child, parents in item.items():
                merged.setdefault(child, [])
                if isinstance(parents, list):
                    merged[child].extend(parents)
                elif isinstance(parents, str):
                    merged[child].append(parents)
        payload = merged
    if not isinstance(payload, dict):
        LOG.warning('层级建议的形状不是对象，按空建议处理')
        return {}

    def canonical(value):
        """把类名对齐到清单里的写法（忽略大小写与首尾空白）。"""
        if not isinstance(value, str):
            return None
        key = value.strip().casefold()
        return lookup.get(key)

    lookup = {}
    for name in class_names:
        if isinstance(name, str) and name.strip():
            lookup.setdefault(name.strip().casefold(), name.strip())

    # 先按"子类名"确定性排序收集候选边，再逐条做环检测。
    candidates = []
    for child in sorted(payload, key=lambda item: str(item)):
        child_name = canonical(child)
        parents = payload[child]
        if isinstance(parents, str) or parents is None:
            parents = [parents]
        if not isinstance(parents, list):
            continue
        for parent in parents:
            parent_name = canonical(parent)
            if not child_name or not parent_name or parent_name == child_name:
                continue
            candidates.append((child_name, parent_name))

    accepted = {}          # 子类 -> [父类]
    edges = []             # 已接受的 (子类, 父类)
    total = 0
    for child_name, parent_name in candidates:
        if len(accepted.get(child_name, ())) >= MAX_PARENTS_PER_CLASS:
            continue
        if total >= MAX_EDGES:
            break
        if (child_name, parent_name) in edges:
            continue      # 重复的父类只记一次
        if (parent_name, child_name) in edges:
            continue
        if _creates_cycle(child_name, parent_name, edges):
            LOG.info('层级建议丢弃成环的边：%s ⊑ %s', child_name, parent_name)
            continue
        accepted.setdefault(child_name, []).append(parent_name)
        edges.append((child_name, parent_name))
        total += 1
    return {child: sorted(parents) for child, parents in sorted(accepted.items())}


def _creates_cycle(child, parent, edges):
    """把 ``child ⊑ parent`` 加进去会不会成环（沿已有的"子→父"边走一遍）。"""
    stack, seen = [parent], set()
    while stack:
        current = stack.pop()
        if current == child:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(target for source, target in edges if source == current)
    return False


def build_prompt(class_names, *, examples=(), request_name=''):
    """构造"用户消息"载荷（中文，JSON 以便模型结构化输出）。"""
    return json.dumps({
        '任务': '判断候选类之间的父子关系',
        '候选类清单': sorted({name for name in class_names if isinstance(name, str) and name.strip()}),
        '术语示例': [str(item) for item in examples][:20],
        '草案名': request_name or '',
        '输出要求': '只输出 JSON：{"子类": ["父类"]}；没有把握的类不要出现在结果里；没有任何层级关系就输出 {}',
    }, ensure_ascii=False)


def suggest_hierarchy(class_names, *, examples=(), request_name='', timeout=120):
    """调一次 LLM 求层级建议；未配置 LLM 时抛 :class:`HierarchySuggestionsUnavailable`。

    返回规范化后的 ``{子类: [父类]}``（可能为空 dict）。网络/格式错误不在这里吞掉：
    调用方（发现流程）负责降级并记日志，这样"为什么这次没有层级"能在服务日志里看到原因。

    同一批候选类 + 同一草案名 + 同一提示词版本会命中**进程内缓存**（见 `_SUGGESTION_CACHE`），
    返回的是副本 —— 调用方改坏返回值不会污染后续调用。
    """
    url, key, model = _llm_settings()
    names = sorted({name for name in class_names if isinstance(name, str) and name.strip()})
    cache_key = (PROMPT_VERSION, request_name or '', tuple(names))
    if cache_key in _SUGGESTION_CACHE:
        LOG.info('层级建议命中缓存：候选类 %d 个', len(names))
        return deepcopy(_SUGGESTION_CACHE[cache_key])
    with external_client(timeout) as client:
        response = client.post(url + '/chat/completions',
                               headers={'Authorization': f'Bearer {key}'}, json={
            'model': model,
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': build_prompt(
                    class_names, examples=examples, request_name=request_name)},
            ],
            'temperature': 0,
        })
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content']
    suggestions = parse_suggestions(content, class_names)
    if len(_SUGGESTION_CACHE) >= _CACHE_LIMIT:
        # 简单粗暴地上限控制：只缓存"最近一批"，避免长期运行后无界增长。
        _SUGGESTION_CACHE.clear()
    _SUGGESTION_CACHE[cache_key] = deepcopy(suggestions)
    LOG.info('层级建议完成：提示词 %s，候选类 %d 个，得到 %d 条父子边',
             PROMPT_VERSION, len(names), sum(len(v) for v in suggestions.values()))
    return suggestions
