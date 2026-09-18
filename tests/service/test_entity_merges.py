from knowledge_service.embeddings import HashingEncoder
from knowledge_service.governance import Governance
from knowledge_service.ontology import Ontology
from knowledge_service.repository import Repository
from knowledge_service.service import KnowledgeService


def test_merge_ledgers_assertion_reassignment_and_reversal(tmp_path):
    repo = Repository(tmp_path / 'merge.sqlite')
    service = KnowledgeService(repo, HashingEncoder())
    project_id = repo.create_project('甲')['id']
    ttl = ('@prefix ex: <http://ex/> . '
           '@prefix owl: <http://www.w3.org/2002/07/owl#> . '
           'ex:Person a owl:Class .')
    repo.save_ontology(project_id, ttl, Ontology(ttl).summary())
    service.write(project_id, [
        {'id': 'keep', 'kind': 'entity', 'type': 'Person', 'text': '张三'},
        {'id': 'drop', 'kind': 'entity', 'type': 'Person', 'text': '张先生'},
    ])
    governance = Governance(service)
    review=repo.create_resolution_review(project_id,{
        'id':'resolution-merge','source_entity_id':'drop','candidate_entity_id':'keep',
        'score':.82,'payload':{'reason':'gray_band'}})

    merged = governance.merge(project_id, 'keep', 'drop', {'keep': 1, 'drop': 1},
        resolution_decision={'id':review['id'],'expected_version':1,'decision':'merged',
            'reason':'人工确认','actor':'reviewer'})

    ledgers = repo.list_merge_operations(project_id)
    assert ledgers[-1]['id'] == merged['operation_id']
    assert ledgers[-1]['operation'] == 'merge_rewrite'
    assert ledgers[-1]['redirects'] == {'drop': 'keep'}
    drop_support = repo.list_assertions(project_id, canonical_record_id='drop')
    keep_support = repo.list_assertions(project_id, canonical_record_id='keep')
    assert drop_support == []
    assert len(keep_support) >= 2
    assert repo.list_resolution_reviews(project_id,status='merged')[0]['id']==review['id']

    reversed_ = governance.undo_merge(project_id, merged['operation_id'])
    ledgers = repo.list_merge_operations(project_id)
    assert ledgers[-1]['operation'] == 'merge_reversal'
    assert ledgers[-1]['reversal_of'] == merged['operation_id']
    assert reversed_['restored'] == 2


def test_merge_consolidates_colliding_facts_and_reversal_restores_them(tmp_path):
    repo = Repository(tmp_path / 'collision.sqlite')
    service = KnowledgeService(repo, HashingEncoder())
    project_id = repo.create_project('甲')['id']
    ttl = ('@prefix ex: <http://ex/> . '
           '@prefix owl: <http://www.w3.org/2002/07/owl#> . '
           'ex:Person a owl:Class . ex:knows a owl:ObjectProperty .')
    repo.save_ontology(project_id, ttl, Ontology(ttl).summary())
    service.write(project_id, [
        {'id': 'keep', 'kind': 'entity', 'type': 'Person', 'text': '张三'},
        {'id': 'drop', 'kind': 'entity', 'type': 'Person', 'text': '张先生'},
        {'id': 'other', 'kind': 'entity', 'type': 'Person', 'text': '李四'},
        {'id': 'a-rel', 'kind': 'relation', 'type': 'knows', 'text': '张三认识李四',
         'subject_id': 'keep', 'object_id': 'other'},
        {'id': 'z-rel', 'kind': 'relation', 'type': 'knows', 'text': '张先生认识李四',
         'subject_id': 'drop', 'object_id': 'other'},
    ])
    governance = Governance(service)

    merged = governance.merge(project_id, 'keep', 'drop', {'keep': 1, 'drop': 1})

    visible = [row for row in service.scoped(project_id, {}) if row['kind'] == 'relation']
    assert [row['id'] for row in visible] == ['a-rel']
    assert len(repo.list_fact_keys(project_id)) == 1
    assert len(repo.list_assertions(project_id, status='accepted', canonical_record_id='a-rel')) == 2

    governance.undo_merge(project_id, merged['operation_id'])
    visible = [row for row in service.scoped(project_id, {}) if row['kind'] == 'relation']
    assert {row['id'] for row in visible} == {'a-rel', 'z-rel'}
    assert len(repo.list_fact_keys(project_id)) == 2
