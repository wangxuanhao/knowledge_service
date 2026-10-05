"""C1「结构待定」服务端契约：知识不丢、标出来、能查、不进证据链、本体补齐后自动解除。

对应《完整落地规划》§4.1 与 §8 的 C1 验收口径：
    造一条含未知概念的知识 → 断言它被标 `结构待定`、可检索、问答引用时显式标注、不进证据链。

这里全部走真实服务对象（KnowledgeService + 真实本体校验），不 mock 校验结果 ——
C1 的价值恰恰在"遇到未知术语时到底放行还是拒绝"，mock 掉校验就等于什么都没测。
"""
import pytest

from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import Repository
from knowledge_service.services import structure_pending
from knowledge_service.services.answers import question_context
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.service import KnowledgeService

TTL_V1 = ('@prefix ex: <http://ex/> . '
          '@prefix owl: <http://www.w3.org/2002/07/owl#> . '
          'ex:Person a owl:Class .')


def _service(tmp_path, name='structure.sqlite', ttl=TTL_V1):
    repo = Repository(tmp_path / name)
    service = KnowledgeService(repo, HashingEncoder())
    project_id = repo.create_project('甲')['id']
    repo.save_ontology(project_id, ttl, Ontology(ttl).summary())
    return repo, service, project_id


def test_unknown_concept_is_accepted_and_marked_pending(tmp_path):
    """未知术语不再整批拒绝：知识收下、打标、说明缺的是哪个概念。"""
    repo, service, project_id = _service(tmp_path)
    rows = service.write(project_id, [
        {'id': 'known', 'kind': 'entity', 'type': 'Person', 'text': '张三'},
        {'id': 'pending', 'kind': 'entity', 'type': 'urn:knowledge:ontology:甲:包裹', 'text': '一个包裹'},
    ])
    assert [row['id'] for row in rows] == ['known', 'pending']
    stored = {row['id']: row for row in service.scoped(project_id, {})}
    assert 'structure_pending' not in stored['known'], '类型正常的记录不该出现结构待定标记'
    status = stored['pending']['structure_pending']
    assert status['state'] == 'pending'
    assert status['terms'] == ['urn:knowledge:ontology:甲:包裹']
    # 标记落进 metadata（可追溯"当初缺的是什么"），且它是事实而非判据
    marker = stored['pending']['metadata']['structure_pending']
    assert marker['terms'] == ['urn:knowledge:ontology:甲:包裹']
    assert marker['reason'] == structure_pending.REASON_UNKNOWN_TERM
    assert marker['marked_at']
    # 知识本体没丢：文字/类型都还在，检索范围里查得到
    assert stored['pending']['text'] == '一个包裹'


def test_pending_knowledge_stays_searchable_and_visible(tmp_path):
    """可检索、可看原文：它出现在范围读取与关键词检索结果里（只是带标记）。"""
    repo, service, project_id = _service(tmp_path)
    service.write(project_id, [{'id': 'pending', 'kind': 'entity',
                                'type': 'urn:knowledge:ontology:甲:包裹', 'text': '跨城包裹'}])
    rows = service.scoped(project_id, {'kinds': ['entity']})
    assert [row['id'] for row in rows] == ['pending']
    hits = service.search(project_id, {'query': '跨城包裹', 'k': 5})
    assert any(hit['id'] == 'pending' for hit in hits['hits']), '结构待定的知识仍然要能搜到'


