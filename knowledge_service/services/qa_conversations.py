"""Private QA history validation; these snapshots never authorize citations."""
import json
import math
import re


MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_CITATION = re.compile(r'[ERFU][1-9][0-9]*\Z')
_EVIDENCE_ARRAYS = ('evidence', 'historical_references', 'future_references', 'uncertain_references')


def _strings(value, keys, *, limit=None, nullable=False):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in keys:
        item = value.get(key)
        if isinstance(item, str):
            result[key] = item if limit is None else item[:limit]
        elif nullable and key in value and item is None:
            result[key] = None
    return result


def _string_list(value, *, count=None, length=None):
    if not isinstance(value, list):
        return []
    items = [item for item in value if isinstance(item, str)]
    if count is not None:
        items = items[:count]
    return [item if length is None else item[:length] for item in items]


def _applicability(value):
    if not isinstance(value, dict):
        return {}
    return {key: {'mode': item['mode'], 'values': _string_list(item.get('values'), count=30, length=200)}
            for key in ('platforms', 'businesses', 'audiences')
            if isinstance(item := value.get(key), dict) and item.get('mode') in ('all', 'specific', 'unknown')}


def sanitize_evidence_summary(value):
    """Match web/provenance-drawer.js's explicit frozen-summary allowlist."""
    if not isinstance(value, dict):
        return {}
    result = _strings(value, ('text_preview', 'kind', 'provenance_ref', 'source_status',
        'source_reason', 'retrieval_channel', 'ranker_version'))
    if 'text_preview' in result:
        result['text_preview'] = result['text_preview'][:240]
    if isinstance(value.get('citation'), str) and _CITATION.fullmatch(value['citation']):
        result['citation'] = value['citation']
    if type(value.get('version')) is int and 0 < value['version'] <= 2**53 - 1:
        result['version'] = value['version']
    result.update(_strings(value, ('version_id', 'valid_from', 'valid_until', 'validity_status',
        'validity_basis', 'usage', 'query_platform', 'source_scope'), limit=500))
    for key in ('answer_eligible', 'document_quote_allowed'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    if isinstance(value.get('exclusion_reasons'), list):
        result['exclusion_reasons'] = _string_list(value['exclusion_reasons'], count=20, length=500)
    if isinstance(value.get('source_eligibility'), list):
        result['source_eligibility'] = [clean for source in value['source_eligibility'][:20]
            if (clean := _strings(source, ('document_version_id', 'governance_version_id',
                'validity_status', 'valid_from', 'valid_until', 'effective_time_source'), limit=500, nullable=True))]
    metadata = value.get('metadata')
    if isinstance(metadata, dict):
        clean = _strings(metadata, ('published_at', 'effective_time_source'), limit=500)
        if applicability := _applicability(metadata.get('applicability')):
            clean['applicability'] = applicability
        if clean:
            result['metadata'] = clean
    if isinstance(value.get('sources'), list):
        sources = []
        for source in value['sources'][:20]:
            clean = _strings(source, ('document_id', 'document_version_id', 'record_version_id',
                'title', 'quote', 'channel', 'valid_from', 'valid_until'))
            if 'quote' in clean:
                clean['quote'] = clean['quote'][:240]
            if isinstance(source, dict) and type(source.get('version')) is int and 0 < source['version'] <= 2**53 - 1:
                clean['version'] = source['version']
            if clean:
                sources.append(clean)
        result['sources'] = sources
    return result


def _filter(value, depth=0):
    if not isinstance(value, dict) or depth > 30:
        return {}
    result = _strings(value, ('field', 'op'))
    for key in ('and', 'or'):
        if isinstance(value.get(key), list):
            result[key] = [_filter(child, depth + 1) for child in value[key]]
    if isinstance(value.get('not'), dict):
        result['not'] = _filter(value['not'], depth + 1)
    item = value.get('value')
    if item is None or isinstance(item, (str, int, float, bool)):
        if 'value' in value:
            result['value'] = item
    elif isinstance(item, list):
        result['value'] = [part for part in item if part is None or isinstance(part, (str, int, float, bool))]
    return result


def _scope(value):
    result = _strings(value, ('valid_at', 'known_at', 'ontology_scope', 'retrieval_mode', 'query'))
    for key in ('include_unknown', 'include_references', 'generate', 'ontology_expansion'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    for key in ('k', 'k_entities', 'k_chunks', 'k_relations', 'k_per_platform', 'k_unknown', 'reference_k', 'hops'):
        if type(value.get(key)) is int:
            result[key] = value[key]
    for key in ('kinds', 'ontology_ids'):
        if isinstance(value.get(key), list):
            result[key] = _string_list(value[key])
    if isinstance(value.get('applicability'), dict):
        result['applicability'] = _applicability(value['applicability'])
    if isinstance(value.get('filters'), dict):
        result['filters'] = _filter(value['filters'])
    return result


def _display_object(value, strings=(), numbers=(), booleans=()):
    result = _strings(value, strings, nullable=True)
    if isinstance(value, dict):
        for key in numbers:
            item = value.get(key)
            if type(item) is int or (type(item) is float and math.isfinite(item)):
                result[key] = item
        for key in booleans:
            if type(value.get(key)) is bool:
                result[key] = value[key]
    return result


def _snapshot_size(messages):
    try:
        size = len(json.dumps(messages, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8'))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError('会话快照必须只含有限 JSON 值') from exc
    if size > MAX_SNAPSHOT_BYTES:
        raise ValueError('会话快照不能超过 8 MiB，请新建会话后继续')


def sanitize_messages(project_id, messages):
    if not isinstance(messages, list) or len(messages) > 1000:
        raise ValueError('会话消息必须为数组且不能超过 1000 条')
    _snapshot_size(messages)
    result = []
    for message in messages:
        if not isinstance(message, dict) or message.get('kind') not in ('user', 'assistant'):
            raise ValueError('会话消息 kind 只能为 user 或 assistant')
        if not isinstance(message.get('text'), str) or not isinstance(message.get('scope'), dict):
            raise ValueError('会话消息 text 必须为字符串且 scope 必须为对象')
        clean = {'kind': message['kind'], 'text': message['text'],
                 'scope': _scope(message['scope']), 'project_id': project_id}
        if message['kind'] == 'assistant':
            clean.update(_strings(message, ('answer_id', 'retrieval_run_id', 'as_of', 'known_at',
                'mode', 'answer_purpose', 'answer_label')))
            for key in _EVIDENCE_ARRAYS:
                if isinstance(message.get(key), list):
                    clean[key] = [sanitize_evidence_summary(item) for item in message[key]]
            if isinstance(message.get('coverage_warnings'), list):
                clean['coverage_warnings'] = _string_list(message['coverage_warnings'])
            if isinstance(message.get('generation'), dict):
                clean['generation'] = _display_object(message['generation'],
                    ('skip_reason', 'material_status'), booleans=('requested', 'called'))
            if isinstance(message.get('timings_ms'), dict):
                clean['timings_ms'] = _display_object(message['timings_ms'], numbers=(
                    'retrieval', 'eligibility', 'freeze', 'summaries', 'llm', 'llm_first_content', 'complete', 'total'))
            if isinstance(message.get('platform_groups'), list):
                clean['platform_groups'] = [_display_object(item, ('platform', 'source_scope'),
                    ('candidate_count', 'evidence_count')) for item in message['platform_groups'] if isinstance(item, dict)]
            if isinstance(message.get('ontology_expansion'), dict):
                expansion = message['ontology_expansion']
                ledger = _display_object(expansion, ('strategy',), ('type_hits',),
                    ('enabled', 'matched', 'has_hierarchy'))
                for key in ('terms', 'subclasses', 'matched_terms', 'expanded_subclasses',
                            'labels', 'type_iris', 'keyword_terms', 'literal_terms', 'entity_terms'):
                    if isinstance(expansion.get(key), list):
                        ledger[key] = _string_list(expansion[key], count=100, length=500)
                if type(expansion.get('used_hits')) is int and 0 <= expansion['used_hits'] <= 2**53 - 1:
                    ledger['used_hits'] = expansion['used_hits']
                if isinstance(expansion.get('entity_matches'), list):
                    matches = []
                    for match in expansion['entity_matches']:
                        item = _strings(match, ('id', 'version_id', 'text', 'matched_name',
                            'type', 'type_label'), limit=500)
                        if isinstance(match, dict) and match.get('match_kind') in ('name', 'alias'):
                            item['match_kind'] = match['match_kind']
                        if item:
                            matches.append(item)
                        if len(matches) == 8:
                            break
                    ledger['entity_matches'] = matches
                clean['ontology_expansion'] = ledger
            if 'error' in message:
                error = message['error']
                if isinstance(error, (str, bool)) or error is None:
                    clean['error'] = error
                elif isinstance(error, dict):
                    clean['error'] = _strings(error, ('message', 'detail', 'code'))
            if 'structure_pending' in message:
                pending = message['structure_pending']
                if pending is None:
                    clean['structure_pending'] = None
                elif isinstance(pending, dict):
                    block = _display_object(pending, ('note',), ('count',))
                    block['terms'] = _string_list(pending.get('terms'))
                    items = pending.get('items')
                    block['items'] = [{
                        **_strings(item, ('citation', 'id', 'kind', 'text', 'text_preview', 'subject_id')),
                        'terms': _string_list(item.get('terms')),
                    } for item in items if isinstance(item, dict)] if isinstance(items, list) else []
                    clean['structure_pending'] = block
        result.append(clean)
    _snapshot_size(result)
    return result


def validate_title(title):
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 80:
        raise ValueError('会话标题必须为 1 到 80 个非空字符')
    if '\x00' in title:
        raise ValueError('会话标题不能包含空字节')
    return title.strip()


class QaConversations:
    def __init__(self, repository):
        self.store = repository.qa_conversations

    def list(self, owner_id, project_id, *, limit=50, offset=0, q=''):
        return self.store.list(owner_id, project_id, limit=limit, offset=offset, q=q.strip())

    def create(self, owner_id, project_id, *, title=None):
        return self.store.create(owner_id, project_id,
            title='新会话' if title is None else validate_title(title), title_auto=title is None)

    def get(self, owner_id, project_id, conversation_id):
        return self.store.get(owner_id, project_id, conversation_id)

    def save(self, owner_id, project_id, conversation_id, *, expected_version, messages):
        clean = sanitize_messages(project_id, messages)
        first_question = next((item['text'] for item in clean if item['kind'] == 'user'), '')
        # Titles are native PostgreSQL text; the JSON snapshot still preserves
        # every question character, including escaped JSON control characters.
        auto_title = ' '.join(first_question.replace('\x00', ' ').split())[:80]
        return self.store.update(owner_id, project_id, conversation_id, expected_version=expected_version,
                                 messages=clean, auto_title=auto_title)

    def rename(self, owner_id, project_id, conversation_id, *, expected_version, title):
        return self.store.update(owner_id, project_id, conversation_id, expected_version=expected_version,
                                 title=validate_title(title))

    def delete(self, owner_id, project_id, conversation_id, *, expected_version):
        self.store.delete(owner_id, project_id, conversation_id, expected_version=expected_version)
