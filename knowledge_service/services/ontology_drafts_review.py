"""审核计划、发布前置校验与原子发布（含逐条裁决与批量收下）。
"""

from __future__ import annotations

from .ontology_drafts_core import (
    IRREVERSIBLE_ACTIONS, PENDING_STATES, FINAL_STATES, REVERT_WARNING, StaleSource, ValidationChanged, ValidationFailed, _status_guard, _require_submitted, _canonical_json, _fingerprint, _semantic_operation, is_annotation_only, _text,
)
from .ontology import Ontology
from .ontology_operations import apply_operations
from .ontology_operations import is_batch_eligible
from .ontology_operations import operation_fingerprint
import json



class DraftReviewPublishMixin:
    """审核计划、发布前置校验与原子发布（含逐条裁决与批量收下）。"""

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
