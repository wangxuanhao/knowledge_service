"""Server-owned temporal eligibility for document QA; search semantics stay intact."""
import re
from ..core.time import normalize_time, utc_now
from ..domain.applicability import matches_applicability, dimension_mode, dimension_values
from ..utils.filters import matches_filter
from . import structure_pending


def freeze_request(request):
    from .qa_policy import purpose
    now = utc_now()
    return {**request, 'answer_purpose': purpose(request),
            '_explicit_valid_at': request.get('_explicit_valid_at', request.get('valid_at') is not None),
            '_explicit_known_at': request.get('_explicit_known_at', request.get('known_at') is not None),
            '_qa_current_snapshot': not request.get('_explicit_known_at', request.get('known_at') is not None),
            'valid_at': normalize_time(request.get('valid_at')) or now,
            'known_at': normalize_time(request.get('known_at')) or now}


def freeze_qa_request(service, project, request):
    """Pin the current index before freezing an implicit QA knowledge time.

    An explicitly requested history view does not depend on the current index.
    A default view pins its ready generation/watermark first, so a concurrent
    publication cannot replace the frozen version with an apparently empty hit.
    Repeated calls in the shared JSON/SSE preparation preserve the same pin.
    """
    explicit = request.get('_explicit_known_at', request.get('known_at') is not None)
    if explicit or request.get('_qa_index_snapshot') is not None:
        return freeze_request(request)
    from .retrieval import PROJECTION_SCHEMA_VERSIONS
    index = service.repository.projections.search_index(
        project, PROJECTION_SCHEMA_VERSIONS['milvus'])
    snapshot = {'project_id': project, 'index_version': index['index_version'],
                'watermark': index['watermark']}
    # A previously normalized implicit request must be frozen after this pin,
    # rather than retaining a time from before its generation was selected.
    frozen = freeze_request({**request, 'known_at': None, '_explicit_known_at': False})
    return {**frozen, '_qa_index_snapshot': snapshot}


def temporal(row, as_of):
    start = normalize_time(row.get('valid_from'))
    end = normalize_time(row.get('valid_until'))
    basis = (row.get('metadata') or {}).get('effective_time_source')
    if start and end and start >= end:
        return 'conflict', 'invalid_interval'
    if end and end <= as_of:
        return 'expired', 'valid_until'
    if start and start > as_of:
        return 'not_yet_effective', 'valid_from'
    if start and basis != 'published_at':
        return 'effective', 'explicit'
    return 'unknown', 'published_at' if basis == 'published_at' else 'missing_valid_from'


def visible(row, request, source_artifact=False):
    """Recheck every exact source version, including business metadata scope."""
    if row.get('recorded_at') and row['recorded_at'] > request['known_at']:
        return False
    if not source_artifact and row.get('superseded_at') and row['superseded_at'] <= request['known_at']:
        return False
    meta = row.get('metadata') or {}
    if meta.get('_deleted') or meta.get('_audit'):
        return False
    if not matches_filter(row, request.get('filters')):
        return False
    if request.get('applicability') and not matches_applicability(row, request['applicability']):
        return False
    if request.get('ontology_scope') == 'ids' and row.get('ontology_id') not in (request.get('ontology_ids') or []):
        return False
    return True


def row_scope(row, request):
    # Platform group membership is an occurrence constraint, not a union scope.
    if row.get('query_platform'):
        return {**request, 'applicability': {
            **(request.get('applicability') or {}),
            'platforms': {'mode': 'specific', 'values': [row['query_platform']]}}}
    return request


