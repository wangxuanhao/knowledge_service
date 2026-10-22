"""Durable, owner-bound complete graph manifests over authoritative scoped reads."""
from __future__ import annotations

import base64
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
from functools import wraps
from uuid import uuid4

from ..core.time import utc_now
from ..repository.scoped_reads import ScopedReads


def _independent_page(operation):
    @wraps(operation)
    def read(self, *args, **kwargs):
        factory = getattr(self.repo, 'independent_read_view', None)
        with factory() if factory is not None else nullcontext(self.repo) as repository:
            sessions = self if repository is self.repo else GraphSessions(repository)
            return operation(sessions, *args, **kwargs)
    return read


class GraphSessions:
    """A manifest fixes scope and data; every page rechecks current eligibility.

    No database transaction is kept open across HTTP requests. A project change
    invalidates the manifest, so a withdrawal cannot leak through an old cursor.
    """
    def __init__(self, repository):
        self.repo = repository
        self.reads = ScopedReads(repository)

    def create(self, project, scope, *, actor_id=None, ttl_seconds=3600):
        scope = json.loads(json.dumps(scope, ensure_ascii=False, allow_nan=False))
        scope.pop('kinds', None)
        # A current session is frozen by the checked project watermark. Keeping
        # current reads lets PostgreSQL use its partial current-version indexes;
        # explicit historical requests retain their requested knowledge time.
        with self.repo.read_snapshot():
            self.repo.get_project(project)
            watermark = self.repo.projections.project_watermark(project)
            entities = self.reads.count(project, scope, kinds=['entity'])
            relations = self.reads.count(project, scope, kinds=['relation'])
        session = {
            'id': str(uuid4()), 'project_id': project, 'scope': scope,
            'entity_count': entities, 'relation_count': relations,
            'watermark': watermark, 'layout_version': 'stable-id-v1',
            'created_at': utc_now(),
            'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat(),
            'owner_id': actor_id, 'cursor_secret': secrets.token_hex(32),
        }
        self.repo.save_artifact('graph-session', session)
        return self._public(session)

    @staticmethod
    def _public(session):
        return {key: value for key, value in session.items() if key not in {'owner_id', 'cursor_secret'}}

    @staticmethod
    def _cursor(session, kind, value):
        body = json.dumps([session['id'], kind, value], separators=(',', ':')).encode()
        signature = hmac.new(session['cursor_secret'].encode(), body, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(signature + body).decode()

    @staticmethod
    def _decode(session, kind, cursor):
        if cursor is None:
            return None
        try:
            if not isinstance(cursor, str) or len(cursor) > 10000:
                raise ValueError()
            encoded = base64.urlsafe_b64decode(cursor)
            signature, body = encoded[:32], encoded[32:]
            expected = hmac.new(session['cursor_secret'].encode(), body, hashlib.sha256).digest()
            values = json.loads(body)
            if not hmac.compare_digest(signature, expected) or values[:2] != [session['id'], kind]:
                raise ValueError()
            return values[2]
        except (ValueError, TypeError, UnicodeError, IndexError) as exc:
            raise ValueError('无效、被修改或不属于本会话的图谱游标') from exc

    @staticmethod
    def _compact(record):
        item = {key: record.get(key) for key in ('id', 'version', 'version_id', 'kind', 'type', 'subject_id', 'object_id')}
        item['text'] = str(record.get('text') or '')[:160]
        return item

    @_independent_page
    def page(self, project, session_id, kind, *, cursor=None, actor_id=None, limit=400):
        if kind not in {'entities', 'relations', 'connected'}:
            raise ValueError('图谱分页类型必须为 entities、relations 或 connected')
        maximum = 500 if kind == 'connected' else 10000
        if type(limit) is not int or not 1 <= limit <= maximum:
            raise ValueError(f'图谱分页上限必须在 1 到 {maximum} 之间')
        session = self.repo.get_artifact('graph-session', session_id)
        if session['project_id'] != project or session.get('owner_id') != actor_id:
            raise PermissionError('不能读取其他账号或项目的图谱会话')
        if datetime.fromisoformat(session['expires_at'].replace('Z', '+00:00')) <= datetime.now(timezone.utc):
            raise ValueError('全量图会话已过期，请重新加载')
        after = self._decode(session, kind, cursor)
        with self.repo.read_snapshot():
            self.repo.get_project(project)
            if self.repo.projections.project_watermark(project) != session['watermark']:
                raise ValueError('项目知识或来源已发生变化，请重新加载全量图以重新核对资格')
            result = self.reads.page(project, session['scope'], kinds=['entity' if kind == 'entities' else 'relation'],
                                     after=after, limit=limit, include_total=False, compact_graph=True)
            entities = []
            if kind == 'connected':
                # Endpoint eligibility is checked with the frozen session scope
                # in this same read snapshot, never through an unscoped lookup.
                endpoint_ids = sorted({record.get(key) for record in result['records']
                                       for key in ('subject_id', 'object_id') if record.get(key)})
                entities = self.reads.by_ids(project, session['scope'], endpoint_ids, kinds=['entity'])
                if {record['id'] for record in entities} != set(endpoint_ids) or any(
                        not record.get('subject_id') or not record.get('object_id') for record in result['records']):
                    raise ValueError('关系端点资格与图谱清单不一致，请重新开始加载')
        records = [self._compact(record) for record in result['records']]
        next_cursor = self._cursor(session, kind, result['next_cursor']) if result['next_cursor'] else None
        page = {'session_id': session_id, 'kind': kind, 'records': records,
                'next_cursor': next_cursor, 'complete': next_cursor is None,
                'total': session['entity_count' if kind == 'entities' else 'relation_count'],
                'watermark': session['watermark'], 'layout_version': session['layout_version']}
        if kind == 'connected':
            page['entities'] = [self._compact(record) for record in entities]
        return page
