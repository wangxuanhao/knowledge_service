"""删列验证：向量已迁到 Milvus，SQLite 不再存 vector 列，也不在 payload 里带 embedding。"""
import json

from knowledge_service.embeddings import HashingEncoder
from knowledge_service.repository import Repository


def test_vector_column_is_dropped_and_vectors_never_stored_in_sqlite(tmp_path):
    repository = Repository(tmp_path / 'fresh.sqlite')
    project_id = repository.create_project('新项目')['id']
    encoder = HashingEncoder()
    vector = encoder.encode(['退款商户'])[0]
    repository.put_record(project_id, {
        'id': 'e0', 'kind': 'entity', 'type': 'Thing', 'text': '退款商户',
        'embedding': vector, 'embedding_model': encoder.identity})

    # vector 列已删（migration 11 的 DROP COLUMN）
    columns = {row['name'] for row in repository._db.execute('PRAGMA table_info(record_versions)')}
    assert 'vector' not in columns

    # payload 不含 embedding（向量只在 Milvus，SQLite 存纯元数据）
    row = repository._db.execute('SELECT payload FROM record_versions WHERE id=?', ('e0',)).fetchone()
    assert 'embedding' not in json.loads(row['payload'])

    # 读记录不再带 embedding 字段
    record = repository.query(project_id)[0]
    assert 'embedding' not in record
    repository.close()