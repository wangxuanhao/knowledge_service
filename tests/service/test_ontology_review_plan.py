"""服务端「审查计划 / 发布门禁」的契约测试。

为什么要锁这两个接口：
    前端原来自己复刻了一份风险与批量批准规则（`isBatchEligible`），
    和服务端的 `operation_risk` / `is_batch_eligible` 是两套实现，
    迟早漂移——漂移时界面会显示"可批准"而服务端拒绝。
    本文件把「谁可以批量批准、为什么被拦、发布还差什么」钉在服务端。
"""
import pytest

from knowledge_service.repository import Repository
from knowledge_service.services.ontology_drafts import OntologyDrafts


BASE = '''
@prefix ex: <https://example.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Root a owl:Class ; rdfs:label "Root" .
ex:Child a owl:Class ; rdfs:subClassOf ex:Root ; rdfs:label "Child" .
ex:Legacy a owl:Class ; owl:deprecated true .
ex:rel a owl:ObjectProperty ; rdfs:domain ex:Root ; rdfs:range ex:Child .
'''

LABEL = 'http://www.w3.org/2000/01/rdf-schema#label'


def setup_service(tmp_path):
    """建一个项目 + 基线本体，返回 (repo, 服务, 项目, 基线版本)。"""
    repo = Repository(tmp_path / 'review_plan.sqlite')
    project_id = repo.create_project('review plan')['id']
    base = repo.save_ontology(project_id, BASE, {})
    return repo, OntologyDrafts(repo), project_id, base


def add_label(service, project_id, draft, value='Child label',
              target='https://example.test/Child'):
    """追加一条低风险人工注释操作（safe 分类的最小样本）。"""
    return service.command(project_id, draft['id'], draft['revision'], {
        'action': 'add_annotation', 'target_iri': target,
        'predicate': LABEL, 'value': value, 'language': 'en'})


def plan_item(plan, operation_id):
    return next(item for item in plan['items']
                if item['operation_id'] == operation_id)


def decide_all(service, project_id, draft, base, submitted, *, reason=None,
               acknowledged=None):
    """对当前全部操作给出批准决定，返回 reviewed 后的预览。"""
    decisions = []
    for operation in submitted['operations']:
        item = {'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'],
                'action': 'approve'}
        if reason:
            item['reason'] = reason
        decisions.append(item)
    return service.decide(
        project_id, draft['id'], submitted['revision'], base['id'],
        submitted['validation_fingerprint'], decisions,
        acknowledged or [], 'reviewer@example.test')


# --------------------------------------------------------------------- 审查计划
def test_review_plan_marks_low_risk_manual_edits_as_safe(tmp_path):
    """两条人工标签编辑：应判为 safe、可批量批准、不需要填理由。"""
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', '安全批次', 'author')
    first = add_label(service, project_id, draft, 'A')
    # 第二次命令必须带上第一次返回的 revision（乐观锁）
    second = add_label(service, project_id, first, 'B',
                       target='https://example.test/Root')
    submitted = service.submit(project_id, draft['id'], second['revision'])

    plan = service.review_plan(project_id, draft['id'])

    # A3：提交不改状态（还是「待处理」）—— 计划里的 status 就是草案状态。
    assert plan['status'] == 'pending'
    assert plan['policy_version'] == 'ontology-review-plan/1'
    assert plan['validation_fingerprint'] == submitted['validation_fingerprint']
    assert plan['counts'] == {'safe': 2, 'manual': 0, 'blocked': 0, 'total': 2}
    for item in plan['items']:
        assert item['classification'] == 'safe', item['reason_codes']
        assert item['reason_codes'] == ['low_risk_no_warning']
        assert item['batch_eligible'] is True
        assert item['approvable'] is True
        assert item['requires_reason'] is False
        assert item['decision'] is None
    assert first['revision'] < submitted['revision']


