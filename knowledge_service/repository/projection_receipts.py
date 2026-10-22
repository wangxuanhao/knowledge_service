"""Bounded, generation-scoped PostgreSQL receipts for confirmed Milvus rows."""
from __future__ import annotations

from collections.abc import Mapping

from ..domain.search_projection import projection_fingerprint


class ReceiptManifest(Mapping):
    """A read-only receipt view; production validation reads bounded ID pages."""

    def __init__(self, receipts, project_id, index_version):
        self.receipts = receipts
        self.project_id, self.index_version = project_id, index_version

    def lookup(self, record_ids):
        return self.receipts.for_record_ids(self.project_id, self.index_version, record_ids)

    def __len__(self):
        return self.receipts.count(self.project_id, self.index_version)

    def __iter__(self):
        for page in self.receipts.pages(self.project_id, self.index_version):
            yield from page

    def __getitem__(self, record_id):
        values = self.lookup([record_id])
        if record_id not in values:
            raise KeyError(record_id)
        return values[record_id]


class ProjectionReceipts:
    def __init__(self, projections):
        self.projections = projections
        self.repository = projections.repository
        self._db = projections._db

    @staticmethod
    def _scope(project_id, index_version):
        if not isinstance(project_id, str) or not project_id:
            raise ValueError('receipt project is required')
        if type(index_version) is not int or not 1 <= index_version < 2**63:
            raise ValueError('receipt generation must be positive INT64')

    def _claimed_scope(self, claimed, cursor=None):
        from .projection_store import ProjectionLeaseLost
        if not self._db.in_transaction:
            raise RuntimeError('receipt writes require the claim transaction')
        cursor = self.projections._locked_claim(claimed) if cursor is None else cursor
        generation = (cursor.get('rebuild_state') or {}).get('index_version')
        if (claimed.target != 'milvus' or generation != claimed.index_version
                or int(cursor['schema_version']) != claimed.schema_version):
            raise ProjectionLeaseLost('receipt generation or schema changed')
        self._scope(claimed.project_id, generation)
        return generation

    def apply_events(self, claimed, events, *, cursor=None):
        generation = self._claimed_scope(claimed, cursor)
        for event in events:
            envelope = (event.get('payload') or {}).get('_milvus_projection')
            if envelope is None or envelope.get('index_version') != generation:
                raise ValueError('receipt event is missing its frozen generation')
            if event['operation'] == 'tombstone':
                self._db.execute('DELETE FROM milvus_projection_receipts '
                    'WHERE project_id=? AND index_version=? AND record_id=?',
                    (claimed.project_id, generation, envelope['record_id']))
            elif envelope.get('row') is not None:
                self._upsert(claimed.project_id, generation, [envelope['row']])

    def _upsert(self, project_id, index_version, rows):
        values = []
        for row in rows:
            if row['project_id'] != project_id or row['index_version'] != index_version:
                raise ValueError('receipt row does not match the claim scope')
            values.append((project_id, index_version, row['record_id'], row['version_id'],
                           projection_fingerprint(row)))
        if values:
            self._db.executemany('''INSERT INTO milvus_projection_receipts
                (project_id,index_version,record_id,version_id,fingerprint) VALUES (?,?,?,?,?)
                ON CONFLICT(project_id,index_version,record_id) DO UPDATE SET
                version_id=excluded.version_id,fingerprint=excluded.fingerprint''', values)

    def replace_page(self, claimed, rows):
        if not isinstance(rows, (list, tuple)) or len(rows) > 512:
            raise ValueError('receipt page must contain at most 512 rows')
        with self.repository._transaction():
            generation = self._claimed_scope(claimed)
            self._upsert(claimed.project_id, generation, rows)

    def reset(self, claimed):
        with self.repository._transaction():
            generation = self._claimed_scope(claimed)
            self._db.execute('DELETE FROM milvus_projection_receipts '
                'WHERE project_id=? AND index_version=?', (claimed.project_id, generation))

    def for_record_ids(self, project_id, index_version, record_ids):
        self._scope(project_id, index_version)
        if (not isinstance(record_ids, (list, tuple)) or len(record_ids) > 1000
                or any(not isinstance(value, str) or not value for value in record_ids)):
            raise ValueError('receipt lookup requires at most 1000 nonempty record IDs')
        if not record_ids:
            return {}
        with self.repository._lock:
            rows = self._db.execute('''SELECT record_id,version_id,fingerprint
                FROM milvus_projection_receipts WHERE project_id=? AND index_version=?
                AND record_id=ANY(?::text[])''', (project_id, index_version, list(record_ids))).fetchall()
        return {row['record_id']: {'version_id': row['version_id'], 'fingerprint': row['fingerprint']}
                for row in rows}

    def count(self, project_id, index_version):
        self._scope(project_id, index_version)
        with self.repository._lock:
            return int(self._db.execute('SELECT count(*) AS receipt_count '
                'FROM milvus_projection_receipts WHERE project_id=? AND index_version=?',
                (project_id, index_version)).fetchone()['receipt_count'])

    def pages(self, project_id, index_version, *, batch_size=512):
        self._scope(project_id, index_version)
        if type(batch_size) is not int or not 1 <= batch_size <= 512:
            raise ValueError('receipt page size must be 1..512')
        with self.repository.independent_read_view() as reader:
            after = ''
            while True:
                rows = reader._db.execute('''SELECT record_id,version_id,fingerprint
                    FROM milvus_projection_receipts WHERE project_id=? AND index_version=?
                    AND record_id>? ORDER BY record_id LIMIT ?''',
                    (project_id, index_version, after, batch_size)).fetchall()
                if not rows:
                    return
                yield {row['record_id']: {'version_id': row['version_id'], 'fingerprint': row['fingerprint']}
                       for row in rows}
                after = rows[-1]['record_id']

    def manifest(self, project_id, index_version):
        self._scope(project_id, index_version)
        return ReceiptManifest(self, project_id, index_version)
