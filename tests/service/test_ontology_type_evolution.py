"""Exercise the complete user workflow without migrating data on draft/publication."""
from fastapi.testclient import TestClient
from rdflib import Literal, URIRef
from rdflib.namespace import OWL, DCTERMS

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.governance import Governance
from knowledge_service.services.ontology import Ontology
from ontology_seed import seed_ontology


BASE = '''
@prefix ex: <https://evolution.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Source a owl:Class ; rdfs:label "旧分类" .
ex:Target a owl:Class ; rdfs:label "保留分类" .
ex:rel a owl:ObjectProperty ; rdfs:domain ex:Source ; rdfs:range ex:Target .
'''


def test_merge_review_publish_then_explicit_instance_migration_and_undo(tmp_path):
    app = create_app(tmp_path / 'type-evolution', encoder=HashingEncoder())
    service = app.state.service
    repo = service.repository
    p = repo.create_project('类型纠正')['id']
    first = seed_ontology(repo, p, BASE, Ontology(BASE).summary())
    service.write(p, [
        {'id': 'one', 'kind': 'entity', 'type': 'https://evolution.test/Source',
         'text': '张三', 'ontology_id': first['id']},
        {'id': 'two', 'kind': 'entity', 'type': 'https://evolution.test/Target',
         'text': '李四', 'ontology_id': first['id']},
        {'id': 'link', 'kind': 'relation', 'type': 'https://evolution.test/rel',
         'subject_id': 'one', 'object_id': 'two', 'text': '原文关系',
         'ontology_id': first['id']},
    ])
    before = {r['id']: r for r in service.scoped(p, {})}
    base = f'/api/projects/{p}'
    with TestClient(app) as client:
        def post(path, body, expected=200):
            response = client.post(base + path, json=body)
            assert response.status_code == expected, response.text
            return response.json()

        draft = post('/ontology-drafts', {'base_ontology_id': first['id'],
            'title': '纠正重复分类', 'source_kind': 'manual'}, 201)
        path = f"/ontology-drafts/{draft['id']}"
        plan = post(path + '/type-merge-preview', {
            'expected_revision': draft['revision'],
            'source_iri': 'https://evolution.test/Source',
            'target_iri': 'https://evolution.test/Target'})
        assert plan['can_merge'] and plan['impact']['linked_relation_count'] == 1
        draft = post(path + '/commands', {'expected_revision': draft['revision'],
            'command': {'action': 'merge_class', 'target_iri': plan['source_iri'],
                'replacement_iri': plan['target_iri'],
                'preview_fingerprint': plan['preview_fingerprint'], 'reason': '两个分类含义重复'}})
        assert {r['id']: r for r in service.scoped(p, {})} == before
        draft = post(path + '/submit', {'expected_revision': draft['revision']})
        codes = [w['code'] for w in draft['validation_report'].get('warnings', [])]
        # Match the workbench's explicit review of each definition. High-risk
        # retirement must not be approved through the unattended batch route.
        for definition in dict.fromkeys(op['target_iri'] for op in draft['operations']):
            reviewed = post(path + '/decisions/by-definition', {
                'expected_revision': draft['revision'], 'expected_ontology_id': first['id'],
                'validation_fingerprint': draft['validation_fingerprint'],
                'acknowledged_warning_codes': codes,
                'target_iri': definition, 'action': 'approve',
                'reason': '已核对结构与历史知识影响'})
            draft = reviewed['draft']
        post(path + '/publish', {'expected_revision': draft['revision'],
            'expected_ontology_id': first['id'],
            'validation_fingerprint': draft['validation_fingerprint'],
            'acknowledged_warning_codes': codes, 'idempotency_key': 'type-evolution',
            'note': '将旧分类合并到保留分类，实例另行受控迁移'})
        latest = repo.get_ontology(p)
        graph = Ontology(latest['turtle']).graph
        source = URIRef('https://evolution.test/Source')
        target = URIRef('https://evolution.test/Target')
        assert (source, OWL.deprecated, Literal(True)) in graph
        assert (source, DCTERMS.isReplacedBy, target) in graph
        assert {r['id']: r for r in service.scoped(p, {})} == before
        groups = client.get(base + '/reclassify').json()['groups']
        renamed = next(g for g in groups if g['from_type'] == str(source))
        assert renamed['to_type'] == str(target)
        keys = [g['key'] for g in groups if g['migratable']]
        preview = post('/reclassify/preview', {'groups': keys})
        assert not preview['blocked']
        assert {r['id']: r for r in service.scoped(p, {})} == before
        result = post('/reclassify', {'groups': keys})
        after = {r['id']: r for r in service.scoped(p, {})}
        assert result['migrated'] == 3 and set(after) == set(before)
        assert after['one']['type'] == str(target)
        assert after['two']['id'] != after['one']['id'], '类型归并不等于实例合并'
        assert after['link']['subject_id'] == 'one' and after['link']['object_id'] == 'two'
        assert after['one']['text'] == before['one']['text']
        assert after['one']['version'] > before['one']['version']
        assert after['one']['ontology_id'] == latest['id']
        assert repo.history(p, 'one')[0]['type'] == str(source)
        Governance(service).undo_merge(p, result['operation_id'])
        restored = {r['id']: r for r in service.scoped(p, {})}
        assert restored['one']['type'] == str(source)
        assert restored['one']['ontology_id'] == first['id']
        assert restored['link']['subject_id'] == 'one'
