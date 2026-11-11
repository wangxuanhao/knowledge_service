"""Read an upload ZIP as one document without extracting archive paths.

The legacy Markdown reader remains responsible for restoring saved sources. This
boundary deliberately rejects multi-document uploads instead of fanning them out.
"""
from __future__ import annotations

from hashlib import sha256
import io
import json
import mimetypes
from pathlib import Path, PurePosixPath
import posixpath
import stat
import tempfile
from urllib.parse import quote
import zipfile

from .document_media import (IMAGE_MIMES, MAX_DOCUMENT_IMAGE_BYTES, MAX_DOCUMENT_IMAGES,
                             MAX_IMAGE_BYTES, inspect_image)
from .document_parser import DocumentParser, MAX_TEXT_CHARS, MAX_UPLOAD_BYTES
from .markdown_package import safe_path
from .markdown_parser import parse_markdown


PRIMARY_EXTENSIONS = {'.md', '.txt', '.pdf', '.docx', '.html', '.htm',
                      '.png', '.jpg', '.jpeg', '.webp', '.mp4', '.webm'}
PAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}
VIDEO_MIMES = {'video/mp4', 'video/webm'}


def inspect_page_image(data, path):
    """A page means one static image, sharing the direct-upload frame policy."""
    from PIL import Image
    try:
        details = inspect_image(data, mimetypes.guess_type(path)[0])
        with Image.open(io.BytesIO(data)) as image:
            if getattr(image, 'n_frames', 1) != 1:
                raise ValueError('Image pages do not support animation or multiple frames; export static images')
        return details
    except (OSError, SyntaxError) as exc:
        raise ValueError(f'Invalid image page {path!r}: {exc}') from exc


