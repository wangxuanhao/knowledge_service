"""Identity support is a correlated, event-versioned authority projection."""
from copy import deepcopy

import pytest

from ontology_seed import seed_ontology
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import Repository
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services.entity_resolution import EntityResolver
from knowledge_service.services.formal_writes import FormalFactWriter
from knowledge_service.services.governance import Governance
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.service import KnowledgeService


TTL = '''@prefix ex: <http://ex/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix kg: <https://knowledge.local/schema/> .
ex:Platform a owl:Class; kg:identityPolicy "reference"; kg:identityKey "code" .
ex:Event a owl:Class; kg:identityPolicy "scoped" .
ex:Contract a owl:Class; kg:identityPolicy "scoped"; kg:identityKey "code" .
ex:Legacy a owl:Class .
ex:knows a owl:ObjectProperty .'''


def system(tmp_path):
    repo = Repository(tmp_path / 'identity.sqlite')
    project = repo.create_project('Identity')['id']
    seed_ontology(repo, project, TTL, Ontology(TTL).summary())
    return repo, KnowledgeService(repo, HashingEncoder()), project


def entity(id, *, start=None, end=None, platform=None, kind='Platform', code=None):
    metadata = {}
    if platform:
        metadata['applicability'] = {'platforms': {'mode': 'specific', 'values': [platform]}}
    if code:
        metadata['identity_keys'] = {'code': code}
    return {'id': id, 'kind': 'entity', 'type': kind, 'text': 'Same',
            'valid_from': start, 'valid_until': end, 'metadata': metadata}


class NoSemantic:
    def search(self, *args, **kwargs):
        raise AssertionError('ambiguous exact identity must never invoke similarity')


def test_published_class_policy_is_explicit_and_legacy_is_conservative():
    ontology = Ontology(TTL)
    summary = {row['id']: row for row in ontology.summary()['classes']}
    assert summary['http://ex/Platform']['identity_policy']['mode'] == 'reference'
    assert summary['http://ex/Event']['identity_policy']['mode'] == 'scoped'
    assert summary['http://ex/Legacy']['identity_policy']['mode'] == 'scoped'
    assert summary['http://ex/Legacy']['identity_policy']['declared'] is False


def test_ambiguous_exact_name_never_selects_first_or_semantic_best(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('a'), entity('b')])
    resolver = EntityResolver(repo, HashingEncoder(), retrieval=NoSemantic())
    result = resolver.resolve(project, entity('mention'), review_threshold=.7, merge_threshold=.9)
    assert result.status == 'review'
    assert result.canonical_id is None
    assert result.reason == 'ambiguous_exact_identity'


@pytest.mark.parametrize(('start','end'), [
    ('2022-01-01','2023-01-01'), ('2020-06-01','2021-06-01'), (None,None)])
def test_reference_cross_time_and_strong_key_conflict_are_hard_gated(tmp_path, monkeypatch, start, end):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('a', start='2020-01-01', end='2021-01-01', code='A')])
    resolver = EntityResolver(repo, HashingEncoder(), retrieval=NoSemantic())
    mention = entity('mention', start=start, end=end, code='A')
    blocked = resolver.resolve(project, mention, review_threshold=.7, merge_threshold=.9)
    assert blocked.status == 'review'
    assert blocked.reason == 'source_identity_projection_not_ready'
    monkeypatch.setattr(repo, 'source_identity_projection_readiness',
                        lambda p: {'ready': True, 'reason': ''}, raising=False)
    resolved = resolver.resolve(project, mention, review_threshold=.7, merge_threshold=.9)
    assert resolved.canonical_id == 'a'
    conflict = resolver.resolve(project, entity('other', code='B'), review_threshold=.7, merge_threshold=.9)
    assert conflict.status == 'review'
    assert conflict.reason == 'conflicting_identity_key'
    with pytest.raises(ValueError, match='业务时间|投影|索引'):
        # A legacy/scoped class retains conservative business-interval identity.
        service.write(project, [entity('e1', kind='Event', start='2020-01-01'),
                                entity('e2', kind='Event', start='2021-01-01')])
        Governance(service).merge(project, 'e1', 'e2', {'e1': 1, 'e2': 1})