def classify(service, project, row, request):
    request = row_scope(row, request)
    if not visible(row, request):
        return None
    from ..domain.evidence_scope import source_scope
    source_request = source_scope(request, row)
    status, basis = temporal(row, request['valid_at'])
    repo = service.repository
    source_cache = request.setdefault('_source_read_cache', {})
    occurrences = []
    has_source = False
    governance = {}

    def document(version, document_id):
        if not version:
            return None
        try:
            if ('version', version) not in source_cache:
                source_cache[('version', version)] = repo.get_record_version(project, version)
            doc = source_cache[('version', version)]
        except KeyError:
            return None
        if not doc or doc.get('kind') != 'document' or doc['id'] != document_id or not visible(doc, source_request, source_artifact=True):
            return None
        # Source text remains the original immutable version. Business validity
        # follows the document authority version visible at knowledge time.
        if ('history', document_id) not in source_cache:
            source_cache[('history', document_id)] = repo.history(project, document_id)
        history = [item for item in source_cache[('history', document_id)]
                   if item.get('kind') == 'document' and visible(item, source_request)]
        if not history:
            return None
        authority = max(history, key=lambda item: item['version'])
        governance[version] = authority
        return doc

    if row.get('kind') == 'chunk':
        version = (row.get('metadata') or {}).get('source_version_id')
        has_source = bool(version)
        doc = document(version, row.get('source_id'))
        if doc:
            occurrences.append({'document': version, 'chunk': row.get('version_id'),
                                'assertion': None, 'status': temporal(governance[version], request['valid_at'])[0]})
    elif row.get('kind') in {'entity', 'relation'}:
        for mapping in repo.list_record_version_assertions(project, record_version_id=row['version_id']):
            if mapping.get('created_at', request['known_at']) > request['known_at']:
                continue
            assertion = repo.get_assertion(project, mapping['assertion_id'])
            if assertion.get('created_at', request['known_at']) > request['known_at']:
                continue
            assertion_id = mapping['assertion_id']
            if ('events', assertion_id) not in source_cache:
                source_cache[('events', assertion_id)] = repo.list_assertion_events(project, assertion_id)
            event = next((item for item in source_cache[('events', assertion_id)]
                          if item['id'] == mapping.get('assertion_event_id')), None)
            if not event or event.get('created_at', request['known_at']) > request['known_at']:
                continue
            version = assertion.get('document_version_id')
            has_source = has_source or bool(version)
            doc = document(version, assertion.get('document_id'))
            if not doc:
                continue
            chunk_id = assertion.get('chunk_id')
            if chunk_id and ('history', chunk_id) not in source_cache:
                source_cache[('history', chunk_id)] = repo.history(project, chunk_id)
            chunks = [chunk for chunk in source_cache.get(('history', chunk_id), [])
                      if chunk.get('kind') == 'chunk' and
                      (chunk.get('metadata') or {}).get('source_version_id') == version
                      and chunk.get('source_id') == doc['id']
                       and visible(chunk, source_request, source_artifact=True)] if assertion.get('chunk_id') else []
            if len(chunks) != 1:
                continue
            states = {temporal(item, request['valid_at'])[0] for item in (governance[version], chunks[0])}
            non_effective = states - {'effective'}
            state = next(iter(non_effective)) if len(non_effective) == 1 else (
                'conflict' if non_effective else 'effective')
            occurrences.append({'document': version, 'chunk': chunks[0]['version_id'],
                                'assertion': mapping['assertion_id'], 'status': state})
    if status in {'effective', 'unknown'}:
        effective = [item for item in occurrences if item['status'] == 'effective']
        if not effective and (status == 'effective' or occurrences):
            states = {item['status'] for item in occurrences}
            status = next(iter(states)) if len(states) == 1 else ('conflict' if states else 'unknown')
            basis = 'source_validity' if has_source else 'missing_exact_source'
    # References retain authorized frozen sources independently of source time.
    # Effective conclusions freeze only individually eligible occurrences.
    allowed = ([item for item in occurrences if item['status'] == 'effective'] if status == 'effective' else
               [item for item in occurrences if item['status'] in {'effective', 'unknown'}] if status == 'unknown' else occurrences)
    usage = 'effective_basis' if status == 'effective' else (
        'qualified_quote' if status == 'unknown' and row.get('kind') == 'chunk' and
        (allowed or not row.get('source_id')) else 'reference')
    return {**row, 'validity_status': status, 'validity_basis': basis,
            'answer_eligible': status == 'effective', 'usage': usage,
            'exclusion_reasons': [] if status == 'effective' else [basis, status],
            '_allowed_source_version_ids': list(dict.fromkeys(item['document'] for item in allowed)),
            '_allowed_assertion_ids': list(dict.fromkeys(item['assertion'] for item in allowed if item['assertion'])),
            '_allowed_chunk_version_ids': list(dict.fromkeys(item['chunk'] for item in allowed if item['chunk'])),
            'source_eligibility': [{'document_version_id': item['document'],
                                    'governance_version_id': governance[item['document']]['version_id'],
                                    'valid_from': governance[item['document']].get('valid_from'),
                                    'valid_until': governance[item['document']].get('valid_until'),
                                    'effective_time_source': (governance[item['document']].get('metadata') or {}).get('effective_time_source'),
                                    'validity_status': item['status']} for item in allowed]}


