from test_legacy_import import legacy
from knowledge_service.services.legacy_import import LegacyImporter


def test_fast_load_never_starts_embedding_model(legacy):
    root, _, _, service = legacy
    class ForbiddenEncoder:
        identity = 'not-loaded'
        def encode(self, texts):
            raise AssertionError('Displaying a project must not wait for the model')
    service.encoder = ForbiddenEncoder()
    result = LegacyImporter(service, root).import_project('原名', build_vectors=False)
    assert result['counts']['nodes'] == 2
    assert len(service.repository.current_records(result['project']['id'])) > 2