def test_review_plan_is_read_only_and_keeps_a_stable_fingerprint(tmp_path):
    """只读投影：重复读取不推进修订号，指纹稳定（前端可放心轮询）。"""
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', '只读', 'author')
    add_label(service, project_id, draft, 'A')
    submitted = service.submit(project_id, draft['id'], draft['revision'] + 1)

    first = service.review_plan(project_id, draft['id'])
    second = service.review_plan(project_id, draft['id'])

    assert first['revision'] == second['revision'] == submitted['revision']
    assert first['fingerprint'] == second['fingerprint']


def test_review_plan_classifies_retirement_as_manual_and_never_batchable(tmp_path):
    """停用是高风险且不可批量：必须逐项审核并填写理由。"""
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', '停用', 'author')
    preview = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'retire_term', 'target_iri': 'https://example.test/Legacy'})
    operation = preview['operations'][0]
    service.submit(project_id, draft['id'], preview['revision'])

    item = plan_item(service.review_plan(project_id, draft['id']), operation['id'])

    assert item['classification'] == 'manual'
    assert item['risk'] == 'high'
    assert 'high_risk' in item['reason_codes']
    assert 'irreversible_action' in item['reason_codes']
    assert item['batch_eligible'] is False
    assert item['requires_reason'] is True
    # 高风险但仍可批准（不是 blocked）
    assert item['approvable'] is True


def test_review_plan_blocks_operations_with_structural_errors(tmp_path):
    """停用仍被依赖的类：判为 blocked，前端不得展示"批准"入口。"""
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', '阻断', 'author')
    preview = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'retire_term', 'target_iri': 'https://example.test/Root'})
    operation = preview['operations'][0]
    submitted = service.submit(project_id, draft['id'], preview['revision'])

    plan = service.review_plan(project_id, draft['id'])
    item = plan_item(plan, operation['id'])

    assert submitted['validation_report']['conforms'] is False
    assert item['classification'] == 'blocked'
    assert 'blocking_error' in item['reason_codes']
    assert item['approvable'] is False
    assert item['batch_eligible'] is False
    assert item['errors'], '阻断原因必须可定位到具体操作'
    assert item['errors'][0]['operation_ids'] == [operation['id']]
    assert plan['counts']['blocked'] == 1


def test_review_plan_and_readiness_gate_on_draft_warnings(tmp_path):
    """草案级警告会把 safe 降级为 manual，并在发布清单里要求逐项确认。"""
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', '警告', 'author')
    preview = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'add_annotation', 'target_iri': 'https://example.test/Child',
        'predicate': 'https://example.test/note',
        'value': 'https://example.test/Legacy', 'value_type': 'iri'})
    submitted = service.submit(project_id, draft['id'], preview['revision'])

    plan = service.review_plan(project_id, draft['id'])
    item = plan['items'][0]

    assert {row['code'] for row in plan['warnings']} == {
        'active_custom_annotation_dependency'}
    assert item['classification'] == 'manual'
    assert 'draft_warning_requires_acknowledgement' in item['reason_codes']
    assert item['batch_eligible'] is False
    assert item['requires_reason'] is True

    operation = submitted['operations'][0]
    service.decide(
        project_id, draft['id'], submitted['revision'], base['id'],
        submitted['validation_fingerprint'],
        [{'operation_id': operation['id'],
          'operation_fingerprint': operation['fingerprint'],
          'action': 'approve', 'reason': '已核对停用依赖'}],
        ['active_custom_annotation_dependency'], 'reviewer')

    readiness = service.publish_readiness(project_id, draft['id'])
    codes = {row['code'] for row in readiness['items']}

    assert 'acknowledgement_required' in codes
    # 警告只要求确认，不阻断发布
    assert readiness['ready'] is True
    assert readiness['counts']['warning'] == 1


def test_review_plan_refuses_final_drafts(tmp_path):
    """已关闭的草案不再有审查计划，但发布清单仍能给出原因。"""
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', '关闭', 'author')
    preview = add_label(service, project_id, draft, 'X')
    closed = service.close(
        project_id, draft['id'], preview['revision'], 'author', '不再需要')

    assert closed['status'] == 'rejected'
    with pytest.raises(ValueError, match='已终结的草案没有审查计划'):
        service.review_plan(project_id, draft['id'])

    readiness = service.publish_readiness(project_id, draft['id'])
    assert readiness['ready'] is False
    # A3：终态（已收下/已驳回）在清单里是一条 draft_settled —— 说明这份请求为什么
    # 不能再发布、以及要去哪里改本体。旧词 draft_not_reviewed 随状态收窄一起退场。
    assert any(row['code'] == 'draft_settled'
               for row in readiness['items']), readiness['items']


