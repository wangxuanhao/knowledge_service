"""One bounded authority collector for projection writes, builds and audits.

Every dependency for a batch is selected in one PostgreSQL statement, including
when called within a READ COMMITTED business transaction. Streaming builds use
one independent repeatable read view across all keyset batches. Payload numbers
are parsed from PostgreSQL text as Decimal before any native type conversion.
"""
from __future__ import annotations

from ..domain.search_filter_catalog import parse_filter_json
from ..domain.search_projection import ProjectionInput


_ENVELOPE = ('project_id', 'id', 'version', 'version_id', 'recorded_at', 'superseded_at')
MAX_BATCH_SIZE = 4096


def _bound(value, name):
    if type(value) is not int or not 1 <= value <= MAX_BATCH_SIZE:
        raise ValueError(f'{name} 必须为 1 到 {MAX_BATCH_SIZE} 的整数')
    return value


def _selection(values, limit):
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or any(
            not isinstance(value, str) or not value for value in values):
        raise ValueError('投影选择必须为非空 ID 的列表')
    # Duplicates are one selected identity; first occurrence defines order.
    selected = list(dict.fromkeys(values))
    if len(selected) > limit:
        raise ValueError('投影选择超过有界批次容量')
    return selected


def _statement(where):
    return f"""SELECT r.*, EXISTS (SELECT 1 FROM ontologies o
             WHERE o.project_id=r.project_id
               AND o.id=r.payload::jsonb->>'ontology_id') AS ontology_known
        FROM record_versions r WHERE r.project_id=? AND {where}
        ORDER BY r.id,r.version_id LIMIT ?"""


def _record(row, prefix=''):
    if prefix and row[prefix + 'version_id'] is None:
        return None
    payload = row[prefix + 'payload']
    if not isinstance(payload, str):
        raise ValueError('投影收集必须读取 PostgreSQL 原始 payload 文本')
    value = parse_filter_json(payload)
    if not isinstance(value, dict):
        raise ValueError('投影 payload 必须为对象')
    return {**value, **{key: row[prefix + key] for key in _ENVELOPE}}


def _input(row):
    return ProjectionInput(record=_record(row), ontology_known=row['ontology_known'])


class ProjectionInputs:
    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _read(reader, project_id, where, params, limit):
        with reader._lock:
            rows = reader._db.execute(_statement(where), [project_id, *params, limit]).fetchall()
        return tuple(_input(row) for row in rows)

    def collect(self, project_id, record_ids=None, *, version_ids=None, limit=256):
        """Collect exact versions or current IDs, in requested first-ID order.

        Missing identities are omitted. With no selection, return current rows
        in ID order, raising if the bound would truncate them. Use ``batches``
        for a project scan. A selection exceeding the bound always raises.
        """
        _bound(limit, 'limit')
        if record_ids is not None and version_ids is not None:
            raise ValueError('record_ids 与 version_ids 不能同时选择')
        records, versions = _selection(record_ids, limit), _selection(version_ids, limit)
        selected = versions if versions is not None else records
        if selected == []:
            return ()
        where, params = "r.superseded_at IS NULL AND r.read_visible AND r.read_kind IN ('entity','relation','chunk')", []
        if versions is not None:
            where += ' AND r.version_id=ANY(?::text[])'
            params = [versions]
        elif records is not None:
            where += ' AND r.id=ANY(?::text[])'
            params = [records]
        with self.repository.independent_read_view() as reader:
            values = self._read(reader, project_id, where, params, limit + 1)
        if len(values) > limit:
            raise ValueError('投影读取超过有界批次容量；使用 batches')
        if selected is None:
            return values
        key = 'version_id' if versions is not None else 'id'
        by_id = {value.record[key]: value for value in values}
        return tuple(by_id[identity] for identity in selected if identity in by_id)

    def snapshot_versions(self, project_id, version_ids):
        """Hydrate an existing captured receipt, preserving its original body."""
        selected = _selection(version_ids, MAX_BATCH_SIZE)
        if not selected:
            return ()
        with self.repository.independent_read_view() as reader:
            values = self._read(reader, project_id, 'r.version_id=ANY(?::text[])', [selected], len(selected))
        # Receipt versions were current when captured. Catchup events remove
        # versions superseded after that snapshot before validation/activation.
        return tuple(ProjectionInput({**value.record, 'superseded_at': None}, value.ontology_known) for value in values)

    def batches(self, project_id, *, batch_size=64):
        """Yield bounded current-row tuples in one consistent project snapshot.

        Business transactions reuse their writer connection for read-own-writes
        and may be READ COMMITTED. They cannot host a project stream; callers
        must use bounded ``collect`` there or stream from an independent view.
        """
        _bound(batch_size, 'batch_size')
        transaction = getattr(self.repository, '_transaction_state', None)
        if (getattr(transaction, 'depth', 0) and
                not getattr(self.repository, '_is_read_view', False)):
            raise RuntimeError('projection batches cannot run inside a business transaction; '
                               'use collect or an independent read view')
        after = None
        with self.repository.independent_read_view() as reader:
            while True:
                where, params = "r.superseded_at IS NULL AND r.read_visible AND r.read_kind IN ('entity','relation','chunk')", []
                if after is not None:
                    where += ' AND r.id>?'
                    params.append(after)
                values = self._read(reader, project_id, where, params, batch_size)
                if not values:
                    return
                yield values
                after = values[-1].record['id']


__all__ = ['MAX_BATCH_SIZE', 'ProjectionInputs']
