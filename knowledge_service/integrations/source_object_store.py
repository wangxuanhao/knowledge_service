"""Content addressed local source bytes; callers authorize metadata, never paths."""
import hashlib
import os
import re
import tempfile
import threading
from contextlib import nullcontext
from pathlib import Path
from typing import Protocol, runtime_checkable

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
