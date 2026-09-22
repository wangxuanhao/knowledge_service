import pytest
from knowledge_service.repository import Repository
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.service import KnowledgeService
from knowledge_service.services.ontology import Ontology
from knowledge_service.api.parity import Merge


@pytest.fixture
def system(tmp_path):
    from knowledge_service.services.governance import Governance
    repo = Repository(tmp_path/'govern.sqlite')
    service = KnowledgeService(repo, HashingEncoder())
    project = repo.create_project('govern')['id']
    ttl = '''@prefix ex: <http://ex/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
    @prefix sh: <http://www.w3.org/ns/shacl#> .
    ex:Person a owl:Class . ex:knows a owl:ObjectProperty .
    ex:age a owl:DatatypeProperty; rdfs:domain ex:Person; rdfs:range xsd:integer .
    ex:nickname a owl:DatatypeProperty; rdfs:domain ex:Person; rdfs:range xsd:string .
    ex:PersonShape a sh:NodeShape; sh:targetClass ex:Person;
      sh:property [sh:path ex:age; sh:maxCount 1; sh:datatype xsd:integer] .'''
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


def test_merge_rewrites_attributes_converges_equal_values_and_keeps_unconstrained_values(system):
    service, governance, p = system
    integer = 'http://www.w3.org/2001/XMLSchema#integer'
    string = 'http://www.w3.org/2001/XMLSchema#string'
    service.write(p, [
        dict(id='age-a', kind='attribute', type='age', text='年龄 20',
             subject_id='a', value=20, datatype=integer),
        dict(id='age-b', kind='attribute', type='age', text='也是年龄 20',
             subject_id='b', value=20, datatype=integer),
        dict(id='nickname-a', kind='attribute', type='nickname', text='昵称 A',
             subject_id='a', value='A', datatype=string),
        dict(id='nickname-b', kind='attribute', type='nickname', text='昵称 B',
             subject_id='b', value='B', datatype=string),
    ])

    governance.merge(p, 'a', 'b', {'a': 1, 'b': 1})

    attributes = [row for row in service.scoped(p, {}) if row['kind'] == 'attribute']
    assert {(row['type'], row['value'], row['subject_id']) for row in attributes} == {
        ('age', 20, 'a'), ('nickname', 'A', 'a'), ('nickname', 'B', 'a')}
    age = next(row for row in attributes if row['type'] == 'age')
    assert len(service.repository.list_assertions(
        p, status='accepted', canonical_record_id=age['id'])) == 2
    rewritten = next(row for row in attributes if row['value'] == 'B')
    current_support = service.repository.list_record_version_assertions(
        p, record_version_id=rewritten['version_id'])
    assert {item['assertion_id'] for item in current_support} == {
        item['id'] for item in service.repository.list_assertions(
            p, status='accepted', canonical_record_id=rewritten['id'])}


def test_max_count_attribute_merge_requires_valid_winner_and_supersedes_loser_atomically(system):
    service, governance, p = system
    datatype = 'http://www.w3.org/2001/XMLSchema#integer'
    service.write(p, [
        dict(id='age-a', kind='attribute', type='age', text='年龄 20',
             subject_id='a', value=20, datatype=datatype),
        dict(id='age-b', kind='attribute', type='age', text='年龄 21',
             subject_id='b', value=21, datatype=datatype),
    ])
    before = service.repository.export_projection(p)

    with pytest.raises(ValueError, match='age-a.*age-b'):
        governance.merge(p, 'a', 'b', {'a': 1, 'b': 1})
    assert service.repository.export_projection(p) == before
    with pytest.raises(ValueError, match='unrelated'):
        governance.merge(p, 'a', 'b', {'a': 1, 'b': 1}, attribute_winners=['unrelated'])
    assert service.repository.export_projection(p) == before
    with pytest.raises(ValueError, match='恰好一个'):
        governance.merge(p, 'a', 'b', {'a': 1, 'b': 1},
                         attribute_winners=['age-a', 'age-b'])
    assert service.repository.export_projection(p) == before

    governance.merge(p, 'a', 'b', {'a': 1, 'b': 1}, attribute_winners=['age-a'])

    assert service.repository.get_record(p, 'age-a')['subject_id'] == 'a'
    loser = service.repository.get_record(p, 'age-b')
    assert loser['metadata']['_deleted'] is True
    assertion = service.repository.list_assertions(p, canonical_record_id='age-b')[0]
    assert assertion['status'] == 'superseded'
    transitions = [(event['from_status'], event['to_status']) for event in
                   service.repository.list_assertion_events(p, assertion['id'])]
    assert transitions[-2:] == [('accepted', 'contradicting'),
                                ('contradicting', 'superseded')]


def test_merge_request_defaults_attribute_winners_to_empty_list():
    request = Merge(keep_id='a', drop_id='b', expected_versions={'a': 1, 'b': 1})
    assert request.attribute_winners == []


def test_max_count_winner_from_dropped_entity_is_rewritten_to_kept_entity(system):
    service, governance, p = system
    datatype = 'http://www.w3.org/2001/XMLSchema#integer'
    service.write(p, [
        dict(id='age-a', kind='attribute', type='age', text='年龄 20',
             subject_id='a', value=20, datatype=datatype),
        dict(id='age-b', kind='attribute', type='age', text='年龄 21',
             subject_id='b', value=21, datatype=datatype),
    ])

    governance.merge(p, 'a', 'b', {'a': 1, 'b': 1}, attribute_winners=['age-b'])

    winner = service.repository.get_record(p, 'age-b')
    assert winner['subject_id'] == 'a'
    assert not winner.get('metadata', {}).get('_deleted')
    assert service.repository.get_record(p, 'age-a')['metadata']['_deleted'] is True
    support = service.repository.list_record_version_assertions(
        p, record_version_id=winner['version_id'])
    assert len(support) == 1


def test_max_count_merge_resolves_each_overlapping_validity_group(system):
    service, governance, p = system
    datatype = 'http://www.w3.org/2001/XMLSchema#integer'
    service.write(p, [
        dict(id='age-early', kind='attribute', type='age', text='早期年龄',
             subject_id='a', value=20, datatype=datatype,
             valid_from='2020-01-01', valid_until='2022-01-01'),
        dict(id='age-bridge', kind='attribute', type='age', text='冲突年龄',
             subject_id='b', value=21, datatype=datatype,
             valid_from='2021-01-01', valid_until='2023-01-01'),
        dict(id='age-late', kind='attribute', type='age', text='后期年龄',
             subject_id='a', value=22, datatype=datatype,
             valid_from='2022-01-01', valid_until='2024-01-01'),
    ])

    governance.merge(p, 'a', 'b', {'a': 1, 'b': 1},
                     attribute_winners=['age-early', 'age-late'])

    current = {row['id']: row for row in service.repository.current_records(p)}
    assert not current['age-early'].get('metadata', {}).get('_deleted')
    assert not current['age-late'].get('metadata', {}).get('_deleted')
    assert current['age-bridge']['metadata']['_deleted'] is True
