from fastapi.testclient import TestClient
import pytest

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


BASE = '''
@prefix ex: <https://example.test/ns#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Root a owl:Class ; rdfs:label "Root" .
ex:AltRoot a owl:Class ; rdfs:label "Alt root" .
ex:Child a owl:Class ; rdfs:label "Child" ;
  rdfs:subClassOf ex:Root, ex:AltRoot .
ex:rel a owl:ObjectProperty ; rdfs:domain ex:Root ; rdfs:range ex:Child .
'''


@pytest.fixture
def workbench(tmp_path):
    app = create_app(tmp_path / 'ontology-api.sqlite', encoder=HashingEncoder())
    repo = app.state.service.repository
    project = repo.create_project('ontology API')
    ontology = repo.bootstrap_ontology(project['id'], BASE, {})
    with TestClient(app) as client:
        yield client, project['id'], ontology


def create_draft(client, project_id, ontology, **overrides):
    body = {
        'base_ontology_id': ontology['id'],
        'source_kind': 'manual',
        'title': 'Workbench draft',
        'actor': 'architect@example.test',
    }
    body.update(overrides)
    response = client.post(
        f'/api/projects/{project_id}/ontology-drafts', json=body)
    assert response.status_code == 201, response.text
    return response.json()


def command(client, project_id, draft, payload):
    response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/commands",
        json={'expected_revision': draft['revision'], 'command': payload})
    assert response.status_code == 200, response.text
    return response.json()


def test_create_list_get_command_close_and_required_revision(workbench):
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology)

    listing = client.get(
        f'/api/projects/{project_id}/ontology-drafts').json()
    assert listing['total'] == 1
    assert listing['items'][0]['id'] == draft['id']

    fetched = client.get(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}")
    assert fetched.status_code == 200
    assert fetched.json()['operations'] == []

    missing_revision = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/commands",
        json={'command': {'action': 'create_term'}})
    assert missing_revision.status_code == 422

    changed = command(client, project_id, draft, {
        'action': 'create_term', 'target_iri': 'urn:test:New',
        'kind': 'class', 'reason': 'new concept'})
    assert changed['revision'] == 2
    assert changed['operations'][0]['target_iri'] == 'urn:test:New'

    disposable = create_draft(
        client, project_id, ontology, title='Disposable')
    closed = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{disposable['id']}/close",
        json={'expected_revision': 1, 'actor': 'owner', 'reason': 'obsolete'})
    assert closed.status_code == 200
    assert closed.json()['status'] == 'rejected'


def test_revision_conflict_has_stable_exact_409_body(workbench):
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology)
    current = command(client, project_id, draft, {
        'action': 'create_term', 'target_iri': 'urn:test:One',
        'kind': 'class'})

    response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/validate",
        json={'expected_revision': 1})
    assert response.status_code == 409
    assert response.json() == {
        'detail': 'ontology draft revision changed',
        'code': 'revision_conflict',
        'details': {
            'draft_id': draft['id'],
            'expected_revision': 1,
            'current_revision': current['revision'],
        },
    }


def test_validation_submit_decision_and_idempotent_publish(workbench):
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology)
    draft = command(client, project_id, draft, {
        'action': 'add_annotation',
        'target_iri': 'https://example.test/ns#Child',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#comment',
        'value': 'Reviewed definition', 'language': 'en',
        'reason': 'improve documentation',
    })
    validated = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/validate",
        json={'expected_revision': draft['revision']}).json()
    submitted_response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/submit",
        json={'expected_revision': validated['revision']})
    assert submitted_response.status_code == 200, submitted_response.text
    submitted = submitted_response.json()
    operation = submitted['operations'][0]
    warnings = [
        item['code'] for item in submitted['validation_report']['warnings']
        if item.get('code')]
    decided_response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/decisions",
        json={
            'expected_revision': submitted['revision'],
            'expected_ontology_id': ontology['id'],
            'validation_fingerprint': submitted['validation_fingerprint'],
            'acknowledged_warning_codes': warnings,
            'actor': 'reviewer@example.test',
            'decisions': [{
                'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'],
                'action': 'approve', 'reason': 'verified',
            }],
        })
    assert decided_response.status_code == 200, decided_response.text
    reviewed = decided_response.json()
    assert reviewed['status'] == 'pending'

    payload = {
        'expected_revision': reviewed['revision'],
        'expected_ontology_id': ontology['id'],
        'validation_fingerprint': reviewed['validation_fingerprint'],
        'acknowledged_warning_codes': warnings,
        'idempotency_key': 'publish-api-test-1',
        'actor': 'publisher@example.test',
        'note': '用例：接口发布与幂等重放',
    }
    first = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/publish",
        json=payload)
    assert first.status_code == 200, first.text
    replay = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/publish",
        json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()['id'] == first.json()['id']


