"""Immutable extraction receipts before knowledge reconciliation and writes."""
import hashlib
import json
from copy import deepcopy

def checkpoint_fingerprint(**inputs):
    return hashlib.sha256(json.dumps(inputs,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

class IngestCheckpoints:
    def __init__(self,store):self.store=store
    def execute(self,document_id,ordinal,fingerprint,extract,guard=None,attempt=1,restart=False):
        row=None if restart else self.store.get(document_id,ordinal,fingerprint)
        if row is not None:return deepcopy(row['output'])
        if guard:guard()
        try:output=extract()
        except Exception as exc:
            exc.failed_chunk_ordinal=ordinal
            raise
        if guard:guard()
        self.store.put(document_id,ordinal,fingerprint,deepcopy(output),attempt=attempt)
        return deepcopy(output)
