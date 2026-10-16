"""Compact QA evidence summaries derived only from frozen provenance."""
from __future__ import annotations


class AnswerEvidence:
    def __init__(self, provenance):
        self.provenance = provenance

    def summaries(self, project_id, answer_id, evidence):
        return [self._summary(project_id, answer_id, row) for row in evidence]

    def _summary(self, project_id, answer_id, row):
        citation = row['citation']
        trace = self.provenance.trace_answer_evidence(
            project_id, answer_id, citation)
        retrieval = trace.get('retrieval') or {}
        purpose = {key: retrieval.get(key, row.get(key)) for key in (
            'validity_status', 'validity_basis', 'answer_eligible', 'usage', 'exclusion_reasons',
            'source_eligibility')}
        nodes = {node.get('ref'): node for node in trace.get('nodes', ())
                 if isinstance(node, dict)}
        occurrence_edges = sorted((
            edge for edge in trace.get('edges', ())
            if edge.get('relation') == 'sourced-from'
            and str(edge.get('target_ref', '')).startswith(
                ('source-occurrence:', 'occ:'))
        ), key=lambda edge: (edge.get('ordinal', 0), edge.get('target_ref', '')))
        sources, seen = [], set()
        for edge in occurrence_edges:
            ref = edge.get('target_ref')
            details = (nodes.get(ref) or {}).get('details') or {}
            key = (details.get('document_id'), details.get('version_id'),
                   details.get('start_char'), details.get('end_char'))
            if key in seen or not any(key):
                continue
            seen.add(key)
            quote = str(details.get('highlight') or '')[:240]
            sources.append({
                'document_id': details.get('document_id'),
                'record_version_id': details.get('version_id'),
                'document_version_id': details.get('version_id'),
                'version': details.get('version'),
                'title': details.get('title') or details.get('document_id'),
                'quote': quote,
                'start_char': details.get('start_char'),
                'end_char': details.get('end_char'),
                **{key: details.get(key) for key in (
                    'valid_from', 'valid_until', 'applicability', 'published_at', 'effective_time_source')},
                **purpose,
            })
        warnings = trace.get('integrity', {}).get('warnings') or []
        reason = next((warning.get('code') for warning in warnings
                       if isinstance(warning, dict) and warning.get('code')), None)
        return {
            **{key: value for key, value in row.items() if not key.startswith('_')},
            **purpose,
            'citation': citation,
            'record_version_id': row.get('version_id'),
            'retrieval_channel': retrieval.get('requested_mode'),
            'ranker_version': retrieval.get('ranker_version'),
            'evidence_text': row.get('text_preview') or row.get('text') or '',
            'applicability': (row.get('metadata') or {}).get('applicability'),
            'published_at': (row.get('metadata') or {}).get('published_at'),
            'effective_time_source': (row.get('metadata') or {}).get('effective_time_source'),
            'valid_from': row.get('valid_from'),
            'valid_until': row.get('valid_until'),
            'sources': sources,
            'source_status': 'complete' if sources else 'missing',
            'source_reason': None if sources else (reason or 'manual_or_missing_source'),
            'provenance_ref': f'answer:{answer_id}#{citation}',
        }
