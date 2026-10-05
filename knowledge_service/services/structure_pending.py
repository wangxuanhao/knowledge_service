"""结构待定（C1）：知识里出现了本体还没有的概念时，知识不丢、也不硬塞进本体。

背景（完整落地规划 §4.1「连接点①：待建模收件箱 + 结构待定」）
------------------------------------------------------------------
文档里抽出来的概念，本体里可能还没有。走错的两条路都出现过：**硬塞进本体**
（本体被业务噪声污染，后来没人敢删）和 **干脆丢掉**（知识消失，且没有地方能看到
"丢过什么"）。这里走第三条：知识照常进来，但**标出来** —— 它叫「结构待定」。

一、什么算结构待定（判据唯一，只来自服务端）
------------------------------------------------
一条知识的类型（实体→类的 IRI / 关系→关系类型 / 属性→属性）在**当前本体**里
``resolve`` 不出来（抛 ``UnknownOntologyTerm``），这条知识就是结构待定。
判据只看**当前本体**，不看"它当初挂的那一版" —— 因为待定的反面是"概念已经建模好了"：
概念一旦进入（发布新版）本体，状态就必须自己解除；若按旧版本判，知识会永远卡在待定，
只能靠人工清标记，那就又变成两个事实来源了。（"这条知识还挂在旧版本体上"是另一件事，
台账里由「本体归属（当前/旧版）」那一列说，迁移是 C2「受控重分类」的活。）
记录上留一枚 marker（``metadata.structure_pending``）说明"当初缺的是哪些术语"，
但**有效状态是推导出来的**：那些术语后来进了本体，状态自动变成「已解除」。
所以本模块**不提供"手动清除标记"入口** —— 两个事实来源必然打架。

端点传染：一条关系的端点实体若是结构待定，这条关系**也是**结构待定（它的 domain/range
根本没法校验），状态里用 ``via`` 说明"因为端点 X 的概念还没有"，端点解除时一起解除。

二、它算什么、不算什么（口径写死在这里，别处不要各写一套）
------------------------------------------------------------
    算：可检索、可看原文、可被引用、出现在实体视图里
    不算：不进问答的正式证据链（问答里**显式标注**并单列）、不计入本体覆盖率、
          不作为约束校验的输入（``service.write`` 里会把它们剔出校验集）
覆盖率：全库目前没有任何覆盖率指标（grep 0 命中），所以这一条现在**没有对象可排除**；
等覆盖率出现时，按 :func:`is_pending` 排除即符合本口径。

三、写入侧谁负责打标
--------------------
``service.write``（人工写入 / 导入 / 抽取）与发现候选正式化：遇到"类型在本体里查不到"
的**只**这一种失败时，不再整批拒绝，而是收下并打标；其它失败（约束、端点、datatype）
照旧拒绝 —— 标错了会把真问题盖掉。
"""
from __future__ import annotations

#: 打在同一条记录 ``metadata`` 里的标记键。留它是为了"当初缺什么"可追溯，
#: 判断"现在还算不算待定"必须走 :func:`state` 推导，不允许直接读它下结论。
MARKER_KEY = 'structure_pending'

#: 会带着本体术语进来的记录种类（文档/切片没有类型，不参与结构待定）。
KNOWLEDGE_KINDS = ('entity', 'relation', 'attribute')

#: 缺术语的原因文案（页面直接用，不要各处重写）。
REASON_UNKNOWN_TERM = '本体里还没有这个概念'

#: 端点传染的原因文案。
REASON_ENDPOINT_TERM = '端点实体挂的概念本体里还没有'

KIND_LABELS = {'class': '类', 'relation': '关系', 'attribute': '属性', None: '术语'}


def _allowed(ontology, kind):
    """按记录种类取本体里的合法术语集合；未知种类返回 None（不参与判断）。"""
    if kind == 'entity':
        return ontology.classes
    if kind == 'relation':
        return ontology.relations
    if kind == 'attribute':
        return ontology.attributes
    return None