def test_review_plan_and_publish_readiness_are_read_only_get_endpoints(
        workbench):
    """审查计划与发布清单必须是只读 GET：读取前后草案修订号不变。"""
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology)
    draft = command(client, project_id, draft, {
        'action': 'add_annotation',
        'target_iri': 'https://example.test/ns#Child',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#label',
        'value': 'Child zh', 'language': 'zh',
    })
    submitted = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/submit",
        json={'expected_revision': draft['revision']}).json()

    plan_response = client.get(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/review-plan")
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert plan['counts'] == {'safe': 1, 'manual': 0, 'blocked': 0, 'total': 1}
    assert plan['items'][0]['batch_eligible'] is True
    assert plan['items'][0]['classification'] == 'safe'
    assert plan['policy_version'] == 'ontology-review-plan/1'

    readiness_response = client.get(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}"
        f"/publish-readiness")
    assert readiness_response.status_code == 200, readiness_response.text
    readiness = readiness_response.json()
    assert readiness['ready'] is False
    codes = {row['code'] for row in readiness['items']}
    # A3：状态里没有「已提交」这一档，改成 submitted_at 记事实；readiness 的条目也跟着改词 ——
    # 这份请求**提交过**（上面刚 POST 了 /submit），所以不再有 not_submitted，
    # 未满足的那一条是「还有变更没有最终决定」。
    assert 'decision_pending' in codes, codes
    assert 'not_submitted' not in codes, codes

    # 只读：两次 GET 之后草案修订号与状态都没有被改动
    after = client.get(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}").json()
    assert after['revision'] == submitted['revision']
    assert after['status'] == 'pending'


def test_validation_failure_and_batch_policy_are_stable_422(workbench):
    client, project_id, ontology = workbench
    empty = create_draft(client, project_id, ontology)
    response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{empty['id']}/submit",
        json={'expected_revision': 1})
    assert response.status_code == 422
    assert response.json()['code'] == 'validation_failed'
    assert response.json()['details'] == {}

    draft = create_draft(client, project_id, ontology, title='Batch policy')
    draft = command(client, project_id, draft, {
        'action': 'add_annotation',
        'target_iri': 'https://example.test/ns#Root',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#comment',
        'value': 'root comment'})
    draft = command(client, project_id, draft, {
        'action': 'add_annotation',
        'target_iri': 'https://example.test/ns#Child',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#comment',
        'value': 'child comment'})
    submitted = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/submit",
        json={'expected_revision': draft['revision']}).json()
    warnings = [
        item['code'] for item in submitted['validation_report']['warnings']
        if item.get('code')]
    decisions = [{
        'operation_id': operation['id'],
        'operation_fingerprint': operation['fingerprint'],
        'action': action, 'reason': 'reviewed',
    } for operation, action in zip(
        submitted['operations'], ('approve', 'reject'), strict=True)]
    rejected_batch = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/decisions",
        json={
            'expected_revision': submitted['revision'],
            'expected_ontology_id': ontology['id'],
            'validation_fingerprint': submitted['validation_fingerprint'],
            'acknowledged_warning_codes': warnings,
            'actor': 'reviewer', 'decisions': decisions,
        })
    assert rejected_batch.status_code == 422
    assert rejected_batch.json()['code'] == 'batch_not_allowed'


