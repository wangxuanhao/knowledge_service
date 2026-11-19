from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ontology_seed import seed_project_ontology

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services.explorer import Explorer


def evaluate(types, gold, vocabulary=(), *, scope=None, other_vocabulary=()):
    versions = {'v1': list(vocabulary), 'v2': list(other_vocabulary)}
    repo = SimpleNamespace(
        ontology_relation_terms=lambda p, ontology_ids, known_at: [
            term for release in (ontology_ids or versions) for term in versions[release]],
    )
    repo.independent_read_view = lambda: nullcontext(repo)
    nodes = [dict(id='s', kind='entity', text='甲'), dict(id='o', kind='entity', text='乙')]
    edges = [dict(kind='relation', type=t, subject_id='s', object_id='o', text='展示标题') for t in types]
    explorer = Explorer(SimpleNamespace(repository=repo))
    with patch.object(ScopedReads, 'page', return_value={'records': [*nodes, *edges], 'next_cursor': None}):
        return explorer.evaluate('project', scope or {}, ['甲', '乙'], [('甲', p, '乙') for p in gold])['relations']


@pytest.mark.parametrize('iri', ['urn:a:退款', 'https://a/t#退款', 'https://a/t/退款', '退款'])
def test_unique_local_name_and_exact_identity(iri):
    score = evaluate([iri], ['退款', iri])
    assert score['f1'] == 1 and score['gold'] == score['predicted'] == 1


@pytest.mark.parametrize('wrong', ['urn:b:refund', 'https://x/refund', '退款已拒绝', '展示标题'])
def test_wrong_predicate_same_endpoints(wrong):
    score = evaluate(['urn:a:refund'], [wrong])
    assert score['true_positive'] == 0 and score['extra'] == [('甲', 'urn:a:refund', '乙')]
    assert score['missing'] == [('甲', wrong, '乙')]


def test_label_is_not_identity_and_collisions_do_not_guess():
    a, b = 'urn:a:approve', 'urn:b:approve'
    terms = [{'id': a, 'label_zh': '通过'}, {'id': b, 'label_zh': '同意'}]
    assert evaluate([a], ['通过'], terms)['f1'] == 1
    # Unused vocabulary still makes the local name ambiguous.
    assert evaluate([a], ['approve'], terms)['true_positive'] == 0
    terms[1]['label_zh'] = '通过'
    mixed = evaluate([a, b], [a, '通过'], terms)
    assert (mixed['true_positive'], mixed['predicted'], mixed['gold']) == (1, 2, 2)
    assert mixed['extra'] == [('甲', b, '乙')] and mixed['missing'] == [('甲', '通过', '乙')]
    assert evaluate([a, b], [a, b], terms)['f1'] == 1


def test_label_local_name_collision_and_iri_looking_label():
    terms = [{'id': 'urn:a:x', 'label_zh': 'other'}, {'id': 'urn:b:other', 'label': 'urn:c:x'}]
    assert evaluate(['urn:a:x'], ['other'], terms)['true_positive'] == 0
    assert evaluate(['urn:b:other'], ['urn:c:x'], terms)['true_positive'] == 0
    assert evaluate([], ['other'], terms)['predicted'] == 0


def test_excluded_release_cannot_make_an_in_scope_predicate_ambiguous():
    a, b = 'urn:a:refund', 'urn:b:refund'
    terms = [{'id': a, 'label_zh': '退还'}]
    excluded = [{'id': b, 'label_zh': '退还'}]
    scope = {'ontology_scope': 'ids', 'ontology_ids': ['v1']}
    for gold in ('refund', '退还', a):
        assert evaluate([a], [gold], terms, scope=scope, other_vocabulary=excluded)['f1'] == 1
    assert evaluate([a], ['refund'], terms, other_vocabulary=excluded)['f1'] == 0


def test_evaluation_api_uses_selected_platform_release_and_known_time(tmp_path):
    def ontology(namespace):
        return f'''
            @prefix ex: <urn:{namespace}:> .
            @prefix owl: <http://www.w3.org/2002/07/owl#> .
            @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
            ex:Actor a owl:Class .
            ex:refund a owl:ObjectProperty ; rdfs:domain ex:Actor ; rdfs:range ex:Actor ;
              rdfs:label "退还"@zh .
        '''

    with TestClient(create_app(tmp_path / 'evaluation.sqlite', HashingEncoder())) as client:
        project = client.post('/api/projects', json={
            'name': 'quality evaluation', 'use_default_ontology': False,
        }).json()['id']
        base = f'/api/projects/{project}'
        first = seed_project_ontology(client, base, ontology('a'))
        service = client.app.state.service
        platform = {'platforms': {'mode': 'specific', 'values': ['美团']}}
        scope_metadata = {'applicability': platform, 'effective_time_source': 'explicit'}
        merchant = '商户' + '甲' * 170
        facts = service.write(project, [{
            **row, 'ontology_id': first['id'], 'valid_from': '2025-01-01', 'metadata': scope_metadata,
        } for row in [
            {'id': 's', 'kind': 'entity', 'type': 'urn:a:Actor', 'text': merchant},
            {'id': 'o', 'kind': 'entity', 'type': 'urn:a:Actor', 'text': '平台'},
            {'id': 'r', 'kind': 'relation', 'type': 'urn:a:refund', 'text': '退款关系',
             'subject_id': 's', 'object_id': 'o'},
        ]])
        seed_project_ontology(client, base, ontology('b'))
        gold = {'entities': [merchant, '平台'], 'relations': [[merchant, '退还', '平台']]}

        def run(**scope):
            response = client.post(base + '/evaluate', json={**gold, **scope})
            assert response.status_code == 200, response.text
            return response.json()

        selected = {'ontology_scope': 'ids', 'ontology_ids': [first['id']]}
        result = run(**selected, applicability=platform, valid_at='2025-02-01', include_unknown=False)
        assert result['entities']['f1'] == result['relations']['f1'] == 1
        assert run(known_at=facts[-1]['recorded_at'])['relations']['f1'] == 1
        assert run()['relations']['f1'] == 0
        assert run(**selected, applicability={'platforms': {'mode': 'specific', 'values': ['京东']}})[
            'relations']['predicted'] == 0