def term_kind_of(ontology, term, kind=None):
    """这个术语在本体里是类 / 关系 / 属性 / 未收录（None）。

    ``kind`` 给了就只在那一类里找（实体的类型只可能是类），避免"类名和关系名重名"时误判。
    """
    for candidate, allowed in (('class', ontology.classes),
                               ('relation', ontology.relations),
                               ('attribute', ontology.attributes)):
        if kind and candidate != kind:
            continue
        try:
            ontology.resolve(term, allowed)
        except Exception:
            continue
        return candidate
    return None


def resolves(ontology, term, kind):
    """术语在给定种类里能否 resolve。空术语一律"未收录"（实体没类型就是待定，不是通过）。"""
    if not term or not isinstance(term, str):
        return False
    allowed = _allowed(ontology, kind)
    if allowed is None:
        return True
    try:
        ontology.resolve(term, allowed)
    except Exception:
        return False
    return True


def unknown_terms(ontology, record, kind=None):
    """这条知识**自己**的类型在它挂的本体里查不到时，返回缺失的术语（否则 []）。

    只看自己的类型：关系端点算不算待定由 :func:`state` 的传染规则决定，
    这样"缺的到底是哪个概念"在收件箱里能一句话说清。
    """
    kind = kind or record.get('kind')
    if kind not in KNOWLEDGE_KINDS:
        return []
    term = record.get('type')
    return [] if resolves(ontology, term, kind) else [term or '(未命名类型)']


def marked_terms(record):
    """记录上 marker 里记着的术语（历史事实：当初缺的是什么）。"""
    marker = (record.get('metadata') or {}).get(MARKER_KEY) or {}
    terms = marker.get('terms')
    if isinstance(terms, str):
        terms = [terms]
    return [str(term) for term in (terms or []) if str(term).strip()]


def mark(record, terms, reason=REASON_UNKNOWN_TERM, at=None):
    """把"缺这些术语"标记到记录上；重复打标只合并术语（保留最早的 reason / 时间）。"""
    terms = [str(term) for term in (terms or []) if str(term).strip()]
    if not terms:
        return None
    from ..core.time import utc_now

    metadata = record.setdefault('metadata', {})
    marker = metadata.get(MARKER_KEY)
    if not isinstance(marker, dict):
        marker = {'terms': [], 'reason': reason, 'marked_at': at or utc_now()}
    merged = list(dict.fromkeys([*(marker.get('terms') or []), *terms]))
    marker.update({'terms': merged})
    marker.setdefault('reason', reason)
    marker.setdefault('marked_at', at or utc_now())
    metadata[MARKER_KEY] = marker
    return marker


def state(ontology, record, endpoint_pending=()):
    """这条知识的「结构待定」有效状态；从未标记且类型正常 → None（页面不显示任何徽标）。

    返回 ``{'state','terms','marked','reason','marked_at','via'}``：
      * ``state`` —— ``pending``（现在仍待定）/ ``cleared``（本体补齐了，自动解除）
      * ``terms`` —— 现在还缺的术语（``cleared`` 时为 []）
      * ``marked`` —— 当初缺的术语（历史，清除不掉）
      * ``via`` —— 传染来源的端点记录 id 列表
    """
    marked = marked_terms(record)
    own = unknown_terms(ontology, record)
    via = [rid for rid in endpoint_pending or () if rid]
    if not marked and not own and not via:
        return None
    terms = list(dict.fromkeys([*own, *marked])) if own or via else []
    if own or via:
        reason = REASON_UNKNOWN_TERM if own else REASON_ENDPOINT_TERM
        return {'state': 'pending', 'terms': terms or marked, 'marked': marked,
                'reason': reason, 'marked_at': (record.get('metadata') or {}).get(
                    MARKER_KEY, {}).get('marked_at') if marked else None,
                'via': via}
    return {'state': 'cleared', 'terms': [], 'marked': marked,
            'reason': (record.get('metadata') or {}).get(MARKER_KEY, {}).get('reason'),
            'marked_at': (record.get('metadata') or {}).get(MARKER_KEY, {}).get('marked_at'),
            'via': []}


