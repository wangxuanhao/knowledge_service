"""Governed discovery acceptance uses the approved release without rewriting occurrences."""
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from knowledge_service.domain.formal_facts import PUBLICATION_RELEASE
from knowledge_service.services.formal_writes import FormalFactWriter
from knowledge_service.services.ontology import Ontology
from test_identity_binding_validation_pure import DetachedRepository, TTL, entity, snapshot, support
from test_ontology_intake import _add, _publish, _request, _scoped_source, _setup


def test_first_release_persists_exact_mapping_and_keeps_literal_evidence(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    cid = 'pending-occurrence'
    _scoped_source(repo, p, 'source', [{'id': cid, 'kind': 'entity', 'text': PUBLICATION_RELEASE,
                                        'proposed_type': '计算引擎', 'evidence': PUBLICATION_RELEASE}])
    before = repo.get_assertion(p, cid)
    with TestClient(app) as client:
        selection, _ = _add(client, path, _request(cid), 'first-release')
        release = _publish(app.state.ontology_drafts, repo, p, selection['draft'])
        after = repo.get_assertion(p, cid)
        assert after['payload'] == before['payload'] and after['quote'] == before['quote']
        record = repo.get_record(p, after['canonical_record_id'])
        assert record['text'] == PUBLICATION_RELEASE
        assert record['type'] == selection['new'][0]['target_iri']
        assert record['ontology_id'] == release['id']
        state = app.state.service.repository.formal_write_snapshot(p)
        event, = [e for e in state['assertion_events'] if e['assertion_id'] == cid and e['to_status'] == 'accepted']
        binding, = [m for m in state['record_version_assertions'] if m['assertion_event_id'] == event['id']]
        assert (binding['mapped_type'], binding['mapped_ontology_id'], binding['record_version_id']) == (
            record['type'], release['id'], record['version_id'])
        # A later ordinary write validates the stored first decision, not the proposal label.
        FormalFactWriter(app.state.service.repository).prepare('manual_write', p, [], {}, {'records': []})


def publication_plan(state, target_type, approved):
    assertion = state['assertions'][0]
    target = {**entity(type=target_type), 'ontology_id': PUBLICATION_RELEASE}
    writer = FormalFactWriter(DetachedRepository(state))
    return writer.prepare('adopt_discovery', 'project', [{**assertion, 'canonical_record_id': target['id']}],
                          {target['id']: 0}, {'records': [target], 'suppress_auto_assertions': True},
                          publication_ontology=Ontology(approved))


def test_publication_does_not_change_dependencies_or_reinterpret_explicit_types():
    pending = support(status='pending')
    pending['payload'].pop('type')
    pending['payload']['proposed_type'] = '未分类候选'
    state = snapshot(assertions=[pending])
    before = deepcopy(state)
    iri = 'http://ex/New'
    approved = TTL.replace('ex:Platform', 'ex:New')
    plan = publication_plan(state, iri, approved)
    assert plan['dependencies']['ontology_id'] == 'onto'
    assert plan['dependencies']['snapshot_fingerprint'] == state['fingerprint']
    put, = [a for a in plan['actions'] if a['kind'] == 'record_put']
    assert put['record']['metadata']['identity_policy'] == 'reference'
    assert state == before
    state['assertions'][0]['payload'].update(type='Platform', ontology_id='onto')
    with pytest.raises(ValueError, match='稳定类型 IRI 不一致'):
        publication_plan(state, iri, approved)


@pytest.mark.parametrize('approved', [TTL, TTL + '\nex:New a owl:ObjectProperty .',
                         TTL + '\nex:New a owl:Class; owl:deprecated true .'])
def test_unapproved_nonclass_and_retired_target_cannot_classify_a_proposal(approved):
    pending = support(status='pending')
    pending['payload'].pop('type')
    pending['payload']['proposed_type'] = 'http://ex/New'
    with pytest.raises(ValueError, match='活动类 IRI|已停用'):
        publication_plan(snapshot(assertions=[pending]), 'http://ex/New', approved)