def test_pending_endpoint_makes_its_relation_pending(tmp_path):
    """端点传染：端点实体待定时，关系也待定（domain/range 没法校验），并说明是哪一端。"""
    repo, service, project_id = _service(
        tmp_path, 'relation.sqlite',
        ('@prefix ex: <http://ex/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . '
         'ex:Person a owl:Class . ex:knows a owl:ObjectProperty .'))
    service.write(project_id, [
        {'id': 'who', 'kind': 'entity', 'type': 'Person', 'text': '张三'},
        {'id': 'thing', 'kind': 'entity', 'type': 'urn:knowledge:ontology:甲:包裹', 'text': '包裹'},
        {'id': 'rel', 'kind': 'relation', 'type': 'knows', 'text': '张三认识包裹',
         'subject_id': 'who', 'object_id': 'thing'},
    ])
    rows = {row['id']: row for row in service.scoped(project_id, {})}
    assert rows['rel']['structure_pending']['state'] == 'pending'
    assert rows['rel']['structure_pending']['via'] == ['thing']
    assert rows['rel']['structure_pending']['reason'] == structure_pending.REASON_ENDPOINT_TERM
    # 传染时记的必须是**真正缺的那一端**：先看向 subject 会把这条关系错记到
    # "人物"名下 —— 真机复验抓到过（关系被归到「文件」那一组）。
    assert rows['rel']['structure_pending']['terms'] == ['urn:knowledge:ontology:甲:包裹'], \
        rows['rel']['structure_pending']
    assert rows['rel']['metadata']['structure_pending']['terms'] == ['urn:knowledge:ontology:甲:包裹']


def test_other_failures_are_still_rejected(tmp_path):
    """只放行"缺术语"这一种失败：约束违规照旧拒绝 —— 标错了会把真问题盖掉。"""
    from knowledge_service.services.service import OntologyValidationError

    repo, service, project_id = _service(
        tmp_path, 'constraint.sqlite',
        ('@prefix ex: <http://ex/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . '
         'ex:Person a owl:Class . ex:City a owl:Class . '
         'ex:livesIn a owl:ObjectProperty ; '
         '  <http://www.w3.org/2000/01/rdf-schema#domain> ex:Person ; '
         '  <http://www.w3.org/2000/01/rdf-schema#range> ex:City .'))
    service.write(project_id, [{'id': 'p1', 'kind': 'entity', 'type': 'Person', 'text': '张三'},
                               {'id': 'c1', 'kind': 'entity', 'type': 'City', 'text': '杭州'},
                               {'id': 'c2', 'kind': 'entity', 'type': 'City', 'text': '苏州'}])
    with pytest.raises(OntologyValidationError):
        service.write(project_id, [{'id': 'bad', 'kind': 'relation', 'type': 'livesIn',
                                    'text': '杭州住在苏州', 'subject_id': 'c1', 'object_id': 'c2'}])


def test_pending_records_are_not_inputs_to_constraint_validation(tmp_path):
    """不作为约束校验的输入：校验接口把待定记录单独列出，并说明为什么跳过。"""
    from fastapi.testclient import TestClient
    from knowledge_service.api import create_app

    with TestClient(create_app(tmp_path / 'validate.sqlite', encoder=HashingEncoder())) as client:
        project_id = client.post('/api/projects', json={
            'name': '甲', 'use_default_ontology': False}).json()['id']
        ttl = ('@prefix ex: <http://ex/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . '
               'ex:Person a owl:Class .')
        ontology = client.post(f'/api/projects/{project_id}/ontologies',
                               json={'turtle': ttl, 'expected_ontology_id': None}).json()
        assert ontology.get('id'), ontology
        created = client.post(f'/api/projects/{project_id}/records', json={'records': [
            {'id': 'known', 'kind': 'entity', 'type': 'Person', 'text': '张三'},
            {'id': 'pending', 'kind': 'entity',
             'type': 'urn:knowledge:ontology:x:包裹', 'text': '一个包裹'}]})
        assert created.status_code == 201, created.text
        body = client.post(f'/api/projects/{project_id}/ontology/validate', json={}).json()
    assert body['conforms'] is True, body
    assert [item['id'] for item in body['structure_pending_skipped']] == ['pending']
    assert '本体建模层' in body['structure_pending_note']


def test_answer_evidence_chain_excludes_pending_and_labels_it(tmp_path):
    """问答：待定知识不进证据链，但**显式标注**出来（能查、能引用、不算正式证据）。"""
    repo, service, project_id = _service(tmp_path, 'answer.sqlite')
    service.write(project_id, [{'id': 'known', 'kind': 'entity', 'type': 'Person', 'text': '张三'},
                               {'id': 'pending', 'kind': 'entity',
                                'type': 'urn:knowledge:ontology:甲:包裹', 'text': '一个包裹'}])
    request = {'query': '包裹', 'k': 10, 'k_entities': 5, 'k_chunks': 5,
               'retrieval_mode': 'keyword', 'hops': 0}
    context = question_context(service, project_id, request)
    evidence_ids = [row['id'] for row in context['evidence_rows']]
    pending_ids = [row['id'] for row in context['structure_pending_rows']]
    assert 'pending' in pending_ids, '待定知识必须作为"不算证据"单独列出来，不能静默消失'
    assert 'pending' not in evidence_ids
    assert context['structure_pending_rows'][0]['structure_pending']['terms']