def annotate(ontology, records):
    """就地给记录集补 ``record['structure_pending']``（推导态）；返回待定条数。

    ``ontology`` 传**当前本体**（判据见模块头）。只对"打过标记"或"类型查不到"的记录做解析，
    正常记录零成本（绝大多数项目是这种情况）。
    """
    pending_ids = set()
    for record in records:
        if record.get('kind') in KNOWLEDGE_KINDS and unknown_terms(ontology, record):
            pending_ids.add(record['id'])
    pending = 0
    for record in records:
        was_marked = bool(marked_terms(record))
        if not was_marked and record['id'] not in pending_ids:
            record.pop('structure_pending', None)
            continue
        endpoint_keys = (('subject_id', 'object_id') if record.get('kind') == 'relation'
                         else ('subject_id',) if record.get('kind') == 'attribute' else ())
        via = [record.get(key) for key in endpoint_keys if record.get(key) in pending_ids]
        status = state(ontology, record, via)
        if status is None:
            record.pop('structure_pending', None)
            continue
        record['structure_pending'] = status
        if status['state'] == 'pending':
            pending += 1
    return pending


def is_pending(record):
    """记录（已 annotate）现在是不是结构待定。"""
    return (record.get('structure_pending') or {}).get('state') == 'pending'


def split(records):
    """把已 annotate 的记录集拆成（正式, 结构待定）两份，保持原顺序。"""
    formal = [record for record in records if not is_pending(record)]
    pending = [record for record in records if is_pending(record)]
    return formal, pending


def summary(current_ontology, records, project_id=None):
    """收件箱数据：现在还缺哪些概念、各被多少条知识用到、样例是什么。

    ``pending_structure`` 是**待建模的概念个数**（页面顶部那个数字），不是记录数 ——
    「3 个类型待定」比「17 条知识待定」更接近用户要做的动作：去本体里补 3 个概念。
    """
    annotate(current_ontology, records)
    pending_rows = [record for record in records if is_pending(record)]
    groups = {}
    for record in pending_rows:
        status = record['structure_pending']
        for term in status['terms'] or ['(未命名类型)']:
            group = groups.setdefault(term, {
                'term': term, 'kind': None, 'record_count': 0, 'records': [],
                'via_endpoint': 0,
            })
            group['record_count'] += 1
            if status.get('via'):
                group['via_endpoint'] += 1
            if len(group['records']) < 5:
                group['records'].append({'id': record['id'], 'kind': record.get('kind'),
                                         'text': record.get('text')})
    for group in groups.values():
        # kind 决定"该建成类还是关系还是属性"，页面写动作时要用。
        group['kind'] = term_kind_of(current_ontology, group['term'])
        group['kind_label'] = KIND_LABELS.get(group['kind'])
        group['suggested_action'] = (
            f'去「本体建模层」把这个{group["kind_label"] or "概念"}建出来；'
            '发布新版本体以后，用到它的知识会自动解除「结构待定」，不需要逐条改。')
    terms = sorted(groups.values(), key=lambda item: (-item['record_count'], item['term']))
    return {
        'pending_structure': len(terms),
        'pending_records': len(pending_rows),
        'terms': terms,
        'records': [{'id': record['id'], 'kind': record.get('kind'), 'text': record.get('text'),
                     'terms': record['structure_pending']['terms']} for record in pending_rows],
        'definitions': {
            'pending': '这条知识的类型在本体里查不到：已收下、能查能引用，但不作为正式证据',
            'cleared': '本体已经补齐这个概念，标记自动解除（不需要人工清理）',
        },
        'note': ('「结构待定」= 抽出来的概念还没进本体。这类知识不丢：能查、能看原文、能被引用；'
                 '但不进问答的正式证据链，也不参与约束校验。'),
        'project_id': project_id,
    }
