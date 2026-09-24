"""Durable storage for governed ontology drafts and their immutable audit history."""
import json
from uuid import uuid4

from ..core.time import utc_now


SOURCE_KINDS = {'manual', 'turtle', 'import', 'ai', 'discovery', 'candidate'}
DRAFT_STATUSES = {
    'editing', 'submitted', 'reviewed', 'published', 'closed',
    'stale_base', 'stale_source',
}
DECISION_ACTIONS = {'approve', 'reject', 'request_changes'}


class OntologyDraftConflict(ValueError):
    """A draft changed after the caller read its revision."""

    def __init__(self, draft_id, expected_revision):
        self.draft_id = draft_id
        self.expected_revision = expected_revision
        super().__init__(
            f'版本冲突：ontology draft {draft_id} is no longer at revision '
            f'{expected_revision}')


def _canonical_json(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(',', ':'))
    except (TypeError, ValueError) as exc:
        raise ValueError('本体草案载荷必须只含有限 JSON 值') from exc


def _required_text(item, key):
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f'本体草案记录缺少 {key}')
    return value


class OntologyDraftStore:
    """Repository-composed store sharing its connection, lock and transactions."""

    _DRAFT_JSON = {'source_context', 'validation_report'}
    _OPERATION_JSON = {'before', 'after', 'evidence', 'impact', 'validation'}

    def __init__(self, repo):
        self.repo = repo
        self._db = repo._db
        self._lock = repo._lock

    def _validate_ontology_reference(self, project_id, ontology_id, field):
        if ontology_id is None:
            return
        if not isinstance(ontology_id, str) or not ontology_id:
            raise ValueError(f'{field} 必须是本体 ID 或 null')
        row = self._db.execute(
            'SELECT 1 FROM ontologies WHERE project_id=? AND id=?',
            (project_id, ontology_id)).fetchone()
        if row is None:
            raise ValueError(f'{field} 不属于此项目')

    @staticmethod
    def _draft(row):
        item = dict(row)
        item['source_context'] = json.loads(item['source_context'])
        item['validation_report'] = (
            json.loads(item['validation_report'])
            if item['validation_report'] is not None else None)
        return item

    @staticmethod
    def _operation(row):
        item = dict(row)
        item['before'] = json.loads(item.pop('before_json'))
        item['after'] = json.loads(item.pop('after_json'))
        item['evidence'] = json.loads(item.pop('evidence_json'))
        item['impact'] = json.loads(item.pop('impact_json'))
        item['validation'] = json.loads(item.pop('validation_json'))
        return item

    @staticmethod
    def _decision(row):
        return dict(row)

    @staticmethod
    def _publish_request(row):
        return dict(row)

    def create(self, project_id, item):
        if not isinstance(item, dict):
            raise ValueError('本体草案必须是对象')
        if item.get('project_id', project_id) != project_id:
            raise ValueError('本体草案不属于此项目')
        source_kind = item.get('source_kind')
        status = item.get('status', 'editing')
        revision = item.get('revision', 1)
        if source_kind not in SOURCE_KINDS:
            raise ValueError('不支持的本体草案来源')
        if status not in DRAFT_STATUSES:
            raise ValueError('不支持的本体草案状态')
        if type(revision) is not int or revision < 1:
            raise ValueError('本体草案 revision 必须为正整数')
        title = _required_text(item, 'title')
        summary = item.get('summary')
        if not isinstance(summary, str):
            raise ValueError('本体草案摘要必须是字符串')
        source_context = item.get('source_context', {})
        if not isinstance(source_context, dict):
            raise ValueError('本体草案来源上下文必须是对象')
        validation_report = item.get('validation_report')
        now = utc_now()
        draft = {
            'id': item.get('id') or str(uuid4()),
            'project_id': project_id,
            'base_ontology_id': item.get('base_ontology_id'),
            'source_kind': source_kind,
            'status': status,
            'revision': revision,
            'title': title,
            'summary': summary,
            'source_context': source_context,
            'validation_report': validation_report,
            'validation_fingerprint': item.get('validation_fingerprint'),
            'published_ontology_id': item.get('published_ontology_id'),
            'legacy_artifact_id': item.get('legacy_artifact_id'),
            'created_at': item.get('created_at') or now,
            'updated_at': item.get('updated_at') or now,
        }
        _required_text(draft, 'id')
        with self.repo._transaction():
            self.repo.get_project(project_id)
            self._validate_ontology_reference(
                project_id, draft['base_ontology_id'], 'base_ontology_id')
            self._validate_ontology_reference(
                project_id, draft['published_ontology_id'], 'published_ontology_id')
            self._db.execute(
                '''INSERT INTO ontology_drafts
                   (id,project_id,base_ontology_id,source_kind,status,revision,title,
                    summary,source_context,validation_report,validation_fingerprint,
                    published_ontology_id,legacy_artifact_id,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (draft['id'], project_id, draft['base_ontology_id'], source_kind,
                 status, revision, title, summary, _canonical_json(source_context),
                 (_canonical_json(validation_report)
                  if validation_report is not None else None),
                 draft['validation_fingerprint'], draft['published_ontology_id'],
                 draft['legacy_artifact_id'], draft['created_at'], draft['updated_at']))
        return self.get(project_id, draft['id'])

    def get(self, project_id, draft_id):
        self.repo.get_project(project_id)
        with self._lock:
            row = self._db.execute(
                'SELECT * FROM ontology_drafts WHERE project_id=? AND id=?',
                (project_id, draft_id)).fetchone()
        if row is None:
            raise KeyError(draft_id)
        return self._draft(row)

    def list(self, project_id, status=None):
        self.repo.get_project(project_id)
        if status is not None and status not in DRAFT_STATUSES:
            raise ValueError('不支持的本体草案状态')
        sql = 'SELECT * FROM ontology_drafts WHERE project_id=?'
        values = [project_id]
        if status is not None:
            sql += ' AND status=?'
            values.append(status)
        sql += ' ORDER BY created_at,id'
        with self._lock:
            return [self._draft(row) for row in self._db.execute(sql, values).fetchall()]

    def append_operations(self, project_id, draft_id, operations):
        if not isinstance(operations, (list, tuple)):
            raise ValueError('本体操作必须是列表')
        saved = []
        with self.repo._transaction():
            self.get(project_id, draft_id)
            for proposed in operations:
                if not isinstance(proposed, dict):
                    raise ValueError('本体操作必须是对象')
                operation = {
                    'id': proposed.get('id') or str(uuid4()),
                    'project_id': project_id,
                    'draft_id': draft_id,
                    'action': _required_text(proposed, 'action'),
                    'target_iri': _required_text(proposed, 'target_iri'),
                    'before': proposed.get('before'),
                    'after': proposed.get('after'),
                    'evidence': proposed.get('evidence', proposed.get('evidence_refs', [])),
                    'impact': proposed.get('impact', {}),
                    'validation': proposed.get('validation', {}),
                    'risk': _required_text(proposed, 'risk'),
                    'fingerprint': _required_text(proposed, 'fingerprint'),
                    'reason': proposed.get('reason'),
                    'supersedes_operation_id': proposed.get('supersedes_operation_id'),
                    'created_at': proposed.get('created_at') or utc_now(),
                }
                _required_text(operation, 'id')
                if operation['supersedes_operation_id'] == operation['id']:
                    raise ValueError('本体操作不能替代自身')
                self._db.execute(
                    '''INSERT INTO ontology_operations
                       (id,project_id,draft_id,action,target_iri,before_json,after_json,
                        evidence_json,impact_json,validation_json,risk,fingerprint,reason,
                        supersedes_operation_id,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (operation['id'], project_id, draft_id, operation['action'],
                     operation['target_iri'], _canonical_json(operation['before']),
                     _canonical_json(operation['after']),
                     _canonical_json(operation['evidence']),
                     _canonical_json(operation['impact']),
                     _canonical_json(operation['validation']), operation['risk'],
                     operation['fingerprint'], operation['reason'],
                     operation['supersedes_operation_id'], operation['created_at']))
                saved.append(self._operation(self._db.execute(
                    'SELECT * FROM ontology_operations WHERE id=?',
                    (operation['id'],)).fetchone()))
        return saved

    def compare_and_set(self, project_id, draft_id, expected_revision, changes):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('expected_revision 必须为正整数')
        if not isinstance(changes, dict):
            raise ValueError('本体草案更新必须是对象')
        allowed = {
            'base_ontology_id', 'source_kind', 'status', 'title', 'summary',
            'source_context', 'validation_report', 'validation_fingerprint',
            'published_ontology_id', 'legacy_artifact_id',
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f'不支持的本体草案更新字段：{sorted(unknown)}')
        if 'source_kind' in changes and changes['source_kind'] not in SOURCE_KINDS:
            raise ValueError('不支持的本体草案来源')
        if 'status' in changes and changes['status'] not in DRAFT_STATUSES:
            raise ValueError('不支持的本体草案状态')
        if 'title' in changes:
            _required_text(changes, 'title')
        if 'summary' in changes and not isinstance(changes['summary'], str):
            raise ValueError('本体草案摘要必须是字符串')
        if ('source_context' in changes
                and not isinstance(changes['source_context'], dict)):
            raise ValueError('本体草案来源上下文必须是对象')
        encoded = dict(changes)
        for key in self._DRAFT_JSON & set(encoded):
            value = encoded[key]
            encoded[key] = _canonical_json(value) if value is not None else None
        assignments = [f'{key}=?' for key in encoded]
        assignments.extend(('revision=revision+1', 'updated_at=?'))
        values = [*encoded.values(), utc_now(), project_id, draft_id, expected_revision]
        with self.repo._transaction():
            for field in ('base_ontology_id', 'published_ontology_id'):
                if field in changes:
                    self._validate_ontology_reference(
                        project_id, changes[field], field)
            cursor = self._db.execute(
                f'''UPDATE ontology_drafts SET {','.join(assignments)}
                    WHERE project_id=? AND id=? AND revision=?''', values)
            if cursor.rowcount != 1:
                exists = self._db.execute(
                    'SELECT 1 FROM ontology_drafts WHERE project_id=? AND id=?',
                    (project_id, draft_id)).fetchone()
                if exists is None:
                    raise KeyError(draft_id)
                raise OntologyDraftConflict(draft_id, expected_revision)
            row = self._db.execute(
                'SELECT * FROM ontology_drafts WHERE project_id=? AND id=?',
                (project_id, draft_id)).fetchone()
        return self._draft(row)

    def append_decisions(self, project_id, draft_id, decisions):
        if not isinstance(decisions, (list, tuple)):
            raise ValueError('本体审核决定必须是列表')
        saved = []
        with self.repo._transaction():
            self.get(project_id, draft_id)
            for proposed in decisions:
                if not isinstance(proposed, dict):
                    raise ValueError('本体审核决定必须是对象')
                action = proposed.get('action')
                if action not in DECISION_ACTIONS:
                    raise ValueError('不支持的本体审核决定')
                decision = {
                    'id': proposed.get('id') or str(uuid4()),
                    'operation_id': _required_text(proposed, 'operation_id'),
                    'operation_fingerprint': _required_text(
                        proposed, 'operation_fingerprint'),
                    'action': action,
                    'reason': proposed.get('reason'),
                    'actor': _required_text(proposed, 'actor'),
                    'supersedes_decision_id': proposed.get('supersedes_decision_id'),
                    'created_at': proposed.get('created_at') or utc_now(),
                }
                _required_text(decision, 'id')
                if decision['supersedes_decision_id'] == decision['id']:
                    raise ValueError('本体审核决定不能替代自身')
                self._db.execute(
                    '''INSERT INTO ontology_review_decisions
                       (id,project_id,draft_id,operation_id,operation_fingerprint,
                        action,reason,actor,supersedes_decision_id,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)''',
                    (decision['id'], project_id, draft_id, decision['operation_id'],
                     decision['operation_fingerprint'], action, decision['reason'],
                     decision['actor'], decision['supersedes_decision_id'],
                     decision['created_at']))
                saved.append(self._decision(self._db.execute(
                    'SELECT * FROM ontology_review_decisions WHERE id=?',
                    (decision['id'],)).fetchone()))
        return saved

    def effective_operations(self, project_id, draft_id):
        self.get(project_id, draft_id)
        with self._lock:
            rows = self._db.execute(
                '''SELECT operation.* FROM ontology_operations AS operation
                   WHERE operation.project_id=? AND operation.draft_id=?
                     AND NOT EXISTS (
                       SELECT 1 FROM ontology_operations AS replacement
                       WHERE replacement.project_id=operation.project_id
                         AND replacement.draft_id=operation.draft_id
                         AND replacement.supersedes_operation_id=operation.id)
                   ORDER BY operation.created_at,operation.rowid''',
                (project_id, draft_id)).fetchall()
        return [self._operation(row) for row in rows]

    def export(self, project_id):
        self.repo.get_project(project_id)
        with self._lock:
            drafts = [self._draft(row) for row in self._db.execute(
                '''SELECT * FROM ontology_drafts
                   WHERE project_id=? ORDER BY rowid''',
                (project_id,)).fetchall()]
            operations = [self._operation(row) for row in self._db.execute(
                '''SELECT * FROM ontology_operations
                   WHERE project_id=? ORDER BY rowid''',
                (project_id,)).fetchall()]
            decisions = [self._decision(row) for row in self._db.execute(
                '''SELECT * FROM ontology_review_decisions
                   WHERE project_id=? ORDER BY rowid''',
                (project_id,)).fetchall()]
            requests = [self._publish_request(row) for row in self._db.execute(
                '''SELECT * FROM ontology_publish_requests
                   WHERE project_id=? ORDER BY created_at,id''',
                (project_id,)).fetchall()]
        return {'drafts': drafts, 'operations': operations, 'decisions': decisions,
                'publish_requests': requests}

    def _restore(self, project_id, history):
        def dependency_order(items, dependency_key, kind):
            pending = list(items)
            pending_ids = {item['id'] for item in pending}
            emitted = set()
            ordered = []
            while pending:
                ready = [item for item in pending
                         if item.get(dependency_key) is None
                         or item.get(dependency_key) in emitted]
                if not ready:
                    missing = sorted({item.get(dependency_key) for item in pending}
                                     - pending_ids - emitted)
                    detail = f'缺少引用 {missing}' if missing else '存在循环引用'
                    raise ValueError(f'{kind} 恢复顺序无效：{detail}')
                for item in ready:
                    pending.remove(item)
                    ordered.append(item)
                    emitted.add(item['id'])
            return ordered

        for draft in history['drafts']:
            self.create(project_id, draft)
        for operation in dependency_order(
                history['operations'], 'supersedes_operation_id', '本体操作'):
            self.append_operations(project_id, operation['draft_id'], [operation])
        for decision in dependency_order(
                history['decisions'], 'supersedes_decision_id', '本体审核决定'):
            self.append_decisions(project_id, decision['draft_id'], [decision])
        for request in history['publish_requests']:
            self._db.execute(
                '''INSERT INTO ontology_publish_requests
                   (id,project_id,draft_id,idempotency_key,request_hash,
                    result_ontology_id,created_at,completed_at)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (request['id'], project_id, request['draft_id'],
                 request['idempotency_key'], request['request_hash'],
                 request.get('result_ontology_id'), request['created_at'],
                 request.get('completed_at')))

    def delete_counts(self, project_id):
        with self.repo._transaction():
            with self.repo._allow_ontology_history_delete():
                counts = {}
                for table in (
                        'ontology_publish_requests', 'ontology_review_decisions',
                        'ontology_operations', 'ontology_drafts'):
                    counts[table] = self._db.execute(
                        f'DELETE FROM {table} WHERE project_id=?',
                        (project_id,)).rowcount
                return counts
