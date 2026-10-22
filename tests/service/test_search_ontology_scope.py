import pytest

from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.answers import question_context
from knowledge_service.services.service import KnowledgeService


@pytest.fixture
def postgres_backend():
    """The scope regression uses in-memory authority and projection doubles."""
    yield


class ScopeRepository:
    def __init__(self):
        self.rows = []
        self.query_count = 0
        self.reference_reads = []
        self.source_reads = []
        self.source = {'id': 'document', 'version_id': 'source:1', 'version': 1,
                       'kind': 'document', 'text': 'refund source',
                       'ontology_id': 'release-a', 'recorded_at': '2020-01-01T00:00:00Z',
                       'valid_from': '2020-01-01T00:00:00Z', 'valid_until': None,
                       'metadata': {'effective_time_source': 'explicit',
                                    'applicability': {'platforms': {'mode': 'all'}}}}
        for index, (key, ontology, text) in enumerate((
            ('a', 'release-a', 'refund'), ('b-neighbor', 'release-b', 'secret'),
            ('a-neighbor', 'release-a', 'neighbor'), ('b', 'release-b', 'refund'),
            ('orphan', 'foreign-release', 'refund'), ('unbound', None, 'refund'),
        )):
            self.rows.append({
                'id': key, 'version_id': key + ':1', 'version': 1, 'kind': 'chunk',
                'text': text, 'ontology_id': ontology, 'source_id': 'document',
                'recorded_at': '2020-01-01T00:00:00Z',
                'valid_from': '2020-01-01T00:00:00Z', 'valid_until': None,
                'metadata': {'start_char': index, 'source_version_id': 'source:1',
                             'effective_time_source': 'explicit',
                             'applicability': {'platforms': {'mode': 'all'}}},
            })

    def list_ontologies(self, project):
        return [{'id': 'release-a'}, {'id': 'release-b'}]

    def query(self, project, **kwargs):
        if kwargs.get('_reference_mode'):
            self.reference_reads.append(kwargs)
        else:
            self.query_count += 1
        return list(self.rows)

    def records_by_version_ids(self, project, versions):
        self.source_reads.append((project, list(versions)))
        return [dict(self.source)] if self.source['version_id'] in versions else []

    def records_at_knowledge_time(self, project, record_ids, known_at):
        return [dict(self.source)] if ('document' in record_ids and
            self.source['recorded_at'] <= known_at) else []

    def distinct_applicability_values(self, project):
        return {}

    def keyword_candidates(self, *args, **kwargs):
        return []

    def projection_ready(self, *args):
        return True


class ReadyMilvus:
    available = True

    def __init__(self):
        self.calls = 0

    def search_bm25(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError('Milvus cannot enforce ontology release scope')


@pytest.mark.parametrize('connected', [False, True])
@pytest.mark.parametrize('scope,allowed', [
    ({'ontology_scope': 'ids', 'ontology_ids': ['release-a']}, {'a', 'a-neighbor'}),
    ({'ontology_scope': 'unknown'}, {'orphan', 'unbound'}),
])
def test_search_and_qa_keep_hits_and_adjacent_context_inside_release_scope(connected, scope, allowed):
    store = ReadyMilvus() if connected else None
    service = KnowledgeService(ScopeRepository(), HashingEncoder(), store)
    service.ontology_labels = lambda project: {}
    service.annotate_structure_pending = lambda project, rows: 0
    request = {'query': 'refund', 'retrieval_mode': 'keyword',
               'k_entities': 0, 'k_relations': 0, 'k_chunks': 10, **scope}
    result = service.search('p', request)
    assert result['hits']
    assert {row['id'] for row in result['hits']} <= allowed
    assert {row['id'] for row in result.get('context_chunks', [])} <= allowed
    assert service.repository.query_count == 1
    if scope['ontology_scope'] == 'ids':
        assert {row['id'] for row in result['context_chunks']} == {'a-neighbor'}
    context = question_context(service, 'p', request)
    assert context['evidence_rows']
    assert {row['id'] for row in context['evidence_rows']} <= allowed
    assert {row['id'] for row in context['context_chunks']} <= allowed
    assert service.repository.query_count == 2
    assert service.repository.source_reads == [('p', ['source:1'])]
    assert len(service.repository.reference_reads) == 1
    reference = service.repository.reference_reads[0]
    assert reference['_reference_limit'] == 501
    assert reference['_reference_terms'] == ['refund']
    if store:
        assert store.calls == 0
