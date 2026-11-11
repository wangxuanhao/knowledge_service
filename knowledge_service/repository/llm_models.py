"""Persistence for platform model profiles and their single atomic selection."""
import json
from uuid import uuid4


class LLMModelStore:
    def __init__(self, repository):
        self.repository = repository
        self._db = repository._db
        with repository._transaction():
            self._db.execute('''INSERT INTO llm_model_settings(singleton) VALUES (true)
                ON CONFLICT(singleton) DO NOTHING''')

    def list(self):
        with self.repository._lock:
            return [dict(row) for row in self._db.execute('''
                SELECT m.*, (m.id = s.active_id) IS TRUE AS is_active
                FROM llm_models m CROSS JOIN llm_model_settings s
                ORDER BY m.created_at, m.id''').fetchall()]

    def get(self, profile_id):
        with self.repository._lock:
            row = self._db.execute('''SELECT m.*, (m.id=s.active_id) IS TRUE AS is_active
                FROM llm_models m CROSS JOIN llm_model_settings s WHERE m.id=?''',
                (profile_id,)).fetchone()
            return dict(row) if row else None

    def active(self):
        with self.repository._lock:
            row = self._db.execute('''SELECT m.* FROM llm_models m
                JOIN llm_model_settings s ON s.active_id=m.id''').fetchone()
            return dict(row) if row else None

    def create(self, *, name, base_url, model, api_key_encrypted):
        ident = str(uuid4())
        with self.repository._transaction():
            self._db.execute('''INSERT INTO llm_models
                (id,name,base_url,model,api_key_encrypted) VALUES (?,?,?,?,?)''',
                (ident, name, base_url, model, api_key_encrypted))
        return self.get(ident)

    def update(self, ident, *, name=None, base_url=None, model=None, api_key_encrypted=None):
        with self.repository._transaction():
            row = self._db.execute('SELECT id FROM llm_models WHERE id=? FOR UPDATE',
                                   (ident,)).fetchone()
            if not row:
                raise KeyError(ident)
            values = {'name': name, 'base_url': base_url, 'model': model,
                      'api_key_encrypted': api_key_encrypted}
            fields = [(key, value) for key, value in values.items() if value is not None]
            if fields:
                self._db.execute('UPDATE llm_models SET ' +
                    ', '.join(key + '=?' for key, _ in fields) + ', updated_at=now() WHERE id=?',
                    tuple(value for _, value in fields) + (ident,))
        return self.get(ident)

    def activate(self, ident):
        with self.repository._transaction():
            self._db.execute('SELECT singleton FROM llm_model_settings FOR UPDATE').fetchone()
            if not self.get(ident):
                raise KeyError(ident)
            self._db.execute('UPDATE llm_model_settings SET active_id=?', (ident,))
        return self.get(ident)

    def snapshot(self, task_id):
        with self.repository._lock:
            row = self._db.execute('SELECT sealed_config FROM llm_task_snapshots WHERE task_id=?',
                                   (task_id,)).fetchone()
            return row['sealed_config'] if row else None

    def pin(self, task_id, sealed_config, identity):
        with self.repository._transaction():
            self._db.execute('''INSERT INTO llm_task_snapshots(task_id,sealed_config,identity)
                VALUES (?,?,?) ON CONFLICT(task_id) DO NOTHING''',
                (task_id, sealed_config, json.dumps(identity, ensure_ascii=False)))
            return self.snapshot(task_id)