def test_hierarchy_pagination_dag_references_and_encoded_http_iri(workbench):
    client, project_id, ontology = workbench
    roots = client.get(
        f'/api/projects/{project_id}/ontology-hierarchy/roots',
        params={'ontology_id': ontology['id'], 'limit': 1})
    assert roots.status_code == 200
    assert roots.json()['next_cursor']
    second = client.get(
        f'/api/projects/{project_id}/ontology-hierarchy/roots',
        params={
            'ontology_id': ontology['id'], 'limit': 1,
            'cursor': roots.json()['next_cursor'],
        })
    assert second.status_code == 200
    assert second.json()['items'][0]['canonical_iri'] != (
        roots.json()['items'][0]['canonical_iri'])

    root_iri = 'https://example.test/ns#Root'
    children = client.get(
        f'/api/projects/{project_id}/ontology-hierarchy/children',
        params={'ontology_id': ontology['id'], 'iri': root_iri})
    assert children.status_code == 200, children.text
    child = children.json()['items'][0]
    assert child['canonical_iri'] == 'https://example.test/ns#Child'
    assert child['is_reference'] is True
    assert child['other_parent_count'] == 1

    neighborhood = client.get(
        f'/api/projects/{project_id}/ontology-hierarchy/neighborhood',
        params={'ontology_id': ontology['id'], 'iri': root_iri})
    assert neighborhood.status_code == 200
    assert neighborhood.json()['term']['iri'] == root_iri


def test_hierarchy_draft_overlay_search_and_matrix(workbench):
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology)
    draft = command(client, project_id, draft, {
        'action': 'create_term', 'target_iri': 'urn:test:Overlay',
        'kind': 'class'})
    draft = command(client, project_id, draft, {
        'action': 'add_parent', 'target_iri': 'urn:test:Overlay',
        'parent_iri': 'https://example.test/ns#Root'})

    children = client.get(
        f'/api/projects/{project_id}/ontology-hierarchy/children',
        params={
            'ontology_id': ontology['id'], 'draft_id': draft['id'],
            'iri': 'https://example.test/ns#Root',
        })
    assert children.status_code == 200, children.text
    assert 'urn:test:Overlay' in {
        item['canonical_iri'] for item in children.json()['items']}

    search = client.get(
        f'/api/projects/{project_id}/ontology-hierarchy/search',
        params={
            'ontology_id': ontology['id'], 'draft_id': draft['id'],
            'q': 'Overlay',
        })
    assert search.status_code == 200
    assert search.json()['items'][0]['canonical_iri'] == 'urn:test:Overlay'

    matrix = client.get(
        f'/api/projects/{project_id}/ontology-matrix',
        params={'ontology_id': ontology['id'], 'limit': 1})
    assert matrix.status_code == 200
    assert matrix.json()['items'][0]['kind'] == 'relation'


def test_rebase_requires_ontology_boundary_field(workbench):
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology)
    response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/rebase",
        json={'expected_revision': 1})
    assert response.status_code == 422

    changed = command(client, project_id, draft, {
        'action': 'create_term', 'target_iri': 'urn:test:Rebase',
        'kind': 'class'})
    latest = client.app.state.service.repository.bootstrap_ontology(
        project_id,
        BASE + '\n<urn:test:Latest> a <http://www.w3.org/2002/07/owl#Class> .',
        {})
    stale = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/validate",
        json={'expected_revision': changed['revision']})
    assert stale.status_code == 409
    assert stale.json()['code'] == 'stale_base'
    current = client.get(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}").json()
    rebased = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/rebase",
        json={
            'expected_revision': current['revision'],
            'expected_ontology_id': latest['id'],
        })
    assert rebased.status_code == 200, rebased.text
    assert rebased.json()['base_ontology_id'] == latest['id']


def submit(client, project_id, draft):
    response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/submit",
        json={'expected_revision': draft['revision']})
    assert response.status_code == 200, response.text
    return response.json()