def reference_candidates(service, project, request):
    """Bounded authority fallback, with independent quota and explicit coverage."""
    query = request['query'].casefold()
    terms = set(re.findall(r'[a-z0-9_]+', query))
    for phrase in re.findall(r'[\u4e00-\u9fff]+', query):
        terms.add(phrase)
        terms.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
    scope = {**request, '_reference_mode': True,
             '_reference_terms': sorted(terms),
             'kinds': (['chunk'] if request.get('kinds') == ['chunk'] else ['chunk', 'entity']),
             'include_unknown': True}
    rows = service.scoped(project, scope)
    service.annotate_structure_pending(project, rows)
    # A stable bounded read is deliberately not advertised as exhaustive history.
    ranked = []
    for row in rows:
        if structure_pending.is_pending(row):
            continue
        score = sum(term in (row.get('text') or '').casefold() for term in terms)
        if score:
            ranked.append({**row, 'score': score, 'channel': 'reference_keyword'})
    ranked.sort(key=lambda row: (-row['score'], row['id']))
    grouped = []
    for row in ranked:
        applicability = (row.get('metadata') or {}).get('applicability')
        mode = dimension_mode(applicability, 'platforms')
        platforms = dimension_values(applicability, 'platforms') if mode == 'specific' else [None]
        selected = dimension_values(request.get('applicability'), 'platforms')
        for platform in platforms:
            if platform and selected and platform not in selected:
                continue
            grouped.append({**row, 'query_platform': platform, 'source_scope': mode})
    return grouped, ['参考检索先在数据库匹配关键词，再有界读取最多 501 条候选；复杂 metadata 筛选与相关性排序前仍可能截断，并非全库历史穷尽检索。']


def _prefetch_chunk_sources(repository, project, rows, request):
    """Batch exact source text and governance versions, never whole histories."""
    sources = {(row.get('metadata') or {}).get('source_version_id')
               for row in rows if row.get('kind') == 'chunk'} - {None, ''}
    if not sources:
        return
    cache = request['_source_read_cache']
    docs = repository.records_by_version_ids(project, sorted(sources))
    by_version = {row['version_id']: row for row in docs}
    for version in sources:
        cache[('version', version)] = by_version.get(version)
    ids = {doc['id'] for doc in docs if doc.get('kind') == 'document'}
    governance = repository.records_at_knowledge_time(project, sorted(ids), request['known_at'])
    for record_id in ids:
        cache[('history', record_id)] = []
    for doc in governance:
        cache[('history', doc['id'])].append(doc)


