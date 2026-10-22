import hashlib
import json
import time

import pytest

from knowledge_service.models import Applicability, IngestSettings
from knowledge_service.domain.formal_facts import canonical_relation_key
from knowledge_service.services.entity_resolution import EntityResolver
from knowledge_service.integrations.semantica_adapter import _route_open_facts
from knowledge_service.integrations.milvus_store import MilvusStore
from knowledge_service.services.retrieval import RetrievalEngine


def test_ingest_scope_and_publication_fallback():
    settings = IngestSettings(applicability={'platforms': {'mode': 'specific', 'values': [' meituan ', '美团']}}, published_at='2026-10-14')
    assert settings.metadata['applicability']['platforms'] == {
        'mode': 'specific', 'values': ['美团']}
    assert settings.valid_from == '2026-10-14T00:00:00.000000Z'
    assert settings.valid_until is None
    assert settings.metadata['effective_time_source'] == 'published_at'


def test_ingest_explicit_effective_time_and_invalid_end():
    settings = IngestSettings(valid_from='2026-10-15', metadata={'published_at': '2026-10-14'})
    assert settings.valid_from == '2026-10-15T00:00:00.000000Z'
    assert settings.metadata['effective_time_source'] == 'explicit'
    with pytest.raises(ValueError):
        IngestSettings(published_at='2026-10-14', valid_until='2026-10-14')
    assert IngestSettings().metadata['effective_time_source'] == 'unknown'


def test_scope_normalization_matching_and_query_resolution():
    from knowledge_service.domain.applicability import (
        dimension_mode, dimension_values, matches_applicability,
        normalize_applicability, resolve_query_applicability, scope_identity,
    )
    scope = normalize_applicability({'platforms': {'mode': 'specific', 'values': ['JD', '京东', ' 美团 ', '']}, 'businesses': {'mode': 'specific', 'values': [' 直播 ', '直播']}})
    assert scope == {
        'platforms': {'mode': 'specific', 'values': ['京东', '美团']},
        'businesses': {'mode': 'specific', 'values': ['直播']},
        'audiences': {'mode': 'unknown', 'values': []},
    }
    assert dimension_values(scope, 'platforms') == ['京东', '美团']
    assert dimension_mode(scope, 'audiences') == 'unknown'
    assert scope_identity(None) == ''
    assert scope_identity(scope) == scope_identity({'businesses': {'mode': 'specific', 'values': ['直播']}, 'platforms': {'mode': 'specific', 'values': ['meituan', 'jd']}})
    assert matches_applicability({'metadata': {'applicability': scope}}, {'platforms': {'mode': 'specific', 'values': ['jd']}, 'businesses': {'mode': 'specific', 'values': ['直播']}})
    assert not matches_applicability({}, {'platforms': {'mode': 'specific', 'values': ['jd']}})
    assert not matches_applicability({'metadata': {'applicability': scope}}, {'businesses': {'mode': 'specific', 'values': ['外卖']}})
    assert matches_applicability({}, None)
    assert dimension_values(
        resolve_query_applicability('比较美团与Taobao规则', None),
        'platforms') == ['淘宝', '美团']
    with pytest.raises(ValueError):
        resolve_query_applicability('美团规则', {'platforms': {'mode': 'specific', 'values': ['京东']}})
    assert dimension_mode(
        resolve_query_applicability('未知商城规则', None), 'platforms') == 'unknown'


@pytest.mark.parametrize('raw', [
    {'platforms': {'mode': 'specific', 'values': []}},
    {'platforms': {'mode': 'all', 'values': ['美团']}},
    {'platforms': {'mode': 'unknown', 'values': ['美团']}},
    {'platforms': {'mode': 'invalid', 'values': []}},
    {'platforms': {'mode': 'specific', 'values': list(map(str, range(65)))}},
    {'platforms': {'mode': 'specific', 'values': ['x' * 129]}},
    {'platforms': {'mode': 'specific', 'values': ['界' * 86]}},
])
def test_scope_validation_rejects_invalid_modes_and_limits(raw):
    from knowledge_service.domain.applicability import normalize_applicability
    with pytest.raises(ValueError):
        normalize_applicability(raw)


