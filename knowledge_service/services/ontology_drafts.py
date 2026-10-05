"""Governed ontology draft lifecycle and read-side graph projections.

This module is deliberately the domain seam above ``OntologyDraftStore``.  It
owns state transitions, server-side operation compilation, validation snapshots,
review policy and publication preflight.  The final atomic publication callback
is supplied by the repository layer (Task 5); this service never assembles a
partial publication transaction itself.
"""

from __future__ import annotations

import base64
import hashlib
import json
from uuid import uuid4

from rdflib import Literal, RDF, RDFS, URIRef
from rdflib.namespace import OWL

from ..repository.ontology_draft_store import OntologyDraftConflict
from ..core.time import utc_now
from .ontology import Ontology, local_name, term_impact
from .ontology_operations import (
    apply_operations,
    build_operation,
    build_restore_operation,
    canonical_turtle_diff,
    is_batch_eligible,
    operation_fingerprint,
    retirement_dependencies,
    validate_ontology_invariants,
)


VALIDATION_RULE_VERSION = 'ontology-drafts/1'
PLAN_POLICY_VERSION = 'ontology-review-plan/1'
# Actions whose review can never be batched away: they change or restore the
# meaning of a published term.  Kept here (not in the front end) so the policy
# has exactly one owner.
IRREVERSIBLE_ACTIONS = frozenset({
    'retire_term', 'restore_term', 'advanced_rdf_patch'})
# ── A3：请求只有三种状态（迁移 0005_draft_status_three_states.sql）─────────
# 「本体」没有状态，只有版本号 v1..vN —— 「已发布」不是状态，是它唯一的存在方式。
# 状态属于**请求**：待处理 → 已收下 / 已驳回。
# 旧词汇的去向（迁移里已经搬过）：editing / submitted / reviewed / stale_base /
# stale_source → pending；published → accepted；closed → rejected。
PENDING_STATES = frozenset({'pending'})
ACCEPTED_STATES = frozenset({'accepted'})
REJECTED_STATES = frozenset({'rejected'})
# 终态（收下/驳回）= 只读账本：任何写操作一律中文拒绝（见 _status_guard）。
FINAL_STATES = ACCEPTED_STATES | REJECTED_STATES
# 「待处理」期间可以继续改。收窄状态**不会削弱任何保护**，因为另外两件事各自
# 有落点、不靠状态记：
#   * 「已经提交」（冻结快照）→ validated_at + validation_fingerprint（迁移 0004）；
#   * 「逐条已有决定」→ decisions 的关系表，按 operation_fingerprint 匹配；
#   * 「要不要重新基线」→ 推导出来的事实（needs_rebase()），不是状态。
EDITABLE_STATES = PENDING_STATES
REVIEW_STATES = PENDING_STATES
OPEN_STATES = PENDING_STATES
# 「回到某一版」（版本管理里的回退）用这个 source_kind 建草案：它的基线**故意**
# 不是最新版。它与"基线过期的普通草案"是两件事 —— 后者要拒绝（在旧基线上做普通
# 编辑会静默丢掉别人后来的改动），前者是用户明说的意图，且发布时留下版本记录。
REVERT_SOURCE = 'revert'
# 回退必须由人确认的那条警告码。回退会让当前版本的结构变成旧版本的结构，
# 中间几版的结构改动不再留在当前版本里（那些版本本身仍在版本管理里可查、可再回去）。
# 复用的是发布警告的确认通道（acknowledged_warning_codes），门禁落在 publish_preflight
# —— 与 SHACL 警告同一处裁决，不另开一套确认机制。
REVERT_WARNING = 'revert_drops_later_versions'
# 「已经提交过」= 这份请求已经进过审核：待处理与终态都能回看逐项决策（那是那次审核的记录）。
SUBMITTED_STATES = PENDING_STATES | FINAL_STATES
NEIGHBORHOOD_LINK_LIMIT = 100


class OntologyDraftError(ValueError):
    """Stable domain error intended for later HTTP error mapping."""

    code = 'ontology_draft_error'

    def __init__(self, message, *, details=None):
        self.details = dict(details or {})
        super().__init__(message)


class RevisionConflict(OntologyDraftError):
    code = 'revision_conflict'


class StaleBase(OntologyDraftError):
    code = 'stale_base'


class StaleSource(OntologyDraftError):
    code = 'stale_source'


class ValidationChanged(OntologyDraftError):
    code = 'validation_changed'


class ValidationFailed(OntologyDraftError):
    code = 'validation_failed'


class BatchNotAllowed(OntologyDraftError):
    code = 'batch_not_allowed'


class DraftImmutable(OntologyDraftError):
    """草案已经走到终态（已发布 / 已关闭），写操作一律不再允许。

    以前这些位置抛的是英文内部断言（"decisions require a submitted draft"、
    "only editing drafts may be submitted"），前端原样弹给用户，于是"发布之后
    点回去"看到的就是一堆看不懂的报错。现在统一成中文错误：说清当前状态、
    不能做什么、下一步该点哪里。
    """

    code = 'draft_immutable'


_STATUS_LABEL = {
    'pending': '待处理', 'accepted': '已收下', 'rejected': '已驳回',
}

# 状态 → 一句话"现在能做什么"。这段文案以前只活在前端 STATUS_GUIDE 里，
# 等于状态的含义有两个出处（服务端的 status + 前端的解释）；统一放这里，
# 前端只渲染 stage_state() 给的结果。
_STATUS_COPY = {
    'pending': '待处理：可以继续改（「本体建模层」）；也可以到收件箱逐条收下或不收。'
               '改完提交后请重跑一次校验，再逐条处理。',
    'accepted': '已收下：这份请求的变更已经写进本体，成为不可变的新版本；'
                '要再改动请基于它新建一份草案。',
    'rejected': '已驳回：这份请求不会产生本体版本。用到它的知识不会被丢掉，'
                '而是标成「结构待定」，等本体补齐后自动解除。',
}


def _status_guard(draft, allowed, action, next_step=''):
    """写操作的状态门禁：终态一律中文拒绝，状态不符时给出下一步。

    allowed 是允许执行该操作的状态集合（EDITABLE_STATES / REVIEW_STATES），
    action 写"用户想做的事"，next_step 写"接下来点哪里"。
    """
    status = draft['status']
    if status in FINAL_STATES:
        fate = '收下（写进本体了）' if status == 'accepted' else '驳回'
        raise DraftImmutable(
            f'这个草案已经{fate}，不能再{action}。'
            f'要调整本体，请到「设计」阶段点「基于当前版本新建草案」，在新草案里改；'
            f'已发布的版本永远保留，不会被覆盖。',
            details={'status': status, 'draft_id': draft['id']})
    if status not in allowed:
        label = _STATUS_LABEL.get(status, status)
        raise DraftImmutable(
            f'草案当前是「{label}」，不能{action}。{next_step}'.rstrip(),
            details={'status': status, 'draft_id': draft['id']})


def _require_submitted(draft, action):
    """「提交审核」这道门：A3 之后它是**时间戳事实**（submitted_at），不再是状态。

    为什么还要留着这道门：提交是"冻结这一轮变更集合"的动作（写校验快照与指纹、
    作废上一次的校验结果）。状态收窄时如果把它一起丢掉，任何一份**从未提交**的
    草案都能直接逐条收下并发布 —— 那等于把流程里的一步悄悄删掉，而不是把状态搬家。
    所以判据从"状态 == submitted"换成"submitted_at 有值"，拒绝时的文案与下一步
    都写清楚（用户不用猜）。
    """
    if not draft.get('submitted_at'):
        raise DraftImmutable(
            f'这份请求还没有提交过，不能{action}。'
            '先在「本体建模层」点「提交审核」把这一轮变更冻结（提交会写校验快照），'
            '再回「本体建模层」逐条收下或不收。',
            details={'status': draft['status'], 'draft_id': draft['id']})