def test_one_click_review_approves_only_auto_approvable_and_reports_the_rest(
        workbench):
    """一键审核：中风险但"可批准且无需理由"的变更整批通过，高风险项被如实挡下。"""
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology, title='一键审核')
    # 低风险注释（安全） + 中风险父级（可一键） + 高风险停用（必须人工）
    draft = command(client, project_id, draft, {
        'action': 'add_annotation',
        'target_iri': 'https://example.test/ns#Root',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#comment',
        'value': 'root comment'})
    draft = command(client, project_id, draft, {
        'action': 'create_term', 'target_iri': 'urn:test:Leaf',
        'kind': 'class'})
    draft = command(client, project_id, draft, {
        'action': 'add_parent', 'target_iri': 'urn:test:Leaf',
        'parent_iri': 'https://example.test/ns#Root'})
    draft = command(client, project_id, draft, {
        'action': 'retire_term', 'target_iri': 'https://example.test/ns#AltRoot',
        'reason': '不再使用'})
    submitted = submit(client, project_id, draft)

    plan = client.get(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}"
        f"/review-plan").json()
    auto = [item for item in plan['items'] if item['auto_approvable']]
    manual = [item for item in plan['items'] if not item['auto_approvable']]
    assert auto, plan['items']
    # 判据必须自洽：可一键批准的项绝不能要求写理由
    assert all(item['requires_reason'] is False for item in auto)
    assert all(item['classification'] in {'safe', 'manual'} for item in auto)
    # 中风险项确实进了批量集合（否则"一键审核"又变成只批低风险的形式主义）
    assert any(item['classification'] == 'manual' for item in auto)
    # 停用（高风险/被依赖阻断）必须留在人工集合里
    retire = [item for item in plan['items'] if item['action'] == 'retire_term']
    assert len(retire) == 1
    assert retire[0]['auto_approvable'] is False
    assert retire[0] in manual

    response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}"
        f"/decisions/batch-approve",
        json={
            'expected_revision': submitted['revision'],
            'expected_ontology_id': ontology['id'],
            'validation_fingerprint': submitted['validation_fingerprint'],
            'acknowledged_warning_codes': [],
            'actor': 'reviewer@workbench',
        })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['batch']['approved'] == len(auto)
    assert payload['batch']['skipped'] == len(manual)
    assert payload['batch']['skipped_operation_ids'] == [
        item['operation_id'] for item in manual]
    assert sum(payload['batch']['skipped_reasons'].values()) == len(manual)
    assert len(auto) >= 2  # 中风险 + 安全项都进了批量集合
    draft_after = payload['draft']
    # 还有变更没有决定（外部来源的整批被跳过），所以草案仍是「待处理」，
    # 而不是被批量放行到发布。
    assert draft_after['status'] == 'pending'
    decided = {row['operation_id']: row['action']
               for row in draft_after['decisions']}
    assert len(decided) == len(auto) and set(decided.values()) == {'approve'}
    assert {item['operation_id'] for item in auto} == set(decided)
    # 停用那条没有被偷偷批准
    assert retire[0]['operation_id'] not in decided


def test_one_click_review_is_stale_revision_safe(workbench):
    """一键审核带着过期修订号时必须整批拒绝，不产生部分决定。"""
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology, title='并发一致')
    draft = command(client, project_id, draft, {
        'action': 'create_term', 'target_iri': 'urn:test:One', 'kind': 'class'})
    draft = command(client, project_id, draft, {
        'action': 'create_term', 'target_iri': 'urn:test:Two', 'kind': 'class'})
    submitted = submit(client, project_id, draft)
    stale = submitted['revision'] - 1
    response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}"
        f"/decisions/batch-approve",
        json={
            'expected_revision': stale,
            'expected_ontology_id': ontology['id'],
            'validation_fingerprint': submitted['validation_fingerprint'],
            'acknowledged_warning_codes': [],
            'actor': 'reviewer@workbench',
        })
    assert response.status_code == 409
    assert response.json()['code'] == 'revision_conflict'
    after = client.get(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}").json()
    assert after['status'] == 'pending'
    assert after['decisions'] == []
    assert after['revision'] == submitted['revision']


