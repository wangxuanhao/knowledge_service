"""Vector manifests and exact source eligibility for community synthesis."""
from types import SimpleNamespace

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
        accepted[item['id']] = {**item, **authority, 'validity_status': status}
    return accepted, rule, request['valid_at']
