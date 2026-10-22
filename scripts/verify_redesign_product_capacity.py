"""Load the real authenticated product from PostgreSQL into WebGL at 150k scale."""
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import platform
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8102')
    parser.add_argument('--credentials', required=True)
    parser.add_argument('--stability-seconds', type=int, default=1800)
    parser.add_argument('--load-repetitions', type=int, default=5)
    parser.add_argument('--server-pid', type=int)
    parser.add_argument('--measurement-notes', default='No external workload declared.')
    parser.add_argument('--read-only-loads', action='store_true',
                        help='Only repeat complete graph loads; create no projects or business records')
    parser.add_argument('--expected-entities', type=int, default=150000)
    parser.add_argument('--expected-relations', type=int, default=300000)
    parser.add_argument('--output', type=Path,
                        default=ROOT/'docs/research/2026-10-19-product-capacity-validation.json')
    args = parser.parse_args()
    if args.load_repetitions < 1 or args.stability_seconds < 0:
        parser.error('load-repetitions must be positive; stability-seconds must be nonnegative')
    if args.read_only_loads and args.output == ROOT/'docs/research/2026-10-19-product-capacity-validation.json':
        parser.error('read-only measurements require a separate --output to preserve the stability report')
    import httpx
    from playwright.sync_api import sync_playwright
    client = httpx.Client(base_url=args.url, trust_env=False, timeout=300)
    response = client.post('/api/auth/login', json=json.loads(Path(args.credentials).read_text(encoding='utf-8')))
    response.raise_for_status()
    token = response.json()['access_token']
    client.headers['Authorization'] = 'Bearer ' + token

    def call(method, path, body=None):
        result = client.request(method, path, json=body)
        if result.is_error:
            raise RuntimeError(f'{method} {path}: {result.status_code} {result.text[:1000]}')
        return result.json()

    project = next(p['id'] for p in call('GET', '/api/projects')['projects'] if p['name'] == '容量验证 · 15 万实体')
    prefix = f'/api/projects/{project}'
    report = {'scope': 'real product HTTP → PostgreSQL scoped pages → worker → WebGL',
              'project_id': project, 'page_errors': [], 'loads': [], 'completed': False,
              'targets': {'complete_seconds': 30, 'frame_p95_ms': 33, 'selection_p95_ms': 200,
                          'js_heap_bytes': 1500*1024**2, 'browser_rss_bytes': 3*1024**3,
                          'stability_seconds': 1800},
              'started_at': datetime.now(timezone.utc).isoformat()}
    output = args.output
    def checkpoint():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    checkpoint()
    def percentile(values, q):
        return sorted(values)[min(len(values)-1, math.ceil(len(values)*q)-1)]

    # The ordinary HTTP path includes query encoding and request/service work.
    # Synthetic vectors are still scale evidence, not a relevance benchmark.
    report['http_retrieval'] = []
    def search(temperature, mode):
        started = time.monotonic()
        result = call('POST', prefix+'/search', {'query': '商户入驻',
            'retrieval_mode': mode, 'kinds': ['entity', 'relation'],
            'k_entities': 10, 'k_relations': 10, 'k_chunks': 0, 'k': 10})
        assert result['active_mode'] == mode and not result.get('degraded')
        assert result.get('_backend') == 'milvus'
        counts = {kind: sum(hit['kind']==kind for hit in result['hits']) for kind in ('entity','relation')}
        assert counts == {'entity': 10, 'relation': 10}, counts
        return {'temperature': temperature, 'mode': mode, 'seconds': round(time.monotonic()-started, 4),
                'channel_counts': counts, 'backend': result['_backend'], 'includes_query_encoding': True}
    report['http_retrieval'].append(search('first request after startup', 'semantic'))
    for mode in ('semantic', 'hybrid'):
        for _ in range(5):
            report['http_retrieval'].append(search('warm sequential', mode))
    with ThreadPoolExecutor(max_workers=4) as executor:
        report['http_retrieval'].extend(executor.map(lambda mode: search('concurrent 4', mode),
                                                   ['semantic','hybrid','semantic','hybrid']))
    report['http_warm_p95_seconds'] = {mode: percentile([r['seconds'] for r in report['http_retrieval']
        if r['temperature']=='warm sequential' and r['mode']==mode], .95) for mode in ('semantic','hybrid')}
    checkpoint()
    print('Real HTTP retrieval measured', json.dumps(report['http_warm_p95_seconds']), flush=True)

    # A second real project makes A→B→A switches exercise project-owned state.
    temporary = None if args.read_only_loads else call('POST', '/api/projects', {
        'name': '容量验收 · 临时空项目',
        'ontology_mode': 'discovery', 'use_default_ontology': False})['id']
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless=True)
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        page.add_init_script('localStorage.setItem("kg_token",' + json.dumps(token) + ');')
        page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
        import psutil
        report['environment'] = {'browser_version': browser.version,
            'viewport': {'width': 1500, 'height': 1000}, 'cpu': platform.processor(),
            'logical_cpus': psutil.cpu_count(), 'host_ram_bytes': psutil.virtual_memory().total,
            'measurement_notes': args.measurement_notes}
        cdp = browser.new_browser_cdp_session()
        gpu = cdp.send('SystemInfo.getInfo')['gpu']
        report['gpu'] = {'devices': gpu.get('devices'), 'feature_status': gpu.get('featureStatus'),
                         'memory_limit': 'Dedicated GPU allocation is not exposed by this browser API.'}
        def resources():
            rss = 0
            for process in cdp.send('SystemInfo.getProcessInfo')['processInfo']:
                try:
                    rss += psutil.Process(int(process['id'])).memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            result = {'browser_rss_bytes': rss,
                      'js_heap_bytes': page.evaluate('performance.memory?.usedJSHeapSize || null')}
            if args.server_pid:
                result['server_rss_bytes'] = psutil.Process(args.server_pid).memory_info().rss
            return result
        page.goto(args.url, wait_until='domcontentloaded')
        page.wait_for_function('window.Auth?.isSuperAdmin() && document.querySelector("#project").options.length > 1', timeout=60000)
        page.select_option('#project', project)
        page.locator('[data-tab="search"]').click()
        page.locator('[data-fg-mode="full"]').click()

        def load(label, expected_entities=150000, expected_relations=300000):
            started = time.monotonic()
            selected_project = page.input_value('#project')
            page.evaluate('''expected=>{const previous=FullGraphView.loader?.manifest?.id;
                const probe={started:performance.now(),first:null};window.capacityLoadProbe=probe;
                function tick(){const stats=FullGraphView.renderer?.stats();
                    if(window.capacityLoadProbe!==probe)return;
                    if(FullGraphView.loader?.manifest?.id!==previous && stats &&
                        ((stats.loaded_entities>0 && stats.drawn_entities===stats.loaded_entities) ||
                        (expected===0 && document.querySelector('.full-graph-progress').textContent.includes('全量加载完成')))){
                    probe.first=performance.now()-probe.started;
                }else requestAnimationFrame(tick);}requestAnimationFrame(tick);}''', expected_entities)
            page.locator('[data-fg="load"]').click()
            page.wait_for_function('window.capacityLoadProbe.first!==null', timeout=480000)
            first_visible = round(page.evaluate('capacityLoadProbe.first')/1000, 3) if expected_entities else None
            page.wait_for_function('window.FullGraphView?.renderer?.stats().loaded_entities === FullGraphView.loader.manifest.entity_count && document.querySelector(".full-graph-progress").textContent.includes("全量加载完成")', timeout=480000)
            page.wait_for_function('FullGraphView.renderer.stats().drawn_entities===FullGraphView.loader.manifest.entity_count && FullGraphView.renderer.stats().drawn_relations===FullGraphView.loader.manifest.relation_count')
            result = page.evaluate('''()=>{
                const view=FullGraphView, d=view.renderer.dataset, ids=new Set(d.records.map(r=>r.id));
                return {...view.renderer.stats(), project_id:view.loader.manifest.project_id, scope:view.loader.manifest.scope,
                    manifest_entities:view.loader.manifest.entity_count,
                    manifest_relations:view.loader.manifest.relation_count,unique_entities:ids.size,
                    unique_relations:new Set(d.edges.map(r=>r.id)).size,
                    dangling_edges:d.edges.filter(e=>!ids.has(e.subject_id)||!ids.has(e.object_id)).length,
                    gl_error:view.renderer.gl.getError(),heap_bytes:performance.memory?.usedJSHeapSize||null};
            }''')
            result.update(label=label, first_visible_seconds=first_visible,
                          complete_seconds=round(time.monotonic()-started, 3), resources=resources())
            assert result['loaded_entities'] == result['drawn_entities'] == result['unique_entities'] == result['manifest_entities']
            assert result['loaded_relations'] == result['drawn_relations'] == result['unique_relations'] == result['manifest_relations']
            assert result['project_id'] == selected_project
            assert result['manifest_entities'] == expected_entities
            assert result['manifest_relations'] == expected_relations
            assert result['dangling_edges'] == result['gl_error'] == 0
            report['loads'].append(result)
            checkpoint()
            print(label, json.dumps(result, ensure_ascii=False), flush=True)

        for index in range(args.load_repetitions):
            load('fixed corpus repetition '+str(index+1),
                 args.expected_entities, args.expected_relations)
        if args.read_only_loads:
            fixed = report['loads']
            report['read_only_loads'] = report['completed'] = True
            report['expected_counts'] = {'entities': args.expected_entities,
                                         'relations': args.expected_relations}
            report['load_distribution'] = {'samples': len(fixed),
                'first_visible_p50_seconds': percentile([r['first_visible_seconds'] for r in fixed], .5),
                'first_visible_p95_seconds': percentile([r['first_visible_seconds'] for r in fixed], .95),
                'complete_p50_seconds': percentile([r['complete_seconds'] for r in fixed], .5),
                'complete_p95_seconds': percentile([r['complete_seconds'] for r in fixed], .95)}
            report['measured_targets'] = {
                'load': report['load_distribution']['complete_p95_seconds'] <= report['targets']['complete_seconds'],
                'js_heap': all(r['resources']['js_heap_bytes'] is not None and
                    r['resources']['js_heap_bytes'] <= report['targets']['js_heap_bytes'] for r in fixed),
                'browser_rss': all(r['resources']['browser_rss_bytes'] <= report['targets']['browser_rss_bytes'] for r in fixed),
                'http_warm_retrieval': all(value <= 2 for value in report['http_warm_p95_seconds'].values())}
            report['scenario_targets_met'] = len(fixed) >= 5 and all(report['measured_targets'].values())
            report['complete_spec_coverage'] = False
            report['separately_measured_or_pending'] = [
                'This read-only run measures full product loads and HTTP retrieval only; interaction, stability, editing and stress results remain in the separate product report.']
            assert not report['page_errors'], report['page_errors']
            checkpoint()
            browser.close()
            print('Read-only product load measurement completed', flush=True)
            return
        def interaction(duration=10):
            page.locator('.fg-gl').scroll_into_view_if_needed()
            page.evaluate('''()=>{window.capacityFrames=[];window.capacitySampling=true;
                let last=performance.now();function tick(now){if(!window.capacitySampling)return;
                    capacityFrames.push(now-last);last=now;requestAnimationFrame(tick);}requestAnimationFrame(tick);}''')
            box = page.locator('.fg-gl').bounding_box()
            x, y = box['x']+box['width']/2, box['y']+box['height']/2
            page.mouse.move(x, y)
            page.mouse.down()
            started, steps = time.monotonic(), 0
            while time.monotonic()-started < duration:
                page.mouse.move(x+math.sin(steps/5)*35, y+math.cos(steps/7)*25)
                if steps % 5 == 0:
                    page.mouse.wheel(0, 8 if steps % 10 == 0 else -8)
                page.wait_for_timeout(50)
                steps += 1
            page.mouse.up()
            frames = page.evaluate('()=>{window.capacitySampling=false;return capacityFrames;}')
            return {'duration_seconds': round(time.monotonic()-started, 3), 'input_steps': steps,
                    'frame_samples': len(frames), 'frame_p95_ms': percentile(frames, .95),
                    'resources': resources()}
        report['interaction'] = interaction()
        checkpoint()
        print('Verified 10-second product pan/zoom', json.dumps(report['interaction']), flush=True)
        # Time actual pointer events through the shared record inspector. Keep
        # onSelect installed; an isolated pick microbenchmark is separate evidence.
        selections = []
        report['selection'] = {'samples_ms': selections}
        for index in range(20):
            identifier = 'e'+str((index*7919)%150000).zfill(6)
            point = page.evaluate('''id=>{const r=FullGraphView.renderer,i=r.dataset.nodeIndex.get(id);
                r.camera.x=r.dataset.positions[i*2];r.camera.y=r.dataset.positions[i*2+1];
                r.camera.scale=100;r.requestDraw();const b=r.canvas.getBoundingClientRect();
                return {x:b.x+b.width/2,y:b.y+b.height/2};}''', identifier)
            page.wait_for_timeout(25)
            started = time.monotonic()
            page.mouse.click(point['x'], point['y'])
            page.wait_for_function('id=>document.querySelector("#graph-detail")?.dataset.recordId===id', arg=identifier)
            selections.append(round((time.monotonic()-started)*1000, 3))
            checkpoint()
            print('Product selection feedback', index+1, selections[-1], 'ms', flush=True)
        report['selection'] = {'samples_ms': selections, 'p95_ms': percentile(selections, .95)}
        page.click('[data-fg="reset"]')
        checkpoint()
        # Locate the very last corpus entity, rather than a preloaded prefix.
        page.fill('[data-fg="query"]', 'e149999')
        page.click('[data-fg="locate"]')
        page.wait_for_function('document.querySelector("#graph-detail")?.dataset.recordId === "e149999"', timeout=30000)
        report['last_entity_inspector'] = True
        page.screenshot(path=str(ROOT/'docs/research/2026-10-19-product-full-graph.png'), full_page=True)
        report['stability'] = {'requested_seconds': args.stability_seconds, 'samples': [], 'switches': []}
        stable_started = time.monotonic()
        next_switch = stable_started+60
        while time.monotonic()-stable_started < args.stability_seconds:
            sample = interaction()
            sample['elapsed_seconds'] = round(time.monotonic()-stable_started, 3)
            sample['stats'] = page.evaluate('FullGraphView.renderer.stats()')
            assert sample['stats']['loaded_entities'] == 150000
            assert not sample['stats']['context_lost']
            report['stability']['samples'].append(sample)
            if time.monotonic() >= next_switch:
                switch = len(report['stability']['switches'])
                if switch % 3 == 0:
                    page.click('[data-fg-mode="local"]')
                    page.click('[data-fg-mode="full"]')
                    kind = 'local/full'
                elif switch % 3 == 1:
                    page.select_option('#project', temporary)
                    page.wait_for_function('FullGraphView.renderer.stats().loaded_entities===0')
                    load('stability empty project B', 0, 0)
                    page.select_option('#project', project)
                    kind = 'project A/B/A'
                else:
                    page.evaluate('wb.setVersionContext({ontologyScope:"unknown",source:"capacity-stability"})')
                    page.wait_for_function('FullGraphView.renderer.stats().loaded_entities===0')
                    load('stability unknown-ontology scope B')
                    assert report['loads'][-1]['scope']['ontology_scope'] == 'unknown'
                    page.evaluate('wb.setVersionContext({ontologyScope:"all",source:"capacity-stability"})')
                    kind = 'ontology scope A/B/A'
                load('stability '+kind)
                report['stability']['switches'].append(kind)
                next_switch = time.monotonic()+60
                print('Product stability seconds', round(time.monotonic()-stable_started), flush=True)
            checkpoint()
        report['stability']['elapsed_seconds'] = round(time.monotonic()-stable_started, 3)
        # Return to the fixed scope before checking cancellation and writes.
        page.evaluate('wb.setVersionContext({ontologyScope:"all",source:"capacity-validation"})')
        load('fixed corpus after stability')
        # A new graph request is actually aborted through the visible control.
        page.click('[data-fg="load"]')
        page.click('[data-fg="cancel"]')
        assert '不完整' in page.locator('.full-graph-progress').inner_text()
        report['cancel_is_explicitly_incomplete'] = True
        checkpoint()

        # Exercise ordinary governed writes in the 450k-row project. The synthetic
        # capacity fixture itself is not represented as a governed extraction run.
        history = call('GET', prefix+'/ontologies')['versions']
        from ontology_validation_helpers import publish_validation_ontology
        publication = history[-1] if history else publish_validation_ontology(lambda method,path,body: call(method,path,body), prefix,
            '@prefix ex: <https://capacity.example/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . '
            '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> . ex:CapacityThing a owl:Class ; rdfs:label "容量验收实体"@zh .')
        started = time.monotonic()
        created = call('POST', prefix+'/records', {'records':[{'kind':'entity','text':'真实大库编辑验收实体',
            'type':'https://capacity.example/CapacityThing','ontology_id':publication['id'],'metadata':{}}]})['records'][0]
        report['governed_create_seconds'] = round(time.monotonic()-started, 3)
        page.evaluate('(row)=>window.openKnowledgeRecord({id:row.id,kind:"entity"})', created)
        page.locator('#graph-detail [data-action="edit"]').click()
        page.wait_for_selector('#structured-record-text')
        page.fill('#structured-record-text', '真实大库编辑验收实体 · 已修订')
        started = time.monotonic()
        with page.expect_response(lambda r:r.request.method=='PUT' and r.url.endswith('/records/'+created['id']), timeout=300000) as saved:
            page.click('#save-structured-record')
        assert saved.value.status == 200, saved.value.status
        edited = saved.value.json()
        assert edited['version'] == created['version']+1
        report['browser_edit_seconds'] = round(time.monotonic()-started, 3)
        page.locator('#record-dialog-message').filter(has_text='已保存').wait_for(timeout=30000)
        page.click('#close-record-dialog')
        stale = client.post(prefix+'/graph-sessions/'+page.evaluate('FullGraphView.loader.manifest.id')+'/page', json={'kind':'entities','limit':1})
        assert stale.status_code == 409
        report['write_invalidates_old_manifest'] = stale.status_code
        revisions = call('GET', prefix+'/records/'+created['id']+'/history')['versions']
        assert len(revisions) == 2
        report['large_corpus_editor_revisions'] = len(revisions)
        checkpoint()
        # This is a fresh full enumeration following the new record and revision.
        load('reload after governed edit', 150001, 300000)
        page.fill('[data-fg="query"]', created['id'])
        page.click('[data-fg="locate"]')
        page.wait_for_function('(id)=>document.querySelector("#graph-detail")?.dataset.recordId===id',arg=created['id'],timeout=30000)
        page.evaluate('window.wb.setVersionContext({ontologyScope:"unknown",source:"capacity-validation"})')
        page.wait_for_function('FullGraphView.renderer.stats().loaded_entities===0')
        report['ontology_scope_change_clears_old_graph'] = True
        assert not report['page_errors'], report['page_errors']
        browser.close()
    report['completed'] = True
    fixed = report['loads'][:args.load_repetitions]
    memory = [row['resources'] for row in report['loads']]
    memory += [row['resources'] for row in report['stability']['samples']]
    report['load_distribution'] = {'samples': len(fixed),
        'first_visible_p50_seconds': percentile([r['first_visible_seconds'] for r in fixed], .5),
        'first_visible_p95_seconds': percentile([r['first_visible_seconds'] for r in fixed], .95),
        'complete_p50_seconds': percentile([r['complete_seconds'] for r in fixed], .5),
        'complete_p95_seconds': percentile([r['complete_seconds'] for r in fixed], .95)}
    report['measured_targets'] = {
        'load': report['load_distribution']['complete_p95_seconds'] <= report['targets']['complete_seconds'],
        'interaction': report['interaction']['frame_p95_ms'] <= report['targets']['frame_p95_ms'],
        'stability_responsiveness': all(row['frame_p95_ms'] <= report['targets']['frame_p95_ms'] for row in report['stability']['samples']),
        'selection': report['selection']['p95_ms'] <= report['targets']['selection_p95_ms'],
        'http_warm_retrieval': all(value <= 2 for value in report['http_warm_p95_seconds'].values()),
        'js_heap': all(r['js_heap_bytes'] is not None and r['js_heap_bytes'] <= report['targets']['js_heap_bytes'] for r in memory),
        'browser_rss': all(r['browser_rss_bytes'] <= report['targets']['browser_rss_bytes'] for r in memory),
        'stability_duration': report['stability']['elapsed_seconds'] >= report['targets']['stability_seconds']}
    report['scenario_targets_met'] = args.load_repetitions >= 5 and all(report['measured_targets'].values())
    report['complete_spec_coverage'] = False
    report['separately_measured_or_pending'] = [
        'The 1k/10k/50k gradients and 1m-edge case were measured by the renderer verifier, not this product run.',
        'Real HTTP timings include query encoding, first-request and concurrency; the synthetic large corpus has no document chunks. Real uploaded-document retrieval is validated separately on the small governed corpus.',
        '403/409, governance and projection consistency are covered by focused regressions, not inferred from the positive editor flow.'
    ]
    call('DELETE', '/api/projects/'+temporary)
    checkpoint()
    print('Real product capacity measurements completed; targets: '+json.dumps(report['measured_targets']), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        from knowledge_service.utils.diagnostics import redact
        print(redact(exc),file=sys.stderr)
        raise SystemExit(1)