def _canonical_json(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(',', ':'))


def _fingerprint(value) -> str:
    return hashlib.sha256(_canonical_json(value).encode('utf-8')).hexdigest()


def _semantic_operation(operation):
    """Remove append-only ledger columns before invoking the compiler seam."""
    return {key: value for key, value in operation.items()
            if key not in {'id', 'project_id', 'draft_id', 'created_at',
                           'supersedes_operation_id'}}


# E1「标注/结构分开计量」：改注释不该刷版本号。
#
# 判据刻意用**本次草案的操作**，而不是去比对两版 Turtle 的指纹：
#   * 操作里只要出现一个结构动作（加类/加关系/改父类/停用…），就是结构变更 → 版本号 +1；
#   * 全是标注动作，说明图结构一个字节都没动 → 复用当前版本号。
# 比对指纹要先解析 Turtle、还要维护"哪些谓词算标注"的白名单，白名单一漏就会把
# 结构变更误判成标注变更（那才是危险的：结构变了却不换版本号）。用操作判据是**保守**的：
# 拿不准就归到结构变更，宁可多一个版本号，不可少一个。
ANNOTATION_ACTIONS = frozenset({'add_annotation', 'remove_annotation'})


def is_annotation_only(operations) -> bool:
    """这批操作是否只动标注（于是发布时复用当前版本号）。

    空操作集返回 False —— 没有操作谈不上"只动标注"，让它走正常的 +1 路径。
    """
    items = [op for op in (operations or []) if isinstance(op, dict)]
    if not items:
        return False
    return all(op.get('action') in ANNOTATION_ACTIONS for op in items)


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a non-empty string')
    return value.strip()


def _as_list(value, field):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError(f'{field} must be a list')
    return list(value)


