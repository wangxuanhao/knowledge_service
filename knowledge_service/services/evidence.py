"""Resolve formal facts and assertions to immutable source evidence."""

import hashlib

from ..repository.scoped_reads import ScopedReads


_WARNING_MESSAGES = {
    'document_version_missing': '断言固定的文档版本不存在。',
    'document_version_mismatch': '断言固定版本不是所声明的来源文档。',
    'chunk_history_missing': '断言所引用的片段没有历史记录。',
    'chunk_history_mismatch': '片段历史与断言固定的来源文档版本不匹配。',
    'chunk_history_ambiguous': '多个片段历史版本匹配断言来源，无法安全选择。',
    'chunk_bounds_invalid': '片段保存的字符范围无效或超出固定文档版本。',
    'chunk_text_mismatch': '片段文本与固定文档版本中的字符范围不一致。',
    'source_hash_unavailable': (
        '该历史断言未保存来源文档哈希，无法进行额外的哈希一致性校验；'
        '固定文档版本与历史切片仍可正常溯源。'),
    'source_hash_mismatch': '断言来源哈希与固定文档版本不一致。',
}


def _warning(code):
    return {'code': code, 'message': _WARNING_MESSAGES[code]}


def _unlocated(reason='source_unavailable'):
    return {
        'mode': 'unlocated', 'reason': reason,
        'start_char': None, 'end_char': None,
        'before': '', 'highlight': '', 'after': '',
    }


def _chunk_location():
    return {
        'mode': 'chunk', 'reason': 'highlight_not_unique',
        'start_char': None, 'end_char': None,
        'before': '', 'highlight': '', 'after': '',
    }


def _located(reason, chunk_text, chunk_start, local_start, local_end):
    return {
        'mode': 'exact',
        'reason': reason,
        'start_char': chunk_start + local_start,
        'end_char': chunk_start + local_end,
        'before': chunk_text[:local_start],
        'highlight': chunk_text[local_start:local_end],
        'after': chunk_text[local_end:],
    }


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


def _new_resolution(assertion):
    return {
        'assertion': assertion,
        'document': None,
        'chunk': {
            'id': assertion.get('chunk_id'),
            'start_char': None, 'end_char': None, 'text': None,
        },
        'location': _unlocated(),
        'source_location': {'code': 'source_mapping_unavailable'},
        'integrity': {
            'complete': False,
            'source_hash_status': 'unavailable',
            'warnings': [],
        },
    }


def _source_location(repository, project_id, document, chunk):
    """Only follow identities pinned on the immutable document and chunk versions."""
    version_id = (document.get('metadata') or {}).get('source_document_version_id')
    chunk_version_id = chunk.get('version_id')
    if not version_id or not chunk_version_id:
        return {'code': 'source_mapping_unavailable'}
    from ..repository.source_store import SourceStore
    try:
        return SourceStore(repository).locate(project_id, version_id, chunk_version_id=chunk_version_id)
    except KeyError:
        return {'code': 'source_mapping_unavailable'}


def _apply_hash_integrity(resolution, document, chunk_complete):
    source_hash = resolution['assertion'].get('source_hash')
    integrity = resolution['integrity']
    if not isinstance(source_hash, str) or not source_hash:
        integrity['source_hash_status'] = 'unavailable'
        integrity['warnings'].append(_warning('source_hash_unavailable'))
        hash_complete = True
    elif hashlib.sha256(document['text'].encode('utf-8')).hexdigest() != source_hash:
        integrity['source_hash_status'] = 'mismatched'
        integrity['warnings'].append(_warning('source_hash_mismatch'))
        hash_complete = False
    else:
        integrity['source_hash_status'] = 'matched'
        hash_complete = True
    integrity['complete'] = chunk_complete and hash_complete
    return hash_complete


