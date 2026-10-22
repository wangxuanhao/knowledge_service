"""Graph session seams; runnable with unittest without PostgreSQL fixtures."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from knowledge_service.services.graph_sessions import GraphSessions


class Repository:
    def __init__(self):
        self.snapshots = 0
        self.watermark = 7
        self.artifacts = {}
        self.projections = SimpleNamespace(project_watermark=lambda _: self.watermark)

    @contextmanager
    def read_snapshot(self):
        self.snapshots += 1
        try:
            yield
        finally:
            self.snapshots -= 1

    def get_project(self, project):
        assert self.snapshots == 1

    def save_artifact(self, kind, value):
        self.artifacts[value['id']] = deepcopy(value)

    def get_artifact(self, kind, key):
        return deepcopy(self.artifacts[key])


class Reads:
    def __init__(self, repo):
        self.repo = repo
        self.calls = []
        self.missing = False

    def count(self, project, scope, *, kinds):
        return 3 if kinds == ['entity'] else 2

    def page(self, project, scope, **kwargs):
        assert self.repo.snapshots == 1
        self.calls.append(('page', project, scope, kwargs))
        return {'records': [{'id': 'r', 'kind': 'relation', 'version_id': 'rv', 'text': '线' * 200,
                             'subject_id': 'a', 'object_id': 'b', 'metadata': {'private': True}}],
                'next_cursor': 'scoped-cursor'}

    def by_ids(self, project, scope, ids, *, kinds):
        assert self.repo.snapshots == 1
        self.calls.append(('by_ids', project, scope, ids, kinds))
        return [{'id': id, 'kind': 'entity', 'version_id': id + 'v', 'text': id,
                 'metadata': {'private': True}} for id in ids if not self.missing or id != 'b']


class ConnectedGraphPages(unittest.TestCase):
    def setUp(self):
        self.repo = Repository()
        self.service = GraphSessions(self.repo)
        self.service.reads = Reads(self.repo)
        self.scope = {'filters': {'field': 'metadata.region', 'op': 'eq', 'value': '北京'}}
        self.manifest = self.service.create('p', self.scope, actor_id='owner')

    def page(self, **kwargs):
        return self.service.page('p', self.manifest['id'], 'connected', actor_id='owner', **kwargs)

    def test_relation_page_hydrates_only_scoped_endpoints_in_same_snapshot(self):
        page = self.page(limit=400)
        self.assertEqual([row['id'] for row in page['entities']], ['a', 'b'])
        self.assertEqual([row['id'] for row in page['records']], ['r'])
        self.assertEqual(len(page['records'][0]['text']), 160)
        self.assertTrue(all('metadata' not in row for row in [*page['records'], *page['entities']]))
        calls = self.service.reads.calls
        self.assertEqual(calls[0][2], self.scope)
        self.assertEqual(calls[0][3]['kinds'], ['relation'])
        self.assertEqual(calls[1][2:], (self.scope, ['a', 'b'], ['entity']))
        self.assertEqual(page['total'], self.manifest['relation_count'])
        self.assertFalse(page['complete'])

    def test_connected_cursor_remains_bound_to_kind_owner_and_watermark(self):
        first = self.page(limit=1)
        self.page(cursor=first['next_cursor'], limit=1)
        self.assertEqual(self.service.reads.calls[-2][3]['after'], 'scoped-cursor')
        with self.assertRaisesRegex(ValueError, '游标'):
            self.service.page('p', self.manifest['id'], 'entities', actor_id='owner', cursor=first['next_cursor'])
        with self.assertRaises(PermissionError):
            self.service.page('p', self.manifest['id'], 'connected', actor_id='other')
        self.repo.watermark += 1
        with self.assertRaisesRegex(ValueError, '变化'):
            self.page()

    def test_connected_pages_are_bounded_and_never_emit_dangling_edges(self):
        for invalid in (0, 501, True, 1.5):
            with self.assertRaises(ValueError):
                self.page(limit=invalid)
        self.service.reads.missing = True
        with self.assertRaisesRegex(ValueError, '端点'):
            self.page(limit=1)

    def test_expired_connected_session_requires_explicit_restart(self):
        self.repo.artifacts[self.manifest['id']]['expires_at'] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ValueError, '过期'):
            self.page()


if __name__ == '__main__':
    unittest.main()
