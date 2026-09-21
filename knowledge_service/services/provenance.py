"""Answer provenance: capture source decisions once, then read frozen projections.

Only exact version mappings establish support. Mutable assertion/run display fields
are captured with the graph; trace reads never consult their current state.
"""
from __future__ import annotations

import math
import re
from uuid import uuid4


_CITATION = re.compile(r'E[1-9][0-9]*\Z')
_ANSWER_CITATION = re.compile(r'\[(E[1-9][0-9]*)\]')
_NODE_ORDER = {kind: index for index, kind in enumerate((
    'answer', 'retrieval', 'record_version', 'assertion', 'review_event',
    'chunk_version', 'source_occurrence', 'document_version', 'ingest_run'))}
_PRIVATE_KEYS = frozenset({
    'authorization', 'headers', 'api_key', 'apikey', 'provider_response',
    'full_response', 'chain_of_thought', 'reasoning', 'reasoning_content',
    'request_headers', 'response_headers', 'messages', 'access_token', 'secret',
})


def _text(value, limit=240):
    return value[:limit] if isinstance(value, str) else None


def _public_json(value):
    """Scope is business JSON, with provider-only keys removed recursively."""
    if isinstance(value, dict):
        return {key: _public_json(item) for key, item in value.items()
                if isinstance(key, str) and key.lower() not in _PRIVATE_KEYS}
    if isinstance(value, (list, tuple)):
        return [_public_json(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _node(node_type, ref, label, status=None, occurred_at=None, **details):
    return {'type': node_type, 'ref': ref, 'label': label, 'status': status,
            'occurred_at': occurred_at, 'details': details}


def _warning(code, ref, message):
    return {'code': code, 'node_ref': ref, 'message': message}


def _retrieval_error_summaries(errors):
    """Keep public failure categories, never provider messages or response bodies."""
    if not isinstance(errors, dict):
        return {}
    categories = (
        (('timeout', 'timed out', '超时'), '检索后端请求超时。'),
        (('语义索引尚未就绪',), '语义索引尚未就绪。'),
        (('嵌入模型与已存向量不一致',), '嵌入模型与已存向量不一致。'),
        (('嵌入维度不匹配',), '嵌入向量维度不匹配。'),
        (('connectionerror', 'connection refused', '连接失败'), '检索后端连接失败。'),
    )
    summaries = {}
    for backend in ('keyword', 'semantic'):
        if backend not in errors or errors[backend] is None:
            continue
        reason = _text(errors[backend], 2000) or ''
        reason = reason.casefold()
        summaries[backend] = next((summary for markers, summary in categories
                                   if any(marker in reason for marker in markers)), '检索后端不可用。')[:240]
    return summaries


class ProvenanceService:
    """Five-operation lifecycle boundary over the repository audit ledger."""

    def __init__(self, repository):
        self.repository = repository

    def begin_retrieval(self, project_id, request):
        query = request.get('query', '')
        if not isinstance(query, str) or not query.strip():
            raise ValueError('检索 query 必须是非空字符串')
        mode = request.get('retrieval_mode', 'hybrid')
        if mode not in {'hybrid', 'semantic', 'keyword'}:
            raise ValueError('不支持的检索模式')
        scope = {key: _public_json(request.get(key)) for key in (
            'filters', 'valid_at', 'known_at', 'kinds')}
        scope['include_unknown'] = request.get('include_unknown', True) is not False
        run_id = 'rr_' + uuid4().hex
        self.repository.begin_provenance_activity(project_id, run_id, 'retrieval', {
            'query': query.strip()[:10000], 'scope': scope, 'requested_mode': mode})
        return run_id

    def complete_retrieval(self, project_id, run_id, context, evidence):
        answer_id = 'ans_' + uuid4().hex
        self.repository.complete_retrieval_and_begin_answer(
            project_id, retrieval_id=run_id, answer_id=answer_id,
            snapshot_factory=lambda: self._capture_retrieval(
                project_id, run_id, answer_id, context, evidence))
        return answer_id

    def _capture_retrieval(self, project_id, run_id, answer_id, context, evidence):
        """Read every branch while the repository owns the completion transaction."""
        repo = self.repository
        retrieval = repo.get_provenance_activity(project_id, run_id)
        if retrieval['kind'] != 'retrieval':
            raise ValueError('指定活动不是 retrieval')
        retrieval_payload = dict(retrieval['payload'])
        retrieval_payload.update({
            'active_modes': sorted({mode for mode in context.get('active_modes', [])
                                    if mode in {'keyword', 'semantic', 'hybrid'}}),
            'backend': _text(context.get('_backend')),
            'degraded': context.get('degraded') is True,
            'retrieval_errors': _retrieval_error_summaries(context.get('retrieval_errors')),
            'candidate_count': context.get('candidate_count') if type(context.get('candidate_count')) is int else None,
            'embedding_model': _text(context.get('embedding_model')),
            'semantic': context.get('semantic') is True,
            'channels': {key: value for key, value in context.get('channels', {}).items()
                         if key in {'entity', 'chunk', 'graph_evidence'} and type(value) is int},
        })
        used = {'activity_id': answer_id, 'source_ref': f'answer:{answer_id}',
                'relation': 'used', 'target_ref': f'retrieval-run:{run_id}', 'ordinal': 0, 'payload': {}}
        citations, edges = [], [used]
        for rank, item in enumerate(evidence, 1):
            citation = item.get('citation')
            if not isinstance(citation, str) or not _CITATION.fullmatch(citation) or citation in citations:
                raise ValueError('证据引用必须是唯一的大写 E 编号')
            graph = _SourceCapture(
                repo, project_id, f'{answer_id}#{citation}').capture(item)
            score = item.get('score')
            audit = {'rank': rank, 'score': score if type(score) in (int, float) and math.isfinite(score) else None,
                     'channel': _text(item.get('channel') or item.get('kind')), 'selected': True}
            record_ref = graph['record_ref']
            citation_ref = f'answer:{answer_id}#{citation}'
            frozen = {**audit, 'citation': citation, 'warnings': graph['warnings']}
            initial = [
                {'activity_id': run_id, 'source_ref': f'retrieval-run:{run_id}',
                 'relation': 'considered', 'target_ref': record_ref, 'payload': frozen},
                {'activity_id': answer_id, 'source_ref': citation_ref,
                 'relation': 'offered', 'target_ref': record_ref, 'payload': frozen},
            ]
            graph_edges = initial + [dict(
                edge, activity_id=answer_id,
                payload={**edge.get('payload', {}), 'citation': citation})
                for edge in graph['edges']]
            for edge in graph_edges:
                edge['ordinal'] = len(edges)
                edges.append(edge)
            citations.append(citation)
        return retrieval_payload, {
            'run_id': run_id, 'offered': citations, 'cited': [],
            'warnings': [], 'answer': '', 'mode': None, 'graph_schema': 'edge-v1'}, edges

    def complete_answer(self, project_id, answer_id, run_id, answer, mode, evidence):
        activity = self.repository.get_provenance_activity(project_id, answer_id)
        activity = self._ensure_edge_v1(project_id, activity)
        payload = activity['payload']
        if activity['kind'] != 'answer' or payload.get('run_id') != run_id:
            raise ValueError('答案和检索运行不匹配')
        if not isinstance(answer, str) or mode not in {'llm', 'evidence_only'}:
            raise ValueError('答案文本或模式无效')
        # Evidence is deliberately not trusted here: the committed offered graph
        # is the authoritative allowlist, even if the caller's list has changed.
        offered = set(payload['offered'])
        mentioned = set(_ANSWER_CITATION.findall(answer))
        cited = sorted(offered & mentioned, key=lambda value: (len(value), value))
        warnings = [_warning('unknown_citation', f'answer:{answer_id}',
                             f'答案引用了未提供的证据 [{citation[:100]}]。')
                    for citation in sorted(mentioned - offered, key=lambda value: (len(value), value))]
        edges = [edge for edge in self.repository.list_provenance_edges(project_id, activity_id=answer_id)
                 if edge['relation'] != 'cites']
        next_ordinal = max((edge['ordinal'] for edge in edges), default=-1) + 1
        for citation in cited:
            offered_edge = self._offered_edge(answer_id, citation, edges)
            edges.append({'activity_id': answer_id, 'source_ref': f'answer:{answer_id}#{citation}',
                          'relation': 'cites', 'target_ref': offered_edge['target_ref'],
                          'ordinal': next_ordinal, 'payload': {}})
            next_ordinal += 1
        self.repository.complete_provenance_activity(project_id, answer_id, {
            **payload, 'answer': answer, 'mode': mode, 'cited': cited, 'warnings': warnings}, edges=edges)

    def fail_activity(self, project_id, activity_id, status, public_error):
        if status not in {'failed', 'cancelled'}:
            raise ValueError('失败活动状态必须是 failed 或 cancelled')
        activity = self.repository.get_provenance_activity(project_id, activity_id)
        if activity['kind'] == 'answer':
            activity = self._ensure_edge_v1(project_id, activity)
        if isinstance(public_error, dict):
            error = {'type': _text(public_error.get('type'), 100),
                     'message': _text(public_error.get('message'), 500)}
        else:
            error = {'type': 'Cancelled' if status == 'cancelled' else 'Error',
                     'message': _text(public_error, 500)}
        transition = (self.repository.cancel_provenance_activity if status == 'cancelled'
                      else self.repository.fail_provenance_activity)
        return transition(project_id, activity_id, {**activity['payload'], 'error': error},
                          edges=self.repository.list_provenance_edges(project_id, activity_id=activity_id))

    def trace_answer_evidence(self, project_id, answer_id, citation):
        activity = self.repository.get_provenance_activity(project_id, answer_id)
        activity = self._ensure_edge_v1(project_id, activity)
        payload = activity['payload']
        if activity['kind'] != 'answer' or citation not in payload.get('offered', ()):
            raise KeyError(citation)
        run_id = payload['run_id']
        retrieval = self.repository.get_provenance_activity(project_id, run_id)
        request = retrieval['payload']
        answer_edges = self.repository.list_provenance_edges(project_id, activity_id=answer_id)
        offered = self._offered_edge(answer_id, citation, answer_edges)
        graph_edges = [edge for edge in answer_edges
                       if edge['relation'] == 'used' or
                       edge['payload'].get('citation') == citation or
                       (edge['relation'] == 'cites' and
                        edge['source_ref'] == f'answer:{answer_id}#{citation}')]
        graph_edges += [edge for edge in self.repository.list_provenance_edges(
            project_id, activity_id=run_id)
            if edge['relation'] == 'considered' and edge['payload'].get('citation') == citation]
        citation_status = ('cited' if citation in payload['cited'] else
                           'uncited' if activity['status'] == 'completed' else 'offered')
        nodes = _TraceProjection(
            self.repository, project_id, graph_edges).nodes() + [
            _node('answer', f'answer:{answer_id}', '答案', activity['status'], activity['started_at'],
                  answer_id=answer_id, mode=payload['mode']),
            _node('answer', f'answer:{answer_id}#{citation}', citation, citation_status,
                  activity['started_at'], answer_id=answer_id, citation=citation),
            _node('retrieval', f'retrieval-run:{run_id}', '检索', retrieval['status'],
                  retrieval['started_at'], run_id=run_id),
        ]
        warnings = [dict(warning) for warning in offered['payload'].get('warnings', ())]
        warnings += [dict(warning) for warning in payload['warnings']]
        warnings.sort(key=lambda item: (item['node_ref'], item['code'], item['message']))
        return {
            'schema_version': '1.0',
            'subject': {'type': 'answer_evidence', 'ref': f'answer:{answer_id}#{citation}',
                        'answer_id': answer_id, 'citation': citation},
            'answer': {'status': activity['status'], 'citation_status': citation_status},
            'retrieval': {'run_ref': f'retrieval-run:{run_id}', 'query': request['query'],
                          'requested_mode': request['requested_mode'], 'active_modes': request['active_modes'],
                          **{key: offered['payload'][key] for key in ('rank', 'score', 'selected')}},
            'nodes': sorted(nodes, key=lambda node: (_NODE_ORDER[node['type']], node['ref'])),
            'edges': [{key: edge[key] for key in ('source_ref', 'relation', 'target_ref', 'ordinal', 'payload')}
                      for edge in sorted(graph_edges, key=lambda edge: (edge['ordinal'], edge['source_ref'], edge['target_ref']))],
            'integrity': {'complete': not warnings, 'warnings': warnings},
        }

    @staticmethod
    def _offered_edge(answer_id, citation, edges):
        matches = [edge for edge in edges
                   if edge['source_ref'] == f'answer:{answer_id}#{citation}'
                   and edge['relation'] == 'offered']
        if len(matches) != 1:
            raise KeyError(citation)
        return matches[0]

    def _ensure_edge_v1(self, project_id, activity):
        payload = activity['payload']
        if payload.get('graph_schema') == 'edge-v1' or 'graphs' not in payload:
            return activity
        graphs = payload.get('graphs')
        run_id = payload.get('run_id')
        if not isinstance(graphs, dict) or not isinstance(run_id, str) or not run_id:
            raise ValueError('旧溯源活动格式无效，无法安全迁移')
        answer_id = activity['id']
        answer_edges, retrieval_edges = self._migrate_edge_v0_edges(
            project_id, answer_id, run_id, payload, graphs)
        migrated_payload = {key: value for key, value in payload.items() if key != 'graphs'}
        migrated_payload['graph_schema'] = 'edge-v1'
        return self.repository.migrate_legacy_answer_graph(
            project_id, answer_id=answer_id, retrieval_id=run_id,
            expected_payload=payload, answer_payload=migrated_payload,
            answer_edges=answer_edges, retrieval_edges=retrieval_edges)

    def _migrate_edge_v0_edges(self, project_id, answer_id, run_id, payload, graphs):
        existing_answer = self.repository.list_provenance_edges(
            project_id, activity_id=answer_id)
        existing_retrieval = self.repository.list_provenance_edges(
            project_id, activity_id=run_id)
        created = {(edge['activity_id'], edge['source_ref'], edge['relation'],
                    edge['target_ref'], edge['ordinal']): edge['created_at']
                   for edge in [*existing_answer, *existing_retrieval]}
        answer_edges, retrieval_edges = [], []

        def append(target, activity_id, source_ref, relation, target_ref, edge_payload,
                   legacy=None):
            edge = {'activity_id': activity_id, 'source_ref': source_ref,
                    'relation': relation, 'target_ref': target_ref,
                    'ordinal': len(target), 'payload': edge_payload}
            if legacy is not None:
                key = (legacy.get('activity_id'), legacy.get('source_ref'),
                       legacy.get('relation'), legacy.get('target_ref'),
                       legacy.get('ordinal'))
                if key in created:
                    edge['created_at'] = created[key]
            target.append(edge)

        used = next((edge for edge in existing_answer if edge['relation'] == 'used'), None)
        if used is None:
            raise ValueError('旧溯源活动缺少 answer → retrieval 关联')
        append(answer_edges, answer_id, used['source_ref'], 'used', used['target_ref'], {}, used)
        occurrence = 0
        for citation in payload.get('offered', ()):
            graph = graphs.get(citation)
            if not isinstance(citation, str) or not _CITATION.fullmatch(citation) or not isinstance(graph, dict):
                raise ValueError('旧溯源活动的证据引用无效')
            record_ref = graph.get('record_ref')
            graph_edges = graph.get('edges')
            if not isinstance(record_ref, str) or not isinstance(graph_edges, list):
                raise ValueError('旧溯源活动的证据图无效')
            audit = graph.get('audit') if isinstance(graph.get('audit'), dict) else {}
            frozen = {
                'rank': audit.get('rank') if type(audit.get('rank')) is int else None,
                'score': audit.get('score') if type(audit.get('score')) in (int, float)
                         and math.isfinite(audit['score']) else None,
                'channel': _text(audit.get('channel')),
                'selected': audit.get('selected') is True,
                'citation': citation,
                'warnings': self._legacy_warnings(graph.get('warnings')),
            }
            citation_ref = f'answer:{answer_id}#{citation}'
            old_offered = next((edge for edge in graph_edges
                                if edge.get('relation') == 'offered'
                                and edge.get('source_ref') == citation_ref), None)
            old_considered = next((edge for edge in graph_edges
                                   if edge.get('relation') == 'considered'
                                   and edge.get('activity_id') == run_id), None)
            if old_offered is None or old_considered is None:
                raise ValueError('旧溯源活动缺少 offered/considered 关联')
            append(answer_edges, answer_id, citation_ref, 'offered', record_ref,
                   frozen, old_offered)
            append(retrieval_edges, run_id, f'retrieval-run:{run_id}', 'considered',
                   record_ref, frozen, old_considered)
            for edge in graph_edges:
                relation = edge.get('relation')
                if edge.get('activity_id') != answer_id or relation in {
                        'used', 'offered', 'cites'}:
                    continue
                if relation == 'sourced-from' and isinstance(
                        edge.get('target_ref'), str) and edge['target_ref'].startswith('document-version:'):
                    occurrence += 1
                    occurrence_ref = f'source-occurrence:{answer_id}#{citation}:legacy:{occurrence}'
                    start, end = self._legacy_source_span(
                        project_id, edge['source_ref'], edge['target_ref'], edge.get('payload'))
                    append(answer_edges, answer_id, edge['source_ref'], 'sourced-from',
                           occurrence_ref, {'start_char': start, 'end_char': end,
                                            'citation': citation}, edge)
                    append(answer_edges, answer_id, occurrence_ref, 'sourced-from',
                           edge['target_ref'], {'citation': citation}, edge)
                    continue
                migrated = {'citation': citation}
                if relation == 'processed-by':
                    old_payload = edge.get('payload') if isinstance(edge.get('payload'), dict) else {}
                    migrated.update({key: old_payload.get(key) for key in (
                        'run_id', 'attempt', 'status_at_capture', 'created_at',
                        'updated_at_at_capture')})
                elif relation == 'supported-by' and isinstance(
                        edge.get('target_ref'), str) and edge['target_ref'].startswith('assertion:'):
                    assertion = self.repository.get_assertion(
                        project_id, edge['target_ref'].removeprefix('assertion:'))
                    migrated['support_origin'] = ('source' if any(assertion.get(key) for key in (
                        'document_id', 'document_version_id', 'chunk_id')) else 'manual')
                append(answer_edges, answer_id, edge['source_ref'], relation,
                       edge['target_ref'], migrated, edge)
        for edge in existing_answer:
            if edge['relation'] == 'cites':
                append(answer_edges, answer_id, edge['source_ref'], 'cites',
                       edge['target_ref'], {}, edge)
        return answer_edges, retrieval_edges

    @staticmethod
    def _legacy_warnings(raw):
        warnings = []
        for item in raw if isinstance(raw, list) else ():
            if not isinstance(item, dict):
                continue
            code, ref, message = (_text(item.get('code'), 100),
                                  _text(item.get('node_ref'), 500),
                                  _text(item.get('message'), 500))
            if all(isinstance(value, str) and value for value in (code, ref, message)):
                warnings.append(_warning(code, ref, message))
        return warnings

    def _legacy_source_span(self, project_id, source_ref, document_ref, raw):
        document = self.repository.get_record_version(
            project_id, document_ref.removeprefix('document-version:'))
        text = document['text']
        raw = raw if isinstance(raw, dict) else {}
        before, highlight, after = (raw.get(key) if isinstance(raw.get(key), str) else ''
                                    for key in ('excerpt_before', 'highlight', 'excerpt_after'))
        context = before + highlight + after
        if highlight and context:
            indexes, offset = [], 0
            while True:
                index = text.find(context, offset)
                if index < 0:
                    break
                indexes.append(index)
                offset = index + 1
            if len(indexes) == 1:
                start = indexes[0] + len(before)
                return start, start + len(highlight)
        if source_ref.startswith('chunk-version:'):
            chunk = self.repository.get_record_version(
                project_id, source_ref.removeprefix('chunk-version:'))
            metadata = chunk.get('metadata') if isinstance(chunk.get('metadata'), dict) else {}
            start, end = metadata.get('start_char'), metadata.get('end_char')
            if type(start) is int and type(end) is int and 0 <= start < end <= len(text):
                return start, end
        return 0, min(len(text), 1000)


class _SourceCapture:
    """Resolve one offered version without any canonical/current-state fallback."""

    def __init__(self, repository, project_id, occurrence_scope):
        self.repo, self.project = repository, project_id
        self.occurrence_scope = occurrence_scope
        self.occurrence_index = 0
        self.nodes, self.edges, self.warnings = {}, [], []

    def add_node(self, node):
        self.nodes.setdefault(node['ref'], node)
        return node['ref']

    def edge(self, source, relation, target, payload=None):
        self.edges.append({'source_ref': source, 'relation': relation,
                           'target_ref': target, 'payload': payload or {}})

    def warn(self, code, ref, message):
        warning = _warning(code, ref, message)
        if warning not in self.warnings:
            self.warnings.append(warning)

    def capture(self, evidence):
        version_id = evidence.get('version_id')
        if not isinstance(version_id, str) or not version_id:
            raise ValueError('检索证据缺少不可变 version_id')
        record = self.repo.get_record_version(self.project, version_id)
        if record['id'] != evidence.get('id'):
            raise ValueError('检索证据记录与版本不匹配')
        ref = self.add_node(_node(
            'record_version', f'record-version:{version_id}', _text(record['text']) or record['id'],
            'captured', record['recorded_at'], record_id=record['id'], version=record['version'],
            kind=record['kind'], text_preview=_text(record['text']), valid_from=record.get('valid_from'),
            valid_until=record.get('valid_until'), recorded_at=record['recorded_at'], terminal_reason=None))
        if record['kind'] == 'chunk':
            chunk_ref = self.chunk(record)
            self.edge(ref, 'supported-by', chunk_ref)
        elif record['kind'] in {'entity', 'relation'}:
            mappings = self.repo.list_record_version_assertions(self.project, record_version_id=version_id)
            if not mappings:
                self.warn('record_version_support_ambiguous', ref, '历史记录版本缺少可唯一确定的断言支撑映射。')
            else:
                manual = True
                for mapping in mappings:
                    if not self.assertion(ref, mapping):
                        manual = False
                if manual:
                    self.nodes[ref]['details']['terminal_reason'] = 'manual_record'
        elif record['kind'] == 'document':
            self.document(record, ref, {}, {})
        return {'record_ref': ref, 'nodes': list(self.nodes.values()),
                'edges': self.edges, 'warnings': self.warnings}

    def assertion(self, record_ref, mapping):
        assertion_ref = 'assertion:' + mapping['assertion_id']
        try:
            assertion = self.repo.get_assertion(self.project, mapping['assertion_id'])
        except KeyError:
            self.warn('assertion_missing', record_ref, '已映射断言不存在。')
            return False
        self.add_node(_node('assertion', assertion_ref, _text(assertion.get('quote')) or '断言',
            assertion['status'], assertion['created_at'], assertion_id=assertion['id'],
            kind=assertion['kind'], status_at_capture=assertion['status'],
            quote=_text(assertion.get('quote'), 1000), start_char=assertion.get('start_char'),
            end_char=assertion.get('end_char')))
        has_source = any(assertion.get(key) for key in (
            'document_id', 'document_version_id', 'chunk_id'))
        self.edge(record_ref, 'supported-by', assertion_ref, {
            'support_origin': 'source' if has_source else 'manual'})
        events = self.repo.list_assertion_events(self.project, assertion['id'])
        event = next((event for event in events if event['id'] == mapping['assertion_event_id']), None)
        if event is None:
            self.warn('assertion_event_missing', assertion_ref, '映射的精确审核事件不存在。')
        else:
            event_ref = self.add_node(_node('review_event', 'review-event:' + event['id'], '审核决定',
                event['to_status'], event['created_at'], event_id=event['id'],
                from_status=event['from_status'], to_status=event['to_status'],
                actor=_text(event['actor']), reason=_text(event['reason'], 1000), created_at=event['created_at']))
            self.edge(assertion_ref, 'decided-by', event_ref)
        if not has_source:
            return True
        if not assertion.get('document_version_id'):
            self.warn('legacy_unversioned_source', assertion_ref, '断言来源没有精确文档版本。')
            return False
        matches = [chunk for chunk in self.repo.history(self.project, assertion['chunk_id'])
                   if chunk.get('kind') == 'chunk' and
                   chunk.get('metadata', {}).get('source_version_id') == assertion['document_version_id']
                   ] if assertion.get('chunk_id') else []
        if len(matches) != 1:
            self.warn('chunk_version_missing' if not matches else 'chunk_version_ambiguous',
                      assertion_ref, '无法唯一定位断言所用的片段版本。')
            return False
        chunk = matches[0]
        if chunk.get('source_id') != assertion.get('document_id'):
            self.warn('document_version_mismatch', assertion_ref, '断言与片段的文档身份不匹配。')
            return False
        chunk_ref = self.chunk(chunk, assertion)
        self.edge(assertion_ref, 'extracted-from', chunk_ref)
        return False

    def chunk(self, chunk, anchor=None):
        ref = 'chunk-version:' + chunk['version_id']
        metadata = self._chunk_metadata(chunk.get('metadata'), ref)
        self.add_node(_node('chunk_version', ref, '原文片段',
            'captured', chunk['recorded_at'], record_id=chunk['id'], version_id=chunk['version_id'],
            text_preview=_text(chunk['text']), start_char=metadata.get('start_char'), end_char=metadata.get('end_char')))
        version_id = metadata['source_version_id']
        if version_id is None:
            return ref
        try:
            document = self.repo.get_record_version(self.project, version_id)
        except KeyError:
            self.warn('document_version_missing', ref, '片段所引用的历史文档版本不存在。')
            return ref
        if document['kind'] != 'document' or document['id'] != chunk.get('source_id'):
            self.warn('document_version_mismatch', ref, '片段与历史文档版本身份不匹配。')
            return ref
        if metadata['end_char'] is not None and metadata['end_char'] > len(document['text']):
            metadata['start_char'] = metadata['end_char'] = None
            self.nodes[ref]['details'].update(start_char=None, end_char=None)
            self.warn('source_span_invalid', ref, '片段定位区间超出历史文档范围。')
        self.document(document, ref, anchor or metadata, metadata)
        return ref

    def _chunk_metadata(self, raw, ref):
        """Allow only typed source anchors; metadata itself is untrusted JSON."""
        raw = raw if isinstance(raw, dict) else {}
        start, end = raw.get('start_char'), raw.get('end_char')
        if not (type(start) is int and type(end) is int and 0 <= start < end):
            start = end = None
            self.warn('source_span_invalid', ref, '片段定位区间缺失或无效。')
        metadata = {'start_char': start, 'end_char': end}
        for key, code, message in (
                ('source_version_id', 'source_version_invalid', '片段来源版本标识必须是非空字符串。'),
                ('run_id', 'ingest_run_invalid', '片段摄取运行标识必须是非空字符串。')):
            value = raw.get(key)
            metadata[key] = value if isinstance(value, str) and value.strip() else None
            if key in raw and metadata[key] is None:
                self.warn(code, ref, message)
        if 'source_version_id' not in raw:
            self.warn('legacy_unversioned_source', ref, '片段来源没有精确文档版本。')
        return metadata

    def document(self, document, source_ref, anchor, metadata):
        text = document['text']
        start, end = anchor.get('start_char'), anchor.get('end_char')
        valid_span = type(start) is int and type(end) is int and 0 <= start < end <= len(text)
        if not valid_span:
            start, end = 0, min(len(text), 1000)
            if anchor:
                self.warn('source_span_invalid', source_ref, '历史来源的定位区间缺失或无效。')
        details = {'document_id': document['id'], 'version_id': document['version_id'],
                   'version': document['version'],
                   'title': _text(document.get('metadata', {}).get('title')) or document['id']}
        ref = self.add_node(_node('document_version', 'document-version:' + document['version_id'],
            details['title'], 'captured', document['recorded_at'], **details))
        self.occurrence_index += 1
        occurrence_ref = f'source-occurrence:{self.occurrence_scope}:{self.occurrence_index}'
        self.edge(source_ref, 'sourced-from', occurrence_ref, {
            'start_char': start, 'end_char': end})
        self.edge(occurrence_ref, 'sourced-from', ref)
        run_id = metadata.get('run_id')
        if not run_id:
            return
        try:
            run = self.repo.get_ingest_run(self.project, run_id)
        except KeyError:
            self.warn('ingest_run_missing', ref, '片段所引用的摄取运行不存在。')
            return
        if run['document_id'] != document['id'] or run['document_version_id'] != document['version_id']:
            self.warn('ingest_run_mismatch', ref, '摄取运行与冻结的文档版本不匹配。')
            return
        snapshot = {'run_id': run['id'], 'attempt': run['attempt'], 'status_at_capture': run['status'],
                    'created_at': run['created_at'], 'updated_at_at_capture': run['updated_at']}
        run_ref = self.add_node(_node('ingest_run', 'ingest-run:' + run['id'], '摄取运行',
            run['status'], run['created_at'], **snapshot))
        self.edge(ref, 'processed-by', run_ref, snapshot)


class _TraceProjection:
    """Resolve a frozen edge branch from immutable records and exact event refs."""

    def __init__(self, repository, project_id, edges):
        self.repo, self.project, self.edges = repository, project_id, edges
        self.refs = {ref for edge in edges for ref in (edge['source_ref'], edge['target_ref'])}

    def nodes(self):
        nodes = []
        assertion_events = self._assertion_events()
        for ref in sorted(self.refs):
            node = self._node(ref, assertion_events)
            if node is not None:
                nodes.append(node)
        self._mark_manual_records(nodes)
        return nodes

    def _node(self, ref, assertion_events):
        if ref.startswith('record-version:'):
            record = self.repo.get_record_version(self.project, ref.removeprefix('record-version:'))
            return _node('record_version', ref, _text(record['text']) or record['id'], 'captured',
                record['recorded_at'], record_id=record['id'], version=record['version'],
                kind=record['kind'], text_preview=_text(record['text']),
                valid_from=record.get('valid_from'), valid_until=record.get('valid_until'),
                recorded_at=record['recorded_at'], terminal_reason=None)
        if ref.startswith('chunk-version:'):
            chunk = self.repo.get_record_version(self.project, ref.removeprefix('chunk-version:'))
            metadata = chunk.get('metadata') if isinstance(chunk.get('metadata'), dict) else {}
            start, end = metadata.get('start_char'), metadata.get('end_char')
            if not (type(start) is int and type(end) is int and 0 <= start < end):
                start = end = None
            if end is not None:
                occurrence = next((edge['target_ref'] for edge in self.edges
                                   if edge['source_ref'] == ref
                                   and edge['relation'] == 'sourced-from'
                                   and edge['target_ref'].startswith('source-occurrence:')), None)
                document_ref = next((edge['target_ref'] for edge in self.edges
                                     if edge['source_ref'] == occurrence
                                     and edge['relation'] == 'sourced-from'
                                     and edge['target_ref'].startswith('document-version:')), None)
                if document_ref is not None:
                    document = self.repo.get_record_version(
                        self.project, document_ref.removeprefix('document-version:'))
                    if end > len(document['text']):
                        start = end = None
            return _node('chunk_version', ref, '原文片段', 'captured', chunk['recorded_at'],
                record_id=chunk['id'], version_id=chunk['version_id'],
                text_preview=_text(chunk['text']), start_char=start, end_char=end)
        if ref.startswith('document-version:'):
            document = self.repo.get_record_version(self.project, ref.removeprefix('document-version:'))
            title = _text(document.get('metadata', {}).get('title')) or document['id']
            return _node('document_version', ref, title, 'captured', document['recorded_at'],
                document_id=document['id'], version_id=document['version_id'],
                version=document['version'], title=title, source_content='full_version')
        if ref.startswith('source-occurrence:'):
            return self._occurrence(ref)
        if ref.startswith('assertion:'):
            assertion = self.repo.get_assertion(self.project, ref.removeprefix('assertion:'))
            event = assertion_events.get(ref)
            status = event['to_status'] if event else assertion['status']
            return _node('assertion', ref, _text(assertion.get('quote')) or '断言', status,
                assertion['created_at'], assertion_id=assertion['id'], kind=assertion['kind'],
                status_at_capture=status, quote=_text(assertion.get('quote'), 1000),
                start_char=assertion.get('start_char'), end_char=assertion.get('end_char'))
        if ref.startswith('review-event:'):
            event = next((item for item in assertion_events.values()
                          if 'review-event:' + item['id'] == ref), None)
            if event is None:
                return None
            return _node('review_event', ref, '审核决定', event['to_status'], event['created_at'],
                event_id=event['id'], from_status=event['from_status'], to_status=event['to_status'],
                actor=_text(event['actor']), reason=_text(event['reason'], 1000), created_at=event['created_at'])
        if ref.startswith('ingest-run:'):
            edge = next((edge for edge in self.edges
                         if edge['relation'] == 'processed-by' and edge['target_ref'] == ref), None)
            if edge is None:
                return None
            snapshot = {key: edge['payload'].get(key) for key in (
                'run_id', 'attempt', 'status_at_capture', 'created_at', 'updated_at_at_capture')}
            return _node('ingest_run', ref, '摄取运行', snapshot['status_at_capture'],
                         snapshot['created_at'], **snapshot)
        return None

    def _assertion_events(self):
        result = {}
        for edge in self.edges:
            if edge['relation'] != 'decided-by' or not edge['source_ref'].startswith('assertion:'):
                continue
            assertion_id = edge['source_ref'].removeprefix('assertion:')
            event_id = edge['target_ref'].removeprefix('review-event:')
            event = next((item for item in self.repo.list_assertion_events(
                self.project, assertion_id) if item['id'] == event_id), None)
            if event is not None:
                result[edge['source_ref']] = event
        return result

    def _occurrence(self, ref):
        incoming = next((edge for edge in self.edges
                         if edge['relation'] == 'sourced-from' and edge['target_ref'] == ref), None)
        outgoing = next((edge for edge in self.edges
                         if edge['relation'] == 'sourced-from' and edge['source_ref'] == ref
                         and edge['target_ref'].startswith('document-version:')), None)
        if incoming is None or outgoing is None:
            return None
        document = self.repo.get_record_version(
            self.project, outgoing['target_ref'].removeprefix('document-version:'))
        text = document['text']
        start, end = incoming['payload'].get('start_char'), incoming['payload'].get('end_char')
        if not (type(start) is int and type(end) is int and 0 <= start < end <= len(text)):
            start, end = 0, min(len(text), 1000)
        title = _text(document.get('metadata', {}).get('title')) or document['id']
        return _node('source_occurrence', ref, '原文定位', 'captured', document['recorded_at'],
            document_id=document['id'], version_id=document['version_id'], version=document['version'],
            title=title, source_content='full_version', start_char=start, end_char=end,
            excerpt_before=text[max(0, start - 240):start],
            highlight=text[start:min(end, start + 1000)], excerpt_after=text[end:end + 240])

    def _mark_manual_records(self, nodes):
        outgoing = {}
        for edge in self.edges:
            outgoing.setdefault(edge['source_ref'], []).append(edge)
        assertion_refs = {node['ref'] for node in nodes if node['type'] == 'assertion'}
        for node in nodes:
            if node['type'] != 'record_version':
                continue
            support_edges = [edge for edge in outgoing.get(node['ref'], ())
                             if edge['relation'] == 'supported-by'
                             and edge['target_ref'] in assertion_refs]
            if support_edges and all(edge['payload'].get('support_origin') == 'manual'
                                     for edge in support_edges):
                node['details']['terminal_reason'] = 'manual_record'
