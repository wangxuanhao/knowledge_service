"""Re-measure the retained 150,001 entity / 300,000 relation project over HTTP.

Uses the running application and ordinary authentication; never imports the app,
starts services, seeds data, migrates a database, or constructs an encoder.
Examples (run from the repository root with the llm_model interpreter)::

    python scripts/recertify_retained_capacity.py --mode read --skip-full-graph
    python scripts/recertify_retained_capacity.py --mode read --full-graph-loads 5
    python scripts/recertify_retained_capacity.py --mode write --exercise-write
    python scripts/recertify_retained_capacity.py --self-test

Read mode is the default. Write mode creates new history revisions on ONLY the
named governed probe, pairing each text edit with a normal CAS-protected restore.
The original writable record is checkpointed before the first PUT. Restoration
also runs in finally after an HTTP error, timeout, or interruption. An unrelated
concurrent edit is never overwritten; that condition requires manual review.
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
import platform
import runpy
import sys
import threading
import time
from typing import Any, Callable
from urllib.parse import quote, urlsplit
import uuid

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
PROJECT = '46249548-2189-4ad7-8f23-70ee2bcc7f6f'
PROBE = '真实大库编辑验收实体'
WRITE_FIELDS = ('id', 'kind', 'text', 'type', 'metadata', 'properties', 'source_id',
                'subject_id', 'object_id', 'ontology_id', 'valid_from', 'valid_until',
                'value', 'datatype')
# run_path reads this leaf module without executing knowledge_service/__init__.py.
_redact = runpy.run_path(str(ROOT / 'knowledge_service/utils/diagnostics.py'))['redact']


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile, with no interpolated or invented observations."""
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def writable(record: dict) -> dict:
    return {key: deepcopy(record[key]) for key in WRITE_FIELDS if key in record}


def equivalent(left: dict, right: dict) -> bool:
    """Compare writable content, allowing normal API omitted-null/default fields."""
    def normalized(record):
        result = {key: value for key, value in writable(record).items() if value is not None}
        result.setdefault('metadata', {})
        result.setdefault('properties', {})
        result.setdefault('type', '')
        return result
    return normalized(left) == normalized(right)


def select_probe(page: dict) -> dict:
    """Resolve one bounded candidate by stable ID while allowing prior edit suffixes."""
    check(isinstance(page.get('records'), list),
          'Probe lookup response must contain the production records list; response keys=' + ','.join(sorted(page)))
    records = page['records']
    check(len(records) <= 5, 'Probe lookup exceeded its five-candidate request limit')
    candidates = [row for row in records if row.get('kind') == 'entity' and
                  str(row.get('text') or '').startswith(PROBE) and row.get('ontology_id')]
    check(not page.get('next_cursor') and len(candidates) == 1,
          f'Bounded lookup found {len(candidates)} governed probe candidates among {len(records)} results; '
          'use --probe-id with the independently verified stable test entity ID')
    return candidates[0]


def summarize(rows: list[dict]) -> dict:
    successful = [row for row in rows if row['ok']]
    seconds = [row['http_seconds'] for row in successful]
    count = len(rows)
    return {'samples': count, 'successful_samples': len(successful),
            'failed_samples': count - len(successful),
            'failure_rate': (count - len(successful)) / count if count else None,
            'status_counts': dict(Counter(str(row.get('status_code')) for row in rows)),
            'p50_seconds': percentile(seconds, .5), 'p95_seconds': percentile(seconds, .95),
            'max_seconds': max(seconds, default=None),
            'all_attempts_p95_seconds': percentile([row['http_seconds'] for row in rows], .95),
            'response_body_bytes': sum(row.get('response_body_bytes', 0) for row in rows),
            'request_body_bytes': sum(row.get('request_body_bytes', 0) for row in rows),
            'small_sample_caution': len(successful) < 20,
            'percentile_method': 'nearest rank, successful validated responses; failures separate'}


