"""Project vectorization uses the existing explicit task entry point."""
from ..repository.projection_store import milvus_delivery_state


def install(app, service):
    @app.get('/api/projects/{p}/indexes/status')
    def status(p: str):
        service.repository.get_project(p)
        cursor = service.repository.projections.cursor(p, 'milvus')
        watermark = service.repository.projections.project_watermark(p)
        state = milvus_delivery_state(cursor, service.projection_sync.schema_version)
        if state in {'available', 'leased'}:
            state = 'ready' if cursor['applied_seq'] == watermark else 'building'
        return {'project_id': p, 'state': state,
                'last_error': (cursor or {}).get('last_error'),
                'collection': (cursor or {}).get('active_destination')}

    @app.post('/api/projects/{p}/indexes/rebuild', status_code=202)
    def rebuild(p: str):
        service.repository.get_project(p)
        return app.state.jobs.enqueue('semantic_index', {'rebuild': True}, p)
