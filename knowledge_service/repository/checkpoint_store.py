"""Immutable successful slice outputs, scoped by extraction fingerprint."""
import json
from uuid import uuid4

from .ingest_batch_store import _json


class CheckpointStore:
    def __init__(self, repository):
        self.repo = repository
        self.db = repository._db

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        result = dict(row)
        for key in ('output_json','diagnostics_json'):
            if isinstance(result[key],str):
                result[key] = json.loads(result[key])
        result['output'] = result['output_json']
        return result

    def get(self, document_id, ordinal, fingerprint):
        with self.repo._lock:
            return self._decode(self.db.execute('''SELECT * FROM ingest_chunk_checkpoints
                WHERE document_id=? AND chunk_ordinal=? AND configuration_fingerprint=?''',
                (document_id,ordinal,fingerprint)).fetchone())

    def put(self, document_id, ordinal, fingerprint, output, **metadata):
        allowed = {'batch_item_id','attempt','chunk_id','chunk_sha256','diagnostics','document_generation'}
        if set(metadata)-allowed:
            raise ValueError('unknown checkpoint metadata')
        with self.repo._transaction():
            self.db.execute('''INSERT INTO ingest_chunk_checkpoints
                (id,document_id,chunk_ordinal,configuration_fingerprint,output_json,
                 batch_item_id,attempt,document_generation,chunk_id,chunk_sha256,diagnostics_json)
                VALUES (?,?,?,?,?::jsonb,?,?,?,?,?,?::jsonb)
                ON CONFLICT(document_id,chunk_ordinal,configuration_fingerprint) DO NOTHING''',
                (uuid4().hex,document_id,ordinal,fingerprint,_json(output),metadata.get('batch_item_id'),
                 metadata.get('attempt',0),metadata.get('document_generation',0),metadata.get('chunk_id'),metadata.get('chunk_sha256'),_json(metadata.get('diagnostics',{}))))
            result = self.get(document_id,ordinal,fingerprint)
            if _json(result['output']) != _json(output):
                raise ValueError('successful checkpoint output conflict')
            return result

    def list_for_document(self, document_id, fingerprint, generation=None):
        with self.repo._lock:
            where='document_id=?'; args=[document_id]
            if fingerprint is not None:
                where+=' AND configuration_fingerprint=?';args.append(fingerprint)
            if generation is not None:
                where+=' AND document_generation=?';args.append(generation)
            return [self._decode(row) for row in self.db.execute('SELECT * FROM ingest_chunk_checkpoints WHERE '+where+' ORDER BY chunk_ordinal',args)]

    def has_other_configuration(self, document_id, ordinal, generation, fingerprint):
        with self.repo._lock:
            return bool(self.db.execute('''SELECT 1 FROM ingest_chunk_checkpoints WHERE document_id=?
                AND chunk_ordinal=? AND document_generation=? AND configuration_fingerprint<>? LIMIT 1''',
                (document_id,ordinal,generation,fingerprint)).fetchone())
