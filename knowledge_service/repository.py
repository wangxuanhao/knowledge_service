"""Transactional SQLite truth store. A revision corrects a whole stable record.

Separate real-world facts/policies need distinct IDs; corrections never split the
old business interval. Historical system-time queries preserve the original.
"""
import json
import math
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .filters import matches_filter, validate_filter
from .time import normalize_time, utc_now


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError('Payload must contain finite JSON values') from exc


class OntologyNotPublished(Exception):
    """The project exists but has no ontology version yet (e.g. discovery/documents mode)."""
    def __init__(self, project_id):
        super().__init__(project_id)
        self.project_id = project_id


class Repository:
    def __init__(self, path):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('PRAGMA foreign_keys=ON')
        self._db.executescript('''
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS record_versions (
              project_id TEXT NOT NULL REFERENCES projects(id), id TEXT NOT NULL,
              version INTEGER NOT NULL, version_id TEXT NOT NULL UNIQUE,
              payload TEXT NOT NULL, recorded_at TEXT NOT NULL, superseded_at TEXT,
              PRIMARY KEY(project_id,id,version));
            CREATE INDEX IF NOT EXISTS versions_project ON record_versions(project_id,recorded_at,superseded_at);
            CREATE UNIQUE INDEX IF NOT EXISTS versions_current ON record_versions(project_id,id) WHERE superseded_at IS NULL;
            CREATE TABLE IF NOT EXISTS ontologies (
              id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
              turtle TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL,
              metadata TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS artifacts (
              id TEXT PRIMARY KEY, kind TEXT NOT NULL, project_id TEXT, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS service_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        ontology_columns = {row['name'] for row in self._db.execute('PRAGMA table_info(ontologies)').fetchall()}
        if 'metadata' not in ontology_columns:
            self._db.execute("ALTER TABLE ontologies ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
        with self._transaction():
            self._db.execute('INSERT OR IGNORE INTO service_settings VALUES (?,?)', ('storage_namespace', str(uuid4())))

    def export_projection(self, project_id):
        """Consistent local snapshot, including history; embeddings stay local."""
        with self._transaction():
            project = self.get_project(project_id)
            namespace = self._db.execute('SELECT value FROM service_settings WHERE key=?', ('storage_namespace',)).fetchone()[0]
            rows = self._db.execute('SELECT * FROM record_versions WHERE project_id=? ORDER BY id,version', (project_id,)).fetchall()
            records = [self._record(row) for row in rows]
            for record in records:
                record.pop('embedding', None)
            return {'namespace': namespace, 'project': project, 'records': records,
                    'ontologies': self.list_ontologies(project_id)}

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def close(self):
        with self._lock:
            self._db.close()

    def store_embeddings(self, project_id, rows, vectors, model):
        """Refresh derived vectors only; never create a business knowledge revision."""
        if len(rows) != len(vectors):
            raise ValueError('Embedding output count mismatch')
        saved = 0
        with self._transaction():
            self.get_project(project_id)
            for row, vector in zip(rows, vectors):
                self._validate_record({'kind':'chunk', 'text':'vector validation', 'embedding':vector})
                cursor = self._db.execute('''UPDATE record_versions
                    SET payload=json_set(payload,'$.embedding',json(?),'$.embedding_model',?)
                    WHERE project_id=? AND version_id=? AND superseded_at IS NULL''',
                    (_json(vector), model, project_id, row['version_id']))
                saved += cursor.rowcount
        return saved

    def create_project(self, name, metadata=None):
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Project name is required')
        metadata = {} if metadata is None else metadata
        if not isinstance(metadata, dict):
            raise ValueError('Project metadata must be an object')
        project = {'id': str(uuid4()), 'name': name, 'metadata': json.loads(_json(metadata)), 'created_at': utc_now()}
        with self._transaction():
            self._db.execute('INSERT INTO projects VALUES (?,?,?,?)', (project['id'], name, _json(metadata), project['created_at']))
        return project

    def get_project(self, project_id):
        with self._lock:
            row = self._db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        if row is None:
            raise KeyError(project_id)
        return {**dict(row), 'metadata': json.loads(row['metadata'])}

    def rename_project(self, project_id, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Project name is required')
        with self._transaction():
            self.get_project(project_id)
            self._db.execute('UPDATE projects SET name=? WHERE id=?', (name.strip(), project_id))
        return self.get_project(project_id)

    def delete_project(self, project_id):
        with self._transaction():
            self.get_project(project_id)
            r = self._db.execute('DELETE FROM record_versions WHERE project_id=?', (project_id,))
            records_deleted = r.rowcount
            o = self._db.execute('DELETE FROM ontologies WHERE project_id=?', (project_id,))
            ontologies_deleted = o.rowcount
            a = self._db.execute('DELETE FROM artifacts WHERE project_id=?', (project_id,))
            artifacts_deleted = a.rowcount
            self._db.execute('DELETE FROM projects WHERE id=?', (project_id,))
        return {'records': records_deleted, 'ontologies': ontologies_deleted, 'artifacts': artifacts_deleted}

    def list_projects(self):
        with self._lock:
            rows = self._db.execute('SELECT * FROM projects ORDER BY created_at,id').fetchall()
            counts_rows = self._db.execute(
                "SELECT project_id, json_extract(payload,'$.kind') AS kind, COUNT(*) AS n "
                "FROM record_versions WHERE superseded_at IS NULL GROUP BY project_id, kind"
            ).fetchall()
        counts_map = {}
        for cr in counts_rows:
            pid, kind, n = cr['project_id'], cr['kind'], cr['n']
            if pid not in counts_map:
                counts_map[pid] = {'documents': 0, 'entities': 0, 'relations': 0, 'chunks': 0}
            key = kind + 's' if kind != 'entity' else 'entities'
            if key in counts_map[pid]:
                counts_map[pid][key] = n
        return [{**dict(row), 'metadata': json.loads(row['metadata']),
                 'counts': counts_map.get(row['id'], {'documents': 0, 'entities': 0, 'relations': 0, 'chunks': 0})}
                for row in rows]

    def _validate_record(self, record):
        if not isinstance(record, dict):
            raise ValueError('Record must be an object')
        if set(record) & {'version', 'version_id', 'recorded_at', 'superseded_at', 'project_id'}:
            raise ValueError('System version fields cannot be supplied by clients')
        record = json.loads(_json(record))
        if record.get('kind') not in {'document', 'entity', 'relation', 'chunk'} or not isinstance(record.get('text'), str):
            raise ValueError('Record requires a supported kind and string text')
        record.setdefault('id', str(uuid4()))
        if not isinstance(record['id'], str) or not record['id']:
            raise ValueError('Record ID must be a non-empty string')
        for key in ('type', 'source_id', 'subject_id', 'object_id', 'embedding_model', 'ontology_id'):
            if record.get(key) is not None and not isinstance(record[key], str):
                raise ValueError(f'{key} must be a string')
        record.setdefault('metadata', {})
        if not isinstance(record['metadata'], dict) or ('properties' in record and not isinstance(record['properties'], dict)):
            raise ValueError('Metadata and properties must be objects')
        for key in ('valid_from', 'valid_until'):
            record[key] = normalize_time(record.get(key))
        if record['valid_from'] and record['valid_until'] and record['valid_from'] >= record['valid_until']:
            raise ValueError('Business interval must have positive duration')
        embedding = record.get('embedding')
        if embedding is not None and (not isinstance(embedding, list) or not embedding or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in embedding)):
            raise ValueError('Embedding must be a non-empty finite numeric list')
        return record

    def _put(self, project_id, record, expected_version=None, recorded_at=None):
        record = self._validate_record(record)
        if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
            raise ValueError('Expected version must be a non-negative integer')
        old = self._db.execute('SELECT version,recorded_at FROM record_versions WHERE project_id=? AND id=? AND superseded_at IS NULL', (project_id, record['id'])).fetchone()
        version = old['version'] if old else 0
        if expected_version is not None and expected_version != version:
            raise ValueError('Version conflict: record has changed')
        now = recorded_at or utc_now()
        if old and now <= old['recorded_at']:
            now = normalize_time(datetime.fromisoformat(old['recorded_at']) + timedelta(microseconds=1))
        self._db.execute('UPDATE record_versions SET superseded_at=? WHERE project_id=? AND id=? AND superseded_at IS NULL', (now, project_id, record['id']))
        version_id = str(uuid4())
        self._db.execute('INSERT INTO record_versions VALUES (?,?,?,?,?,?,NULL)', (project_id, record['id'], version + 1, version_id, _json(record), now))
        return {**record, 'project_id': project_id, 'version': version + 1, 'version_id': version_id, 'recorded_at': now, 'superseded_at': None}

    def put_record(self, project_id, record, expected_version=None):
        with self._transaction():
            self.get_project(project_id)
            return self._put(project_id, record, expected_version)

    def put_batch(self, project_id, records, expected_versions=None):
        if not isinstance(records, list):
            raise ValueError('Records must be a list')
        expected_versions = {} if expected_versions is None else expected_versions
        if not isinstance(expected_versions, dict) or any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in expected_versions.items()):
            raise ValueError('Expected versions must map record IDs to non-negative integers')
        records = [self._validate_record(record) for record in records]
        ids = {record['id'] for record in records}
        if len(ids) != len(records):
            raise ValueError('Batch record IDs must be unique')
        if not set(expected_versions).issubset(ids):
            raise ValueError('Expected versions reference records outside the batch')
        with self._transaction():
            self.get_project(project_id)
            latest = self._db.execute('SELECT MAX(recorded_at) FROM record_versions WHERE project_id=?', (project_id,)).fetchone()[0]
            now = utc_now()
            if latest and now <= latest:
                now = normalize_time(datetime.fromisoformat(latest) + timedelta(microseconds=1))
            return [self._put(project_id, record, expected_versions.get(record['id']), now) for record in records]

    @staticmethod
    def _record(row):
        return {**json.loads(row['payload']), **{key: row[key] for key in ('project_id', 'version', 'version_id', 'recorded_at', 'superseded_at')}}

    def history(self, project_id, record_id):
        self.get_project(project_id)
        with self._lock:
            rows = self._db.execute('SELECT * FROM record_versions WHERE project_id=? AND id=? ORDER BY version', (project_id, record_id)).fetchall()
        return [self._record(row) for row in rows]

    def current_records(self, project_id):
        """Read latest system versions, including expired and future facts."""
        self.get_project(project_id)
        with self._lock:
            rows = self._db.execute('SELECT * FROM record_versions WHERE project_id=? AND superseded_at IS NULL ORDER BY id', (project_id,)).fetchall()
        return [self._record(row) for row in rows]

    def query(self, project_id, filters=None, valid_at=None, known_at=None, kinds=None, include_unknown=True):
        validate_filter(filters)
        now = utc_now()
        valid_at = normalize_time(valid_at) or now
        known_at = normalize_time(known_at) or now
        if kinds is not None and (not isinstance(kinds, (list, tuple, set)) or any(k not in {'document', 'entity', 'relation', 'chunk'} for k in kinds)):
            raise ValueError('Invalid record kinds')
        self.get_project(project_id)
        with self._lock:
            rows = self._db.execute('SELECT * FROM record_versions WHERE project_id=? AND recorded_at<=? AND (superseded_at IS NULL OR superseded_at>?) ORDER BY id,version', (project_id, known_at, known_at)).fetchall()
        result = []
        for row in rows:
            record = self._record(row)
            start, end = record['valid_from'], record['valid_until']
            if (not include_unknown and start is None) or (start is not None and start > valid_at) or (end is not None and end <= valid_at):
                continue
            if kinds is not None and record['kind'] not in kinds:
                continue
            if matches_filter(record, filters):
                result.append(record)
        return result

    def get_record(self, project_id, record_id, valid_at=None, known_at=None):
        records = self.query(project_id, {'field': 'id', 'op': 'eq', 'value': record_id}, valid_at, known_at)
        if not records:
            raise KeyError(record_id)
        return records[0]

    def save_ontology(self, project_id, turtle, summary, metadata=None):
        metadata = {} if metadata is None else metadata
        if not isinstance(turtle, str) or not isinstance(summary, dict) or not isinstance(metadata, dict):
            raise ValueError('Ontology requires Turtle text and summary object')
        item = {'id': str(uuid4()), 'project_id': project_id, 'turtle': turtle,
                'summary': json.loads(_json(summary)), 'created_at': utc_now(),
                'metadata': json.loads(_json(metadata))}
        with self._transaction():
            self.get_project(project_id)
            self._db.execute('''INSERT INTO ontologies
                (id,project_id,turtle,summary,created_at,metadata) VALUES (?,?,?,?,?,?)''',
                (item['id'], project_id, turtle, _json(summary), item['created_at'], _json(metadata)))
        return item

    def publish_ontology_draft(self, project_id, draft, expected_parent_id):
        """Atomically publish a discovery draft and its immutable ontology version."""
        if not isinstance(draft, dict) or draft.get('project_id') != project_id:
            raise ValueError('Ontology discovery draft does not belong to this project')
        summary = draft.get('summary')
        metadata = draft.get('ontology_metadata', {})
        if not isinstance(draft.get('turtle'), str) or not isinstance(summary, dict) or not isinstance(metadata, dict):
            raise ValueError('Ontology discovery draft is incomplete')
        ontology = {'id': str(uuid4()), 'project_id': project_id, 'turtle': draft['turtle'],
                    'summary': json.loads(_json(summary)), 'created_at': utc_now(),
                    'metadata': json.loads(_json(metadata))}
        published = {**draft, 'status': 'published', 'revision': draft.get('revision', 1) + 1,
                     'published_at': utc_now(), 'ontology_id': ontology['id'],
                     'mapped_entities': 0, 'mapped_relations': 0, 'mapped_attributes': 0,
                     'requires_controlled_reingest': True}
        with self._transaction():
            self.get_project(project_id)
            latest = self._db.execute(
                'SELECT id FROM ontologies WHERE project_id=? ORDER BY rowid DESC LIMIT 1',
                (project_id,)).fetchone()
            latest_id = latest['id'] if latest else None
            if latest_id != expected_parent_id:
                raise ValueError('Version conflict: 本体已更新，请基于当前版本重新生成发现草案')
            self._db.execute('''INSERT INTO ontologies
                (id,project_id,turtle,summary,created_at,metadata) VALUES (?,?,?,?,?,?)''',
                (ontology['id'], project_id, ontology['turtle'], _json(ontology['summary']),
                 ontology['created_at'], _json(ontology['metadata'])))
            self._db.execute(
                'INSERT INTO artifacts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                (published['id'], 'ontology_discovery_draft', project_id, _json(published)))
        return ontology, published

    def commit_ontology_change(self, project_id, ontology, proposal, document, expected_document_version,
                               expected_ontology_id):
        """Atomically publish an ontology version, proposal decision and candidate revalidation."""
        if not all(isinstance(item,dict) for item in (ontology,proposal,document)):
            raise ValueError('Ontology change commit requires object payloads')
        with self._transaction():
            self.get_project(project_id)
            latest=self._db.execute('SELECT id FROM ontologies WHERE project_id=? ORDER BY rowid DESC LIMIT 1',
                                    (project_id,)).fetchone()
            if not latest or latest['id']!=expected_ontology_id:
                raise ValueError('Version conflict: 本体已更新，请重新评估草案影响')
            self._db.execute('''INSERT INTO ontologies
                (id,project_id,turtle,summary,created_at,metadata) VALUES (?,?,?,?,?,?)''',
                (ontology['id'],project_id,ontology['turtle'],_json(ontology['summary']),
                 ontology['created_at'],_json(ontology.get('metadata',{}))))
            saved_document=self._put(project_id,document,expected_document_version)
            self._db.execute('INSERT INTO artifacts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                             (proposal['id'],'ontology_change',project_id,_json(proposal)))
        return ontology,saved_document

    def list_ontologies(self, project_id):
        self.get_project(project_id)
        with self._lock:
            rows = self._db.execute('SELECT * FROM ontologies WHERE project_id=? ORDER BY rowid', (project_id,)).fetchall()
        return [{**dict(row), 'summary': json.loads(row['summary']),
                 'metadata': json.loads(row['metadata'] or '{}')} for row in rows]

    def get_ontology(self, project_id, ontology_id=None):
        items = self.list_ontologies(project_id)
        for item in reversed(items):
            if ontology_id is None or item['id'] == ontology_id:
                return item
        if ontology_id is None:
            raise OntologyNotPublished(project_id)
        raise KeyError(ontology_id)

    def save_artifact(self, kind, item):
        with self._transaction():
            if item.get('project_id'): self.get_project(item['project_id'])
            self._db.execute('INSERT INTO artifacts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                             (item['id'],kind,item.get('project_id'),_json(item)))
        return item

    def get_artifact(self,kind,artifact_id):
        with self._lock:
            row=self._db.execute('SELECT payload FROM artifacts WHERE id=? AND kind=?',(artifact_id,kind)).fetchone()
        if not row: raise KeyError(artifact_id)
        return json.loads(row[0])

    def list_artifacts(self,kind,project_id=None):
        with self._lock:
            rows=self._db.execute('SELECT payload FROM artifacts WHERE kind=? ORDER BY rowid DESC',(kind,)).fetchall()
        result=[json.loads(r[0]) for r in rows]
        return [r for r in result if project_id is None or r.get('project_id')==project_id]
