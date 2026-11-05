import pytest

from knowledge_service.core.time import normalize_time
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import Repository
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services.answers import question_context
from knowledge_service.services.service import KnowledgeService
from ontology_seed import seed_ontology
from test_retrieval import activate_index


class ReadyMilvus:
    available = True

    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def search(self, project, query, *, kinds, k, scope, **options):
        self.calls.append((project, query, kinds, k, scope, options))
        assert options['retrieval_mode'] == 'keyword'
        assert kinds == ['chunk'] and 1 <= k <= 512
        # Deliberately include other releases: PG must recheck the exact IDs.
        return [{'record_id': row['id'], 'version_id': row['version_id'], 'score': 1.}
                for row in self.rows if query in row['text']][:k]


@pytest.mark.parametrize('connected', [False, True])
@pytest.mark.parametrize('scope,allowed', [
    ({'ontology_scope': 'ids', 'ontology_ids': ['release-a']}, {'a', 'a-neighbor'}),
    ({'ontology_scope': 'unknown'}, {'orphan', 'unbound'}),
])
def test_search_and_qa_keep_hits_and_adjacent_context_inside_release_scope(
        tmp_path, monkeypatch, connected, scope, allowed):
    """Primary ownership is scoped; adjacent originals follow exact source identity."""
    repo = Repository(tmp_path / 'ontology-scope.sqlite')
    project = repo.create_project('ontology-scope')['id']
    releases = {}
    for name in ('release-a', 'release-b'):
        releases[name] = seed_ontology(repo, project,
            '@prefix owl: <http://www.w3.org/2002/07/owl#> . '
            f'<https://test/{name}> a owl:Class .', {})['id']
    scope = {**scope}
    if scope.get('ontology_ids'):
        scope['ontology_ids'] = [releases[name] for name in scope['ontology_ids']]
    metadata = {'effective_time_source': 'explicit',
                'applicability': {'platforms': {'mode': 'all'}}}
    documents = {}
    for name, release in (('document', releases['release-a']), ('unknown-document', None),
                          ('other-document', releases['release-a'])):
        documents[name] = repo.put_record(project, {
            'id': name, 'kind': 'document', 'text': 'refund source',
            'ontology_id': release, 'valid_from': '2020-01-01', 'metadata': metadata})
    old_document = documents['document']
    documents['document'] = repo.put_record(project, {
        'id': 'document', 'kind': 'document', 'text': 'refund source revised',
        'ontology_id': releases['release-a'], 'valid_from': '2020-01-01',
        'metadata': metadata}, expected_version=old_document['version'])
    rows = []
    for index, (key, release, text, source) in enumerate((
        ('a', releases['release-a'], 'refund', 'document'),
        ('b-neighbor', releases['release-b'], 'secret', 'document'),
        ('a-neighbor', releases['release-a'], 'neighbor', 'document'),
        ('b', releases['release-b'], 'refund', 'document'),
        ('orphan', 'foreign-release', 'refund', 'unknown-document'),
        ('unbound', None, 'refund', 'unknown-document'),
    )):
        rows.append(repo.put_record(project, {
            'id': key, 'kind': 'chunk', 'text': text, 'ontology_id': release,
            'source_id': source, 'valid_from': '2020-01-01',
            'metadata': {**metadata, 'start_char': index,
                         'source_version_id': documents[source]['version_id']}}))
    # These closer neighbors must never cross source, revision, business, or time boundaries.
    decoys = [
        {'id': 'other-source', 'source_id': 'other-document',
         'metadata': {'source_version_id': documents['other-document']['version_id']}},
        {'id': 'other-revision',
         'metadata': {'source_version_id': old_document['version_id']}},
        {'id': 'other-applicability',
         'metadata': {'applicability': {'platforms': {'mode': 'specific', 'values': ['other']}}}},
        {'id': 'future-neighbor', 'valid_from': '2027-01-01'},
    ]
    for decoy in decoys:
        rows.append(repo.put_record(project, {
            'kind': 'chunk', 'text': 'irrelevant neighbor', 'ontology_id': releases['release-a'],
            'source_id': 'document', 'valid_from': '2020-01-01', **decoy,
            'metadata': {**metadata, 'start_char': 1,
                         'source_version_id': documents['document']['version_id'],
                         **decoy.get('metadata', {})}}))
    encoder = HashingEncoder()
    index = activate_index(repo, project, encoder)
    store = ReadyMilvus(rows)
    service = KnowledgeService(repo, encoder, store if connected else None)
    service.ontology_labels = lambda project: {}
    service.annotate_structure_pending = lambda project, rows: 0
    reference_reads, source_reads, hydrations = [], [], []
    original_query, original_sources = repo.query, repo.records_by_version_ids
    original_hydrate = ScopedReads.by_ids

    def query(project, **kwargs):
        assert kwargs.get('_reference_mode'), 'current search must not preload authority rows'
        reference_reads.append(kwargs)
        return original_query(project, **kwargs)

    def sources(project, versions):
        source_reads.append((project, list(versions)))
        return original_sources(project, versions)

    def hydrate(reader, project, selected_scope, ids, *, kinds=None):
        assert len(ids) <= 10
        hydrations.append((dict(selected_scope), list(ids), kinds))
        return original_hydrate(reader, project, selected_scope, ids, kinds=kinds)

    def forbidden_encode(*args):
        raise AssertionError('keyword retrieval must not encode')

    monkeypatch.setattr(repo, 'query', query)
    monkeypatch.setattr(repo, 'records_by_version_ids', sources)
    monkeypatch.setattr(ScopedReads, 'by_ids', hydrate)
    monkeypatch.setattr(encoder, 'encode', forbidden_encode)
    request = {'query': 'refund', 'retrieval_mode': 'keyword',
               'k_entities': 0, 'k_relations': 0, 'k_chunks': 10,
               'valid_at': '2025-01-01T00:00:00Z', 'include_unknown': False, **scope}
    if not connected:
        for retrieve in (service.search, lambda project, body: question_context(service, project, body)):
            with pytest.raises(RuntimeError, match='Milvus'):
                retrieve(project, request)
        assert store.calls == reference_reads == source_reads == hydrations == []
        return

    result = service.search(project, request)
    primary = {'a'} if scope['ontology_scope'] == 'ids' else {'orphan', 'unbound'}
    supplementary = {'b-neighbor'} if scope['ontology_scope'] == 'ids' else set()
    assert {row['id'] for row in result['hits']} == primary
    assert {row['id'] for row in result['hits']} <= allowed
    # The same immutable source can support facts from another publication.
    # Its physical neighbor is context, never an extra primary ontology hit.
    assert {row['id'] for row in result['context_chunks']} == supplementary
    assert reference_reads == source_reads == []
    search_hydrations = len(hydrations)
    context = question_context(service, project, request)
    assert {row['id'] for row in context['hits']} == primary
    assert {row['id'] for row in context['context_chunks']} == supplementary
    assert {row['id'] for row in context['evidence_rows']} == primary | supplementary
    for key in ('historical_references', 'future_references', 'uncertain_references'):
        assert context[key] == []
    document = documents['document' if scope['ontology_scope'] == 'ids' else 'unknown-document']
    assert source_reads == [(project, [document['version_id']])]
    assert len(reference_reads) == 1
    reference = reference_reads[0]
    assert reference['_reference_limit'] == 501
    assert reference['_reference_terms'] == ['refund']
    expected_versions = {row['id']: row['version_id'] for row in rows}
    assert all(row['version_id'] == expected_versions[row['id']]
               for row in [*result['hits'], *context['evidence_rows'], *context['context_chunks']])
    assert all(row['_allowed_source_version_ids'] == [document['version_id']]
               for row in context['evidence_rows'])
    assert all(row['_allowed_chunk_version_ids'] == [row['version_id']]
               and row['answer_eligible'] is True and row['usage'] == 'effective_basis'
               for row in context['evidence_rows'])
    for row in [*result['context_chunks'], *context['context_chunks'],
                *[item for item in context['evidence_rows'] if item['id'] in supplementary]]:
        assert row['context_reason'] == 'adjacent_chunk' and row['anchor_chunk_id'] == 'a'
        assert row['source_id'] == document['id']
        assert row['metadata']['source_version_id'] == document['version_id']
        assert row['metadata']['applicability'] == document['metadata']['applicability']
        assert 'keyword_score' not in row and 'retrieval_channel' not in row
    assert all('_source_context_authorization' not in row for row in context['evidence_rows'])
    forbidden = {'b', 'a-neighbor', *[decoy['id'] for decoy in decoys]}
    assert all(row['id'] not in forbidden for container in (result, context)
               for key in ('hits', 'source_chunks', 'context_chunks', 'evidence_rows',
                           'historical_references', 'future_references', 'uncertain_references')
               for row in container.get(key, []))
    assert len(store.calls) == 2
    assert all(call[4]['ontology_scope'] == scope['ontology_scope']
               and call[5]['index_version'] == index['index_version'] for call in store.calls)
    primary_positions = {0, search_hydrations}
    for position, (selected, ids, kinds) in enumerate(hydrations):
        assert normalize_time(selected['valid_at']) == normalize_time(request['valid_at'])
        assert selected['include_unknown'] is False
        if position in primary_positions:
            assert selected['ontology_scope'] == scope['ontology_scope']
            assert selected.get('ontology_ids') == scope.get('ontology_ids')
            assert set(ids) == {'a', 'b', 'orphan', 'unbound'} and kinds == ['chunk']
        else:
            assert selected['ontology_scope'] == 'all' and selected['ontology_ids'] is None
            assert selected['_platform_match'] == 'all_only'
            assert set(ids) <= primary | {document['id']}
            assert kinds in (['chunk'], ['document'])
