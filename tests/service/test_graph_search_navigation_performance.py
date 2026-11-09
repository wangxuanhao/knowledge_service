"""Bounded neighborhood measurements on the existing isolated PostgreSQL backend.

Run with the repository's regular pytest entry point, never beside another pytest
process. ``tests/conftest.py`` redirects Repository(tmp_path) to a test-only PG
schema and cleans it up. This module does not connect to a running business API.
Timing is evidence; the assertions protect workload/identity/budget semantics.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / '.benchmarks' / 'search-local-neighborhood' / 'postgres.json'


def _records():
    """980 branching/high-degree nodes, a separate 20-node depth witness."""
    nodes = [dict(id=f'e{i:04}', kind='entity', type='Thing', text=f'基准实体{i}',
                  metadata={'navigation_benchmark': True}) for i in range(980)]
    nodes += [dict(id=f'c{i:02}', kind='entity', type='Thing', text=f'链实体{i}',
                   metadata={'navigation_benchmark': True}) for i in range(20)]
    pairs = {(f'e{(i - 1) // 5:04}', f'e{i:04}') for i in range(1, 980)}
    pairs |= {(f'e{i:04}', f'e{1 + i % 979:04}') for i in range(1, 980)}
    pairs |= {('e0000', f'e{i:04}') for i in range(1, 101)}
    pairs |= {(f'c{i:02}', f'c{i + 1:02}') for i in range(19)}
    edges = [dict(id=f'r{i:05}', kind='relation', type='related', text='基准关联',
                  subject_id=subject, object_id=target,
                  metadata={'navigation_benchmark': True})
             for i, (subject, target) in enumerate(sorted(pairs))]
    return [*nodes, *edges]


def _integer_setting(name, default, maximum):
    value = int(os.environ.get(name, default))
    if not 1 <= value <= maximum:
        raise ValueError(f'{name} must be between 1 and {maximum}')
    return value


def _summary(values):
    ordered = sorted(values)
    return {'median_ms': statistics.median(ordered),
            'p95_ms': ordered[math.ceil(len(ordered) * .95) - 1],
            'min_ms': ordered[0], 'max_ms': ordered[-1]}


def test_search_navigation_real_postgres_bounded_neighborhood_measurements(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from knowledge_service.api import create_app
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.repository import Repository
    from knowledge_service.repository.pg_engine import SqliteishConnection
    from knowledge_service.services.graph_sessions import GraphSessions
    from knowledge_service.services.service import KnowledgeService
    from knowledge_service.utils.diagnostics import redact

    # conftest owns DB isolation. Explicitly disable optional external vector
    # connectors even when the parent process has unrelated service settings.
    monkeypatch.setenv('KG_VECTOR_BACKEND', '')
    app = create_app(tmp_path / 'navigation-performance', encoder=HashingEncoder(), auth_disabled=True)
    repo = app.state.service.repository
    assert isinstance(repo._db, SqliteishConnection), 'Measurements require the real PostgreSQL adapter'
    assert repo._db._conn.info.server_version >= 120000
    pg_version = repo._db._conn.info.server_version
    project = repo.create_project('隔离测试：检索邻域性能')['id']
    records = _records()
    setup_started = time.perf_counter()
    repo.put_batch(project, records)
    setup_ms = (time.perf_counter() - setup_started) * 1000

    def forbidden(*_args, **_kwargs):
        pytest.fail('A bounded neighborhood attempted a full-corpus/session read')

    # Fail loudly if this API regresses into either legacy graph assembly or a
    # full graph/export operation. Ontology presentation still uses its actual
    # independent scoped reads; no database reads are replaced with fakes.
    monkeypatch.setattr(KnowledgeService, 'scoped', forbidden)
    monkeypatch.setattr(Repository, 'query', forbidden)
    monkeypatch.setattr(Repository, 'export_projection', forbidden)
    monkeypatch.setattr(GraphSessions, 'create', forbidden)
    monkeypatch.setattr(GraphSessions, 'page', forbidden)

    active = None
    original_execute = SqliteishConnection.execute

    def observe_execute(connection, sql, params=()):
        if active is not None:
            active['sql_count'] += 1
            normalized = ' '.join(sql.split())
            if ' adjacent ORDER BY id,version LIMIT ?' in normalized:
                # Class-level wrapping observes borrowed read-only PG adapters
                # as well as the writer. Record only sizes/limits, never SQL
                # parameter contents, connection strings, or credentials.
                directions = 2 if "->>'subject_id'=ANY" in sql and "->>'object_id'=ANY" in sql else 1
                frontiers = params[-(directions + 1):-1]
                active['frontier_queries'].append({
                    'frontier_sizes': [len(frontier) for frontier in frontiers],
                    'limit': int(params[-1]),
                })
        return original_execute(connection, sql, params)

    monkeypatch.setattr(SqliteishConnection, 'execute', observe_execute)
    samples = _integer_setting('KG_NAV_PERF_SAMPLES', 5, 30)
    warmups = _integer_setting('KG_NAV_PERF_WARMUPS', 1, 5)
    artifact = {
        'schema_version': 1, 'status': 'running', 'kind': 'real-postgres-api',
        'fixture': {'entities': 1000, 'relations': len(records) - 1000,
                    'sha256': hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                    'setup_ms_excluded': setup_ms,
                    'topology': 'five-way tree + sibling ring + 100-degree hub; disjoint 20-node chain'},
        'postgres_server_version': pg_version,
        'policy': {'warmups_per_case': warmups, 'samples_per_case': samples,
                   'p95_method': 'nearest rank; at five samples p95 is the maximum',
                   'scope': 'isolated pytest PostgreSQL schema, synthetic project only',
                   'timed_region': 'synchronous TestClient POST through real API/service/PG and JSON response',
                   'sql_instrumentation': 'pass-through adapter wrapper; sizes/counts only; included in timing',
                   'cache_policy': 'per-case warmup; no PG/OS cache flush; serial requests',
                   'limits': ['local test-machine measurements', 'no production corpus or network transport',
                              'no Milvus/model/embedding latency', 'no exact-millisecond CI threshold']},
        'cases': [], 'guards': {},
    }
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)

    def save():
        ARTIFACT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    save()
    try:
        # TestClient intentionally has no context-manager lifespan: these read
        # endpoints require no background workers or external service startup.
        client = TestClient(app)
        for topology, seed in [('branching-high-degree', 'e0000'), ('depth-witness', 'c00')]:
            for hops in (1, 2, 3, 10):
                body = dict(seeds=[seed], hops=hops, direction='both', max_nodes=500, max_edges=1000)
                case = {'topology': topology, 'request': body, 'warmup_ms': [], 'samples': []}
                artifact['cases'].append(case)
                identity = None
                for index in range(warmups + samples):
                    active = {'sql_count': 0, 'frontier_queries': []}
                    started = time.perf_counter()
                    response = client.post(f'/api/projects/{project}/graph-neighborhood', json=body)
                    duration = (time.perf_counter() - started) * 1000
                    observed, active = active, None
                    assert response.status_code == 200, redact(response.text)
                    result = response.json()
                    entities = [row for row in result['records'] if row['kind'] == 'entity']
                    edges = [row for row in result['records'] if row['kind'] == 'relation']
                    ids = {row['id'] for row in entities}
                    assert seed in ids and len(ids) == len(entities) <= body['max_nodes']
                    assert len({row['id'] for row in edges}) == len(edges) <= body['max_edges']
                    assert all(row['subject_id'] in ids and row['object_id'] in ids for row in edges)
                    frontiers = observed['frontier_queries']
                    assert 1 <= len(frontiers) <= hops
                    assert all(1 <= entry['limit'] <= body['max_edges'] + 1 for entry in frontiers)
                    assert all(1 <= size <= body['max_nodes'] for entry in frontiers for size in entry['frontier_sizes'])
                    if topology == 'depth-witness':
                        # A long chain remains below budgets, so this proves
                        # requested 10 hops actually traversed ten frontiers.
                        assert len(frontiers) == hops
                        assert ids == {f'c{i:02}' for i in range(hops + 1)}
                        assert len(edges) == hops and result['truncated'] is False
                    elif hops >= 2:
                        assert result['truncated'] is True and len(entities) == body['max_nodes']
                    if result['truncated']:
                        assert result['continuation'] == {**body, 'reason': 'budget_exhausted'}
                    current_identity = sorted((row['id'], row['version_id']) for row in result['records'])
                    if identity is None:
                        identity = current_identity
                    assert current_identity == identity, 'Repeated bounded reads returned different records'
                    measurement = {'total_ms': duration, 'response_bytes': len(response.content),
                                   'entities': len(entities), 'relations': len(edges),
                                   'truncated': result['truncated'], **observed}
                    if index < warmups:
                        case['warmup_ms'].append(duration)
                    else:
                        case['samples'].append(measurement)
                case.update(_summary([item['total_ms'] for item in case['samples']]))
                save()
        # A scope-qualified missing seed is an actual empty bounded response;
        # it must not fall back to a project-wide graph or claim truncation.
        empty_body = dict(seeds=['absent-navigation-seed'], hops=10, direction='both',
                          max_nodes=500, max_edges=1000)
        active = {'sql_count': 0, 'frontier_queries': []}
        empty_response = client.post(f'/api/projects/{project}/graph-neighborhood', json=empty_body)
        empty_observed, active = active, None
        assert empty_response.status_code == 200, redact(empty_response.text)
        empty_result = empty_response.json()
        assert empty_result['records'] == [] and empty_result['truncated'] is False
        assert empty_result['continuation'] is None and empty_observed['frontier_queries'] == []
        artifact['guards']['empty_qualified_seed'] = {
            'request': empty_body, 'entities': 0, 'relations': 0, 'truncated': False,
            'response_bytes': len(empty_response.content), **empty_observed,
            'included_in_timing_samples': False,
        }
        artifact['status'] = 'passed'
    except BaseException as error:
        artifact['status'] = 'failed'
        artifact['error'] = redact(f'{type(error).__name__}: {error}')
        raise
    finally:
        active = None
        save()
