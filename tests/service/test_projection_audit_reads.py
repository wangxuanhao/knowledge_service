"""Independent, bounded PG reads for current build receipts and validation.

Automatic authority repair was replaced by explicit generation builds. Their
captured inputs and confirmed receipts retain the same read-isolation contract.
"""
import threading

import pytest

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import (
    ProjectionInput, build_projection, projection_fingerprint,
)
from knowledge_service.integrations.milvus_store import MilvusStore
from knowledge_service.repository.core import Repository
from knowledge_service.repository.pg_engine import SqliteishConnection
from knowledge_service.repository.projection_inputs import ProjectionInputs
from knowledge_service.repository.projection_receipts import ReceiptManifest
from knowledge_service.repository.projection_snapshot import CapturedProjectionInputs
from knowledge_service.repository.projection_store import ProjectionGap
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
from knowledge_service.services.projection_sync import ProjectionSync


def projection_row(index, *, project='project', index_version=2):
    return build_projection(ProjectionInput({
        'project_id': project, 'id': f'r{index}', 'version_id': f'captured-v{index}',
        'kind': 'entity', 'text': f'captured {index}',
    }), default_catalog(), index_version=index_version)


def capture_build(repo, project):
    sync = ProjectionSync(repo.projections, {}, schema_version=3)
    return repo.projections.begin_rebuild(
        project, 'milvus', 3, 'explicit-build', lease_guard=sync._rebuild_lease_guard,
        embedding_model='test', dim=1)


def test_receipt_scan_uses_a_private_connection_and_does_not_block_business_reads_or_writes(tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'audit-reader')
    project = repo.create_project('audit reader')['id']
    repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': 'a'})
    with repo._transaction():
        repo._db.execute('INSERT INTO milvus_projection_receipts '
            '(project_id,index_version,record_id,version_id,fingerprint) VALUES (?,?,?,?,?)',
            (project, 2, 'a', 'captured-a', 'a' * 64))
    entered, resume, read_done = threading.Event(), threading.Event(), threading.Event()
    failures, connections, pages = [], [], []
    original = SqliteishConnection.execute

    def gated(connection, statement, params=()):
        if (threading.current_thread() is audit_thread
                and 'FROM milvus_projection_receipts' in statement and not entered.is_set()):
            assert connection is not repo._db
            assert original(connection, 'SHOW transaction_read_only').fetchone()[0] == 'on'
            connections.append(connection)
            entered.set()
            assert resume.wait(5)
        return original(connection, statement, params)

    monkeypatch.setattr(SqliteishConnection, 'execute', gated)

    def audit():
        try:
            pages.extend(repo.projections.receipts.pages(project, 2, batch_size=1))
        except BaseException as exc:
            failures.append(exc)

    def business_read_and_write():
        try:
            assert repo.get_project(project)['id'] == project
            repo.put_record(project, {'id': 'b', 'kind': 'entity', 'text': 'concurrent business write'})
            read_done.set()
        except BaseException as exc:
            failures.append(exc)

    audit_thread = threading.Thread(target=audit)
    reader = threading.Thread(target=business_read_and_write)
    audit_thread.start()
    try:
        assert entered.wait(5), 'validation did not enter the receipt scan'
        reader.start()
        assert read_done.wait(1), 'background receipt scan blocks the primary Repository reader/writer'
    finally:
        resume.set()
        audit_thread.join(5)
        if reader.ident:
            reader.join(5)
    assert not audit_thread.is_alive() and not reader.is_alive()
    assert not failures
    assert len(connections) == 1
    assert pages == [{'a': {'version_id': 'captured-a', 'fingerprint': 'a' * 64}}]
    assert repo.get_record(project, 'b')['text'] == 'concurrent business write'


@pytest.mark.parametrize('unexpected_row', [False, True])
def test_validation_reads_only_scanned_receipts_in_bounded_project_generation_batches(unexpected_row):
    rows = [projection_row(index) for index in range(600)]
    expected = {row['record_id']: {'version_id': row['version_id'],
                                  'fingerprint': projection_fingerprint(row)} for row in rows}
    fetched = []

    class Receipts:
        def count(self, project, generation):
            assert (project, generation) == ('project', 2)
            return len(expected)

        def for_record_ids(self, project, generation, record_ids):
            assert (project, generation) == ('project', 2)
            assert len(record_ids) <= 512
            fetched.append(list(record_ids))
            return {record_id: expected[record_id] for record_id in record_ids if record_id in expected}

        def pages(self, *args):
            raise AssertionError('validation must not materialize the entire receipt manifest')

    if unexpected_row:
        rows.append(projection_row('unexpected-ghost'))
    store = MilvusStore(dim=1, encoder_identity='test')
    store.check_collection = lambda **kwargs: store

    def scan(expression, params, fields):
        assert expression == 'project_id == {project} and index_version == {index}'
        assert params == {'project': 'project', 'index': 2}
        assert 'embedding' not in fields and 'sparse' not in fields
        yield from rows

    store._query_pages = scan
    result = MilvusProjectionAdapter(store).validate('project', {'index_version': 2},
        default_catalog(), expected=ReceiptManifest(Receipts(), 'project', 2))
    assert result == {'ok': not unexpected_row, 'count': len(rows), 'expected_count': 600}
    assert [len(page) for page in fetched] == [512, 89 if unexpected_row else 88]
    assert [record_id for page in fetched for record_id in page] == [row['record_id'] for row in rows]


