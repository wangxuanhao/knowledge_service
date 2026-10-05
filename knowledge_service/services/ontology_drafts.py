"""Governed ontology draft lifecycle and read-side graph projections.

本模块是 ``OntologyDraftStore`` 之上的领域接缝，负责状态流转、服务端操作编译、
校验快照、审核策略与发布前置校验。最终那次原子发布由 repository 层注入的回调执行，
本服务本体从不自己拼半个发布事务。

代码组织（2026-10-05 拆分自单一 2707 行巨类）：
  * ``ontology_drafts_core``      —— 状态常量 / 领域异常 / 纯校验助手（共享基座）
  * ``ontology_drafts_commands``  —— 变更命令编译与影响评估
  * ``ontology_drafts_review``    —— 审核计划 / 发布前置 / 原子发布
  * ``ontology_drafts_evidence``  —— 证据引用还原
  * ``ontology_drafts_readmodels``—— 只读结构投影（层级树 / 搜索 / 邻域 / 矩阵）
本模块保留请求生命周期主干（创建 / 编辑 / 校验 / 提交 / 裁决 / 重基线），
并以 mixin 组合出对外唯一的 ``OntologyDrafts`` 门面——公共 API 与拆分前完全一致。
"""

from __future__ import annotations

import json
from uuid import uuid4
from rdflib import Literal, RDF, RDFS, URIRef
from rdflib.namespace import OWL
from ..core.time import utc_now
from ..repository.ontology_draft_store import OntologyDraftConflict
from .ontology import Ontology
from .ontology_operations import (apply_operations, is_batch_eligible, operation_fingerprint, validate_ontology_invariants)

from .ontology_drafts_core import (
    VALIDATION_RULE_VERSION, PLAN_POLICY_VERSION, IRREVERSIBLE_ACTIONS, PENDING_STATES, ACCEPTED_STATES, REJECTED_STATES, FINAL_STATES, EDITABLE_STATES, REVIEW_STATES, OPEN_STATES, REVERT_SOURCE, REVERT_WARNING, SUBMITTED_STATES, NEIGHBORHOOD_LINK_LIMIT, ANNOTATION_ACTIONS, OntologyDraftError, RevisionConflict, StaleBase, StaleSource, ValidationChanged, ValidationFailed, BatchNotAllowed, DraftImmutable, _STATUS_LABEL, _STATUS_COPY, _status_guard, _require_submitted, _canonical_json, _fingerprint, _semantic_operation, is_annotation_only, _text, _as_list,
)
from .ontology_drafts_commands import DraftCommandMixin
from .ontology_drafts_evidence import DraftEvidenceMixin
from .ontology_drafts_readmodels import DraftReadModelsMixin
from .ontology_drafts_review import DraftReviewPublishMixin


