"""Resolve formal facts and assertions to immutable source evidence."""

import hashlib


_WARNING_MESSAGES = {
    'document_version_missing': '断言固定的文档版本不存在。',
    'document_version_mismatch': '断言固定版本不是所声明的来源文档。',
    'chunk_history_missing': '断言所引用的片段没有历史记录。',
    'chunk_history_mismatch': '片段历史与断言固定的来源文档版本不匹配。',
    'chunk_history_ambiguous': '多个片段历史版本匹配断言来源，无法安全选择。',
    'legacy_chunk_version_assumed': '旧片段未记录来源版本，已使用唯一的来源匹配历史。',
    'chunk_bounds_invalid': '片段保存的字符范围无效或超出固定文档版本。',
    'chunk_text_mismatch': '片段文本与固定文档版本中的字符范围不一致。',
    'source_hash_unavailable': '断言没有保存来源文档哈希，无法校验哈希完整性。',
    'source_hash_mismatch': '断言来源哈希与固定文档版本不一致。',
}


def _warning(code):
    return {'code': code, 'message': _WARNING_MESSAGES[code]}


def _unlocated():
    return {
        'mode': 'unlocated', 'start_char': None, 'end_char': None,
        'before': '', 'highlight': '', 'after': '',
    }


def _base_result(assertion):
    return {
        'assertion_id': assertion['id'],
        'status': assertion['status'],
        'kind': assertion['kind'],
        'document': {
            'id': assertion.get('document_id'),
            'version': None,
            'version_id': assertion.get('document_version_id'),
            'title': assertion.get('document_id'),
        },
        'chunk': {
            'id': assertion.get('chunk_id'),
            'start_char': None,
            'end_char': None,
            'text': None,
        },
        'location': _unlocated(),
        'integrity': {
            'complete': False,
            'source_hash_status': 'unavailable',
            'warnings': [],
        },
    }


def _occurrences(text, needle):
    if not isinstance(needle, str) or not needle:
        return []
    positions = []
    cursor = 0
    while True:
        position = text.find(needle, cursor)
        if position < 0:
            return positions
        positions.append(position)
        cursor = position + 1


def _unique_span(text, needle):
    positions = _occurrences(text, needle)
    if len(positions) != 1:
        return None
    return positions[0], positions[0] + len(needle)


def _relation_span(text, payload):
    subject = payload.get('subject') or payload.get('subject_text')
    object_ = payload.get('object') or payload.get('object_text')
    if not all(isinstance(value, str) and value for value in (subject, object_)):
        return None
    spans = set()
    for subject_start in _occurrences(text, subject):
        subject_end = subject_start + len(subject)
        for object_start in _occurrences(text, object_):
            object_end = object_start + len(object_)
            spans.add((min(subject_start, object_start), max(subject_end, object_end)))
    if not spans:
        return None
    minimum = min(end - start for start, end in spans)
    shortest = sorted(span for span in spans if span[1] - span[0] == minimum)
    return shortest[0] if len(shortest) == 1 else None


def _payload(assertion):
    payload = assertion.get('payload')
    return payload if isinstance(payload, dict) else {}


def _payload_metadata(assertion):
    metadata = _payload(assertion).get('metadata')
    return metadata if isinstance(metadata, dict) else {}


def _preferred_evidence(assertion):
    payload = _payload(assertion)
    metadata = _payload_metadata(assertion)
    if assertion['kind'] == 'attribute':
        return (payload.get('attribute_evidence') or metadata.get('attribute_evidence')
                or payload.get('evidence') or metadata.get('evidence'))
    return (payload.get('evidence') or metadata.get('evidence')
            or payload.get('text'))


def _legacy_span(assertion, chunk_text):
    payload = _payload(assertion)
    if assertion['kind'] == 'relation':
        return _relation_span(chunk_text, payload)
    if assertion['kind'] == 'attribute':
        needle = payload.get('attribute_evidence') or payload.get('evidence')
    else:
        needle = payload.get('text') or payload.get('evidence')
    return _unique_span(chunk_text, needle)


def _located(mode, chunk_text, chunk_start, local_start, local_end):
    return {
        'mode': mode,
        'start_char': chunk_start + local_start,
        'end_char': chunk_start + local_end,
        'before': chunk_text[:local_start],
        'highlight': chunk_text[local_start:local_end],
        'after': chunk_text[local_end:],
    }


def _apply_hash_integrity(result, assertion, document, chunk_complete):
    source_hash = assertion.get('source_hash')
    if not isinstance(source_hash, str) or not source_hash:
        result['integrity']['source_hash_status'] = 'unavailable'
        result['integrity']['warnings'].append(_warning('source_hash_unavailable'))
        hash_complete = True
    elif hashlib.sha256(document['text'].encode('utf-8')).hexdigest() != source_hash:
        result['integrity']['source_hash_status'] = 'mismatched'
        result['integrity']['warnings'].append(_warning('source_hash_mismatch'))
        hash_complete = False
    else:
        result['integrity']['source_hash_status'] = 'matched'
        hash_complete = True
    result['integrity']['complete'] = chunk_complete and hash_complete