class BenchmarkFailure(RuntimeError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise BenchmarkFailure(message)


class WindowsMemoryCounters:
    """PDH English counters avoid localized PowerShell names and subprocess load."""
    PATHS = {'page_reads_per_second': r'\Memory\Page Reads/sec',
             'page_writes_per_second': r'\Memory\Page Writes/sec',
             'pages_input_per_second': r'\Memory\Pages Input/sec',
             'pages_output_per_second': r'\Memory\Pages Output/sec',
             'page_faults_per_second': r'\Memory\Page Faults/sec',
             'committed_bytes': r'\Memory\Committed Bytes',
             'commit_limit_bytes': r'\Memory\Commit Limit'}

    def __init__(self):
        self.query = None
        self.counters = {}
        if os.name != 'nt':
            return
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.dll = ctypes.WinDLL('pdh')
        handle = wintypes.HANDLE
        self.dll.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(handle)]
        self.dll.PdhOpenQueryW.restype = wintypes.LONG
        self.dll.PdhAddEnglishCounterW.argtypes = [handle, wintypes.LPCWSTR, ctypes.c_size_t,
                                                 ctypes.POINTER(handle)]
        self.dll.PdhAddEnglishCounterW.restype = wintypes.LONG
        self.dll.PdhCollectQueryData.argtypes = [handle]
        self.dll.PdhCollectQueryData.restype = wintypes.LONG
        self.dll.PdhCloseQuery.argtypes = [handle]
        class ValueUnion(ctypes.Union):
            _fields_ = [('doubleValue', ctypes.c_double), ('largeValue', ctypes.c_longlong)]
        class CounterValue(ctypes.Structure):
            _anonymous_ = ('value',)
            _fields_ = [('status', wintypes.DWORD), ('value', ValueUnion)]
        self.value_type = CounterValue
        self.dll.PdhGetFormattedCounterValue.argtypes = [handle, wintypes.DWORD,
                                                        ctypes.POINTER(wintypes.DWORD),
                                                        ctypes.POINTER(CounterValue)]
        self.dll.PdhGetFormattedCounterValue.restype = wintypes.LONG
        query = handle()
        if self.dll.PdhOpenQueryW(None, 0, ctypes.byref(query)) != 0:
            return
        self.query = query
        for name, path in self.PATHS.items():
            counter = handle()
            if self.dll.PdhAddEnglishCounterW(query, path, 0, ctypes.byref(counter)) == 0:
                self.counters[name] = counter
        self.dll.PdhCollectQueryData(query)

    def sample(self) -> dict:
        if self.query is None:
            return {}
        self.dll.PdhCollectQueryData(self.query)
        values = {}
        for name, handle in self.counters.items():
            value = self.value_type()
            status = self.dll.PdhGetFormattedCounterValue(handle, 0x200, None,
                                                          self.ctypes.byref(value))
            if status == 0 and value.status in (0, 1) and math.isfinite(value.doubleValue):
                values[name] = value.doubleValue
        return values

    def close(self):
        if self.query is not None:
            self.dll.PdhCloseQuery(self.query)
            self.query = None


class ResourceSampler:
    def __init__(self, owner: 'Benchmark'):
        self.owner = owner
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='capacity-resources', daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=10)

    def run(self):
        counters = None
        try:
            import psutil
            counters = WindowsMemoryCounters()
            psutil.cpu_percent()
            try:
                server = psutil.Process(self.owner.args.server_pid)
                server.cpu_percent()
            except psutil.Error:
                server = None
            client = psutil.Process()
            last_checkpoint = time.monotonic()
            while not self.stop_event.is_set():
                memory = psutil.virtual_memory()
                swap = psutil.swap_memory()
                disk = psutil.disk_io_counters()
                sample = {'at': utc_now(), 'elapsed_seconds': time.monotonic() - self.owner.started,
                          'host_cpu_percent': psutil.cpu_percent(),
                          'available_memory_bytes': memory.available,
                          'host_memory_used_percent': memory.percent,
                          'swap_or_pagefile_allocated_bytes': swap.used,
                          'swap_in_bytes_counter': swap.sin, 'swap_out_bytes_counter': swap.sout,
                          'benchmark_client_rss_bytes': client.memory_info().rss,
                          'windows_memory': counters.sample()}
                if disk:
                    sample['host_disk_io_counters'] = {key: getattr(disk, key) for key in
                                                      ('read_bytes', 'write_bytes', 'read_count', 'write_count')}
                if server:
                    try:
                        with server.oneshot():
                            info = server.memory_info()
                            sample['server'] = {'pid': server.pid, 'rss_bytes': info.rss,
                                                'cpu_percent': server.cpu_percent(),
                                                'threads': server.num_threads()}
                            for key in ('num_page_faults', 'pagefile', 'private', 'peak_pagefile'):
                                if hasattr(info, key):
                                    sample['server'][key] = getattr(info, key)
                    except psutil.Error:
                        sample['server'] = {'pid': self.owner.args.server_pid, 'unavailable': True}
                        server = None
                else:
                    sample['server'] = {'pid': self.owner.args.server_pid, 'unavailable': True}
                with self.owner.lock:
                    self.owner.report['resource_samples'].append(sample)
                if time.monotonic() - last_checkpoint >= 5:
                    self.owner.checkpoint()
                    last_checkpoint = time.monotonic()
                self.stop_event.wait(self.owner.args.resource_interval)
        except Exception as exc:
            with self.owner.lock:
                self.owner.report['resource_sampling_error'] = self.owner.safe_error(exc)
        finally:
            if counters is not None:
                counters.close()


