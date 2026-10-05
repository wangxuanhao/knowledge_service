from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.governance import Governance
from knowledge_service.services.ontology import Ontology
from knowledge_service.repository import Repository
from knowledge_service.services.service import KnowledgeService


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

def test_duplicate_groups_only_groups_same_name_and_same_type(tmp_path):
    """「疑似重复」的口径：名称完全相同（忽略大小写与空白）且本体类型相同 —— 刻意不做模糊匹配。

    为什么这么窄：这个数字是给用户核对的。模糊分能给出"12 组"这种好看的数字，
    但用户点进去会问"这三条凭什么算重复"——那是查重向导（语义档）该回答的问题，
    不是顶部那个数字该背的锅。软删除的实体不参与统计。
    """
    repo = Repository(tmp_path / 'dupes.sqlite')
    service = KnowledgeService(repo, HashingEncoder())
    project_id = repo.create_project('乙')['id']
    ttl = ('@prefix ex: <http://ex/> . '
           '@prefix owl: <http://www.w3.org/2002/07/owl#> . '
           'ex:Person a owl:Class . ex:Company a owl:Class .')
    repo.save_ontology(project_id, ttl, Ontology(ttl).summary())
    service.write(project_id, [
        {'id': 'a1', 'kind': 'entity', 'type': 'Person', 'text': '张三'},
        {'id': 'a2', 'kind': 'entity', 'type': 'Person', 'text': '  张三 '},
        {'id': 'a3', 'kind': 'entity', 'type': 'Company', 'text': '张三'},
        {'id': 'b1', 'kind': 'entity', 'type': 'Person', 'text': '李四'},
        {'id': 'b2', 'kind': 'entity', 'type': 'Person', 'text': '李四'},
    ])
    # 软删除走真正的删除通道（它会给记录盖 _deleted 标记），不绕过治理层直接改记录
    Governance(service).delete(project_id, 'b2', 1)

    groups = Governance(service).duplicate_groups(project_id)

    assert [group['name'] for group in groups] == ['张三'], groups
    assert {member['id'] for member in groups[0]['members']} == {'a1', 'a2'}
    assert groups[0]['type'] == 'Person'
    assert all(member['ontology_id'] for member in groups[0]['members'])
