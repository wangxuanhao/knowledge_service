"""Fail-closed validation for repository projection restores."""


FULL_TOP_LEVEL = {
    'namespace', 'schema_version', 'governance_history_included', 'project',
    'records', 'ontologies', 'artifacts', 'governance', 'provenance',
}
FULL_GOVERNANCE = {
    'assertions', 'assertion_events', 'fact_keys', 'ingest_runs',
    'resolution_reviews', 'merge_operations', 'ontology',
    'attribute_conflict_sets','attribute_conflict_members',
}
FULL_ONTOLOGY = {'drafts', 'operations', 'decisions', 'publish_requests'}
FULL_PROVENANCE = {'activities', 'edges', 'record_version_assertions'}


def _contract(fields, optional=(), *, extensible=False):
    required = frozenset(fields)
    return required, required | frozenset(optional), extensible


# A full projection is a versioned wire format, not a loose database dump.
# Nullable database columns remain required because exporters always emit them.
FULL_ROW_CONTRACTS = {
    ('project',): _contract({
        'id', 'name', 'metadata', 'created_at',
    }),
    ('records',): _contract({
        'id', 'kind', 'text', 'metadata', 'valid_from', 'valid_until',
        'project_id', 'version', 'version_id', 'recorded_at', 'superseded_at',
    }, extensible=True),
    ('ontologies',): _contract({
        'id', 'project_id', 'turtle', 'summary', 'created_at', 'metadata',
    }),
    ('artifacts',): _contract({'id', 'kind', 'project_id', 'payload'}),
    ('governance', 'assertions'): _contract({
        'id', 'project_id', 'kind', 'document_id', 'document_version_id',
        'chunk_id', 'source_hash', 'start_char', 'end_char', 'quote', 'payload',
        'status', 'canonical_record_id', 'decision_reason', 'decision_version',
        'created_at', 'decided_at', 'actor',
    }),
    ('governance', 'assertion_events'): _contract({
        'id', 'assertion_id', 'project_id', 'from_status', 'to_status',
        'decision_version', 'reason', 'actor', 'canonical_record_id', 'created_at',
    }),
    ('governance', 'fact_keys'): _contract({
        'project_id', 'fact_key', 'canonical_record_id', 'created_at', 'retired_at',
    }),
    ('governance', 'ingest_runs'): _contract({
        'id', 'project_id', 'document_id', 'document_version_id', 'attempt',
        'retry_of', 'status', 'active_stage', 'readiness', 'counts', 'failure',
        'version', 'created_at', 'updated_at',
    }),
    ('governance', 'resolution_reviews'): _contract({
        'id', 'project_id', 'source_entity_id', 'candidate_entity_id', 'score',
        'status', 'decision_version', 'payload', 'reason', 'actor', 'created_at',
        'decided_at',
    }),
    ('governance', 'merge_operations'): _contract({
        'id', 'project_id', 'operation', 'status', 'redirects', 'assertion_moves',
        'before_state', 'after_state', 'expected_versions', 'reversal_of',
        'created_at',
    }),
    ('governance','attribute_conflict_sets'): _contract({
        'project_id','id','entity_id','predicate','version','state','operation_id',
        'created_at','resolved_at','reason','actor','decisions',
    }),
    ('governance','attribute_conflict_members'): _contract({
        'project_id','set_id','record_id','version','version_id','assertion_versions',
    }),
    ('governance', 'ontology', 'drafts'): _contract({
        'id', 'project_id', 'base_ontology_id', 'source_kind', 'status', 'revision',
        'title', 'summary', 'source_context', 'validation_report',
        'validation_fingerprint', 'validated_at', 'submitted_at',
        'published_ontology_id', 'created_at', 'updated_at',
    }),
    ('governance', 'ontology', 'operations'): _contract({
        'id', 'project_id', 'draft_id', 'action', 'target_iri', 'risk',
        'fingerprint', 'reason', 'supersedes_operation_id', 'created_at', 'before',
        'after', 'evidence', 'impact', 'validation',
    }),
    ('governance', 'ontology', 'decisions'): _contract({
        'id', 'project_id', 'draft_id', 'operation_id', 'operation_fingerprint',
        'action', 'reason', 'actor', 'supersedes_decision_id', 'created_at',
    }),
    ('governance', 'ontology', 'publish_requests'): _contract({
        'id', 'project_id', 'draft_id', 'idempotency_key', 'request_hash',
        'result_ontology_id', 'created_at', 'completed_at',
    }),
    ('provenance', 'record_version_assertions'): _contract({
        'project_id', 'record_id', 'record_version_id', 'assertion_id',
        'assertion_event_id', 'created_at',
    }),
    ('provenance', 'activities'): _contract({
        'id', 'project_id', 'kind', 'status', 'payload', 'started_at', 'completed_at',
    }),
    ('provenance', 'edges'): _contract({
        'id', 'project_id', 'activity_id', 'source_ref', 'relation', 'target_ref',
        'ordinal', 'payload', 'created_at',
    }),
}
# 快照线格式与数据库迁移编号分别管理。19 增加冻结冲突成员与集合决定；
# 导出与恢复只使用这一种当前行形状。
LATEST_FULL_SNAPSHOT_FORMAT = 19
_RECORD_KINDS = {'document', 'entity', 'relation', 'attribute', 'chunk'}