def test_state_clears_automatically_once_the_concept_is_modeled(tmp_path):
    """收下之后自动解除：概念进本体（发布新版）→ 状态变已解除，标记仍留在记录上作历史。"""
    repo, service, project_id = _service(tmp_path, 'clear.sqlite')
    service.write(project_id, [{'id': 'pending', 'kind': 'entity',
                                'type': 'urn:knowledge:ontology:甲:包裹', 'text': '一个包裹'}])
    assert service.structure_pending_report(project_id)['pending_structure'] == 1
    # 概念必须建成**同一个 IRI**（记录的类型就是它）：这是"本体补上了这个概念"的真实形态
    ttl_v2 = (TTL_V1 + ' @prefix pkg: <urn:knowledge:ontology:甲:> . pkg:包裹 a owl:Class .')
    repo.save_ontology(project_id, ttl_v2, Ontology(ttl_v2).summary())
    report = service.structure_pending_report(project_id)
    assert report['pending_structure'] == 0
    assert report['pending_records'] == 0
    row = service.scoped(project_id, {})[0]
    assert row['structure_pending']['state'] == 'cleared'
    assert row['structure_pending']['terms'] == []
    assert row['structure_pending']['marked'] == ['urn:knowledge:ontology:甲:包裹']
    assert row['metadata']['structure_pending']['terms'] == ['urn:knowledge:ontology:甲:包裹']


def test_inbox_groups_terms_and_suggests_the_action(tmp_path):
    """收件箱：按概念分组（不是按记录），给出条数、样例与"该去哪儿做什么"。"""
    repo, service, project_id = _service(tmp_path, 'inbox.sqlite')
    service.write(project_id, [
        {'id': 'p1', 'kind': 'entity', 'type': 'urn:knowledge:ontology:甲:包裹', 'text': '包裹一'},
        {'id': 'p2', 'kind': 'entity', 'type': 'urn:knowledge:ontology:甲:包裹', 'text': '包裹二'},
        {'id': 'p3', 'kind': 'entity', 'type': 'urn:knowledge:ontology:甲:寄件人', 'text': '王某'},
        {'id': 'ok', 'kind': 'entity', 'type': 'Person', 'text': '张三'},
    ])
    report = service.structure_pending_report(project_id)
    assert report['pending_structure'] == 2, '数字是"待建模的概念个数"，不是记录数'
    assert report['pending_records'] == 3
    by_term = {item['term']: item for item in report['terms']}
    assert by_term['urn:knowledge:ontology:甲:包裹']['record_count'] == 2
    assert [item['id'] for item in by_term['urn:knowledge:ontology:甲:包裹']['records']] == ['p1', 'p2']
    assert '本体建模层' in by_term['urn:knowledge:ontology:甲:包裹']['suggested_action']
    assert report['note'] and report['definitions']['cleared']


def test_marker_alone_does_not_make_a_record_pending(tmp_path):
    """标记是事实、状态是推导：类型能解析时，光有标记也不算待定（防止两套判据）。"""
    repo, service, project_id = _service(tmp_path, 'marker.sqlite')
    service.write(project_id, [{'id': 'x', 'kind': 'entity', 'type': 'Person', 'text': '张三'}])
    repo.save_ontology(project_id, TTL_V1, Ontology(TTL_V1).summary())
    rows = service.scoped(project_id, {})
    record = rows[0]
    structure_pending.mark(record, ['某些历史术语'])
    ontology = service.structure_pending_ontology(project_id)
    assert structure_pending.state(ontology, record)['state'] == 'cleared'
    assert structure_pending.annotate(ontology, [record]) == 0

