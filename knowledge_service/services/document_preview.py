"""Read-only upload previews, including individually selectable Markdown ZIP members."""
import base64
import hashlib
from html import escape
from pathlib import Path
import zipfile

from ..integrations.document_media import (
    MAX_DOCUMENT_IMAGES, MAX_DOCUMENT_IMAGE_BYTES, inspect_image,
)
from ..integrations.document_parser import DocumentParseError, MAX_TEXT_CHARS
from ..integrations.markdown_package import MarkdownPackageReader
from ..integrations.markdown_parser import parse_markdown
from ..integrations.video_asr import inspect_video, ASRError
from .chunking import split_document, split_source_blocks
from .document_temporal import document_time_preview, source_temporal_exclusions


def source_preview(entry):
    """Return sanitized article HTML with deduplicated, validated inline image bytes."""
    images, assets, warnings = [], {}, list(entry['warnings'])
    total_bytes = 0
    for index, asset in enumerate(entry['assets']):
        is_video=asset.get('provenance',{}).get('media_type')=='video'
        image = dict(index=index, alt=asset.get('alt', ''), url=asset['url'],
                     status='external' if asset['kind'] == 'external' else asset['status'])
        if is_video:
            image['media_type']='video'
            warnings.append(dict(code='video_asr_on_ingest',message='视频语音转写在正式处理时执行，预览不启动模型'))
        data = asset.get('data')
        if data is not None and index < MAX_DOCUMENT_IMAGES:
            try:
                total_bytes += len(data)
                if total_bytes > MAX_DOCUMENT_IMAGE_BYTES:
                    raise ValueError('Document image byte limit exceeded')
                details = inspect_video(data, asset.get('mime_type')) if is_video else inspect_image(data, asset.get('mime_type'))
                key = hashlib.sha256(data).hexdigest()
                if key not in assets:
                    assets[key] = dict(mime_type=details['mime_type'], base64=base64.b64encode(data).decode('ascii'))
                image.update(status='available', asset_key=key)
            except (ValueError, OSError, SyntaxError, ASRError) as exc:
                image.update(status='missing', message=str(exc))
                warnings.append(dict(code='source_asset_unreadable', path=asset.get('resolved_path', asset['url'])))
        elif data is not None:
            image.update(status='missing', message='图片数量超过文档上限')
            warnings.append(dict(code='source_asset_limit', path=asset['url']))
        images.append(image)
    return dict(html=entry['parsed']['rendered_html'], images=images, assets=assets), warnings


def chunk_preview(text, settings, parsed_metadata, *, chunks=None, excluded_ranges=()):
    chunks = split_document(text, settings) if chunks is None and text else chunks or []
    result=dict(total=len(chunks), chunks=chunks[:20], preview_limit=20,
                document_time=document_time_preview(text, settings, excluded_ranges=excluded_ranges),
                strategy=settings['chunk_strategy'], chunk_size=settings['chunk_size'],
                chunk_overlap=settings['chunk_overlap'], parsed=parsed_metadata)
    if parsed_metadata.get('source_format') in {'mp4','webm'} or parsed_metadata.get('video_count'):
        result['media_note']='视频语音转写在正式处理时执行，预览不启动 ASR，转写完成后生成带时间定位的切片。'
    return result


def parsed_source_entry(parsed):
    """Match converted document images without fetching external or local paths."""
    if parsed.provenance.get('media_type')=='video':
        markdown=parse_markdown('<video src="source-video"></video>')
    elif parsed.metadata.get('source_format') == 'txt':
        markdown = dict(rendered_html='<pre>' + escape(parsed.text) + '</pre>', images=[])
    else:
        markdown = parse_markdown(parsed.text)
    supplied = {asset.get('original_path', asset.get('path')): asset for asset in parsed.assets}
    if parsed.provenance.get('media_type')=='video' and parsed.assets:
        supplied={'source-video':dict(parsed.assets[0],url='source-video',path='source-video')}
    assets, warnings = [], list(parsed.metadata.get('warnings', []))
    for reference in markdown['images']:
        asset = {**reference, **supplied.get(reference.get('path', reference['url']), {})}
        if asset['kind'] == 'local' and 'data' not in asset:
            asset['status'] = 'missing'
            warnings.append(dict(code='source_asset_missing', path=reference.get('path', reference['url'])))
        assets.append(asset)
    return dict(parsed=markdown, assets=assets, warnings=warnings)


def preview_upload(uploads, upload, options, limits, package_path=None, preview_view='combined'):
    staged = uploads.stage(upload, allow_package=limits.markdown_package, max_bytes=limits.max_file_bytes)
    try:
        if Path(staged.original_name).suffix.lower() != '.zip':
            payload, parsed = uploads.payload(staged, options)
            if preview_view == 'source':
                result = dict(parsed=parsed.metadata, document_time=document_time_preview(payload['text'], payload))
            else:
                result = chunk_preview(payload['text'], payload, parsed.metadata)
            if preview_view == 'source':
                article, warnings = source_preview(parsed_source_entry(parsed))
                result.update(source_preview=article, warnings=warnings)
            return result
        try:
            package = MarkdownPackageReader(max_documents=limits.max_batch_items).read(
                staged.path.read_bytes(), staged.original_name)
        except (ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise DocumentParseError('invalid_package', 'ZIP 知识包无法解析，请检查目录、manifest 和文件编码。' + str(exc)) from exc
        documents = package['documents']
        for entry in documents:
            if len(entry['text']) > MAX_TEXT_CHARS or len(entry['text'].encode('utf-8')) > limits.max_file_bytes:
                raise DocumentParseError('file_too_large', 'ZIP 内文档超过正文或文件大小上限。' + entry['path'])
        selected_path = package_path or documents[0]['path']
        entry = next((document for document in documents if document['path'] == selected_path), None)
        if entry is None:
            raise DocumentParseError('package_document_not_found', 'ZIP 中找不到所选文档。' + selected_path)
        settings = options.model_dump()
        settings.update(title=entry['title'] or entry['path'], metadata={**options.metadata, **entry['metadata']})
        metadata = dict(source_format='md', source_file=entry['path'],
                        video_count=sum(asset.get('provenance',{}).get('media_type')=='video' for asset in entry['assets']))
        exclusions = source_temporal_exclusions(entry['parsed'])
        if preview_view == 'source':
            text = '\n\n'.join(('#' * block['payload'].get('level', 1) + ' ' if block['type'] == 'heading' else '')
                               + block['normalized_text'] for block in entry['parsed']['blocks'] if block['normalized_text'])
            result = dict(parsed=metadata, document_time=document_time_preview(text, settings, excluded_ranges=exclusions))
        else:
            chunks, text = split_source_blocks(entry['parsed']['blocks'], settings, return_text=True)
            result = chunk_preview(text, settings, metadata, chunks=chunks, excluded_ranges=exclusions)
        if preview_view != 'chunks':
            article, warnings = source_preview(entry)
            result.update(source_preview=article, warnings=warnings)
        result.update(title=settings['title'], metadata=settings['metadata'],
                      package=dict(filename=staged.original_name, selected_path=entry['path'],
                                   document_count=len(documents), image_count=sum(len(item['assets']) for item in documents),
                                   documents=[dict(path=item['path'], title=item['title'] or item['path'],
                                                   image_count=len(item['assets']), warning_count=len(item['warnings']))
                                              for item in documents]))
        return result
    finally:
        uploads.discard(staged)