def test_one_support_must_match_time_and_every_scope_dimension(tmp_path, monkeypatch):
    repo, service, project = system(tmp_path)
    monkeypatch.setattr(repo, 'source_identity_projection_readiness',
                        lambda p: {'ready': True, 'reason': ''}, raising=False)
    service.write(project, [entity('canonical', start='2020-01-01', end='2021-01-01', platform='美团')])
    payload = entity('mention', start='2022-01-01', end='2023-01-01', platform='淘宝')
    before = deepcopy(payload)
    FormalFactWriter(repo).apply('manual_write', project, [{
        'id': 'second-support', 'kind': 'entity', 'canonical_record_id': 'canonical',
        'document_id': 'doc-b', 'document_version_id': 'doc-b-v1', 'payload': payload,
        'quote': 'Same', 'actor': 'reviewer'}], {}, {'records': []})
    reads = ScopedReads(repo)
    def visible(year, platform):
        return reads.by_ids(project, {'valid_at': f'{year}-06-01', 'applicability': {
            'platforms': {'mode': 'specific', 'values': [platform]}}}, ['canonical'])
    assert visible('2022', '淘宝')
    assert not visible('2022', '美团')
    assert not visible('2021', '淘宝')
    assert visible('2020', '美团')
    assert repo.get_assertion(project, 'second-support')['payload'] == before
    contexts = repo.current_source_contexts(project, ['canonical'])['canonical']
    assert len(contexts) == 2
    assert {row['valid_from'][:4] for row in contexts} == {'2020', '2022'}


def test_support_withdrawal_known_at_and_backfill_are_event_aware(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('manual')])
    support = repo.list_assertions(project, canonical_record_id='manual')[0]
    accepted_at = repo.list_assertion_events(project, support['id'])[-1]['created_at']
    repo.transition_assertion(project, support['id'], support['decision_version'],
                              'superseded', 'withdraw declaration', 'reviewer')
    reads = ScopedReads(repo)
    assert not reads.by_ids(project, {}, ['manual'])
    assert reads.by_ids(project, {'known_at': accepted_at}, ['manual'])
    repo._db.execute('DELETE FROM source_context_support WHERE project_id=?', (project,))
    first = repo.backfill_source_contexts(project, batch_size=1)
    second = repo.backfill_source_contexts(project, batch_size=1)
    assert first['missing'] == second['missing'] == 0
    assert first['support_count'] == second['support_count'] == 1
    assert not reads.by_ids(project, {}, ['manual'])
    assert reads.by_ids(project, {'known_at': accepted_at}, ['manual'])


def test_reference_endpoint_is_not_hidden_by_first_document_interval():
    rows = [entity('a', start='2020-01-01', end='2021-01-01'),
            entity('b', start='2020-01-01', end='2021-01-01'),
            {'id': 'edge', 'kind': 'relation', 'type': 'knows', 'text': 'knows',
             'subject_id': 'a', 'object_id': 'b',
             'valid_from': '2022-01-01', 'valid_until': '2023-01-01'}]
    assert Ontology(TTL).validate_timeline(rows)['conforms'] is True


def test_gate_is_rechecked_inside_formal_transaction_and_rolls_back(tmp_path, monkeypatch):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('keep', start='2020-01-01'),
                            entity('drop', start='2022-01-01')])
    drop = repo.get_record(project, 'drop')
    raw = {key: value for key, value in drop.items()
           if key not in {'project_id', 'version', 'version_id', 'recorded_at', 'superseded_at'}}
    raw['metadata'] = {**raw['metadata'], '_deleted': True, 'merged_into': 'keep'}
    monkeypatch.setattr(repo, 'source_identity_projection_readiness',
                        lambda p: {'ready': True, 'reason': ''}, raising=False)
    plan = FormalFactWriter(repo).prepare('merge_rewrite', project, [], {'drop': 1}, {'records': [raw]})
    monkeypatch.setattr(repo, 'source_identity_projection_readiness',
                        lambda p: {'ready': False, 'reason': 'rollback_or_incomplete_build'})
    with pytest.raises(ValueError, match='rollback_or_incomplete_build'):
        repo.execute_formal_write(plan)
    assert repo.get_record(project, 'drop')['version'] == 1
    assert not repo.get_record(project, 'drop')['metadata'].get('_deleted')


def test_missing_exact_mapping_is_diagnostic_and_never_qualifies(tmp_path):
    repo, _, project = system(tmp_path)
    repo.put_record(project, entity('old', kind='Legacy'))
    assertion = repo.create_assertion(project, {'id': 'old-occurrence', 'kind': 'entity',
        'payload': entity('old', kind='Legacy'), 'actor': 'migration-test'})
    repo.transition_assertion(project, assertion['id'], 1, 'accepted', 'historical acceptance',
                              'migration-test', 'old')
    assert repo.source_context_diagnostics(project)['missing'] == 1
    assert ScopedReads(repo).by_ids(project, {}, ['old']) == []


