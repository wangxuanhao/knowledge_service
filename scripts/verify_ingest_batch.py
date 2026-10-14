"""Create an isolated verification project and exercise a small batch via HTTP."""
import argparse
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import httpx


def verify(base_url, files, *, project_id=None, mode='documents', concurrency='auto', timeout=600):
    token = os.getenv('KG_VERIFICATION_TOKEN', '')
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    with httpx.Client(base_url=base_url, headers=headers, timeout=60, trust_env=False) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        cap = request('GET', '/api/ingest/capabilities')
        if len(files) > cap['max_batch_items']:
            raise ValueError('Too many files for server capabilities')
        if project_id is None:
            project_id = request('POST', '/api/projects', json={'name': '批次验收-' + uuid4().hex[:8],
                                'ontology_mode': mode})['id']
        base = f'/api/projects/{project_id}/ingest-batches'
        batch = request('POST', base, json={'settings': {'extraction_mode': mode,
                        'requested_concurrency': concurrency, 'extract': mode != 'documents'}})
        url = base + '/' + batch['id']
        for path in files:
            with path.open('rb') as stream:
                request('POST', url + '/files', files={'file': (path.name, stream)},
                        headers={'Idempotency-Key': uuid4().hex})
        request('POST', url + '/start', json={})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            batch = request('GET', url)
            if batch['status'] in {'completed', 'completed_with_errors', 'paused', 'cancelled'}:
                break
            time.sleep(2)
        else:
            raise TimeoutError('Batch verification timed out; durable batch remains available')
        page = request('GET', url + '/items?limit=50')
        print(json.dumps({'project_id': project_id, 'batch_id': batch['id'], 'status': batch['status'],
                          'counts': batch['counts'], 'effective_concurrency': batch['effective_concurrency'],
                          'first_page_items': len(page['items'])}, ensure_ascii=False))
        return 0 if batch['status'] == 'completed' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', type=Path, nargs='+')
    parser.add_argument('--base-url', default='http://127.0.0.1:8100')
    parser.add_argument('--project-id')
    parser.add_argument('--mode', choices=['documents', 'ontology', 'discovery'], default='documents')
    parser.add_argument('--concurrency', choices=['auto', '1', '2', '4', '8'], default='auto')
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    concurrency = args.concurrency if args.concurrency == 'auto' else int(args.concurrency)
    raise SystemExit(verify(args.base_url, args.files, project_id=args.project_id, mode=args.mode,
                           concurrency=concurrency, timeout=args.timeout))