class OntologyDrafts:
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
    def _operation_args(command):
        action = command.get('action')
        before = command.get('before')
        after = command.get('after')
        if action == 'create_term':
            after = after if after is not None else {'kind': command.get('kind')}
        elif action in {'add_parent', 'remove_parent'}:
            value = command.get('parent_iri', command.get('value'))
            if action.startswith('add_'):
                after = after if after is not None else {'value': value}
            else:
                before = before if before is not None else {'value': value}
        elif action in {'add_domain', 'remove_domain', 'add_range', 'remove_range'}:
            value = command.get('value') or command.get(action.split('_', 1)[1] + '_iri')
            if action.startswith('add_'):
                after = after if after is not None else {'value': value}
            else:
                before = before if before is not None else {'value': value}
        elif action in {'add_annotation', 'remove_annotation'}:
            spec = {
                'predicate': command.get('predicate'), 'value': command.get('value'),
                'language': command.get('language'), 'datatype': command.get('datatype'),
            }
            if command.get('value_type'):
                spec['type'] = command['value_type']
            spec = {key: value for key, value in spec.items() if value is not None}
            if action == 'add_annotation':
                after = after if after is not None else spec
            else:
                before = before if before is not None else spec
        elif action == 'set_datatype':
            after = after if after is not None else {
                'datatype': command.get('datatype')}
        return action, before, after

    @staticmethod
    def _annotation_spec(predicate, value):
        spec = {'predicate': str(predicate), 'value': str(value)}
        if isinstance(value, URIRef):
            spec['type'] = 'iri'
        else:
            spec['language'] = value.language
            spec['datatype'] = str(value.datatype) if value.datatype else None
        return spec

    def _expand_restore(self, operation, ontology):
        """Keep activation atomic while making selected definition edges reviewable."""
        after = operation['after']
        selected = list(after['selected_fields'])
        template = after['template']
        activation = json.loads(_canonical_json(operation))
        activation['after']['activation_only'] = True
        activation['fingerprint'] = operation_fingerprint(activation)
        target = URIRef(operation['target_iri'])
        raw = [activation]

        if 'annotations' in selected:
            structural = {
                RDF.type, RDFS.subClassOf, RDFS.domain, RDFS.range,
                OWL.deprecated,
            }
            current = {
                _canonical_json(self._annotation_spec(predicate, value)):
                self._annotation_spec(predicate, value)
                for predicate, value in ontology.graph.predicate_objects(target)
                if predicate not in structural
            }
            desired = {
                _canonical_json(spec): spec
                for spec in template.get('annotations', [])}
            raw.extend({
                'action': 'remove_annotation', 'target_iri': str(target),
                'before': current[key],
            } for key in sorted(set(current) - set(desired)))
            raw.extend({
                'action': 'add_annotation', 'target_iri': str(target),
                'after': desired[key],
            } for key in sorted(set(desired) - set(current)))

        predicates = {
            'parents': ('parent', RDFS.subClassOf),
            'domain': ('domain', RDFS.domain),
            'range': ('range', RDFS.range),
        }
        for field, (suffix, predicate) in predicates.items():
            if field not in selected:
                continue
            if predicate in {RDFS.domain, RDFS.range}:
                current_values = {
                    str(value) for value in ontology.constraint_types(target, predicate)}
            else:
                current_values = {
                    str(value) for value in ontology.graph.objects(target, predicate)}
            desired_values = set(template.get(field, []))
            raw.extend({
                'action': f'remove_{suffix}', 'target_iri': str(target),
                'before': {'value': value},
            } for value in sorted(current_values - desired_values))
            raw.extend({
                'action': f'add_{suffix}', 'target_iri': str(target),
                'after': {'value': value},
            } for value in sorted(desired_values - current_values))

        if 'datatype' in selected:
            current_values = list(ontology.graph.objects(target, RDFS.range))
            current = str(current_values[0]) if len(current_values) == 1 else None
            desired = template.get('datatype')
            if current != desired:
                raw.append({
                    'action': 'set_datatype', 'target_iri': str(target),
                    'before': {'datatype': current},
                    'after': {'datatype': desired},
                })
        return raw

    def _rebuild_operations(self, project_id, draft, operations, ontology, command):
        evidence = self._authoritative_evidence(draft, command)
        confidence = command.get('confidence')
        rebuilt = []
        working = Ontology(ontology.graph.serialize(format='turtle'))
        for raw in operations:
            action = raw['action']
            target = raw['target_iri']
            impact = {
                **self._impact(project_id, target, ontology=working),
                **(raw.get('impact') or {}),
            }
            validation = {
                key: value for key, value in (raw.get('validation') or {}).items()
                if key not in {'warnings', 'source', 'confidence'}
            }
            warnings = list((raw.get('validation') or {}).get('warnings') or [])
            if action == 'retire_term':
                dependency_report = retirement_dependencies(
                    working, target,
                    active_records=range(impact['formal_records']))
                impact['dependency_report'] = dependency_report
                validation['errors'] = dependency_report['errors']
                validation['info'] = dependency_report['info']
                warnings.extend(dependency_report['warnings'])
            item = build_operation(
                action, target, before=raw.get('before'), after=raw.get('after'),
                source=draft['source_kind'], impact=impact, warnings=warnings,
                confidence=confidence,
                evidence=evidence or raw.get('evidence') or [],
                validation=validation,
                reason=command.get('reason', raw.get('reason')),
                ontology=working)
            if draft['source_kind'] in {'turtle', 'discovery'} and item['risk'] == 'low':
                item['risk'] = 'medium'
                item['fingerprint'] = operation_fingerprint(item)
            rebuilt.append(item)
            try:
                applied = apply_operations(
                    working.graph.serialize(format='turtle'), [item])
            except ValueError:
                continue
            working = Ontology(applied)
        return rebuilt

    def _compile_command(self, project_id, draft, command, ontology):
        if not isinstance(command, dict):
            raise ValueError('command must be an object')
        action = command.get('action')
        if action in {'turtle', 'replace_turtle', 'diff_turtle'}:
            edited = command.get('edited_turtle', command.get('turtle'))
            if not isinstance(edited, str):
                raise ValueError('Turtle command requires edited_turtle')
            raw = canonical_turtle_diff(
                ontology.graph.serialize(format='turtle'), edited,
                published=draft['base_ontology_id'] is not None,
                base_ontology_id=draft['base_ontology_id'],
                source_ontology_id=command.get('source_ontology_id'),
                restore_operation_builder=lambda target_iri, source_ontology_id: (
                    build_restore_operation(
                        self.repository, project_id, target_iri,
                        source_ontology_id,
                        selected_fields=command.get('selected_fields') or [])))
            expanded = []
            for operation in raw:
                expanded.extend(
                    self._expand_restore(operation, ontology)
                    if operation['action'] == 'restore_term' else [operation])
            return self._rebuild_operations(
                project_id, draft, expanded, ontology, command)
        target = command.get('target_iri')
        if action == 'restore_term':
            selected = command.get('selected_fields', command.get('selection'))
            operation = build_restore_operation(
                self.repository, project_id, target,
                command.get('source_ontology_id'),
                selected_fields=selected or [])
            operation['reason'] = command.get('reason')
            operation['fingerprint'] = operation_fingerprint(operation)
            return self._rebuild_operations(
                project_id, draft, self._expand_restore(operation, ontology),
                ontology, command)
        action, before, after = self._operation_args(command)
        # Risk, warnings and fingerprints are always recomputed here.  Client
        # supplied values with those names are intentionally ignored.
        return self._rebuild_operations(project_id, draft, [{
            'action': action, 'target_iri': target,
            'before': before, 'after': after,
        }], ontology, command)

    def _impact(self, project_id, target_iri, *, ontology=None):
        ontology = ontology or Ontology(
            self._base_turtle(project_id, self._latest_id(project_id)))
        target = URIRef(target_iri)
        descendants = set()
        pending_nodes = [target]
        while pending_nodes:
            parent = pending_nodes.pop()
            for child in ontology.graph.subjects(RDFS.subClassOf, parent):
                if child not in descendants and ontology.is_active_term(child):
                    descendants.add(child)
                    pending_nodes.append(child)
        references = term_impact(self, project_id, target_iri, ontology)
        records = self.repository.current_records(project_id, vectors='none')
        direct = [
            row for row in records
            if row.get('type') == target_iri
            or target_iri in (row.get('properties') or {})]
        direct_ids = {row['id'] for row in direct}
        linked = [
            row for row in records
            if row.get('kind') == 'relation' and row['id'] not in direct_ids
            and (row.get('subject_id') in direct_ids
                 or row.get('object_id') in direct_ids)]
        affected_records = [*direct, *linked]
        constraint_details = [{
            'subject': str(subject), 'predicate': str(predicate),
        } for subject, predicate in ontology.graph.subject_predicates(target)]
        constraints = len(constraint_details)
        formal_records = len(affected_records)
        pending_ids = set()
        target_names = {target_iri, local_name(target_iri)}
        for row in records:
            metadata = row.get('metadata') or {}
            candidates = [*(metadata.get('review_candidates') or []),
                          *(metadata.get('discovery_candidates') or [])]
            for candidate in candidates:
                referenced_names = {
                    candidate.get(key)
                    for key in ('target_type', 'proposed_type', 'predicate')}
                if (candidate.get('status') == 'pending'
                        and target_names & referenced_names):
                    pending_ids.add(candidate.get('id') or _fingerprint(candidate))
        pending = len(pending_ids)
        referenced = bool(descendants or constraints or formal_records or pending)
        return {
            'formal_records': formal_records,
            'pending': pending,
            'descendants': len(descendants),
            'constraints': constraints,
            'leaf': not descendants,
            'referenced': referenced,
            'record_ids': [row['id'] for row in affected_records],
            'record_ids_truncated': False,
            'record_preview': references['record_preview'],
            'kind_counts': references['kind_counts'],
            'linked_relation_count': references['linked_relation_count'],
            'constraints_detail': constraint_details,
            'pending_candidate_ids': sorted(pending_ids),
            'classification_preview': {
                'records': references['kind_counts'],
                'descendants': len(descendants),
                'constraints': constraints,
                'pending_candidates': pending,
            },
        }

    def command(self, project_id, draft_id, expected_revision, command):
        with self.repository._transaction():
            draft = self.store.get(project_id, draft_id)
            self._assert_revision(draft, expected_revision)
            _status_guard(draft, EDITABLE_STATES, '修改本体',
                          '先在设计阶段点「提交审核」把变更集合冻结，再逐项审核。')
            self._check_current(project_id, draft, expected_revision)
            base_turtle, effective = self._ontology_for_draft(project_id, draft)
            ontology = Ontology(base_turtle)
            action = command.get('action') if isinstance(command, dict) else None
            supersedes = command.get(
                'supersedes_operation_id', command.get('operation_id'))
            if action == 'withdraw_operation':
                current = {row['id']: row for row in self._effective_operations(
                    project_id, draft_id)}
                withdrawn = current.get(supersedes)
                if withdrawn is None:
                    raise ValueError('withdrawn operation is not current')
                compiled = [{
                    'action': 'withdraw_operation',
                    'target_iri': withdrawn['target_iri'],
                    'before': {'operation_id': withdrawn['id']},
                    'after': None,
                    'evidence': [], 'impact': {},
                    'validation': {'withdrawn': True},
                    'risk': 'low', 'reason': command.get('reason'),
                    'supersedes_operation_id': withdrawn['id'],
                }]
                compiled[0]['fingerprint'] = operation_fingerprint(compiled[0])
            else:
                compiled = self._compile_command(
                    project_id, draft, command, ontology)
            if supersedes:
                current = {row['id']: row for row in effective}
                if supersedes not in current:
                    raise ValueError('superseded operation is not current')
                if len(compiled) != 1:
                    raise ValueError(
                        'one adjustment may supersede exactly one operation')
                compiled[0]['supersedes_operation_id'] = supersedes

            # Replacements occupy the original logical slot so dependent
            # annotations/edges never move ahead of their declaration.
            candidate_operations = list(effective)
            if supersedes:
                slot = next(index for index, row in enumerate(candidate_operations)
                            if row['id'] == supersedes)
                candidate_operations[slot:slot + 1] = (
                    [] if action == 'withdraw_operation' else compiled)
            else:
                candidate_operations.extend(compiled)

            working = self._base_turtle(project_id, draft['base_ontology_id'])
            for operation in candidate_operations:
                validation = operation.get('validation') or {}
                if validation.get('errors') or validation.get('withdrawn'):
                    continue
                try:
                    working = apply_operations(
                        working, [_semantic_operation(operation)])
                except ValueError as exc:
                    if operation not in compiled:
                        raise
                    issue = {
                        'code': 'operation_blocked', 'severity': 'error',
                        'message': str(exc), 'operation_ids': [],
                        'term_iris': [operation['target_iri']],
                    }
                    operation['validation'] = {
                        **validation,
                        'errors': [*(validation.get('errors') or []), issue],
                    }
                    operation['fingerprint'] = operation_fingerprint(operation)
            self.store.append_operations(project_id, draft_id, compiled)
            # 改了变更集合 = 这一轮的提交失效：请求回到「还没提交」，
            # 必须重新提交并校验（旧决定也会因为 operation_fingerprint 变化而失效）。
            updated = self._cas(project_id, draft_id, expected_revision, {
                'validation_report': None, 'validation_fingerprint': None,
                'submitted_at': None})
        return self._preview(project_id, updated)

    # -------------------------------------------------------------- validation
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

    def _refresh_source_context(self, project_id, context):
        refreshed = json.loads(_canonical_json(context))
        refs = refreshed.get('documents')
        targets = refs if isinstance(refs, list) else [refreshed]
        for reference in targets:
            if not isinstance(reference, dict):
                continue
            document_id = reference.get('document_id') or reference.get('id')
            if not document_id:
                continue
            try:
                document = self._current_source_document(project_id, document_id)
            except KeyError as exc:
                raise StaleSource(
                    f'source document {document_id!r} no longer exists') from exc
            reference['expected_document_version'] = document.get('version')
            reference['expected_document_version_id'] = document.get('version_id')
            candidate_ids = reference.get('candidate_ids') or []
            if reference.get('candidate_id'):
                candidate_ids = [*candidate_ids, reference['candidate_id']]
            candidates = {}
            for candidate_id in dict.fromkeys(candidate_ids):
                candidate = self._candidate(document, candidate_id)
                if candidate is None:
                    raise StaleSource(
                        f'source candidate {candidate_id!r} no longer exists')
                candidates[candidate_id] = candidate
            reference['candidate_fingerprints'] = {
                candidate_id: _fingerprint(candidate)
                for candidate_id, candidate in candidates.items()}
            reference['candidate_statuses'] = {
                candidate_id: candidate.get('status')
                for candidate_id, candidate in candidates.items()}
        return refreshed

    # --------------------------------------------------------------- publishing
    @staticmethod
    def _client_publish_hash(project_id, draft_id, expected_revision,
                             expected_ontology_id, validation_fingerprint,
                             acknowledged_warning_codes, idempotency_key, actor,
                             note=''):
        # 发布说明进指纹：换了说明就是**另一次发布意图**，不能让同一个幂等键
        # 重放出一份说明对不上的版本（版本记录要能回答"这一版为什么发"）。
        return _fingerprint({
            'project_id': project_id, 'draft_id': draft_id,
            'expected_revision': expected_revision,
            'expected_ontology_id': expected_ontology_id,
            'validation_fingerprint': validation_fingerprint,
            'acknowledged_warning_codes': sorted(
                set(acknowledged_warning_codes or [])),
            'idempotency_key': idempotency_key, 'actor': actor,
            'note': note or '',
        })

    def publish_preflight(self, project_id, draft_id, expected_revision,
                          expected_ontology_id, validation_fingerprint,
                          acknowledged_warning_codes, idempotency_key, actor,
                          note=''):
        draft = self.store.get(project_id, draft_id)
        self._assert_revision(draft, expected_revision)
        # A3 之前这里认 reviewed（"审核已完成"那一档状态）。现在状态只有三种，
        # "决定齐不齐"由下面 invalid 那一段逐条校验（每条变更都要有**当前**决定），
        # 所以门禁只剩一件事：只有「待处理」的请求能发布；已收下/已驳回是只读记录。
        _status_guard(draft, PENDING_STATES, '收下并发布',
                      '所有变更先形成收下/不收的决定；被全部不收的请求会直接变成「已驳回」，不会发布。')
        # 同 decide()：发布也要求这份请求**提交过**（提交冻结了校验快照与指纹，
        # 发布要用的正是这一份）。A3 之前由状态 reviewed 隐含，现在由时间戳直说。
        _require_submitted(draft, '收下并发布')
        self._check_current(
            project_id, draft, expected_revision,
            expected_ontology_id=expected_ontology_id)
        actor = _text(actor, 'actor')
        idempotency_key = _text(idempotency_key, 'idempotency_key')
        # 发布说明必填（服务端裁决，不是界面上的软提示）：版本记录要能回答
        # "这一版为什么发、谁发的"。只靠界面校验的话，任何别的调用方都能绕过，
        # 版本列表上就会出现一排没人看得懂的版本。
        note = _text(note, 'note')
        if len(note) > 500:
            raise ValueError('note must be at most 500 characters')
        # 回退必须由人确认：这是 publish_readiness 里那条警告的**门禁那一半** ——
        # 清单只负责说清代价，"不说清就不让发"要在这里兑现，否则警告只是一行字。
        revert_from = self._revert_base(project_id, draft)
        if revert_from and REVERT_WARNING not in set(acknowledged_warning_codes or []):
            raise ValidationFailed(
                'revert requires explicit acknowledgement', details={
                    'warning_codes': [REVERT_WARNING],
                    'revert_from': revert_from,
                    'message': '发布回退草案前需要确认：这之后发布的版本里的结构改动不会留在当前版本里。'})
        report, fingerprint, _ = self._require_validation(
            project_id, draft, validation_fingerprint,
            acknowledged_warning_codes, allow_errors=True)
        operations = self._active_operations(project_id, draft_id)
        _, decision_rows = self._history(project_id, draft_id)
        decisions = {row['operation_id']: row
                     for row in self._effective_decisions(decision_rows)}
        invalid = [
            operation['id'] for operation in operations
            if (operation['id'] not in decisions
                or decisions[operation['id']]['action'] not in {'approve', 'reject'}
                or decisions[operation['id']]['operation_fingerprint']
                != operation['fingerprint'])]
        if invalid:
            raise ValidationChanged(
                'publish requires a final current decision for every operation',
                details={'operation_ids': invalid})
        approved = []
        for operation in operations:
            decision = decisions.get(operation['id'])
            if (decision is not None and decision['action'] == 'approve'
                    and decision['operation_fingerprint'] == operation['fingerprint']):
                approved.append(operation)
        if not approved and not revert_from:
            # 回退草案零变更也能发：它的内容由 base（那一版）决定，"回退"本身就是这次
            # 发布的全部内容。代价由 revert 警告的勾选确认担起来（见上方那段）。
            raise ValidationFailed('publish requires at least one current approval')
        try:
            approved_turtle = apply_operations(
                self._base_turtle(project_id, draft['base_ontology_id']),
                map(_semantic_operation, approved))
        except ValueError as exc:
            raise ValidationFailed(
                'approved operation subset failed validation',
                details={'reason': str(exc)}) from exc
        request = {
            'project_id': project_id, 'draft': draft,
            'draft_id': draft_id, 'expected_revision': expected_revision,
            'expected_ontology_id': expected_ontology_id,
            'validation_fingerprint': fingerprint,
            'validation_report': report, 'operations': approved,
            'decisions': [decisions[row['id']] for row in approved],
            'turtle': approved_turtle,
            'summary': Ontology(approved_turtle).summary(),
            'acknowledged_warning_codes': sorted(
                set(acknowledged_warning_codes or [])),
            'idempotency_key': idempotency_key, 'actor': actor,
            'note': note,
        }
        request['client_request_hash'] = self._client_publish_hash(
            project_id, draft_id, expected_revision, expected_ontology_id,
            validation_fingerprint, acknowledged_warning_codes,
            idempotency_key, actor, note)
        request['request_hash'] = _fingerprint({
            key: value for key, value in request.items()
            if key not in {'draft', 'validation_report', 'summary'}})
        return request

    # ------------------------------------------- 审查计划 / 发布门禁（只读投影）
    def review_plan(self, project_id, draft_id):
        """返回服务端唯一裁决的 safe / manual / blocked 分类。

        这是一个**只读**投影：不修改草案状态，所以工作台可以随时刷新。
        前端必须逐行渲染本结果，不得复刻风险规则
        （规则唯一出处是 ``ontology_operations.operation_risk`` /
        ``is_batch_eligible``，本方法只是把结论整理成可渲染的行）。
        """
        draft = self.store.get(project_id, draft_id)
        if draft['status'] in FINAL_STATES:
            raise ValueError('已终结的草案没有审查计划')
        operations = self._active_operations(project_id, draft_id)
        try:
            # 草案叠加图用于判断"注释指向了停用术语"这类需要本体语境的资格
            turtle, _ = self._ontology_for_draft(project_id, draft)
        except ValueError:
            turtle = None
        report = draft.get('validation_report') or {}
        _, decision_rows = self._history(project_id, draft_id)
        decisions = {
            row['operation_id']: row
            for row in self._effective_decisions(decision_rows)}
        blocked_ids = {
            operation_id
            for issue in report.get('errors') or []
            for operation_id in issue.get('operation_ids') or []}
        has_unscoped_errors = any(
            not (issue.get('operation_ids') or [])
            for issue in report.get('errors') or [])
        warning_codes = sorted(self._warning_codes(report))
        items = []
        for operation in operations:
            decision = decisions.get(operation['id'])
            if (decision is not None
                    and decision.get('operation_fingerprint')
                    != operation['fingerprint']):
                # 操作指纹变了，旧决定不再决定任何东西
                decision = None
            items.append(self._review_plan_item(
                operation, decision, report, blocked_ids, has_unscoped_errors,
                warning_codes, turtle))
        counts = {
            name: sum(1 for item in items if item['classification'] == name)
            for name in ('safe', 'manual', 'blocked')}
        return {
            'draft_id': draft_id, 'status': draft['status'],
            'revision': draft['revision'],
            'policy_version': self.plan_policy_version,
            'rule_version': self.validation_rule_version,
            'validation_fingerprint': draft.get('validation_fingerprint'),
            'warnings': report.get('warnings') or [],
            'counts': {**counts, 'total': len(items)},
            'items': items,
            'fingerprint': _fingerprint({
                'policy_version': self.plan_policy_version,
                'rule_version': self.validation_rule_version,
                'draft_id': draft_id, 'revision': draft['revision'],
                'validation_fingerprint': draft.get('validation_fingerprint'),
                'items': [[item['operation_id'], item['operation_fingerprint'],
                           item['classification'], item['reason_codes']]
                          for item in items],
            }),
        }

    def _review_plan_item(self, operation, decision, report, blocked_ids,
                          has_unscoped_errors, warning_codes, turtle):
        """把单条操作整理成一行审查计划；分类规则与 decide() 完全一致。"""
        validation = operation.get('validation') or {}
        operation_warnings = list(validation.get('warnings') or [])
        operation_errors = [
            issue for issue in report.get('errors') or []
            if operation['id'] in (issue.get('operation_ids') or [])]
        codes = []
        blocked = operation['id'] in blocked_ids or has_unscoped_errors
        if validation.get('rebase_status') == 'conflict':
            blocked = True
            codes.append('rebase_conflict')
        if blocked:
            if 'rebase_conflict' not in codes:
                codes.append('blocking_error')
            classification, batch_eligible = 'blocked', False
        else:
            # 批量资格判定走服务端同一条规则，再叠加"草案级警告"闸门
            batch_eligible = not warning_codes and is_batch_eligible(
                operation, ontology=turtle)
            if batch_eligible:
                classification = 'safe'
                codes.append('low_risk_no_warning')
            else:
                classification = 'manual'
                if operation.get('risk') == 'high':
                    codes.append('high_risk')
                elif operation.get('risk') == 'medium':
                    codes.append('medium_risk')
                if operation.get('action') in IRREVERSIBLE_ACTIONS:
                    codes.append('irreversible_action')
                if operation_warnings:
                    codes.append('operation_warning')
                if warning_codes:
                    codes.append('draft_warning_requires_acknowledgement')
                if not codes:
                    codes.append('requires_review')
        requires_reason = classification == 'manual' and not blocked and (
            operation.get('risk') == 'high' or bool(warning_codes))
        return {
            'operation_id': operation['id'],
            'operation_fingerprint': operation['fingerprint'],
            'action': operation.get('action'),
            'target_iri': operation.get('target_iri'),
            'risk': operation.get('risk'),
            'source': operation.get('source'),
            'classification': classification,
            'reason_codes': codes,
            'batch_eligible': batch_eligible,
            'approvable': not blocked,
            # 与 decide() 的理由要求一致：高风险批准、或存在草案级警告时必须填理由
            'requires_reason': requires_reason,
            # 「一键审核」的判据（服务器唯一裁决）：这条变更可以被审核人一次性批准，
            # 不需要逐条写理由。阻断项、高风险项、以及草案带警告时的全部变更都不在内
            # —— 人工判断仍然只能人工做，批量通道只负责把重复劳动去掉。
            'auto_approvable': (not blocked) and not requires_reason,
            'impact': operation.get('impact') or {},
            'warnings': operation_warnings,
            'errors': operation_errors,
            'decision': None if decision is None else {
                'action': decision.get('action'),
                'reason': decision.get('reason'),
                'actor': decision.get('actor'),
                'created_at': decision.get('created_at'),
            },
        }

    def publish_readiness(self, project_id, draft_id):
        """只读发布清单：逐条给出未满足条件、严重级别与修复建议。

        检查项与 ``publish_preflight`` 对齐，但**不修改任何状态**，
        因此发布按钮可以随时重新读取并把全部未满足条件展示出来
        （不允许只把按钮置灰而不解释原因）。
        """
        draft = self.store.get(project_id, draft_id)
        items = []

        def add(code, message, severity='error', *, operation_ids=None,
                remediation=''):
            items.append({
                'code': code, 'message': message, 'severity': severity,
                'operation_ids': list(operation_ids or []),
                'remediation': remediation})

        if draft['status'] in FINAL_STATES:
            fate = '已收下' if draft['status'] == 'accepted' else '已驳回'
            add('draft_settled',
                '这份请求%s，是只读记录，不能再发布' % fate,
                remediation='要再改本体，请在「本体建模层」基于当前版本新建一份草案')
        elif not draft.get('submitted_at'):
            # A3：状态里没有"已提交"这一档，但它仍然是发布的前置条件（提交冻结了
            # 校验快照与指纹）。清单要说出来，否则界面上按钮灰着却讲不出为什么 ——
            # 而 publish_preflight 同样会拒绝，两边口径必须一致。
            add('not_submitted', '这份请求还没有提交过审核',
                remediation='在「本体建模层」点「提交审核」冻结这一轮变更，再回「本体建模层」逐条收下或不收')
        latest = self._latest_id(project_id)
        revert_from = self._revert_base(project_id, draft)
        if revert_from:
            # 「回到某一版」不是"基线过期"：让人去重新基线等于把回退意图抹掉。
            # 这里给的是一条**要人确认的警告**，说清回退的代价。
            version = self._version_number(project_id, revert_from)
            add(REVERT_WARNING,
                '这在回到 v%s：发布后当前版本会变成那一版的结构（再加上你这次的改动）；'
                '它之后发布的那些版本里的结构改动不会留在当前版本里'
                '（那些版本本身仍在「版本管理」里，随时可以再回到它们）。' % version,
                severity='warning',
                remediation='确认这就是你要的，再勾选这条警告发布')
        elif draft['base_ontology_id'] != latest:
            add('base_outdated', '草案基线已不是项目当前本体版本',
                remediation='重新基线（rebase）并重新校验')
        if draft['source_kind'] in {'discovery', 'candidate', 'import'}:
            try:
                self._source_snapshot(project_id, draft)
            except StaleSource as exc:
                add('stale_source', str(exc) or '来源快照已变化',
                    remediation='刷新来源快照并重新校验')
        operations = self._active_operations(project_id, draft_id)
        if not operations and not revert_from:
            # 回退草案没有操作是正常的（它的内容就是那一版），别在这里报"没有有效变更"。
            add('no_operation', '草案没有有效变更',
                remediation='补充变更或关闭草案')
        _, decision_rows = self._history(project_id, draft_id)
        decisions = {
            row['operation_id']: row
            for row in self._effective_decisions(decision_rows)}
        pending = [
            operation['id'] for operation in operations
            if (operation['id'] not in decisions
                or decisions[operation['id']]['action'] not in {'approve', 'reject'}
                or decisions[operation['id']]['operation_fingerprint']
                != operation['fingerprint'])]
        if pending:
            add('decision_pending', '%d 条变更尚未形成最终决定' % len(pending),
                operation_ids=pending,
                remediation='在审核阶段逐条收下或不收')
        approved = [
            operation for operation in operations
            if (operation['id'] in decisions
                and decisions[operation['id']]['action'] == 'approve'
                and decisions[operation['id']]['operation_fingerprint']
                == operation['fingerprint'])]
        if operations and not pending and not approved:
            add('no_approval', '没有任何变更被收下',
                remediation='至少收下一条变更，或关闭草案')
        fingerprint = draft.get('validation_fingerprint')
        try:
            report, fingerprint, _ = self._compute_validation(project_id, draft)
        except Exception as exc:  # 防御性边界：校验本身不可用时也不能抛给前端
            report = None
            add('validation_unavailable', '无法计算校验快照：%s' % exc,
                remediation='修复本体/数据问题后重试')
        if report is not None:
            if draft.get('validation_fingerprint') != fingerprint:
                add('validation_changed', '校验快照已过期',
                    remediation='重新运行校验并确认结果')
            for issue in report.get('errors') or []:
                add(issue.get('code') or 'validation_error',
                    issue.get('message') or '校验未通过',
                    operation_ids=issue.get('operation_ids'),
                    remediation='修复或不收相关变更')
            warning_codes = sorted(self._warning_codes(report))
            if warning_codes:
                add('acknowledgement_required',
                    '发布前需要确认校验警告：' + ', '.join(warning_codes),
                    severity='warning',
                    remediation='在发布面板逐项勾选确认')
        if approved and report is not None and not (report.get('errors') or []):
            try:
                apply_operations(
                    self._base_turtle(project_id, draft['base_ontology_id']),
                    map(_semantic_operation, approved))
            except ValueError as exc:
                add('approved_subset_invalid', '被收下的变更子集无法应用：%s' % exc,
                    remediation='不收或退回相关变更后重新校验')
        severities = {item['severity'] for item in items}
        return {
            'ready': 'error' not in severities,
            'draft_id': draft_id, 'status': draft['status'],
            'revision': draft['revision'],
            'validation_fingerprint': fingerprint,
            'counts': {
                'error': sum(1 for item in items if item['severity'] == 'error'),
                'warning': sum(1 for item in items
                               if item['severity'] == 'warning'),
                'info': sum(1 for item in items if item['severity'] == 'info'),
            },
            'approval_counts': {
                'approved': len(approved),
                'pending': len(pending),
                'total': len(operations)},
            # 回退草案要告诉前端"它可以零变更发布"：门禁（gatePublish）靠这个判断，
            # 否则界面上会因为 approved=0 把发布按钮永远锁住 —— 用户就只能看着
            # 一条正确的回退路径点不动按钮。
            'revert': revert_from,
            'items': items,
        }

    # --------------------------------------------------- 一键审核 / 证据人话化
    def batch_approve(self, project_id, draft_id, expected_revision,
                      expected_ontology_id, validation_fingerprint,
                      acknowledged_warning_codes, actor):
        """一键审核：把「可批准且不需要写理由」的变更一次事务全部批准。

        这是给审核人减负的通道，不是绕过审核：

        * 集合由**服务端**自己的 ``review_plan`` 决定（``auto_approvable``），
          前端只表达"我要一键批准"这个意图，不参与挑选；
        * 阻断项、高风险项、以及草案带警告时的所有变更**永不进入**这个集合
          —— 它们必须逐条人工处理，理由也必须由人写；
        * 整批写入，任何一条失效（修订号/指纹/基线变化）都整批拒绝，
          不会出现"批了一半"的中间态；
        * 返回里附上未被批准的条数与原因分布，UI 必须把它们显示出来。

        ``acknowledged_warning_codes`` 仍然照常校验：草案带警告时集合本来就是空的，
        所以走不到批量写入。
        """
        plan = self.review_plan(project_id, draft_id)
        pending = [item for item in plan['items'] if item.get('decision') is None]
        target = [item for item in pending if item.get('auto_approvable')]
        skipped = [item for item in pending if item not in target]
        reasons = {}
        for item in skipped:
            code = (item.get('reason_codes') or ['requires_review'])[0]
            reasons[code] = reasons.get(code, 0) + 1
        summary = {
            'approved': len(target),
            'skipped': len(skipped),
            'skipped_reasons': reasons,
            'skipped_operation_ids': [item['operation_id'] for item in skipped],
            'policy_version': plan['policy_version'],
            'plan_fingerprint': plan['fingerprint'],
        }
        if not target:
            # 没有可批量批准的项不是错误：把"为什么没有"如实回给 UI。
            return {'draft': self.get(project_id, draft_id), 'batch': summary}
        decisions = [{
            'operation_id': item['operation_id'],
            'operation_fingerprint': item['operation_fingerprint'],
            'action': 'approve',
            'reason': None,
        } for item in target]
        draft = self.decide(
            project_id, draft_id, expected_revision, expected_ontology_id,
            validation_fingerprint, decisions, acknowledged_warning_codes,
            actor, assisted_batch=True)
        return {'draft': draft, 'batch': summary}

    def evidence_index(self, project_id, draft_id):
        """把操作上的证据引用翻译成审核人看得懂的一条条来源。

        草案操作里的 ``evidence`` 是一串机器引用（``document-version:<文档>:<版本>``、
        ``candidate:<候选 id>``）。原样展示等于没展示——审核人看到的是
        "来源快照 / 置信度 — / 未保存摘录"这种占位文案。这里用**发现运行时冻结的
        候选快照**（``candidate_snapshot``）把引用还原成：文档标题、片段范围、
        置信度与原文句子。

        只读，不推导、不补全：解析不到的引用如实标 ``resolved=False`` 并保留原始
        引用串，绝不用当前数据去"猜"历史证据。
        """
        draft = self.store.get(project_id, draft_id)
        candidates = {}
        document_titles = {}
        for run in self.repository.list_discovery_runs(project_id):
            for candidate in run.get('candidate_snapshot') or []:
                if not isinstance(candidate, dict):
                    continue
                candidate_id = candidate.get('id') or candidate.get('assertion_id')
                if candidate_id and candidate_id not in candidates:
                    candidates[candidate_id] = candidate
                document_id = candidate.get('document_id')
                if document_id and document_id not in document_titles:
                    document_titles[document_id] = candidate.get('document_title')
        resolved = unresolved = 0
        rows = {}
        for operation in self._active_operations(project_id, draft_id):
            entries = []
            for raw in operation.get('evidence') or []:
                entry = self._evidence_entry(raw, candidates, document_titles)
                if entry['resolved']:
                    resolved += 1
                else:
                    unresolved += 1
                entries.append(entry)
            rows[operation['id']] = entries
        return {
            'draft_id': draft_id,
            'counts': {'resolved': resolved, 'unresolved': unresolved},
            'explanation': (
                '每条变更下面的「来源」是发现阶段冻结的原文证据：文档、片段范围、'
                '置信度和命中的原句。解析不到的会标「仅引用」，表示该证据只留下了 '
                'ID，当前无法还原成原文。'),
            'items': rows,
        }

    @staticmethod
    def _evidence_entry(raw, candidates, document_titles):
        ref = raw.get('ref') if isinstance(raw, dict) else raw
        ref = ref if isinstance(ref, str) else json.dumps(
            raw, ensure_ascii=False, sort_keys=True)
        kind, _, rest = ref.partition(':')
        if kind == 'candidate':
            candidate = candidates.get(rest)
            if candidate:
                title = candidate.get('document_title') or document_titles.get(
                    candidate.get('document_id'))
                return {
                    'ref': ref, 'kind': 'candidate', 'resolved': True,
                    'document_title': title,
                    'document_id': candidate.get('document_id'),
                    'document_version_id': candidate.get('document_version_id'),
                    'chunk_id': candidate.get('chunk_id') or candidate.get(
                        'assertion_chunk_id'),
                    'start_char': candidate.get('start_char'),
                    'end_char': candidate.get('end_char'),
                    'confidence': candidate.get('confidence'),
                    'evidence': candidate.get('evidence'),
                    'evidence_status': candidate.get('evidence_status'),
                    'candidate_kind': candidate.get('kind'),
                    'proposed_type': candidate.get('proposed_type'),
                    'assertion_id': candidate.get('assertion_id'),
                }
        if kind == 'document-version':
            document_id, _, version_id = rest.partition(':')
            title = document_titles.get(document_id)
            return {
                'ref': ref, 'kind': 'document_version',
                'resolved': bool(document_id),
                'document_title': title, 'document_id': document_id or None,
                'document_version_id': version_id or None,
            }
        return {'ref': ref, 'kind': 'unknown', 'resolved': False}

    def is_annotation_only(self, operations) -> bool:
        """这批操作是否只动标注 —— 发布时据此决定要不要复用当前版本号（E1）。

        以服务方法暴露，供 repository 层在发布事务里直接问，避免 repository
        去 import services 模块级函数（那会绕成分层倒挂 + 循环导入）。
        """
        return is_annotation_only(operations)

    def publish(self, project_id, draft_id, expected_revision,
                expected_ontology_id, validation_fingerprint,
                acknowledged_warning_codes, idempotency_key, actor, note=''):
        actor = _text(actor, 'actor')
        idempotency_key = _text(idempotency_key, 'idempotency_key')
        draft = self.store.get(project_id, draft_id)
        replay = getattr(self.publisher, 'replay_ontology_publish', None)
        # 幂等重放只在「这个幂等键**确实是**这份请求那次发布用的键」时走。
        #
        # 以前的条件是"草案已经是终态就一律重放"，于是拿一个**新键**再发一次
        # 会得到 409 `idempotency_conflict`（"ontology publish request does not exist"）——
        # 用户看到的是"发布请求不存在"，而不是"这份请求已经收下了，不能再发布"，
        # 下一步点哪里更是一句都没有。这跟 A0 修掉的"门禁撒谎"是同一类问题。
        # 现在：键存在才重放（幂等语义与"恢复未完成事务"两条路都保留）；
        # 键不存在就落到下面 publish_preflight 的状态守卫，给出中文的 draft_immutable。
        if (draft['status'] in FINAL_STATES and replay is not None
                and self.store.get_publish_request(
                    project_id, draft_id, idempotency_key) is not None):
            return replay(
                project_id, draft_id, idempotency_key,
                self._client_publish_hash(
                    project_id, draft_id, expected_revision,
                    expected_ontology_id, validation_fingerprint,
                    acknowledged_warning_codes, idempotency_key, actor, note))
        prepared = self.publish_preflight(
            project_id, draft_id, expected_revision, expected_ontology_id,
            validation_fingerprint, acknowledged_warning_codes,
            idempotency_key, actor, note)
        if self.publisher is None:
            raise RuntimeError(
                'atomic ontology publication delegate is not configured')
        if callable(self.publisher):
            return self.publisher(**prepared)
        publish = getattr(self.publisher, 'publish_ontology_draft', None)
        if publish is None:
            raise TypeError('publisher must be callable or expose publish_ontology_draft')
        return publish(**prepared)

    # ------------------------------------------------------------ read models
    def _read_ontology(self, project_id, ontology_id=None, draft_id=None):
        # 读「草案叠加层」时，基线只有一个正确来源：草案自己的 base_ontology_id。
        #
        # 这里以前会拿调用方顺手带上的 ontology_id 跟草案基线比对，不一致就抛
        # ValueError('draft overlay must use its own base ontology')。但调用方带的
        # 那个 id 几乎总是「项目当前版本」，而草案往往建立在更早的版本上——甚至
        # 建立在还没有本体的空项目上（base_ontology_id=None）。于是只要草案发布过
        # 一次，当前版本就必然前移，之后每次读层级树/关系矩阵都 422，画布直接空白。
        # 现在：草案请求一律以草案基线为准，ontology_id 只用于「看历史版本」。
        if draft_id is not None:
            draft = self.store.get(project_id, draft_id)
            turtle, _ = self._ontology_for_draft(project_id, draft)
            return Ontology(turtle), draft['base_ontology_id']
        if ontology_id is None:
            ontology_id = self._latest_id(project_id)
        return Ontology(self._base_turtle(project_id, ontology_id)), ontology_id

    @staticmethod
    def _decode_cursor(cursor):
        if cursor in (None, ''):
            return 0
        try:
            return int(base64.urlsafe_b64decode(
                str(cursor).encode('ascii') + b'===').decode('ascii'))
        except (ValueError, UnicodeError) as exc:
            raise ValueError('invalid cursor') from exc

    @staticmethod
    def _page_slice(items, cursor, limit):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('limit must be between 1 and 200')
        offset = OntologyDrafts._decode_cursor(cursor)
        page = items[offset:offset + limit]
        next_offset = offset + len(page)
        next_cursor = None
        if next_offset < len(items):
            next_cursor = base64.urlsafe_b64encode(
                str(next_offset).encode('ascii')).decode('ascii').rstrip('=')
        return page, {'next_cursor': next_cursor,
                      'total': len(items), 'limit': limit}

    @staticmethod
    def _page(items, cursor, limit):
        page, metadata = OntologyDrafts._page_slice(items, cursor, limit)
        return {'items': page, **metadata}

    @staticmethod
    def _class_maps(ontology):
        active = {node for node in ontology.classes if ontology.is_active_term(node)}
        parents = {
            node: {parent for parent in ontology.graph.objects(node, RDFS.subClassOf)
                   if parent in active}
            for node in active}
        children = {node: set() for node in active}
        for child, values in parents.items():
            for parent in values:
                children[parent].add(child)
        return active, parents, children

    @staticmethod
    def _term_item(ontology, node):
        labels = list(ontology.graph.objects(node, RDFS.label))
        zh = en = plain = ''
        for label in labels:
            language = (label.language or '').lower()
            if not zh and language.startswith('zh'):
                zh = str(label)
            elif not en and language.startswith('en'):
                en = str(label)
            elif not plain and not language:
                plain = str(label)
        item = {
            'id': str(node), 'name': local_name(node),
            'label': plain or en or local_name(node),
            'label_zh': zh, 'label_en': en,
            'description': str(ontology.graph.value(node, RDFS.comment) or ''),
            'active': ontology.is_active_term(node),
        }
        if node in ontology.classes:
            item['parents'] = [
                str(value) for value in ontology.graph.objects(
                    node, RDFS.subClassOf)]
        elif node in ontology.relations:
            item['domain'] = [
                str(value) for value in ontology.constraint_types(node, RDFS.domain)]
            item['range'] = [
                str(value) for value in ontology.constraint_types(node, RDFS.range)]
        elif node in ontology.attributes:
            item['domain'] = [
                str(value) for value in ontology.constraint_types(node, RDFS.domain)]
            item['range'] = [
                str(value) for value in ontology.graph.objects(node, RDFS.range)]
        return item

    def _display_path(self, node, parents, ontology):
        memo = {}
        visiting = set()

        def best_path(current):
            if current in memo:
                return memo[current]
            if current in visiting:
                return (current,)
            visiting.add(current)
            direct = sorted(parents.get(current, set()), key=str)
            candidates = [best_path(parent) + (current,) for parent in direct]
            visiting.remove(current)
            best = min(
                candidates or [(current,)],
                key=lambda path: (len(path), tuple(map(str, path))))
            memo[current] = best
            return best

        best = best_path(node)
        return [{'iri': str(item), 'label': self._term_item(
            ontology, item)['label']} for item in best]

    def _class_item(self, node, parents, children, ontology, *, parent=None):
        item = self._term_item(ontology, node)
        return {
            **item, 'iri': str(node), 'canonical_iri': str(node),
            'is_reference': len(parents.get(node, set())) > 1 and parent is not None,
            'child_count': len(children.get(node, set())),
            'other_parent_count': max(0, len(parents.get(node, set())) -
                                      (1 if parent is not None else 0)),
            'display_path': self._display_path(node, parents, ontology),
        }

    def roots(self, project_id, *, ontology_id=None, draft_id=None,
              cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        active, parents, children = self._class_maps(ontology)
        nodes = [node for node in sorted(active, key=str) if not parents[node]]
        page, metadata = self._page_slice(nodes, cursor, limit)
        return {'items': [self._class_item(
            node, parents, children, ontology) for node in page], **metadata}

    def children(self, project_id, iri, *, ontology_id=None, draft_id=None,
                 cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        _, parents, children = self._class_maps(ontology)
        parent = URIRef(iri)
        nodes = sorted(children.get(parent, set()), key=str)
        page, metadata = self._page_slice(nodes, cursor, limit)
        return {'items': [self._class_item(
            node, parents, children, ontology, parent=parent)
            for node in page], **metadata}

    def search(self, project_id, query, *, ontology_id=None, draft_id=None,
               cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        needle = _text(query, 'query').casefold()
        active, parents, children = self._class_maps(ontology)
        declared = sorted(
            (ontology.classes | ontology.relations | ontology.attributes), key=str)
        matches = []
        for node in declared:
            if not ontology.is_active_term(node):
                continue
            all_labels = [str(value) for value in ontology.graph.objects(
                node, RDFS.label)]
            description = str(ontology.graph.value(node, RDFS.comment) or '')
            haystack = ' '.join([
                str(node), local_name(node), description,
                *all_labels,
            ])
            if needle in haystack.casefold():
                matches.append(node)
        page, metadata = self._page_slice(matches, cursor, limit)
        items = []
        for node in page:
            iri = str(node)
            if node in active:
                items.append(self._class_item(
                    node, parents, children, ontology))
            else:
                items.append({**self._term_item(ontology, node),
                              'iri': iri, 'canonical_iri': iri})
        return {'items': items, **metadata}

    def neighborhood(self, project_id, iri, *, ontology_id=None, draft_id=None,
                     cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        active, parents, children = self._class_maps(ontology)
        node = URIRef(iri)
        if node not in active:
            # 不要抛裸 KeyError：全局 handler 会把它转成 404「未找到：<一长串 IRI>」，
            # 用户只能看到一个裸 IRI，既不知道发生了什么，也不知道能干什么。
            # 改成返回一个前端能优雅处理的结果，把原因说成人话：
            # 要么是「已停用」（在词汇表里但被 owl:deprecated 标停），
            # 要么是「幽灵引用」（已删除，或属于别的版本/别的项目）。
            known = node in (ontology.classes | ontology.relations | ontology.attributes)
            return {
                'term': None,
                'not_found': True,
                'reason': ('该对象已停用，不在当前本体的活动对象里。'
                           if known else
                           '该对象不在当前本体的对象里（可能已被删除，或属于其它版本）。'),
                'iri': iri,
            }
        adjacent = sorted(parents[node] | children[node], key=str)
        page, metadata = self._page_slice(adjacent, cursor, limit)
        result = {'items': [self._class_item(
            value, parents, children, ontology,
            parent=node if value in children[node] else None)
            for value in page], **metadata}
        result['term'] = self._class_item(node, parents, children, ontology)
        relation_nodes = sorted((
            value for value in ontology.relations
            if ontology.is_active_term(value)
            and node in (set(ontology.constraint_types(value, RDFS.domain))
                         | set(ontology.constraint_types(value, RDFS.range)))), key=str)
        attribute_nodes = sorted((
            value for value in ontology.attributes
            if ontology.is_active_term(value)
            and node in set(ontology.constraint_types(value, RDFS.domain))), key=str)
        projection_limit = min(limit, NEIGHBORHOOD_LINK_LIMIT)
        result['relations'] = [{
            **self._term_item(ontology, value),
            'iri': str(value), 'kind': 'relation',
        } for value in relation_nodes[:projection_limit]]
        result['attributes'] = [{
            **self._term_item(ontology, value),
            'iri': str(value), 'kind': 'attribute',
        } for value in attribute_nodes[:projection_limit]]
        result.update({
            'relation_count': len(relation_nodes),
            'attribute_count': len(attribute_nodes),
            'relations_truncated': len(relation_nodes) > projection_limit,
            'attributes_truncated': len(attribute_nodes) > projection_limit,
            'link_projection_limit': projection_limit,
        })
        return result

    def matrix(self, project_id, *, ontology_id=None, draft_id=None,
               cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        nodes = sorted([
            *((value, 'relation') for value in ontology.relations
              if ontology.is_active_term(value)),
            *((value, 'attribute') for value in ontology.attributes
              if ontology.is_active_term(value)),
        ], key=lambda item: str(item[0]))
        page, metadata = self._page_slice(nodes, cursor, limit)
        return {
            'items': [{
                **self._term_item(ontology, node),
                'iri': str(node), 'kind': kind,
            } for node, kind in page],
            **metadata,
        }


__all__ = [
    'OntologyDrafts', 'OntologyDraftError', 'RevisionConflict', 'StaleBase',
    'StaleSource', 'ValidationChanged', 'ValidationFailed', 'BatchNotAllowed',
    'VALIDATION_RULE_VERSION',
]
