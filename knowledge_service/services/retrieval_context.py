"""Small, bounded helpers for retrieval scope and neighboring source context."""
from __future__ import annotations
import secrets

from ..domain.evidence_scope import source_scope

def enrich_source_context(repository, project, scope, result):
    from ..repository.scoped_reads import ScopedReads
    reader = ScopedReads(repository)
    sources, neighbors = [], []
    authorizations = {}
    groups = result.get('platform_groups') or [result]
    for group in groups:
        group_scope = dict(scope)
        platform = group.get('platform')
        mode = group.get('source_scope')
        if mode in {'all', 'specific', 'unknown'}:
            group_scope['_platform_match'] = {'all': 'all_only', 'specific': 'specific_only', 'unknown': 'unknown_only'}[mode]
        if platform:
            group_scope['applicability'] = {**(scope.get('applicability') or {}),
                'platforms': {'mode': 'specific', 'values': [platform]}}
        hit_rows = group.get('hits', [])
        context = reader.source_context(project, group_scope, hit_rows)
        anchors = {row['version_id']: row for row in hit_rows if row.get('version_id')}
        for key in ('source_chunks', 'context_chunks'):
            group[key] = []
            for row in context[key]:
                versions = row.get('_source_anchor_versions', [])
                anchor = next((anchors[version] for version in versions if version in anchors), None)
                if anchor is None:
                    continue
                token = secrets.token_urlsafe(24)
                authorizations[token] = {
                    'artifact_id': row['id'], 'artifact_version_id': row['version_id'],
                    'anchor_id': anchor['id'], 'anchor_version_id': anchor['version_id'],
                    'query_platform': platform, 'source_scope': mode,
                    'filters': source_scope(group_scope, anchor).get('filters')}
                group[key].append({**{name: value for name, value in row.items()
                                      if name != '_source_anchor_versions'},
                    'query_platform': platform, 'source_scope': mode,
                    '_source_context_authorization': token})
        sources.extend(group['source_chunks'])
        neighbors.extend(group['context_chunks'])
    result['source_chunks'] = sources
    result['context_chunks'] = neighbors
    result['_source_context_authorizations'] = authorizations
    return result