def test_evidence_projection_is_read_only_and_documents_unknown_refs(workbench):
    """证据投影：接口只读，无法还原的引用如实标 unresolved，不猜内容。"""
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology, title='证据')
    draft = command(client, project_id, draft, {
        'action': 'create_term', 'target_iri': 'urn:test:Evidence',
        'kind': 'class'})
    before = client.get(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}").json()
    response = client.get(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/evidence")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload['items']) == {op['id'] for op in before['operations']}
    assert payload['counts'] == {'resolved': 0, 'unresolved': 0}
    assert 'explanation' in payload
    after = client.get(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}").json()
    assert after['revision'] == before['revision']


def test_evidence_entry_resolves_frozen_candidates_and_keeps_raw_ref():
    """引用解析：候选引用还原成文档/片段/置信度/原句；未知引用保留原始串。"""
    from knowledge_service.services.ontology_drafts import OntologyDrafts

    candidate = {
        'id': 'doc-1:chunk:0:discovery:entity_ab', 'assertion_id':
        'doc-1:chunk:0:discovery:entity_ab', 'document_id': 'doc-1',
        'document_version_id': 'ver-1', 'chunk_id': 'doc-1:chunk:0',
        'start_char': 67, 'end_char': 104, 'confidence': 0.95,
        'evidence': '外卖价为外卖商品/服务的销售价。', 'evidence_status': 'exact',
        'kind': 'relation', 'proposed_type': '为', 'document_title': '价格说明.md',
    }
    resolved = OntologyDrafts._evidence_entry(
        'candidate:doc-1:chunk:0:discovery:entity_ab',
        {'doc-1:chunk:0:discovery:entity_ab': candidate}, {})
    assert resolved['resolved'] is True
    assert resolved['document_title'] == '价格说明.md'
    assert resolved['evidence'] == '外卖价为外卖商品/服务的销售价。'
    assert resolved['confidence'] == 0.95

    versioned = OntologyDrafts._evidence_entry(
        'document-version:doc-1:ver-1', {}, {'doc-1': '价格说明.md'})
    assert versioned['kind'] == 'document_version'
    assert versioned['document_title'] == '价格说明.md'

    unknown = OntologyDrafts._evidence_entry('candidate:missing', {}, {})
    assert unknown['resolved'] is False
    assert unknown['ref'] == 'candidate:missing'
    assert 'evidence' not in unknown

def stage_gate(client, project_id, draft_id=None):
    params = {'draft_id': draft_id} if draft_id else None
    response = client.get(
        f'/api/projects/{project_id}/ontology-drafts/stage-availability',
        params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_stage_availability_is_the_single_source_of_truth_for_the_gate(workbench):
    """阶段门禁（能不能进 + 为什么 + 下一步）由服务端算，前端只渲染。

    这条只读接口存在的理由：状态本来就存在服务端（draft.status + 写操作的状态守卫），
    但"哪个阶段能打开"以前是浏览器里现推的 —— 刷新后没选中草案时审核/校验/发布全灰，
    用户只看到"点不开"，而服务端一直知道答案。这里走完整个生命周期，锁住每一步的回答。
    """
    client, project_id, ontology = workbench

    # ① 还没有草案：只有发现可进；设计锁住，原因里必须给出下一步（建草案）
    empty = stage_gate(client, project_id)
    assert empty['draft_id'] is None and empty['draft_count'] == 0
    assert empty['status'] is None and empty['status_label'] is None
    assert empty['stages']['discover']['allowed'] is True
    assert empty['stages']['design']['allowed'] is False
    assert '本体建模层' in empty['stages']['design']['reason'], '没草案时要指出去哪建（编辑台）'
    assert empty['next_action']['kind'] == 'new_draft'

    # ② 已有草案但一个都没选中：不能误导成"没有草案"，要说"去选一个"
    draft = create_draft(client, project_id, ontology)
    unselected = stage_gate(client, project_id)
    assert unselected['stages']['design']['allowed'] is True
    assert '选中' in unselected['stages']['review']['reason']
    assert unselected['next_action']['kind'] == 'select_draft'

    # ③ 还没提交（待处理里的第一个细分）：设计与收件箱都可进 —— 收件箱那一页按
    #    pending_phase 分工，这一档它给的是"去哪里改"的指路卡（A2-c：审核台没有提交按钮，
    #    它只把用户送过去）。所以"下一步"是去「本体建模层」，不是在审核台提交。
    editing = stage_gate(client, project_id, draft['id'])
    assert editing['status'] == 'pending' and editing['status_label'] == '待处理'
    # A3："还没提交 / 已提交待处理 / 全部有决定" 三种处境由服务端**推导**给出。
    assert editing['pending_phase'] == 'editing'
    assert '本体建模层' in editing['status_copy']
    assert editing['stages']['design']['allowed'] is True
    # A3：publish 不再是阶段（收下就是发布）—— 阶段表里根本没有这个 key。
    assert set(editing['stages']) == {'discover', 'design', 'review'}
    # 收件箱这一扇门对"待处理"一律开着：还没提交时它渲染的是指路卡（去哪里改），
    # 不是空的审核队列 —— 所以门禁说"能进"并不撒谎。真正决定阶段的是服务端推导的
    # pending_phase，以及状态条上那唯一的下一步。
    assert editing['stages']['review']['allowed'] is True
    action = editing['next_action']
    assert action['kind'] == 'design', '编辑中的草案：下一步是去「本体建模层」，不是在审核台提交'
    assert '本体建模层' in action['label'] and action['stage'] == 'review'
    # 它是"跳转"不是"提交"：提交按钮才有"没变更就不能点"的语义，跳转永远可点。
    assert action['enabled'] is True

    # ④ 有变更后：下一步仍是"去本体建模层"，但说明里要报出**有多少条变更等着提交**
    #    （用户据此判断"那边是不是真的有东西"）。
    draft = command(client, project_id, draft, {
        'action': 'add_annotation',
        'target_iri': 'https://example.test/ns#Child',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#comment',
        'value': 'Stage gate', 'language': 'en', 'reason': 'stage gate test',
    })
    changed = stage_gate(client, project_id, draft['id'])
    assert changed['next_action']['kind'] == 'design'
    assert '本体建模层' in changed['next_action']['title']
    assert '1 条变更' in changed['next_action']['title'], changed['next_action']['title']

    # ⑤ 提交后：收件箱打开（进入时自动跑校验）；"发布"不是阶段，它由 next_action 表达。
    submitted = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/submit",
        json={'expected_revision': draft['revision']}).json()
    pending = stage_gate(client, project_id, draft['id'])
    # A3：提交不改状态（还是「待处理」），但"已提交"记在 submitted_at 上 → 推导阶段是 reviewing。
    assert pending['status'] == 'pending' and pending['status_label'] == '待处理'
    assert pending['pending_phase'] == 'reviewing'
    assert pending['stages']['review']['allowed'] is True
    assert pending['next_action']['kind'] == 'review'
    assert pending['next_action']['label'] == '去收件箱逐条处理'

    # ⑤.5 /validate 仍可重复调用（审核阶段自动跑的就是它）；通过后门禁不变：审核开、发布锁。
    confirmed = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/validate",
        json={'expected_revision': submitted['revision']}).json()
    ready_for_review = stage_gate(client, project_id, draft['id'])
    assert ready_for_review['stages']['review']['allowed'] is True
    assert ready_for_review['pending_phase'] == 'reviewing'
    assert ready_for_review['next_action']['kind'] == 'review'

    # ⑥ 逐项决定做完：可以收下并发布了（"发布"由 next_action 表达，不是一扇门）
    operation = confirmed['operations'][0]
    warnings = [
        item['code'] for item in confirmed['validation_report']['warnings']
        if item.get('code')]
    reviewed = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/decisions",
        json={
            'expected_revision': confirmed['revision'],
            'expected_ontology_id': ontology['id'],
            'validation_fingerprint': confirmed['validation_fingerprint'],
            'acknowledged_warning_codes': warnings,
            'actor': 'reviewer@example.test',
            'decisions': [{
                'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'],
                'action': 'approve', 'reason': 'verified',
            }],
        }).json()
    assert reviewed['status'] == 'pending'
    ready = stage_gate(client, project_id, draft['id'])
    assert ready['pending_phase'] == 'settled'
    assert ready['next_action']['kind'] == 'publish'

    # ⑦ 不属于本项目的 draft_id 要退化成"没选中"，不能 500（前端刷新时会带旧 id）
    unknown = stage_gate(client, project_id, 'not-a-draft')
    assert unknown['draft_id'] is None
    assert unknown['stages']['design']['allowed'] is True
    # ⑧ 不存在的项目仍按其他只读接口的口径 404
    assert client.get(
        '/api/projects/nope/ontology-drafts/stage-availability').status_code == 404