def apply_validity(service, project, context, request):
    request = {**request, '_source_read_cache': {}}
    from ..domain.evidence_scope import source_scope
    anchors = {}
    for container in [context, *context.get('platform_groups', [])]:
        for row in container.get('hits', []):
            if container is not context:
                platform, mode = container.get('platform'), container.get('source_scope')
                if (row.get('query_platform'), row.get('source_scope')) != (platform, mode):
                    continue
                if mode in {'all', 'specific', 'unknown'} and dimension_mode(
                        (row.get('metadata') or {}).get('applicability'), 'platforms') != mode:
                    continue
            if visible(row, row_scope(row, request)):
                anchors[(row.get('id'), row.get('version_id'), row.get('query_platform'), row.get('source_scope'))] = row
    authorizations = context.get('_source_context_authorizations') or {}

    def qualification_request(row):
        token = row.get('_source_context_authorization')
        if token is None:
            return row_scope(row, request)
        proof = authorizations.get(token)
        if not isinstance(proof, dict) or (
                row.get('id'), row.get('version_id'), row.get('query_platform'), row.get('source_scope')) != (
                proof.get('artifact_id'), proof.get('artifact_version_id'),
                proof.get('query_platform'), proof.get('source_scope')):
            return None
        anchor = anchors.get((proof.get('anchor_id'), proof.get('anchor_version_id'),
                              proof.get('query_platform'), proof.get('source_scope')))
        if anchor is None:
            return None
        # Never execute a residual AST supplied by material. Recompute it from
        # this request and the independently qualified exact direct hit.
        return source_scope(row_scope(row, request), anchor)
    # Scope/snapshot failures are not business-validity conflicts. Remove their
    # text from every public projection, including raw native retrieval arrays.
    public_row_arrays = ('hits', 'source_chunks', 'context_chunks', 'evidence_rows',
                         'structure_pending_rows', 'unknown_scope_rows')
    for container in [context, *context.get('platform_groups', [])]:
        for key in public_row_arrays:
            if key in container:
                container[key] = [row for row in container[key]
                                  if (selected := qualification_request(row)) is not None and visible(row, selected)]
    context.update(as_of=request['valid_at'], valid_at=request['valid_at'], known_at=request['known_at'])
    warnings = [*context.get('coverage_warnings', []),
                '上下文仅补充已检索到的相邻片段，条件或例外可能不完整。']
    direct = list(context.get('evidence_rows', []))
    # Preserve retrieval's unknown-scope partition. Only the qualification
    # layer may use original chunks for a question without a platform scope.
    if dimension_mode(request.get('applicability'), 'platforms') != 'specific':
        direct.extend(row for row in context.get('unknown_scope_rows', [])
                      if row.get('kind') == 'chunk')
    extra = []
    if request.get('include_references', True):
        try:
            extra, notes = reference_candidates(service, project, request)
            warnings.extend(notes)
        except (AttributeError, NotImplementedError):
            warnings.append('参考检索不可用，无法据此判断是否存在历史或未来规则。')
    _prefetch_chunk_sources(service.repository, project, [*direct, *extra], request)
    direct_keys = {(row.get('version_id') or row['id'], row.get('query_platform')) for row in direct}
    seen, classified = set(), []
    for row in [*direct, *extra]:
        key = (row.get('version_id') or row['id'], row.get('query_platform'))
        if key in seen:
            continue
        seen.add(key)
        selected = qualification_request(row)
        item = classify(service, project, row, selected) if selected is not None else None
        if item is None:
            continue
        classified.append(item)
        if (row.get('source_scope') == 'unknown' and
                dimension_mode(request.get('applicability'), 'platforms') == 'specific'):
            classified[-1].update(answer_eligible=False, usage='reference')
    context['evidence_rows'] = [row for row in classified if row['answer_eligible'] and
                              (row.get('version_id') or row['id'], row.get('query_platform')) in direct_keys]
    from .qa_policy import DOCUMENT
    reference_rows = classified
    if request.get('answer_purpose') == DOCUMENT:
        # Entity hits precede source chunks in hybrid retrieval. Reserve the
        # bounded reference slots for traceable originals before derived facts,
        # or a full entity quota can silently leave document QA no model input.
        reference_rows = sorted(classified, key=lambda row: not (
            row.get('kind') == 'chunk' and row.get('_allowed_source_version_ids')
            and row.get('version_id') in row.get('_allowed_chunk_version_ids', [])))
    budget = max(1, min(request.get('reference_k', 5), 20))
    for key, status in (('historical_references', 'expired'),
                        ('future_references', 'not_yet_effective'),
                        ('uncertain_references', 'unknown')):
        counts, chosen = {}, []
        for row in reference_rows:
            group = (row.get('query_platform'), row.get('source_scope'))
            if row['validity_status'] == status and counts.get(group, 0) < budget:
                chosen.append(row)
                counts[group] = counts.get(group, 0) + 1
        context[key] = chosen
    context['uncertain_references'].extend(row for row in classified
                                          if row['validity_status'] == 'conflict')
    if not request.get('include_references', True):
        if context['historical_references'] or context['future_references']:
            warnings.append('命中来源含已失效或尚未生效内容，已排除；历史与未来参考展示已关闭。')
        context['historical_references'] = []
        context['future_references'] = []
    selected_platforms = dimension_values(request.get('applicability'), 'platforms')
    if len(selected_platforms) > 20:
        warnings.append('本次检索预算仅覆盖前 20 个所选平台，其余平台尚未检索，不能推断其规则。')
    def occurrence(item, record_id):
        return (record_id, item.get('query_platform'), item.get('source_scope'))
    effective_nodes = {occurrence(row, row['id']): row for row in context['evidence_rows']
                       if row.get('kind') == 'entity'}
    edges = {occurrence(row, row['id']): row for row in context['evidence_rows']
             if row.get('kind') == 'relation'}
    # Every relation and endpoint must qualify in this very platform occurrence.
    # An equally named/id node returned for a different platform cannot complete
    # a path whose own endpoint was rejected by source or temporal governance.
    context['reasoning_paths'] = [path for path in context.get('reasoning_paths', [])
        if path.get('steps') and all(
            occurrence(path, step['relation_id']) in edges
            and occurrence(path, step['from']) in effective_nodes
            and occurrence(path, step['to']) in effective_nodes
            and {step['from'], step['to']} == {
                edges[occurrence(path, step['relation_id'])].get('subject_id'),
                edges[occurrence(path, step['relation_id'])].get('object_id')}
            for step in path['steps'])][:200]
    for path in context['reasoning_paths']:
        for step in path['steps']:
            edge = edges[occurrence(path, step['relation_id'])]
            step.update(direction='forward' if step['from'] == edge['subject_id'] else 'backward',
                        subject_id=edge['subject_id'], object_id=edge['object_id'],
                        relation_version_id=edge.get('version_id'),
                        from_version_id=effective_nodes[occurrence(path, step['from'])].get('version_id'),
                        to_version_id=effective_nodes[occurrence(path, step['to'])].get('version_id'),
                        source_version_ids=edge.get('_allowed_source_version_ids', []),
                        validity_status='effective')
    context['reasoning_path_status'] = 'available' if context['reasoning_paths'] else 'no_qualified_path'
    if 'channels' in context:
        context['channels']['graph_evidence'] = sum(row.get('kind') == 'relation'
                                                   for row in context['evidence_rows'])
    for group in context.get('platform_groups', []):
        group['evidence_rows'] = [row for row in context['evidence_rows']
                                  if row.get('query_platform') == group.get('platform')
                                  and ('source_scope' not in group or
                                       row.get('source_scope') == group['source_scope'])]
        group['reasoning_paths'] = [path for path in context['reasoning_paths']
            if path.get('query_platform') == group.get('platform')
            and path.get('source_scope') == group.get('source_scope')]
        if not group['evidence_rows']:
            warnings.append(str(group.get('platform')) + '：未检索到该时点确认有效的依据，不能据此断言不存在规则。')
    if context.get('graph_truncated'):
        warnings.append('图关系上下文达到节点或关系预算，当前路径仅覆盖已读取邻域，不能据此断言全库没有路径。')
    context['coverage_warnings'] = warnings
    context['eligibility_summary'] = {key: len(context[key]) for key in (
        'evidence_rows', 'historical_references', 'future_references', 'uncertain_references')}
    context.pop('_source_context_authorizations', None)
    for container in [context, *context.get('platform_groups', [])]:
        for key in (*public_row_arrays, 'historical_references', 'future_references', 'uncertain_references'):
            for row in container.get(key, []):
                row.pop('_source_context_authorization', None)
    return context


def qualified_text(context):
    rows = [row for row in context.get('uncertain_references', []) if row.get('usage') == 'qualified_quote']
    if not rows:
        return ''
    grouped = {}
    for row in rows:
        platform = row.get('query_platform') or row.get('source_scope') or '见文档适用范围'
        identity = (row['text'], platform)
        grouped.setdefault(identity, []).append(row['citation'])
    # Identical copies remain distinct frozen sources but need not duplicate
    # the entire quotation in the reader's answer.
    return '\n\n文档表述（有效性待核对，不作为已确认有效规则依据）\n' + '\n\n'.join(
        ' '.join(f'[{citation}]' for citation in citations)
        + f' 文档写明：\n{quote_text(text)}\n\n适用平台：{platform}'
        for (text, platform), citations in grouped.items())


def quote_text(text):
    """Keep citation-like tokens inside source text distinct from server citations."""
    return re.sub(r'\[([A-Z][0-9]+)\]', r'［\1］', text)
