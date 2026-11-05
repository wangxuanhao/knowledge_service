"""Project vectorization uses the existing explicit task entry point."""


def install(app, service):
    @app.get('/api/projects/{p}/indexes/status')
    def status(p: str):
        return service.data.projection_status(p, service.projection_sync)

    @app.post('/api/projects/{p}/indexes/rebuild', status_code=202)
    def rebuild(p: str):
        service.data.get_project(p)
        return app.state.jobs.enqueue('semantic_index', {'rebuild': True}, p)
