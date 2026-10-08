"""受控重分类（C2）：本体出了新版本之后，把**还挂在旧版本上的知识**迁到当前本体。

背景（完整落地规划 §4.2「连接点②：受控重分类」）
--------------------------------------------------
本体发布 v4 之后，v3 时期写进来的知识仍然挂着 v3（``record_versions.ontology_id``）。
以前这件事**没有任何界面能看见**，用户只能感觉到"检索/问答里的类型对不上了"。
台账（B1）先把事实露出来 —— 每一行都写「本体 v3 · 2026/2/1（旧版）」；这里接着做**动作**：
把它迁到当前版本。

一、三档处置（``plan()`` 的产出，也是页面上唯一的口径）
--------------------------------------------------------
按「旧本体版本 + 旧类型」分组，每组给一个处置：

    carry    旧类型在**当前本体**里仍然存在   → 只把 ontology_id 改到当前版本
    rename   旧类型不在当前本体里（或被停用）， → 改 type（新类型）+ ontology_id
             但写了 dcterms:isReplacedBy 替代映射
    unmapped 没有去处                          → **不迁移**：先去「本体建模层」把概念建出来
                                                 （这些知识本来就是 C1 的「结构待定」，
                                                  概念一进本体，状态自动解除）

「停用类时指定的替代类」与这里共用同一份数据（``dcterms:isReplacedBy``）——
规划明确要求不重复问用户，所以本模块只读映射，不产生映射。

二、三条硬约束（缺一条就会变成灾难，每条都有对应实现与测试）
------------------------------------------------------------
1. **默认不跑** —— :meth:`Reclassify.plan` / :meth:`preview` 全是只读；
   :meth:`apply` 必须显式交出"要迁哪几组"，没有"一键迁全部"的默认值。
2. **不原地改** —— 每条迁移都走 ``service.write`` 产生**新版本**（``record_versions`` 多一行，
   ``superseded_at`` 落在旧行上）；旧版本永远可查、可恢复。
3. **可回滚** —— 整批迁移写成**一条**审计操作（``audit:<op_id>`` + ``merge_operations`` 账本），
   台账的「可撤销的操作」里点一次就整批回退；回退会先检查这批记录在此期间有没有被
   别人改过，有则拒绝（绝不覆盖别人的编辑）。

三、诚实边界（页面文案与这里必须一致）
--------------------------------------
* ``unmapped`` 组**不会**被自动迁移，也不会偷偷改类型；页面上如实说"没有去处"。
* 迁移**不改知识的正文**（text / properties / valid_from…），只改它挂的本体与类型。
* 预演（:meth:`preview`）跑的是**与写入路径同一个函数**（``KnowledgeService._timeline_check``），
  所以"预演说没问题、真跑却被拒"这种撒谎不会发生；预演也**不会**写入任何东西。
"""
from __future__ import annotations

from rdflib.namespace import DCTERMS

from .governance import Governance, writable
from .ontology import Ontology
from .ontology_vocabulary import local_name
from . import structure_pending

#: 三档处置的名字与页面文案（放一处，页面直接用，不要各处重写）。
ACTIONS = {
    'carry': '原样搬到新版本',
    'rename': '按替代映射改类型',
    'unmapped': '没有去处',
}

#: 记录种类 → 本体术语种类（"类"只挂在实体上）。
TERM_KIND = {'entity': 'class', 'relation': 'relation', 'attribute': 'attribute'}

#: 每条记录在面板里展示原文的截断长度。
_TEXT_LIMIT = 60

#: 每组最多带几条记录明细（页面要"能看出是哪几条"，但不要给整库）。
_SAMPLE_LIMIT = 20


class ReclassifyError(ValueError):
    """受控重分类的拒绝（人话版，直接给用户看）。"""


def _truncate(text, limit=_TEXT_LIMIT):
    value = ' '.join(str(text or '').split())
    return value if len(value) <= limit else value[:limit] + '…'


