"""Continuous API acceptance: discovery, governed edits, inheritance and facts."""
from ontology_seed import seed_project_ontology
from fastapi.testclient import TestClient
from rdflib import OWL, RDF, RDFS, URIRef, XSD

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
from knowledge_service.services.ontology import Ontology


def checked(response, expected=200):
    assert response.status_code == expected, response.text
    return response.json()


def edit(client, base, draft, **command):
    return checked(client.post(base + f"/ontology-drafts/{draft['id']}/commands", json={
        'expected_revision': draft['revision'], 'command': command}))


def publish(client, base, draft):
    path = base + f"/ontology-drafts/{draft['id']}"
    current = checked(client.post(path + '/submit', json={
        'expected_revision': draft['revision']}))
    warnings = [item['code'] for item in current['validation_report']['warnings']
                if item.get('code')]
    fingerprint = current['validation_fingerprint']
    for operation in current['operations']:
        current = checked(client.post(path + '/decisions', json={
            'expected_revision': current['revision'],
            'expected_ontology_id': draft['base_ontology_id'],
            'validation_fingerprint': fingerprint,
            'acknowledged_warning_codes': warnings,
            'decisions': [{'operation_id': operation['id'],
                           'operation_fingerprint': operation['fingerprint'],
                           'action': 'approve', 'reason': 'Synthetic acceptance fixture reviewed'}]}))
    body = {'expected_revision': current['revision'],
            'expected_ontology_id': draft['base_ontology_id'],
            'validation_fingerprint': current['validation_fingerprint'],
            'acknowledged_warning_codes': warnings,
            'idempotency_key': 'acceptance-' + draft['id'],
            'note': 'Synthetic acceptance: incremental ontology change'}
    result = checked(client.post(path + '/publish', json=body))
    assert checked(client.post(path + '/publish', json=body))['id'] == result['id']
    return result


def manual(client, base, ontology_id):
    return checked(client.post(base + '/ontology-drafts', json={
        'base_ontology_id': ontology_id, 'source_kind': 'manual',
        'title': 'Incremental parents and attributes'}), 201)



class AcceptanceQualityEngine:
    def check(self, ontology):
        return {'engine': 'acceptance', 'engine_version': '1', 'passed': True,
                'issues': [], 'stats': {}, 'metrics': {}, 'thresholds': {},
                'threshold_failures': [], 'competency_questions': []}


