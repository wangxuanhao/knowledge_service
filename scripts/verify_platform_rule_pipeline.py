"""Run real upload, governed publication, retrieval and graph acceptance in an isolated project.

No extractor is replaced. Quality failures stop the command and remain inspectable.
Use the same output directory to resume after reviewing a failed ingestion item.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

import httpx
from dotenv import dotenv_values


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def load_state(output, base_url, input_sha256):
    path = Path(output) / 'state.json'
    if not path.exists():
        return {'base_url': base_url, 'input_sha256': input_sha256,
                'run_id': uuid4().hex, 'created_for_validation': True}
    state = json.loads(path.read_text(encoding='utf-8'))
    if state.get('base_url') != base_url or state.get('input_sha256') != input_sha256:
        raise RuntimeError('恢复目录的输入文件或服务地址不同，请使用另一输出目录')
    if not state.get('created_for_validation'):
        raise RuntimeError('仅支持脚本创建的隔离验收项目')
    return state


def select_relation(candidates):
    entities = {item['id']: item for item in candidates if item.get('kind') == 'entity'}
    for relation in candidates:
        if relation.get('kind') != 'relation':
            continue
        if relation.get('subject_id') == relation.get('object_id'):
            continue
        ends = [entities.get(relation.get(key)) for key in ('subject_id', 'object_id')]
        version = relation.get('document_version_id')
        if version and all(end and end.get('document_version_id') == version for end in ends):
            return list(dict.fromkeys([end['id'] for end in ends] + [relation['id']]))
    raise RuntimeError('未找到两端实体及关系均属于同一来源版本的候选，请检查关系端点')


def verify_lineage(selected, candidates, records, assertions, ontology_id):
    by_candidate = {row['id']: row for row in candidates}
    by_assertion = {row['id']: row for row in assertions}
    by_record = {row['id']: row for row in records}
    canonical = {}
    for identifier in selected:
        candidate = by_candidate[identifier]
        assertion = by_assertion.get(candidate.get('assertion_id') or identifier)
        if not assertion or assertion['status'] != 'accepted':
            raise RuntimeError('所选候选缺少接受断言。' + identifier)
        source_version = candidate.get('assertion_document_version_id') or candidate['document_version_id']
        if (assertion.get('document_version_id') != source_version
                or not assertion.get('chunk_id') or not str(assertion.get('quote') or '').strip()):
            raise RuntimeError('所选断言的来源版本、切片或原文片段不完整。' + identifier)
        record = by_record.get(assertion.get('canonical_record_id'))
        if not record or record.get('kind') != candidate['kind'] or record.get('ontology_id') != ontology_id:
            raise RuntimeError('所选断言的正式记录或本体映射不匹配。' + identifier)
        canonical[identifier] = record['id']
    for identifier in selected:
        candidate = by_candidate[identifier]
        if candidate['kind'] == 'relation':
            record = by_record[canonical[identifier]]
            for key in ('subject_id', 'object_id'):
                if record.get(key) != canonical.get(candidate[key]):
                    raise RuntimeError('所选关系的正式端点映射不匹配。' + identifier)
    return canonical


def verify_evidence(assertion, evidence, frozen):
    expected = assertion['document_version_id']
    if (frozen.get('version_id') != expected or evidence.get('document', {}).get('version_id') != expected
            or not evidence.get('integrity', {}).get('complete')
            or evidence.get('source_location', {}).get('status') != 'available'
            or not evidence['source_location'].get('spans')):
        raise RuntimeError('知识缺少完整的固定来源映射')
    text = frozen['text']
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    if assertion.get('source_hash') and assertion['source_hash'] != digest:
        raise RuntimeError('断言来源哈希与固定文档不同')
    chunk = evidence['chunk']
    start, end = chunk.get('start_char'), chunk.get('end_char')
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
            or text[start:end] != chunk.get('text')):
        raise RuntimeError('来源切片与固定文档字符范围不同')
    location = evidence['location']
    if location.get('mode') == 'exact':
        if text[location['start_char']:location['end_char']] != assertion['quote']:
            raise RuntimeError('精确证据与固定原文不同')
    elif location.get('mode') != 'chunk':
        raise RuntimeError('知识无法定位原文片段')
    return digest


def check_batch_items(items):
    failures = [item for item in items if item['status'] in
                {'needs_attention', 'cancelled', 'interrupted', 'failed'}]
    if failures:
        detail = [{'id': item['id'], 'status': item['status'],
                   'error_code': item.get('error_code'), 'message': item.get('public_error')}
                  for item in failures]
        raise RuntimeError('批次需要审核或重试。' + json.dumps(detail, ensure_ascii=False))


class Pipeline:
    def __init__(self, client, output, state, timeout):
        self.client, self.output, self.state = client, Path(output), state
        self.timeout = timeout
        self.stage = 'starting'

    def save(self, **updates):
        self.state.update(updates)
        write_json(self.output / 'state.json', self.state)

    def evidence(self, name, payload):
        write_json(self.output / (name + '.json'), payload)
        return payload

    def call(self, method, path, body=None, **kwargs):
        response = self.client.request(method, path, json=body, **kwargs)
        if not response.is_success:
            # The response body is restricted to this local acceptance evidence.
            self.evidence('failure-response', {'stage': self.stage, 'path': path,
                                             'status': response.status_code, 'body': response.text})
            raise RuntimeError(f'{self.stage} HTTP {response.status_code}. 请查看 failure-response.json')
        return response.json()

    @property
    def project_path(self):
        return '/api/projects/' + self.state['project_id']

    def create(self):
        self.stage = 'create_project'
        if 'project_id' not in self.state:
            # A response lost after project creation can be recovered by its run marker.
            projects = self.call('GET', '/api/projects')['projects']
            matches = [p for p in projects if p.get('metadata', {}).get('validation_run') == self.state['run_id']]
            if len(matches) > 1:
                raise RuntimeError('验收标识匹配多个项目，请核对 state.json')
            project = matches[0] if matches else self.call('POST', '/api/projects', {
                'name': '真实链路验收 · ' + self.state['run_id'][:12],
                'use_default_ontology': False, 'ontology_mode': 'discovery',
                'metadata': {'validation_run': self.state['run_id'], 'isolated': True}})
            self.save(project_id=project['id'])
        if 'batch_id' in self.state:
            return
        self.stage = 'create_batch'
        batches = self.call('GET', self.project_path + '/ingest-batches?limit=1000')['items']
        matches = [b for b in batches if b.get('settings', b.get('settings_json', {})).get('metadata', {}).get('validation_run') == self.state['run_id']]
        settings = {'extraction_mode': 'discovery', 'extract_attributes': True,
                    'chunk_strategy': 'fixed', 'chunk_size': 10000, 'chunk_overlap': 200,
                    'requested_concurrency': 1, 'auto_merge': False, 'document_time_mode': 'auto',
                    'metadata': {'validation_run': self.state['run_id']}}
        batch = matches[0] if matches else self.call('POST', self.project_path + '/ingest-batches', {'settings': settings})
        self.save(batch_id=batch['id'])

    def ingest(self, source, platform):
        self.stage = 'upload'
        path = self.project_path + '/ingest-batches/' + self.state['batch_id']
        if not self.state.get('uploaded'):
            options = {'document_time_mode': 'auto'}
            if platform:
                options['applicability'] = {'platforms': {'mode': 'specific', 'values': [platform]}}
            with Path(source).open('rb') as handle:
                result = self.call('POST', path + '/files',
                    files={'file': (Path(source).name, handle, 'application/octet-stream')},
                    data={'options': json.dumps(options, ensure_ascii=False)},
                    headers={'Idempotency-Key': self.state['run_id'] + '-upload'})
            self.evidence('upload', result)
            self.save(uploaded=True)
        batch = self.call('GET', path)
        if batch['status'] == 'draft':
            self.call('POST', path + '/start')
        self.stage = 'ingest'
        deadline = time.monotonic() + self.timeout
        last = None
        while time.monotonic() < deadline:
            batch = self.evidence('batch', self.call('GET', path))
            items = self.evidence('items', self.call('GET', path + '/items?limit=1000'))['items']
            progress = [(x['status'], x['stage'], x.get('progress')) for x in items]
            if progress != last:
                print(json.dumps({'stage': self.stage, 'items': progress}, ensure_ascii=False), flush=True)
                last = progress
            if batch['status'] not in {'running', 'draft'}:
                check_batch_items(items)
                if items and all(x['status'] == 'completed' for x in items):
                    self.save(ingested=True)
                    return
                raise RuntimeError('批次未完成，当前状态 ' + batch['status'])
            time.sleep(3)
        raise TimeoutError('真实抽取未在预算内结束，使用相同命令继续轮询')

    def candidates(self):
        self.stage = 'candidates'
        rows, offset, expected = [], 0, None
        while True:
            result = self.call('GET', self.project_path + '/ontology-discovery/candidates',
                               params={'batch_id': self.state['batch_id'], 'offset': offset, 'limit': 1000})
            expected = result.get('expected_ontology_id')
            page = result['items']
            rows.extend(page)
            if len(rows) >= result['total'] or not page:
                break
            offset += len(page)
        self.evidence('candidates', {'items': rows, 'expected_ontology_id': expected})
        return rows, expected

    def publish(self):
        if 'draft_id' not in self.state:
            rows, expected = self.candidates()
            selected = self.state.get('selected_candidate_ids') or select_relation(rows)
            self.save(selected_candidate_ids=selected)
            body = {'candidate_ids': selected, 'expected_ontology_id': expected,
                    'create_new_draft': True, 'confirmed_mappings': {},
                    'name': '隔离验收所选关系与实体'}
            self.stage = 'selection_preview'
            # Freeze the request before the mutating call, including its idempotency key.
            frozen_path = self.output / 'selection-request.json'
            if frozen_path.exists():
                frozen = json.loads(frozen_path.read_text(encoding='utf-8'))
            else:
                preview = self.evidence('selection-preview', self.call('POST', self.project_path + '/ontology-discovery/selection-preview', body))
                if not preview.get('can_add'):
                    raise RuntimeError('候选预览需要人工消歧或补齐定义，请查看 selection-preview.json')
                frozen = {**body, 'preview_fingerprint': preview['preview_fingerprint'],
                          'source_fingerprint': preview['source_fingerprint'],
                          'idempotency_key': self.state['run_id'] + '-selection'}
                self.evidence('selection-request', frozen)
            self.stage = 'add_selection'
            added = self.evidence('selection-added', self.call('POST', self.project_path + '/ontology-discovery/add-selection', frozen))
            self.save(draft_id=added['draft']['id'])
        self.stage = 'review_publish'
        path = self.project_path + '/ontology-drafts/' + self.state['draft_id']
        draft = self.call('GET', path)
        if draft['status'] == 'accepted':
            if not draft.get('published_ontology_id'):
                raise RuntimeError('草案已发布，但未找到对应本体回执')
            self.save(ontology_id=draft['published_ontology_id'])
            return
        if draft['status'] != 'pending':
            raise RuntimeError('验收草案已驳回，请核对审核记录')
        if not draft.get('submitted_at'):
            draft = self.call('POST', path + '/submit', {'expected_revision': draft['revision']})
        submitted = self.evidence('draft-submitted', draft)
        if not submitted['validation_report']['conforms']:
            raise RuntimeError('本体草案校验未通过')
        common = {'expected_ontology_id': submitted['base_ontology_id'],
                  'validation_fingerprint': submitted['validation_fingerprint'],
                  'acknowledged_warning_codes': [x['code'] for x in submitted['validation_report']['warnings']]}
        decisions = {row['operation_id']: row for row in draft.get('decisions', [])}
        for operation in submitted['operations']:
            # All operations originate from the explicit three-candidate acceptance selection.
            decision = decisions.get(operation['id'])
            if decision and decision.get('operation_fingerprint') == operation['fingerprint']:
                if decision['action'] != 'approve':
                    raise RuntimeError('验收操作已有其他审核结论，请核对 decisions')
                continue
            draft = self.call('POST', path + '/decisions', {
                **common, 'expected_revision': draft['revision'],
                'decisions': [{'operation_id': operation['id'], 'operation_fingerprint': operation['fingerprint'],
                               'action': 'approve', 'reason': '隔离验收，核对所选实体与关系来源'}]})
        frozen_path = self.output / 'publish-request.json'
        if frozen_path.exists():
            body = json.loads(frozen_path.read_text(encoding='utf-8'))
        else:
            body = {**common, 'expected_revision': draft['revision'],
                    'idempotency_key': self.state['run_id'] + '-publish', 'note': '真实平台规则最小关系链验收'}
            self.evidence('publish-request', body)
        ontology = self.evidence('publication', self.call('POST', path + '/publish', body))
        self.save(ontology_id=ontology['id'])

    def records(self, kind):
        rows, cursor = [], None
        while True:
            page = self.call('POST', self.project_path + '/records/page',
                             {'kinds': [kind], 'limit': 1000, 'cursor': cursor, 'ontology_scope': 'all'})
            rows.extend(page['records'])
            cursor = page.get('next_cursor')
            if not cursor:
                return rows

    def index_job(self):
        path = self.output / 'index-job.json'
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        return self.evidence('index-job', self.call('POST', self.project_path + '/indexes/rebuild', {}))

    def verify(self):
        self.stage = 'records'
        records = {kind: self.records(kind) for kind in ('entity', 'relation', 'chunk', 'document')}
        self.evidence('records', records)
        entities = {row['id'] for row in records['entity']}
        if not entities or not records['relation']:
            raise RuntimeError('正式实体或关系为空')
        for relation in records['relation']:
            if relation.get('subject_id') not in entities or relation.get('object_id') not in entities:
                raise RuntimeError('正式关系端点不完整')
        assertions = self.evidence('accepted-assertions', self.call('GET', self.project_path + '/assertions', params={'status': 'accepted'}))
        accepted = assertions if isinstance(assertions, list) else assertions.get('items', assertions.get('assertions', []))
        if not accepted:
            raise RuntimeError('未找到已接受的来源断言')
        snapshot = json.loads((self.output / 'candidates.json').read_text(encoding='utf-8'))['items']
        canonical = verify_lineage(self.state['selected_candidate_ids'], snapshot,
            records['entity'] + records['relation'], accepted, self.state['ontology_id'])
        by_assertion = {row['id']: row for row in accepted}
        verified_sources = set()
        source_checks = []
        for candidate_id in canonical:
            candidate = next(row for row in snapshot if row['id'] == candidate_id)
            assertion_id = candidate.get('assertion_id') or candidate_id
            evidence = self.evidence('source-evidence-' + hashlib.sha256(candidate_id.encode()).hexdigest()[:12],
                self.call('GET', self.project_path + '/assertions/' + assertion_id + '/evidence'))
            assertion = by_assertion[assertion_id]
            history = self.call('GET', self.project_path + '/records/' + assertion['document_id'] + '/history')['versions']
            frozen = next(row for row in history if row['version_id'] == assertion['document_version_id'])
            digest = verify_evidence(assertion, evidence, frozen)
            source_checks.append({'assertion_id': assertion_id, 'frozen_document_sha256': digest,
                                  'stored_source_hash_status': evidence['integrity']['source_hash_status']})
            raw_id = evidence['source_location']['document_version_id']
            raw_path = self.project_path + '/source-documents/' + raw_id
            raw = self.call('GET', raw_path)
            pinned = [chunk for chunk in raw['chunks'] if chunk['id'] == assertion['chunk_id']
                      and chunk.get('metadata', {}).get('source_version_id') == assertion['document_version_id']]
            if raw['document_id'] != assertion['document_id'] or len(pinned) != 1:
                raise RuntimeError('固定来源与断言的不可变切片关联不完整')
            location = self.evidence('source-locate-' + hashlib.sha256(candidate_id.encode()).hexdigest()[:12],
                self.call('GET', raw_path + '/locate', params={'chunk_version_id': pinned[0]['version_id']}))
            if location.get('status') != 'available' or not location.get('spans'):
                raise RuntimeError('固定来源切片无法定位')
            if raw_id not in verified_sources:
                response = self.client.get(raw_path + '/source')
                response.raise_for_status()
                if str(raw.get('format')) in {'markdown', 'md', 'text', 'txt'}:
                    if response.content.decode('utf-8-sig') != raw['source_text']:
                        raise RuntimeError('MinIO 固定来源正文与来源版本记录不一致')
                if raw.get('mime_type') == 'application/zip':
                    package = self.client.get(raw_path + '/package')
                    package.raise_for_status()
                    if hashlib.sha256(package.content).hexdigest() != self.state['input_sha256']:
                        raise RuntimeError('MinIO 原始ZIP哈希与输入不一致')
                verified_sources.add(raw_id)
        self.evidence('source-verification', source_checks)
        self.stage = 'index'
        status = self.call('GET', self.project_path + '/indexes/status')
        if status.get('state') != 'ready':
            job = self.index_job()
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                status = self.call('GET', self.project_path + '/indexes/status')
                self.evidence('index-status', status)
                if status.get('state') == 'ready':
                    break
                job_id = job.get('id', job.get('job_id'))
                if job_id:
                    current = self.evidence('index-job-status', self.call('GET', '/api/jobs/' + job_id))
                    if current.get('status') in {'failed', 'cancelled'}:
                        raise RuntimeError('检索索引构建失败')
                time.sleep(3)
            else:
                raise TimeoutError('检索索引尚未就绪')
        self.evidence('index-status', status)
        self.stage = 'search'
        entity = next(row for row in records['entity'] if row['id'] in canonical.values())
        query = entity['text']
        searches = {}
        for mode in ('keyword', 'semantic', 'hybrid'):
            searches[mode] = self.call('POST', self.project_path + '/search',
                {'query': query, 'retrieval_mode': mode, 'k': 10, 'k_entities': 5,
                 'k_chunks': 5, 'k_relations': 5, 'ontology_scope': 'all'})
            self.evidence('search-' + mode, searches[mode])
            hits = searches[mode].get('results', searches[mode].get('hits', []))
            if (not any(hit.get('id') == entity['id'] for hit in hits)
                    or searches[mode].get('active_mode') != mode or searches[mode].get('degraded')
                    or searches[mode].get('_backend') != 'milvus'):
                raise RuntimeError(mode + ' 检索未通过正式实体与实际模式验证')
        self.stage = 'graph'
        session = self.evidence('graph-session', self.call('POST', self.project_path + '/graph-sessions', {}))
        session_id = session.get('id', session.get('session_id'))
        for kind in ('entities', 'relations'):
            result = self.call('POST', self.project_path + '/graph-sessions/' + session_id + '/page', {'kind': kind, 'limit': 10000})
            self.evidence('graph-' + kind, result)
            if not result['records'] or len(result['records']) != result['total'] or not result['complete']:
                raise RuntimeError('最小验收图谱结果不完整。' + kind)
        report = {'project_id': self.state['project_id'], 'batch_id': self.state['batch_id'],
                  'ontology_id': self.state['ontology_id'], 'selected_candidate_ids': self.state['selected_candidate_ids'],
                  'counts': {kind: len(rows) for kind, rows in records.items()}, 'query': query,
                  'verified_canonical_records': canonical, 'verified_source_evidence': len(canonical),
                  'scope': '真实文件及真实模型的最小关系链，全部质量失败须显式人工处理', 'passed': True}
        self.evidence('report', report)
        self.save(completed=True)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--base-url', default='http://127.0.0.1:8100')
    parser.add_argument('--platform', default='')
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(args.output_dir, args.base_url.rstrip('/'), hashlib.sha256(args.file.read_bytes()).hexdigest())
    state.setdefault('run_id', uuid4().hex)
    write_json(args.output_dir / 'state.json', state)
    config = dotenv_values(Path(__file__).resolve().parents[1] / '.env')
    username = os.getenv('KG_ACCEPTANCE_USERNAME') or config.get('KG_SEED_ADMIN_USERNAME') or 'admin'
    password = os.getenv('KG_ACCEPTANCE_PASSWORD') or config.get('KG_SEED_ADMIN_PASSWORD') or 'admin123'
    with httpx.Client(base_url=state['base_url'], trust_env=False, timeout=180) as client:
        login = client.post('/api/auth/login', json={'username': username, 'password': password})
        login.raise_for_status()
        client.headers['Authorization'] = 'Bearer ' + login.json()['access_token']
        pipeline = Pipeline(client, args.output_dir, state, args.timeout)
        try:
            pipeline.create()
            pipeline.ingest(args.file, args.platform)
            if not state.get('ontology_id'):
                pipeline.publish()
            pipeline.verify()
        except Exception as exc:
            pipeline.evidence('failure', {'stage': pipeline.stage, 'type': type(exc).__name__, 'message': str(exc)})
            print(f'验收停止于 {pipeline.stage}。{exc}', file=sys.stderr)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
