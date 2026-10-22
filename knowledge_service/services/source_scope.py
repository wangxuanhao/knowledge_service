"""Inherit only verifiable scope from immutable source document versions."""
from copy import deepcopy
from ..domain.applicability import normalize_applicability


def inherit_source_scope(record, candidate, repository, project_id, cache=None):
    metadata = record.setdefault('metadata', {})
    explicit = normalize_applicability(metadata.get('applicability'))
    has_explicit_scope = any(value['mode'] != 'unknown' for value in explicit.values())
    inherited = normalize_applicability(None)
    version_id = candidate.get('document_version_id')
    document_id = candidate.get('document_id')
    if repository is not None and version_id and document_id:
        cache = {} if cache is None else cache
        if version_id not in cache:
            try:
                cache[version_id] = repository.get_record_version(project_id, version_id)
            except KeyError:
                cache[version_id] = None
        document = cache[version_id]
        if document is None:
            if not has_explicit_scope:
                metadata['applicability'] = inherited
            return record
        if document['id'] != document_id or document.get('kind') != 'document':
            raise ValueError('source document version does not match candidate')
        inherited = normalize_applicability((document.get('metadata') or {}).get('applicability'))
    if not has_explicit_scope:
        metadata['applicability'] = deepcopy(inherited)
    if repository is not None and version_id and document_id:
        for field in ('valid_from', 'valid_until'):
            if field not in candidate:
                record[field] = document.get(field)
    return record


def build_scope_repair(repository, project_id):
    """Return a reviewable plan; never infer sources from record names or IDs."""
    assertions = repository.list_assertions(project_id)
    by_id = {item['id']: item for item in assertions}
    repairs, conflicts, preserved = [], [], []
    for record in repository.current_records(project_id, kinds=['entity', 'relation', 'attribute']):
        metadata = record.get('metadata') or {}
        scope = normalize_applicability(metadata.get('applicability'))
        if metadata.get('_deleted') or any(value['mode'] != 'unknown' for value in scope.values()):
            preserved.append(record['id'])
            continue
        ids = set(metadata.get('discovery_candidate_ids') or [])
        if metadata.get('discovery_candidate_id'):
            ids.add(metadata['discovery_candidate_id'])
        sources = {item['id']: item for item in assertions
                   if item.get('canonical_record_id') == record['id']}
        sources.update({key: by_id[key] for key in ids if key in by_id})
        reason, scopes, refs = None, [], []
        if not sources or ids - set(by_id):
            reason = 'unproven_source'
        for assertion in sorted(sources.values(), key=lambda row: row['id']):
            document_id, version_id = assertion.get('document_id'), assertion.get('document_version_id')
            if not document_id or not version_id:
                reason = 'unproven_source'
                continue
            try:
                document = repository.get_record_version(project_id, version_id)
            except KeyError:
                reason = 'missing_source_version'
                continue
            if document.get('id') != document_id or document.get('kind') != 'document':
                reason = 'source_identity_mismatch'
                continue
            inherited = normalize_applicability((document.get('metadata') or {}).get('applicability'))
            scopes.append(inherited)
            refs.append({'assertion_id': assertion['id'], 'assertion_status': assertion['status'],
                         'expected_assertion_version': assertion['decision_version'],
                         'document_id': document_id, 'document_version_id': version_id,
                         'valid_from': document.get('valid_from'), 'valid_until': document.get('valid_until')})
        if scopes and any(item != scopes[0] for item in scopes):
            reason = 'source_scope_conflict'
        if not scopes or not any(value['mode'] != 'unknown' for value in scopes[0].values()):
            reason = reason or 'unknown_source_scope'
        if reason:
            conflicts.append({'record_id': record['id'], 'reason': reason, 'sources': refs})
            continue
        repairs.append({'record_id': record['id'], 'kind': record['kind'],
                        'expected_version': record['version'], 'expected_version_id': record['version_id'],
                        'applicability': scopes[0], 'sources': refs,
                        'valid_from': record.get('valid_from'), 'valid_until': record.get('valid_until')})
    return {'project_id': project_id, 'repairs': repairs, 'conflicts': conflicts, 'preserved': preserved}


def prepare_scope_repair(repository, report):
    """Recheck proof and revisions, then prepare the normal atomic write plan."""
    from .formal_writes import FormalFactWriter, _writable
    from uuid import uuid4
    project_id = report['project_id']
    if build_scope_repair(repository, project_id) != report:
        raise ValueError('source scope repair version conflict; create a fresh dry-run')
    records, expected = [], {}
    for repair in report['repairs']:
        current = repository.get_record(project_id, repair['record_id'])
        if current['version_id'] != repair['expected_version_id']:
            raise ValueError('source scope repair version conflict')
        row = _writable(current)
        row.setdefault('metadata', {})['applicability'] = deepcopy(repair['applicability'])
        row['metadata']['source_scope_repair'] = {'sources': deepcopy(repair['sources'])}
        records.append(row)
        expected[row['id']] = repair['expected_version']
    if not records:
        return None
    # Self redirects carry accepted provenance onto the new version while keeping
    # pending assertions pending. No source is invented or accepted by this repair.
    ledger = {'id': str(uuid4()), 'operation': 'source_scope_repair',
              'redirects': {row['id']: row['id'] for row in records},
              'before_state': [], 'expected_versions': expected}
    plan = FormalFactWriter(repository).prepare('restore_snapshot', project_id, [], expected,
        {'records': records, 'suppress_auto_assertions': True, 'ledger': ledger})
    for repair in report['repairs']:
        if plan['dependencies']['records'].get(repair['record_id']) != repair['expected_version_id']:
            raise ValueError('source scope repair version conflict')
        for source in repair['sources']:
            if plan['dependencies']['assertions'].get(source['assertion_id']) != source['expected_assertion_version']:
                raise ValueError('source scope repair source version conflict')
    puts = [action['record'] for action in plan['actions'] if action['kind'] == 'record_put']
    if ({row['id'] for row in puts} != set(expected)
            or any(row.get('metadata', {}).get('_deleted') for row in puts)
            or any(action.get('kind') == 'fact_key_retarget' for action in plan['actions'])):
        raise ValueError('source scope repair would change canonical identity')
    return plan


def apply_scope_repair(service, report):
    plan = prepare_scope_repair(service.repository, report)
    if plan is None:
        return []
    result = service.repository.execute_formal_write(plan)
    service._drain_projections(report['project_id'])
    return result['accepted_records']