# --------------------------------------------------------------------- 发布门禁
def test_publish_readiness_lists_every_unmet_condition(tmp_path):
    """未完成审核时，清单必须逐条列出未满足条件与修复建议。

    A3：状态里没有「已提交」这一档，「提交审核」改由 submitted_at 记事实 ——
    但它对发布**仍然是硬前置**（提交冻结了校验快照与指纹）。所以清单必须分两段说清：
      ① 还没提交 → not_submitted：先说"差提交"，别让用户去对着灰按钮猜；
      ② 提交过、但还有变更没有最终决定 → decision_pending（带 operation_ids 与修复建议）。
    """
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', '门禁', 'author')
    preview = add_label(service, project_id, draft, 'Child label')

    # ① 还没提交：清单里必须有这一条，且服务端发布时也真的拒绝（两边同一口径）
    unsubmitted = service.publish_readiness(project_id, draft['id'])
    assert unsubmitted['ready'] is False
    early = {row['code']: row for row in unsubmitted['items']}
    assert 'not_submitted' in early, unsubmitted['items']
    assert '提交审核' in early['not_submitted']['remediation']
    with pytest.raises(ValueError, match='还没有提交过'):
        service.publish_preflight(
            project_id, draft['id'], preview['revision'], base['id'],
            None, [], 'not-submitted-key', 'author',
            '用例：未提交不得发布')

    service.submit(project_id, draft['id'], preview['revision'])

    readiness = service.publish_readiness(project_id, draft['id'])

    assert readiness['ready'] is False
    assert readiness['counts']['error'] >= 1
    assert readiness['approval_counts'] == {
        'approved': 0, 'pending': 1, 'total': 1}
    by_code = {row['code']: row for row in readiness['items']}
    # ② 提交过之后不再有 not_submitted，换成了"逐条决定没做完"
    assert 'not_submitted' not in by_code, readiness['items']
    assert 'decision_pending' in by_code, readiness['items']
    assert by_code['decision_pending']['operation_ids'] == [
        preview['operations'][0]['id']]
    assert by_code['decision_pending']['remediation'], '每条未满足条件都要给修复建议'


def test_publish_readiness_is_clean_once_every_operation_is_decided(tmp_path):
    """全部批准后，发布清单应为 ready，且不再有任何 error 级条目。"""
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', '干净', 'author')
    preview = add_label(service, project_id, draft, 'Child label')
    submitted = service.submit(project_id, draft['id'], preview['revision'])
    reviewed = decide_all(service, project_id, draft, base, submitted)

    assert reviewed['status'] == 'pending'
    readiness = service.publish_readiness(project_id, draft['id'])

    assert readiness['ready'] is True
    assert readiness['counts']['error'] == 0
    assert readiness['approval_counts'] == {
        'approved': 1, 'pending': 0, 'total': 1}
    assert readiness['validation_fingerprint'] == reviewed['validation_fingerprint']


def test_publish_readiness_reports_base_drift_as_error(tmp_path):
    """草案基线落后于项目当前本体时，发布清单必须点名这条阻断。"""
    repo, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', '漂移', 'author')
    preview = add_label(service, project_id, draft, 'Child label')
    submitted = service.submit(project_id, draft['id'], preview['revision'])
    decide_all(service, project_id, draft, base, submitted)

    # 项目当前本体前进：草案基线随即落后
    new_base = repo.save_ontology(project_id, BASE + '\n# 新版本', {})
    assert new_base['id'] != base['id']

    readiness = service.publish_readiness(project_id, draft['id'])
    codes = {row['code'] for row in readiness['items']}

    assert 'base_outdated' in codes
    assert readiness['ready'] is False
    assert any(row['remediation'] for row in readiness['items']
               if row['code'] == 'base_outdated')
