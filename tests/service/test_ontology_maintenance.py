from fastapi.testclient import TestClient
from ontology_seed import seed_ontology
from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.ontology import Ontology


TTL='''@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
:Thing a owl:Class ; rdfs:label "事物" .
:Person a owl:Class ; rdfs:label "人员" ; rdfs:subClassOf :Thing .
:Organization a owl:Class ; rdfs:label "组织" ; rdfs:subClassOf :Thing .
:knows a owl:ObjectProperty ; rdfs:label "认识" ; rdfs:domain :Person ; rdfs:range :Person .
:age a owl:DatatypeProperty ; rdfs:label "年龄" ; rdfs:domain :Person ; rdfs:range xsd:integer .'''



def setup(tmp_path):
    app = create_app(tmp_path / 'ontology-maintenance.sqlite', HashingEncoder())
    client = TestClient(app)
    client.__enter__()
    project = client.post('/api/projects', json={'name': 'ontology', 'use_default_ontology': False}).json()['id']
    ontology = seed_ontology(app.state.service.repository, project, TTL, Ontology(TTL).summary())
    return client, app, project, '/api/projects/' + project, ontology


def open_draft(client, base, ontology):
    result = client.post(base + '/ontology-drafts', json={
        'base_ontology_id': ontology['id'], 'source_kind': 'manual', 'title': '手工维护'})
    assert result.status_code == 201, result.text
    return result.json()


def edit(client, base, draft, commands):
    command = commands if isinstance(commands, dict) else {'action': 'batch', 'commands': commands}
    return client.post(base + '/ontology-drafts/' + draft['id'] + '/commands', json={
        'expected_revision': draft['revision'], 'command': command})


def test_modify_term_constraints_creates_governed_draft_and_keeps_history(tmp_path):
    c, app, p, base, old = setup(tmp_path)
    try:
        draft = open_draft(c, base, old)
        response = edit(c, base, draft, [
            {'action': 'remove_annotation', 'target_iri': 'https://test/knows',
             'predicate': 'http://www.w3.org/2000/01/rdf-schema#label', 'value': '认识'},
            {'action': 'add_annotation', 'target_iri': 'https://test/knows',
             'predicate': 'http://www.w3.org/2000/01/rdf-schema#label', 'value': '熟悉'},
            {'action': 'add_annotation', 'target_iri': 'https://test/knows',
             'predicate': 'http://www.w3.org/2000/01/rdf-schema#comment', 'value': '人工维护'},
            {'action': 'remove_domain', 'target_iri': 'https://test/knows', 'value': 'https://test/Person'},
            {'action': 'add_domain', 'target_iri': 'https://test/knows', 'value': 'https://test/Thing'},
        ])
        assert response.status_code == 200, response.text
        new = response.json()
        term = next(x for x in new['ontology']['relations'] if x['id'] == 'https://test/knows')
        assert term['label'] == '熟悉' and term['description'] == '人工维护'
        assert term['domain'] == ['https://test/Thing']
        assert new['id'] == draft['id'] and new['revision'] == draft['revision'] + 1
        assert 'deprecation' not in new and 'Deprecation' not in response.headers
        assert len(app.state.service.repository.list_ontologies(p)) == 1
        assert app.state.service.repository.get_ontology(p, old['id'])['turtle'] == old['turtle']
        assert edit(c, base, draft, {'action': 'add_parent', 'target_iri': 'https://test/Person',
                                  'value': 'https://test/Organization'}).status_code == 409
    finally:
        c.__exit__(None, None, None)


def test_relation_maintenance_supports_multiple_allowed_endpoint_types_as_union(tmp_path):
    c, app, p, base, old = setup(tmp_path)
    try:
        response = edit(c, base, open_draft(c, base, old), [
            {'action': 'add_domain', 'target_iri': 'https://test/knows', 'value': 'https://test/Organization'},
            {'action': 'add_range', 'target_iri': 'https://test/knows', 'value': 'https://test/Organization'},
        ])
        assert response.status_code == 200, response.text
        saved = response.json()
        term = next(x for x in saved['ontology']['relations'] if x['id'] == 'https://test/knows')
        assert set(term['domain']) == {'https://test/Person', 'https://test/Organization'}
        assert set(term['range']) == {'https://test/Person', 'https://test/Organization'}
        assert 'owl:unionOf' in saved['turtle']
        assert Ontology(saved['turtle']).validate_timeline([
            {'id': 'person', 'kind': 'entity', 'type': 'https://test/Person', 'text': '张三'},
            {'id': 'org', 'kind': 'entity', 'type': 'https://test/Organization', 'text': '机构'},
            {'id': 'edge', 'kind': 'relation', 'type': 'https://test/knows', 'text': '认识',
             'subject_id': 'person', 'object_id': 'org'},
        ])['conforms']
    finally:
        c.__exit__(None, None, None)


def test_impact_and_retirement_review_preserve_old_knowledge(tmp_path):
    c, app, p, base, old = setup(tmp_path)
    try:
        response = c.post(base + '/records', json={'records': [
            {'id': 'p', 'kind': 'entity', 'text': '张三', 'type': 'https://test/Person'}]})
        assert response.status_code == 201, response.text
        impact = c.get(base + '/ontology/term-impact', params={'uri': 'https://test/Person'}).json()
        assert impact['record_count'] == 1 and impact['constraint_count'] == 3
        retired = edit(c, base, open_draft(c, base, old), {
            'action': 'retire_term', 'target_iri': 'https://test/Person', 'reason': '审核停用影响'})
        assert retired.status_code == 200, retired.text
        assert retired.json()['operations'][0]['validation']['errors']
        assert next(x for x in retired.json()['ontology']['classes'] if x['id'] == 'https://test/Person')['active']
        assert app.state.service.repository.get_record(p, 'p')['ontology_id'] == old['id']
        assert len(app.state.service.repository.list_ontologies(p)) == 1
    finally:
        c.__exit__(None, None, None)


