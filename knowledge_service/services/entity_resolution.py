"""三段式提及到实体的解析。"""
import logging
from dataclasses import dataclass

from .retrieval import RetrievalEngine
from .entity_matching import semantic_candidates
from ..domain.applicability import scope_identity

LOG = logging.getLogger('knowledge_service.entity_resolution')


@dataclass(frozen=True)
class ResolutionOutcome:
    status: str
    canonical_id: str | None = None
    candidate_id: str | None = None
    score: float | None = None
    reason: str = ''


class EntityResolver:
    def __init__(self, repository, encoder, milvus_store=None, *, retrieval=None):
        self.repository = repository
        self.encoder = encoder
        self.retrieval = retrieval or RetrievalEngine(repository, encoder, milvus_store)

    def semantic_candidates(self, project_id, text, rows, *, scope=None, limit=12):
        return semantic_candidates(self.retrieval, project_id, text, rows,
                                   scope=scope, limit=limit)

    def resolve(self, project_id, mention, *, review_threshold, merge_threshold, tx=None):
        if not 0 <= review_threshold < merge_threshold <= 1:
            raise ValueError('解析阈值要求 0 <= review < merge <= 1')
        if not isinstance(mention, dict) or mention.get('kind') != 'entity':
            raise ValueError('实体解析需要一个实体提及')
        rows = [row for row in self.repository.current_records(project_id, kinds=['entity'])
                if row['kind'] == 'entity' and not row.get('metadata', {}).get('_deleted')
                and row['id'] != mention.get('id')]
        mention_scope = scope_identity((mention.get('metadata') or {}).get('applicability'))
        eligible = [row for row in rows if scope_identity((row.get('metadata') or {}).get('applicability')) == mention_scope and all(row.get(key) == mention.get(key)
                    for key in ('type', 'valid_from', 'valid_until'))]
        folded = mention.get('text', '').casefold()
        exact = [row for row in eligible if folded in [name.casefold() for name in
                 [row['text'], *row.get('metadata', {}).get('aliases', [])]
                 if isinstance(name, str)]]
        if len(exact) == 1:
            return ResolutionOutcome('aligned', canonical_id=exact[0]['id'], score=1.0,
                                     reason='exact_name_or_alias')
        if not eligible:
            return ResolutionOutcome('separate', reason='no_compatible_candidate')
        hits = self.semantic_candidates(project_id, mention.get('text', ''), eligible, limit=1)
        best, score = hits[0], float(hits[0]['score'])
        if score >= merge_threshold and mention.get('_auto_merge', True):
            return ResolutionOutcome('aligned', canonical_id=best['id'], score=score,
                                     reason='semantic_high_band')
        if score >= review_threshold:
            return ResolutionOutcome('review', candidate_id=best['id'], score=score,
                                     reason='semantic_gray_band')
        return ResolutionOutcome('separate', candidate_id=best['id'], score=score,
                                 reason='semantic_low_band')