def _select_chunk(repository, project_id, assertion, document, warnings):
    chunk_id = assertion.get('chunk_id')
    if not isinstance(chunk_id, str) or not chunk_id:
        warnings.append(_warning('chunk_history_missing'))
        return None
    history = repository.history(project_id, chunk_id)
    if not history:
        warnings.append(_warning('chunk_history_missing'))
        return None
    exact = [
        chunk for chunk in history
        if chunk.get('kind') == 'chunk'
        and chunk.get('source_id') == assertion.get('document_id')
        and isinstance(chunk.get('metadata'), dict)
        and chunk['metadata'].get('source_version_id') == document['version_id']]
    if len(exact) == 1:
        return exact[0]
    warnings.append(_warning(
        'chunk_history_ambiguous' if len(exact) > 1 else 'chunk_history_mismatch'))
    return None


def _resolve_assertion_evidence(repository, project_id, assertion):
    """Build one trusted resolution used by every external serializer."""
    resolution = _new_resolution(assertion)
    warnings = resolution['integrity']['warnings']
    try:
        document = repository.get_record_version(
            project_id, assertion.get('document_version_id'))
    except KeyError:
        warnings.append(_warning('document_version_missing'))
        resolution['location'] = _unlocated('document_version_missing')
        return resolution
    if (document.get('kind') != 'document'
            or document.get('id') != assertion.get('document_id')):
        warnings.append(_warning('document_version_mismatch'))
        resolution['location'] = _unlocated('document_version_mismatch')
        return resolution
    resolution['document'] = document

    chunk = _select_chunk(repository, project_id, assertion, document, warnings)
    if chunk is None:
        resolution['location'] = _unlocated(warnings[-1]['code'])
        hash_complete = _apply_hash_integrity(resolution, document, False)
        if not hash_complete:
            resolution['location'] = _unlocated('source_hash_mismatch')
        return resolution
    resolution['source_location'] = _source_location(repository, project_id, document, chunk)
    metadata = chunk.get('metadata')
    metadata = metadata if isinstance(metadata, dict) else {}
    start, end = metadata.get('start_char'), metadata.get('end_char')
    resolution['chunk'] = {
        'id': chunk['id'], 'start_char': start, 'end_char': end,
        'text': chunk.get('text'),
    }
    if not (type(start) is int and type(end) is int
            and 0 <= start < end <= len(document['text'])):
        warnings.append(_warning('chunk_bounds_invalid'))
        resolution['location'] = _unlocated('chunk_bounds_invalid')
        hash_complete = _apply_hash_integrity(resolution, document, False)
        if not hash_complete:
            resolution['location'] = _unlocated('source_hash_mismatch')
        return resolution
    if chunk.get('text') != document['text'][start:end]:
        warnings.append(_warning('chunk_text_mismatch'))
        resolution['location'] = _unlocated('chunk_text_mismatch')
        hash_complete = _apply_hash_integrity(resolution, document, False)
        if not hash_complete:
            resolution['location'] = _unlocated('source_hash_mismatch')
        return resolution
    if not _apply_hash_integrity(resolution, document, True):
        resolution['location'] = _unlocated('source_hash_mismatch')
        return resolution

    quote = assertion.get('quote')
    assertion_start = assertion.get('start_char')
    assertion_end = assertion.get('end_char')
    coordinates_match = (
        isinstance(quote, str) and bool(quote)
        and type(assertion_start) is int and type(assertion_end) is int
        and start <= assertion_start < assertion_end <= end
        and document['text'][assertion_start:assertion_end] == quote)
    chunk_coordinates = (
        type(assertion_start) is int and type(assertion_end) is int
        and assertion_start == start and assertion_end == end)
    whole_slice_quote = isinstance(quote, str) and quote == chunk['text']
    payload = _payload(assertion)
    evidence_status = (payload.get('evidence_status')
                       or _payload_metadata(assertion).get('evidence_status'))
    modern_whole_chunk = (
        coordinates_match and chunk_coordinates and whole_slice_quote
        and evidence_status == 'exact'
        and _preferred_evidence(assertion) == quote)
    unverified_chunk_anchor = chunk_coordinates and not modern_whole_chunk

    if coordinates_match and not unverified_chunk_anchor:
        resolution['location'] = _located(
            'stored_quote_verified', chunk['text'], start,
            assertion_start - start, assertion_end - start)
    else:
        resolution['location'] = _chunk_location()
    return resolution


