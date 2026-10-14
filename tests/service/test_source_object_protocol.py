"""Content-addressed object protocol accepts bounded caller streams."""
import hashlib
import io
import pytest
from knowledge_service.integrations.source_object_store import SourceObjectStore,SourceObjectStoreProtocol


def test_stream_expected_hash_and_readonly_open(tmp_path):
    store=SourceObjectStore(tmp_path/'sources')
    payload='规则来源'.encode('utf-8'); digest=hashlib.sha256(payload).hexdigest()
    first=store.put(io.StringIO('规则来源'),expected_sha256=digest)
    second=store.put(io.BytesIO(payload),digest)
    assert first['storage_key']==second['storage_key'] and store.exists(first['storage_key'])
    with store.open(first['storage_key']) as source:
        assert source.read()==payload
        with pytest.raises(io.UnsupportedOperation): source.write(b'invalid')
    with pytest.raises(ValueError,match='checksum'):
        store.put(io.StringIO('wrong'),expected_sha256=digest)
    assert len([p for p in (tmp_path/'sources').rglob('*') if p.is_file()])==1


def test_object_open_exists_delete_reject_escape(tmp_path):
    store=SourceObjectStore(tmp_path/'sources')
    for key in ('../etc/passwd','C:/bad','ab/'+'f'*64):
        with pytest.raises(ValueError): store.open(key)
        with pytest.raises(ValueError): store.exists(key)
    obj=store.put(b'bytes')
    assert not store.delete_if_unreferenced(obj['storage_key'],lambda key:True)
    assert store.exists(obj['storage_key'])
    assert store.delete_if_unreferenced(obj['storage_key'],lambda key:False)
    assert not store.exists(obj['storage_key'])
