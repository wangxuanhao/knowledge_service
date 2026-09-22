import pytest

from knowledge_service.services.entity_resolution import EntityResolver
from knowledge_service.repository import Repository


class Encoder:
    identity = 'resolver-v1'

    def encode(self, texts):
        vectors = {'张三': [1.0, 0.0], '张先生': [0.95, 0.05], '李四': [0.0, 1.0]}
        return [vectors.get(text, [0.5, 0.5]) for text in texts]


def _repo(tmp_path):
    repo = Repository(tmp_path / 'resolution.sqlite')
    project_id = repo.create_project('甲')['id']
    repo.put_record(project_id, {
        'id': 'person-1', 'kind': 'entity', 'type': 'Person', 'text': '张三',
        'metadata': {'aliases': ['老张']}, 'embedding': [1.0, 0.0],
        'embedding_model': 'resolver-v1',
    })
    return repo, project_id


def test_entity_resolver_has_aligned_review_and_separate_bands(tmp_path):
    repo, project_id = _repo(tmp_path)
    resolver = EntityResolver(repo, Encoder())

    exact = resolver.resolve(project_id, {
        'id': 'mention-1', 'kind': 'entity', 'type': 'Person', 'text': '老张'},
        review_threshold=.70, merge_threshold=.90)
    assert exact.status == 'aligned' and exact.canonical_id == 'person-1'

    review = resolver.resolve(project_id, {
        'id': 'mention-2', 'kind': 'entity', 'type': 'Person', 'text': '张先生',
        '_auto_merge': False}, review_threshold=.70, merge_threshold=.90)
    # 删列后本地无向量，语义相似合并（review 档）不可用，降级 separate；语义档需 Milvus 向量
    assert review.status == 'separate'
    assert review.candidate_id is None

    separate = resolver.resolve(project_id, {
        'id': 'mention-3', 'kind': 'entity', 'type': 'Company', 'text': '张先生'},
        review_threshold=.70, merge_threshold=.90)
    assert separate.status == 'separate'


def test_resolution_review_is_persisted_and_cas_guarded(tmp_path):
    repo, project_id = _repo(tmp_path)
    review = repo.create_resolution_review(project_id, {
        'id': 'resolution-1', 'source_entity_id': 'mention-2',
        'candidate_entity_id': 'person-1', 'score': .82,
        'payload': {'reason': 'semantic_gray_band'},
    })
    decided = repo.decide_resolution_review(
        project_id, review['id'], expected_version=1, decision='separate',
        reason='同名不同人', actor='reviewer')

    assert decided['decision_version'] == 2
    assert decided['status'] == 'separate'
    with pytest.raises(ValueError, match='冲突'):
        repo.decide_resolution_review(
            project_id, review['id'], expected_version=1, decision='merged',
            reason='过期操作', actor='reviewer')
