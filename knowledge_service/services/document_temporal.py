"""Conservative, deterministic document dates with normalized-search-text evidence.

Only explicit leading metadata and clauses about this document are eligible.
Offsets refer to the supplied search text, including any appended OCR text. The
immutable source version and its existing span maps remain the reading authority.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import re
from zoneinfo import ZoneInfo

from ..core.time import normalize_time


RESOLVER_VERSION = 'document-temporal-v1'
TIMEZONE = 'Asia/Shanghai'
_DATE = (r'\d{4}(?:年\d{1,2}月\d{1,2}日|[-/]\d{1,2}[-/]\d{1,2})'
         r'(?:[T \t]?\d{1,2}[:：]\d{2}(?:[:：]\d{2}(?:\.\d{1,6})?)?'
         r'(?:Z|[+-]\d{2}:?\d{2})?)?')
_DATES = re.compile(_DATE)
_FIELDS = ('published_at', 'valid_from', 'valid_until', 'revised_at')
_HEADERS = {'发布日期': 'published_at', '发布时间': 'published_at',
            '生效日期': 'valid_from', '生效时间': 'valid_from',
            '失效日期': 'valid_until', '失效时间': 'valid_until',
            '修订日期': 'revised_at', '修订时间': 'revised_at'}
_HEADER = re.compile(r'^[ \t]*(?:[-*]\s+)?(?:\*\*)?('
                     + '|'.join(_HEADERS) + r'|是否生效)(?:\*\*)?\s*[：:]\s*(?:\*\*)?')
_SUBJECT = r'本(?:规则|表|规范|办法|细则|制度|公告|通知|规定|指引|协议|标准|说明)'


def _parse_date(raw, inclusive=False):
    value = raw.replace('：', ':').replace('年', '-').replace('月', '-').replace('日', '').replace('/', '-')
    match = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})(.*)$', value)
    year, month, day, clock = match.groups()
    value = f'{int(year):04d}-{int(month):02d}-{int(day):02d}{clock}'
    if clock and clock[0].isdigit():
        value = value[:10] + 'T' + clock
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    zone = TIMEZONE
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(TIMEZONE))
    else:
        zone = 'UTC' if parsed.utcoffset() == timedelta(0) else parsed.strftime('%z')
        if zone != 'UTC':
            zone = zone[:3] + ':' + zone[3:]
    if inclusive and not clock:
        parsed += timedelta(days=1)
    return normalize_time(parsed), zone


def _eligible_text(text):
    """Mask examples, code and quotation without changing character positions."""
    pattern = re.compile(r'```[^\n]*\n[\s\S]*?(?:```|\Z)|~~~[^\n]*\n[\s\S]*?(?:~~~|\Z)'
                         r'|`[^`\n]*`|“[^“”]*”|‘[^‘’]*’|"[^"]*"|\'[^\']*\''
                         r'|^[ \t]*>[^\n]*|^(?: {4}|\t)[^\n]*'
                         r'|^[^\n]*(?:例如|示例|举例|样例)[^\n]*', re.MULTILINE)
    return pattern.sub(lambda match: ''.join('\n' if char == '\n' else ' ' for char in match[0]), text)


def source_temporal_exclusions(source):
    """Map exclusions into original block text, before window/header repetition."""
    from ..integrations.markdown_parser import MarkdownParser, temporal_excluded_ranges

    excluded = []
    parser = None
    offset = 0
    for block in source['blocks']:
        text = block['normalized_text']
        if not text:
            continue
        prefix = '#' * block['payload'].get('level', 1) + ' ' if block['type'] == 'heading' else ''
        if block['type'] in {'code', 'blockquote'}:
            ranges = [[0, len(text)]]
        elif 'temporal_excluded_ranges' in block['payload']:
            ranges = block['payload']['temporal_excluded_ranges']
        else:
            # Sources frozen before parser 1.1 retain their original Markdown.
            parser = parser or MarkdownParser()
            raw = source['source_text'][block['source_start_char']:block['source_end_char']]
            ranges = temporal_excluded_ranges(parser.md.parse(raw), text)
        excluded.extend((offset + len(prefix) + start, offset + len(prefix) + end) for start, end in ranges)
        offset += len(prefix) + len(text) + 2
    return excluded


def _candidates(text, supplied_title, excluded_ranges):
    parts, cursor = [], 0
    for start, end in sorted(excluded_ranges):
        start, end = max(cursor, start), min(len(text), end)
        if start < end:
            parts.extend((text[cursor:start], ''.join('\n' if char == '\n' else ' ' for char in text[start:end])))
            cursor = end
    parts.append(text[cursor:])
    masked = _eligible_text(''.join(parts))
    result = {name: [] for name in _FIELDS}
    source_status = None

    def add(name, match, kind, inclusive=False, base=0):
        start, end = base + match.start(), base + match.end()
        raw = text[start:end]
        candidate = {'raw': raw, 'start_char': start, 'end_char': end, 'kind': kind,
                     'value': None, 'timezone': TIMEZONE}
        try:
            candidate['value'], candidate['timezone'] = _parse_date(raw, inclusive)
        except (ValueError, OverflowError):
            candidate['error'] = 'invalid_date'
        if inclusive:
            candidate['end_semantics'] = 'inclusive_day'
        if not any(item['start_char'] == start and item['end_char'] == end for item in result[name]):
            result[name].append(candidate)

    # Header fields are accepted only in the leading metadata area.
    offset = 0
    for line in masked[:2000].splitlines(keepends=True)[:20]:
        if re.match(r'^\s*##\s', line):
            break
        header = _HEADER.match(line)
        if header:
            if header[1] == '是否生效':
                raw = text[offset + header.end():offset + len(line)].strip().rstrip('*').rstrip()
                start = text.find(raw, offset + header.end(), offset + len(line))
                source_status = {'raw': raw, 'start_char': start, 'end_char': start + len(raw)}
            else:
                for date in _DATES.finditer(line, header.end()):
                    add(_HEADERS[header[1]], date, 'header', base=offset)
        offset += len(line)

    title = re.search(r'^#\s+([^\n]+)', masked[:2000], re.MULTILINE)
    notice = bool(re.search(r'通知|公告|公示', title[1] if title else supplied_title))
    subject = r'本(?:公告|通知)' if notice else _SUBJECT
    own_clause = re.compile(subject + r'\s*(?:的)?\s*(?:将|已)?\s*(?=于|自|有效)')
    for own in own_clause.finditer(masked):
        # A foreign titled rule ends this document's clause ownership.
        tail = re.split(r'[。；;\n《]', masked[own.end():own.end() + 240], maxsplit=1)[0]
        previous_end = 0
        for date in _DATES.finditer(tail):
            prefix = tail[previous_end:date.start()]
            # A continuation can omit the subject only immediately after the
            # prior date's action and comma. A new noun ends that ownership.
            prefix = re.sub(r'^[^，,]*[，,]\s*', '', prefix, count=1)
            suffix = tail[date.end():]
            kind = None
            name = None
            inclusive = False
            if re.fullmatch(r'有效至\s*', prefix):
                name, kind, inclusive = 'valid_until', 'valid_through', True
            elif re.fullmatch(r'有效期(?:为|自)?\s*', prefix):
                name, kind = 'valid_from', 'validity_range'
            elif re.fullmatch(r'(?:至|到)\s*', prefix) and '有效期' in tail[:date.start()]:
                name, kind, inclusive = 'valid_until', 'validity_range', True
            elif re.fullmatch(r'(?:于|自)\s*', prefix):
                effective = re.match(r'\s*(?:起|开始)?\s*(最新修订|最新|首次|正式)?(?:施行|实施|生效|执行|试行)', suffix)
                if effective:
                    name = 'valid_from'
                    kind = {'首次': 'first_effective', '最新修订': 'latest_effective',
                            '最新': 'latest_effective'}.get(effective[1], 'self_effective')
                elif re.match(r'\s*(?:起)?(?:正式)?(?:失效|废止|终止)', suffix):
                    name, kind = 'valid_until', 'self_expiry'
                elif re.match(r'\s*(?:进行)?(?:修订|修正)', suffix):
                    name, kind = 'revised_at', 'self_revision'
            if name:
                add(name, date, kind, inclusive, own.end())
            else:
                break
            previous_end = date.end()

    # Only the document's own H1 may carry the explicit expired title marker.
    if title:
        marker = re.search(r'[\[【](' + _DATE + r')\s*已失效[\]】]', title[1])
        if marker:
            date = _DATES.match(text, title.start(1) + marker.start(1))
            add('valid_until', date, 'title_expiry')
    for candidates in result.values():
        candidates.sort(key=lambda item: (item['start_char'], item['end_char']))
    return result, source_status


def resolve_document_temporal(text: str, settings: dict, *, excluded_ranges=()) -> dict:
    """Return resolved settings without changing input or consulting wall time."""
    output = dict(settings)
    metadata = deepcopy(settings.get('metadata') or {})
    output['metadata'] = metadata
    if settings.get('document_time_mode', 'manual') != 'auto':
        return output
    previous = metadata.get('document_temporal')
    previous_fields = previous.get('fields') if isinstance(previous, dict) else None
    if not isinstance(previous_fields, dict):
        previous_fields = {}
    candidates, source_status = _candidates(text, settings.get('title') or '', excluded_ranges)
    temporal = {'resolver_version': RESOLVER_VERSION, 'text_sha256': sha256(text.encode()).hexdigest(),
                'coordinate_basis': 'normalized_search_text', 'timezone': TIMEZONE,
                'fields': {}, 'warnings': []}
    for name in _FIELDS:
        supplied = settings.get(name)
        if supplied is None and name in ('published_at', 'revised_at'):
            supplied = metadata.get(name)
        prior = previous_fields.get(name)
        if name == 'revised_at' and supplied is None and isinstance(prior, dict):
            supplied = prior.get('supplied_value')
        invalid_revision = False
        try:
            supplied = normalize_time(supplied)
        except ValueError:
            if name != 'revised_at':
                raise
            invalid_revision = True
            raw_revision = supplied
            supplied = None
            temporal['warnings'].append('invalid_legacy_revised_at')
        # A prior automatic choice is evidence, not a newly supplied override.
        if (isinstance(prior, dict) and prior.get('status') != 'manual'
                and prior.get('value') == supplied):
            supplied = None
        choices = candidates[name]
        eligible = [item for item in choices if item['kind'] != 'first_effective']
        if not eligible:
            eligible = choices
        values = {item['value'] for item in eligible if item['value'] is not None}
        needs_review = invalid_revision or len(values) > 1 or any(item.get('error') for item in eligible)
        value = supplied or (next(iter(values)) if len(values) == 1 and not needs_review else None)
        status = 'manual' if supplied else 'review' if needs_review else 'extracted' if value else 'unknown'
        temporal['fields'][name] = {'value': value, 'status': status, 'candidates': choices[:40]}
        if invalid_revision:
            temporal['fields'][name]['supplied_value'] = raw_revision
        if len(choices) > 40:
            temporal['warnings'].append(f'{name}_candidate_limit')
            if not supplied:
                temporal['fields'][name].update(value=None, status='review')
    fields = temporal['fields']
    start, end = fields['valid_from'], fields['valid_until']
    if start['value'] and end['value'] and start['value'] >= end['value']:
        if start['status'] == end['status'] == 'manual':
            raise ValueError('失效时间必须晚于生效时间')
        temporal['warnings'].append('reversed_interval')
        for boundary in (start, end):
            if boundary['status'] != 'manual':
                boundary.update(value=None, status='review')
    if source_status:
        temporal['source_status'] = source_status
        if re.search(r'失效|废止|下线', source_status['raw']) and not end['value']:
            temporal['warnings'].append('expired_status_without_date')
        if (source_status['raw'] == '生效中'
                and any(item['kind'] == 'title_expiry' for item in end['candidates'])):
            temporal['warnings'].append('title_status_conflict')
    for name in ('published_at', 'valid_from', 'valid_until'):
        output[name] = fields[name]['value']
    for name in ('published_at', 'revised_at'):
        if fields[name]['value']:
            metadata[name] = fields[name]['value']
        else:
            metadata.pop(name, None)
    metadata['effective_time_source'] = {'manual': 'explicit', 'extracted': 'extracted'}.get(start['status'], 'unknown')
    metadata['document_temporal'] = temporal
    return output


def document_time_preview(text: str, settings: dict, *, excluded_ranges=()) -> dict:
    resolved = resolve_document_temporal(text, settings, excluded_ranges=excluded_ranges)
    return {**{key: resolved.get(key) for key in ('published_at', 'valid_from', 'valid_until')},
            **{key: resolved.get('metadata', {}).get(key)
               for key in ('effective_time_source', 'document_temporal')}}