def _select_chunk(repository, project_id, assertion, document, warnings):
    chunk_id = assertion.get('chunk_id')
    if not isinstance(chunk_id, str) or not chunk_id:
        warnings.append(_warning('chunk_history_missing'))
        return None
    history = repository.history(project_id, chunk_id)
    if not history:
        warnings.append(_warning('chunk_history_missing'))
        return None
    source_matches = [
        chunk for chunk in history
        if chunk.get('kind') == 'chunk'
        and chunk.get('source_id') == assertion.get('document_id')]
    exact = []
    for chunk in source_matches:
        if chunk.get('recorded_at') == document.get('recorded_at'):
            exact.append(chunk)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        warnings.append(_warning('chunk_history_ambiguous'))
        return None
    legacy = []
    for chunk in source_matches:
        metadata = chunk.get('metadata')
        metadata = metadata if isinstance(metadata, dict) else {}
        if not metadata.get('source_version_id'):
            legacy.append(chunk)
    if len(source_matches) == 1 and len(legacy) == 1:
        warnings.append(_warning('legacy_chunk_version_assumed'))
        return source_matches[0]
    warnings.append(_warning(
        'chunk_history_ambiguous' if len(source_matches) > 1
        else 'chunk_history_mismatch'))
    return None


def _resolve_assertion_evidence(repository, project_id, assertion):
    """Resolve an already project-scoped assertion without mutable-state fallback."""
    result = _base_result(assertion)
    warnings = result['integrity']['warnings']
    version_id = assertion.get('document_version_id')
    try:
        document = repository.get_record_version(project_id, version_id)
    except KeyError:
        warnings.append(_warning('document_version_missing'))
        return result
    if (document.get('kind') != 'document'
            or document.get('id') != assertion.get('document_id')):
        warnings.append(_warning('document_version_mismatch'))
        return result

    title = (document.get('metadata') or {}).get('title') or \
        (document.get('metadata') or {}).get('source_file') or document['id']
    result['document'] = {
        'id': document['id'], 'version': document['version'],
        'version_id': document['version_id'], 'title': title,
    }
    chunk = _select_chunk(repository, project_id, assertion, document, warnings)
    if chunk is None:
        _apply_hash_integrity(result, assertion, document, False)
        return result

    metadata = chunk.get('metadata')
    metadata = metadata if isinstance(metadata, dict) else {}
    start, end = metadata.get('start_char'), metadata.get('end_char')
    result['chunk'] = {
        'id': chunk['id'], 'start_char': start, 'end_char': end,
        'text': chunk.get('text'),
    }
    if not (type(start) is int and type(end) is int
            and 0 <= start < end <= len(document['text'])):
        warnings.append(_warning('chunk_bounds_invalid'))
        _apply_hash_integrity(result, assertion, document, False)
        return result
    if chunk.get('text') != document['text'][start:end]:
        warnings.append(_warning('chunk_text_mismatch'))
        _apply_hash_integrity(result, assertion, document, False)
        return result

    _apply_hash_integrity(result, assertion, document, True)

    quote = assertion.get('quote')
    assertion_start = assertion.get('start_char')
    assertion_end = assertion.get('end_char')
    coordinates_match = (
        isinstance(quote, str) and bool(quote)
        and type(assertion_start) is int and type(assertion_end) is int
        and start <= assertion_start < assertion_end <= end
        and document['text'][assertion_start:assertion_end] == quote)
    full_chunk = (coordinates_match and assertion_start == start
                  and assertion_end == end and quote == chunk['text'])
    payload = _payload(assertion)
    modern_full_chunk = (
        full_chunk and (payload.get('evidence_status')
                        or _payload_metadata(assertion).get('evidence_status')) == 'exact'
        and _preferred_evidence(assertion) == quote)
    legacy_full_chunk = full_chunk and not modern_full_chunk

    if coordinates_match and not legacy_full_chunk:
        location = _located(
            'exact', chunk['text'], start,
            assertion_start - start, assertion_end - start)
    elif legacy_full_chunk:
        recovered = _legacy_span(assertion, chunk['text'])
        if recovered is not None:
            location = _located(
                'recovered_in_chunk', chunk['text'], start, *recovered)
        else:
            location = _located('chunk', chunk['text'], start, 0, len(chunk['text']))
    else:
        location = _located('chunk', chunk['text'], start, 0, len(chunk['text']))
    result['location'] = location
    return result


def resolve_assertion_evidence(repository, project_id, assertion_id):
    """Return immutable, project-isolated evidence for one assertion."""
    assertion = repository.get_assertion(project_id, assertion_id)
    return _resolve_assertion_evidence(repository, project_id, assertion)


