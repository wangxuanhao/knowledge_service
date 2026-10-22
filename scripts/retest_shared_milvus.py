"""HTTP acceptance of explicitly selected projects in shared knowledge_vectors.

No application/encoder imports. Uses existing login and read-only PG observations.
Only --build schedules vectorization; only --edit exercises a synthetic record,
with CAS restoration in finally. Never vectorizes the capacity project.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import runpy
import time

import httpx
import psutil
import psycopg
from psycopg.conninfo import make_conninfo
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
MAIN = 'eeb3a4dc-7475-4937-9966-74aa261334e6'
CONTROL = '828887db-e1f3-4f5d-b2b3-4b3dc97d2040'
CAPACITY = '46249548-2189-4ad7-8f23-70ee2bcc7f6f'
PROBE = 'discovery-4f9466e7f5c3a18893e9ca6c9451'
FIELDS = ('id', 'kind', 'text', 'type', 'metadata', 'properties', 'source_id',
          'subject_id', 'object_id', 'ontology_id', 'valid_from', 'valid_until',
          'value', 'datatype')
redact = runpy.run_path(str(ROOT / 'knowledge_service/utils/diagnostics.py'))['redact']


def writable(row):
    return {key: deepcopy(row[key]) for key in FIELDS if row.get(key) is not None}


def same(left, right):
    a, b = writable(left), writable(right)
    for row in (a, b):
        row.setdefault('metadata', {})
        row.setdefault('properties', {})
        row.setdefault('type', '')
    return a == b


def stats(rows):
    good = sorted(r['seconds'] for r in rows if r['ok'])
    return {'attempts': len(rows), 'successes': len(good),
            'failures': len(rows) - len(good),
            'status_counts': dict(Counter(str(r['status']) for r in rows)),
            'p50_seconds': good[max(0, math.ceil(len(good)*.5)-1)] if good else None,
            'p95_seconds': good[max(0, math.ceil(len(good)*.95)-1)] if good else None,
            'max_seconds': max(good, default=None),
            'small_sample_caution': len(good) < 20,
            'percentile_method': 'nearest rank on validated successful HTTP responses'}


class Retest:
    def __init__(self, args):
        self.args = args
        self.cfg = {**dotenv_values(ROOT / '.env', interpolate=False), **os.environ}
        self.output = ROOT / args.output
        self.result = {'started_at': datetime.now(timezone.utc).isoformat(),
                       'project': MAIN, 'control_project': CONTROL,
                       'capacity_project': CAPACITY,
                       'scope': 'ordinary project acceptance; not 150k vector certification',
                       'samples': [], 'builds': [], 'edits': [], 'observations': {},
                       'service_pid': args.service_pid}
        if args.resume and self.output.exists():
            self.result = json.loads(self.output.read_text(encoding='utf-8'))
            if self.result.get('fatal_error'):
                self.result.setdefault('previous_failures', []).append(self.result.pop('fatal_error'))
        self.client = httpx.Client(base_url=args.base_url, trust_env=False, timeout=90)
        self.secrets = []

    def save(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(self.result, ensure_ascii=False, indent=2,
                                         default=str), encoding='utf-8')

    def request(self, category, method, path, payload=None, validator=None, required=True):
        started = time.perf_counter()
        sample = {'category': category, 'status': None, 'ok': False}
        data = None
        try:
            response = self.client.request(method, path, json=payload)
            sample['seconds'] = time.perf_counter() - started
            sample['status'] = response.status_code
            sample['bytes'] = len(response.content)
            data = response.json()
            if response.is_error:
                raise RuntimeError(str(data.get('detail', response.status_code)))
            if validator:
                validator(data)
            sample['ok'] = True
            if isinstance(data, dict) and 'hits' in data:
                sample.update({key: data.get(key) for key in
                               ('_backend', 'active_mode', 'degraded', 'complete',
                                'candidate_count', 'keyword_backend')})
                sample['hit_ids'] = [row['id'] for row in data['hits']]
        except Exception as exc:
            sample.setdefault('seconds', time.perf_counter() - started)
            error = redact(str(exc))
            for secret in self.secrets:
                error = error.replace(secret, '<REDACTED>')
            sample['error'] = error[:900]
        self.result['samples'].append(sample)
        self.save()
        if required and not sample['ok']:
            raise RuntimeError(f'{category}: {sample.get("error")}')
        return data, sample

    def observe(self, label):
        dsn = self.cfg.get('KG_DATABASE_URL') or make_conninfo(
            host=self.cfg.get('KG_DATABASE_HOST', '127.0.0.1'),
            port=self.cfg.get('KG_DATABASE_PORT', '5432'), dbname=self.cfg['POSTGRES_DB'],
            user=self.cfg['POSTGRES_APP_USER'], password=self.cfg['POSTGRES_APP_PASSWORD'])
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as db:
            db.execute('SET default_transaction_read_only=on')
            db.execute("SET statement_timeout='15s'")
            rows = db.execute('''SELECT c.project_id,c.status,c.applied_seq,
                s.projection_seq,c.active_destination,c.rebuild_state
                FROM projection_cursors c JOIN project_projection_sequences s USING(project_id)
                WHERE c.target='milvus' ORDER BY c.project_id''').fetchall()
            counts = db.execute('''SELECT project_id,count(*) FROM record_versions
                WHERE superseded_at IS NULL GROUP BY project_id''').fetchall()
            events = db.execute('''SELECT project_id,seq,
                payload ? '_milvus_projection', payload ? 'embedding'
                FROM projection_outbox WHERE project_id=%s ORDER BY seq DESC LIMIT 4''',
                (CONTROL,)).fetchall()
            blockers = db.execute('''SELECT count(*) FROM pg_stat_activity
                WHERE cardinality(pg_blocking_pids(pid))>0''').fetchone()[0]
        observation = {'indexes': [dict(zip(('project_id','status','applied_seq','watermark',
                                           'collection','build_state'), row)) for row in rows],
                       'current_record_counts': dict(counts), 'recent_control_events': events,
                       'blocked_sessions': blockers,
                       'available_memory_gib': psutil.virtual_memory().available / 2**30}
        if self.args.service_pid:
            observation['service_rss_mib'] = psutil.Process(self.args.service_pid).memory_info().rss/2**20
        with httpx.Client(trust_env=False, timeout=15) as c:
            observation['milvus_stats'] = c.post(
                'http://127.0.0.1:19530/v2/vectordb/collections/get_stats',
                json={'collectionName':'knowledge_vectors'}).json()
            info = c.post('http://127.0.0.1:19530/v2/vectordb/collections/describe',
                          json={'collectionName':'knowledge_vectors'}).json()['data']
            observation['fields'] = info['fields']
        self.result['observations'][label] = observation
        self.save()

    def build(self, project):
        assert project in (MAIN, CONTROL), 'Only explicitly selected ordinary projects'
        started = time.perf_counter()
        job, _ = self.request('explicit_build_submit', 'POST', f'/api/projects/{project}/indexes/rebuild')
        self.result['builds'].append({'project_id':project, 'job':job})
        self.save()
        for _ in range(300):
            status, _ = self.request('explicit_build_poll', 'GET', '/api/jobs/' + job['id'])
            if status['status'] in ('succeeded', 'completed', 'done', 'failed', 'cancelled'):
                self.result['builds'][-1].update(seconds=time.perf_counter()-started, final=status)
                self.save()
                assert status['status'] in ('succeeded','completed','done'), str(status.get('error'))
                print(json.dumps({'build_project':project,'seconds':time.perf_counter()-started,
                                  'status':status['status'],'result':status.get('result')},ensure_ascii=False),flush=True)
                return
            time.sleep(2)
        raise RuntimeError('Explicit project build did not finish within polling budget')

    def edit_pair(self, label):
        path = f'/api/projects/{CONTROL}/records/{PROBE}'
        original, _ = self.request(label+'_original', 'GET', path)
        checkpoint = {'label':label,'original':original,'restored':False}
        self.result['edits'].append(checkpoint)
        self.save()
        changed = writable(original)
        changed['text'] += ' · shared-milvus-retest-20261021'
        edited = None
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                future = pool.submit(self.request, label+'_put', 'PUT', path,
                                      {'record':changed,'expected_version':original['version']})
                time.sleep(.03)
                self.request(label+'_parallel_read','POST',f'/api/projects/{MAIN}/records/page',
                             {'kinds':['entity'],'limit':20,'include_total':False})
                edited, _ = future.result()
            assert edited['text'] == changed['text']
            checkpoint['edited_version'] = edited['version']
            _, stale = self.request(label+'_stale_cas','PUT',path,
                {'record':changed,'expected_version':original['version']},required=False)
            stale.update(expected_status=409, assertion_passed=stale['status'] == 409)
            assert stale['status'] == 409, 'Stale CAS must be rejected'
            checkpoint['stale_cas_rejected'] = True
            if label == 'built':
                self.wait_ready(CONTROL)
                data, _ = self.request('changed_record_search','POST',f'/api/projects/{CONTROL}/search',
                    {'query':changed['text'],'retrieval_mode':'keyword','k':20,'kinds':['entity']})
                assert any(r['id']==PROBE and r['version']==edited['version'] for r in data['hits'])
                checkpoint['new_version_searchable'] = True
        finally:
            current, _ = self.request(label+'_before_restore','GET',path)
            if same(current, original):
                checkpoint['restored'] = True
            elif same(current, edited if edited is not None else changed):
                restored, _ = self.request(label+'_restore','PUT',path,
                    {'record':writable(original),'expected_version':current['version']})
                checkpoint['restored'] = same(restored,original)
                checkpoint['restored_version'] = restored['version']
            else:
                checkpoint['restore_conflict'] = 'Unrelated edit; refused overwrite'
            self.save()
        assert checkpoint['restored'], 'Original writable content must be restored'

    def wait_ready(self, project):
        started = time.perf_counter()
        for _ in range(60):
            data, _ = self.request('wait_ready','GET',f'/api/projects/{project}/indexes/status')
            if data['state']=='ready':
                return time.perf_counter()-started
            if data['state'] in ('failed','requires_rebuild'):
                raise RuntimeError(str(data))
            time.sleep(1)
        raise RuntimeError('Incremental index failed to catch up within 60 seconds')

    def search_check(self, expected_ids, extra=None):
        def validate(data):
            assert data['_backend']=='milvus' and data['degraded'] is False
            assert data['hits'], 'Expected representative query hits'
            assert all(r['id'] in expected_ids for r in data['hits']), 'Project isolation violation'
            assert data['complete'] is True
            if extra:
                assert all(extra(r) for r in data['hits']), 'Native filter did not match authority'
        return validate

    def run(self):
        auth = self.client.post('/api/auth/login', json={
            'username':self.cfg['KG_SEED_ADMIN_USERNAME'],'password':self.cfg['KG_SEED_ADMIN_PASSWORD']})
        assert auth.status_code==200, f'Authentication HTTP {auth.status_code}; body omitted'
        token = auth.json()['access_token']; self.secrets.append(token)
        self.client.headers['Authorization'] = 'Bearer '+token
        self.observe('resume_before' if self.args.resume else 'before')
        pages = {}
        for project in (MAIN, CONTROL):
            pages[project],_ = self.request('setup_page','POST',f'/api/projects/{project}/records/page',
                {'kinds':['entity','relation','chunk'],'limit':100,'include_total':True})
        if self.args.edit and not self.args.resume:
            status, _ = self.request('unbuilt_precheck','GET',f'/api/projects/{CONTROL}/indexes/status')
            if status['state'] == 'not_built':
                self.edit_pair('unbuilt')
                self.observe('after_unbuilt_edit')
            else:
                self.result['unbuilt_edit_skipped'] = 'Control project already has an index state'
        if self.args.build:
            for project in (MAIN, CONTROL):
                status, _ = self.request('build_precheck','GET',f'/api/projects/{project}/indexes/status')
                if status['state'] != 'ready':
                    self.build(project)
                self.wait_ready(project)
        self.observe('after_build')
        if self.args.edit:
            self.edit_pair('built')
            self.result['restore_catchup_seconds'] = self.wait_ready(CONTROL)
            self.observe('after_built_edit')
        ids={p:{r['id'] for r in page['records']} for p,page in pages.items()}
        for project,query in ((MAIN,'设备 温度'),(CONTROL,'天海贸易')):
            for mode in ('keyword','semantic','hybrid'):
                body={'query':query,'retrieval_mode':mode,'k':10}
                for n in range(self.args.samples+1):
                    self.request(('warmup_' if n==0 else '')+f'{project[:8]}_{mode}',
                        'POST',f'/api/projects/{project}/search',body,
                        self.search_check(ids[project]),required=False)
        rows=pages[MAIN]['records']
        entity=next(r for r in rows if r['kind']=='entity')
        filters=[('type',{'filters':{'field':'type','op':'eq','value':entity['type']}},
                  lambda r:r['type']==entity['type']),
                 ('ontology',{'ontology_scope':'ids','ontology_ids':[entity['ontology_id']]},
                  lambda r:r.get('ontology_id')==entity['ontology_id'])]
        for name,scope,extra in filters:
            for mode in ('keyword','semantic','hybrid'):
                self.request('filter_'+name+'_'+mode,'POST',f'/api/projects/{MAIN}/search',
                    {'query':entity['text'] if name == 'type' else '设备',
                     'retrieval_mode':mode,'k':10,**scope},
                    self.search_check(ids[MAIN],extra),required=False)
        for mode in ('keyword','semantic','hybrid'):
            self.request('metadata_qualified_'+mode,'POST',f'/api/projects/{MAIN}/search',
                {'query':'设备','retrieval_mode':mode,
                 'filters':{'field':'metadata.__audit_absent_field','op':'exists','value':False}},
                self.search_check(ids[MAIN]), required=True)
            def empty_metadata_scope(data):
                assert data['_backend']=='milvus' and data['degraded'] is False
                assert data['complete'] is True and data['hits']==[], 'Metadata predicate was ignored'
            self.request('metadata_excluded_'+mode,'POST',f'/api/projects/{MAIN}/search',
                {'query':'设备','retrieval_mode':mode,
                 'filters':{'field':'metadata.__audit_absent_field','op':'exists','value':True}},
                empty_metadata_scope, required=True)
        def capacity_page(data):
            assert len(data['records']) == 20, 'Expected bounded capacity page of 20 entities'
        for _ in range(self.args.samples):
            self.request('capacity_page20','POST',f'/api/projects/{CAPACITY}/records/page',
                {'kinds':['entity'],'limit':20,'include_total':False},
                capacity_page,required=False)
        self.observe('final')
        final = self.result['observations']['final']
        assert len(final['fields']) == 21 and not any(
            field['name'].startswith('bf_') for field in final['fields'])
        before_indexes = {row['project_id']: row['build_state'].get('index_version')
                          for row in self.result['observations']['after_build']['indexes']}
        for row in final['indexes']:
            if row['project_id'] in (MAIN, CONTROL):
                assert row['status'] == 'ready' and row['applied_seq'] == row['watermark']
                assert row['build_state'].get('index_version') == before_indexes[row['project_id']]
            if row['project_id'] == CAPACITY:
                assert not row['build_state'].get('index_version'), 'Capacity must remain unbuilt'
        self.result['validation_passed'] = all(
            row.get('assertion_passed', row['ok']) for row in self.result['samples'])
        self.result['finished_at']=datetime.now(timezone.utc).isoformat()
        self.result['summary']={cat:stats([r for r in self.result['samples'] if r['category']==cat])
                               for cat in dict.fromkeys(r['category'] for r in self.result['samples'])}
        self.save()
        print(json.dumps(self.result['summary'],ensure_ascii=False,indent=2),flush=True)
        assert self.result['validation_passed'], 'Unexpected request or validation failures; inspect saved evidence'


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',action='store_true')
    parser.add_argument('--edit',action='store_true')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--samples',type=int,default=20)
    parser.add_argument('--service-pid',type=int)
    parser.add_argument('--base-url',default='http://127.0.0.1:8100')
    parser.add_argument('--output',default='docs/research/2026-10-21-shared-milvus-retest.json')
    args=parser.parse_args()
    test=Retest(args)
    try:
        test.run()
    except Exception as exc:
        test.result['fatal_error']=redact(str(exc))[:1000]
        test.save()
        raise
    finally:
        test.client.close()
