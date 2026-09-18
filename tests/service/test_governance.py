import pytest
from knowledge_service.repository import Repository
from knowledge_service.embeddings import HashingEncoder
from knowledge_service.service import KnowledgeService
from knowledge_service.ontology import Ontology


@pytest.fixture
def system(tmp_path):
    from knowledge_service.governance import Governance
    repo = Repository(tmp_path/'govern.sqlite')
    service = KnowledgeService(repo, HashingEncoder())
    project = repo.create_project('govern')['id']
    ttl = '@prefix ex: <http://ex/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . ex:Person a owl:Class . ex:knows a owl:ObjectProperty .'
    repo.save_ontology(project, ttl, Ontology(ttl).summary())
    service.write(project, [
        dict(id='a',kind='entity',type='Person',text='张三',metadata={'city':'北京'}),
        dict(id='b',kind='entity',type='Person',text='张先生',metadata={'city':'北京','source':'other'}),
        dict(id='c',kind='entity',type='Person',text='李四',metadata={'city':'上海'}),
        dict(id='r',kind='relation',type='knows',text='相识',subject_id='b',object_id='c')])
    yield service, Governance(service), project
    repo.close()


def test_alias_merge_and_restore_preserve_history(system):
    service, governance, p = system
    alias = governance.alias(p,'a','张老师',1)
    assert governance.resolve(p,'张老师')['canonical']['id'] == 'a'
    merged = governance.merge(p,'a','b',{'a':alias['version'],'b':1})
    assert merged['backend'] == 'semantica'
    current = {r['id']:r for r in service.scoped(p,{})}
    assert 'b' not in current and current['r']['subject_id'] == 'a'
    assert '张先生' in current['a']['metadata']['aliases']
    assert current['a']['metadata']['merged_sources'][0]['id'] == 'b'
    history = service.repository.history(p,'b')
    assert len(history) == 2
    old = service.scoped(p,{'known_at':history[0]['recorded_at']})
    assert 'b' in {r['id'] for r in old}
    restored = governance.undo_merge(p, merged['operation_id'])
    assert restored['restored'] == 3
    current = {r['id']:r for r in service.scoped(p,{})}
    assert current['r']['subject_id'] == 'b' and 'b' in current


def test_merge_conflicts_and_time_incompatibility(system):
    service, governance, p = system
    with pytest.raises(ValueError, match='版本冲突'):
        governance.merge(p,'a','b',{'a':20,'b':1})
    row = service.repository.current_records(p)[0]
    row = {k:v for k,v in row.items() if k not in {'version','version_id','project_id','recorded_at','superseded_at'}}
    row['valid_until']='2030-01-01'
    service.repository.put_record(p,row)
    with pytest.raises(ValueError,match='区间'):
        governance.merge(p,'a','b',{'a':2,'b':1})


def test_resolve_returns_all_exact_duplicates_for_two_entity_selection(system):
    service, governance, p = system
    service.write(p,[dict(id='duplicate-a',kind='entity',type='Person',text='张三',metadata={})])
    result=governance.resolve(p,'张三')
    assert result['status']=='exact_duplicates'
    assert result['canonical'] is None
    assert {item['id'] for item in result['candidates']}=={'a','duplicate-a'}
    assert all(item['score']==1 for item in result['candidates'])


def test_delete_cascades_edges_and_restore(system):
    service, governance, p = system
    result = governance.delete(p,'b',1)
    assert result['deleted'] == 2
    assert {r['id'] for r in service.scoped(p,{})} == {'a','c'}
    governance.undo_merge(p,result['operation_id'])
    assert {r['id'] for r in service.scoped(p,{})} == {'a','b','c','r'}