def test_applicability_model_rejects_lists_and_accepts_current_dimensions():
    with pytest.raises(ValueError, match='mode/values'):
        Applicability(platforms=['meituan'])
    native = Applicability(
        platforms={'mode': 'specific', 'values': ['美团']},
        businesses={'mode': 'all', 'values': []},
    ).model_dump()
    assert native == {
        'platforms': {'mode': 'specific', 'values': ['美团']},
        'businesses': {'mode': 'all', 'values': []},
        'audiences': {'mode': 'unknown', 'values': []},
    }


def test_scope_predicate_uses_or_within_dimensions_and_and_across_them():
    from knowledge_service.domain.applicability import build_scope_predicate
    predicate = build_scope_predicate({
        'platforms': {'mode': 'specific', 'values': ['美团', '京东']}, 'businesses': {'mode': 'specific', 'values': ['直播']}})
    assert predicate.matches({'platforms': {'mode': 'specific', 'values': ['京东']}, 'businesses': {'mode': 'specific', 'values': ['直播']}})
    assert predicate.matches({
        'platforms': {'mode': 'all', 'values': []},
        'businesses': {'mode': 'specific', 'values': ['直播']},
    })
    assert predicate.matches({
        'platforms': {'mode': 'specific', 'values': ['美团']},
        'businesses': {'mode': 'all', 'values': []},
    })
    assert not predicate.matches({'platforms': {'mode': 'specific', 'values': ['淘宝']}, 'businesses': {'mode': 'specific', 'values': ['直播']}})
    assert not predicate.matches({'platforms': {'mode': 'specific', 'values': ['美团']}, 'businesses': {'mode': 'specific', 'values': ['外卖']}})
    assert not predicate.matches({'businesses': {'mode': 'specific', 'values': ['直播']}})
    assert build_scope_predicate({'businesses': {'mode': 'specific', 'values': ['直播']}}).matches({
        'platforms': {'mode': 'unknown', 'values': []},
        'businesses': {'mode': 'specific', 'values': ['直播']},
    })


def test_scope_predicate_uses_left_closed_right_open_effective_time():
    from knowledge_service.domain.applicability import build_scope_predicate
    at_start = build_scope_predicate(
        None, valid_at='2026-10-14', include_unknown=False)
    assert at_start.matches({
        'metadata': {}, 'valid_from': '2026-10-14', 'valid_until': '2026-10-15'})
    assert not at_start.matches({
        'metadata': {}, 'valid_from': '2026-10-13', 'valid_until': '2026-10-14'})
    assert not at_start.matches({'metadata': {}, 'valid_from': None})
    assert build_scope_predicate(
        None, valid_at='2026-10-14', include_unknown=True
    ).matches({'metadata': {}, 'valid_from': None, 'valid_until': None})