def _require_exact_section(section, expected, label):
    if not isinstance(section, dict) or set(section) != expected:
        missing = sorted(expected - set(section)) if isinstance(section, dict) else sorted(expected)
        unknown = sorted(set(section) - expected) if isinstance(section, dict) else []
        raise ValueError(
            f'完整治理备份 {label} 字段不匹配：缺少 {missing}，未知 {unknown}')


def _section(snapshot, path):
    value = snapshot
    for key in path:
        value = value[key]
    return value


def _require_exact_row(row, required, allowed, label, extensible=False):
    if not isinstance(row, dict):
        raise ValueError(f'完整治理备份 {label} 行必须是对象')
    fields = set(row)
    missing = sorted(required - fields)
    unknown = [] if extensible else sorted(fields - allowed)
    if missing or unknown:
        raise ValueError(
            f'完整治理备份 {label} 字段不匹配：缺少 {missing}，未知 {unknown}')


def _validate_record_envelope(row, label):
    def reject(field):
        raise ValueError(f'完整治理备份 {label}.{field} 类型或值无效')

    if 'embedding' in row:
        raise ValueError(
            f'完整治理备份 {label}.embedding 是仅限本地存储的非备份字段')
    if not isinstance(row['id'], str) or not row['id']:
        reject('id')
    if not isinstance(row['kind'], str) or row['kind'] not in _RECORD_KINDS:
        reject('kind')
    if not isinstance(row['text'], str):
        reject('text')
    if not isinstance(row['metadata'], dict):
        reject('metadata')
    for field in ('valid_from', 'valid_until', 'superseded_at'):
        if row[field] is not None and not isinstance(row[field], str):
            reject(field)
    if type(row['version']) is not int or row['version'] < 1:
        reject('version')
    for field in ('version_id', 'recorded_at'):
        if not isinstance(row[field], str) or not row[field]:
            reject(field)


def _validate_full_rows(snapshot):
    for path, (required, allowed, extensible) in FULL_ROW_CONTRACTS.items():
        label = '.'.join(path)
        section = _section(snapshot, path)
        if path == ('project',):
            _require_exact_row(section, required, allowed, label, extensible)
            continue
        if not isinstance(section, list):
            raise ValueError(f'完整治理备份 {label} 必须是列表')
        for index, row in enumerate(section):
            row_label = f'{label}[{index}]'
            _require_exact_row(
                row, required, allowed, row_label, extensible)
            if path == ('records',):
                _validate_record_envelope(row, row_label)


