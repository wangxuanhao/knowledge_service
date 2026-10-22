"""Compare live product content with independent PostgreSQL authority reads.

Business records are never modified. The normal graph-session API creates its
usual session artifacts; optional QA creates normal retrieval/answer audit rows.
No app imports, test encoder, vector substitution or service startup is used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy

import httpx
import psycopg
from psycopg.conninfo import make_conninfo
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ('eeb3a4dc-7475-4937-9966-74aa261334e6',
            '828887db-e1f3-4f5d-b2b3-4b3dc97d2040')
CAPACITY = '46249548-2189-4ad7-8f23-70ee2bcc7f6f'
PROBE = '1ae672c2-50ff-48a9-b72f-a7daa9a51542'
FIELDS = ('id', 'kind', 'text', 'type', 'metadata', 'properties', 'source_id',
          'subject_id', 'object_id', 'ontology_id', 'valid_from', 'valid_until',
          'value', 'datatype', 'version', 'version_id')


def canonical(row):
    result = {key: row[key] for key in FIELDS if row.get(key) is not None}
    result.setdefault('metadata', {})
    result.setdefault('properties', {})
    result.setdefault('type', '')
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8100')
    parser.add_argument('--output', default='docs/research/2026-10-21-production-content-validation.json')
    parser.add_argument('--include-qa', action='store_true')
    parser.add_argument('--generate', action='store_true')
    args = parser.parse_args()
    cfg = {**dotenv_values(ROOT / '.env', interpolate=False), **os.environ}
    dsn = cfg.get('KG_DATABASE_URL') or make_conninfo(
        host=cfg.get('KG_DATABASE_HOST', '127.0.0.1'),
        port=cfg.get('KG_DATABASE_PORT', '5432'), dbname=cfg['POSTGRES_DB'],
        user=cfg['POSTGRES_APP_USER'], password=cfg['POSTGRES_APP_PASSWORD'])
    redact = runpy.run_path(str(ROOT/'knowledge_service/utils/diagnostics.py'))['redact']
    output = ROOT / args.output
    report = {'started_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'live authenticated API vs independent PG authority; no latency certification',
              'business_writes': False, 'checks': [], 'validation_passed': False}
    secrets = []

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def record(name, **details):
        report['checks'].append({'check': name, 'passed': True, **details})
        save()

    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as db, \
                httpx.Client(base_url=args.base_url, trust_env=False, timeout=240) as client:
            db.execute('SET default_transaction_read_only=on')
            db.execute("SET statement_timeout='15s'")
            response = client.post('/api/auth/login', json={
                'username': cfg['KG_SEED_ADMIN_USERNAME'], 'password': cfg['KG_SEED_ADMIN_PASSWORD']})
            assert response.status_code == 200, 'Ordinary authentication failed; body omitted'
            token = response.json()['access_token']
            secrets.append(token)
            client.headers['Authorization'] = 'Bearer ' + token

            def call(method, path, body=None):
                response = client.request(method, path, json=body)
                assert response.status_code < 400, f'{path}: HTTP {response.status_code}: {redact(response.text)[:500]}'
                return response.json()

            def authority(project, ids=None):
                sql = '''SELECT id,version,version_id,payload FROM record_versions
                         WHERE project_id=%s AND superseded_at IS NULL'''
                params = [project]
                if ids is not None:
                    sql += ' AND id=ANY(%s::text[])'
                    params.append(list(ids))
                else:
                    sql += ' AND read_visible'
                rows = {}
                for identifier, version, version_id, payload in db.execute(sql, params):
                    body = json.loads(payload) if isinstance(payload, str) else dict(payload)
                    rows[identifier] = {**body, 'id': identifier, 'version': version, 'version_id': version_id}
                return rows

            def compare(project, rows, label):
                expected = authority(project, [row['id'] for row in rows])
                for row in rows:
                    assert row['id'] in expected, f'{label}: record escaped project/current authority'
                    assert canonical(row) == canonical(expected[row['id']]), f'{label}: body/version mismatch for {row["id"]}'
                record(label, project_id=project, records=len(rows), content_sha256=digest([canonical(r) for r in rows]))

            def compare_frozen(project, rows, known_at, label):
                if not rows:
                    return
                versions = {row.get('version_id') or row.get('record_version_id') for row in rows}
                assert None not in versions, label+': citation has no exact authority version'
                expected = {}
                for identifier, version_id, payload in db.execute('''
                    SELECT id,version_id,payload FROM record_versions WHERE project_id=%s
                      AND version_id=ANY(%s::text[]) AND recorded_at<=%s
                      AND (superseded_at IS NULL OR superseded_at>%s)
                    ''', (project, list(versions), known_at, known_at)):
                    expected[version_id] = (identifier, json.loads(payload) if isinstance(payload,str) else payload)
                for row in rows:
                    version_id=row.get('version_id') or row.get('record_version_id')
                    assert version_id in expected, label+': record version was not visible at frozen time'
                    identifier, body = expected[version_id]
                    assert row.get('id')==identifier and row.get('kind')==body['kind'], label+': citation identity mismatch'
                    if 'text' in row:
                        assert row['text']==body.get('text',''), label+': frozen citation text mismatch'
                record(label, project_id=project, records=len(rows), known_at=known_at)

            projects = call('GET', '/api/projects')['projects']
            assert any(p['id'] == CAPACITY for p in projects), 'Retained capacity project missing from management'
            record('retained_capacity_project_visible')
            for project in PROJECTS:
                prefix = '/api/projects/' + project
                all_rows = authority(project)
                page = call('POST', prefix+'/records/page', {'kinds': ['entity','relation','attribute','chunk'], 'limit':100})
                compare(project, page['records'], 'ordinary_record_page')
                expected_ids = {identifier for identifier,row in all_rows.items()
                                if row['kind'] in {'entity','relation','attribute','chunk'}}
                assert {row['id'] for row in page['records']} == expected_ids and not page.get('next_cursor')
                for row in page['records']:
                    compare(project, [call('GET', prefix+'/records/'+row['id'])], 'shared_detail')

                manifest = call('POST', prefix+'/graph-sessions', {})
                node_ids, edges = set(), []
                for kind, selected in (('entities','entity'), ('relations','relation')):
                    graph = call('POST', prefix+'/graph-sessions/'+manifest['id']+'/page', {'kind':kind,'limit':10000})
                    assert graph['complete'] and not graph.get('next_cursor')
                    rows = graph['records']
                    expected = {key:row for key,row in all_rows.items() if row['kind']==selected}
                    assert {row['id'] for row in rows} == set(expected), 'Graph silently omitted/added records'
                    for row in rows:
                        for key in ('id','kind','version','version_id','type','subject_id','object_id'):
                            assert row.get(key) == expected[row['id']].get(key), 'Compact graph changed identity/endpoint'
                        assert row['text'] == str(expected[row['id']].get('text') or '')[:160]
                    if selected=='entity':
                        node_ids.update(expected)
                    else:
                        edges.extend(rows)
                assert all(e['subject_id'] in node_ids and e['object_id'] in node_ids for e in edges)
                record('complete_small_graph_matches_authority', project_id=project, nodes=len(node_ids), edges=len(edges))

                query = '设备 温度' if project == PROJECTS[0] else '天海贸易'
                for mode in ('keyword','semantic','hybrid'):
                    for scope_name, scope in (('native',{}), ('metadata_missing', {
                            'filters':{'field':'metadata.__audit_absent_field','op':'exists','value':False}})):
                        result = call('POST', prefix+'/search', {'query':query,'retrieval_mode':mode,'k':10,**scope})
                        assert result['_backend']=='milvus' and result['active_mode']==mode and result['degraded'] is False
                        assert result['hits'] and result['complete'] is True
                        compare(project, result['hits'], f'current_{mode}_{scope_name}_hits')
                    empty = call('POST', prefix+'/search', {'query':query,'retrieval_mode':mode,'k':10,
                        'filters':{'field':'metadata.__audit_absent_field','op':'exists','value':True}})
                    assert not any('__audit_absent_field' in (row.get('metadata') or {}) for row in all_rows.values())
                    assert empty['hits']==[] and empty['complete'] is True, 'Metadata predicate was ignored or truncated'
                    record('metadata_negative_scope_'+mode, project_id=project)
                if args.include_qa:
                    qa = call('POST', prefix+'/qa', {'query':query+'的文档介绍', 'retrieval_mode':'hybrid',
                        'generate':args.generate,'include_references':True,'hops':2})
                    assert qa.get('answer_id'), 'QA did not freeze a real answer/retrieval identity'
                    assert qa.get('_backend')=='milvus' and qa.get('active_modes')==['hybrid'] and qa.get('degraded') is False
                    assert qa.get('provenance_complete') is True and qa.get('known_at') and qa.get('hits')
                    assert qa.get('complete') is True, 'QA silently lost retrieval completeness'
                    compare_frozen(project, qa['hits'], qa['known_at'], 'qa_frozen_hits')
                    citations = [*qa.get('evidence',[]),*qa.get('historical_references',[]),
                                 *qa.get('future_references',[]),*qa.get('uncertain_references',[])]
                    compare_frozen(project, citations, qa['known_at'], 'qa_frozen_citations')
                    assert len({r['citation'] for r in citations})==len(citations), 'Citation IDs overlap'
                    for row in citations:
                        trace=call('GET', prefix+'/answers/'+qa['answer_id']+'/evidence/'+row['citation']+'/provenance')
                        assert trace['subject']['answer_id']==qa['answer_id'], 'Citation resolves to a different answer'
                        for source in row.get('sources',[]):
                            raw=db.execute('SELECT payload FROM record_versions WHERE project_id=%s AND id=%s AND version_id=%s',
                                (project,source['document_id'],source['document_version_id'])).fetchone()
                            assert raw is not None, 'Citation source/version is not in project authority'
                    if not citations:
                        assert qa.get('generation',{}).get('called') is False, 'Model generated without frozen supporting material'
                    report.setdefault('qa_results', []).append(qa)
                    record('default_current_hybrid_qa', project_id=project, generation=qa.get('generation'),
                           frozen_citations=len(citations), answer_id=qa['answer_id'])

            prefix = '/api/projects/' + CAPACITY
            compare(CAPACITY, [call('GET', prefix+'/records/'+PROBE)], 'retained_capacity_probe')
            page = call('POST', prefix+'/records/page', {'kinds':['entity'],'limit':20,'include_total':False})
            compare(CAPACITY, page['records'], 'retained_capacity_page20')
            report['validation_passed'] = True
    except Exception as exc:
        error = redact(str(exc))
        for secret in secrets:
            error = error.replace(secret, '<REDACTED>')
        report['error'] = {'type':type(exc).__name__, 'detail':error[:1200]}
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps({'validation_passed':report['validation_passed'], 'checks':len(report['checks']),
                      'error':report.get('error'), 'output':str(output)},ensure_ascii=False))
    return 0 if report['validation_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
