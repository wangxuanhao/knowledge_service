"""Content addressed local source bytes; callers authorize metadata, never paths."""
import hashlib
import os
import re
import tempfile
from pathlib import Path


class SourceObjectStore:
    def __init__(self, root='data/sources'):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key):
        if not re.fullmatch(r'[0-9a-f]{2}/[0-9a-f]{64}', str(key)) or key[:2] != key[3:5]:
            raise ValueError('Invalid source storage key')
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('Source path escapes object root')
        return path

    def put(self, data, mime_type='application/octet-stream'):
        data = bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        key = digest[:2] + '/' + digest
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.staging-')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # Hard-link publishes a complete immutable file without replacing another writer.
            try:
                os.link(temporary, path)
            except FileExistsError:
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ValueError('Stored source object checksum mismatch')
        finally:
            os.unlink(temporary)
        return dict(id=digest, sha256=digest, size_bytes=len(data), mime_type=mime_type, storage_key=key)

    def read(self, storage_key):
        path = self._path(storage_key)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != path.name:
            raise ValueError('Stored source object checksum mismatch')
        return data

    def delete_unreferenced(self, storage_key, is_referenced):
        """Metadata owner must hold its transaction/GC lock throughout this callback."""
        path = self._path(storage_key)
        if is_referenced(storage_key):
            return False
        path.unlink(missing_ok=True)
        return True


LocalSourceObjectStore = SourceObjectStore