def _validate_conflict_ledger(snapshot):
    governance=snapshot['governance']
    sets={row['id']:row for row in governance['attribute_conflict_sets']}
    records={row['version_id']:row for row in snapshot['records']}
    events={(row['assertion_id'],row['decision_version']):row for row in governance['assertion_events']}
    operations={row['id'] for row in governance['merge_operations'] if row['operation']=='merge_rewrite'}
    membership=set()
    record_membership=set()
    for member in governance['attribute_conflict_members']:
        conflict=sets.get(member['set_id']);record=records.get(member['version_id'])
        if (not conflict or not record or record['id']!=member['record_id'] or record['version']!=member['version']
            or record.get('kind')!='attribute' or record.get('subject_id')!=conflict['entity_id']
            or record.get('type')!=conflict['predicate'] or not record.get('metadata',{}).get('_quarantined')):
            raise ValueError('完整治理备份冲突成员与冻结事实修订不一致')
        if not isinstance(member['assertion_versions'],list) or not 1<=len(member['assertion_versions'])<=1000:
            raise ValueError('完整治理备份冲突成员支撑列表无效')
        for assertion in member['assertion_versions']:
            if not isinstance(assertion,dict) or set(assertion)!={'id','version'} or type(assertion['version']) is not int:
                raise ValueError('完整治理备份冲突支撑决定版本无效')
            event=events.get((assertion['id'],assertion['version']))
            if not event or event['to_status']!='contradicting' or event['canonical_record_id']!=record['id']:
                raise ValueError('完整治理备份冲突成员与来源决定不一致')
        membership.add((member['set_id'],member['version_id']))
        record_membership.add((member['set_id'],member['record_id']))
    for conflict in sets.values():
        if (conflict['state'] not in {'open','resolved','reversed'} or type(conflict['version']) is not int
            or conflict['version']!=(1 if conflict['state']=='open' else 2)
            or (conflict['state']=='open')!=(conflict['resolved_at'] is None) or conflict['operation_id'] not in operations):
            raise ValueError('完整治理备份冲突集合状态无效')
        if sum(key[0]==conflict['id'] for key in membership)<2:raise ValueError('完整治理备份冲突集合缺少成员')
    for record in snapshot['records']:
        if record.get('metadata',{}).get('_quarantined') and not any(
            (set_id,record['id']) in record_membership for set_id in record.get('metadata',{}).get('conflict_set_ids',[])):
            raise ValueError('完整治理备份隔离事实缺少冲突集合')


def validate_restore_snapshot(snapshot):
    """Validate snapshot shape, format and project ownership.

    完整与轻量备份均须明确使用当前快照线格式；它与数据库迁移编号无关。
    """
    if not isinstance(snapshot, dict):
        raise ValueError('项目备份格式无效')
    history_flag = snapshot.get('governance_history_included')
    if type(history_flag) is not bool:
        raise ValueError('项目备份 governance_history_included 必须是布尔值')
    if not isinstance(snapshot.get('project'), dict):
        raise ValueError('项目备份格式无效')
    project_id = snapshot['project'].get('id')
    if not isinstance(project_id, str) or not project_id:
        raise ValueError('项目备份缺少稳定项目 ID')

    schema_version = snapshot.get('schema_version')
    if type(schema_version) is not int or schema_version != LATEST_FULL_SNAPSHOT_FORMAT:
        raise ValueError(
            f'项目备份 schema_version 必须是当前格式 {LATEST_FULL_SNAPSHOT_FORMAT}')

    declared_full = history_flag
    governance = snapshot.get('governance')
    ontology = governance.get('ontology') if isinstance(governance, dict) else None
    provenance = snapshot.get('provenance')
    if declared_full:
        _require_exact_section(snapshot, FULL_TOP_LEVEL, '顶层')
        _require_exact_section(governance, FULL_GOVERNANCE, 'governance')
        _require_exact_section(ontology, FULL_ONTOLOGY, 'governance.ontology')
        _require_exact_section(provenance, FULL_PROVENANCE, 'provenance')

    ontology = ontology or {
        'drafts': [], 'operations': [], 'decisions': [], 'publish_requests': []}
    provenance = provenance or {
        'activities': [], 'edges': [], 'record_version_assertions': []}
    artifacts = snapshot.get('artifacts') or []

    scoped_sections = [
        ('records', snapshot.get('records', [])),
        ('ontologies', snapshot.get('ontologies', [])),
        ('artifacts', artifacts),
    ]
    if isinstance(governance, dict):
        scoped_sections.extend(
            (f'governance.{key}', governance.get(key, []))
            for key in FULL_GOVERNANCE - {'ontology'})
    if isinstance(ontology, dict):
        scoped_sections.extend(
            (f'governance.ontology.{key}', ontology.get(key, []))
            for key in FULL_ONTOLOGY)
    if isinstance(provenance, dict):
        scoped_sections.extend(
            (f'provenance.{key}', provenance.get(key, []))
            for key in FULL_PROVENANCE)
    for label, rows in scoped_sections:
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or row.get('project_id') != project_id:
                raise ValueError(
                    f'{label}[{index}].project_id 必须等于项目 {project_id}')

    if declared_full:
        _validate_full_rows(snapshot)
        _validate_conflict_ledger(snapshot)
    elif any(row.get('metadata',{}).get('_quarantined') for row in snapshot.get('records',[])):
        raise ValueError('隔离属性需要完整治理备份，轻量备份不能恢复冲突事实')

    return {
        'declared_full': declared_full,
        'project_id': project_id,
        'governance': governance,
        'ontology': ontology,
        'provenance': provenance,
        'artifacts': artifacts,
    }
