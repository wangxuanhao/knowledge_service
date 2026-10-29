import hashlib
import io

import pytest

from knowledge_service.integrations import source_object_store as module


class Missing(Exception):
    response = {'Error': {'Code': 'NoSuchKey'}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.bodies = []
        self.puts = []

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise Missing()
        return {'ContentLength': len(self.objects[Key]), 'ETag': 'never-a-checksum'}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise Missing()
        body = io.BytesIO(self.objects[Key])
        self.bodies.append(body)
        return {'Body': body, 'ContentLength': len(self.objects[Key]), 'ETag': 'never-a-checksum'}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        self.objects[kwargs['Key']] = kwargs['Body'].read()

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


def store(client=None, **kwargs):
    cls = getattr(module, 'S3SourceObjectStore', None)
    assert cls is not None, 'S3 content-addressed adapter is missing'
    return cls(bucket='sources', client=client or FakeS3(), **kwargs)


def test_s3_put_stream_checks_full_hash_and_closes_response():
    client = FakeS3()
    objects = store(client)
    raw = b'original bytes\r\n'
    digest = hashlib.sha256(raw).hexdigest()
    obj = objects.put(io.BytesIO(raw), 'text/plain', digest)
    assert obj['storage_key'] == digest[:2] + '/' + digest
    assert objects.exists(obj['storage_key'])
    assert objects.read(obj['storage_key']) == raw
    assert client.bodies[-1].closed
    with objects.open(obj['storage_key']) as opened:
        assert opened.read() == raw
        with pytest.raises((io.UnsupportedOperation, AttributeError)):
            opened.write(b'changed')
        with pytest.raises((io.UnsupportedOperation, AttributeError)):
            opened.truncate(0)
    assert opened.closed
    objects.put(raw, 'text/plain')
    assert len(client.puts) == 1
    assert all(body.closed for body in client.bodies)


def test_s3_rejects_wrong_hash_corruption_invalid_keys_and_oversize():
    client = FakeS3()
    objects = store(client, max_object_bytes=8)
    with pytest.raises(ValueError, match='checksum'):
        objects.put(b'bytes', expected_sha256='0' * 64)
    assert not client.objects
    with pytest.raises(ValueError, match='size|limit'):
        objects.put(io.BytesIO(b'123456789'))
    obj = objects.put(b'valid')
    client.objects[obj['storage_key']] = b'corrupt'
    with pytest.raises(ValueError, match='checksum'):
        objects.read(obj['storage_key'])
    assert client.bodies[-1].closed
    with pytest.raises(ValueError, match='checksum'):
        objects.put(b'valid')
    for key in ('../secret', '00/' + 'a' * 64):
        with pytest.raises(ValueError, match='key'):
            objects.exists(key)
    with pytest.raises(FileNotFoundError):
        objects.read('00/' + '0' * 64)


def test_s3_gc_respects_reference_check_and_alias():
    objects = store()
    obj = objects.put(b'delete')
    assert not objects.delete_if_unreferenced(obj['storage_key'], lambda key: True)
    assert objects.exists(obj['storage_key'])
    assert objects.delete_unreferenced(obj['storage_key'], lambda key: False)
    assert not objects.exists(obj['storage_key'])


def test_environment_factory_requires_explicit_valid_s3_configuration(monkeypatch, tmp_path):
    factory = getattr(module, 'source_object_store_from_environment', None)
    assert factory is not None, 'environment-selected source object factory is missing'
    monkeypatch.setenv('KG_SOURCE_OBJECT_BACKEND', 'local')
    monkeypatch.setenv('KG_SOURCE_OBJECT_ROOT', str(tmp_path))
    assert isinstance(factory(), module.SourceObjectStore)
    monkeypatch.setenv('KG_SOURCE_OBJECT_BACKEND', 's3')
    for key in ('BUCKET', 'ENDPOINT', 'ACCESS_KEY', 'SECRET_KEY'):
        monkeypatch.delenv('KG_SOURCE_S3_' + key, raising=False)
    with pytest.raises(ValueError, match='S3'):
        factory()
    monkeypatch.setenv('KG_SOURCE_OBJECT_BACKEND', 'typo')
    with pytest.raises(ValueError, match='backend'):
        factory()