def _label_map(ontology):
    """本体里每个术语 IRI → 人看得懂的名字（中文标签优先，退回本地名）。

    同时按**完整 IRI** 和**本地名**建索引：记录里的类型两种写法都存在
    （抽取管线写 IRI，人工/测试常写本地名），面板要两种都认得出。
    """
    labels = {}
    summary = ontology.summary()
    for kind in ('classes', 'relations', 'attributes'):
        for item in summary.get(kind, []):
            name = item.get('label_zh') or item.get('label') or item['name']
            labels[item['id']] = name
            labels.setdefault(item['name'], name)
    return labels


def _term_name(labels, term):
    if not term:
        return '(未命名类型)'
    return labels.get(str(term)) or local_name(term) or str(term)


def _replacement(ontology, term, kind):
    """这个术语的替代映射（``dcterms:isReplacedBy``）指向哪个 IRI；没有就 None。

    先 resolve 成本体里的 URIRef 再读图：记录里的类型可能是本地名（'包'），
    拿字符串直接查 rdflib 图永远查不到。
    """
    node = _term_node(ontology, term, kind)
    if node is None:
        return None
    for target in ontology.graph.objects(node, DCTERMS.isReplacedBy):
        return str(target)
    return None


def _term_node(ontology, term, kind):
    """把记录里的类型解析成本体里的 URIRef（解析不出来返回 None）。"""
    if not term:
        return None
    try:
        return ontology.resolve(term, structure_pending._allowed(ontology, kind))
    except Exception:
        return None


