from copy import deepcopy
from contextlib import nullcontext
import json
from threading import RLock
from types import SimpleNamespace
import unittest

from knowledge_service.repository import OntologyDraftStore, Repository
from knowledge_service.repository.snapshot_validation import validate_restore_snapshot


def full_snapshot(version=18):
    draft = {
        'id': 'draft', 'project_id': 'project', 'base_ontology_id': None,
        'source_kind': 'manual', 'status': 'pending', 'revision': 1,
        'title': 'Current draft', 'summary': '', 'source_context': {},
        'validation_report': None, 'validation_fingerprint': None,
        'validated_at': None, 'submitted_at': None, 'published_ontology_id': None,
        'created_at': 'now', 'updated_at': 'now',
    }
    if version < 18:
        draft['legacy_artifact_id'] = None
    if version < 17:
        draft.pop('submitted_at')
    if version < 16:
        draft.pop('validated_at')
    return {
        'namespace': 'namespace', 'schema_version': version,
        'governance_history_included': True,
        'project': {'id': 'project', 'name': 'Current', 'metadata': {}, 'created_at': 'now'},
        'records': [{
            'id': 'document', 'project_id': 'project', 'kind': 'document', 'text': 'Source',
            'metadata': {}, 'valid_from': None, 'valid_until': None, 'version': 1,
            'version_id': 'document-version', 'recorded_at': 'now', 'superseded_at': None,
            'business_context': {'preserved': True},
        }],
        'ontologies': [], 'artifacts': [],
        'governance': {
            'assertions': [], 'assertion_events': [], 'fact_keys': [], 'ingest_runs': [],
            'resolution_reviews': [], 'merge_operations': [],
            'ontology': {'drafts': [draft], 'operations': [], 'decisions': [], 'publish_requests': []},
        },
        'provenance': {'activities': [], 'edges': [], 'record_version_assertions': []},
    }


class CurrentStorageContractTests(unittest.TestCase):
    def validate(self, snapshot):
        return validate_restore_snapshot(snapshot)

    def test_current_full_snapshot_preserves_current_draft_and_record_fields(self):
        snapshot = full_snapshot()
        original = deepcopy(snapshot)
        result = self.validate(snapshot)
        self.assertTrue(result['declared_full'])
        self.assertEqual(result['ontology']['drafts'][0]['submitted_at'], None)
        self.assertEqual(snapshot, original)

    def test_current_lean_snapshot_remains_supported(self):
        snapshot = full_snapshot()
        snapshot['governance_history_included'] = False
        snapshot.pop('governance')
        snapshot.pop('provenance')
        self.assertFalse(self.validate(snapshot)['declared_full'])

    def test_export_stamps_current_format_and_validates_as_a_complete_snapshot(self):
        snapshot = full_snapshot()
        repository = Repository.__new__(Repository)
        repository._transaction = nullcontext
        repository.get_project = lambda project: snapshot['project']
        repository._db = SimpleNamespace(execute=lambda *args: SimpleNamespace(
            fetchone=lambda: ('namespace',), fetchall=lambda: []))
        repository._assertions = SimpleNamespace(export=lambda project: ([], []))
        repository._ingest = SimpleNamespace(export=lambda project: [])
        repository._reviews = SimpleNamespace(export=lambda project: [])
        repository._ontology_drafts = SimpleNamespace(export=lambda project: snapshot['governance']['ontology'])
        for method in ('list_ontologies', 'list_merge_operations', 'list_record_version_assertions',
                       'list_provenance_activities', 'list_provenance_edges'):
            setattr(repository, method, lambda project: [])
        exported = repository.export_projection('project')
        self.assertEqual(exported['schema_version'], 18)
        self.assertTrue(self.validate(exported)['declared_full'])

    def test_old_snapshot_formats_are_rejected_for_full_and_lean_restores(self):
        for version in (14, 15, 16, 17):
            for full in (False, True):
                with self.subTest(version=version, full=full):
                    snapshot = full_snapshot(version)
                    snapshot['governance_history_included'] = full
                    with self.assertRaisesRegex(ValueError, 'schema_version'):
                        self.validate(snapshot)

    def test_both_snapshot_modes_require_the_current_integer_format(self):
        for full in (False, True):
            for version in (None, True, '18', 19):
                with self.subTest(full=full, version=version):
                    snapshot = full_snapshot()
                    snapshot['governance_history_included'] = full
                    snapshot['schema_version'] = version
                    with self.assertRaisesRegex(ValueError, 'schema_version'):
                        self.validate(snapshot)
            snapshot = full_snapshot()
            snapshot['governance_history_included'] = full
            snapshot.pop('schema_version')
            with self.assertRaisesRegex(ValueError, 'schema_version'):
                self.validate(snapshot)

    def test_removed_draft_artifact_link_is_rejected_in_current_full_snapshot(self):
        snapshot = full_snapshot()
        snapshot['governance']['ontology']['drafts'][0]['legacy_artifact_id'] = 'old-artifact'
        with self.assertRaisesRegex(ValueError, 'legacy_artifact_id'):
            self.validate(snapshot)
        store = OntologyDraftStore(SimpleNamespace(_db=None, _lock=RLock()))
        with self.assertRaisesRegex(ValueError, 'legacy_artifact_id'):
            store.compare_and_set('project', 'draft', 1, {'legacy_artifact_id': 'old-artifact'})

    def test_current_full_validation_still_rejects_missing_draft_fields_and_embedding(self):
        snapshot = full_snapshot()
        snapshot['governance']['ontology']['drafts'][0].pop('submitted_at')
        with self.assertRaisesRegex(ValueError, 'submitted_at'):
            self.validate(snapshot)
        snapshot = full_snapshot()
        snapshot['records'][0]['embedding'] = [1.0, 0.0]
        with self.assertRaisesRegex(ValueError, 'embedding'):
            self.validate(snapshot)

    def test_repository_rejects_obsolete_postgres_vector_options(self):
        repository = Repository.__new__(Repository)
        repository.get_project = lambda project: None
        repository._lock = RLock()
        repository._db = SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchall=lambda: []))
        for method, options in (
            (repository.current_records, {'vectors': 'array'}),
            (repository.query, {'vectors': 'list'}),
            (repository.query, {'include_embeddings': False}),
        ):
            with self.subTest(method=method.__name__, options=options):
                with self.assertRaises(TypeError):
                    method('project', **options)
        with self.assertRaises(AttributeError):
            repository.store_embeddings('project', [], [], 'model')

    def test_record_reconstruction_preserves_payload_without_vector_options(self):
        payload = {'id': 'record', 'kind': 'entity', 'text': 'Entity', 'business_context': {'a': 1}}
        row = {'payload': json.dumps(payload), 'project_id': 'project', 'version': 2,
               'version_id': 'version', 'recorded_at': 'now', 'superseded_at': None}
        self.assertEqual(Repository._record(row), {**payload, **{key: value for key, value in row.items() if key != 'payload'}})
        with self.assertRaises(TypeError):
            Repository._record(row, vectors='list')


if __name__ == '__main__':
    unittest.main()
