"""Source-preserving service splitters, shared by preview and ingestion."""
import re

from .models import Ingest


def structural_boundaries(text):
    numeral = r'[一二三四五六七八九十百千万零〇两\d]+'
    prefix = r'(?m)^[ \t]*(?:\*\*|__)?'
    primary = [m.start() for m in re.finditer(prefix + rf'(?:第{numeral}[章节条]|[一二三四五六七八九十百千万零〇两]+[、．.])', text)]
    headings = [m.start() for m in re.finditer(r'(?m)^[ \t]*#{1,6}[ \t]+', text)]
    # Numbered evidence lists stay with their enclosing Chinese clause.
    fallback = [] if primary else [m.start() for m in re.finditer(prefix + rf'(?:[（(]{numeral}[）)]|\d+[、．.]\s*)', text)]
    return sorted(set([0, *headings, *primary, *fallback, len(text)]))


def split_document(text, options):
    config = Ingest.model_validate({**options, 'text': text, 'title': options.get('title', '预览')})
    size, overlap, strategy = config.chunk_size, config.chunk_overlap, config.chunk_strategy
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