def test_datatype_commands_capture_the_current_draft_precondition(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.services.ontology_drafts import OntologyDrafts

    repo = Repository(tmp_path / 'datatype-preconditions.sqlite')
    pid = repo.create_project('Datatype command regression')['id']
    service = OntologyDrafts(repo)
    draft = service.create(pid, None, 'manual', 'Typed attribute', 'acceptance')
    draft = service.command(pid, draft['id'], draft['revision'], {
        'action': 'create_term', 'target_iri': 'urn:acceptance:rating', 'kind': 'attribute'})
    previous = None
    for datatype in (str(XSD.decimal), str(XSD.integer), None):
        draft = service.command(pid, draft['id'], draft['revision'], {
            'action': 'set_datatype', 'target_iri': 'urn:acceptance:rating', 'datatype': datatype})
        operation = draft['operations'][-1]
        assert operation['before'] == {'datatype': previous}
        assert not operation['validation'].get('errors')
        attribute = draft['ontology']['attributes'][0]
        assert attribute['range'] == ([datatype] if datatype else [])
        previous = datatype
    blocked = service.command(pid, draft['id'], draft['revision'], {
        'action': 'set_datatype', 'target_iri': 'urn:acceptance:rating',
        'before': {'datatype': str(XSD.decimal)}, 'datatype': str(XSD.string)})
    assert blocked['operations'][-1]['validation']['errors']
    assert blocked['ontology']['attributes'][0]['range'] == []
    # Replacing the earlier clear operation is replayed at its original slot,
    # where the datatype was integer, rather than the current cleared value.
    replacement = service.command(pid, draft['id'], blocked['revision'], {
        'action': 'set_datatype', 'target_iri': 'urn:acceptance:rating',
        'datatype': str(XSD.string), 'supersedes_operation_id': draft['operations'][-1]['id']})
    operation = next(op for op in replacement['operations']
                     if op.get('supersedes_operation_id') == draft['operations'][-1]['id'])
    assert operation['before'] == {'datatype': str(XSD.integer)}
    assert not operation['validation'].get('errors')
    assert replacement['ontology']['attributes'][0]['range'] == [str(XSD.string)]


def test_commands_merge_attribute_domains_using_governed_union_shape():
    from knowledge_service.services.ontology_operations import (
        build_operation, canonical_turtle_diff, apply_operations)
    from rdflib.compare import isomorphic

    baseline = '''@prefix : <urn:baseline:> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        :Merchant a owl:Class; rdfs:label "Merchant" .
        :employeeCount a owl:DatatypeProperty; rdfs:label "employeeCount";
            rdfs:domain :Merchant; rdfs:range xsd:integer .'''
    merchant, shop, attribute = map(URIRef, (
        'urn:baseline:Merchant', 'urn:baseline:Shop', 'urn:baseline:employeeCount'))
    operations = [
        build_operation('create_term', str(shop), after={'kind': 'class'}),
        build_operation('add_domain', str(attribute), after={'value': str(shop)}),
    ]
    edited = Ontology(apply_operations(baseline, operations))
    domain = edited.graph.value(attribute, RDFS.domain)
    assert (domain, OWL.unionOf, None) in edited.graph
    assert set(edited.constraint_types(attribute, RDFS.domain)) == {merchant, shop}
    assert set(edited.graph.objects(attribute, RDFS.range)) == {XSD.integer}
    rebuilt = Ontology(apply_operations(baseline, canonical_turtle_diff(
        baseline, edited.graph.serialize(format='turtle'))))
    assert isomorphic(rebuilt.graph, edited.graph)
    assert set(Ontology(baseline).constraint_types(attribute, RDFS.domain)) == {merchant}


def test_four_ingestion_rounds_keep_multiple_parents_attributes_and_history(tmp_path, monkeypatch):
    """Four document batches interleaved with six governed ontology releases."""
    def discover(self, text, include_attributes=False, *, reserved_class_names=()):
        n = int(text.split('batch=')[1].split()[0])
        merchant, rule = f'Merchant-{n}', f'Rule-{n}'
        return [
            {'id': 'merchant', 'kind': 'entity', 'text': merchant,
             'proposed_type': 'Merchant' if n < 3 else 'Shop', 'confidence': .96},
            {'id': 'rule', 'kind': 'entity', 'text': rule,
             'proposed_type': 'RuleDocument', 'confidence': .96},
            {'id': 'edge', 'kind': 'relation', 'subject_id': 'rule', 'object_id': 'merchant',
             'subject': rule, 'object': merchant, 'proposed_type': 'appliesTo', 'confidence': .96},
            *[{'id': f'count-{i}', 'kind': 'attribute', 'entity_id': 'merchant',
               'subject': merchant, 'proposed_type': 'employeeCount', 'value': n * 10 + i,
               'attribute_evidence': f'employees {n * 10 + i}', 'evidence_status': 'exact',
               'confidence': .96} for i in (1, 2)],
        ]

    monkeypatch.setattr(SemanticaExtractor, 'discover', discover)
    app = create_app(tmp_path / 'continuous-acceptance.sqlite', HashingEncoder())
    app.state.ontology_drafts.quality_engine = AcceptanceQualityEngine()
    with TestClient(app) as client:
        pid = checked(client.post('/api/projects', json={
            'name': 'Isolated four-round acceptance', 'use_default_ontology': False,
            'ontology_mode': 'discovery'}), 201)['id']
        base = f'/api/projects/{pid}'
        repo = app.state.service.repository
        frozen_versions = {}
        saved_facts = {}
        parent_a, parent_b, rating = map(URIRef, (
            'urn:acceptance:Entity', 'urn:acceptance:Business', 'urn:acceptance:rating'))
        merchant_iri = shop_iri = None

        def assert_preserved():
            for ontology_id, old_turtle in frozen_versions.items():
                old = checked(client.get(base + '/ontology', params={'ontology_id': ontology_id}))
                assert old['turtle'] == old_turtle
            records = repo.current_records(pid, kinds=['entity', 'relation', 'attribute'])
            assert saved_facts.keys() <= {row['id'] for row in records}
            for row in records:
                if row['id'] in saved_facts:
                    assert (row['version_id'], row['ontology_id']) == saved_facts[row['id']]
            saved_facts.update({row['id']: (row['version_id'], row['ontology_id']) for row in records})
            return records

        def freeze_current():
            current = checked(client.get(base + '/ontology'))
            frozen_versions[current['id']] = current['turtle']
            return current, Ontology(current['turtle']).graph

        for n in range(1, 5):
            document = checked(client.post(base + '/documents', json={
                'title': f'Batch {n}', 'text': f'batch={n} Merchant-{n} Rule-{n} employees {n*10+1} employees {n*10+2}',
                'extraction_mode': 'discovery', 'extract_attributes': True,
                'resolve_entities': False}), 201)
            assert document['discovery_candidates'] == 5
            assert len(assert_preserved()) == (n - 1) * 5
            path = base + '/ontology-discovery'
            inventory = checked(client.get(path + '/candidates'))
            candidates = inventory['items']
            assert len(candidates) == inventory['total'] == 5
            assert len({item['document_id'] for item in candidates}) == 1
            assert all(item.get('assertion_id') and not item['materialized'] for item in candidates)
            confirmed = {item['id']: item['suggested_mapping']['target_iri'] for item in candidates
                         if item['suggested_mapping'] is not None}
            body = {'candidate_ids': [item['id'] for item in candidates],
                    'expected_ontology_id': inventory['expected_ontology_id'],
                    'confirmed_mappings': confirmed, 'name': f'Round {n}',
                    'create_new_draft': n == 1}
            if n == 3:
                # A new subtype may reuse a published field only after its
                # parent edge is explicitly included in the chosen working draft.
                shop_iri = URIRef(f'urn:knowledge:ontology:{pid}:Shop')
                prepared = manual(client, base, inventory['expected_ontology_id'])
                prepared = edit(client, base, prepared, action='batch', commands=[
                    {'action': 'create_term', 'target_iri': str(shop_iri), 'kind': 'class'},
                    {'action': 'add_annotation', 'target_iri': str(shop_iri),
                     'predicate': str(RDFS.label), 'value': 'Shop', 'language': 'zh'},
                    {'action': 'add_parent', 'target_iri': str(shop_iri), 'value': str(merchant_iri)},
                ])
                body.update(target_draft_id=prepared['id'], expected_revision=prepared['revision'])
            before_preview = (len(repo.list_ontologies(pid)),
                              len(app.state.ontology_drafts.store.list(pid)),
                              len(repo.list_intake_runs(pid)))
            preview = checked(client.post(path + '/selection-preview', json=body))
            assert (len(repo.list_ontologies(pid)), len(app.state.ontology_drafts.store.list(pid)),
                    len(repo.list_intake_runs(pid))) == before_preview
            frozen_request = {**body, 'preview_fingerprint': preview['preview_fingerprint'],
                              'source_fingerprint': preview['source_fingerprint'],
                              'idempotency_key': f'round-{n}'}
            if n in (1, 3):
                assert preview['can_add'], preview
                result = checked(client.post(path + '/add-selection', json=frozen_request))
                draft = result['draft']
                if n == 1:
                    merchant_candidate = next(item['id'] for item in candidates
                                              if item['kind'] == 'entity' and item['proposed_type'] == 'Merchant')
                    merchant_iri = URIRef(next(item['target_iri'] for item in result['new']
                                              if item['candidate_id'] == merchant_candidate))
                else:
                    assert draft['id'] == prepared['id']
                    assert {op['id'] for op in prepared['operations']} <= {op['id'] for op in draft['operations']}
                publish(client, base, draft)
                retry_path = path + '/add-selection'
            else:
                assert len(confirmed) == 5
                assert preview['can_confirm_reuse'] and not preview['can_add'], preview
                count_before = len(repo.list_ontologies(pid))
                result = checked(client.post(path + '/confirm-reuse', json=frozen_request))
                assert result['receipt']['facts']['added'] == 5
                assert result['receipt']['facts']['skipped'] == 0
                assert len(repo.list_ontologies(pid)) == count_before
                retry_path = path + '/confirm-reuse'
            records = assert_preserved()
            assert len(records) == n * 5
            assert sum(row['kind'] == 'attribute' for row in records) == n * 2
            assert {str(row['value']) for row in records if row['kind'] == 'attribute'} == {
                str(batch * 10 + i) for batch in range(1, n + 1) for i in (1, 2)}
            entities = {row['id'] for row in records if row['kind'] == 'entity'}
            assert all(row['subject_id'] in entities for row in records if row['kind'] in ('relation', 'attribute'))
            assert all(row['object_id'] in entities for row in records if row['kind'] == 'relation')
            versions_before_retry = len(repo.list_ontologies(pid))
            retry = checked(client.post(retry_path, json=frozen_request))
            assert retry['receipt'] == result['receipt']
            assert len(repo.list_ontologies(pid)) == versions_before_retry
            assert len(assert_preserved()) == n * 5
            current, graph = freeze_current()
            if n == 1:
                # First manual release: a parent and a typed attribute.
                draft = manual(client, base, current['id'])
                for iri, kind in ((parent_a, 'class'), (rating, 'attribute')):
                    draft = edit(client, base, draft, action='create_term', target_iri=str(iri), kind=kind)
                draft = edit(client, base, draft, action='add_parent', target_iri=str(merchant_iri), value=str(parent_a))
                draft = edit(client, base, draft, action='add_domain', target_iri=str(rating), value=str(merchant_iri))
                draft = edit(client, base, draft, action='set_datatype', target_iri=str(rating), datatype=str(XSD.decimal))
                publish(client, base, draft)
                current, graph = freeze_current()
            elif n == 2:
                # Next release adds a second parent without replacing the first.
                draft = manual(client, base, current['id'])
                draft = edit(client, base, draft, action='create_term', target_iri=str(parent_b), kind='class')
                draft = edit(client, base, draft, action='add_parent', target_iri=str(merchant_iri), value=str(parent_b))
                draft = edit(client, base, draft, action='add_domain', target_iri=str(rating), value=str(parent_b))
                for target, value in ((merchant_iri, parent_b), (merchant_iri, merchant_iri),
                                      (parent_a, merchant_iri), (merchant_iri, URIRef('urn:acceptance:Missing'))):
                    previous_ids = {op['id'] for op in draft['operations']}
                    blocked = edit(client, base, draft, action='add_parent', target_iri=str(target), value=str(value))
                    operation = next(op for op in blocked['operations'] if op['id'] not in previous_ids)
                    assert operation['validation']['errors']
                    assert blocked['ontology'] == draft['ontology']
                    draft = edit(client, base, blocked, action='withdraw_operation', operation_id=operation['id'])
                publish(client, base, draft)
                current, graph = freeze_current()
            elif n == 3:
                # New discovered types must preserve the two existing parents and attribute.
                assert set(graph.objects(merchant_iri, RDFS.subClassOf)) == {parent_a, parent_b}
                shop = shop_iri
                draft = manual(client, base, current['id'])
                draft = edit(client, base, draft, action='add_parent', target_iri=str(shop), value=str(parent_b))
                publish(client, base, draft)
                current, graph = freeze_current()
                assert set(graph.objects(shop, RDFS.subClassOf)) == {merchant_iri, parent_b}
            else:
                # Removing one parent/domain must retain the other; datatype edits are versioned.
                draft = manual(client, base, current['id'])
                draft = edit(client, base, draft, action='remove_parent', target_iri=str(merchant_iri), value=str(parent_a))
                draft = edit(client, base, draft, action='remove_domain', target_iri=str(rating), value=str(parent_b))
                draft = edit(client, base, draft, action='set_datatype', target_iri=str(rating), datatype=str(XSD.string))
                publish(client, base, draft)
                current, graph = freeze_current()
            assert (rating, RDF.type, OWL.DatatypeProperty) in graph
            assert set(graph.objects(rating, RDFS.range)) == {XSD.string if n == 4 else XSD.decimal}
            expected_parents = {parent_b} if n == 4 else ({parent_a} if n == 1 else {parent_a, parent_b})
            assert set(graph.objects(merchant_iri, RDFS.subClassOf)) == expected_parents
            # Multiple allowed classes are encoded as owl:unionOf, so check
            # the resolved constraint set rather than its RDF storage shape.
            domains = set(Ontology(current['turtle']).constraint_types(rating, RDFS.domain))
            assert domains == ({merchant_iri, parent_b} if 2 <= n <= 3 else {merchant_iri})
            assert_preserved()

        overview = checked(client.get(base + '/ontology-discovery'))
        assert overview['candidate_status_counts']['materialized'] == 20
        assert overview['unpublished_candidate_count'] == 0
        assert len(repo.list_ontologies(pid)) == 6
        ledger = checked(client.post(base + '/records/page', json={
            'kinds': ['entity', 'relation', 'attribute'], 'limit': 1000}))['records']
        assert len([row for row in ledger if row['kind'] in ('entity', 'relation', 'attribute')]) == 20
        graph = checked(client.post(base + '/subgraph', json={}))
        visible = {row['id'] for row in graph['nodes'] + graph['edges']}
        assert {row['id'] for row in assert_preserved() if row['kind'] in ('entity', 'relation')} <= visible


def test_browser_repeated_parent_and_attribute_edits_use_real_api(tmp_path, monkeypatch):
    """Use production controllers and real isolated API, with no mocked edit responses."""
    import json
    from pathlib import Path
    from urllib.parse import urlsplit
    import pytest
    from knowledge_service.services import hierarchy_suggestion
    parent_discoveries = []
    def suggest(names, **kwargs):
        parent_discoveries.append(kwargs)
        return {}
    monkeypatch.setattr(hierarchy_suggestion, 'suggest_hierarchy', suggest)

    playwright = pytest.importorskip('playwright.sync_api')
    edge = Path(r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
    if not edge.exists():
        pytest.skip('Edge is required for browser acceptance')
    web = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'
    turtle = '''@prefix : <urn:acceptance:> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        :Child a owl:Class; rdfs:label "Child" .
        :ParentA a owl:Class; rdfs:label "Parent A" .
        :ParentB a owl:Class; rdfs:label "Parent B" .
        :rating a owl:DatatypeProperty; rdfs:label "Rating";
            rdfs:domain :Child; rdfs:range xsd:decimal .'''
    app = create_app(tmp_path / 'browser-edit-acceptance.sqlite', HashingEncoder())
    app.state.ontology_drafts.quality_engine = AcceptanceQualityEngine()
    with TestClient(app) as client, playwright.sync_playwright() as runtime:
        pid = checked(client.post('/api/projects', json={
            'name': 'Isolated browser editing', 'use_default_ontology': False}), 201)['id']
        base = f'/api/projects/{pid}'
        initial = seed_project_ontology(client, base + '', turtle)
        bootstrap = f'''let current={json.dumps(pid)};
            function endpoint(s){{return '/api/projects/'+current+s;}}
            let status=(m)=>{{window.lastNotice=m;}};
            async function api(url,body,method='POST'){{
              const r=await fetch(url,{{method,headers:{{'Content-Type':'application/json'}},
                body:body===undefined?undefined:JSON.stringify(body)}});
              const data=await r.json(); if(!r.ok)throw new Error(data.detail||r.status);return data;
            }}'''
        html = f'''<html><head><meta charset="utf-8">
            <link rel="stylesheet" href="/assets/ontology-model.css">
            <link rel="stylesheet" href="/assets/ontology-model-panel.css"></head><body>
            <section class="ontology-model" id="tab-ontology-model"></section><script>{bootstrap}</script>
            <script src="/assets/ontology-model.js"></script>
            <script src="/assets/ontology-model-panel.js"></script>
            <script>window.loaded=OntologyModel.refresh();</script></body></html>'''
        browser = runtime.chromium.launch(headless=True, executable_path=str(edge))
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors, command_requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def respond(route):
            request = route.request
            parts = urlsplit(request.url)
            if parts.path.startswith('/api/'):
                if parts.path.endswith('/commands'):
                    command_requests.append(request.post_data_json)
                response = client.request(request.method, parts.path + ('?' + parts.query if parts.query else ''),
                                          content=request.post_data, headers={'Content-Type': 'application/json'})
                route.fulfill(status=response.status_code, body=response.content,
                              content_type='application/json')
            elif parts.path.startswith('/assets/'):
                route.fulfill(body=(web / parts.path.removeprefix('/assets/')).read_bytes(),
                              content_type='text/css' if parts.path.endswith('.css') else 'text/javascript')
            else:
                route.fulfill(body=html, content_type='text/html')

        page.route('http://acceptance.test/**', respond)
        page.goto('http://acceptance.test/')
        page.evaluate('window.loaded')
        def select_definition(iri):
            directory = page.locator('.om-hierarchy-directory')
            if not directory.evaluate('(node) => node.open'):
                directory.locator(':scope > summary').click()
            page.locator('#om-side [data-sel="' + iri + '"]').click()

        select_definition('urn:acceptance:Child')
        for parent in ('ParentA', 'ParentB'):
            page.get_by_text('编辑继承（父类）', exact=True).click()
            row = page.locator('.om-constraint-edit .om-ce-row')
            row.locator('select').select_option('urn:acceptance:' + parent)
            row.get_by_role('button', name='添加', exact=True).click()
            page.wait_for_function('''parent => !OntologyModel.S.loading && OntologyModel.S.summary.classes
                .find(c=>c.id==='urn:acceptance:Child').parents.includes(parent)''',
                arg='urn:acceptance:' + parent)
        assert len(command_requests) == 2
        assert command_requests[1]['expected_revision'] > command_requests[0]['expected_revision']
        remaining = page.locator('.om-constraint-edit select').evaluate('(s)=>[...s.options].map(o=>o.value)')
        assert not {'urn:acceptance:Child', 'urn:acceptance:ParentA', 'urn:acceptance:ParentB'} & set(remaining)
        assert page.evaluate('OntologyModel.S.graph.edges.filter(e=>e.kind==="inherit").length') == 2
        draft_id = page.evaluate('OntologyModel.S.draft.id')
        def review_and_publish_ui(note):
            before = page.evaluate('OntologyModel.S.ontologyId')
            page.get_by_role('button', name='校验并审核', exact=True).click()
            page.wait_for_function('''() => !OntologyModel.S.loading && OntologyModel.S.draft?.submitted_at''')
            page.get_by_role('button', name='逐条审核', exact=True).click(timeout=3000)
            dialog = page.get_by_role('dialog', name='逐条审核')
            dialog.wait_for()
            for checkbox in dialog.locator('input[type=checkbox]').all():
                checkbox.check()
            for row in dialog.locator('.om-review-item').all():
                approve = row.get_by_role('button', name='收下', exact=True)
                if approve.is_enabled():
                    row.get_by_role('textbox', name='处理理由').fill('Synthetic change reviewed against source and schema')
                    approve.click()
                    row.get_by_text('已收下', exact=True).wait_for()
                    page.wait_for_function('''() => document.querySelector('.om-review-dialog')
                        .getAttribute('aria-busy')!=='true' ''')
            dialog.get_by_role('button', name='关闭逐条审核', exact=True).click()
            page.get_by_placeholder('必填：这一版改了什么').fill(note)
            page.get_by_role('button', name=__import__('re').compile('^发布为 v')).click()
            page.wait_for_function('''before => !OntologyModel.S.loading && OntologyModel.S.ontologyId!==before''', arg=before)
            return checked(client.get(base + '/ontology'))

        first_release = review_and_publish_ui('Browser: two parents added')
        # Edit again after publication: it must create a new draft, not mutate the accepted one.
        page.get_by_text('编辑继承（父类）', exact=True).click()
        page.locator('.om-ce-chip').filter(has_text='Parent A').get_by_role('button').click()
        page.wait_for_function('''() => !OntologyModel.S.loading && OntologyModel.S.summary.classes
            .find(c=>c.id==='urn:acceptance:Child').parents.length===1''')
        assert page.evaluate('OntologyModel.S.draft.id') != draft_id
        select_definition('urn:acceptance:rating')
        page.get_by_text('编辑所属类 / 数据类型', exact=True).click()
        domain = page.locator('.om-ce-row').filter(has_text='所属类 domain')
        domain.locator('select').select_option('urn:acceptance:ParentB')
        domain.get_by_role('button', name='添加', exact=True).click()
        page.wait_for_function('''() => !OntologyModel.S.loading && OntologyModel.S.summary.attributes
            .find(a=>a.id==='urn:acceptance:rating').domain.length===2''')
        page.get_by_text('编辑所属类 / 数据类型', exact=True).click()
        datatype = page.locator('.om-ce-row').filter(has_text='数据类型')
        datatype.locator('select').select_option(str(XSD.string))
        datatype.get_by_role('button', name='应用类型', exact=True).click()
        page.wait_for_function('''() => !OntologyModel.S.loading && OntologyModel.S.summary.attributes
            .find(a=>a.id==='urn:acceptance:rating').range.includes('http://www.w3.org/2001/XMLSchema#string')''')
        second_id = page.evaluate('OntologyModel.S.draft.id')
        review_and_publish_ui('Browser: parent removed and attribute updated')
        current = Ontology(checked(client.get(base + '/ontology'))['turtle']).graph
        assert set(current.objects(URIRef('urn:acceptance:Child'), RDFS.subClassOf)) == {URIRef('urn:acceptance:ParentB')}
        assert set(current.objects(URIRef('urn:acceptance:rating'), RDFS.range)) == {XSD.string}
        original = Ontology(checked(client.get(base + '/ontology', params={'ontology_id': initial['id']}))['turtle']).graph
        assert not list(original.objects(URIRef('urn:acceptance:Child'), RDFS.subClassOf))
        prior = Ontology(checked(client.get(base + '/ontology', params={'ontology_id': first_release['id']}))['turtle']).graph
        assert len(list(prior.objects(URIRef('urn:acceptance:Child'), RDFS.subClassOf))) == 2
        # The independent parent action remains available after publication,
        # even with no new discovery candidates; repeated runs do not publish.
        published_id = page.evaluate('OntologyModel.S.ontologyId')
        for _ in range(2):
            page.get_by_role('button', name='发现父类', exact=True).click()
            page.wait_for_function('''() => !OntologyModel.S.discoveringParents''')
        assert len(parent_discoveries) == 2
        assert all(call['refresh'] for call in parent_discoveries)
        assert page.evaluate('OntologyModel.S.ontologyId') == published_id
        assert not errors, errors
        browser.close()