class Reclassify:
    """受控重分类服务：看（plan）/ 预演（preview）/ 迁移（apply）/ 撤销（走治理层 undo）。"""

    def __init__(self, service):
        self.service = service
        self.repo = service.repository

    # ---------------------------------------------------------------- 只读：计划
    def plan(self, project_id):
        """把「还挂在旧版本上的知识」按旧类型分组，逐组给出处置。**只读**。"""
        versions = self.repo.list_ontologies(project_id)
        if not versions:
            raise ReclassifyError('这个项目还没有本体：先在本体建模层建一份并发布')
        version_label = self._version_labels(versions)
        current = versions[-1]
        current_ontology = self.service.structure_pending_ontology(project_id)
        rows = self.service.scoped(
            project_id, {'kinds': list(structure_pending.KNOWLEDGE_KINDS)})
        stale = [row for row in rows if (row.get('ontology_id') or None) != current['id']]

        labels_current = _label_map(current_ontology)
        ctx = self._context(project_id, versions, current_ontology, labels_current)
        labels_old, groups = {}, {}
        for row in stale:
            version = row.get('ontology_id') or None
            if version not in labels_old:
                labels_old[version] = self._labels_of(version, ctx)
            key = self._key(row)
            group = groups.get(key)
            if group is None:
                group = groups[key] = self._group(row, ctx, labels_old[version])
            group['records'].append({
                'id': row['id'], 'version': row.get('version'),
                'text': _truncate(row.get('text'))})
            group['record_count'] += 1

        for group in groups.values():
            group['records'] = group['records'][:_SAMPLE_LIMIT]
        ordered = sorted(groups.values(),
                         key=lambda item: (item['action'], -item['record_count'], item['key']))
        migratable = [group for group in ordered if group['migratable']]
        return {
            'project_id': project_id,
            'current_ontology_id': current['id'],
            'current_version': version_label[current['id']],
            'total_records': len(rows),
            'stale_records': len(stale),
            'groups': ordered,
            'migratable_groups': len(migratable),
            'unmapped_groups': len(ordered) - len(migratable),
            'migratable_records': sum(group['record_count'] for group in migratable),
            'unmapped_records': sum(group['record_count'] for group in ordered
                                    if not group['migratable']),
            'definitions': ACTIONS,
            'constraints': [
                '默认不跑：这一页只是看；要迁移必须逐组勾选后点「确认迁移」',
                '不原地改：每条迁移都产生新版本，旧版本仍在版本历史里可查、可恢复',
                '可整批撤销：一次迁移写成一条操作，在「可撤销的操作」里一键回退',
            ],
            'note': '迁移只改"这条知识挂在哪一版本体上、用哪个类型"，不改它的正文与有效期。',
        }

    # ---------------------------------------------------------------- 只读：预演
    def preview(self, project_id, keys):
        """干跑：这几组迁过去以后，本体校验会不会拦下来。**不写任何东西**。"""
        plan = self.plan(project_id)
        selected = self._selected(plan, keys)
        updates = self._updates(project_id, selected, plan)
        report = self._validate(project_id, updates, plan['current_ontology_id'])
        return {
            'project_id': project_id,
            'ontology_id': plan['current_ontology_id'],
            'ontology_version': plan['current_version'],
            'would_change': len(updates),
            'groups': [{'key': group['key'], 'action': group['action'],
                        'action_label': group['action_label'],
                        'from_label': group['from_label'], 'to_label': group['to_label'],
                        'record_count': group['record_count']} for group in selected],
            'validation': report,
            'blocked': not report['conforms'],
            'note': '预演用的是与真正写入同一个校验函数（_timeline_check），'
                    '所以这里说通过、写入就不会被拦；说被拦，就一定能看到具体是哪几条。',
            'undoable': '真正迁移后整批写成一条操作，可在「可撤销的操作」里一键撤销。',
        }

    # ---------------------------------------------------------------- 写入：迁移
    def apply(self, project_id, keys, actor):
        """把选中的组迁到当前本体：每条产生新版本，整批一条可撤销操作。"""
        plan = self.plan(project_id)
        selected = self._selected(plan, keys)
        if not selected:
            raise ReclassifyError('没有选中任何一组：先在迁移面板里勾选要迁的类')
        ontology_id = plan['current_ontology_id']
        with self.service.lock:
            # 拿**当下**的记录版本（不是 plan 那一刻的）：期间有人改过就由
            # expected_versions 把整批拒掉，绝不覆盖别人的编辑。
            updates = self._updates(project_id, selected, plan)
            report = self._validate(project_id, updates, ontology_id)
            if not report['conforms']:
                raise ReclassifyError(
                    '这批迁移会被本体校验拦下（一条都没写）：' + '；'.join(report['errors'][:3]))
            from .governance import Governance
            result = Governance(self.service)._commit(
                project_id, self._before(project_id, updates), updates, 'reclassify',
                audit_extra={'reclassify': {
                    'from_to': [{'key': group['key'], 'from_type': group['from_type'],
                                 'to_type': group['to_type'], 'record_count': group['record_count']}
                                for group in selected],
                    'to_ontology_id': ontology_id,
                    'actor': actor,
                }})
        return {
            'project_id': project_id,
            'operation_id': result['operation_id'],
            'migrated': len(result['records']),
            'ontology_id': ontology_id,
            'ontology_version': plan['current_version'],
            'groups': [{'key': group['key'], 'from_label': group['from_label'],
                        'to_label': group['to_label'], 'record_count': group['record_count']}
                       for group in selected],
            'note': f'已把 {len(result["records"])} 条知识迁到{plan["current_version"]}：'
                    '每条都产生了新版本（旧版本仍在版本历史里）。',
            'undo': {'operation_id': result['operation_id'], 'label': '撤销这次迁移'},
        }

    # ------------------------------------------------------------------ 内部工具
    @staticmethod
    def _key(row):
        """分组的 key：旧本体版本 + 记录种类 + 类型 IRI（页面勾选、预演、迁移共用同一个 key）。"""
        return f'{row.get("ontology_id") or "none"}:{row["kind"]}:{row.get("type") or ""}'

    @staticmethod
    def _version_labels(versions):
        """本体版本 → 展示名，版本号与复用语义以仓储字段为准。"""
        ordered = list(versions)
        by_version = {}
        for item in ordered:
            by_version.setdefault(item['version'], []).append(item)

        def short_id(item, peers):
            ontology_id = str(item['id'])
            width = min(8, len(ontology_id))
            while any(str(peer['id'])[:width] == ontology_id[:width]
                      for peer in peers if peer['id'] != item['id']):
                width += 1
            return ontology_id[:width]

        labels = {}
        for index, item in enumerate(ordered):
            date = str(item.get('created_at') or '')[:10]
            tail = '（当前）' if index == len(ordered) - 1 else '（旧版）'
            parts = [f'本体 v{item["version"]}']
            peers = by_version[item['version']]
            if len(peers) > 1:
                parts.append(short_id(item, peers))
            if date:
                parts.append(date)
            if item['version_reused']:
                parts.append('标注修订')
            labels[item['id']] = ' · '.join(parts) + tail
        return labels

    def _context(self, project_id, versions, current_ontology, labels_current):
        """一次 plan 里共享的上下文：版本顺序、标签、已解析过的本体（缓存，别再解析整段 Turtle）。"""
        ordered = sorted(versions, key=lambda item: str(item.get('created_at') or ''))
        return {'project_id': project_id,
                'order': [item['id'] for item in ordered],
                'version_label': self._version_labels(versions),
                'current_ontology': current_ontology,
                'labels_current': labels_current,
                'cache': {}}

    def _old_ontology(self, ontology_id, ctx):
        cache = ctx['cache']
        if ontology_id not in cache:
            cache[ontology_id] = Ontology(
                self.repo.get_ontology(ctx['project_id'], ontology_id)['turtle'])
        return cache[ontology_id]

    def _labels_of(self, ontology_id, ctx):
        """旧版本体里的术语标签（旧类型可能在新本体里已经没有名字了）。"""
        if ontology_id is None:
            return {}
        try:
            return _label_map(self._old_ontology(ontology_id, ctx))
        except Exception:
            return {}

    def _group(self, row, ctx, labels_old):
        """给一条记录所在的组定处置（每组只算一次）。"""
        current_ontology = ctx['current_ontology']
        labels_current = ctx['labels_current']
        version, kind, term = row.get('ontology_id') or None, row['kind'], row.get('type')
        action, target = 'unmapped', None
        declared = structure_pending.resolves(current_ontology, term, kind)
        active = declared and current_ontology.is_active_term(term)
        if declared:
            replacement = _replacement(current_ontology, term, kind)
            usable = bool(replacement) and structure_pending.resolves(
                current_ontology, replacement, kind)
            if not active and usable:
                # 类型还在，但已被停用且写了替代映射：这正是"停用类时要求指定替代类"
                # 留下的数据 —— 现在用它把知识直接落到替代概念上，比先搬版本再让用户自己改强。
                action, target = 'rename', replacement
            elif not active and not usable:
                # 停用、又没有可用的替代类：**不能**当"原样搬" —— 本体门禁不允许把停用
                # 术语写进新版本（"停用"本身就是"别再用它"）。这类知识归「没有去处」。
                action, target = 'unmapped', None
            else:
                action, target = 'carry', term
        else:
            replacement = _replacement(current_ontology, term, kind) \
                or self._replacement_in_history(term, kind, version, ctx)
            if replacement and structure_pending.resolves(current_ontology, replacement, kind):
                action, target = 'rename', replacement
        note = {
            'carry': '类型在新本体里还在：只把归属改到新版本，类型不动。',
            'rename': '旧类型有替代映射：连同类型一起改到替代概念。',
            'unmapped': '新本体里没有这个类型、也没有替代映射：先在本体建模层把概念建出来并发布，'
                        '这些知识会自动脱离「结构待定」，再来迁移。',
        }[action]
        if action == 'unmapped' and declared:
            note = ('这个类型已经被停用、又没有可用的替代类：本体门禁不允许把停用术语写进新版本。'
                    '请在本体建模层恢复它、或指定替代类，再来迁移。')
        return {
            'key': self._key(row),
            'kind': kind,
            'kind_label': structure_pending.KIND_LABELS.get(TERM_KIND.get(kind), '术语'),
            'action': action, 'action_label': ACTIONS[action], 'migratable': action != 'unmapped',
            'from_ontology_id': version,
            'from_version': ctx['version_label'].get(version, '未知版本'),
            'from_type': term, 'from_label': _term_name(labels_old, term),
            'to_type': target,
            'to_label': _term_name(labels_current, target) if target else None,
            'record_count': 0, 'records': [], 'sample_limit': _SAMPLE_LIMIT,
            'note': note,
        }

    def _replacement_in_history(self, term, kind, from_version, ctx):
        """替代映射记在**哪一版**上都算：从这条知识挂的那一版往后找，取最先出现的那个。

        现实里映射常常记在一个中间版本上（那一版把类停用并指定了替代类），而最新版
        干脆把这个类删了。只看当前版本会误判成"没有去处"，把本来能自动迁的知识丢给人工。
        """
        order = ctx['order']
        start = order.index(from_version) if from_version in order else 0
        for version in order[start:]:
            try:
                ontology = self._old_ontology(version, ctx)
            except Exception:
                continue
            replacement = _replacement(ontology, term, kind)
            if replacement:
                return replacement
        return None

    def _selected(self, plan, keys):
        """把请求里的组 key 解析成计划里的组；不认的 key 与「没有去处」都当面拒绝。"""
        wanted = list(keys or [])
        if not wanted:
            raise ReclassifyError('没有指定要迁移的组：请逐组勾选（默认不跑）')
        by_key = {group['key']: group for group in plan['groups']}
        selected, unknown, blocked = [], [], []
        for key in wanted:
            group = by_key.get(key)
            if group is None:
                unknown.append(key)
            elif not group['migratable']:
                blocked.append(group['from_label'])
            else:
                selected.append(group)
        if unknown:
            raise ReclassifyError('这些分组已经不成立了（本体或记录变过），请重新读一次面板：'
                                  + '、'.join(unknown))
        if blocked:
            raise ReclassifyError('这些类型在新本体里没有去处，不能迁移：' + '、'.join(blocked)
                                  + '；请先在本体建模层把概念建出来并发布')
        return selected

    def _updates(self, project_id, selected, plan):
        """按选中的组生成"迁移后的记录"（内存里，还没写）。

        字段裁剪走治理层同一个 :func:`writable`（按 ``RecordWrite`` 白名单），
        所以迁移和合并/删除走的是同一条写入通道，不会夹带只读字段。
        """
        ontology_id = plan['current_ontology_id']
        wanted = {group['key']: group for group in selected}
        updates = []
        for row in self.service.scoped(
                project_id, {'kinds': list(structure_pending.KNOWLEDGE_KINDS)}):
            group = wanted.get(self._key(row))
            if group is None:
                continue
            update = writable(row)
            update['ontology_id'] = ontology_id
            if group['action'] == 'rename':
                update['type'] = group['to_type']
            updates.append(update)
        return updates

    def _before(self, project_id, updates):
        """写入前的原样记录（撤销要靠它：审计记录里存的就是这一份）。"""
        wanted = {row['id'] for row in updates}
        return [row for row in self.service._latest(project_id) if row['id'] in wanted]

    def _validate(self, project_id, updates, ontology_id):
        """用**写入路径同一个**函数预演：把要改的记录并进当前图谱后跑一次校验。

        校验报告里的 errors 是结构化的（``{'record_id':…, 'message':…}``），这里翻成人话，
        页面上直接显示"哪一条、为什么"——用户要能自己判断该不该迁。
        """
        prospective = {row['id']: row for row in self.service.scoped(
            project_id, {'kinds': list(structure_pending.KNOWLEDGE_KINDS)})}
        for row in updates:
            prospective[row['id']] = {**prospective.get(row['id'], {}), **row}
        _, _, _, _, report = self.service._timeline_check(
            project_id, ontology_id, prospective)
        return {'conforms': report['conforms'],
                'errors': [_readable_error(item) for item in (report.get('errors') or [])][:10],
                'checked_ontology_id': ontology_id}


def _readable_error(item):
    """把一条校验错误说成人话（照旧保留记录 id，用户要能对上号）。"""
    if not isinstance(item, dict):
        return str(item)
    record_id = item.get('record_id') or item.get('id') or item.get('focus') or '（未知记录）'
    return f'{record_id}：{item.get("message") or item}'
