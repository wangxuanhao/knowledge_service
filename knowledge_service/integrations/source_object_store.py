"""Content addressed local source bytes; callers authorize metadata, never paths."""
import hashlib
import base64
import io
import os
import re
import tempfile
import threading
from contextlib import nullcontext
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

_ROOT_LOCKS = {}
_ROOT_LOCKS_GUARD = threading.Lock()


@runtime_checkable
class SourceObjectStoreProtocol(Protocol):
    def put(self,data,mime_type='application/octet-stream',expected_sha256=None) -> dict: ...
    def open(self,storage_key): ...
    def exists(self,storage_key) -> bool: ...
    def read(self,storage_key) -> bytes: ...
    def delete_if_unreferenced(self,storage_key,is_referenced,reference_lock=None) -> bool: ...


class SourceObjectStore:
    def __init__(self, root='data/sources'):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with _ROOT_LOCKS_GUARD:
            self._object_lock=_ROOT_LOCKS.setdefault(str(self.root).casefold(),threading.RLock())

    def _path(self, key):
        if not re.fullmatch(r'[0-9a-f]{2}/[0-9a-f]{64}', str(key)) or key[:2] != key[3:5]:
            raise ValueError('Invalid source storage key')
        candidate=self.root / key
        path = candidate.resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('Source path escapes object root')
        if path!=candidate:
            raise ValueError('Source object symlink aliases are prohibited')
        return path

    def put(self, data, mime_type='application/octet-stream',expected_sha256=None):
        # Preserve existing put(bytes,mime) and support put(stream,expected_sha256).
        if expected_sha256 is None and re.fullmatch(r'[0-9a-fA-F]{64}',str(mime_type)):
            expected_sha256=mime_type;mime_type='application/octet-stream'
        if expected_sha256 is not None:
            expected_sha256=str(expected_sha256).lower()
            if not re.fullmatch(r'[0-9a-f]{64}',expected_sha256): raise ValueError('Invalid expected source checksum')
        hasher=hashlib.sha256();size=0
        fd, temporary = tempfile.mkstemp(dir=self.root, prefix='.staging-')
        try:
            with os.fdopen(fd, 'wb') as stream:
                if hasattr(data,'read'):
                    while True:
                        piece=data.read(1024*1024)
                        if not piece: break
                        if isinstance(piece,str): piece=piece.encode('utf-8')
                        if not isinstance(piece,(bytes,bytearray,memoryview)): raise TypeError('Source stream must produce bytes or text')
                        stream.write(piece);hasher.update(piece);size+=len(piece)
                else:
                    piece=bytes(data)
                    stream.write(piece);hasher.update(piece);size=len(piece)
                stream.flush()
                os.fsync(stream.fileno())
            digest=hasher.hexdigest()
            if expected_sha256 is not None and digest!=expected_sha256:
                raise ValueError('Source stream checksum mismatch')
            key=digest[:2]+'/'+digest;path=self._path(key)
            # Hard-link publishes a complete immutable file without replacing another writer.
            with self._object_lock:
                path.parent.mkdir(parents=True,exist_ok=True)
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                        raise ValueError('Stored source object checksum mismatch')
        finally:
            os.unlink(temporary)
        return dict(id=digest, sha256=digest, size_bytes=size, mime_type=mime_type, storage_key=key)

    def open(self,storage_key):
        path=self._path(storage_key)
        with self._object_lock:
            stream=path.open('rb')
            try:
                digest=hashlib.sha256()
                for piece in iter(lambda:stream.read(1024*1024),b''): digest.update(piece)
                if digest.hexdigest()!=path.name: raise ValueError('Stored source object checksum mismatch')
                stream.seek(0)
                return stream
            except BaseException:
                stream.close()
                raise

    def exists(self,storage_key):
        return self._path(storage_key).is_file()

    def read(self, storage_key):
        with self.open(storage_key) as stream:
            return stream.read()

    def delete_unreferenced(self, storage_key, is_referenced):
        """Metadata owner must hold its transaction/GC lock throughout this callback."""
        return self.delete_if_unreferenced(storage_key,is_referenced)

    def delete_if_unreferenced(self,storage_key,is_referenced,reference_lock=None):
        """Caller holds its DB reference lock, or supplies the same context manager here."""
        if not callable(is_referenced): raise TypeError('A reference check callback is required')
        path=self._path(storage_key)
        with reference_lock if reference_lock is not None else nullcontext():
            with self._object_lock:
                if is_referenced(storage_key): return False
                path.unlink(missing_ok=True)
                return True


LocalSourceObjectStore = SourceObjectStore


def _validated_key(key):
    if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{2}/[0-9a-f]{64}', key) or key[:2] != key[3:5]:
        raise ValueError('Invalid source storage key')
    return key


