"""保留来源的服务端切片器，预览与摄取共用。"""
import logging
import re

from ..models import Ingest

LOG = logging.getLogger('knowledge_service.chunking')


def structural_boundaries(text):
    numeral = r'[一二三四五六七八九十百千万零〇两\d]+'
    prefix = r'(?m)^[ \t]*(?:\*\*|__)?'
    primary = [m.start() for m in re.finditer(prefix + rf'(?:第{numeral}[章节条]|[一二三四五六七八九十百千万零〇两]+[、．.])', text)]
    headings = [m.start() for m in re.finditer(r'(?m)^[ \t]*#{1,6}[ \t]+', text)]
    # 编号证据列表与其所在的中文分句保持在一起。
    fallback = [] if primary else [m.start() for m in re.finditer(prefix + rf'(?:[（(]{numeral}[）)]|\d+[、．.]\s*)', text)]
    return sorted(set([0, *headings, *primary, *fallback, len(text)]))


def split_document(text, options):
    config = Ingest.model_validate({**{k:v for k,v in options.items() if k in Ingest.model_fields}, 'text': text, 'title': options.get('title', '预览')})
    size, overlap, strategy = config.chunk_size, config.chunk_overlap, config.chunk_strategy
    LOG.debug('切片：策略=%s 字符数=%d 块大小=%d 重叠=%d', strategy, len(text), size, overlap)
    if strategy == 'structural':
        boundaries = structural_boundaries(text)
        rows = []
        for start, stop in zip(boundaries, boundaries[1:]):
            while start < stop:
                end = min(start + size, stop)
                rows.append({'text': text[start:end], 'start_char': start, 'end_char': end})
                if end == stop:
                    break
                start = end - overlap
        return rows
    boundaries = []
    if strategy == 'paragraph':
        boundaries = [m.end() for m in re.finditer(r'\n\s*\n|[。！？!?；;]\s*', text)]
    rows, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text) and boundaries:
            choices = [b for b in boundaries if start + overlap < b <= end]
            if choices:
                end = choices[-1]
        rows.append({'text': text[start:end], 'start_char': start, 'end_char': end})
        if end == len(text):
            break
        start = end - overlap
    return rows


def split_mapped_chunks(chunks, options, return_text=False):
    """Apply normal ingest windows while retaining every intersecting source span."""
    text_parts, spans, segments, offset = [], [], [], 0
    for chunk in chunks:
        text_parts.append(chunk['text'])
        segments.append((offset, offset + len(chunk['text']), chunk.get('metadata', {})))
        for span in chunk.get('source_spans', []):
            spans.append({**span, 'chunk_start_char': offset + span['chunk_start_char'],
                          'chunk_end_char': offset + span['chunk_end_char']})
        offset += len(chunk['text']) + 2
    text = '\n\n'.join(text_parts)
    rows = []
    for row in split_document(text, options):
        mapped = []
        for span in spans:
            start = max(row['start_char'], span['chunk_start_char'])
            end = min(row['end_char'], span['chunk_end_char'])
            if start >= end:
                continue
            mapped.append({**span, 'chunk_start_char': start - row['start_char'],
                           'chunk_end_char': end - row['start_char'],
                           'block_start_char': span['block_start_char'] + start - span['chunk_start_char'],
                           'block_end_char': span['block_start_char'] + end - span['chunk_start_char']})
        intersecting = [metadata for start, end, metadata in segments
                        if start < row['end_char'] and end > row['start_char']]
        current_metadata = dict(intersecting[0]) if intersecting else {}
        paths = [metadata['heading_path'] for metadata in intersecting if metadata.get('heading_path')]
        if paths:
            current_metadata['heading_paths'] = paths
        if current_metadata.get('source_kind') == 'image_ocr':
            current_metadata['ocr_block_ids'] = list(dict.fromkeys(s['ocr_block_id'] for s in mapped))
        rows.append({**row, 'source_spans': mapped, 'metadata': current_metadata})
    return (rows, text) if return_text else rows


def split_source_blocks(blocks, options, return_text=False):
    """Normal windows span prose blocks; tables retain row-aware special windows."""
    from ..integrations.markdown_parser import chunk_markdown_blocks
    rows, pending, text_parts = [], [], []
    stream_offset = 0
    def flush():
        nonlocal stream_offset
        if pending:
            mapped, text = split_mapped_chunks(pending, options, return_text=True)
            for row in mapped:
                row['start_char'] += stream_offset
                row['end_char'] += stream_offset
            rows.extend(mapped)
            text_parts.append(text)
            stream_offset += len(text) + 2
            pending.clear()
    for block in blocks:
        if block['type'] == 'table':
            flush()
            table_rows = chunk_markdown_blocks({'blocks': [block]}, options.get('chunk_size', 1800))
            header_length = len(' | '.join(block['payload']['headers']))
            for row in table_rows:
                span = row['source_spans'][0]
                row['start_char'] = stream_offset + span['block_start_char']
                row['end_char'] = stream_offset + span['block_end_char']
                if span['payload'].get('repeated_header'):
                    # The repeated header comes from the original header, not the row offset.
                    row['source_spans'] = [
                        {**span, 'chunk_end_char': header_length, 'block_start_char': 0,
                         'block_end_char': header_length, 'payload': {'table_header': True, 'repeated_header': True}},
                        {**span, 'chunk_start_char': header_length + 1}]
            rows.extend(table_rows)
            text_parts.append(block['normalized_text'])
            stream_offset += len(block['normalized_text']) + 2
            continue
        text = block['normalized_text']
        if not text:
            continue
        prefix = '#' * block['payload'].get('level', 1) + ' ' if block['type'] == 'heading' else ''
        pending.append(dict(text=prefix + text,
            source_spans=[dict(block_id=block['block_id'], chunk_start_char=len(prefix),
                chunk_end_char=len(prefix) + len(text), block_start_char=0, block_end_char=len(text), payload={})],
            metadata=dict(source_kind='markdown', source_document_version_id=block['document_version_id'],
                          heading_path=block['heading_path'])))
    flush()
    for ordinal, row in enumerate(rows):
        row.update(id='md-' + str(ordinal), ordinal=ordinal)
    return (rows, '\n\n'.join(text_parts)) if return_text else rows
