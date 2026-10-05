"""本体请求（草案）域共享基座：状态常量、领域异常与纯校验助手。

被 ontology_drafts 主模块与各 mixin 共同导入；此处不得反向依赖主模块。
"""

from __future__ import annotations

from .ontology_operations import operation_fingerprint
import hashlib
import json


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


__all__ = [
    'VALIDATION_RULE_VERSION', 'PLAN_POLICY_VERSION', 'IRREVERSIBLE_ACTIONS', 'PENDING_STATES', 'ACCEPTED_STATES', 'REJECTED_STATES', 'FINAL_STATES', 'EDITABLE_STATES', 'REVIEW_STATES', 'OPEN_STATES', 'REVERT_SOURCE', 'REVERT_WARNING', 'SUBMITTED_STATES', 'NEIGHBORHOOD_LINK_LIMIT', 'OntologyDraftError', 'RevisionConflict', 'StaleBase', 'StaleSource', 'ValidationChanged', 'ValidationFailed', 'BatchNotAllowed', 'DraftImmutable', '_status_guard', '_require_submitted', '_canonical_json', '_fingerprint', '_semantic_operation', 'ANNOTATION_ACTIONS', 'is_annotation_only', '_text', '_as_list',
]
