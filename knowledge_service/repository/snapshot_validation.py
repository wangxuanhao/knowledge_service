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


def _require_exact_section(section, expected, label):
    if not isinstance(section, dict) or set(section) != expected:
        missing = sorted(expected - set(section)) if isinstance(section, dict) else sorted(expected)
        unknown = sorted(set(section) - expected) if isinstance(section, dict) else []
        raise ValueError(
            f'完整治理备份 {label} 字段不匹配：缺少 {missing}，未知 {unknown}')


def validate_restore_snapshot(snapshot, current_schema_version):
    """Validate snapshot shape, version, ownership and artifact references."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('project'), dict):
        raise ValueError('项目备份格式无效')
    project_id = snapshot['project'].get('id')
    if not isinstance(project_id, str) or not project_id:
        raise ValueError('项目备份缺少稳定项目 ID')

    schema_version = snapshot.get('schema_version')
    if (isinstance(schema_version, bool)
            or (schema_version is not None and not isinstance(schema_version, int))):
        raise ValueError('项目备份 schema_version 无效')
    if isinstance(schema_version, int) and schema_version > current_schema_version:
        raise ValueError(
            f'项目备份 schema_version {schema_version} 新于当前版本 '
            f'{current_schema_version}')

    declared_full = snapshot.get('governance_history_included') is True
    governance = snapshot.get('governance')
    ontology = governance.get('ontology') if isinstance(governance, dict) else None
    provenance = snapshot.get('provenance')
    if declared_full:
        _require_exact_section(snapshot, FULL_TOP_LEVEL, '顶层')
        _require_exact_section(governance, FULL_GOVERNANCE, 'governance')
        _require_exact_section(ontology, FULL_ONTOLOGY, 'governance.ontology')
        _require_exact_section(provenance, FULL_PROVENANCE, 'provenance')
        for label, rows in (
                ('records', snapshot['records']),
                ('ontologies', snapshot['ontologies']),
                ('artifacts', snapshot['artifacts']),
                *((f'governance.{key}', governance[key])
                  for key in FULL_GOVERNANCE - {'ontology'}),
                *((f'governance.ontology.{key}', ontology[key])
                  for key in FULL_ONTOLOGY),
                *((f'provenance.{key}', provenance[key])
                  for key in FULL_PROVENANCE)):
            if not isinstance(rows, list):
                raise ValueError(f'完整治理备份 {label} 必须是列表')

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
