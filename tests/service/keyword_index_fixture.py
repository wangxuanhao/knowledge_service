"""Explicit keyword-index fixtures with real PostgreSQL readiness guards."""
from types import SimpleNamespace
import json

from knowledge_service.repository.scoped_reads import ScopedReads
from test_production_projection_receipts import activate_legacy


def install_keyword_store(service, monkeypatch):
    """Replace only external ranking I/O; authority and index guards stay real."""
    repo = service.repository

    def search(project, query, *, kinds, k, scope, platform_match=None,
               eligible_version_ids=None, **options):
        assert options['retrieval_mode'] == 'keyword'
        rows = ScopedReads(repo).ranked_keyword(
            project, {**scope, '_platform_match': platform_match}, query,
            kinds=kinds, limit=100)
        if eligible_version_ids is not None:
            eligible = set(eligible_version_ids)
            rows = [row for row in rows if row['version_id'] in eligible]
        from knowledge_service.repository.projection_contexts import ProjectionContexts
        from knowledge_service.domain.applicability import build_scope_predicate,dimension_mode
        contexts=ProjectionContexts(repo).collect(project,rows) if rows else {}
        predicate=build_scope_predicate(scope.get('applicability'),scope.get('valid_at'),scope.get('include_unknown',True))
        mode={'all_only':'all','unknown_only':'unknown','specific_only':'specific'}.get(platform_match)
        result=[]
        for row in rows[:k]:
            support=next((c for c in contexts.get(row['id'],[]) if row['kind']!='entity' or
                predicate.matches(c) and (not mode or dimension_mode(c.get('metadata',{}).get('applicability'),'platforms')==mode)),None)
            result.append({'record_id':row['id'],'version_id':row['version_id'],'score':row['score'],
                **({key:row[key] for key in ('window_start','window_end') if key in row}),
                **({'support_context_id':support['id'],'support_context_version':support['version']} if support else {})})
        return result

    monkeypatch.setattr(service, 'milvus_store', SimpleNamespace(available=True, search=search))


def activate_keyword_index(repo, project):
    """Activate this test's written data at its actual current watermark."""
    activate_legacy(repo, project, {}, watermark=repo.projections.project_watermark(project))
    repo._db.execute("""UPDATE projection_cursors SET schema_version=4,
        rebuild_state=(rebuild_state::jsonb || ?::jsonb) WHERE project_id=? AND target='milvus'""",
        (json.dumps({'search_capabilities':['attributes_v1','attribute_windows_v1','source_contexts_v1',
                    'entity_source_contexts_v1','relation_source_contexts_v1','entity_fact_contexts_v1']}),project))


def ready_keyword_index(service, project, monkeypatch):
    """Publish a current generation and replace only external keyword I/O."""
    activate_keyword_index(service.repository, project)
    install_keyword_store(service, monkeypatch)
