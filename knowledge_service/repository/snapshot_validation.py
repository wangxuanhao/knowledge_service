"""Fail-closed validation for repository projection restores."""


FULL_TOP_LEVEL = {
    'namespace', 'schema_version', 'governance_history_included', 'project',
    'records', 'ontologies', 'artifacts', 'governance', 'provenance',
}
FULL_GOVERNANCE = {
    'assertions', 'assertion_events', 'fact_keys', 'ingest_runs',
    'resolution_reviews', 'merge_operations', 'ontology',
}
FULL_ONTOLOGY = {'drafts', 'operations', 'decisions', 'publish_requests'}
FULL_PROVENANCE = {'activities', 'edges', 'record_version_assertions'}


def _contract(fields, optional=(), *, extensible=False):
    required = frozenset(fields)
    return required, required | frozenset(optional), extensible


# A full projection is a versioned wire format, not a loose database dump.
# Nullable database columns remain required because exporters always emit them.
_FULL_ROW_CONTRACTS_V14 = {
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
    ('governance', 'ontology', 'drafts'): _contract({
        'id', 'project_id', 'base_ontology_id', 'source_kind', 'status', 'revision',
        'title', 'summary', 'source_context', 'validation_report',
        'validation_fingerprint', 'published_ontology_id', 'legacy_artifact_id',
        'created_at', 'updated_at',
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
# Migration 15 expands CHECK constraints but does not change the projection row
# shape, so v14 and v15 snapshots deliberately share the exact wire contract.
FULL_ROW_CONTRACTS = {
    14: _FULL_ROW_CONTRACTS_V14,
    15: _FULL_ROW_CONTRACTS_V14,
}

# ── 快照**线格式**版本（与数据库迁移编号无关）────────────────────────────────
#
# 这两个概念曾经被混为一谈：快照里的 `schema_version` 直接取
# `SELECT MAX(version) FROM schema_migrations`。而迁移编号是**按迁移文件**的
# （0001 → 1、0002 → 2），于是导出快照被标成 2，与这里的下限 14 矛盾 ——
# 备份恢复整体失败。二者回答的是不同问题，必须分开：
#   * 迁移编号：这个库的 schema 是哪一版（DB 自己的事）；
#   * 线格式版本：这份快照的形状是哪一版（文件自己的事，决定能否被解读）。
#
# 基线表的行形状与最后一代完整快照契约一致，故本项目导出的快照标 15，
# 并接受 14~15 的输入：14 与 15 的行形状相同（15 只收紧了 CHECK 约束，
# 见上方 FULL_ROW_CONTRACTS 的说明），因此两者共用同一套契约。
LATEST_FULL_SNAPSHOT_FORMAT = max(FULL_ROW_CONTRACTS)
MIN_FULL_SNAPSHOT_FORMAT = min(FULL_ROW_CONTRACTS)

# 兼容旧名：历史快照里 `schema_version` 与行形状版本同值，旧引用可能仍用此名。
MIN_FULL_SCHEMA_VERSION = MIN_FULL_SNAPSHOT_FORMAT
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


def _validate_full_rows(snapshot, schema_version):
    contracts = FULL_ROW_CONTRACTS.get(schema_version)
    if contracts is None:
        raise ValueError(f'项目备份 schema_version {schema_version} 不受支持')
    for path, (required, allowed, extensible) in contracts.items():
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


def validate_restore_snapshot(snapshot, current_snapshot_format):
    """Validate snapshot shape, version, ownership and artifact references.

    `current_snapshot_format` 是**快照线格式**版本（`LATEST_FULL_SNAPSHOT_FORMAT`），
    不是数据库迁移编号 —— 见该常量的说明。
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
    if history_flag:
        if type(schema_version) is not int:
            raise ValueError('完整治理项目备份必须包含整数 schema_version')
        if not MIN_FULL_SNAPSHOT_FORMAT <= schema_version <= current_snapshot_format:
            raise ValueError(
                f'项目备份 schema_version {schema_version} 不在支持范围 '
                f'{MIN_FULL_SNAPSHOT_FORMAT}..{current_snapshot_format}')
    elif schema_version is not None:
        if type(schema_version) is not int:
            raise ValueError('项目备份 schema_version 无效')
        if schema_version > current_snapshot_format:
            raise ValueError(
                f'项目备份 schema_version {schema_version} 新于当前版本 '
                f'{current_schema_version}')

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
        _validate_full_rows(snapshot, schema_version)
        artifact_ids = {row['id'] for row in artifacts}
        for draft in ontology['drafts']:
            artifact_id = draft.get('legacy_artifact_id')
            if artifact_id is not None and artifact_id not in artifact_ids:
                raise ValueError(
                    f'legacy_artifact_id {artifact_id} 未包含在完整备份 artifacts 中')

    return {
        'declared_full': declared_full,
        'project_id': project_id,
        'governance': governance,
        'ontology': ontology,
        'provenance': provenance,
        'artifacts': artifacts,
    }