def test_terminal_draft_locks_every_stage_but_discover(workbench):
    """已收下 / 已驳回的请求是"只读账本"：只有「发现」还能进，其余门必须锁住。

    这条守的是真机发现的冲突（项目 1001_开放，草案已发布时实测）：

        修复前  design allowed=False（"本轮已发布完毕…请去「本体建模层」"）
                validate/review/publish allowed=True    ← 三扇门全开

    同一屏上"设计灰着 + 黄条写着进不去 + 审核/发布却亮着能点"，就是用户说的
    "多个环节的流程上有冲突"。根因是当时用"已提交过"的状态集合推导，而它含已发布
    的草案；门后的写操作只认"还能发布"的状态，所以点进去必然被服务端拒绝 —— 门禁在撒谎。
    """
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology)
    draft = command(client, project_id, draft, {
        'action': 'add_annotation',
        'target_iri': 'https://example.test/ns#Child',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#comment',
        'value': 'Terminal gate', 'language': 'en', 'reason': 'terminal gate test',
    })
    validated = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/validate",
        json={'expected_revision': draft['revision']}).json()
    submitted = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/submit",
        json={'expected_revision': validated['revision']}).json()
    operation = submitted['operations'][0]
    warnings = [
        item['code'] for item in submitted['validation_report']['warnings']
        if item.get('code')]
    reviewed = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/decisions",
        json={
            'expected_revision': submitted['revision'],
            'expected_ontology_id': ontology['id'],
            'validation_fingerprint': submitted['validation_fingerprint'],
            'acknowledged_warning_codes': warnings,
            'actor': 'reviewer@example.test',
            'decisions': [{
                'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'],
                'action': 'approve', 'reason': 'verified',
            }],
        }).json()
    assert reviewed['status'] == 'pending'
    published = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/publish",
        json={
            'expected_revision': reviewed['revision'],
            'expected_ontology_id': ontology['id'],
            'validation_fingerprint': reviewed['validation_fingerprint'],
            'acknowledged_warning_codes': warnings,
            'idempotency_key': 'terminal-gate-test-1',
            'actor': 'publisher@example.test',
            'note': '用例：终态门禁前的正常发布',
        })
    assert published.status_code == 200, published.text

    gate = stage_gate(client, project_id, draft['id'])
    assert gate['status'] == 'accepted' and gate['status_label'] == '已收下'
    assert gate['stages']['discover']['allowed'] is True
    assert gate['next_action']['kind'] == 'discover'
    # A3：发布不是阶段 —— 阶段表里没有 publish 这个 key。
    assert set(gate['stages']) == {'discover', 'design', 'review'}
    # 设计门：锁着，并把用户送去唯一能改结构的地方
    assert gate['stages']['design']['allowed'] is False
    assert '本体建模层' in gate['stages']['design']['reason']
    # 收件箱门：一律锁着（修复前全开），理由指向回看与再改的去处，
    # 而不是让用户去点另一个同样锁着的门
    assert gate['stages']['review']['allowed'] is False
    assert '本体建模层' in gate['stages']['review']['reason']
    assert '本体建模层' in gate['stages']['review']['reason']
    # 终态不再有"细分阶段 / 需要重新基线"这类跟随状态的推导字段
    assert gate['pending_phase'] is None and gate['needs_rebase'] is None

    # 反过来确认"门禁没撒谎"：真去发布，服务端也拒绝（published 不在可发布状态里）。
    # 用新的幂等键，避免被"同一把键重放"那条路径吸收掉，测的才是状态守卫本身。
    current = client.get(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}").json()
    again = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/publish",
        json={
            'expected_revision': current.get('draft', current).get('revision'),
            'expected_ontology_id': ontology['id'],
            'validation_fingerprint': reviewed['validation_fingerprint'],
            'acknowledged_warning_codes': warnings,
            'idempotency_key': 'terminal-gate-test-2',
            'actor': 'publisher@example.test',
            'note': '用例：终态草案不许再发',
        })
    # A3：写操作的门禁抛 DraftImmutable（code=draft_immutable）。它不属于"修订冲突/基线过期"
    # 那几个 409，所以**断言语义**（code）而不是传输层状态码 —— 后者会随映射表变化而变。
    assert again.status_code == 422, again.text
    assert again.json()['code'] == 'draft_immutable'
    # 拒绝之后项目里的已发布版本数不变：终态草案不会再产出第二个版本
    versions = client.get(f'/api/projects/{project_id}/ontologies').json()['versions']
    assert len([item for item in versions if item['id'] == published.json()['id']]) == 1


