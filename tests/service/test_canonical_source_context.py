"""The public source reader must never infer fact provenance from obsolete hints."""
from threading import RLock
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from knowledge_service.repository.scoped_reads import ScopedReads


class CanonicalSourceContextTests(unittest.TestCase):
    def test_unmapped_fact_cannot_use_obsolete_chunk_metadata(self):
        class EmptyDatabase:
            def execute(self, *_args):
                return SimpleNamespace(fetchall=lambda: [])

        repo = SimpleNamespace(_db=EmptyDatabase(), _lock=RLock())
        fact = {'id': 'fact', 'kind': 'entity', 'version_id': 'fact-v1',
                'metadata': {'chunk_id': 'anchor'}}
        anchor = {'id': 'anchor', 'kind': 'chunk', 'version_id': 'chunk-v1',
                  'source_id': 'doc', 'metadata': {'source_version_id': 'doc-v1'}}
        document = {'id': 'doc', 'kind': 'document', 'version_id': 'doc-v1'}

        def by_ids(_reader, _project, _scope, ids, *, kinds):
            return [row for row in (anchor, document)
                    if row['id'] in ids and row['kind'] in kinds]

        with patch.object(ScopedReads, 'by_ids', by_ids):
            context = ScopedReads(repo).source_context('project', {}, [fact])
        self.assertEqual(context, {'source_chunks': [], 'context_chunks': []})


if __name__ == '__main__':
    unittest.main()
