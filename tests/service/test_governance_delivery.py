"""Focused governance boundary regressions; no database is required."""
import copy
import threading
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from knowledge_service.api import knowledge
from knowledge_service.models import ResolutionReviewDecision
from knowledge_service.services.governance import Governance


TTL = '''@prefix ex: <http://ex/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
ex:Person a owl:Class . ex:age a owl:DatatypeProperty .
ex:S a sh:NodeShape; sh:targetClass ex:Person;
sh:property [ sh:path ex:age; sh:maxCount 1 ] .'''


class GovernanceDeliveryTests(unittest.TestCase):
    def service(self):
        rows = [dict(id=id, kind='entity', text=id, type='http://ex/Person',
                     version=1, ontology_id='release', metadata={}) for id in ('keep', 'drop')]
        rows += [dict(id=id, kind='attribute', type='http://ex/age', subject_id=subject,
                      version=2, value=value, datatype='http://www.w3.org/2001/XMLSchema#integer',
                      ontology_id='release', metadata={})
                 for id, subject, value in [('age-a', 'keep', 20), ('age-b', 'drop', 30)]]
        repo = SimpleNamespace(current_records=lambda p: copy.deepcopy(rows),
                               get_ontology=lambda p, ontology_id: {'turtle': TTL})
        service = SimpleNamespace(repository=repo, lock=threading.RLock())
        return service, rows

    def test_preview_returns_authoritative_conflicts_and_revisions_without_writing(self):
        service, rows = self.service()
        before = copy.deepcopy(rows)
        result = Governance(service).merge_preview('p', 'keep', 'drop', {'keep': 1, 'drop': 1})
        self.assertEqual(result['expected_versions'], {'keep': 1, 'drop': 1})
        self.assertEqual(result['attribute_conflicts'][0]['record_ids'], ['age-a', 'age-b'])
        self.assertEqual(result['attribute_conflicts'][0]['values'][1]['value'], 30)
        self.assertEqual(result['affected_records']['attributes'], 2)
        self.assertEqual(len(result['preview_fingerprint']), 64)
        self.assertEqual(result['attribute_conflicts'][0]['values'][0]['version'], 2)
        self.assertEqual(rows, before)

    def test_changed_or_added_attribute_invalidates_confirmed_preview_without_entity_revision(self):
        for change in ('value', 'new_attribute', 'new_relation'):
            with self.subTest(change=change):
                service, rows = self.service()
                governance = Governance(service)
                preview = governance.merge_preview('p', 'keep', 'drop', {'keep': 1, 'drop': 1})
                if change == 'value':
                    rows[2].update(value=99, version=3, version_id='changed-attribute')
                elif change == 'new_attribute':
                    rows.append({**rows[2], 'id': 'new-attribute', 'value': 40})
                else:
                    rows.append(dict(id='new-edge', kind='relation', subject_id='keep', object_id='drop', version=1))
                with self.assertRaisesRegex(ValueError, '重新预览'):
                    governance.merge('p', 'keep', 'drop', {'keep': 1, 'drop': 1},
                        attribute_winners=['age-a'], preview_fingerprint=preview['preview_fingerprint'])

    def test_merge_commit_guard_rechecks_changed_and_new_attributes_and_restores_parent(self):
        from knowledge_service.core.ingest_context import ingest_commit_guard
        service, rows = self.service()
        governance = Governance(service)
        preview = governance.merge_preview('p', 'keep', 'drop', {'keep': 1, 'drop': 1})
        entered = []

        @contextmanager
        def parent_guard():
            entered.append('parent')
            yield

        parent_token = ingest_commit_guard.set(parent_guard)
        try:
            with governance._merge_commit_guard('p', 'keep', 'drop',
                    {'keep': 1, 'drop': 1}, preview['preview_fingerprint']):
                transaction_guard = ingest_commit_guard.get()
                with transaction_guard():
                    entered.append('unchanged')
                rows.append({**rows[2], 'id': 'late-attribute', 'value': 45})
                with self.assertRaisesRegex(ValueError, '重新预览'):
                    with transaction_guard():
                        self.fail('stale merge reached the writer')
            self.assertIs(ingest_commit_guard.get(), parent_guard)
            self.assertEqual(entered, ['parent', 'unchanged', 'parent'])
        finally:
            ingest_commit_guard.reset(parent_token)

    def test_merge_guards_all_participating_attributes_even_the_unchanged_winner(self):
        from knowledge_service.core.ingest_context import ingest_commit_guard
        service, _ = self.service()
        service.repository.list_assertions = lambda *args, **kwargs: []
        seen = {}

        def write(project, records, **kwargs):
            seen.update(kwargs)
            # The real repository invokes this seam after acquiring formal table locks.
            with ingest_commit_guard.get()():
                return records

        service.write = write
        governance = Governance(service)
        preview = governance.merge_preview('p', 'keep', 'drop', {'keep': 1, 'drop': 1})
        result = governance.merge('p', 'keep', 'drop', {'keep': 1, 'drop': 1},
            attribute_winners=['age-a'], preview_fingerprint=preview['preview_fingerprint'])
        self.assertTrue(result['operation_id'])
        self.assertEqual(seen['expected_versions']['age-a'], 2)
        self.assertEqual(seen['expected_versions']['age-b'], 2)
        self.assertEqual(seen['ledger']['expected_versions']['age-a'], 2)
        self.assertIsNone(ingest_commit_guard.get())

    def test_transaction_recheck_rejects_a_late_change_after_merge_preparation(self):
        from knowledge_service.core.ingest_context import ingest_commit_guard
        service, rows = self.service()
        service.repository.list_assertions = lambda *args, **kwargs: []

        def write(*args, **kwargs):
            rows[2].update(value=99, version=3)
            with ingest_commit_guard.get()():
                self.fail('stale merge reached the writer')

        service.write = write
        governance = Governance(service)
        preview = governance.merge_preview('p', 'keep', 'drop', {'keep': 1, 'drop': 1})
        with self.assertRaisesRegex(ValueError, '重新预览'):
            governance.merge('p', 'keep', 'drop', {'keep': 1, 'drop': 1},
                attribute_winners=['age-a'], preview_fingerprint=preview['preview_fingerprint'])
        self.assertIsNone(ingest_commit_guard.get())

    def test_preview_preserves_merge_compatibility_and_revision_guards(self):
        service, _ = self.service()
        with self.assertRaisesRegex(ValueError, '版本冲突'):
            Governance(service).merge_preview('p', 'keep', 'drop', {'keep': 9, 'drop': 1})

    def test_resolution_merge_forwards_conflict_winners_and_returns_requested_review(self):
        service, _ = self.service()
        target = {'id': 'review', 'candidate_entity_id': 'keep', 'source_entity_id': 'drop'}
        service.data = SimpleNamespace(list_resolution_reviews=lambda *args, **kw:
            [{**target, 'status': 'merged'}, {'id': 'other', 'status': 'merged'}])
        app = FastAPI()
        app.state.auth_disabled = True
        knowledge.install(app, service)
        request = dict(decision='merged', expected_version=1, reason='同一对象',
            expected_entity_versions={'keep': 1, 'drop': 1}, attribute_winners=['age-b'],
            preview_fingerprint='a' * 64)
        with patch.object(Governance, 'merge', return_value={'operation_id': 'merge'}) as merge:
            response = TestClient(app).post('/api/projects/p/resolution-reviews/review', json=request)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(merge.call_args.kwargs['attribute_winners'], ['age-b'])
        self.assertEqual(merge.call_args.kwargs['preview_fingerprint'], 'a' * 64)
        self.assertEqual(result['review']['id'], 'review')


if __name__ == '__main__':
    unittest.main()