class SingleDocumentPackageReader:
    def __init__(self, parser=None, max_files=5000, max_uncompressed_bytes=512 * 1024 * 1024,
                 max_ratio=200):
        for name, value in (('max_files', max_files), ('max_uncompressed_bytes', max_uncompressed_bytes),
                            ('max_ratio', max_ratio)):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 1:
                raise ValueError(f'{name} must be positive')
        self.parser = parser if parser is not None else DocumentParser()
        self.max_files = max_files
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_ratio = max_ratio

    def read(self, data, filename='package.zip'):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files, root_prefix = self._members(archive)
            used = set()

            def read_member(path, max_bytes=MAX_UPLOAD_BYTES):
                if path not in files:
                    raise ValueError(f'Package file is missing: {path}')
                info = files[path]
                if info.file_size > max_bytes:
                    raise ValueError(f'Package file exceeds byte limit ({max_bytes}): {path}')
                used.add(path)
                return archive.read(info)

            manifest = {}
            if 'manifest.json' in files:
                try:
                    manifest = json.loads(read_member('manifest.json').decode('utf-8-sig'))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise ValueError('manifest.json must contain valid UTF-8 JSON') from exc
            entry = self._select(manifest, files, has_manifest='manifest.json' in files)
            try:
                if 'pages' in entry:
                    document = self._pages(entry, filename, read_member)
                else:
                    document = self._primary(entry, read_member, files)
                    document['primary_archive_path'] = files[entry['entry']].filename
            except (OSError, SyntaxError) as exc:
                raise ValueError(f'Invalid package document or image: {exc}') from exc
            unused = [dict(code='unused_package_file', path=path) for path in sorted(files) if path not in used]
            warnings = [*document['warnings'], *unused]
            document['warnings'].extend(unused)
        return dict(filename=filename, documents=[document], manifest=manifest,
                    manifest_archive_path=files['manifest.json'].filename if 'manifest.json' in files else None,
                    root_prefix=root_prefix, warnings=warnings)

    def _members(self, archive):
        infos = archive.infolist()
        if len(infos) > self.max_files or sum(info.file_size for info in infos) > self.max_uncompressed_bytes:
            raise ValueError('Package expansion limit exceeded')
        files = {}
        for info in infos:
            path = safe_path(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Package symlinks are prohibited')
            if info.flag_bits & 1:
                raise ValueError('Encrypted package members are prohibited')
            if info.is_dir():
                continue
            if path in files:
                raise ValueError('Duplicate package entry')
            if info.file_size > self.max_ratio * max(info.compress_size, 1):
                raise ValueError('Package compression ratio limit exceeded')
            files[path] = info
        # Strip one common outer folder, never guess between candidate roots.
        prefix = ''
        if files and all('/' in path for path in files):
            roots = {path.split('/', 1)[0] for path in files}
            if len(roots) == 1:
                prefix = roots.pop() + '/'
                files = {path[len(prefix):]: info for path, info in files.items()}
        return files, prefix

    def _select(self, manifest, files, *, has_manifest):
        if not isinstance(manifest, dict):
            raise ValueError('manifest must be an object')
        if has_manifest:
            if 'schema_version' in manifest:
                if type(manifest['schema_version']) is not int or manifest['schema_version'] != 2:
                    raise ValueError('Unknown package schema_version; only version 2 is supported')
                if 'documents' in manifest or not isinstance(manifest.get('document'), dict):
                    raise ValueError('Version 2 requires one manifest.document object')
                entry = dict(manifest['document'])
            elif 'documents' in manifest:
                entries = manifest['documents']
                if not isinstance(entries, list):
                    raise ValueError('manifest.documents must be a list')
                if len(entries) != 1:
                    raise ValueError('A ZIP must contain a single document; split legacy multi-document packages with the packaging script')
                old = entries[0]
                if isinstance(old, str):
                    entry = {'entry': old}
                elif isinstance(old, dict):
                    entry = {key: value for key, value in old.items() if key != 'path'}
                    entry['entry'] = old.get('path')
                else:
                    raise ValueError('Legacy manifest document must be a path string or object')
            else:
                raise ValueError('Manifest requires schema_version=2 and one document')
        else:
            candidates = [path for path in files if '/' not in path and
                          PurePosixPath(path).suffix.lower() in PRIMARY_EXTENSIONS]
            outside_assets = [path for path in files if path not in candidates and not path.startswith('assets/')]
            if len(candidates) != 1 or outside_assets:
                raise ValueError('A ZIP without a manifest requires one root main file and assets/ attachments; use document.pages for ordered images')
            entry = {'entry': candidates[0]}
        if not isinstance(entry.get('metadata', {}), dict):
            raise ValueError('Manifest document metadata must be an object')
        if 'title' in entry and (not isinstance(entry['title'], str) or not entry['title'].strip() or
                                 len(entry['title']) > 300):
            raise ValueError('Manifest document title must be a nonempty string of at most 300 characters')
        if ('entry' in entry) == ('pages' in entry):
            raise ValueError('Manifest document requires exactly one of entry or pages')
        if 'entry' in entry:
            path = safe_path(entry['entry'])
            if PurePosixPath(path).suffix.lower() not in PRIMARY_EXTENSIONS:
                raise ValueError('Unsupported package primary document format')
            if path not in files:
                raise ValueError(f'Manifest primary document is missing: {path}')
            entry['entry'] = path
        else:
            pages = entry['pages']
            if not isinstance(pages, list) or not pages or len(pages) > MAX_DOCUMENT_IMAGES:
                raise ValueError(f'document.pages must contain 1..{MAX_DOCUMENT_IMAGES} image paths')
            pages = [safe_path(path) for path in pages]
            if len(set(pages)) != len(pages):
                raise ValueError('Duplicate image page path')
            for path in pages:
                if PurePosixPath(path).suffix.lower() not in PAGE_EXTENSIONS:
                    raise ValueError('Image pages support PNG, JPEG and WebP only')
                if path not in files:
                    raise ValueError(f'Manifest image page is missing: {path}')
            entry['pages'] = pages
        return entry

    @staticmethod
    def _decode(data):
        try:
            text = data.decode('utf-8-sig')
        except UnicodeError as exc:
            raise ValueError('Text documents must use UTF-8 encoding') from exc
        if not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise ValueError(f'Document must contain text and fit the {MAX_TEXT_CHARS} character limit')
        return text

    def _primary(self, entry, read_member, files):
        path = entry['entry']
        extension = PurePosixPath(path).suffix.lower()
        data = read_member(path)
        extracted, provenance = [], {}
        if extension in {'.md', '.txt', '.html', '.htm'}:
            text = self._decode(data)
            structural = dict(document_parser='builtin-html' if extension in {'.html', '.htm'} else 'builtin-text',
                              parser_version='1', ocr_mode='not_applicable', warnings=[])
        else:
            # Only this generated basename is written. No archive path reaches disk.
            with tempfile.TemporaryDirectory(prefix='single-document-package-') as temporary:
                temporary_path = Path(temporary) / ('primary' + extension)
                temporary_path.write_bytes(data)
                result = self.parser.parse(temporary_path, PurePosixPath(path).name,
                                           size=len(data), digest=sha256(data).hexdigest())
            text, structural = result.text, dict(result.metadata)
            extracted, provenance = list(result.assets), dict(result.provenance)
            if len(text) > MAX_TEXT_CHARS:
                raise ValueError('Parsed document exceeds text character limit')
        parser_metadata = {'source_file': PurePosixPath(path).name, 'source_format': extension.lstrip('.'),
                           'source_size_bytes': len(data), 'source_sha256': sha256(data).hexdigest(), **structural}
        # An image/video has no invented body text, while its parsed view still
        # needs an inert media anchor for the original reader.
        display_text = text
        if not display_text.strip() and extracted:
            display_text = self._media_references(extracted)
        parsed = parse_markdown(display_text)
        assets, warnings = self._assets(parsed, path, read_member, files, extracted)
        provenance.update(primary_filename=PurePosixPath(path).name, primary_path=path)
        return dict(path=path, text=text, assets=assets, warnings=warnings, parsed=parsed,
                    title=entry.get('title', PurePosixPath(path).stem), metadata=entry.get('metadata', {}),
                    parser_metadata=parser_metadata, provenance=provenance,
                    primary_data=data, primary_filename=PurePosixPath(path).name)

    @staticmethod
    def _media_references(assets):
        lines = []
        for ordinal, asset in enumerate(assets):
            path = asset.get('path') or asset.get('original_path') or f'assets/media-{ordinal + 1}'
            url = quote(path, safe='/')
            is_video = asset.get('provenance', {}).get('media_type') == 'video'
            lines.append(f'<video src="{url}"></video>' if is_video else f'![]({url})')
        return '\n\n'.join(lines)

    def _pages(self, entry, filename, read_member):
        pages = entry['pages']
        extracted = []
        for number, path in enumerate(pages, 1):
            data = read_member(path, MAX_IMAGE_BYTES)
            details = inspect_page_image(data, path)
            extracted.append(dict(path=path, original_path=path, resolved_path=path, kind='local',
                                  status='available', data=data, **details,
                                  provenance=dict(media_type='image', page_no=number)))
        text = self._media_references(extracted)
        parsed = parse_markdown(text)
        assets, warnings = self._assets(parsed, '', read_member, {}, extracted)
        items = [dict(ordinal=number - 1, page_no=number, image_path=path)
                 for number, path in enumerate(pages, 1)]
        return dict(path=pages[0], text='', assets=assets, warnings=warnings, parsed=parsed,
                    title=entry.get('title', Path(filename).stem), metadata=entry.get('metadata', {}),
                    parser_metadata=dict(document_parser='ordered-image-pages', parser_version='1',
                                         page_count=len(pages), image_count=len(pages), warnings=[]),
                    provenance=dict(media_type='image_pages', pages=pages, items=items))

    def _assets(self, parsed, primary_path, read_member, files, extracted):
        extracted_by_path = {}
        for item in extracted:
            for key in ('path', 'original_path', 'url'):
                if item.get(key):
                    extracted_by_path[item[key]] = item
        assets, warnings = [], []
        total_image_bytes = 0
        if len(parsed['images']) > MAX_DOCUMENT_IMAGES:
            raise ValueError('Document image count limit exceeded')
        for image in parsed['images']:
            asset = dict(image)
            is_video = image.get('provenance', {}).get('media_type') == 'video'
            if image['kind'] == 'local':
                relative = image['path'].replace('\\', '/')
                extracted_item = extracted_by_path.get(relative)
                if extracted_item is not None:
                    asset = {**extracted_item, **image}
                    asset['provenance'] = {**image.get('provenance', {}), **extracted_item.get('provenance', {})}
                    asset['status'] = extracted_item.get('status', 'available' if extracted_item.get('data') else 'missing')
                    asset.setdefault('resolved_path', relative)
                    asset.setdefault('original_path', relative)
                else:
                    resolved = safe_path(posixpath.normpath(posixpath.join(posixpath.dirname(primary_path), relative)))
                    asset.update(resolved_path=resolved, original_path=image['path'])
                    mime = mimetypes.guess_type(resolved)[0]
                    allowed = VIDEO_MIMES if is_video else set(IMAGE_MIMES.values())
                    if resolved not in files or mime not in allowed:
                        asset['status'] = 'missing'
                        warning = dict(code='source_asset_missing', path=resolved)
                        if mime not in allowed:
                            warning['reason'] = 'unsupported_image_format'
                        warnings.append(warning)
                    else:
                        asset.update(data=read_member(resolved, MAX_UPLOAD_BYTES if is_video else MAX_IMAGE_BYTES),
                                     status='available', mime_type=mime)
            if asset.get('status') == 'available' and 'data' in asset:
                is_video = asset.get('provenance', {}).get('media_type') == 'video'
                if is_video:
                    from .video_asr import inspect_video
                    details = inspect_video(asset['data'], asset.get('mime_type'))
                    asset['provenance'] = {**asset.get('provenance', {}), 'duration_ms': details['duration_ms']}
                else:
                    asset.update(inspect_image(asset['data'], asset.get('mime_type')))
                    total_image_bytes += len(asset['data'])
                    if total_image_bytes > MAX_DOCUMENT_IMAGE_BYTES:
                        raise ValueError('Document image byte limit exceeded')
            assets.append(asset)
        return assets, warnings


def read_document_package(data, **kwargs):
    return SingleDocumentPackageReader(**kwargs).read(data)