def _legacy_documents(repo, project_id, row, origins):
    """Retain legacy evidence for records that predate assertion storage."""
    documents = []
    seen = set()
    for origin in origins[:20]:
        meta = origin.get('metadata', {})
        source_id = origin.get('source_id')
        if origin.get('kind') == 'document':
            source_id = origin['id']
        if not source_id:
            continue
        versions = repo.history(project_id, source_id)
        pin = meta.get('source_version_id')
        if origin.get('kind') == 'document':
            pin = origin.get('version_id')
        if pin:
            doc = next((version for version in versions
                        if version['version_id'] == pin), None)
        else:
            cutoff = origin.get('recorded_at', row['recorded_at'])
            doc = next((version for version in reversed(versions)
                        if version['recorded_at'] <= cutoff), None)
        if not doc or doc['kind'] != 'document' or doc['version_id'] in seen:
            continue
        seen.add(doc['version_id'])
        text = doc['text']
        start, end = meta.get('start_char'), meta.get('end_char')
        mode = 'unlocated'
        reason = '来源已记录，但没有可核验的原文定位信息。'
        if type(start) is int and type(end) is int and 0 <= start < end <= len(text):
            mode = 'offset'
            reason = '按记录保存的原文字符范围定位（可能是抽取片段范围）。'
        else:
            start = end = None
            passage = meta.get('passage')
            if not isinstance(passage, str):
                passage = ''
            needles = [
                (passage, 'passage', '证据文本首次匹配'),
                (origin.get('text', ''), 'text', '名称 / 内容首次匹配，不代表原始抽取位置'),
            ]
            for needle, kind, label in needles:
                if needle and needle in text:
                    start = text.index(needle)
                    end = start + len(needle)
                    mode, reason = kind, label
                    break
        before = text[:start] if start is not None else text
        highlight = text[start:end] if start is not None else ''
        after = text[end:] if end is not None else ''
        documents.append({
            'id': doc['id'], 'version': doc['version'],
            'version_id': doc['version_id'], 'assertion_id': None,
            'assertion_status': None,
            'title': (doc.get('metadata', {}).get('title')
                      or doc.get('metadata', {}).get('source_file') or doc['id']),
            'source_content': doc.get('metadata', {}).get('legacy', {}).get(
                'source_content', 'original'),
            'mode': mode, 'reason': reason, 'start_char': start, 'end_char': end,
            'before': before, 'highlight': highlight, 'after': after,
            'preview': text[max(0, (start or 0) - 100):min(len(text), (start or 0) + 500)],
        })
    return documents


def evidence(service, project_id, record_id, scope):
    rows = service.scoped(project_id, scope)
    row = next((record for record in rows if record['id'] == record_id), None)
    if row is None:
        raise KeyError(record_id)
    repo = service.repository
    assertions = repo.list_assertions(project_id, canonical_record_id=record_id)
    documents = []
    if assertions:
        seen = set()
        reasons = {
            'exact': '按断言保存并核验的原文字符范围定位。',
            'recovered_in_chunk': '从已核验片段中唯一恢复原文定位。',
            'chunk': '片段已核验，但无法唯一恢复更小的原文范围。',
            'unlocated': '固定来源无法完整核验，未提供推测定位。',
        }
        for assertion in assertions[:20]:
            resolved = _resolve_assertion_evidence(repo, project_id, assertion)
            version_id = resolved['document']['version_id']
            validated_document = resolved['document']['version'] is not None
            if validated_document and version_id in seen:
                continue
            if validated_document:
                seen.add(version_id)
            location = resolved['location']
            if not validated_document:
                text = ''
            else:
                try:
                    document = repo.get_record_version(project_id, version_id)
                except KeyError:
                    text = ''
                else:
                    text = document['text']
            start, end = location['start_char'], location['end_char']
            before = text[:start] if start is not None else text
            highlight = text[start:end] if start is not None else ''
            after = text[end:] if end is not None else ''
            documents.append({
                'id': resolved['document']['id'],
                'version': resolved['document']['version'],
                'version_id': version_id,
                'assertion_id': resolved['assertion_id'],
                'assertion_status': resolved['status'],
                'title': resolved['document']['title'],
                'source_content': 'full_version',
                'mode': location['mode'],
                'reason': reasons[location['mode']],
                'start_char': start, 'end_char': end,
                'before': before, 'highlight': highlight, 'after': after,
                'preview': text[max(0, (start or 0) - 100):min(len(text), (start or 0) + 500)],
                'chunk': resolved['chunk'],
                'integrity': resolved['integrity'],
            })
    else:
        origins = [row] + [
            source for source in row.get('metadata', {}).get('merged_sources', [])
            if isinstance(source, dict)]
        documents = _legacy_documents(repo, project_id, row, origins)
    counts = {
        status: sum(item['status'] == status for item in assertions)
        for status in ('accepted', 'pending', 'contradicting', 'rejected', 'superseded')}
    return {
        'record_id': record_id, 'record_version': row['version'],
        'documents': documents,
        'assertions': [
            {key: value for key, value in item.items() if key != 'payload'}
            for item in assertions],
        'support_counts': counts,
        'message': '' if documents else
            '这条知识没有可解析的来源文档。可能是手工录入，或旧数据未保存来源；不能精确回到原文。',
    }
