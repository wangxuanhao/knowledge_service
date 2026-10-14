"""Inspect ZIP bytes without extracting attacker controlled filesystem paths."""
import io
import json
import mimetypes
import posixpath
import stat
import zipfile
from pathlib import PurePosixPath
from .markdown_parser import parse_markdown


def safe_path(name):
    name = name.replace('\\', '/')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or ':' in name or '\x00' in name:
        raise ValueError('Unsafe package path')
    return str(path)


class MarkdownPackageReader:
    def __init__(self, max_files=5000, max_uncompressed_bytes=512 * 1024 * 1024, max_ratio=200):
        self.max_files, self.max_uncompressed_bytes, self.max_ratio = max_files, max_uncompressed_bytes, max_ratio

    def read(self, data, filename='package.zip'):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > self.max_files or sum(i.file_size for i in infos) > self.max_uncompressed_bytes:
                raise ValueError('Package expansion limit exceeded')
            files = {}
            for info in infos:
                path = safe_path(info.filename)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError('Package symlinks are prohibited')
                if info.is_dir(): continue
                if path in files: raise ValueError('Duplicate package entry')
                if info.file_size > self.max_ratio * max(info.compress_size, 1):
                    raise ValueError('Package compression ratio limit exceeded')
                files[path] = archive.read(info)
        manifest = json.loads(files.get('manifest.json', b'{}'))
        entries = manifest.get('documents')
        if entries is None:
            entries = [{'path': p} for p in sorted(files) if p.lower().endswith('.md')]
        if not isinstance(entries, list): raise ValueError('manifest.documents must be a list')
        documents = []
        for entry in entries:
            entry = {'path': entry} if isinstance(entry, str) else entry
            path = safe_path(entry['path'])
            if path not in files or not path.lower().endswith('.md'): raise ValueError('Manifest Markdown is missing')
            text = files[path].decode('utf-8-sig')
            parsed = parse_markdown(text)
            assets, warnings = [], []
            for image in parsed['images']:
                asset = dict(image)
                if image['kind'] == 'local':
                    relative = image['path'].replace('\\', '/')
                    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), relative))
                    resolved = safe_path(resolved)
                    asset.update(resolved_path=resolved, original_path=image['path'])
                    if resolved in files:
                        mime=mimetypes.guess_type(resolved)[0]
                        if mime not in {'image/png','image/jpeg','image/webp','image/gif','image/bmp','image/tiff'}:
                            asset['status']='missing'
                            warnings.append(dict(code='source_asset_missing',path=resolved,reason='unsupported_image_format'))
                        else:
                            asset.update(data=files[resolved], status='available', mime_type=mime)
                    else:
                        asset['status'] = 'missing'
                        warnings.append(dict(code='source_asset_missing', path=resolved))
                assets.append(asset)
            documents.append(dict(path=path, text=text, assets=assets, warnings=warnings, metadata=entry.get('metadata', {}), title=entry.get('title'), parsed=parsed))
        if not documents: raise ValueError('Package contains no Markdown documents')
        return dict(filename=filename, documents=documents, manifest=manifest)


def read_markdown_package(data, **kwargs):
    return MarkdownPackageReader(**kwargs).read(data)