def test_resumable_backfill_converges(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('one'), entity('two'), entity('three')])
    before = repo.source_context_diagnostics(project)
    repo._db.execute('DELETE FROM source_context_support WHERE project_id=?', (project,))
    first = repo.backfill_source_contexts(project, batch_size=1, max_batches=1)
    assert first['complete'] is False and first['after']
    second = repo.backfill_source_contexts(project, batch_size=1, after=first['after'])
    assert second['complete'] is True
    assert second['support_count'] == before['support_count'] == 3
    assert second['missing'] == 0


def test_null_scope_and_unnormalized_legacy_values_use_shared_scope_rules(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('legacy-ref')])
    canonical = repo.get_record(project, 'legacy-ref')
    # Old assertions may predate model normalization. Rebuild never edits them.
    raw = entity('raw', platform='\tmeituan\n')
    raw['metadata']['applicability']['businesses'] = None
    item = repo.create_assertion(project, {'id': 'legacy-space', 'kind': 'entity',
        'payload': raw, 'actor': 'backfill-test'})
    accepted = repo.transition_assertion(project, item['id'], 1, 'accepted', 'accepted original',
                                          'backfill-test', 'legacy-ref')
    event = repo.list_assertion_events(project, item['id'])[-1]
    repo.add_record_version_assertion(project, 'legacy-ref', canonical['version_id'], item['id'], event['id'])
    assert repo.source_context_diagnostics(project)['invalid'] == 0
    assert repo.get_assertion(project, item['id'])['payload'] == raw
    assert any(row['assertion_id'] == accepted['id'] for row in
               repo.current_source_contexts(project, ['legacy-ref'])['legacy-ref'])


def test_scoped_strong_key_merge_preserves_disjoint_support_visibility(tmp_path, monkeypatch):
    repo, service, project = system(tmp_path)
    monkeypatch.setattr(repo, 'source_identity_projection_readiness',
                        lambda p: {'ready': True, 'reason': ''}, raising=False)
    # Reproduce pre-feature duplicates without inventing a document source.
    for id, year in [('keep', '2020'), ('drop', '2022')]:
        raw = entity(id, kind='Contract', start=f'{year}-01-01', end=f'{int(year)+1}-01-01', code='same-number')
        record = repo.put_record(project, raw)
        assertion = repo.create_assertion(project, {'id': id+'-declaration', 'kind': 'entity',
            'payload': raw, 'actor': 'legacy-manual'})
        repo.transition_assertion(project, assertion['id'], 1, 'accepted', 'direct declaration', 'legacy-manual', id)
        event = repo.list_assertion_events(project, assertion['id'])[-1]
        repo.add_record_version_assertion(project, id, record['version_id'], assertion['id'], event['id'])
    Governance(service).merge(project, 'keep', 'drop', {'keep': 1, 'drop': 1})
    assert [row['id'] for row in service.scoped(project, {'valid_at': '2022-06-01'})] == ['keep']
    assert not service.scoped(project, {'valid_at': '2021-06-01'})


def test_unpublished_metadata_cannot_enable_cross_source_identity(tmp_path):
    repo, service, project = system(tmp_path)
    raw = entity('legacy', kind='Legacy')
    raw['metadata'].update(identity_policy='reference', identity_source_qualified=True,
                           identity_key_fields=['fake'], identity_namespace='forged')
    service.write(project, [raw])
    metadata = repo.get_record(project, 'legacy')['metadata']
    assert metadata.get('identity_policy') != 'reference'
    assert metadata.get('identity_source_qualified') is not True
    assert not metadata.get('identity_key_fields')


def test_explicit_assertion_cannot_attach_a_conflicting_strong_key(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('canonical', code='A')])
    with pytest.raises(ValueError, match='唯一键'):
        FormalFactWriter(repo).prepare('manual_write', project, [{
            'id': 'conflict', 'kind': 'entity', 'canonical_record_id': 'canonical',
            'payload': entity('source', code='B'), 'actor': 'reviewer'}], {}, {'records': []})


def test_equal_scoped_times_are_normalized_without_mutating_mention(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('event', kind='Event', start='2020-01-01', end='2021-01-01')])
    mention = entity('mention', kind='Event', start='2020-01-01', end='2021-01-01')
    before = deepcopy(mention)
    result = EntityResolver(repo, HashingEncoder(), retrieval=NoSemantic()).resolve(
        project, mention, review_threshold=.7, merge_threshold=.9)
    assert result.canonical_id == 'event'
    assert mention == before