def test_terminal_draft_beside_editable_draft_still_locks_design(workbench):
    """多草案并存时，选中一份**终态**草案 → 「设计」必须锁住并说清下一步去哪。

    这条守的是真机复验脚本 `scripts/verify_ontology_gate.py` 挖出来的洞
    （真项目 13c51a63：13 份编辑中 + 4 份已关闭）。

    修复前 `design = entry(has_active_draft, design_reason)` 只问"**项目里**有没有
    未完结草案"，完全不看"**被选中的是哪一份**"：

        has_active_draft = 项目里有别的 editing 草案 → True
        → 用户在左栏点了一份已关闭的草案，「设计」按钮照样亮着；
        → 点进去只有只读视图（renderDesignActions 对终态草案不给编辑），
        → 而且走到 `else: design_reason = ''` **一个字的解释都没有**。

    门禁说"能进"而实际进不去、还不给理由，就是撒谎。
    单个草案的用例（test_terminal_draft_locks_every_stage_but_discover）碰不到它，
    因为那份用例里项目只有一份草案，会走 `not has_active_draft` 分支。
    """
    client, project_id, ontology = workbench
    editable = create_draft(client, project_id, ontology, title='可编辑的那份')
    discarded = create_draft(client, project_id, ontology, title='要被关闭的那份')
    closed = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{discarded['id']}/close",
        json={'expected_revision': discarded['revision'], 'reason': '不做了',
              'actor': 'architect@example.test'})
    assert closed.status_code == 200, closed.text
    assert closed.json()['status'] == 'rejected'

    # 选中被关闭的那份：设计必须锁，且必须告诉用户"去选中另一份，或去设计台新建"。
    gate = stage_gate(client, project_id, discarded['id'])
    assert gate['status'] == 'rejected'
    assert gate['stages']['design']['allowed'] is False, (
        '项目里还有别的可编辑草案时，选中的终态草案不该让「设计」亮起来')
    reason = gate['stages']['design']['reason']
    assert reason, '锁住了就必须写原因，不能留空'
    # 它此刻该指向的是"换一份草案"与"设计台新建"，而不是"去看历史版本"。
    assert '本体建模层' in reason
    # A3："换一份继续改"指的是「待处理」的草案（旧词汇叫「编辑中」）。
    assert '待处理' in reason
    # 终态的其余门照旧全锁。
    assert gate['stages']['review']['allowed'] is False
    assert '本体建模层' in gate['stages']['review']['reason']

    # 同一份项目里换成可编辑的那份：设计重新放行（判定落在选中的草案上，不是项目上）。
    editable_gate = stage_gate(client, project_id, editable['id'])
    assert editable_gate['stages']['design']['allowed'] is True

    # 一份都没选中：仍放行（这是"还没选"，不是"不允许"），进去有"选一个"的提示。
    unselected = stage_gate(client, project_id)
    assert unselected['stages']['design']['allowed'] is True


