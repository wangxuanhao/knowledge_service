"""Live identity, model/Milvus, outbox and frozen-source verification.

Run through verify_authenticated.py; creates and removes only its own project.
"""
import argparse
import json
import time
import urllib.request
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    base = parser.parse_args().url.rstrip('/')

    def request(path, body=None, method=None, stream=False):
        req = urllib.request.Request(base + path,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'}, method=method)
        with urllib.request.urlopen(req, timeout=180) as response:
            raw = response.read().decode()
            if stream:
                return {block.split('\n', 1)[0].removeprefix('event: '):
                        json.loads(block.split('data: ', 1)[1])
                        for block in raw.split('\n\n') if block.startswith('event: ')}
            return json.loads(raw) if raw else None

    health = request('/api/health')
    assert health['semantic'] and health['embedding']['ready'], health
    assert health['embedding']['dimension'] == health['vector']['dimension'], health
    assert health['vector']['collection_ready'], health
    assert request('/readyz')['status'] == 'ready'
    print('PASS model encoding and Milvus dimensions:', health['embedding']['dimension'])
    project = request('/api/projects', {'name': 'Hardening verification ' + uuid4().hex[:8]})
    root = '/api/projects/' + project['id']
    try:
        ontology = request(root + '/ontology')
        draft = request(root + '/ontology-drafts', {
            'base_ontology_id': ontology['id'], 'title': 'Live audit validation',
            'actor': 'forged-actor', 'source_context': {'actor': 'forged-actor'}})
        assert 'forged-actor' not in draft['source_context']['actor']
        assert 'user:' in draft['source_context']['actor']
        draft = request(root + f"/ontology-drafts/{draft['id']}/commands", {
            'expected_revision': draft['revision'], 'command': {
                'action': 'create_term', 'kind': 'class',
                'target_iri': 'urn:verification:LiveProbe'}})
        path = root + '/ontology-drafts/' + draft['id']
        draft = request(path + '/submit', {'expected_revision': draft['revision']})
        warning_codes = [w['code'] for w in draft['validation_report']['warnings']]
        draft = request(path + '/decisions', {
            'expected_revision': draft['revision'], 'expected_ontology_id': ontology['id'],
            'validation_fingerprint': draft['validation_fingerprint'],
            'acknowledged_warning_codes': warning_codes, 'actor': 'forged-reviewer',
            'decisions': [{'operation_id': op['id'], 'operation_fingerprint': op['fingerprint'],
                           'action': 'approve', 'reason': 'live verification'}
                          for op in draft['operations']]})
        version = request(path + '/publish', {
            'expected_revision': draft['revision'], 'expected_ontology_id': ontology['id'],
            'validation_fingerprint': draft['validation_fingerprint'],
            'acknowledged_warning_codes': warning_codes, 'idempotency_key': str(uuid4()),
            'actor': 'forged-publisher', 'note': 'Live outbox and audit validation'})
        assert 'forged' not in version['metadata']['publication']['actor']
        print('PASS authenticated creation/review/publication identity')
        source = '冻结验证原文：张三使用测试系统，系统保留原文版本用于追溯。'
        doc = request(root + '/documents', {'title': '冻结验证原文', 'text': source, 'extract': False})
        request(root + '/ontology-index/repair', {}, 'POST')
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = request(root + '/ontology-index')
            if state['ready']:
                break
            time.sleep(.5)
        assert state['ready'], state
        assert state['authoritative_ontology_id'] == state['indexed_ontology_id']
        print('PASS durable outbox and authoritative/index reconciliation')
        job = request(root + '/indexes/rebuild', {}, 'POST')
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            rebuilt = request('/api/jobs/' + job['id'])
            if rebuilt['status'] not in {'queued', 'running'}:
                break
            time.sleep(.5)
        assert rebuilt['status'] == 'completed', rebuilt
        assert rebuilt['result']['scope'] == 'project', rebuilt
        assert rebuilt['result']['restored_projects'] == [project['id']], rebuilt
        assert request(root + '/ontology-index')['ready']
        print('PASS durable semantic rebuild and completed project receipt')
        hits = request(root + '/search', {'query': '张三使用的系统', 'retrieval_mode': 'semantic'})
        assert hits['hits'], hits
        events = request(root + '/qa/stream', {'query': '张三使用什么系统',
            'retrieval_mode': 'semantic', 'generate': False}, stream=True)
        assert 'error' not in events and events['done']['provenance_complete'], events
        evidence = events['evidence']['evidence']
        assert evidence and any(source in s['quote'] for e in evidence for s in e['sources']), evidence
        original = doc['document']
        request(root + '/records/' + original['id'], {'expected_version': original['version'],
            'record': {'kind': 'document', 'text': '这段是后来修改的内容',
                       'metadata': original['metadata']}}, method='PUT')
        answer_id = events['done']['answer_id']
        trace = request(root + '/answers/' + answer_id + '/evidence/' + evidence[0]['citation'] + '/provenance')
        assert source in json.dumps(trace, ensure_ascii=False), trace
        print('PASS semantic retrieval and frozen original source after document revision')
    finally:
        request(root, method='DELETE')


if __name__ == '__main__':
    main()
