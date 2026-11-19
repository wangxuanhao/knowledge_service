"""Actual graph/ledger UI over PostgreSQL; deterministic extraction, no model calls."""
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import json
from threading import Thread
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from ontology_seed import seed_project_ontology
from test_multiround_ontology_ingestion import AcceptanceQualityEngine, checked, manual, publish
from test_workspace_redesign_ui import EDGE, Handler

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
from knowledge_service.services import hierarchy_suggestion

NS = 'https://graph.test/'
TTL = '''@prefix : <https://graph.test/> .
 @prefix owl: <http://www.w3.org/2002/07/owl#> .
 @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
 @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
 :Party a owl:Class ; rdfs:label "Party" .
 :Shop a owl:Class ; rdfs:label "Shop" .
 :owns a owl:ObjectProperty ; rdfs:domain :Party ; rdfs:range :Shop .
 :city a owl:DatatypeProperty ; rdfs:domain :Shop ; rdfs:range xsd:string .'''


def fixture_extract(self, text, ontology):
    return [dict(id='p', kind='entity', type=NS+'Party', text='Ada'),
            dict(id='s', kind='entity', type=NS+'Shop', text='Corner Shop'),
            dict(id='r', kind='relation', type=NS+'owns', text='Ada owns Corner Shop', subject_id='p', object_id='s'),
            dict(id='a', kind='attribute', type=NS+'city', text='Oslo', subject_id='s', value='Oslo',
                 datatype='http://www.w3.org/2001/XMLSchema#string', metadata={'attribute_evidence': 'Oslo'})]


def test_published_parent_ingested_facts_graph_and_ledger(tmp_path, monkeypatch):
    runtime = pytest.importorskip('playwright.sync_api')
    if not EDGE.exists():
        pytest.skip('Edge required for actual render')
    monkeypatch.setattr(SemanticaExtractor, 'extract', fixture_extract)
    monkeypatch.setattr(hierarchy_suggestion, 'suggest_hierarchy', lambda *a, **kw: {'Shop': ['Party']})
    app = create_app(tmp_path/'graph-acceptance', HashingEncoder())
    app.state.ontology_drafts.quality_engine = AcceptanceQualityEngine()
    with ExitStack() as stack:
        client = stack.enter_context(TestClient(app))
        pid = checked(client.post('/api/projects', json={'name':'Graph acceptance', 'use_default_ontology':False}), 201)['id']
        base = '/api/projects/'+pid
        first = seed_project_ontology(client, base, TTL)
        draft = manual(client, base, first['id'])
        suggestion = checked(client.post(base+f"/ontology-drafts/{draft['id']}/discover-parents", json={'expected_revision': draft['revision']}))
        assert suggestion['added_count'] == 1
        assert checked(client.get(base+'/ontology'))['id'] == first['id']
        publish(client, base, suggestion['draft'])  # Explicit submit + individual review decision + publish.
        release = checked(client.get(base+'/ontology'))
        assert release['id'] != first['id']
        definition, = release['summary']['attributes']
        assert (definition['id'], definition['domain'], definition['range']) == (NS+'city', [NS+'Shop'], ['http://www.w3.org/2001/XMLSchema#string'])
        checked(client.post(base+'/documents', json={'title':'Graph source', 'text':'Ada owns Corner Shop in Oslo.',
            'extraction_mode':'ontology', 'extract_attributes':True, 'resolve_entities':False}), 201)
        rows = checked(client.post(base+'/records/page', json={'kinds':['entity','relation','attribute','document']}))['records']
        def one(kind):
            row, = [r for r in rows if r['kind']==kind]
            return row
        edge, attr, doc = one('relation'), one('attribute'), one('document')
        entities = {r['id']:r for r in rows if r['kind']=='entity'}
        assert entities[edge['subject_id']]['text']=='Ada' and entities[edge['object_id']]['text']=='Corner Shop'
        assert attr['subject_id']==edge['object_id'] and attr['type']==definition['id']
        assert {r['ontology_id'] for r in [edge,attr,*entities.values()]}=={release['id']}
        owner = checked(client.get(base+'/records/'+attr['subject_id']))
        assert [p['id'] for p in owner['class_parents']]==[NS+'Party']
        value_url = base+'/entities/'+owner['id']+'/attributes/'+attr['id']
        read = {'expected_version_id':attr['version_id']}
        assert checked(client.post(value_url, json=read))['value']=='Oslo'
        source, = checked(client.post(value_url+'/sources', json=read))['sources']
        assert source['document_id']==doc['id'] and source['document_version_id'] and source['chunk_id'] and source['assertion_id']
        assert source['quote'] == doc['text'][source['start_char']:source['end_char']] == 'Oslo'
        assert checked(client.get(base+'/records/'+source['chunk_id']))['source_id']==doc['id']

        # Serve the real shipped shell/assets; forward every API request to this same app, never a live service.
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        Thread(target=server.serve_forever,daemon=True).start()
        stack.callback(server.server_close); stack.callback(server.shutdown)
        browser = stack.enter_context(runtime.sync_playwright()).chromium.launch(headless=True,executable_path=str(EDGE),args=['--enable-unsafe-swiftshader'])
        stack.callback(browser.close)
        page = browser.new_page(viewport={'width':1440,'height':960})
        errors = []
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        def forward(route):
            req=route.request; url=urlsplit(req.url)
            response=client.request(req.method,url.path+('?' + url.query if url.query else ''),content=req.post_data,headers={'Content-Type':'application/json'})
            route.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','application/json'))
        page.route('**/api/**',forward)
        page.goto(f'http://127.0.0.1:{server.server_port}/')
        page.wait_for_function("() => FullGraphView.loader?.state==='complete'")
        assert page.evaluate('() => [FullGraphView.renderer.dataset.nodeCount, FullGraphView.renderer.dataset.edgeCount]')==[2,1]
        page.evaluate('row=>window.focusLedgerRecord(row)', {'id':edge['id'],'kind':'relation'})
        tr=page.locator('table.ledger-relation tr[data-record-id="'+edge['id']+'"]')
        tr.wait_for()
        assert tr.locator('[data-ledger-entity]').evaluate_all('(els)=>els.map(e=>e.dataset.ledgerEntity)')==[edge['subject_id'],edge['object_id']]
        tr.locator('[data-locate-graph]').click()
        page.wait_for_function('id=>document.querySelector("#graph-detail")?.dataset.recordId===id',arg=edge['id'])
        page.screenshot(path=str(tmp_path/'published-relation.png'))
        page.locator('#graph-detail [data-action="ledger"]').click()
        tr.locator('[data-ledger-entity="'+owner['id']+'"]').click()
        page.locator('table.ledger-entity tr.ledger-row-located[data-record-id="'+owner['id']+'"]').wait_for()
        assert not errors
        (tmp_path/'graph-receipt.json').write_text(json.dumps({
            'project_id':pid, 'ontology_id':release['id'], 'record_id':edge['id'],
            'subject_id':edge['subject_id'], 'object_id':edge['object_id'],
            'attribute_id':attr['id'], 'value':attr['value'], 'type':attr['type'],
            'version_id':attr['version_id'], 'source':source,
        }, indent=2, ensure_ascii=False), encoding='utf-8')