class OntologyDrafts(
        DraftCommandMixin, DraftReviewPublishMixin,
        DraftEvidenceMixin, DraftReadModelsMixin):
    """Deep interface for draft editing, review, validation and graph reads."""

    def __init__(self, repository, *, publisher=None,
                 validation_rule_version=VALIDATION_RULE_VERSION):
        self.repository = repository
        self.store = repository._ontology_drafts
        self.publisher = publisher
        self.validation_rule_version = _text(
            validation_rule_version, 'validation_rule_version')
        self.plan_policy_version = PLAN_POLICY_VERSION

    # ------------------------------------------------------------------ basics
    def _latest_id(self, project_id):
        versions = self.repository.list_ontologies(project_id)
        return versions[-1]['id'] if versions else None

    def _version_number(self, project_id, ontology_id):
        """版本号按发布顺序现算（1 起）。库里不存序号 —— 存了就会与顺序脱节，
        而版本号是给人看的（"回到 v2"），必须和版本列表里显示的号一致。"""
        for index, item in enumerate(self.repository.list_ontologies(project_id), start=1):
            if item['id'] == ontology_id:
                return index
        return '?'

    def _base_turtle(self, project_id, base_ontology_id):
        if base_ontology_id is None:
            return ''
        return self.repository.get_ontology(project_id, base_ontology_id)['turtle']

    def _translate_conflict(self, exc):
        return RevisionConflict(str(exc), details={
            'draft_id': exc.draft_id,
            'expected_revision': exc.expected_revision,
        })

    def _cas(self, project_id, draft_id, expected_revision, changes):
        try:
            return self.store.compare_and_set(
                project_id, draft_id, expected_revision, changes)
        except OntologyDraftConflict as exc:
            raise self._translate_conflict(exc) from exc

    @staticmethod
    def _assert_revision(draft, expected_revision):
        if draft['revision'] != expected_revision:
            raise RevisionConflict(
                'ontology draft revision changed', details={
                    'draft_id': draft['id'],
                    'expected_revision': expected_revision,
                    'current_revision': draft['revision'],
                })

    def _history(self, project_id, draft_id):
        history = self.store.export(project_id)
        operations = [row for row in history['operations']
                      if row['draft_id'] == draft_id]
        decisions = [row for row in history['decisions']
                     if row['draft_id'] == draft_id]
        return operations, decisions

    def _effective_operations(self, project_id, draft_id):
        """Return current operations in stable logical, not append, order."""
        history, _ = self._history(project_id, draft_id)
        current_ids = {
            row['id'] for row in self.store.effective_operations(project_id, draft_id)}
        by_id = {row['id']: row for row in history}
        positions = {row['id']: index for index, row in enumerate(history)}

        def logical_position(operation):
            current = operation
            seen = set()
            while current.get('supersedes_operation_id'):
                parent_id = current['supersedes_operation_id']
                if parent_id in seen or parent_id not in by_id:
                    break
                seen.add(parent_id)
                current = by_id[parent_id]
            return positions[current['id']]

        effective = [row for row in history if row['id'] in current_ids]
        effective.sort(key=lambda row: (logical_position(row), positions[row['id']]))
        return [row for row in effective
                if not (row.get('validation') or {}).get('withdrawn')]

    @staticmethod
    def _effective_decisions(all_decisions):
        superseded = {
            row['supersedes_decision_id'] for row in all_decisions
            if row.get('supersedes_decision_id')}
        latest = [row for row in all_decisions if row['id'] not in superseded]
        # A later decision for the same operation is authoritative even when an
        # imported legacy history omitted its explicit supersession link.
        by_operation = {}
        for row in latest:
            by_operation[row['operation_id']] = row
        return list(by_operation.values())

    def _active_operations(self, project_id, draft_id):
        return [row for row in self._effective_operations(project_id, draft_id)
                if (row.get('validation') or {}).get('rebase_status') != 'no-op']

    def _ontology_for_draft(self, project_id, draft):
        operations = self._active_operations(project_id, draft['id'])
        applicable = [row for row in operations
                      if (row.get('validation') or {}).get('rebase_status') != 'conflict'
                      and not (row.get('validation') or {}).get('errors')]
        turtle = self._base_turtle(project_id, draft['base_ontology_id'])
        if applicable:
            turtle = apply_operations(turtle, map(_semantic_operation, applicable))
        return turtle, operations

    def _preview(self, project_id, draft):
        _, all_decisions = self._history(project_id, draft['id'])
        effective = self._effective_operations(project_id, draft['id'])
        effective_ids = {row['id'] for row in effective}
        decisions = [row for row in self._effective_decisions(all_decisions)
                     if row['operation_id'] in effective_ids
                     and row['operation_fingerprint'] == next(
                         (op['fingerprint'] for op in effective
                          if op['id'] == row['operation_id']), None)]
        try:
            turtle, _ = self._ontology_for_draft(project_id, draft)
        except ValueError:
            turtle = self._base_turtle(project_id, draft['base_ontology_id'])
        return {
            **draft,
            'draft': draft,
            'operations': effective,
            'decisions': decisions,
            'turtle': turtle,
            'ontology': Ontology(turtle).summary() if turtle.strip() else {
                'classes': [], 'relations': [], 'attributes': [], 'triples': 0},
        }

    # ----------------------------------------------------------- source checks
    @staticmethod
    def _source_references(context):
        references = []
        documents = context.get('documents') if isinstance(context, dict) else None
        if isinstance(documents, list):
            references.extend(item for item in documents if isinstance(item, dict))
        if isinstance(context, dict) and context.get('document_id'):
            references.append(context)
        return references

    @staticmethod
    def _candidate(document, candidate_id):
        metadata = document.get('metadata') or {}
        candidates = [*(metadata.get('review_candidates') or []),
                      *(metadata.get('discovery_candidates') or [])]
        return next((item for item in candidates
                     if item.get('id') == candidate_id), None)

    def _current_source_document(self, project_id, document_id):
        """Read the unsuperseded source revision without a wall-clock race."""
        history = self.repository.history(project_id, document_id)
        document = next((item for item in reversed(history)
                         if item.get('superseded_at') is None), None)
        if document is None or (document.get('metadata') or {}).get('_deleted'):
            raise KeyError(document_id)
        return document

    def _source_snapshot(self, project_id, draft, *, require_current=True):
        context = draft.get('source_context') or {}
        snapshots = []
        for reference in self._source_references(context):
            document_id = reference.get('document_id') or reference.get('id')
            try:
                document = self._current_source_document(project_id, document_id)
            except KeyError as exc:
                if require_current:
                    raise StaleSource(
                        f'source document {document_id!r} no longer exists') from exc
                continue
            expected_version = reference.get(
                'expected_document_version', reference.get('version'))
            expected_version_id = reference.get(
                'expected_document_version_id', reference.get('version_id'))
            if require_current and expected_version is not None and (
                    document.get('version') != expected_version):
                raise StaleSource(
                    f'source document {document_id!r} revision changed', details={
                        'document_id': document_id,
                        'expected_version': expected_version,
                        'current_version': document.get('version'),
                    })
            if require_current and expected_version_id is not None and (
                    document.get('version_id') != expected_version_id):
                raise StaleSource(
                    f'source document {document_id!r} version changed')
            candidate_ids = reference.get('candidate_ids') or []
            if reference.get('candidate_id'):
                candidate_ids = [*candidate_ids, reference['candidate_id']]
            candidates = []
            for candidate_id in candidate_ids:
                candidate = self._candidate(document, candidate_id)
                if candidate is None:
                    if require_current:
                        raise StaleSource(
                            f'source candidate {candidate_id!r} no longer exists')
                    continue
                expected_status = reference.get('expected_candidate_status')
                statuses = reference.get('candidate_statuses') or {}
                expected_status = statuses.get(candidate_id, expected_status)
                if (require_current and expected_status is not None
                        and candidate.get('status') != expected_status):
                    raise StaleSource(
                        f'source candidate {candidate_id!r} status changed')
                expected_fingerprints = reference.get('candidate_fingerprints') or {}
                actual_fingerprint = _fingerprint(candidate)
                if (require_current and expected_fingerprints.get(candidate_id)
                        and expected_fingerprints[candidate_id] != actual_fingerprint):
                    raise StaleSource(
                        f'source candidate {candidate_id!r} changed')
                candidates.append({
                    'id': candidate_id, 'status': candidate.get('status'),
                    'fingerprint': actual_fingerprint})
            snapshots.append({
                'document_id': document_id,
                'version': document.get('version'),
                'version_id': document.get('version_id'),
                'candidates': candidates,
            })
        return snapshots

    def _authoritative_evidence(self, draft, command):
        requested = command.get('evidence_refs', command.get('evidence'))
        if requested is not None and (
                not isinstance(requested, list)
                or not all(isinstance(item, str) and item.strip()
                           for item in requested)):
            raise ValueError('evidence_refs must be a list of non-empty strings')
        if draft['source_kind'] not in {
                'discovery', 'candidate', 'import', 'turtle'}:
            return list(dict.fromkeys(requested or []))

        context = draft.get('source_context') or {}
        authoritative = []
        for reference in self._source_references(context):
            document_id = reference.get('document_id') or reference.get('id')
            version = reference.get(
                'expected_document_version_id', reference.get(
                    'version_id', reference.get(
                        'expected_document_version', reference.get('version'))))
            if document_id and version is not None:
                authoritative.append(f'document-version:{document_id}:{version}')
            candidate_ids = [*(reference.get('candidate_ids') or [])]
            if reference.get('candidate_id'):
                candidate_ids.append(reference['candidate_id'])
            authoritative.extend(
                f'candidate:{candidate_id}' for candidate_id in candidate_ids)
        for key, prefix in (
                ('candidate_refs', 'candidate'),
                ('document_refs', 'document-version')):
            values = context.get(key) or []
            authoritative.extend(
                value if ':' in str(value) else f'{prefix}:{value}'
                for value in values)
        authoritative.extend(context.get('evidence_refs') or [])
        authoritative = list(dict.fromkeys(authoritative))
        if requested is not None:
            forged = sorted(set(requested) - set(authoritative))
            if forged:
                raise ValueError(
                    'evidence_refs are not present in the frozen source snapshot: '
                    + ', '.join(forged))
            return list(dict.fromkeys(requested))
        return authoritative

    @staticmethod
    def _document_evidence_ref(reference):
        document_id = reference.get('document_id') or reference.get('id')
        version = reference.get(
            'expected_document_version_id', reference.get(
                'version_id', reference.get(
                    'expected_document_version', reference.get('version'))))
        if document_id and version is not None:
            return f'document-version:{document_id}:{version}'
        return None

    def _refresh_operation_evidence(self, draft, refreshed_draft, evidence):
        """Refresh versioned refs without widening an operation's evidence set."""
        authoritative = set(self._authoritative_evidence(refreshed_draft, {}))
        old_documents = {
            (reference.get('document_id') or reference.get('id')): reference
            for reference in self._source_references(
                draft.get('source_context') or {})
        }
        new_documents = {
            (reference.get('document_id') or reference.get('id')): reference
            for reference in self._source_references(
                refreshed_draft.get('source_context') or {})
        }
        refreshed_refs = {}
        for document_id, old_reference in old_documents.items():
            new_reference = new_documents.get(document_id)
            if new_reference is None:
                continue
            old_ref = self._document_evidence_ref(old_reference)
            new_ref = self._document_evidence_ref(new_reference)
            if old_ref and new_ref:
                refreshed_refs[old_ref] = new_ref

        selected = []
        for reference in evidence or []:
            refreshed = refreshed_refs.get(reference, reference)
            if refreshed not in authoritative:
                raise StaleSource(
                    f'operation evidence {reference!r} no longer exists in '
                    'the refreshed source snapshot')
            if refreshed not in selected:
                selected.append(refreshed)
        return selected

    def _mark_stale(self, project_id, draft, expected_revision, kind, error):
        """把"这份草案过期了"讲清楚，然后拒绝这次写操作。

        A3 之前这里会把 status 写成 stale_base / stale_source —— 那是拿一个**状态**去记一件
        **可以推导的事实**（基线落后于当前本体 / 来源快照变了），于是必然出现"状态说没过期、
        实际已过期"这种两套口径打架。状态收成三种之后，过期一律当场推导（needs_rebase()），
        这里只负责把 kind 附进错误详情再抛出（kind 仍是 'stale_base' / 'stale_source'，
        与异常 code 对齐，前端按它给"重新基线 / 刷新来源"）。
        """
        error.details.setdefault('draft', draft)
        error.details.setdefault('needs_rebase', kind)
        raise error

    def needs_rebase(self, project_id, draft, *, check_source=True):
        """「需重新基线」是**推导**出来的事实，不是草案状态（A3）。

        两种来源：① 草案基线落后于项目当前本体 → 'stale_base'；
                  ② 来源快照（discovery / candidate）变了 → 'stale_source'。
        不需要则返回 None。读不出来（数据库暂时报错、文档被删等）时返回 None 而不抛 ——
        "读不到"不等于"过期了"，宁可不说也不撒谎（真去写的那一刻 _check_current 仍会拦）。

        「回到某一版」的草案**排除在外**：它的基线故意不是最新版，让人去"重新基线"
        等于把回退意图直接抹掉。要不要回退是用户在版本管理里做的决定，不是过期。
        """
        if draft is None:
            return None
        if self._revert_base(project_id, draft):
            return None
        if draft.get('base_ontology_id') != self._latest_id(project_id):
            return 'stale_base'
        if check_source and draft.get('source_kind') in {'discovery', 'candidate'}:
            try:
                self._source_snapshot(project_id, draft)
            except StaleSource:
                return 'stale_source'
            except Exception:  # noqa: BLE001 —— 读不出来≠过期
                return None
        return None

    def pending_phase(self, project_id, draft):
        """pending 草案的细分阶段（**推导**，不落库）。

        A3 把 editing / submitted / reviewed 三档收成一档 pending，三档的区分改由这里算：
          * 'settled'   —— 每条变更都有当前决定（下一步：收下并发布）；
          * 'reviewing' —— 已提交且校验通过，还有变更没决定（下一步：逐条收下 / 不收）；
          * 'editing'   —— 还没提交（或提交后校验没过）（下一步：去「本体建模层」改）。
        判据与 publish_readiness / publish_preflight 逐字对齐：决定必须"当前"才算数
        （operation_fingerprint 匹配），否则改一下草案就等于把旧决定撤回了。
        """
        if draft is None or draft.get('status') not in PENDING_STATES:
            return None
        # 「已经提交」由**提交时刻**（submitted_at）回答，不是 validated_at ——
        # 提交会故意清空 validated_at（重新提交即作废上一次校验），拿它当"提交过没有"
        # 的判据会让刚提交的请求被当成"还没提交"，收件箱直接把它弹回编辑台。
        if not draft.get('submitted_at'):
            return 'editing'
        operations = self._active_operations(project_id, draft['id'])
        if not operations:
            return 'editing'
        _, decision_rows = self._history(project_id, draft['id'])
        decided = set()
        for row in self._effective_decisions(decision_rows):
            if row.get('action') not in {'approve', 'reject'}:
                continue
            operation = next((item for item in operations
                              if item['id'] == row.get('operation_id')), None)
            if operation and row.get('operation_fingerprint') == operation['fingerprint']:
                decided.add(operation['id'])
        if all(operation['id'] in decided for operation in operations):
            return 'settled'
        return 'reviewing'

    def _revert_base(self, project_id, draft):
        """这是不是一份「回到某一版」的草案？是的话返回它要回到的那一版 id。

        判两件事，缺一不可：
          * ``source_kind`` 是 revert（**显式意图**，不是"基线过期了"）；
          * 那一版确实还在这个项目里（不放开凭空指定的版本 id —— 否则回退就成了
            一条可以指向不存在基线的旁路）。

        为什么值得单独一个助手：服务端有三处都假设"基线必须是最新版"
        （create / _check_current / publish_readiness / needs_rebase），回退要逐处
        放行而不是只改一处 —— 只改一处会出现"能建草案但发不出去"这种半通的状态。
        """
        if draft.get('source_kind') != REVERT_SOURCE:
            return None
        base = draft.get('base_ontology_id')
        if not base:
            return None
        known = {item['id'] for item in self.repository.list_ontologies(project_id)}
        return base if base in known else None

    def _check_current(self, project_id, draft, expected_revision, *,
                       expected_ontology_id=None, check_source=True):
        latest = self._latest_id(project_id)
        expected = draft['base_ontology_id']
        # 「回到某一版」的草案基线故意不是最新版：只有基线确实是本项目里存在的
        # 那一版时才放行，否则仍按"基线过期"处理（不放开凭空指定的版本 id）。
        revert = self._revert_base(project_id, draft)
        if expected != latest and not revert:
            self._mark_stale(
                project_id, draft, expected_revision, 'stale_base',
                StaleBase('draft base is not the current project ontology',
                          details={'base_ontology_id': expected,
                                   'current_ontology_id': latest}))
        if (expected_ontology_id is not None and expected_ontology_id != latest
                and expected_ontology_id != revert):
            # A stale client precondition does not make a current draft stale.
            # Only persist stale_base when the draft's own immutable base fell
            # behind the project ontology.
            raise StaleBase(
                'expected ontology is not the current project ontology', details={
                    'expected_ontology_id': expected_ontology_id,
                    'current_ontology_id': latest,
                    'draft': draft,
                })
        if check_source and draft['source_kind'] in {'discovery', 'candidate'}:
            try:
                self._source_snapshot(project_id, draft)
            except StaleSource as exc:
                self._mark_stale(
                    project_id, draft, expected_revision, 'stale_source', exc)
        return latest

    # --------------------------------------------------------------- lifecycle
    def get(self, project_id, draft_id):
        """Return a draft with its current operations, decisions and preview."""
        return self._preview(project_id, self.store.get(project_id, draft_id))

    def list(self, project_id, status=None):
        """List lightweight draft records; previews remain an explicit read."""
        return self.store.list(project_id, status=status)

    def stage_state(self, project_id, draft_id=None):
        """阶段门禁的唯一判据：三扇门各自能不能进、为什么、下一步点哪里。

        为什么门禁放在服务端：状态本来就存在这里（``draft.status`` + 写操作的
        ``_status_guard``），但"哪个阶段能打开"以前是浏览器里 stageRules() 现推的 ——
        刷新后没选中草案时审核/发布全灰，用户只看到"点不开"，而服务端一直知道答案。
        现在前端只渲染这份结果，不再自己算。

        A3（状态 7→3）之后这里少了两扇门：
          * **发布不是阶段** —— 收下就是发布（publish 是「收下」的结果，不是一次要用户
            单独去点的动作），所以 ``stages`` 里没有 publish 这个 key 了；
          * **校验不是阶段** —— A2 已经把它并进收件箱（进入时自动跑），不再是四站流水线。

        三扇门（严格顺序、逐级累积解锁，已走过的始终可回看）：

        - 发现：永远可进（它是入口，收下/驳回之后也回到这里）；
        - 设计：项目里有**待处理**的草案、且选中的那份也是待处理 → 可进；
        - 收件箱：选中的草案是待处理、且不需要先重新基线 → 可进。

        ``reason`` 是给用户看的"下一步"，不是给日志看的：只写当前真正该做的那一步，
        不要并列两条互相指向（否则会把用户指去点另一个同样进不去的门）。
        """
        drafts = self.store.list(project_id)
        selected = None
        if draft_id:
            selected = next((item for item in drafts if item['id'] == draft_id), None)
        status = selected['status'] if selected else None
        label = _STATUS_LABEL.get(status, status or '') if status else ''
        has_draft = selected is not None
        has_drafts = has_draft or bool(drafts)
        # 终态（已收下 / 已驳回）是"只读账本"：只有「发现」还能进，理由指向真正能干活的地方。
        terminal = bool(has_draft and status in FINAL_STATES)
        if terminal:
            fate = '收下' if status == 'accepted' else '驳回'
            terminal_reason = (
                f'这份请求已{fate}，是只读的历史记录：回看版本、结构对比与发布记录请用「本体建模层」顶栏的「版本管理」；'
                '要再调整正式本体，先在「本体建模层」基于当前版本新建一份草案。')
        else:
            terminal_reason = ''
        # 「待处理」＝请求还没收下也没驳回 —— 只有这种草案能改、能处理。
        open_drafts = [draft for draft in drafts if draft['status'] in PENDING_STATES]
        has_open_draft = bool(open_drafts)
        editable_selected = bool(has_draft and status in PENDING_STATES)
        # 这两件都是**推导**，不是状态：细分阶段（还没提交/待逐条处理/全部有决定）与
        # 「要不要先重新基线」。只对选中的那份算，避免为列表里每一份都读一遍决定。
        rebase_kind = self.needs_rebase(project_id, selected) if (has_draft and not terminal) else None
        phase = self.pending_phase(project_id, selected) if (has_draft and not terminal) else None

        # 「设计」＝"这一页能不能改结构"。判定必须落在**被选中的那份草案**上：
        #   ① 项目里没有待处理的草案（全都收下/驳回了）→ 本轮收口了，去「本体建模层」开新的；
        #   ② 有待处理的草案但一份都没选中 → 放行，让人进工作区再选；
        #   ③ 选中了待处理的草案 → 放行（若基线过期，理由指向"先重新基线"）；
        #   ④ 选中了终态草案 → 必须锁住。这是真机复验脚本 verify_ontology_gate.py 挖出来的：
        #      某项目 13 份可编辑 + 4 份已关闭，用户点了已关闭的那份，于是「设计」亮着、
        #      点进去只有只读视图、页面上连原因都不写（走到 else 分支给了空 reason）。
        #      门禁说"能进"而实际进不去，就是撒谎。
        if not has_drafts:
            design_reason = '还没有草案：先到「发现」用候选生成草案，或去「本体建模层」新建一份。'
        elif not has_open_draft:
            design_reason = ('本轮已处理完毕（当前只有已收下/已驳回的请求）：等知识写入产生新候选后，'
                             '再到「发现」生成下一轮；要直接调整当前本体结构，请去「本体建模层」。')
        elif not has_draft:
            design_reason = ('本项目有待处理的草案：先在左侧列表里选中一个'
                             '（或用「本体建模层」打开它）。')
        elif terminal:
            # 项目里还有别的可处理草案时，"设计"锁住的理由要**同时**给出两条可行动作：
            # 换一份草案继续改（左栏），或去「本体建模层」新建。只说"这是只读记录"会让
            # 用户卡在"那我该点哪"。这条是真机复验脚本 verify_ontology_gate.py 的教训。
            design_reason = (
                f'当前选中的是「{label}」（只读的历史记录）：先在左侧选中一份「待处理」的草案继续改，'
                '或去「本体建模层」新建一份；回看版本与发布记录请用「版本管理」。'
                if has_open_draft else terminal_reason)
        elif rebase_kind == 'stale_base':
            design_reason = ('这份草案的基线已经不是项目当前本体了：先在「本体建模层」'
                             '点「重新基线」，再继续改。')
        else:
            design_reason = ''
        design_allowed = bool(not terminal and has_open_draft
                              and (not has_draft or editable_selected))

        if terminal:
            review_reason = terminal_reason
        elif not has_draft:
            review_reason = ('先在左侧选中一份「待处理」的草案，再逐条收下或不收。'
                             if has_open_draft else design_reason)
        elif rebase_kind == 'stale_base':
            review_reason = ('这份草案的基线已经变了：先在「本体建模层」点「重新基线」'
                             '（之后要重新校验），再逐条收下或不收。')
        elif rebase_kind == 'stale_source':
            review_reason = ('这份草案的来源快照已经变了：先在「本体建模层」点「刷新来源」'
                             '（之后要重新校验），再逐条收下或不收。')
        else:
            review_reason = ''
        # 待处理的草案**一律**能进「收件箱」：那一页按 pending_phase 自己分工 ——
        # 还没提交时它只给一张"去哪里改"的指路卡（+ 本轮发现回执），不摆要你决定的事。
        # 生成草案后就落在这一页（A2 的决定），所以这里不能因为"还没提交"就锁掉；
        # "锁住"只留给终态和需要重新基线的草案 —— 那两种是真的没有可做的事。
        review_allowed = bool(not terminal and has_draft
                              and status in PENDING_STATES and not rebase_kind)

        def entry(allowed, reason=''):
            return {'allowed': bool(allowed), 'reason': '' if allowed else reason}

        return {
            'project_id': project_id,
            'draft_id': selected['id'] if selected else None,
            'draft_count': len(drafts),
            'open_draft_count': len(open_drafts),
            'status': status,
            'status_label': label or None,
            'status_copy': _STATUS_COPY.get(status) if status else None,
            # 三态之外的两件推导事实，前端只渲染不自己算（同门禁一个规矩）。
            'pending_phase': phase,
            'needs_rebase': rebase_kind,
            'stages': {
                'discover': entry(True),
                'design': entry(design_allowed, design_reason),
                'review': entry(review_allowed, review_reason),
            },
            'next_action': self._next_action(project_id, selected, has_drafts),
        }

    def _next_action(self, project_id, draft, has_drafts):
        """状态条上那唯一一个"下一步"。

        前端按 ``kind`` 挂本地处理器（新建草稿/提交/去收件箱/收下并发布/重新基线/刷新来源），
        文案与可用性由服务端给 —— 这样"下一步是什么"也只有一处口径。

        三态之后这里**先看推导结果**（要不要重新基线、待处理细分到哪一步），再看状态：
        pending 一档里含着"还没提交 / 已提交待逐条处理 / 全部有决定待收下"三种处境，
        全靠 editng/submitted/reviewed 三档记的时候，这三种是三个状态；现在它们是同一档。
        """
        if draft is None:
            if has_drafts:
                return {'kind': 'select_draft', 'label': '', 'stage': None, 'enabled': False,
                        'title': '本项目已有草案：在左侧「对象与草案」里选中一个继续'}
            return {'kind': 'new_draft', 'label': '去本体建模层新建草案', 'stage': 'design',
                    'enabled': True,
                    'title': '改本体（含新建草案、保存即生效）都在「本体建模层」；'
                             '本页只处理别人提的请求（机器抽的候选 / 业务方的申请）'}
        status = draft['status']
        revision = draft.get('revision')
        if status in FINAL_STATES:
            fate = '收下' if status == 'accepted' else '驳回'
            return {'kind': 'discover', 'label': '去发现新候选', 'stage': 'discover', 'enabled': True,
                    'title': (f'本轮已{fate}。等知识写入产生新候选后，在「发现」生成下一轮草案；'
                              '要直接调整当前本体结构，请去「本体建模层」。')}
        rebase_kind = self.needs_rebase(project_id, draft)
        if rebase_kind == 'stale_base':
            return {'kind': 'rebase', 'label': '重新基线', 'stage': None, 'enabled': True,
                    'title': '把草案基线更新到项目当前本体版本，然后重新校验'}
        if rebase_kind == 'stale_source':
            return {'kind': 'reload', 'label': '刷新来源', 'stage': None, 'enabled': True,
                    'title': '重新读取来源快照；来源没变就不用动候选'}
        phase = self.pending_phase(project_id, draft)
        if phase == 'settled':
            # 发布不是页面、也不是第二个按钮：它是「收下」的结果。这个入口只在收尾失败时出现（重试）。
            return {'kind': 'publish', 'label': '收下并发布', 'stage': 'review', 'enabled': True,
                    'title': '全部变更都有决定了：点这里把它写成不可变版本（发布是「收下」的结果）'}
        if phase == 'reviewing':
            return {'kind': 'review', 'label': '去收件箱逐条处理', 'stage': 'review',
                    'enabled': True,
                    'title': '进入收件箱：逐条收下或不收；全部有决定后会自动发布'}
        changes = len(self._active_operations(project_id, draft['id']))
        return {'kind': 'design', 'label': '去本体建模层改这份草案', 'stage': 'review',
                'enabled': True,
                'title': (f'这份草案还在编辑中（修订 {revision}，{changes} 条变更）：'
                          '继续改与「提交并校验」都在「本体建模层」' if changes
                          else '这份草案还没有变更：去「本体建模层」补变更，或驳回它')}

    def update_publication_effects(self, project_id, draft_id,
                                   expected_revision, effects):
        """Refresh frozen source side effects while a source draft is editable."""
        draft = self.store.get(project_id, draft_id)
        self._assert_revision(draft, expected_revision)
        _status_guard(draft, EDITABLE_STATES, '改写来源副作用',
                      '副作用只在编辑态的草案上刷新。')
        if draft['source_kind'] not in {'discovery', 'candidate', 'import'}:
            raise ValueError('manual drafts do not have source publication effects')
        if not isinstance(effects, dict):
            raise ValueError('publication effects must be an object')
        self._check_current(project_id, draft, expected_revision)
        context = dict(draft.get('source_context') or {})
        context['publication_effects'] = effects
        updated = self._cas(project_id, draft_id, expected_revision, {
            'source_context': context,
            'validation_report': None,
            'validation_fingerprint': None,
            'submitted_at': None,
        })
        return self._preview(project_id, updated)

    def create(self, project_id, base_ontology_id_or_none=None, source='manual',
               title=None, actor=None, *, source_context=None, summary='',
               base_ontology_id=None):
        if base_ontology_id is not None:
            if (base_ontology_id_or_none is not None
                    and base_ontology_id_or_none != base_ontology_id):
                raise ValueError('base ontology was provided twice')
            base_ontology_id_or_none = base_ontology_id
        if isinstance(source, dict):
            source_data = dict(source)
            source_kind = source_data.pop('kind', source_data.pop('source_kind', None))
            source_context = {**source_data, **(source_context or {})}
        else:
            source_kind = source
        title = _text(title, 'title')
        actor = _text(actor, 'actor')
        with self.repository._transaction():
            latest = self._latest_id(project_id)
            if source_kind == REVERT_SOURCE:
                # 「回到某一版」：允许（也**必须**）以历史版本为基线。校验那一版属于本项目，
                # 免得回退变成一条能指向不存在基线的旁路。
                known = {item['id']
                         for item in self.repository.list_ontologies(project_id)}
                if base_ontology_id_or_none not in known:
                    raise StaleBase(
                        'revert requires an existing version of this project',
                        details={'base_ontology_id': base_ontology_id_or_none,
                                 'current_ontology_id': latest})
            elif base_ontology_id_or_none != latest:
                raise StaleBase(
                    'new draft must use the current ontology as its base', details={
                        'base_ontology_id': base_ontology_id_or_none,
                        'current_ontology_id': latest})
            context = dict(source_context or {})
            context['actor'] = actor
            if source_kind == REVERT_SOURCE:
                # 把"回到哪一版"写进草案来源：版本管理里能直接读出这一版是回到谁得到的，
                # 不用去翻 base_ontology_id 里那串 id。
                context['revert_from'] = base_ontology_id_or_none
            if source_kind in {'discovery', 'candidate'}:
                # Honour an explicit caller snapshot as an optimistic source
                # precondition before filling omitted immutable snapshot fields.
                self._source_snapshot(
                    project_id, {'source_context': context}, require_current=True)
                context = self._refresh_source_context(project_id, context)
            draft = self.store.create(project_id, {
                'id': str(uuid4()), 'base_ontology_id': base_ontology_id_or_none,
                'source_kind': source_kind, 'status': 'pending', 'revision': 1,
                'title': title, 'summary': summary, 'source_context': context,
            })
            if source_kind in {'discovery', 'candidate'}:
                self._source_snapshot(project_id, draft)
        return draft

    def create_with_command(
            self, project_id, base_ontology_id_or_none=None, source='manual',
            title=None, actor=None, *, source_context=None, summary='', command,
            base_ontology_id=None):
        """Create a draft and its initial compiled operations as one write."""
        with self.repository._transaction():
            draft = self.create(
                project_id, base_ontology_id_or_none, source, title, actor,
                source_context=source_context, summary=summary,
                base_ontology_id=base_ontology_id)
            return self.command(
                project_id, draft['id'], draft['revision'], command)

    @staticmethod
    def _operation_issues(operations):
        errors, warnings, info = [], [], []
        for operation in operations:
            validation = operation.get('validation') or {}
            for issue in validation.get('errors') or []:
                errors.append({**issue, 'operation_ids': [operation['id']]})
            for issue in validation.get('warnings') or []:
                warnings.append({**issue, 'operation_ids': [operation['id']]})
            for issue in validation.get('info') or []:
                info.append({**issue, 'operation_ids': [operation['id']]})
            if validation.get('rebase_status') == 'conflict':
                errors.append({
                    'code': 'rebase_conflict', 'severity': 'error',
                    'message': validation.get('rebase_message',
                                              'operation conflicts with new base'),
                    'operation_ids': [operation['id']],
                    'term_iris': [operation['target_iri']],
                })
        return errors, warnings, info

    @staticmethod
    def _overlay_dependency_issues(ontology):
        declared = ontology.classes | ontology.relations | ontology.attributes
        structural = {
            RDF.type, RDFS.subClassOf, RDFS.domain, RDFS.range,
            OWL.deprecated,
            URIRef('http://purl.org/dc/terms/isReplacedBy'),
        }
        warnings = []
        for subject, predicate, value in ontology.graph:
            if (subject not in declared or value not in declared
                    or not ontology.is_active_term(subject)
                    or ontology.is_active_term(value)
                    or predicate in structural
                    or str(predicate).startswith('http://www.w3.org/ns/shacl#')):
                continue
            warnings.append({
                'code': 'active_custom_annotation_dependency',
                'severity': 'warning',
                'message': (
                    f'active term references deprecated term through {predicate}'),
                'operation_ids': [],
                'term_iris': [str(subject), str(value)],
            })
        info = [{
            'code': 'deprecated_term_structure_retained',
            'severity': 'info',
            'message': 'deprecated term definition remains available for restoration',
            'operation_ids': [], 'term_iris': [str(term)],
        } for term in sorted(declared, key=str)
            if not ontology.is_active_term(term)]
        return warnings, info

    @staticmethod
    def _extend_unique(target, additions):
        existing = {_canonical_json(item) for item in target}
        for item in additions:
            encoded = _canonical_json(item)
            if encoded not in existing:
                target.append(item)
                existing.add(encoded)

    def _compute_validation(self, project_id, draft):
        operations = self._active_operations(project_id, draft['id'])
        errors, warnings, info = self._operation_issues(operations)
        candidate_turtle = self._base_turtle(project_id, draft['base_ontology_id'])
        graph_report = {'conforms': True, 'errors': []}
        try:
            candidate_turtle = apply_operations(candidate_turtle, [
                _semantic_operation(row) for row in operations
                if (row.get('validation') or {}).get('rebase_status') != 'conflict'
                and not (row.get('validation') or {}).get('errors')])
            candidate_ontology = Ontology(candidate_turtle)
            graph_report = validate_ontology_invariants(candidate_ontology)
            errors.extend(graph_report.get('errors') or [])
            overlay_warnings, overlay_info = self._overlay_dependency_issues(
                candidate_ontology)
            self._extend_unique(warnings, overlay_warnings)
            self._extend_unique(info, overlay_info)
        except ValueError as exc:
            issue = {'code': 'graph_integrity', 'severity': 'error',
                     'message': str(exc), 'operation_ids': [], 'term_iris': []}
            graph_report = {'conforms': False, 'errors': [issue]}
            errors.append(issue)
        historical = {'checked_records': 0, 'nonconforming_records': 0,
                      'errors': []}
        prospective = {'conforms': not errors, 'errors': list(errors)}
        if not errors:
            records = self.repository.current_records(project_id, vectors='none')
            formal = [row for row in records
                      if row.get('kind') in {'entity', 'relation', 'attribute'}]
            historical['checked_records'] = len(formal)
            if formal:
                historical_report = Ontology(candidate_turtle).validate_timeline(formal)
                if not historical_report.get('conforms'):
                    historical['nonconforming_records'] = len(
                        historical_report.get('errors') or [])
                    historical['errors'] = historical_report.get('errors') or []
                    info.append({
                        'code': 'historical_impact', 'severity': 'info',
                        'message': 'historical records do not satisfy the prospective ontology',
                        'operation_ids': [], 'term_iris': [],
                    })
        report = {
            'rule_version': self.validation_rule_version,
            'graph_integrity': graph_report,
            'prospective_new_write_contract': prospective,
            'historical_impact': historical,
            'errors': errors, 'warnings': warnings, 'info': info,
            'conforms': not errors,
        }
        source_snapshot = self._source_snapshot(project_id, draft)
        fingerprint = _fingerprint({
            'rule_version': self.validation_rule_version,
            'base_ontology_id': draft['base_ontology_id'],
            'source_versions': source_snapshot,
            'operation_fingerprints': [row['fingerprint'] for row in operations],
            'report': report,
        })
        return report, fingerprint, candidate_turtle

    def validate(self, project_id, draft_id, expected_revision):
        draft = self.store.get(project_id, draft_id)
        self._assert_revision(draft, expected_revision)
        _status_guard(draft, OPEN_STATES, '重新校验')
        self._check_current(project_id, draft, expected_revision)
        with self.repository._transaction():
            draft = self.store.get(project_id, draft_id)
            self._assert_revision(draft, expected_revision)
            self._check_current(project_id, draft, expected_revision)
            report, fingerprint, _ = self._compute_validation(project_id, draft)
            # validated_at＝“校验通过”的落点（迁移 0004）：待处理的草案、校验无阻断错误才写入。
            # A3 之前这里认 submitted（状态里的一档）；现在 submitted 并进 pending，
            # 判据自然变成「待处理」—— 也就是说"提交过没有"由 validated_at 与
            # 变更决定共同回答，不再靠一个状态枚举。
            validated = draft['status'] in PENDING_STATES and report['conforms']
            updated = self._cas(project_id, draft_id, expected_revision, {
                'validation_report': report,
                'validation_fingerprint': fingerprint,
                'validated_at': utc_now() if validated else None,
            })
        return self._preview(project_id, updated)

    def submit(self, project_id, draft_id, expected_revision):
        draft = self.store.get(project_id, draft_id)
        self._assert_revision(draft, expected_revision)
        _status_guard(draft, EDITABLE_STATES, '提交审核')
        self._check_current(project_id, draft, expected_revision)
        with self.repository._transaction():
            draft = self.store.get(project_id, draft_id)
            self._assert_revision(draft, expected_revision)
            _status_guard(draft, EDITABLE_STATES, '提交审核')
            self._check_current(project_id, draft, expected_revision)
            operations = self._active_operations(project_id, draft_id)
            active = [row for row in operations
                      if (row.get('validation') or {}).get('rebase_status') != 'no-op']
            # 回退草案可以**零变更**：它的内容就是那一版的结构，"回退"本身就是这次提交的
            # 全部意图（与 git revert 同构：产生一个新版本，而树等于旧版本）。
            # 下方那条门禁防的是"提交一份什么都没做的草案"；回退不需要再另外改一笔，
            # 而且它的代价已经由 revert 警告 + 勾选确认担了起来。
            if not active and not self._revert_base(project_id, draft):
                raise ValidationFailed(
                    'a draft must contain at least one effective operation')
            report, fingerprint, _ = self._compute_validation(project_id, draft)
            current_ids = {row['id'] for row in active}
            _, decision_rows = self._history(project_id, draft_id)
            retained = {
                row['operation_id']: row
                for row in self._effective_decisions(decision_rows)
                if row['operation_id'] in current_ids
                and row['action'] in {'approve', 'reject'}
                and row['operation_fingerprint'] == next(
                    (operation['fingerprint'] for operation in active
                     if operation['id'] == row['operation_id']), None)}
            # A3：提交**不改状态**（草案仍然是「待处理」）。提交做的事只有一件 ——
            # 冻结这一轮的快照：写报告与指纹、并要求重新校验（validated_at 清空）。
            # 旧代码在这里根据已保留的决定把状态推到 reviewed / closed，那是把
            # "审核走到哪一步"记进状态；现在它由 pending_phase() 按决定实时推出来。
            updated = self._cas(project_id, draft_id, expected_revision, {
                'validation_report': report,
                'validation_fingerprint': fingerprint,
                'validated_at': None,
                # 「已提交」= 这个时间戳（不是状态，也不是 validated_at：
                # 提交本身就会把上一次的"校验通过"作废）。
                'submitted_at': utc_now(),
            })
        return self._preview(project_id, updated)

    # ---------------------------------------------------------------- decisions
    @staticmethod
    def _warning_codes(report):
        return {row.get('code') for row in (report.get('warnings') or [])
                if row.get('code')}

    def _require_validation(self, project_id, draft, supplied_fingerprint,
                            acknowledged_warning_codes, *, allow_errors=False):
        report, current, turtle = self._compute_validation(project_id, draft)
        if (not supplied_fingerprint
                or supplied_fingerprint != current
                or draft.get('validation_fingerprint') != current):
            raise ValidationChanged(
                'validation snapshot changed; validate again', details={
                    'validation_fingerprint': current, 'report': report})
        if not report['conforms'] and not allow_errors:
            raise ValidationFailed('draft validation failed', details={'report': report})
        acknowledged = set(acknowledged_warning_codes or [])
        missing = self._warning_codes(report) - acknowledged
        if missing:
            raise ValidationFailed(
                'unacknowledged warnings: ' + ', '.join(sorted(missing)),
                details={'warning_codes': sorted(missing), 'report': report})
        return report, current, turtle

    def decide(self, project_id, draft_id, expected_revision,
               expected_ontology_id, validation_fingerprint, decisions,
               acknowledged_warning_codes, actor, *, assisted_batch=False):
        draft = self.store.get(project_id, draft_id)
        self._assert_revision(draft, expected_revision)
        _status_guard(draft, REVIEW_STATES, '做审核决定')
        # A3：状态里没有"已提交"这一档了，但"提交审核"仍然是一道门 ——
        # 没提交过的请求没有冻结的变更集合，直接收下等于把流程里的一步删掉。
        _require_submitted(draft, '做审核决定')
        self._check_current(
            project_id, draft, expected_revision,
            expected_ontology_id=expected_ontology_id)
        actor = _text(actor, 'actor')
        proposed = _as_list(decisions, 'decisions')
        if not proposed or len(proposed) > 100:
            raise BatchNotAllowed('a decision request must contain 1..100 decisions')
        report, _, turtle = self._require_validation(
            project_id, draft, validation_fingerprint,
            acknowledged_warning_codes, allow_errors=True)
        operations = {row['id']: row for row in self._active_operations(
            project_id, draft_id)}
        _, previous_rows = self._history(project_id, draft_id)
        latest_decisions = {
            row['operation_id']: row
            for row in self._effective_decisions(previous_rows)}
        previous = {
            row['operation_id']: row
            for row in latest_decisions.values()
            if row.get('action') in {'approve', 'reject'}
            and row.get('operation_id') in operations
            and row.get('operation_fingerprint') == operations[
                row['operation_id']]['fingerprint']
        }
        blocked_ids = {
            operation_id
            for issue in report.get('errors') or []
            for operation_id in issue.get('operation_ids') or []}
        has_unscoped_errors = any(
            not (issue.get('operation_ids') or [])
            for issue in report.get('errors') or [])
        warning_codes = self._warning_codes(report)
        if len(proposed) > 1:
            # 批量决定有两条通道，判据都在服务端：
            # ① 默认（严格）：只允许「服务端判定低风险 + 草案无警告」的批准；
            # ② assisted_batch=True（工作台「一键批准」）：允许「可批准且不需要写理由」的
            #    中风险变更 —— 阻断项、高风险项、草案带警告时的全部变更仍然一条都不放行。
            # 任何一条不满足就整批拒绝，绝不产生"批了一半"的决定。
            def batch_allowed(item):
                operation = operations.get(item.get('operation_id'))
                if item.get('action') != 'approve' or operation is None:
                    return False
                if not assisted_batch:
                    return (is_batch_eligible(operation, ontology=turtle)
                            and not warning_codes)
                if operation['id'] in blocked_ids or has_unscoped_errors:
                    return False
                return not warning_codes and operation.get('risk') != 'high'

            if not all(batch_allowed(item) for item in proposed):
                raise BatchNotAllowed(
                    '批量决定包含不可批量的变更：阻断项、高风险项或需要写理由的变更必须逐条处理')
        saved = []
        for item in proposed:
            if not isinstance(item, dict):
                raise ValueError('each decision must be an object')
            operation = operations.get(item.get('operation_id'))
            if operation is None:
                raise ValueError('decision operation is not current')
            if item.get('operation_fingerprint') != operation['fingerprint']:
                raise ValidationChanged('decision operation fingerprint changed')
            action = item.get('action')
            if action not in {'approve', 'reject', 'request_changes'}:
                raise ValueError('unsupported decision action')
            if (action == 'approve'
                    and (operation['id'] in blocked_ids or has_unscoped_errors)):
                raise ValidationFailed(
                    'blocking operations cannot be approved',
                    details={'report': report, 'operation_id': operation['id']})
            reason = item.get('reason')
            if action in {'reject', 'request_changes'}:
                reason = _text(reason, 'reason')
            if action == 'approve' and operation['risk'] == 'high':
                reason = _text(reason, 'reason')
            if action == 'approve' and warning_codes:
                reason = _text(reason, 'reason')
            saved.append({
                'id': item.get('id') or str(uuid4()),
                'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'],
                'action': action, 'reason': reason, 'actor': actor,
                'supersedes_decision_id': (
                    latest_decisions.get(operation['id']) or {}).get('id'),
            })
        with self.repository._transaction():
            locked = self.store.get(project_id, draft_id)
            self._assert_revision(locked, expected_revision)
            _status_guard(locked, REVIEW_STATES, '做审核决定',
                          '先在「设计」阶段点「提交审核」冻结变更集合。')
            self._check_current(
                project_id, locked, expected_revision,
                expected_ontology_id=expected_ontology_id)
            self._require_validation(
                project_id, locked, validation_fingerprint,
                acknowledged_warning_codes, allow_errors=True)
            # A3：这条保护以前挂在 reviewed 状态上（"已审核的草案不能再改决定，要重审先要求调整"）。
            # 状态收窄成三种之后由**推导**承担：每条活跃变更都已有当前决定 ＝ 这份请求已经
            # 可以收下了，此时再改决定必须先「退回继续编辑」（request_changes），否则就是
            # 绕过"冻结快照"那道门（改一下变更、旧决定照样有效）。
            locked_operations = self._active_operations(project_id, draft_id)
            _, locked_rows = self._history(project_id, draft_id)
            locked_decisions = {
                row['operation_id']: row
                for row in self._effective_decisions(locked_rows)}
            settled = bool(locked_operations) and all(
                (row := locked_decisions.get(operation['id'])) is not None
                and row.get('action') in {'approve', 'reject'}
                and row.get('operation_fingerprint') == operation['fingerprint']
                for operation in locked_operations)
            if settled and not any(
                    item.get('action') == 'request_changes' for item in proposed):
                raise DraftImmutable(
                    '全部变更都已经有决定了，不能做审核决定；要重审请先在「本体建模层」'
                    '点「退回继续编辑」，改完重新提交并校验。',
                    details={'status': locked['status'], 'pending_phase': 'settled',
                             'draft_id': draft_id})
            self.store.append_decisions(project_id, draft_id, saved)
            combined = {**previous}
            combined.update({
                row['operation_id']: row for row in saved
                if row['action'] in {'approve', 'reject'}})
            # A3：决定不再把状态在 editing/submitted/reviewed 之间推来推去，只有
            # **一种决定会改状态**：每条变更都被驳回 → 这份请求就是「已驳回」（终态）。
            # 其余（退回继续编辑、部分决定、全部决定完待收下）都还是「待处理」，
            # 到底处在哪一步由 pending_phase() 按决定实时推出来。
            request_changes = any(row['action'] == 'request_changes' for row in saved)
            if request_changes:
                status = 'pending'
            elif all(op_id in combined for op_id in operations) and all(
                    combined[op_id]['action'] == 'reject' for op_id in operations):
                status = 'rejected'
            else:
                status = 'pending'
            updated = self._cas(project_id, draft_id, expected_revision, {
                'status': status,
                # 退回继续编辑＝这一轮的校验作废（改了东西就不能拿旧报告去发布）
                **({'validation_report': None, 'validation_fingerprint': None,
                    'submitted_at': None} if request_changes else {}),
            })
        return self._preview(project_id, updated)

    def close(self, project_id, draft_id, expected_revision, actor, reason):
        actor, reason = _text(actor, 'actor'), _text(reason, 'reason')
        with self.repository._transaction():
            draft = self.store.get(project_id, draft_id)
            self._assert_revision(draft, expected_revision)
            _status_guard(draft, EDITABLE_STATES, '驳回',
                          '只有「待处理」的请求可以驳回；已收下/已驳回的是只读记录。')
            context = dict(draft.get('source_context') or {})
            context['closure'] = {'actor': actor, 'reason': reason}
            closed = self._cas(project_id, draft_id, expected_revision, {
                'status': 'rejected', 'source_context': context})
            effects = context.get('publication_effects') or {}
            run_id = effects.get('discovery_run_id')
            if draft.get('source_kind') == 'discovery' and run_id:
                run = self.repository.get_discovery_run(project_id, run_id)
                resolved_ids = {
                    outcome['candidate_id']
                    for outcome in run.get('candidate_outcomes') or []
                }
                terminal = [{
                    'candidate_id': binding['candidate_id'],
                    'status': 'skipped',
                    'reason_code': 'draft_closed',
                } for binding in run.get('candidate_bindings') or []
                    if binding['candidate_id'] not in resolved_ids]
                self.repository.transition_discovery_run(
                    # 注意：这里是**发现运行**的状态（词表见 discovery_run_store.RUN_STATUSES），
                    # 跟草案的三种状态不是一套 —— 驳回请求对运行而言就是"关闭"。
                    project_id, run_id, 'draft_created', 'closed',
                    candidate_outcomes=terminal)
            return closed

    # ------------------------------------------------------------------ rebase
    @staticmethod
    def _operation_is_noop(ontology, operation):
        graph = ontology.graph
        target = URIRef(operation['target_iri'])
        action = operation['action']
        before, after = operation.get('before') or {}, operation.get('after') or {}
        if action == 'create_term':
            declarations = {
                'class': {OWL.Class, RDFS.Class},
                'relation': {OWL.ObjectProperty},
                'attribute': {OWL.DatatypeProperty},
            }.get(after.get('kind'), set())
            return any((target, RDF.type, kind) in graph for kind in declarations)
        predicates = {
            'parent': RDFS.subClassOf, 'domain': RDFS.domain, 'range': RDFS.range}
        for suffix, predicate in predicates.items():
            if action == f'add_{suffix}':
                return (target, predicate, URIRef(after.get('value', ''))) in graph
            if action == f'remove_{suffix}':
                return (target, predicate, URIRef(before.get('value', ''))) not in graph
        if action == 'add_annotation':
            value = after.get('value')
            node = URIRef(value) if after.get('type') == 'iri' else Literal(
                value, lang=after.get('language'),
                datatype=URIRef(after['datatype']) if after.get('datatype') else None)
            return (target, URIRef(after.get('predicate', '')), node) in graph
        if action == 'remove_annotation':
            value = before.get('value')
            node = URIRef(value) if before.get('type') == 'iri' else Literal(
                value, lang=before.get('language'),
                datatype=URIRef(before['datatype']) if before.get('datatype') else None)
            return (target, URIRef(before.get('predicate', '')), node) not in graph
        if action == 'retire_term':
            return target in (ontology.classes | ontology.relations | ontology.attributes) \
                and not ontology.is_active_term(target)
        if action == 'restore_term':
            return ontology.is_active_term(target)
        if action == 'set_datatype':
            values = {str(value) for value in graph.objects(target, RDFS.range)}
            requested = after.get('datatype')
            return values == ({requested} if requested else set())
        return False

    @staticmethod
    def _rebase_before(ontology, operation):
        """Rebuild graph-derived preconditions while retaining removal intent."""
        action = operation['action']
        target = URIRef(operation['target_iri'])
        old = operation.get('before') or {}
        if action in {'remove_parent', 'remove_domain', 'remove_range'}:
            return {'value': old.get('value')}
        if action == 'remove_annotation':
            return {key: old[key] for key in (
                'predicate', 'value', 'type', 'language', 'datatype')
                    if key in old}
        if action == 'set_datatype':
            values = sorted(
                (str(value) for value in ontology.graph.objects(target, RDFS.range)))
            return {'datatype': values[0] if len(values) == 1 else None}
        if action == 'advanced_rdf_patch':
            # The old fragment is the semantic removal intent for a scoped
            # patch; apply_operations will classify it as a conflict if the
            # latest graph no longer matches that precondition.
            return json.loads(_canonical_json(old))
        return None

    def rebase(self, project_id, draft_id, expected_revision,
               expected_ontology_id):
        draft = self.store.get(project_id, draft_id)
        self._assert_revision(draft, expected_revision)
        _status_guard(draft, OPEN_STATES, '重新基线')
        latest = self._latest_id(project_id)
        if expected_ontology_id != latest:
            raise StaleBase('rebase target must be the latest ontology', details={
                'expected_ontology_id': expected_ontology_id,
                'current_ontology_id': latest})
        # 「要不要重新基线」是推导出来的（基线落后 / 来源快照变了），不是状态。
        if not self.needs_rebase(project_id, draft):
            raise ValueError('rebase requires a stale_base or stale_source draft')
        old_operations = self._active_operations(project_id, draft_id)
        turtle = self._base_turtle(project_id, latest)
        replacements = []
        classifications = []
        source_snapshot_fingerprint = None
        if draft['source_kind'] in {'discovery', 'candidate'}:
            refreshed_context = self._refresh_source_context(
                project_id, draft.get('source_context') or {})
            source_snapshot_fingerprint = _fingerprint(refreshed_context)
        rebuild_draft = dict(draft)
        if source_snapshot_fingerprint is not None:
            rebuild_draft['source_context'] = refreshed_context
        for operation in old_operations:
            ontology = Ontology(turtle)
            old_validation = operation.get('validation') or {}
            fresh_validation = {}
            if source_snapshot_fingerprint is not None:
                fresh_validation['source_snapshot_fingerprint'] = (
                    source_snapshot_fingerprint)
            rebuild_command = {
                'confidence': old_validation.get('confidence'),
                'reason': operation.get('reason'),
            }
            if source_snapshot_fingerprint is None:
                rebuild_command['evidence_refs'] = operation.get('evidence') or []
            else:
                rebuild_command['evidence_refs'] = self._refresh_operation_evidence(
                    draft, rebuild_draft, operation.get('evidence') or [])
            rebuilt = self._rebuild_operations(
                project_id, rebuild_draft, [{
                    'action': operation['action'],
                    'target_iri': operation['target_iri'],
                    'before': self._rebase_before(ontology, operation),
                    'after': json.loads(_canonical_json(operation.get('after'))),
                    'validation': fresh_validation,
                }], ontology, rebuild_command)[0]
            if self._operation_is_noop(ontology, operation):
                status, message = 'no-op', 'operation is already true on latest base'
            else:
                try:
                    if not (rebuilt.get('validation') or {}).get('errors'):
                        turtle = apply_operations(turtle, [rebuilt])
                    status, message = 'clean', None
                except ValueError as exc:
                    status, message = 'conflict', str(exc)
            classifications.append({'operation_id': operation['id'],
                                    'classification': status,
                                    'message': message})
            if (status == 'clean' and rebuilt is not None
                    and rebuilt['fingerprint'] == operation['fingerprint']):
                # An unchanged semantic operation remains byte-for-byte stable,
                # preserving its decisions as required.
                continue
            if status == 'clean':
                replacement = rebuilt
            else:
                replacement = rebuilt
                validation = {**(replacement.get('validation') or {}),
                              'rebase_status': status}
                if message:
                    validation['rebase_message'] = message
                replacement['validation'] = validation
                replacement['fingerprint'] = operation_fingerprint(
                    _semantic_operation(replacement))
            replacement.update({
                'id': str(uuid4()),
                'supersedes_operation_id': operation['id']})
            replacements.append(replacement)
        context = dict(draft.get('source_context') or {})
        if draft['source_kind'] in {'discovery', 'candidate'}:
            context = refreshed_context
        with self.repository._transaction():
            locked = self.store.get(project_id, draft_id)
            self._assert_revision(locked, expected_revision)
            if self._latest_id(project_id) != expected_ontology_id:
                raise StaleBase('rebase target is no longer latest')
            if locked['source_kind'] in {'discovery', 'candidate'}:
                locked_context = self._refresh_source_context(
                    project_id, locked.get('source_context') or {})
                if _canonical_json(locked_context) != _canonical_json(context):
                    raise StaleSource('source changed while rebasing; retry')
            if replacements:
                self.store.append_operations(project_id, draft_id, replacements)
            updated = self._cas(project_id, draft_id, expected_revision, {
                'base_ontology_id': latest, 'status': 'pending',
                'source_context': context,
                'validation_report': None, 'validation_fingerprint': None,
                'submitted_at': None,
            })
        result = self._preview(project_id, updated)
        result['rebase'] = classifications
        return result


__all__ = [
    'OntologyDrafts', 'OntologyDraftError', 'RevisionConflict', 'StaleBase',
    'StaleSource', 'ValidationChanged', 'ValidationFailed', 'BatchNotAllowed',
    'DraftImmutable', 'VALIDATION_RULE_VERSION',
]
