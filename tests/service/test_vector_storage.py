"""向量存储验证：向量只在 Milvus，PostgreSQL 的 record_versions 不存 vector 列，
也不在 payload 里带 embedding。"""
import json

from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import Repository


def test_vector_column_absent_and_vectors_never_persisted_in_database(tmp_path):
    repository = Repository(tmp_path / 'fresh.sqlite')
    project_id = repository.create_project('新项目')['id']
    encoder = HashingEncoder()
    vector = encoder.encode(['退款商户'])[0]
    repository.put_record(project_id, {
        'id': 'e0', 'kind': 'entity', 'type': 'Thing', 'text': '退款商户',
        'embedding': vector, 'embedding_model': encoder.identity})

    # PostgreSQL 基线的 record_versions 本来就没有 vector 列
    columns = {
        row['name'] for row in repository._db.execute(
            'SELECT column_name AS name FROM information_schema.columns '
            'WHERE table_schema = current_schema() AND table_name = ?',
            ('record_versions',))
    }
    assert 'vector' not in columns

    # payload 不含 embedding（向量只在 Milvus，PostgreSQL 存纯元数据）
    row = repository._db.execute('SELECT payload FROM record_versions WHERE id=?', ('e0',)).fetchone()
    assert 'embedding' not in json.loads(row['payload'])

    # 读记录不再带 embedding 字段
    record = repository.query(project_id)[0]
    assert 'embedding' not in record
    repository.close()