def test_scope_identity_uses_one_schema_for_every_nonempty_scope():
    from knowledge_service.domain.applicability import scope_identity
    current = {'platforms': {'mode': 'specific', 'values': ['美团']}, 'businesses': {'mode': 'unknown', 'values': []}, 'audiences': {'mode': 'unknown', 'values': []}}
    payload = json.dumps({'schema_version': 2, **current}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    assert scope_identity(current) == hashlib.sha256(payload.encode()).hexdigest()
    assert scope_identity({
        'platforms': {'mode': 'all', 'values': []}}) not in ('', scope_identity(None))
    assert scope_identity({
        'platforms': {'mode': 'all', 'values': []}}) != scope_identity(current)


def test_vocabulary_finds_longest_exact_terms_in_each_dimension():
    from knowledge_service.domain.applicability import (
        ApplicabilityVocabulary, dimension_mode, dimension_values,
        resolve_query_applicability,
    )
    vocabulary = ApplicabilityVocabulary(
        platforms=['美团', '京东'],
        businesses=['直播', '电商直播'],
        audiences=['商家'],
    )
    live = resolve_query_applicability('直播规则', None, vocabulary)
    assert dimension_mode(live, 'platforms') == 'unknown'
    assert dimension_values(live, 'businesses') == ['直播']
    all_dimensions = resolve_query_applicability('美团直播商家', None, vocabulary)
    assert dimension_values(all_dimensions, 'platforms') == ['美团']
    assert dimension_values(all_dimensions, 'businesses') == ['直播']
    assert dimension_values(all_dimensions, 'audiences') == ['商家']
    longest = resolve_query_applicability('电商直播规则', None, vocabulary)
    assert dimension_values(longest, 'businesses') == ['电商直播']
    assert dimension_values(
        resolve_query_applicability('MEITUAN rules', None, vocabulary),
        'platforms') == ['美团']
    assert dimension_mode(
        resolve_query_applicability('premeituan rules', None, vocabulary),
        'platforms') == 'unknown'
    with pytest.raises(ValueError, match='冲突'):
        resolve_query_applicability(
            '美团直播规则', {'businesses': {'mode': 'specific', 'values': ['外卖']}}, vocabulary)


def test_project_vocabulary_is_not_limited_to_one_records_value_count():
    from knowledge_service.domain.applicability import (
        ApplicabilityVocabulary, dimension_values, resolve_query_applicability,
    )
    businesses = [f'业务{index}' for index in range(65)]
    vocabulary = ApplicabilityVocabulary(businesses=businesses)
    resolved = resolve_query_applicability('业务64规则', None, vocabulary)
    assert dimension_values(resolved, 'businesses') == ['业务64']


def test_vocabulary_uses_unicode_aware_boundaries_for_english_terms():
    from knowledge_service.domain.applicability import (
        ApplicabilityVocabulary, dimension_mode, dimension_values,
        resolve_query_applicability,
    )
    vocabulary = ApplicabilityVocabulary(businesses=['live-stream'])
    exact = resolve_query_applicability(
        'MEITUAN live-stream rules', None, vocabulary)
    assert dimension_values(exact, 'platforms') == ['美团']
    assert dimension_values(exact, 'businesses') == ['live-stream']

    embedded = resolve_query_applicability(
        'prelive-streaming émeituan', None, vocabulary)
    assert dimension_mode(embedded, 'platforms') == 'unknown'
    assert dimension_mode(embedded, 'businesses') == 'unknown'


def test_vocabulary_overlap_resolution_scales_for_long_queries():
    from knowledge_service.domain.applicability import (
        ApplicabilityVocabulary, dimension_values, resolve_query_applicability,
    )
    vocabulary = ApplicabilityVocabulary(businesses=['甲'])
    started = time.perf_counter()
    resolved = resolve_query_applicability('甲' * 10_000, None, vocabulary)
    elapsed = time.perf_counter() - started
    assert dimension_values(resolved, 'businesses') == ['甲']
    assert elapsed < 1.5


def test_v2_scope_identity_has_stable_golden_hash():
    from knowledge_service.domain.applicability import scope_identity
    assert scope_identity({
        'platforms': {'mode': 'all', 'values': []},
        'businesses': {'mode': 'specific', 'values': ['直播']},
    }) == '89ce50984c66b2e3a5c5a13939e6001ac134549faabf6d9c142113862dfbefbe'


def test_scoped_fact_keys_preserve_legacy_identity():
    record = {'type': 'p', 'subject_id': 's', 'object_id': 'o'}
    legacy = 'fact_' + hashlib.sha256(json.dumps(['p', 's', 'o', None, None], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    assert canonical_relation_key(record) == legacy
    scoped = {**record, 'metadata': {'applicability': {'platforms': {'mode': 'specific', 'values': ['美团']}}}}
    assert canonical_relation_key(scoped) != legacy
    assert canonical_relation_key(scoped) != canonical_relation_key({**record, 'metadata': {'applicability': {'platforms': {'mode': 'specific', 'values': ['京东']}}}})


def test_query_scope_uses_overlap():
    from knowledge_service.domain.applicability import dimension_values, resolve_query_applicability
    assert dimension_values(resolve_query_applicability(
        '美团规则', {'platforms': {'mode': 'specific', 'values': ['美团', '京东']}}), 'platforms') == ['美团']
    assert dimension_values(resolve_query_applicability(
        '比较美团和京东', {'platforms': {'mode': 'specific', 'values': ['美团']}}), 'platforms') == ['美团']


def test_valid_until_without_known_start_is_allowed():
    assert IngestSettings(valid_until='2026-10-14').valid_until == '2026-10-14T00:00:00.000000Z'


def test_open_entity_literal_conflict_is_quarantined():
    entities = [{'id': 's', 'text': '规则', 'proposed_type': 'Rule'}, {'id': 'o', 'text': '生效', 'proposed_type': 'Status'}]
    fact = {'subject': '规则', 'predicate': '状态', 'object': '生效', 'fact_kind': 'attribute', 'literal_type': None, 'evidence': '规则状态生效'}
    accepted, exceptions = _route_open_facts('规则状态生效', entities, [fact], True)
    assert accepted == []
    assert exceptions[0]['reason_code'] == 'relation_attribute_ambiguous'


def test_entity_resolution_requires_identical_scope():
    row = {'id': 'jd', 'kind': 'entity', 'text': '直播', 'type': 'Business', 'metadata': {'applicability': {'platforms': {'mode': 'specific', 'values': ['京东']}}}}
    class Repository:
        def current_records(self, *args, **kwargs):
            return [row]
    mention = {**row, 'id': 'mt', 'metadata': {'applicability': {'platforms': {'mode': 'specific', 'values': ['美团']}}}}
    assert EntityResolver(Repository(), None).resolve('p', mention, review_threshold=.7, merge_threshold=.9).status == 'separate'


@pytest.mark.parametrize('object_type', ['DataEntity', 'Status'])
def test_open_explicit_scalar_not_overridden_by_named_entity(object_type):
    entities = [{'id': 's', 'text': '规则', 'proposed_type': 'Rule'}, {'id': 'o', 'text': '生效', 'proposed_type': object_type}]
    fact = {'subject': '规则', 'predicate': '状态', 'object': '生效', 'fact_kind': 'attribute', 'literal_type': 'text', 'evidence': '规则状态生效'}
    accepted, exceptions = _route_open_facts('规则状态生效', entities, [fact], True)
    assert not exceptions
    assert [item['kind'] for item in accepted] == ['attribute']


def test_milvus_v2_rejects_legacy_candidate_id_filters():
    store = MilvusStore()
    with pytest.raises(ValueError, match='does not accept candidate_ids'):
        store._filter_expr('p', candidate_ids=[])
    with pytest.raises(ValueError, match='does not accept candidate_ids'):
        store._filter_expr('p', candidate_ids=['v-1'])

    class Encoder:
        def encode(self, texts):
            raise AssertionError('empty scope must return before encoding')

    result = RetrievalEngine(None, Encoder(), store)._search_via_milvus(
        'p', '直播', 'keyword', [], ['chunk'], 10, {'chunk': 10}, {})
    assert result['hits'] == []
    assert result['candidate_count'] == 0


def test_adjacent_context_supports_fixed_chunks_and_keeps_scope_boundary(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.repository.scoped_reads import ScopedReads
    repo = Repository(tmp_path / 'adjacent-scope.sqlite')
    project = repo.create_project('adjacent context scope')['id']
    document = repo.put_record(project, {'id': 'doc', 'kind': 'document', 'text': 'source',
        'metadata': _scope_for_test('美团')})
    rows = [
        {'id': 'a0', 'kind': 'chunk', 'source_id': 'doc', 'text': '前文',
         'metadata': {'start_char': 0, 'source_version_id': 'doc-v1', **_scope_for_test('美团')}},
        {'id': 'a1', 'kind': 'chunk', 'source_id': 'doc', 'text': '命中',
         'metadata': {'start_char': 10, 'source_version_id': 'doc-v1', **_scope_for_test('美团')}},
        {'id': 'a2', 'kind': 'chunk', 'source_id': 'doc', 'text': '后文',
         'metadata': {'start_char': 20, 'source_version_id': 'doc-v1', **_scope_for_test('美团')}},
        {'id': 'jd', 'kind': 'chunk', 'source_id': 'doc', 'text': '京东后文',
         'metadata': {'start_char': 30, 'source_version_id': 'doc-v1', **_scope_for_test('京东')}},
    ]
    for row in rows:
        row['metadata']['source_version_id'] = document['version_id']
    rows = repo.put_batch(project, rows)
    context = ScopedReads(repo).source_context(project, {
        'applicability': _scope_for_test('美团')['applicability']}, [rows[1]], radius=2)['context_chunks']
    assert [row['id'] for row in context] == ['a0', 'a2']
    assert all(row['context_reason'] == 'adjacent_chunk' for row in context)


def _scope_for_test(platform):
    return {'applicability': {'platforms': {'mode': 'specific', 'values': [platform]}}}
