"""Vector manifests and exact source eligibility for community synthesis."""
from types import SimpleNamespace

from ..domain.applicability import dimension_mode, dimension_values
from .answer_validity import classify, freeze_request
from .community_snapshot import digest
from .qa_policy import RULE


def vector_reports(rows):
    result = []
    for row in rows:
        report = row['report']
        findings = report.get('findings', [])
        material = [report.get('title', ''), report.get('summary', ''), findings]
        text = '\n'.join([*material[:2], *(item['text'] for item in findings)])
        result.append({'id': row['id'], 'fingerprint': digest(material), 'text': text[:3000]})
    return result


def report_manifest(rows):
    return {item['id']: item['fingerprint'] for item in rows}


def qualify_sources(repository, project, scope, question, evidence):
    """Recheck the exact report occurrence with the same rules as knowledge QA."""
    request = freeze_request({**scope, 'query': question})
    return (qualify_question_sources(repository, project, request, evidence),
            request['answer_purpose'] == RULE, request['valid_at'])


def _source_platforms(document):
    applicability = (document.get('metadata') or {}).get('applicability')
    if dimension_mode(applicability, 'platforms') == 'specific':
        return dimension_values(applicability, 'platforms')
    return []


def qualify_question_sources(repository, project, request, evidence, *, include_platforms=False):
    rule = request['answer_purpose'] == RULE
    service = SimpleNamespace(repository=repository)
    accepted, checked = {}, {}
    for item in evidence:
        version = item.get('record_version_id')
        document = item.get('document_version_id')
        chunk = item.get('chunk_version_id')
        assertion = item.get('assertion_id')
        if item.get('kind') != 'document' or not all((version, document, chunk, assertion)):
            continue
        if version not in checked:
            try:
                record = repository.get_record_version(project, version)
                checked[version] = classify(service, project, record, request)
            except KeyError:
                checked[version] = None
        row = checked[version]
        if not row or (rule and not row['answer_eligible']):
            continue
        if document not in row['_allowed_source_version_ids'] or chunk not in row['_allowed_chunk_version_ids']:
            continue
        if assertion not in row['_allowed_assertion_ids']:
            continue
        authority = next(source for source in row['source_eligibility'] if source['document_version_id'] == document)
        status = authority['validity_status'] if row['validity_status'] == 'effective' else row['validity_status']
        source = {**item, **authority, 'validity_status': status}
        if include_platforms:
            version = authority.get('governance_version_id') or document
            source['source_platforms'] = _source_platforms(repository.get_record_version(project, version))
        accepted[item['id']] = source
    return accepted


def original_evidence(repository, project, request, hits):
    """Keep exact original chunks that satisfy the frozen knowledge QA policy."""
    service = SimpleNamespace(repository=repository)
    evidence = []
    for hit in hits:
        if hit.get('kind') != 'chunk' or not (chunk_version := hit.get('version_id')):
            continue
        try:
            chunk = repository.get_record_version(project, chunk_version)
        except KeyError:
            continue
        if chunk.get('kind') != 'chunk':
            continue
        meta = chunk.get('metadata') or {}
        version = meta.get('source_version_id')
        quote = (chunk.get('text') or '').strip()[:800]
        if not version or not quote:
            continue
        row = classify(service, project, chunk, request)
        if not row or version not in row['_allowed_source_version_ids']:
            continue
        if request['answer_purpose'] == RULE and not row['answer_eligible']:
            continue
        if row['validity_status'] not in ('effective', 'unknown', 'expired', 'not_yet_effective'):
            continue
        authority = next(item for item in row['source_eligibility'] if item['document_version_id'] == version)
        document = repository.get_record_version(project, version)
        governance = repository.get_record_version(project, authority.get('governance_version_id') or version)
        document_meta = document.get('metadata') or {}
        evidence.append({**authority, 'id': 'E-' + digest([version, chunk_version])[:24],
                         'kind': 'document', 'document_id': document['id'],
                         'source_version_id': document_meta.get('source_document_version_id'),
                         'chunk_version_id': chunk_version,
                         'title': str(document_meta.get('title') or document['id'])[:240], 'quote': quote,
                         'validity_status': row['validity_status'], 'source_platforms': _source_platforms(governance)})
    return evidence
