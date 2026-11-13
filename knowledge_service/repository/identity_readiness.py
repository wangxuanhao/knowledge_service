"""Fail closed until the source-qualified native generation is audited and live."""


def source_identity_projection_readiness(repository,project):
    from ..services.retrieval import PROJECTION_SCHEMA_VERSIONS
    try:
        with repository.independent_read_view() as reader:
            index=reader.projections.search_index(project,PROJECTION_SCHEMA_VERSIONS['milvus'])
            cursor=reader.projections.cursor(project,'milvus') or {}
            state=cursor.get('rebuild_state') or {}
            required={'source_contexts_v1','entity_source_contexts_v1','relation_source_contexts_v1','entity_fact_contexts_v1'}
            if not required<=set(index.get('search_capabilities') or []):
                return {'ready':False,'reason':'source_context_capabilities_missing'}
            if state.get('phase')!='ready' or not state.get('build_id') or (state.get('validation') or {}).get('ok') is not True:
                return {'ready':False,'reason':'source_context_build_not_validated'}
            if state.get('uncertain_write') or state.get('requires_new_snapshot'):
                return {'ready':False,'reason':'source_context_build_uncertain'}
            diagnostics=reader.source_context_diagnostics(project)
            if diagnostics.get('missing_active',diagnostics.get('missing')) or diagnostics.get('invalid_active',diagnostics.get('invalid')):
                return {'ready':False,'reason':'source_context_support_incomplete','diagnostics':diagnostics}
            return {'ready':True,'index_version':index['index_version'],'watermark':index['watermark']}
    except RuntimeError as exc:
        return {'ready':False,'reason':getattr(exc,'state',None) or str(exc)}
