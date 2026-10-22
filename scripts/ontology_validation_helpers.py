"""Create validation-project ontologies through the public governed workflow."""
from uuid import uuid4


def publish_validation_ontology(call, project_path, turtle):
    """call(method, path, body) must raise for non-successful HTTP responses."""
    versions = call('GET', project_path + '/ontologies', None)['versions']
    current_id = versions[-1]['id'] if versions else None
    draft = call('POST', project_path + '/ontology-drafts', {
        'base_ontology_id': current_id, 'source_kind': 'turtle',
        'title': '验证项目本体', 'summary': '用于自动验收的明确结构定义'})
    path = project_path + '/ontology-drafts/' + draft['id']
    draft = call('POST', path + '/commands', {'expected_revision': draft['revision'],
        'command': {'action': 'diff_turtle', 'edited_turtle': turtle, 'reason': '准备验证结构'}})
    submitted = call('POST', path + '/submit', {'expected_revision': draft['revision']})
    warnings = [x['code'] for x in submitted['validation_report']['warnings']]
    if not submitted['validation_report']['conforms']:
        raise RuntimeError('验证项目本体未通过发布校验')
    current = submitted
    for operation in submitted['operations']:
        current = call('POST', path + '/decisions', {
            'expected_revision': current['revision'], 'expected_ontology_id': current_id,
            'validation_fingerprint': submitted['validation_fingerprint'],
            'acknowledged_warning_codes': warnings,
            'decisions': [{'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'],
                'action': 'approve', 'reason': '核对自动验证结构'}]})
    return call('POST', path + '/publish', {
        'expected_revision': current['revision'], 'expected_ontology_id': current_id,
        'validation_fingerprint': submitted['validation_fingerprint'],
        'acknowledged_warning_codes': warnings, 'idempotency_key': str(uuid4()),
        'note': '建立自动验收使用的本体结构'})