def test_direct_reference_revision_cannot_bypass_projection_gate(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('canonical', start='2020-01-01')])
    with pytest.raises(ValueError, match='索引|投影'):
        service.write(project, [entity('canonical', start='2022-01-01')],
                      revision=True, expected_version=1)
    assert repo.get_record(project, 'canonical')['version'] == 1


def test_late_mapping_does_not_backdate_support_or_erase_legacy_history(tmp_path):
    repo, _, project = system(tmp_path)
    record = repo.put_record(project, entity('old', kind='Legacy'))
    item = repo.create_assertion(project, {'id': 'late-mapped', 'kind': 'entity',
        'payload': entity('old', kind='Legacy'), 'actor': 'reviewer'})
    repo.transition_assertion(project, item['id'], 1, 'accepted', 'new decision', 'reviewer', 'old')
    accepted_at = repo.list_assertion_events(project, item['id'])[-1]['created_at']
    repo.add_record_version_assertion(project, 'old', record['version_id'], item['id'],
        repo.list_assertion_events(project, item['id'])[-1]['id'])
    reads = ScopedReads(repo)
    assert reads.by_ids(project, {'known_at': record['recorded_at']}, ['old'])
    assert not reads.by_ids(project, {'known_at': accepted_at}, ['old'])
    assert reads.by_ids(project, {}, ['old'])


def test_old_operation_cannot_backdate_a_newer_assertion_decision(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('source'), entity('target')])
    operation = repo._reserve_record_operation(project)
    assertion = repo.list_assertions(project, canonical_record_id='source')[0]
    repo.transition_assertion(project, assertion['id'], 2, 'contradicting', 'review started', 'reviewer')
    repo.transition_assertion(project, assertion['id'], 3, 'accepted', 'review complete', 'reviewer', 'source')
    event = repo.list_assertion_events(project, assertion['id'])[-1]
    repo.add_record_version_assertion(project, 'source', repo.get_record(project, 'source')['version_id'],
        assertion['id'], event['id'])
    before = repo.list_assertion_events(project)
    with pytest.raises(ValueError) as error:
        FormalFactWriter(repo).apply('merge_rewrite', project, [], {}, {
            'records': [], 'ledger': {'id': 'backdated-merge', 'redirects': {'source': 'target'},
                'before_state': [], 'expected_versions': {}}}, operation_context=operation)
    assert error.value.code == 'operation_context_stale'
    assert error.value.retryable is False
    assert repo.list_assertion_events(project) == before
    assert repo.get_assertion(project, assertion['id'])['canonical_record_id'] == 'source'


def test_fact_supported_endpoint_uses_entity_output_filters_only_on_entity(tmp_path, monkeypatch):
    repo, service, project = system(tmp_path)
    monkeypatch.setattr(repo, 'source_identity_projection_readiness',
                        lambda p: {'ready': True, 'reason': ''}, raising=False)
    service.write(project, [entity('a', start='2020-01-01', end='2021-01-01'),
                            entity('b', start='2020-01-01', end='2021-01-01')])
    FormalFactWriter(repo).apply('manual_write', project, [], {}, {'records': [{
        'id': 'edge', 'kind': 'relation', 'type': 'knows', 'text': 'knows',
        'subject_id': 'a', 'object_id': 'b', 'valid_from': '2022-01-01', 'valid_until': '2023-01-01'}]})
    scope = {'valid_at': '2022-06-01', '_type_iris': ['http://ex/Platform'],
             'filters': {'field': 'id', 'op': 'eq', 'value': 'a'}}
    assert [row['id'] for row in ScopedReads(repo).by_ids(project, scope, ['a'], kinds=['entity'])] == ['a']


