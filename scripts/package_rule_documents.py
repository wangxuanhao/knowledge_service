"""Package selected Markdown and referenced image/video assets into uploadable ZIPs."""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import posixpath
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowledge_service.integrations.document_media import (  # noqa: E402
    IMAGE_MIMES, MAX_DOCUMENT_IMAGE_BYTES, MAX_DOCUMENT_IMAGES, MAX_IMAGE_BYTES, inspect_image,
)
from knowledge_service.integrations.document_parser import MAX_TEXT_CHARS, MAX_UPLOAD_BYTES  # noqa: E402
from knowledge_service.integrations.markdown_package import MarkdownPackageReader, safe_path  # noqa: E402
from knowledge_service.integrations.markdown_parser import parse_markdown  # noqa: E402
from knowledge_service.integrations.video_asr import inspect_video, ASRError, MAX_VIDEO_BYTES  # noqa: E402


MANIFEST_START = b'{"documents":['
MANIFEST_END = b']}'


def reject_links(path):
    for parent in (path, *path.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError(f'Symlink/junction paths are prohibited: {path}')


def source_path(root, name):
    name = safe_path(name)
    source = root / name
    reject_links(source)
    if not source.is_file():
        raise ValueError(f'Source file is missing: {name}')
    return source


def file_signature(info):
    # Windows stat/fstat can disagree on ctime for an unchanged file.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def read_source(root, name, max_bytes):
    source = source_path(root, name)
    before = file_signature(source.stat())
    if before[2] > max_bytes:
        raise ValueError(f'File exceeds byte limit ({max_bytes}): {name}')
    with source.open('rb') as stream:
        if file_signature(os.fstat(stream.fileno())) != before:
            raise ValueError(f'Source changed while reading: {name}')
        data = stream.read(max_bytes + 1)
        after = file_signature(os.fstat(stream.fileno()))
    if len(data) > max_bytes or before != after or file_signature(source.stat()) != before:
        raise ValueError(f'Source changed while reading: {name}')
    return dict(path=name, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                signature=before), data


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def select_documents(args):
    if args.manifest is not None:
        if args.selection:
            raise ValueError('--manifest cannot be combined with --file or --list-file')
        if args.manifest.stat().st_size > MAX_UPLOAD_BYTES:
            raise ValueError('Input manifest exceeds byte limit')
        manifest = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
        if not isinstance(manifest, dict) or not isinstance(manifest.get('documents'), list):
            raise ValueError('manifest.documents must be a list')
        entries = manifest['documents']
    else:
        entries = []
        for selection in args.selection or []:
            if isinstance(selection, Path):
                with selection.open(encoding='utf-8-sig') as stream:
                    entries.extend(line.strip() for line in stream
                                   if line.strip() and not line.lstrip().startswith('#'))
            else:
                entries.append(selection)
    selected, seen = [], set()
    for entry in entries:
        if isinstance(entry, str):
            entry = {'path': entry}
        if not isinstance(entry, dict):
            raise ValueError('Manifest document must be a path string or object')
        original = entry.get('path')
        try:
            path = safe_path(original)
            if not path.lower().endswith('.md'):
                raise ValueError('Selected document must have a .md extension')
            if not isinstance(entry.get('metadata', {}), dict):
                raise ValueError('Manifest document metadata must be an object')
            if 'title' in entry and (not isinstance(entry['title'], str) or
                                    not entry['title'].strip() or len(entry['title']) > 300):
                raise ValueError('Manifest title must be a nonempty string of at most 300 characters')
            normalized = {'path': path}
            for key in ('title', 'metadata'):
                if key in entry:
                    normalized[key] = entry[key]
            json_bytes(normalized)
        except (ValueError, TypeError) as exc:
            raise ValueError(f'Markdown {original!r}: {exc}') from exc
        if path not in seen:
            seen.add(path)
            selected.append(normalized)
    if not selected:
        raise ValueError('Select at least one Markdown document')
    return selected


def prepare_document(root, entry, files, warnings):
    path = entry['path']
    try:
        record, data = read_source(root, path, min(MAX_UPLOAD_BYTES, MAX_TEXT_CHARS * 4 + 3))
        text = data.decode('utf-8-sig')
        if not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise ValueError(f'Markdown must contain text and fit the {MAX_TEXT_CHARS} character limit')
        images = parse_markdown(text)['images']
        if len(images) > MAX_DOCUMENT_IMAGES:
            raise ValueError(f'Document image count exceeds limit ({MAX_DOCUMENT_IMAGES})')
        files[path] = record
        members = {path: record}
        total_image_bytes = 0
        for image in images:
            reference = image['url']
            is_video=image.get('provenance',{}).get('media_type')=='video'
            try:
                if image['kind'] == 'external':
                    warnings.append(dict(document=path, url=reference, code='external_video' if is_video else 'external_image'))
                    continue
                if image['kind'] == 'unsafe':
                    raise ValueError('Unsafe image path')
                if image['kind'] == 'base64':
                    inspect_image(image['data'], image['mime_type'])
                    total_image_bytes += len(image['data'])
                else:
                    relative = image['path'].replace('\\', '/')
                    # Check the original route too, before normalizing away any .. components.
                    reject_links(root / posixpath.dirname(path) / relative)
                    resolved = safe_path(posixpath.normpath(posixpath.join(posixpath.dirname(path), relative)))
                    mime = mimetypes.guess_type(resolved)[0]
                    if mime not in ({'video/mp4','video/webm'} if is_video else IMAGE_MIMES.values()):
                        raise ValueError('Unsupported image format')
                    if resolved not in files:
                        asset, image_data = read_source(root, resolved, MAX_VIDEO_BYTES if is_video else MAX_IMAGE_BYTES)
                        if is_video:inspect_video(image_data,mime)
                        else:inspect_image(image_data, mime)
                        files[resolved] = asset
                    members[resolved] = files[resolved]
                    total_image_bytes += files[resolved]['size_bytes']
                if total_image_bytes > MAX_DOCUMENT_IMAGE_BYTES:
                    raise ValueError(f'Document image bytes exceed limit ({MAX_DOCUMENT_IMAGE_BYTES})')
            except (ValueError, OSError, SyntaxError, ImportError, ASRError) as exc:
                raise ValueError(f'{"Video" if is_video else "Image"} {reference!r}: {exc}') from exc
    except (ValueError, OSError, SyntaxError, ImportError) as exc:
        raise ValueError(f'Markdown {path!r}: {exc}') from exc
    return dict(entry=entry, encoded_entry=json_bytes(entry), files=members)


def member_size(name, size):
    """Exact local-header + central-directory + payload size for our stored entries."""
    return 76 + 2 * len(name.encode('utf-8')) + size


def write_package(root, stage, number, documents, files, expected_size, reader):
    name = f'rule-documents-{number:03d}.zip'
    target = stage / name
    manifest = MANIFEST_START + b','.join(doc['encoded_entry'] for doc in documents) + MANIFEST_END
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        archive.writestr('manifest.json', manifest)
        for path, record in files.items():
            source = source_path(root, path)
            if file_signature(source.stat()) != record['signature']:
                raise ValueError(f'Source changed before packaging: {path}')
            digest, size = hashlib.sha256(), 0
            with source.open('rb') as stream, archive.open(path, 'w') as member:
                before = file_signature(os.fstat(stream.fileno()))
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > record['size_bytes']:
                        raise ValueError(f'Source changed while packaging: {path}')
                    digest.update(chunk)
                    member.write(chunk)
                after = file_signature(os.fstat(stream.fileno()))
            if (before != record['signature'] or after != before or
                    file_signature(source.stat()) != before or size != record['size_bytes'] or
                    digest.hexdigest() != record['sha256']):
                raise ValueError(f'Source changed while packaging: {path}')
    if target.stat().st_size != expected_size:
        raise ValueError(f'ZIP size calculation mismatch: {name}')
    data = target.read_bytes()
    package = reader.read(data, name)
    for document in package['documents']:
        for image in document['assets']:
            if image['kind'] == 'local' and image['status'] != 'available':
                raise ValueError(f"Markdown {document['path']!r}: unavailable image {image['url']!r}")
    paths = [doc['entry']['path'] for doc in documents]
    return dict(filename=name, documents=paths, images=[path for path in files if path not in paths and not path.lower().endswith(('.mp4','.webm'))],
                videos=[path for path in files if path not in paths and path.lower().endswith(('.mp4','.webm'))],
                size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def package_documents(args):
    root = args.root.expanduser().absolute()
    reject_links(root)
    if not root.is_dir():
        raise ValueError(f'Source root is not a directory: {root}')
    root = root.resolve()
    output = args.output_dir.expanduser().absolute()
    if output.resolve().is_relative_to(root):
        raise ValueError('Output directory must be outside the source root')
    if os.path.lexists(output):
        raise ValueError(f'Output directory already exists: {output}')
    if not 1 <= args.max_documents <= 1000:
        raise ValueError('--max-documents must be between 1 and 1000')
    if not 1 <= args.max_zip_bytes <= MAX_UPLOAD_BYTES:
        raise ValueError(f'--max-zip-bytes must be between 1 and {MAX_UPLOAD_BYTES}')
    entries = select_documents(args)
    output.parent.mkdir(parents=True, exist_ok=True)
    reader = MarkdownPackageReader()
    files, warnings, packages = {}, [], []
    documents, current_files = [], {}
    payload_size = 0
    manifest_size = len(MANIFEST_START) + len(MANIFEST_END)
    with tempfile.TemporaryDirectory(prefix=f'.{output.name}-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'complete'
        stage.mkdir()
        current_size = 0
        for entry in entries:
            document = prepare_document(root, entry, files, warnings)
            new_files = {path: info for path, info in document['files'].items() if path not in current_files}
            added_size = sum(member_size(path, info['size_bytes']) for path, info in new_files.items())
            next_manifest_size = manifest_size + len(document['encoded_entry']) + bool(documents)
            next_size = 22 + member_size('manifest.json', next_manifest_size) + payload_size + added_size
            if documents and (len(documents) >= args.max_documents or next_size > args.max_zip_bytes or
                              len(current_files) + len(new_files) + 1 > reader.max_files):
                packages.append(write_package(root, stage, len(packages) + 1, documents,
                                              current_files, current_size, reader))
                documents, current_files = [], {}
                payload_size = 0
                new_files = document['files']
                added_size = sum(member_size(path, info['size_bytes']) for path, info in new_files.items())
                next_manifest_size = len(MANIFEST_START) + len(document['encoded_entry']) + len(MANIFEST_END)
                next_size = 22 + member_size('manifest.json', next_manifest_size) + added_size
            if next_size > args.max_zip_bytes or len(new_files) + len(current_files) + 1 > reader.max_files:
                raise ValueError(f"Markdown {entry['path']!r}: document and images exceed package limit")
            documents.append(document)
            current_files.update(new_files)
            payload_size += added_size
            manifest_size, current_size = next_manifest_size, next_size
        packages.append(write_package(root, stage, len(packages) + 1, documents,
                                      current_files, current_size, reader))
        receipt = dict(source_root=str(root), max_documents=args.max_documents,
                       max_zip_bytes=args.max_zip_bytes, packages=packages, warnings=warnings,
                       files=[{key: info[key] for key in ('path', 'size_bytes', 'sha256')} for info in files.values()])
        (stage / 'packaging-report.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n',
                                                      encoding='utf-8')
        if os.path.lexists(output):
            raise ValueError(f'Output directory already exists: {output}')
        stage.rename(output)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='Read-only source directory')
    parser.add_argument('--output-dir', type=Path, required=True, help='New directory outside --root')
    parser.add_argument('--file', dest='selection', action='append', help='Relative Markdown path, repeatable')
    parser.add_argument('--list-file', dest='selection', action='append', type=Path,
                        help='UTF-8/BOM path list, blank/# lines ignored, selectors follow CLI order')
    parser.add_argument('--manifest', type=Path, help='JSON documents manifest, exclusive with file/list selectors')
    parser.add_argument('--max-documents', type=int, default=200, help='Documents per ZIP, 1..1000 (default 200)')
    parser.add_argument('--max-zip-bytes', type=int, default=24_000_000,
                        help='Stored ZIP byte limit, 1..25000000 (default 24000000)')
    args = parser.parse_args(argv)
    try:
        receipt = package_documents(args)
    except (ValueError, OSError, TypeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        parser.exit(2, f'error: {exc}\n')
    print(f"Created {len(receipt['packages'])} ZIP package(s) in {args.output_dir}")
    for warning in receipt['warnings']:
        print(f"warning: external image kept as URL in {warning['document']}: {warning['url']}", file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
