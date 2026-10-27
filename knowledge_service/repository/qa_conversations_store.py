"""Account-and-project-scoped QA display snapshots with optimistic writes."""
import json
from uuid import uuid4

from ..core.time import utc_now
from .core import _json


_SUMMARY = ('id,title,version,created_at,updated_at,'
            'json_array_length(messages::json) AS message_count')


class QaConversationsStore:
    def __init__(self, repo):
        self.repo = repo
        self._db = repo._db

    @staticmethod
    def _detail(row):
        messages = json.loads(row['messages'])
        return {**{key: row[key] for key in (
            'id', 'title', 'version', 'created_at', 'updated_at')},
            'message_count': len(messages), 'messages': messages}

    def _owned_row(self, owner_id, project_id, conversation_id, *, for_update=False):
        row = self._db.execute(
            'SELECT * FROM qa_conversations WHERE owner_id=? AND project_id=? AND id=?'
            + (' FOR UPDATE' if for_update else ''),
            (owner_id, project_id, conversation_id)).fetchone()
        if row is None:
            raise KeyError(conversation_id)
        return row

    def list(self, owner_id, project_id, *, limit, offset, q):
        with self.repo._transaction(repeatable_read_only=True):
            self.repo.get_project(project_id)
            where = 'owner_id=? AND project_id=?'
            params = [owner_id, project_id]
            if q:
                where += ' AND strpos(lower(title),lower(?))>0'
                params.append(q)
            total = self._db.execute(
                f'SELECT COUNT(*) AS total FROM qa_conversations WHERE {where}', params).fetchone()['total']
            rows = self._db.execute(
                f'SELECT {_SUMMARY} FROM qa_conversations WHERE {where}'
                ' ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?',
                [*params, limit, offset]).fetchall()
            return {'conversations': [dict(row) for row in rows], 'total': total,
                    'has_more': offset + len(rows) < total}

    def create(self, owner_id, project_id, *, title, title_auto):
        conversation_id, now = 'qc_' + uuid4().hex, utc_now()
        with self.repo._transaction():
            self.repo.get_project(project_id)
            self._db.execute(
                '''INSERT INTO qa_conversations
                   (id,owner_id,project_id,title,title_auto,messages,version,created_at,updated_at)
                   VALUES (?,?,?,?,?,'[]',1,?,?)''',
                (conversation_id, owner_id, project_id, title, title_auto, now, now))
            return self._detail(self._owned_row(owner_id, project_id, conversation_id))

    def get(self, owner_id, project_id, conversation_id):
        with self.repo._lock:
            return self._detail(self._owned_row(owner_id, project_id, conversation_id))

    def update(self, owner_id, project_id, conversation_id, *, expected_version,
               messages=None, auto_title=None, title=None):
        with self.repo._transaction():
            current = self._owned_row(owner_id, project_id, conversation_id, for_update=True)
            if current['version'] != expected_version:
                raise ValueError('版本冲突：会话已在其他窗口更新，请重新载入')
            new_title = title if title is not None else (
                auto_title if current['title_auto'] and auto_title else current['title'])
            cursor = self._db.execute(
                '''UPDATE qa_conversations
                   SET title=?,title_auto=?,messages=?,version=version+1,updated_at=?
                   WHERE owner_id=? AND project_id=? AND id=? AND version=?''',
                (new_title, False if title is not None else current['title_auto'],
                 _json(messages) if messages is not None else current['messages'], utc_now(),
                 owner_id, project_id, conversation_id, expected_version))
            if cursor.rowcount != 1:
                raise ValueError('版本冲突：会话已在其他窗口更新，请重新载入')
            return self._detail(self._owned_row(owner_id, project_id, conversation_id))

    def delete(self, owner_id, project_id, conversation_id, *, expected_version):
        with self.repo._transaction():
            current = self._owned_row(owner_id, project_id, conversation_id, for_update=True)
            if current['version'] != expected_version:
                raise ValueError('版本冲突：会话已在其他窗口更新，请重新载入')
            cursor = self._db.execute(
                'DELETE FROM qa_conversations WHERE owner_id=? AND project_id=? AND id=? AND version=?',
                (owner_id, project_id, conversation_id, expected_version))
            if cursor.rowcount != 1:
                raise ValueError('版本冲突：会话已在其他窗口更新，请重新载入')
