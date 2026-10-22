"""Explicit low-level version fixtures; never a production publication API."""
def seed_ontology(repository, project_id, turtle, summary, metadata=None):
    from knowledge_service.services.ontology import Ontology
    repository = vars(repository).get('_ApplicationData__repository', repository)
    with repository._transaction():
        repository.get_project(project_id)
        return repository._insert_ontology_version(
            project_id, turtle, Ontology(turtle).summary(), {**(metadata or {}), 'write_path': 'test_fixture'})


def seed_project_ontology(client, project_path, turtle):
    from knowledge_service.services.ontology import Ontology
    project_id = project_path.rstrip('/').split('/')[-1]
    return seed_ontology(client.app.state.service.repository, project_id,
                         turtle, Ontology(turtle).summary())