def _document_contract(resolution):
    assertion = resolution['assertion']
    document = resolution['document']
    if document is None:
        return {
            'id': assertion.get('document_id'),
            'version': None,
            'version_id': assertion.get('document_version_id'),
            'title': assertion.get('document_id'),
            'source_content': None,
        }
    metadata = document.get('metadata')
    metadata = metadata if isinstance(metadata, dict) else {}
    return {
        'id': document['id'], 'version': document['version'],
        'version_id': document['version_id'],
        'title': metadata.get('title') or metadata.get('source_file') or document['id'],
        'source_content': 'full_version',
    }


def _serialize_assertion_resolution(resolution):
    assertion = resolution['assertion']
    return {
        'assertion_id': assertion['id'],
        'assertion_status': assertion['status'],
        'candidate_kind': assertion['kind'],
        'document': _document_contract(resolution),
        'chunk': dict(resolution['chunk']),
        'location': dict(resolution['location']),
        'source_location': dict(resolution['source_location']),
        'integrity': {
            **resolution['integrity'],
            'warnings': [dict(warning) for warning in resolution['integrity']['warnings']],
        },
    }


def resolve_assertion_evidence(repository, project_id, assertion_id):
    """Return immutable, project-isolated evidence for one assertion."""
    assertion = repository.get_assertion(project_id, assertion_id)
    return _serialize_assertion_resolution(
        _resolve_assertion_evidence(repository, project_id, assertion))


def _serialize_formal_document(resolution):
    public = _serialize_assertion_resolution(resolution)
    document = resolution['document']
    location = resolution['location']
    text = document['text'] if document is not None else ''
    start, end = location['start_char'], location['end_char']
    if location['mode'] == 'exact':
        before, highlight, after = text[:start], text[start:end], text[end:]
    elif location['mode'] == 'chunk':
        before, highlight, after = text, '', ''
    else:
        before = highlight = after = ''
    reasons = {
        'exact': '按断言保存并核验的原文字符范围定位。',
        'chunk': '片段已核验，断言未保存可核验的原文字符范围。',
        'unlocated': '固定来源无法完整核验，未提供推测定位。',
    }
    return {
        'id': public['document']['id'],
        'version': public['document']['version'],
        'version_id': public['document']['version_id'],
        'assertion_id': public['assertion_id'],
        'assertion_status': public['assertion_status'],
        'title': public['document']['title'],
        'source_content': public['document']['source_content'],
        'mode': location['mode'], 'reason': reasons[location['mode']],
        'start_char': start, 'end_char': end,
        'before': before, 'highlight': highlight, 'after': after,
        'preview': (text[max(0, (start or 0) - 100):min(len(text), (start or 0) + 500)]
                    if document is not None else ''),
        'chunk': public['chunk'], 'integrity': public['integrity'],
        'source_location': public['source_location'],
    }


def _resolution_score(resolution):
    location_rank = {
        'unlocated': 0, 'chunk': 1, 'exact': 2,
    }
    return (
        int(resolution['integrity']['complete']),
        location_rank[resolution['location']['mode']],
    )


def evidence(service, project_id, record_id, scope):
    repo = service.repository
    rows = ScopedReads(repo).by_ids(project_id, scope, [record_id])
    row = next((record for record in rows if record['id'] == record_id), None)
    if row is None:
        raise KeyError(record_id)
    assertions = repo.list_assertions(project_id, canonical_record_id=record_id)
    documents = []
    if assertions:
        selected = []
        version_indexes = {}
        for assertion in assertions:
            resolution = _resolve_assertion_evidence(repo, project_id, assertion)
            document = resolution['document']
            if document is None:
                selected.append(resolution)
                continue
            version_id = document['version_id']
            index = version_indexes.get(version_id)
            if index is None:
                version_indexes[version_id] = len(selected)
                selected.append(resolution)
            elif _resolution_score(resolution) > _resolution_score(selected[index]):
                selected[index] = resolution
        ranked = sorted(selected, key=_resolution_score, reverse=True)
        documents = [
            _serialize_formal_document(resolution)
            for resolution in ranked[:20]
        ]
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
            '这条知识没有保存可解析的来源断言，无法定位来源文档。',
    }