def test_term_creation_batch_is_atomic_and_retains_datatype(tmp_path):
    c, app, p, base, old = setup(tmp_path)
    try:
        draft = open_draft(c, base, old)
        invalid = edit(c, base, draft, [
            {'action': 'create_term', 'target_iri': 'https://test/bad', 'kind': 'relation'},
            {'action': 'add_domain', 'target_iri': 'https://test/bad', 'value': 'https://test/Missing'},
        ])
        assert invalid.status_code == 422, invalid.text
        unchanged = c.get(base + '/ontology-drafts/' + draft['id']).json()
        assert unchanged['revision'] == draft['revision'] and unchanged['operations'] == []
        added = edit(c, base, draft, [
            {'action': 'create_term', 'target_iri': 'https://test/score', 'kind': 'attribute'},
            {'action': 'add_domain', 'target_iri': 'https://test/score', 'value': 'https://test/Person'},
            {'action': 'set_datatype', 'target_iri': 'https://test/score', 'datatype': 'http://www.w3.org/2001/XMLSchema#decimal'},
        ])
        assert added.status_code == 200, added.text
        attr = next(x for x in added.json()['ontology']['attributes'] if x['id'] == 'https://test/score')
        assert attr['domain'] == ['https://test/Person'] and attr['range'] == ['http://www.w3.org/2001/XMLSchema#decimal']
        cycle = edit(c, base, added.json(), {'action': 'batch', 'commands': [
            {'action': 'add_parent', 'target_iri': 'https://test/Thing', 'value': 'https://test/Person'}]})
        assert cycle.status_code == 422 and 'cycle' in cycle.text
    finally:
        c.__exit__(None, None, None)


def test_labels_and_unicode_identity_use_explicit_atomic_commands(tmp_path):
    c, app, p, base, old = setup(tmp_path)
    try:
        iri = 'urn:knowledge:ontology:' + p + ':保护对象'
        added = edit(c, base, open_draft(c, base, old), [
            {'action': 'create_term', 'target_iri': iri, 'kind': 'class'},
            {'action': 'add_annotation', 'target_iri': iri, 'predicate': 'http://www.w3.org/2000/01/rdf-schema#label', 'value': 'Protected Object'},
            {'action': 'add_annotation', 'target_iri': iri, 'predicate': 'http://www.w3.org/2000/01/rdf-schema#label', 'value': '保护对象', 'language': 'zh'},
        ])
        assert added.status_code == 200, added.text
        term = next(x for x in added.json()['ontology']['classes'] if x['id'] == iri)
        assert term['label'] == 'Protected Object' and term['label_zh'] == '保护对象'
        assert iri in added.json()['turtle'] and any(str(item) == iri for item in Ontology(added.json()['turtle']).classes)
        changed = edit(c, base, added.json(), [
            {'action': 'remove_annotation', 'target_iri': iri, 'predicate': 'http://www.w3.org/2000/01/rdf-schema#label', 'value': '保护对象', 'language': 'zh'},
            {'action': 'add_annotation', 'target_iri': iri, 'predicate': 'http://www.w3.org/2000/01/rdf-schema#label', 'value': '商家', 'language': 'zh'},
        ])
        assert changed.status_code == 200, changed.text
        assert next(x for x in changed.json()['ontology']['classes'] if x['id'] == iri)['label_zh'] == '商家'
        cleared = edit(c, base, changed.json(), {'action': 'remove_annotation', 'target_iri': iri,
            'predicate': 'http://www.w3.org/2000/01/rdf-schema#label', 'value': '商家', 'language': 'zh'})
        assert cleared.status_code == 200, cleared.text
        assert next(x for x in cleared.json()['ontology']['classes'] if x['id'] == iri)['label_zh'] == ''
        assert len(app.state.service.repository.list_ontologies(p)) == 1
    finally:
        c.__exit__(None, None, None)


def test_impact_breaks_down_by_record_kind_and_lists_readable_names(tmp_path):
    """停用评估必须分类计数：实体类被停用时"关系"才是受影响大头，单一总数无法判断后果。"""
    c,app,p,base,old=setup(tmp_path)
    try:
        created=c.post(base+'/records',json={'records':[
            {'id':'p1','kind':'entity','text':'张三','type':'https://test/Person'},
            {'id':'p2','kind':'entity','text':'李四','type':'https://test/Person'},
            {'id':'o1','kind':'entity','text':'某机构','type':'https://test/Organization'},
            {'id':'edge','kind':'relation','text':'张三 认识 李四','type':'https://test/knows',
             'subject_id':'p1','object_id':'p2'},
          ]})
        assert created.status_code==201,created.text
        impact=c.get(base+'/ontology/term-impact?uri=https%3A%2F%2Ftest%2FPerson').json()
        # 分类计数：2 个 Person 实体 + 1 条以 Person 为端点的关系；Organization 实体不该被算进来
        assert impact['kind_counts']=={'entity':2,'relation':1,'attribute':0},impact['kind_counts']
        names={x['text'] for x in impact['record_preview']}
        assert {'张三','李四'} <= names
        assert '某机构' not in names
        # 明细带 kind，前端才能分组展示
        assert {x['kind'] for x in impact['record_preview']}=={'entity','relation'}
        # 向后兼容字段仍在
        assert impact['record_count']==3
        assert len(impact['record_ids'])==3 and 'constraint_count' in impact
    finally:c.__exit__(None,None,None)
