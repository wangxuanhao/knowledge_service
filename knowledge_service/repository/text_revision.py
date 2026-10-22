"""Transaction-owned authority for a narrowly qualified entity text revision.

The integrity probe returns one boolean. Its database work can scale with the
project's edges; unrelated records are never transferred or decoded in Python.
"""
from __future__ import annotations

from threading import get_ident


def _live(alias):
    # Match Python truthiness of metadata._deleted, including legacy JSON values.
    # read_visible also excludes audit records, which write validation must retain.
    return (f"COALESCE({alias}.payload::jsonb#>'{{metadata,_deleted}}','null'::jsonb) "
            "IN ('null'::jsonb,'false'::jsonb,'0'::jsonb,'\"\"'::jsonb,"
            "'[]'::jsonb,'{}'::jsonb)")


class EntityTextRevision:
    def __init__(self, repository, project_id, record_id, expected_version):
        self.repository = repository
        self.project_id = project_id
        self.record_id = record_id
        self.expected_version = expected_version
        self._owner = get_ident()
        self._connection = repository._db
        self._depth = repository._transaction_state.depth
        self._active = True
        self._snapshot = None
        self._candidate = None
        self._assertion_id = None
        repository.get_project(project_id)
        row = repository._db.execute(
            '''SELECT * FROM record_versions
               WHERE project_id=? AND id=? AND superseded_at IS NULL''',
            (project_id, record_id)).fetchone()
        self.record = repository._record(row) if row is not None else None
        if self.record is not None and self.record['version'] != expected_version:
            raise ValueError('版本冲突：记录已变更')

    def close(self):
        self._active = False

    def _check_owner(self):
        repository = self.repository
        if (not self._active or get_ident() != self._owner
                or repository._db is not self._connection
                or not repository._db.in_transaction
                or repository._transaction_state.depth != self._depth):
            raise ValueError('正式写入权威会话已失效')

    def requires_full_validation(self, ontology_id):
        """Conservatively certify the unchanged endpoint and constraint baseline."""
        self._check_owner()
        # type() checks exact JSON presence in one parse: missing/JSON null are
        # absent, while even false, empty strings and empty arrays are present.
        # Do not use native millisecond columns to certify this temporal gate.
        time_path = '$ ? (@.valid_from.type() != "null" || @.valid_until.type() != "null")'
        sql = f'''
            WITH facts AS MATERIALIZED (
                SELECT r.read_kind AS kind,r.read_subject_id AS subject_id,
                       r.read_object_id AS object_id,
                       (jsonb_path_exists(r.payload::jsonb,'{time_path}')
                        OR ((r.read_subject_id=? OR (
                                r.read_kind='relation' AND r.read_object_id=?))
                            AND r.payload::jsonb->>'ontology_id' IS DISTINCT FROM ?)
                        OR (r.read_kind='attribute'
                            AND r.payload::jsonb->>'ontology_id'=?)) AS full_check
                FROM record_versions r
                WHERE r.project_id=? AND r.superseded_at IS NULL
                  AND r.read_kind IN ('relation','attribute')
                  AND CASE WHEN r.read_visible THEN TRUE ELSE ({_live('r')}) END
            )
            SELECT EXISTS (
                SELECT 1 FROM facts WHERE full_check
                UNION ALL
                SELECT 1 FROM (
                    (SELECT subject_id AS id FROM facts
                     UNION
                     SELECT object_id FROM facts WHERE kind='relation')
                    EXCEPT
                    SELECT e.id FROM record_versions e
                    WHERE e.project_id=? AND e.superseded_at IS NULL
                      AND e.read_kind='entity'
                      AND CASE WHEN e.read_visible THEN TRUE ELSE ({_live('e')}) END
                      AND NOT jsonb_path_exists(e.payload::jsonb,'{time_path}')
                ) missing
            )'''
        # Keep NULL endpoints in the set difference: they certify a bad fact.
        # read_visible implies _live, but audit rows require the JSON fallback.
        return bool(self.repository._db.execute(
            sql, (self.record_id, self.record_id, ontology_id, ontology_id,
                  self.project_id, self.project_id)).fetchone()[0])

    def snapshot(self, candidate, assertion_id, latest_ontology):
        self._check_owner()
        repository = self.repository
        # The surrounding table locks protect both this point read and its
        # absence predicate against every assertion/ontology/record writer.
        try:
            assertion = repository.get_assertion(self.project_id, assertion_id)
        except KeyError:
            assertion = None
        events, mappings = [], []
        if assertion is not None:
            events = [dict(row) for row in repository._db.execute(
                '''SELECT * FROM assertion_events WHERE project_id=?
                   AND assertion_id=? AND decision_version=?''',
                (self.project_id, assertion_id,
                 assertion['decision_version'])).fetchall()]
            mappings = [dict(row) for row in repository._db.execute(
                '''SELECT * FROM record_version_assertions
                   WHERE project_id=? AND assertion_id=?
                     AND assertion_event_id=ANY(?::text[])''',
                (self.project_id, assertion_id,
                 [row['id'] for row in events])).fetchall()]
        snapshot = {
            'project_id': self.project_id,
            'storage_namespace': repository.storage_namespace,
            'ontology_id': latest_ontology['id'], 'ontology': latest_ontology,
            'records': [self.record],
            'assertions': [assertion] if assertion is not None else [],
            'assertion_events': events, 'record_version_assertions': mappings,
            # Entity writes never inspect or mutate relation/attribute keys.
            'fact_keys': [], 'resolution_reviews': [], 'merge_operations': [],
        }
        snapshot['fingerprint'] = repository._publish_fingerprint(snapshot)
        self._snapshot = snapshot
        self._candidate = candidate
        self._assertion_id = assertion_id
        return snapshot

    def execute(self, plan):
        self._check_owner()
        repository = self.repository
        repository._validate_formal_write_plan(plan)
        actions = plan['actions']
        puts = [item for item in actions if item['kind'] == 'record_put']
        accepts = [item for item in actions if item['kind'] == 'assertion_accept']
        if (self._snapshot is None or plan['project_id'] != self.project_id
                or plan['operation'] != 'manual_write'
                or plan['recorded_at'] is not None
                or plan['operation_context'] is not None
                or plan['review_items']
                or len(actions) != 2 or len(puts) != 1 or len(accepts) != 1
                or puts[0]['record'] != self._candidate
                or puts[0]['expected_version'] != self.expected_version
                or accepts[0]['assertion']['id'] != self._assertion_id
                or accepts[0]['canonical_record_id'] != self.record_id
                or plan['dependencies'] != repository._formal_write_dependencies(
                    self._snapshot)):
            raise ValueError('正式写入计划超出文字修订权威范围')
        result = repository._execute_formal_write_actions(plan)
        self.close()
        return result
