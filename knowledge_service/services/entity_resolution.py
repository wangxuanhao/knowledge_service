"""三段式提及到实体的解析。"""
import logging
from dataclasses import dataclass

from .retrieval import RetrievalEngine
from .entity_matching import semantic_candidates
from ..domain.entity_identity import (IdentityPolicy, compatible_identity, exact_name,
    identity_scope, key_conflict, projection_readiness, source_boundary, strong_match)
from .ontology import Ontology, UnknownOntologyTerm

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
        from ..repository import OntologyNotPublished
        policy = IdentityPolicy()
        try:
            ontology = Ontology(self.repository.get_ontology(project_id)['turtle'])
            mention = {**mention, 'type': str(ontology.resolve(mention['type'], ontology.classes))}
            policy = ontology.identity_policy(mention['type'])
            normalized = []
            for row in rows:
                try: normalized.append({**row, 'type': str(ontology.resolve(row['type'], ontology.classes))})
                except UnknownOntologyTerm: continue
            rows = normalized
        except OntologyNotPublished:
            pass
        except UnknownOntologyTerm:
            return ResolutionOutcome('separate', reason='no_compatible_candidate')
        same_identity_scope = [row for row in rows if row.get('type') == mention.get('type')
            and identity_scope(row, policy) == identity_scope(mention, policy)]
        eligible = [row for row in same_identity_scope if compatible_identity(row, mention, policy)]
        strong = [row for row in eligible if strong_match(row, mention, policy)]
        exact = strong or [row for row in eligible if exact_name(mention, row)]
        if len(exact) > 1:
            return ResolutionOutcome('review', reason='ambiguous_exact_identity')
        if len(exact) == 1:
            if source_boundary(exact[0]) != source_boundary(mention) and projection_readiness(
                    self.repository, project_id).get('ready') is not True:
                return ResolutionOutcome('review', candidate_id=exact[0]['id'],
                    score=1.0, reason='source_identity_projection_not_ready')
            return ResolutionOutcome('aligned', canonical_id=exact[0]['id'], score=1.0,
                                     reason='strong_identity_key' if strong else 'exact_name_or_alias')
        if any(exact_name(mention, row) and key_conflict(row, mention, policy)
               for row in same_identity_scope):
            return ResolutionOutcome('review', reason='conflicting_identity_key')
        # Similarity is never sufficient evidence for crossing a source boundary.
        eligible = [row for row in eligible if source_boundary(row) == source_boundary(mention)]
        if not eligible:
            return ResolutionOutcome('separate', reason='no_compatible_candidate')
        hits = self.semantic_candidates(project_id, mention.get('text', ''), eligible, limit=1)
        if not hits:
            return ResolutionOutcome('separate', reason='no_semantic_candidate')
        best, score = hits[0], float(hits[0]['score'])
        if policy.declared and policy.mode == 'reference' and score >= review_threshold:
            return ResolutionOutcome('review', candidate_id=best['id'], score=score,
                                     reason='reference_similarity_requires_review')
        if score >= merge_threshold and mention.get('_auto_merge', True):
            return ResolutionOutcome('aligned', canonical_id=best['id'], score=score,
                                     reason='semantic_high_band')
        if score >= review_threshold:
            return ResolutionOutcome('review', candidate_id=best['id'], score=score,
                                     reason='semantic_gray_band')
        return ResolutionOutcome('separate', candidate_id=best['id'], score=score,
                                 reason='semantic_low_band')