class S3SourceObjectStore:
    """S3/MinIO immutable objects with full SHA256 verification, never ETag trust.

    Spooling caps memory; the total limit also bounds disk usage and remote reads.
    boto3 is imported only for an explicitly configured S3 backend.
    """
    def __init__(self, *, bucket, endpoint=None, access_key=None, secret_key=None,
                 region='us-east-1', client=None, max_object_bytes=1_000_000_000,
                 spool_memory_bytes=8 * 1024 * 1024):
        if not bucket or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,254}', bucket):
            raise ValueError('Invalid S3 source bucket')
        if max_object_bytes < 1 or spool_memory_bytes < 1:
            raise ValueError('Source object limits must be positive')
        self.bucket, self.max_object_bytes = bucket, max_object_bytes
        self.spool_memory_bytes = min(spool_memory_bytes, max_object_bytes)
        self._object_lock = threading.RLock()
        if client is None:
            parsed = urlsplit(endpoint or '')
            if (parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username
                    or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
                raise ValueError('Invalid S3 source endpoint')
            if not access_key or not secret_key:
                raise ValueError('S3 source access key and secret key are required')
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:
                raise RuntimeError('S3 source backend requires the s3 dependency extra') from exc
            client = boto3.client('s3', endpoint_url=endpoint, region_name=region,
                aws_access_key_id=access_key, aws_secret_access_key=secret_key,
                config=Config(signature_version='s3v4', connect_timeout=5, read_timeout=30,
                    retries={'mode': 'standard', 'total_max_attempts': 3},
                    s3={'addressing_style': 'path'}))
        self.client = client

    @staticmethod
    def _missing(exc):
        response = getattr(exc, 'response', {})
        return str(response.get('Error', {}).get('Code')) in ('404', 'NoSuchKey', 'NotFound')

    def exists(self, storage_key):
        key = _validated_key(storage_key)
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:
            if self._missing(exc):
                return False
            raise

    def _spool(self, data, expected_sha256=None):
        if expected_sha256 is not None:
            expected_sha256 = str(expected_sha256).lower()
            if not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
                raise ValueError('Invalid expected source checksum')
        stream = tempfile.SpooledTemporaryFile(max_size=self.spool_memory_bytes, mode='w+b')
        hasher, size = hashlib.sha256(), 0
        source = data if hasattr(data, 'read') else io.BytesIO(bytes(data))
        try:
            while True:
                piece = source.read(min(1024 * 1024, self.max_object_bytes + 1 - size))
                if not piece:
                    break
                if not isinstance(piece, (bytes, bytearray, memoryview)):
                    raise TypeError('Source stream must produce bytes')
                size += len(piece)
                if size > self.max_object_bytes:
                    raise ValueError('Source object size limit exceeded')
                stream.write(piece)
                hasher.update(piece)
            digest = hasher.hexdigest()
            if expected_sha256 is not None and digest != expected_sha256:
                raise ValueError('Source object checksum mismatch')
            stream.seek(0)
            return stream, digest, size
        except BaseException:
            stream.close()
            raise

    def put(self, data, mime_type='application/octet-stream', expected_sha256=None):
        if expected_sha256 is None and re.fullmatch(r'[0-9a-fA-F]{64}', str(mime_type)):
            expected_sha256, mime_type = mime_type, 'application/octet-stream'
        stream, digest, size = self._spool(data, expected_sha256)
        key = digest[:2] + '/' + digest
        try:
            with self._object_lock:
                if not self.exists(key):
                    self.client.put_object(Bucket=self.bucket, Key=key, Body=stream,
                        ContentLength=size, ContentType=mime_type,
                        ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode('ascii'))
                # Verify an existing object as well as the just-uploaded object.
                with self.open(key):
                    pass
        finally:
            stream.close()
        return dict(id=digest, sha256=digest, size_bytes=size, mime_type=mime_type, storage_key=key)

    def open(self, storage_key):
        key = _validated_key(storage_key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if self._missing(exc):
                raise FileNotFoundError('Source object is missing') from None
            raise
        body = response['Body']
        try:
            if int(response.get('ContentLength', 0)) > self.max_object_bytes:
                raise ValueError('Source object size limit exceeded')
            stream, _, _ = self._spool(body, key[3:])
        finally:
            body.close()
        return io.BufferedReader(stream)

    def read(self, storage_key):
        with self.open(storage_key) as stream:
            return stream.read()

    def delete_if_unreferenced(self, storage_key, is_referenced, reference_lock=None):
        key = _validated_key(storage_key)
        if not callable(is_referenced):
            raise TypeError('A reference check callback is required')
        with reference_lock if reference_lock is not None else nullcontext():
            with self._object_lock:
                if is_referenced(key):
                    return False
                self.client.delete_object(Bucket=self.bucket, Key=key)
                return True

    def delete_unreferenced(self, storage_key, is_referenced):
        return self.delete_if_unreferenced(storage_key, is_referenced)


def source_object_store_from_environment():
    backend = os.getenv('KG_SOURCE_OBJECT_BACKEND', 'local').strip().lower()
    if backend == 'local':
        return SourceObjectStore(os.getenv('KG_SOURCE_OBJECT_ROOT', 'data/sources'))
    if backend != 's3':
        raise ValueError('Unknown source object backend')
    values = {name: os.getenv('KG_SOURCE_S3_' + name, '').strip()
              for name in ('ENDPOINT', 'BUCKET', 'ACCESS_KEY', 'SECRET_KEY')}
    if not all(values.values()):
        raise ValueError('S3 source endpoint, bucket, access key and secret key are required')
    return S3SourceObjectStore(endpoint=values['ENDPOINT'], bucket=values['BUCKET'],
        access_key=values['ACCESS_KEY'], secret_key=values['SECRET_KEY'],
        region=os.getenv('KG_SOURCE_S3_REGION', 'us-east-1'))