class Benchmark:
    def __init__(self, args):
        self.args = args
        self.started = time.monotonic()
        self.lock = threading.RLock()
        self.settings = {**dotenv_values(args.env_file), **os.environ}
        self.secrets = [str(value) for key, value in self.settings.items() if value and
                        any(word in key.upper() for word in ('PASSWORD', 'TOKEN', 'SECRET', 'KEY', 'DSN'))]
        self.prefix = '/api/projects/' + quote(args.project_id, safe='')
        self.client = httpx.Client(base_url=args.base_url, trust_env=False,
                                   timeout=httpx.Timeout(args.read_timeout, connect=10),
                                   limits=httpx.Limits(max_connections=8, max_keepalive_connections=8))
        self.report = {'schema_version': 1, 'started_at': utc_now(), 'completed': False,
                       'mode': args.mode, 'run_id': uuid.uuid4().hex[:12],
                       'context': {'base_url': args.base_url, 'project_id': args.project_id,
                                   'expected_entities': args.expected_entities,
                                   'expected_relations': args.expected_relations,
                                   'probe_text': PROBE, 'samples_per_warm_category': args.samples,
                                   'concurrency': 4, 'write_pairs_requested': args.write_pairs,
                                   'full_graph_loads_requested': args.full_graph_loads,
                                   'read_timeout_seconds': args.read_timeout,
                                   'write_timeout_seconds': args.write_timeout,
                                   'graph_page_limit': args.graph_page_limit,
                                   'neighborhood_node_budget': args.max_nodes,
                                   'neighborhood_edge_budget': args.max_edges,
                                   'server_pid': args.server_pid, 'python': platform.python_version(),
                                   'platform': platform.platform(), 'logical_cpus': os.cpu_count(),
                                   'background_workload_notes': args.measurement_notes,
                                   'resource_interval_seconds': args.resource_interval,
                                   'authentication': 'ordinary /api/auth/login; existing dotenv values; no secrets saved',
                                   'database_monitor': 'not run by this HTTP-only script; use a separate observer connection',
                                   'scope': 'HTTP transport + application + existing PostgreSQL/Milvus; no browser timings'},
                       'targets': {'warm_http_p95_seconds': 2, 'complete_graph_seconds': 30,
                                   'governed_edit_seconds': None,
                                   'governed_edit_target_note': 'No documented per-edit latency threshold; measured separately.'},
                       'historical_context': {'source': 'docs/research/2026-10-20-main-capacity-diagnosis.md',
                                              'single_governed_put_seconds': 102.888,
                                              'single_browser_full_graph_seconds': 77.516,
                                              'comparability': 'Single-run context only; no direct speedup ratio or P95 comparison.'},
                       'measurement_notes': [
                           'HTTP seconds use perf_counter through complete response-body download; JSON decode timed separately.',
                           'Body byte counts are decoded HTTP payload bytes; encoded Content-Length is recorded separately.',
                           'Warmups are retained but excluded from warm percentiles. Failed validations/statuses are counted separately.',
                           'Fixture vectors certify capacity and backend availability, not business semantic relevance.',
                           'Pagefile allocation is not evidence of paging. PDH page reads/writes measure host paging activity; no process attribution.',
                           'CPU/memory/paging are observed correlations; no causal hardware attribution is made.',
                           'Read mode does not modify business records; normal graph manifests are persisted graph-session artifacts.',
                           'Controlled writes append revisions; restoring content does not erase revision history.'],
                       'requests': [], 'full_graph_loads': [], 'write_pairs': [],
                       'resource_samples': [], 'errors': []}
        self.sampler = ResourceSampler(self)

    def safe_error(self, exc: BaseException) -> str:
        result = _redact(f'{type(exc).__name__}: {exc}')
        for secret in self.secrets:
            if len(secret) > 3:
                result = result.replace(secret, '<REDACTED>')
        return result[:800]

    def checkpoint(self):
        with self.lock:
            self.report['elapsed_seconds'] = time.monotonic() - self.started
            payload = json.dumps(self.report, ensure_ascii=False, indent=2)
            for secret in self.secrets:
                if len(secret) > 3:
                    payload = payload.replace(secret, '<REDACTED>')
            payload = _redact(payload)
            self.args.output.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.args.output.with_suffix(self.args.output.suffix + '.tmp')
            temporary.write_text(payload, encoding='utf-8')
            temporary.replace(self.args.output)

    def measure(self, category: str, phase: str, method: str, path: str,
                body: dict | None = None, validator: Callable | None = None,
                expected_status: int = 200) -> tuple[Any, dict]:
        request_id = uuid.uuid4().hex
        row = {'category': category, 'phase': phase, 'method': method, 'path': path,
               'request_id': request_id,
               'started_at': utc_now(), 'start_elapsed_seconds': time.monotonic() - self.started,
               'expected_status': expected_status, 'status_code': None, 'ok': False}
        started = time.perf_counter()
        data = None
        try:
            timeout = self.args.write_timeout if method == 'PUT' else self.args.read_timeout
            response = self.client.request(method, path, json=body, timeout=timeout,
                                           headers={'X-Capacity-Request': request_id,
                                                    'X-Request-ID': request_id})
            row['http_seconds'] = time.perf_counter() - started
            row['status_code'] = response.status_code
            row['response_body_bytes'] = len(response.content)
            row['request_body_bytes'] = len(response.request.content)
            row['encoded_content_length_header'] = response.headers.get('content-length')
            row['content_encoding'] = response.headers.get('content-encoding')
            row['response_capacity_request'] = response.headers.get('x-capacity-request')
            row['response_request_id'] = response.headers.get('x-request-id')
            check(response.status_code == expected_status,
                  f'HTTP status {response.status_code}; expected {expected_status}; response body omitted')
            decode_started = time.perf_counter()
            data = response.json()
            row['json_decode_seconds'] = time.perf_counter() - decode_started
            if validator:
                row['validation'] = validator(data) or {}
            row['ok'] = True
        except Exception as exc:
            row.setdefault('http_seconds', time.perf_counter() - started)
            row['error'] = self.safe_error(exc)
        finally:
            row['operation_seconds'] = time.perf_counter() - started
            with self.lock:
                self.report['requests'].append(row)
            self.checkpoint()
        return data, row

    def required(self, *args, **kwargs):
        data, row = self.measure(*args, **kwargs)
        check(row['ok'], f"{row['category']} failed: {row.get('error', 'unknown')}")
        return data

    def login_and_discover(self):
        username = self.settings.get('KG_SEED_ADMIN_USERNAME')
        password = self.settings.get('KG_SEED_ADMIN_PASSWORD')
        check(bool(username and password), 'Existing .env must provide seed admin credentials; no fallback password is used')
        login_request_id = uuid.uuid4().hex
        response = self.client.post('/api/auth/login', json={'username': username, 'password': password},
                                    headers={'X-Capacity-Request': login_request_id,
                                             'X-Request-ID': login_request_id})
        self.report['authentication_request'] = {
            'request_id': login_request_id, 'status_code': response.status_code,
            'response_capacity_request': response.headers.get('x-capacity-request'),
            'response_request_id': response.headers.get('x-request-id')}
        check(response.status_code == 200, f'Ordinary authentication failed with HTTP {response.status_code}; body omitted')
        token = response.json().get('access_token')
        check(bool(token), 'Authentication response has no access_token')
        self.secrets.append(token)
        self.client.headers['Authorization'] = 'Bearer ' + token
        schema = self.required('openapi', 'setup', 'GET', '/openapi.json')
        required_routes = ('/api/projects/{project}/records/page',
                           '/api/projects/{project}/graph-neighborhood',
                           '/api/projects/{project}/graph-sessions',
                           '/api/projects/{project}/graph-sessions/{session_id}/page')
        check(all(route in schema['paths'] for route in required_routes), 'Current OpenAPI lacks required bounded read endpoints')
        revision = schema['components']['schemas']['Revision']
        check({'record', 'expected_version'} <= set(revision['properties']), 'Current normal PUT schema lacks CAS fields')
        self.report['openapi_sha256'] = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
        # Prior manual acceptance edits can leave a suffix in the probe's text.
        # Explicit verified IDs bypass name search; automatic discovery considers
        # at most five candidates and never chooses between ambiguous entities.
        if self.args.probe_id:
            self.probe_id = self.args.probe_id
            self.report['probe_discovery'] = {'method': 'explicit_stable_id', 'id': self.probe_id}
        else:
            page = self.required('probe_lookup', 'setup', 'POST', self.prefix + '/records/page',
                                 {'query': PROBE, 'kinds': ['entity'], 'limit': 5, 'include_total': False})
            self.report['probe_discovery'] = {
                'method': 'bounded_named_candidates', 'query': PROBE, 'limit': 5,
                'response_keys': sorted(page),
                'candidates': [{key: row.get(key) for key in ('id', 'kind', 'text', 'ontology_id')}
                               for row in page.get('records', []) if isinstance(row, dict)]}
            self.checkpoint()
            self.probe_id = select_probe(page)['id']
        self.probe_path = self.prefix + '/records/' + quote(self.probe_id, safe='')
        self.original = self.required('probe_detail', 'setup', 'GET', self.probe_path,
                                      validator=self.validate_probe)
        self.report['probe'] = {'id': self.probe_id, 'version': self.original['version'],
                                'ontology_id': self.original['ontology_id'],
                                'originalRecord': writable(self.original)}
        self.report['context']['actual_probe_text'] = self.original['text']
        self.checkpoint()
        if self.args.mode in ('read', 'all'):
            self.required('records_page20_initial', 'setup', 'POST', self.prefix + '/records/page',
                          {'kinds': ['entity'], 'limit': 20, 'include_total': False},
                          validator=self.validate_page)
            relation = self.required('seed_relation_lookup', 'setup', 'POST', self.prefix + '/records/page',
                                     {'kinds': ['relation'], 'limit': 1, 'include_total': False})
            check(bool(relation.get('records')), 'No relation found for representative connected expansion seed')
            self.seed_id = self.args.seed_id or relation['records'][0]['subject_id']
            self.report['context']['neighborhood_seed_id'] = self.seed_id
            self.report['context']['neighborhood_seed_source'] = 'explicit CLI seed' if self.args.seed_id else 'first bounded relation-page subject'

    def validate_probe(self, data):
        check(data.get('id') == self.probe_id and data.get('kind') == 'entity', 'Probe identity/kind changed')
        check(bool(data.get('ontology_id')) and isinstance(data.get('version'), int), 'Probe must have governed ontology binding and CAS version')
        return {'id': data['id'], 'version': data['version'], 'ontology_id': data['ontology_id']}

    @staticmethod
    def validate_page(data):
        records = data.get('records')
        check(isinstance(records, list) and 0 < len(records) <= 20, 'Bounded records page must contain 1–20 records')
        return {'records': len(records), 'has_next': bool(data.get('next_cursor')), 'total': data.get('total')}

    def validate_search(self, data, mode):
        check(data.get('_backend') == 'milvus', 'Search must use the real milvus backend')
        check(data.get('degraded') is False, 'Search must explicitly report degraded=false')
        check(data.get('active_mode') == mode, 'Search active_mode differs from requested mode')
        hits = data.get('hits', [])
        counts = Counter(hit['kind'] for hit in hits)
        check(bool(hits) and len(hits) <= 20, 'Search should return 1–20 hits')
        return {'backend': data['_backend'], 'degraded': data['degraded'],
                'active_mode': mode, 'hits': len(hits), 'channel_counts': dict(counts)}

    def validate_neighborhood(self, data):
        records = data.get('records', [])
        counts = Counter(record.get('kind') for record in records)
        check(counts['entity'] <= self.args.max_nodes and counts['relation'] <= self.args.max_edges,
              'Neighborhood response exceeded the requested node/edge budgets')
        check(any(row.get('id') == self.seed_id for row in records), 'Neighborhood must contain its seed')
        return {'counts': dict(counts), 'truncated': data.get('truncated'),
                'node_budget': self.args.max_nodes, 'edge_budget': self.args.max_edges}

    def validate_manifest(self, data):
        check(data.get('project_id') == self.args.project_id, 'Graph manifest belongs to another project')
        check(data.get('entity_count') == self.args.expected_entities and
              data.get('relation_count') == self.args.expected_relations, 'Graph manifest corpus counts differ from retained corpus')
        return {'entities': data['entity_count'], 'relations': data['relation_count'], 'id': data['id']}

    def read_cases(self):
        cases = [('detail', 'GET', self.probe_path, None, self.validate_probe),
                 ('records_page20', 'POST', self.prefix + '/records/page',
                  {'kinds': ['entity'], 'limit': 20, 'include_total': False}, self.validate_page),
                 ('exact_record_page', 'POST', self.prefix + '/records/page',
                  {'record_ids': [self.probe_id], 'kinds': ['entity'], 'limit': 1, 'include_total': False}, self.validate_page),
                 ('keyword_page20', 'POST', self.prefix + '/records/page',
                  {'query': PROBE, 'kinds': ['entity'], 'limit': 20, 'include_total': False}, self.validate_page),
                 ('history', 'GET', self.probe_path + '/history', None,
                  lambda data: {'versions': len(data.get('versions', []))})]
        for hops in (1, 3, 10):
            cases.append((f'neighborhood_hops{hops}', 'POST', self.prefix + '/graph-neighborhood',
                          {'seeds': [self.seed_id], 'hops': hops, 'direction': 'both',
                           'max_nodes': self.args.max_nodes, 'max_edges': self.args.max_edges},
                          self.validate_neighborhood))
        for mode in ('semantic', 'hybrid'):
            cases.append((mode, 'POST', self.prefix + '/search',
                          {'query': PROBE, 'retrieval_mode': mode, 'kinds': ['entity', 'relation'],
                           'k_entities': 10, 'k_relations': 10, 'k_chunks': 0, 'k': 20},
                          lambda data, selected=mode: self.validate_search(data, selected)))
        cases.append(('graph_manifest', 'POST', self.prefix + '/graph-sessions', {}, self.validate_manifest))
        return cases

    def run_reads(self):
        cases = self.read_cases()
        if self.args.categories:
            selected = set(self.args.categories.split(','))
            known = {case[0] for case in cases}
            check(selected <= known, 'Unknown categories: ' + ','.join(sorted(selected - known)))
            cases = [case for case in cases if case[0] in selected]
        for category, method, path, body, validator in cases:
            self.measure(category, 'warmup', method, path, body, validator)
            for _ in range(self.args.samples):
                self.measure(category, 'warm', method, path, body, validator)
            self.refresh_summaries()
            summary = self.report['summaries'].get('warm.' + category, {})
            print(json.dumps({'category': category, 'phase': 'warm', **summary}, ensure_ascii=True), flush=True)
        if not self.args.skip_concurrency:
            mixed = [case for case in cases if case[0] in ('detail', 'records_page20', 'semantic', 'neighborhood_hops3')]
            if mixed:
                jobs = [case for _ in range(self.args.samples) for case in mixed]
                def concurrent_request(case):
                    name, method, path, body, validator = case
                    return self.measure(name, 'concurrent4', method, path, body, validator)[1]
                started = time.monotonic()
                with ThreadPoolExecutor(max_workers=4) as executor:
                    list(executor.map(concurrent_request, jobs))
                self.report['concurrent4_wall_seconds'] = time.monotonic() - started
                self.refresh_summaries()
                print(json.dumps({'phase': 'concurrent4', 'requests': len(jobs),
                                  'wall_seconds': self.report['concurrent4_wall_seconds']}, ensure_ascii=True), flush=True)
        if not self.args.skip_full_graph:
            for index in range(self.args.full_graph_loads):
                self.run_full_graph(index + 1)

    def run_full_graph(self, number):
        result = {'number': number, 'started_at': utc_now(), 'ok': False, 'kinds': {}}
        self.report['full_graph_loads'].append(result)
        start = time.monotonic()
        initial_request_count = len(self.report['requests'])
        try:
            manifest = self.required('graph_manifest', 'fullgraph', 'POST', self.prefix + '/graph-sessions',
                                     {}, self.validate_manifest)
            result['manifest_id'] = manifest['id']
            entity_ids = set()
            for kind, expected in (('entities', self.args.expected_entities),
                                   ('relations', self.args.expected_relations)):
                seen = entity_ids if kind == 'entities' else set()
                cursor = None
                pages = 0
                digest = hashlib.sha256()
                while True:
                    page = self.required('graph_' + kind + '_page', 'fullgraph', 'POST',
                                         self.prefix + '/graph-sessions/' + quote(manifest['id'], safe='') + '/page',
                                         {'kind': kind, 'cursor': cursor, 'limit': self.args.graph_page_limit})
                    records = page.get('records', [])
                    check(len(records) <= self.args.graph_page_limit, 'Graph page exceeded requested batch size')
                    check(page.get('total') == expected, 'Graph page total changed from the manifest')
                    for record in records:
                        identifier = record['id']
                        check(identifier not in seen, 'Duplicate graph ID across pages')
                        seen.add(identifier)
                        digest.update(identifier.encode('utf-8') + b'\n')
                        if kind == 'relations':
                            check(record.get('subject_id') in entity_ids and record.get('object_id') in entity_ids,
                                  'Graph relation endpoint absent from complete entity enumeration')
                    pages += 1
                    result['kinds'][kind] = {'unique_records': len(seen), 'pages': pages,
                                             'id_sequence_sha256': digest.hexdigest()}
                    following = page.get('next_cursor')
                    check(not following or following != cursor, 'Graph cursor did not advance')
                    check(bool(records) or not following, 'Graph returned empty page with another cursor')
                    check(len(seen) <= expected, 'Graph enumeration exceeded manifest count')
                    if not following:
                        break
                    cursor = following
                    self.checkpoint()
                check(len(seen) == expected, 'Full graph enumeration differs from manifest count')
                self.checkpoint()
            result['ok'] = True
        except Exception as exc:
            result['error'] = self.safe_error(exc)
        finally:
            result['wall_seconds'] = time.monotonic() - start
            requests = self.report['requests'][initial_request_count:]
            result['http_seconds_sum'] = sum(row['http_seconds'] for row in requests)
            result['json_decode_seconds_sum'] = sum(row.get('json_decode_seconds', 0) for row in requests)
            result['response_body_bytes'] = sum(row.get('response_body_bytes', 0) for row in requests)
            result['requests'] = len(requests)
            result['within_30_seconds'] = result['ok'] and result['wall_seconds'] <= 30
            self.refresh_summaries()
            print(json.dumps({'full_graph_load': result}, ensure_ascii=True), flush=True)

    def restore_probe(self, original, edited, pair):
        latest = self.required('restore_read', 'write_control', 'GET', self.probe_path,
                               validator=self.validate_probe)
        if equivalent(latest, original):
            pair['restored'] = True
            pair['restored_version'] = latest['version']
            return latest
        check(equivalent(latest, edited), 'Probe changed concurrently; refusing to overwrite unrelated content during restore')
        restored = self.required('governed_restore', 'write', 'PUT', self.probe_path,
                                 {'record': writable(original), 'expected_version': latest['version']},
                                 validator=self.validate_probe)
        check(equivalent(restored, original), 'Restore response did not preserve original governed fields')
        verify = self.required('restore_verify', 'write_control', 'GET', self.probe_path,
                               validator=self.validate_probe)
        check(equivalent(verify, original), 'Read after restore differs from original governed fields')
        pair['restored'] = True
        pair['restored_version'] = verify['version']
        return verify

    def run_writes(self):
        check(self.args.exercise_write, 'Write exercise requires --exercise-write')
        original_record = writable(self.original)
        for number in range(1, self.args.write_pairs + 1):
            pair = {'number': number, 'started_at': utc_now(), 'restored': False, 'ok': False}
            self.report['write_pairs'].append(pair)
            before = self.required('write_before', 'write_control', 'GET', self.probe_path,
                                   validator=self.validate_probe)
            check(equivalent(before, original_record), 'Probe content changed before pair; no edit performed')
            edited = deepcopy(original_record)
            edited['text'] = original_record['text'] + f" [capacity-recertification {self.report['run_id']} pair {number}]"
            pair['before_version'] = before['version']
            pair['attempted_text'] = edited['text']
            self.checkpoint()
            # A timed-out PUT can still commit. Always inspect and attempt a
            # content-guarded CAS restore, even if the edit response was lost.
            try:
                saved = self.required('governed_edit', 'write', 'PUT', self.probe_path,
                                      {'record': edited, 'expected_version': before['version']},
                                      validator=self.validate_probe)
                check(equivalent(saved, edited), 'Saved edit response differs from intended content')
                check(saved['version'] > before['version'], 'Edit did not advance the CAS revision')
                pair['edited_version'] = saved['version']
                if number == 1:
                    self.required('stale_version_cas', 'write_control', 'PUT', self.probe_path,
                                  {'record': original_record, 'expected_version': before['version']},
                                  expected_status=409)
                    after = self.required('stale_cas_verify', 'write_control', 'GET', self.probe_path,
                                          validator=self.validate_probe)
                    check(after['version'] == saved['version'] and equivalent(after, edited),
                          'Stale CAS rejection changed the record')
                    self.report['stale_version_rejected_409'] = True
                pair['ok'] = True
            except BaseException as exc:
                pair['error'] = self.safe_error(exc)
                if not isinstance(exc, Exception):
                    raise
            finally:
                try:
                    self.restore_probe(original_record, edited, pair)
                except Exception as exc:
                    pair['restoration_error'] = self.safe_error(exc)
                    pair['manual_review_required'] = True
                    pair['ok'] = False
                self.refresh_summaries()
                print(json.dumps({'write_pair': pair}, ensure_ascii=True), flush=True)
            if not pair['ok'] or not pair['restored']:
                break

    def run_writes_with_reads(self):
        """Keep a bounded HTTP reader active during edits and guarded restores."""
        stop = threading.Event()
        def read_loop():
            while not stop.is_set():
                self.measure('records_page20', 'during_write', 'POST',
                             self.prefix + '/records/page',
                             {'kinds': ['entity'], 'limit': 20, 'include_total': False},
                             self.validate_page)
                if stop.is_set():
                    break
                self.measure('detail', 'during_write', 'GET', self.probe_path,
                             validator=self.validate_probe)
                stop.wait(.25)
        worker = threading.Thread(target=read_loop, name='capacity-write-reader')
        worker.start()
        try:
            self.run_writes()
        finally:
            stop.set()
            worker.join()

    def refresh_summaries(self):
        with self.lock:
            grouped = {}
            for row in self.report['requests']:
                grouped.setdefault(row['phase'] + '.' + row['category'], []).append(row)
            summaries = {name: summarize(rows) for name, rows in grouped.items()}
            for name, summary in summaries.items():
                if name.startswith(('warm.', 'concurrent4.')):
                    summary['warm_p95_target_seconds'] = 2
                    summary['warm_p95_evidence_sufficient'] = summary['successful_samples'] >= 20
                    summary['warm_target_passed'] = bool(summary['successful_samples'] >= max(20, self.args.samples) and
                                                         not summary['failed_samples'] and
                                                         summary['p95_seconds'] is not None and summary['p95_seconds'] <= 2)
            write_rows = [row for row in self.report['requests'] if row['phase'] == 'write']
            if write_rows:
                summaries['write.all_paired_puts'] = summarize(write_rows)
            self.report['summaries'] = summaries
            samples = self.report['resource_samples']
            server_rss = [s['server']['rss_bytes'] for s in samples if 'rss_bytes' in s['server']]
            windows_max = {}
            for key in WindowsMemoryCounters.PATHS:
                values = [s['windows_memory'][key] for s in samples if key in s['windows_memory']]
                windows_max[key] = max(values, default=None)
            self.report['resource_summary'] = {
                'samples': len(samples),
                'host_cpu_percent_max': max((s['host_cpu_percent'] for s in samples), default=None),
                'available_memory_bytes_min': min((s['available_memory_bytes'] for s in samples), default=None),
                'server_rss_bytes_max': max(server_rss, default=None),
                'client_rss_bytes_max': max((s['benchmark_client_rss_bytes'] for s in samples), default=None),
                'windows_memory_max': windows_max,
                'pagefile_allocation_is_not_paging_evidence': True}
            loads = self.report['full_graph_loads']
            successful_loads = [load['wall_seconds'] for load in loads if load.get('ok')]
            self.report['full_graph_summary'] = {
                'attempted_loads': len(loads), 'successful_loads': len(successful_loads),
                'p50_seconds': percentile(successful_loads, .5),
                'p95_seconds': percentile(successful_loads, .95),
                'small_sample_caution': len(successful_loads) < 20,
                'complete_load_target_seconds': 30,
                'percentile_method': 'nearest rank; complete successful loads only; failed loads separate'}
            measured_targets = [summary['warm_target_passed'] for summary in summaries.values()
                                if 'warm_target_passed' in summary]
            measured_targets.extend(load.get('within_30_seconds', False) for load in loads if 'wall_seconds' in load)
            self.report['measured_performance_targets_passed'] = all(measured_targets) if measured_targets else None
        self.checkpoint()

    def run(self) -> int:
        self.checkpoint()
        if not self.args.skip_resources:
            self.sampler.start()
        try:
            self.login_and_discover()
            if self.args.mode in ('read', 'all'):
                self.run_reads()
            if self.args.mode in ('write', 'all'):
                if self.args.read_during_write:
                    self.run_writes_with_reads()
                else:
                    self.run_writes()
            self.report['completed'] = True
        except BaseException as exc:
            self.report['errors'].append(self.safe_error(exc))
        finally:
            if not self.args.skip_resources:
                self.sampler.stop()
            self.report['finished_at'] = utc_now()
            self.refresh_summaries()
            self.client.close()
        failed = [row for row in self.report['requests'] if not row['ok']]
        graph_failed = any(not item['ok'] for item in self.report['full_graph_loads'])
        write_failed = any(not item['ok'] or not item['restored'] for item in self.report['write_pairs'])
        self.report['validation_passed'] = bool(self.report['completed'] and not self.report['errors'] and
                                                not failed and not graph_failed and not write_failed)
        self.checkpoint()
        print(json.dumps({'output': str(self.args.output), 'completed': self.report['completed'],
                          'validation_passed': self.report['validation_passed'],
                          'failed_requests': len(failed), 'elapsed_seconds': self.report['elapsed_seconds'],
                          'errors': self.report['errors']}, ensure_ascii=True), flush=True)
        return 0 if self.report['validation_passed'] else 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base-url', default='http://127.0.0.1:8100')
    parser.add_argument('--project-id', default=PROJECT)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--mode', choices=('read', 'write', 'all'), default='read')
    parser.add_argument('--samples', type=int, default=20)
    parser.add_argument('--server-pid', type=int, default=14584)
    parser.add_argument('--exercise-write', action='store_true')
    parser.add_argument('--write-pairs', type=int, default=5)
    parser.add_argument('--read-during-write', action='store_true',
                        help='Measure bounded page/detail HTTP reads concurrently with edit/restore PUTs.')
    parser.add_argument('--expected-entities', type=int, default=150001)
    parser.add_argument('--expected-relations', type=int, default=300000)
    parser.add_argument('--skip-full-graph', action='store_true')
    parser.add_argument('--full-graph-loads', type=int, default=1)
    parser.add_argument('--graph-page-limit', type=int, default=10000)
    parser.add_argument('--max-nodes', type=int, default=500)
    parser.add_argument('--max-edges', type=int, default=1000)
    parser.add_argument('--seed-id')
    parser.add_argument('--probe-id', help='Independently verified stable governed test entity ID; avoids mutable-name discovery.')
    parser.add_argument('--categories', help='Comma-separated warm categories; see read_cases. Omitted means all.')
    parser.add_argument('--skip-concurrency', action='store_true')
    parser.add_argument('--read-timeout', type=float, default=120)
    parser.add_argument('--write-timeout', type=float, default=240)
    parser.add_argument('--resource-interval', type=float, default=2)
    parser.add_argument('--skip-resources', action='store_true', help='Use the separate resource/PG observer only.')
    parser.add_argument('--measurement-notes', default='No external workload declared; record concurrent browser/index work explicitly.')
    parser.add_argument('--self-test', action='store_true', help='Offline unit checks only; no app/DB/server requests.')
    args = parser.parse_args(argv)
    if args.mode in ('write', 'all') and not args.exercise_write:
        parser.error('--mode write/all requires --exercise-write; read is the safe default')
    if args.samples < 1 or not 1 <= args.write_pairs <= 10 or args.full_graph_loads < 1:
        parser.error('samples and full-graph-loads must be positive; write-pairs must be 1–10')
    if not 1 <= args.graph_page_limit <= 10000 or not 1 <= args.max_nodes <= 10000 or not 1 <= args.max_edges <= 10000:
        parser.error('graph page and neighborhood budgets must be between 1 and 10000')
    if min(args.read_timeout, args.write_timeout, args.resource_interval) <= 0:
        parser.error('timeouts and resource interval must be positive')
    parsed = urlsplit(args.base_url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error('base-url must be an http(s) origin with no credentials, query, or fragment')
    args.base_url = args.base_url.rstrip('/')
    args.output = (args.output or ROOT / f'docs/research/2026-10-21-retained-capacity-http-{args.mode}.json').resolve()
    return args


def self_test() -> int:
    """Pure helper and HTTP-contract checks; MockTransport cannot reach a server."""
    assert percentile([], .95) is None
    assert percentile(list(range(1, 21)), .95) == 19
    assert percentile([4, 2, 3, 1], .5) == 2
    rows = [{'ok': True, 'http_seconds': value, 'status_code': 200} for value in range(1, 21)]
    rows.append({'ok': False, 'http_seconds': 99, 'status_code': 503})
    summary = summarize(rows)
    assert summary['samples'] == 21 and summary['p95_seconds'] == 19 and summary['failed_samples'] == 1
    original = {'id': 'probe', 'kind': 'entity', 'text': PROBE, 'ontology_id': 'release', 'version': 2}
    assert equivalent(original, {**original, 'version': 3, 'metadata': {}, 'properties': {}, 'source_id': None})
    assert not equivalent(original, {**original, 'ontology_id': 'different'})
    assert 'version' not in writable(original)
    assert parse_args([]).mode == 'read'
    assert not parse_args([]).exercise_write
    assert not parse_args([]).read_during_write
    assert parse_args(['--read-during-write']).read_during_write
    suffixed = {**original, 'text': PROBE + ' — earlier acceptance revision'}
    assert select_probe({'records': [suffixed], 'next_cursor': None})['id'] == 'probe'
    for invalid_page in ({'items': [suffixed]},
                         {'records': [suffixed, {**suffixed, 'id': 'other'}]},
                         {'records': [suffixed], 'next_cursor': 'more-candidates'}):
        try:
            select_probe(invalid_page)
        except BenchmarkFailure:
            pass
        else:
            raise AssertionError('Missing records key or ambiguous bounded probe lookup must fail safely')
    assert parse_args(['--probe-id', 'verified-probe']).probe_id == 'verified-probe'
    # Exercise the real guarded restore method against a stateful fake HTTP CAS
    # endpoint. No application import, network, credential read, or file write.
    state = {**original, 'version': 3, 'text': PROBE + ' marker'}
    edited = writable(state)
    calls = []
    def handle(request):
        calls.append(request.method)
        if request.method == 'GET':
            return httpx.Response(200, json=state)
        body = json.loads(request.content)
        if body['expected_version'] != state['version']:
            return httpx.Response(409, json={'detail': 'stale'})
        state.update(body['record'])
        state['version'] += 1
        return httpx.Response(200, json=state)
    benchmark = object.__new__(Benchmark)
    benchmark.client = httpx.Client(transport=httpx.MockTransport(handle), base_url='http://offline.invalid')
    benchmark.probe_path = '/probe'
    benchmark.probe_id = 'probe'
    def required(category, phase, method, path, body=None, validator=None, **kwargs):
        response = benchmark.client.request(method, path, json=body)
        check(response.status_code == kwargs.get('expected_status', 200), 'Unexpected mock HTTP status')
        data = response.json()
        if validator:
            validator(data)
        return data
    benchmark.required = required
    pair = {}
    benchmark.restore_probe(original, edited, pair)
    assert pair['restored'] and equivalent(state, original) and state['version'] == 4
    assert calls == ['GET', 'PUT', 'GET']
    required('stale', '', 'PUT', '/probe', {'record': edited, 'expected_version': 3}, expected_status=409)
    assert equivalent(state, original) and state['version'] == 4
    state['text'] = 'third party change'
    try:
        benchmark.restore_probe(original, edited, {})
    except BenchmarkFailure:
        pass
    else:
        raise AssertionError('Unrelated concurrent change must prevent restoration')
    assert state['text'] == 'third party change' and state['version'] == 4
    benchmark.client.close()
    print('Offline self-test passed: percentiles, failure counts, read-only default, CAS restore and concurrency guard.')
    return 0


if __name__ == '__main__':
    arguments = parse_args()
    sys.exit(self_test() if arguments.self_test else Benchmark(arguments).run())