def test_current_support_diagnostics_do_not_require_closed_history_to_be_valid(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('canonical')])
    canonical = repo.get_record(project, 'canonical')
    invalid = entity('mention')
    invalid['valid_from'] = 'malformed-legacy-time'
    item = repo.create_assertion(project, {'id':'bad-history', 'kind':'entity',
        'payload':invalid, 'actor':'legacy'})
    repo.transition_assertion(project, item['id'], 1, 'accepted', 'legacy acceptance', 'legacy', 'canonical')
    event = repo.list_assertion_events(project, item['id'])[-1]
    repo.add_record_version_assertion(project, 'canonical', canonical['version_id'], item['id'], event['id'])
    before = repo.source_context_diagnostics(project)
    assert before.get('invalid_active') == 1
    repo.transition_assertion(project, item['id'], 2, 'superseded', 'legacy source withdrawn', 'reviewer')
    after = repo.source_context_diagnostics(project)
    assert after['invalid'] == 1 and after['invalid_active'] == 0
    assert after['missing_active'] == 0
    missing = repo.create_assertion(project, {'id':'unmapped-history', 'kind':'entity',
        'payload':entity('unmapped'), 'actor':'legacy'})
    repo.transition_assertion(project, missing['id'], 1, 'accepted', 'missing mapping', 'legacy', 'canonical')
    assert repo.source_context_diagnostics(project)['missing_active'] == 1
    repo.transition_assertion(project, missing['id'], 2, 'superseded', 'withdrawn', 'reviewer')
    final = repo.source_context_diagnostics(project)
    assert final['missing'] == 1 and final['missing_active'] == 0


@pytest.mark.parametrize(('start','end'), [
    ('1970-01-01T00:00:00Z','1969-12-31T00:00:00Z'),
    ('1970-01-01T00:00:00Z','1970-01-01T00:00:00Z'),
    ('1970-01-02T00:00:00Z','1970-01-01T00:00:00Z')])
def test_backfill_detects_invalid_intervals_at_unix_epoch(tmp_path, start, end):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('canonical')])
    target = repo.get_record(project, 'canonical')
    raw = entity('legacy-mention', start=start, end=end)
    item = repo.create_assertion(project, {'id':'epoch-invalid', 'kind':'entity',
        'payload':raw, 'actor':'legacy'})
    repo.transition_assertion(project, item['id'], 1, 'accepted', 'legacy acceptance', 'legacy', 'canonical')
    event = repo.list_assertion_events(project, item['id'])[-1]
    repo.add_record_version_assertion(project, 'canonical', target['version_id'], item['id'], event['id'])
    row = repo._db.execute('SELECT invalid_reason FROM source_context_support WHERE project_id=? AND assertion_id=?',
        (project,item['id'])).fetchone()
    assert row['invalid_reason'] == 'invalid_source_interval'
    assert repo.source_context_diagnostics(project)['invalid_active'] == 1


def test_valid_interval_starting_at_unix_epoch_is_not_unknown_or_invalid(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('epoch-valid', start='1970-01-01T00:00:00Z', end='1970-01-02T00:00:00Z')])
    assert repo.source_context_diagnostics(project)['invalid_active'] == 0
    assert [row['id'] for row in service.scoped(project, {'valid_at':'1970-01-01T12:00:00Z',
        'include_unknown':False})] == ['epoch-valid']


def test_legacy_date_only_support_uses_utc_even_in_non_utc_database_session(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('canonical')])
    target = repo.get_record(project, 'canonical')
    with repo._transaction():
        repo._db.execute("SET LOCAL TIME ZONE 'Asia/Shanghai'")
        item = repo.create_assertion(project, {'id':'legacy-date', 'kind':'entity',
            'payload':entity('mention', start='1970-01-01', end='1970-01-02'), 'actor':'legacy'})
        repo.transition_assertion(project, item['id'], 1, 'accepted', 'legacy acceptance', 'legacy', 'canonical')
        event = repo.list_assertion_events(project, item['id'])[-1]
        repo.add_record_version_assertion(project, 'canonical', target['version_id'], item['id'], event['id'])
    context = repo._db.execute('SELECT valid_from_epoch_ms,valid_until_epoch_ms FROM source_context_support WHERE project_id=? AND assertion_id=?',
        (project,item['id'])).fetchone()
    assert context['valid_from_epoch_ms'] == 0
    assert context['valid_until_epoch_ms'] == 86400000
    assert [row['id'] for row in service.scoped(project, {'valid_at':'1970-01-01T20:00:00Z',
        'include_unknown':False})] == ['canonical']


@pytest.mark.parametrize('invalid', ['1970-01-01T00:00:00','today'])
def test_source_epoch_helper_rejects_formats_rejected_by_public_time_contract(tmp_path, invalid):
    from knowledge_service.core.time import normalize_time
    repo, _, _ = system(tmp_path)
    with pytest.raises(ValueError):
        normalize_time(invalid)
    assert repo._db.execute('SELECT identity_support_epoch(?)', (invalid,)).fetchone()[0] is None