def test_compact_manifest_preserves_deleted_falsies_and_audit_records(tmp_path):
    repo = Repository(tmp_path / 'audit-falsies')
    project = repo.create_project('audit falsies')['id']
    other = repo.create_project('other')['id']
    expected = {}
    for index, deleted in enumerate([None, False, 0, '', [], {}, True, 1, 'false', [1], {'x': 1}]):
        row = repo.put_record(project, {'id': f'e{index}', 'kind': 'entity', 'text': 'a',
            'metadata': {'_deleted': deleted, '_audit': index == 1}})
        if not deleted:
            expected[row['id']] = row['version_id']
    repo.put_record(project, {'id': 'document', 'kind': 'document', 'text': 'not indexed'})
    repo.put_record(other, {'id': 'foreign', 'kind': 'entity', 'text': 'foreign'})
    sequence, manifest = repo.projection_authority_manifest(project, {'entity', 'relation', 'chunk'})
    assert sequence == repo.projections.project_watermark(project)
    assert manifest == expected


def test_independent_snapshot_is_read_only_and_payload_fetch_uses_current_versions(tmp_path):
    repo = Repository(tmp_path / 'audit-versions')
    project = repo.create_project('audit versions')['id']
    old = repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': 'old'})
    current = repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': 'current'})
    with repo._db.independent_read_snapshot() as snapshot:
        assert snapshot.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
    from psycopg.errors import ReadOnlySqlTransaction
    with pytest.raises(ReadOnlySqlTransaction):
        with repo._db.independent_read_snapshot() as snapshot:
            snapshot.execute('UPDATE projects SET name=name WHERE id=?', (project,))
    assert repo.projection_authority_records(project, [old['version_id']]) == []
    assert [r['version_id'] for r in repo.projection_authority_records(project, [current['version_id']])] == [current['version_id']]
    with pytest.raises(ValueError, match='256'):
        repo.projection_authority_records(project, ['v'] * 257)


def test_captured_build_reads_exact_versions_and_aborts_if_a_captured_body_disappears(tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'captured-audit-versions')
    project = repo.create_project('captured audit versions')['id']
    captured = repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': 'captured'})
    rebuild = capture_build(repo, project)
    current = repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': 'new current'})
    values = list(CapturedProjectionInputs(repo, rebuild['claim'], batch_size=1))
    assert [value.record['version_id'] for value in values] == [captured['version_id']]
    assert values[0].record['text'] == 'captured'

    requests = []

    def missing_body(inputs, selected_project, versions):
        assert selected_project == project
        assert versions == [captured['version_id']]
        requests.append(list(versions))
        return ()

    monkeypatch.setattr(ProjectionInputs, 'snapshot_versions', missing_body)
    with pytest.raises(RuntimeError, match='captured projection version disappeared'):
        list(CapturedProjectionInputs(repo, rebuild['claim'], batch_size=1))
    assert requests == [[captured['version_id']]]
    assert repo.get_record(project, 'a')['version_id'] == current['version_id']
    assert repo.projections.receipts.count(project, rebuild['state']['index_version']) == 0
    assert repo.projections.cursor(project, 'milvus')['active_destination'] is None


@pytest.mark.parametrize('mismatch', ['version_id', 'fingerprint'])
def test_receipt_validation_rejects_a_different_version_or_body_with_the_same_record_id(mismatch):
    row = projection_row(1)
    expected = {'r1': {'version_id': row['version_id'], 'fingerprint': projection_fingerprint(row)}}
    if mismatch == 'version_id':
        row['version_id'] = 'new-current-version'
    else:
        row['text'] = 'changed body'
    store = MilvusStore(dim=1, encoder_identity='test')
    store.check_collection = lambda **kwargs: store
    store._query_pages = lambda *args: iter([row])
    result = MilvusProjectionAdapter(store).validate(
        'project', {'index_version': 2}, default_catalog(), expected=expected)
    assert result == {'ok': False, 'count': 1, 'expected_count': 1}


def test_build_activation_rechecks_project_watermark_before_publishing_the_generation(tmp_path):
    repo = Repository(tmp_path / 'audit-watermark')
    project = repo.create_project('audit watermark')['id']
    repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': 'captured'})
    rebuild = capture_build(repo, project)
    captured_sequence = rebuild['snapshot_seq']
    repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': 'concurrent change'})
    before = repo.projections.cursor(project, 'milvus')
    with pytest.raises(ProjectionGap, match='activation is stale'):
        with repo.projections.activation_guard(project, rebuild['claim'], 3) as watermark:
            assert watermark > captured_sequence
            repo.projections.activate_index(rebuild['claim'], 3, captured_sequence, rebuild['state'])
    assert repo.projections.cursor(project, 'milvus') == before
    assert before['active_destination'] is None
    assert repo.projections.receipts.count(project, rebuild['state']['index_version']) == 0
