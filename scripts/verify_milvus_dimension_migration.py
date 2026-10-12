"""Exercise the real Milvus schema migration in a disposable collection.

The authoritative projection is in memory; no PostgreSQL or user collection is
modified. This complements the authenticated application/1024-dimension probe.
"""
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowledge_service.integrations.milvus_store import MilvusStore
from knowledge_service.services.job_handlers import job_handlers


def main():
    name = 'verify_dimension_' + uuid4().hex
    rows = {p: [{'id': p + '-chunk', 'version_id': p + '-v1',
                 'kind': 'chunk', 'text': '维度迁移验证原文', 'project_id': p}]
            for p in ('p1', 'p2')}
    artifacts = {}

    class Projection:
        def list_projects(self):
            return [{'id': p} for p in rows]

        def current_records(self, p, **kwargs):
            return [dict(row) for row in rows[p]]

        def get_ontology(self, p):
            return {'id': p + '-ontology'}

        def list_artifacts(self, kind):
            return [v for (k, _), v in artifacts.items() if k == kind]

        def save_artifact(self, kind, value):
            artifacts[(kind, value['id'])] = value

        def unit_of_work(self):
            return nullcontext()

    args = {'host': os.getenv('KG_MILVUS_HOST', 'localhost'),
            'port': os.getenv('KG_MILVUS_PORT', '19530'), 'collection': name}
    old = MilvusStore(dim=2, **args).connect()
    current = None
    owned = False
    try:
        assert not old._client.has_collection(name)
        owned = True
        old.ensure_collection()
        old.upsert([{**row, 'embedding': [1., 0.], 'embedding_model': 'old'}
                    for project in rows.values() for row in project])
        current = MilvusStore(dim=3, **args).connect()
        service = SimpleNamespace(data=Projection(),
            reconnect_vector_store=lambda: current,
            encoder=SimpleNamespace(identity='migration-probe', dimension=3,
                encode=lambda texts: [[1., 0., 0.] for _ in texts]))
        result = job_handlers(service, None)['semantic_index'](
            {}, 'p1', lambda *_: None, 'migration-probe')
        assert result['recreated'] and result['scope'] == 'collection', result
        assert result['restored_projects'] == ['p1', 'p2'], result
        assert current.collection_dim() == 3
        saved = current._client.query(name, filter='project_id in ["p1", "p2"]',
            output_fields=['project_id', 'embedding_model'], consistency_level='Strong')
        assert {row['project_id'] for row in saved} == {'p1', 'p2'}, saved
        assert all(row['embedding_model'] == 'migration-probe' for row in saved)
        assert all(value['status'] == 'completed' for value in artifacts.values())
        print('PASS real Milvus dimension migration: 2 -> 3; both projects restored')
    finally:
        if owned and old._client.has_collection(name):
            old._client.drop_collection(name)
            print('CLEANED disposable collection:', name)
        if current:
            current.close()
        old.close()


if __name__ == '__main__':
    main()
