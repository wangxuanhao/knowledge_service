"""Real authenticated CRUD, Milvus retrieval and five-variant experiment acceptance."""
from argparse import ArgumentParser
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8101')
    parser.add_argument('--credentials', required=True)
    args = parser.parse_args()
    import httpx
    from playwright.sync_api import sync_playwright
    client = httpx.Client(base_url=args.url, trust_env=False, timeout=240)
    print('Authenticating live workflow client', flush=True)
    response = client.post('/api/auth/login', json=json.loads(Path(args.credentials).read_text(encoding='utf-8')))
    response.raise_for_status()
    token = response.json()['access_token']
    client.headers['Authorization'] = 'Bearer ' + token

    def call(method, path, body=None):
        result = client.request(method, path, json=body)
        if result.is_error:
            raise RuntimeError(f'{method} {path}: {result.status_code} {result.text[:1000]}')
        return result.json()

    project = next(p['id'] for p in call('GET', '/api/projects')['projects'] if p['name'] == '商户入驻 · 改造验收')
    prefix = f'/api/projects/{project}'
    report = {'project_id': project, 'crud': [], 'retrieval': [], 'page_errors': []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless=True)
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        facet_requests = []
        page.on('request', lambda request: facet_requests.append(request.url) if request.url.endswith('/metadata/facets') else None)
        page.add_init_script('localStorage.setItem("kg_token",' + json.dumps(token) + ');')
        page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
        page.goto(args.url, wait_until='domcontentloaded')
        page.wait_for_function('window.Auth?.isSuperAdmin() && document.querySelector("#project").options.length > 1', timeout=60000)
        page.select_option('#project', project)
        page.locator('[data-tab="projects"]').click()
        page.wait_for_selector('.project-card')
        assert not page.locator('#scope').is_visible()
        assert not facet_requests, 'Closed filters scanned project metadata'
        page.locator('[data-tab="search"]').click()
        with page.expect_response(lambda r:r.url.endswith('/metadata/facets')) as facets:
            page.click('#scope-toggle')
        assert facets.value.ok and facets.value.json()['fields']
        page.click('#scope-toggle')
        assert not page.locator('#cb-details').evaluate('(node)=>node.open')
        report['metadata'] = {'management_has_no_filter_rail': True, 'closed_rail_scan_requests': 0,
                              'opening_rail_loads_real_fields': True, 'toggle_closes_rail': True}
        for kind, title, term, endpoints in [
            ('relation', '关系', 'onboards', [('起点实体', 'merchant-a'), ('终点实体', 'platform-a')]),
            ('attribute', '属性', 'status', [('所属实体', 'merchant-a')]),
        ]:
            page.locator('[data-tab="records"]').click()
            page.get_by_role('button', name='新增' + title, exact=True).click()
            page.wait_for_selector('#create-record-type option', state='attached')
            page.select_option('#create-record-type', 'https://redesign.example/' + term)
            page.fill('#create-record-text', '浏览器验收' + title)
            for label, identifier in endpoints:
                picker = page.locator('.record-entity-picker').filter(has=page.get_by_role('textbox', name=label + '准确 ID', exact=True))
                picker.get_by_role('textbox', name=label + '准确 ID', exact=True).fill(identifier)
                picker.get_by_role('button', name='按 ID 定位', exact=True).click()
                picker.locator('.record-entity-selected').filter(has_text=identifier).wait_for()
            if kind == 'attribute':
                page.fill('#create-record-value', '联调已核对')
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/records'), timeout=240000) as saved:
                page.click('#save-created-record')
            assert saved.value.status == 201, saved.value.status
            row = saved.value.json()['records'][0]
            before_versions = call('GET', prefix + '/records/' + row['id'] + '/history')['versions']
            assert before_versions[-1]['version'] == row['version']
            page.locator('#record-create-dialog').get_by_role('button', name='关闭 ×', exact=True).click()
            page.evaluate('(row)=>window.openKnowledgeRecord({id:row.id,kind:row.kind})', row)
            page.locator('#graph-detail [data-action="edit"]').click()
            page.wait_for_selector('#structured-record-text')
            page.fill('#structured-record-text', '浏览器验收' + title + ' · 新修订')
            with page.expect_response(lambda r: r.request.method == 'PUT' and r.url.endswith('/records/' + row['id']), timeout=240000) as edited:
                page.click('#save-structured-record')
            assert edited.value.status == 200, edited.value.status
            assert edited.value.json()['version'] == row['version'] + 1
            page.locator('#record-dialog-message').filter(has_text='已保存').wait_for(timeout=30000)
            page.click('#close-record-dialog')
            versions = call('GET', prefix + '/records/' + row['id'] + '/history')['versions']
            # Repeated validation may reuse an existing canonical relation or
            # attribute. A save must append exactly one immutable revision.
            assert len(versions) == len(before_versions) + 1
            assert [v['version_id'] for v in versions[:-1]] == [v['version_id'] for v in before_versions]
            assert versions[-1]['version'] == row['version'] + 1
            report['crud'].append({'kind': kind, 'created': 201, 'edited': 200,
                'revision_before_edit': row['version'], 'revision_after_edit': versions[-1]['version'],
                'history_before_edit': len(before_versions), 'history_revisions': len(versions)})
            print('Verified real CRUD', kind, flush=True)

        # Index repair is an explicit job, followed by actual Milvus requests.
        job = call('POST', prefix + '/indexes/rebuild', {})
        deadline = time.monotonic() + 300
        report['index_retries'] = 0
        while time.monotonic() < deadline:
            state = call('GET', '/api/jobs/' + job['id'])
            if state['status'] == 'failed' and 'already leased' in str(state.get('error', '')):
                time.sleep(2)
                call('POST', '/api/jobs/'+job['id']+'/retry', {})
                report['index_retries'] += 1
                continue
            if state['status'] in {'completed', 'failed', 'cancelled'}:
                break
            time.sleep(1)
        assert state['status'] == 'completed', state.get('error')
        for mode in ('keyword', 'semantic', 'hybrid'):
            result = call('POST', prefix + '/search', {'query': '青禾商户', 'retrieval_mode': mode, 'k': 5})
            assert result['hits'], mode
            assert result['active_mode'] == mode and not result['degraded'], result.get('active_mode')
            if mode in {'semantic', 'hybrid'}:
                assert result['_backend'] == 'milvus', result['_backend']
            report['retrieval'].append({'requested_mode': mode, 'active_mode': result.get('active_mode'),
                'backend': result.get('_backend'), 'hits': len(result['hits'])})
            print('Verified retrieval', mode, flush=True)

        page.locator('[data-tab="quality"]').click()
        page.locator('[data-rex-tab="retrieval"]').click()
        page.locator('#retrieval-experiments [name="name"]').fill('真实五变体联调验收')
        page.locator('#retrieval-experiments [name="questions"]').fill('青禾商户的入驻状态是什么？')
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/retrieval-experiments'), timeout=30000) as created:
            page.locator('#retrieval-experiments button[type="submit"]').click()
        assert created.value.ok
        run_id = created.value.json()['id']
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            run = call('GET', prefix + '/retrieval-experiments/' + run_id)
            if run['status'] in {'completed', 'failed', 'cancelled', 'invalidated'}:
                break
            time.sleep(1)
        assert run['status'] == 'completed', run.get('error')
        assert len(run['results']) == 5 and all(r['status'] == 'completed' for r in run['results'])
        page.locator('.rex-detail [data-action="export"]').wait_for(timeout=15000)
        with page.expect_download() as downloaded:
            page.locator('.rex-detail [data-action="export"]').click()
        exported = json.loads(Path(downloaded.value.path()).read_text(encoding='utf-8'))
        assert len(exported['results']) == 5
        report['experiment'] = {'status': run['status'], 'variants': len(run['results']),
            'exported_results': len(exported['results']), 'omitted_stages': {r['variant']: r['omitted_stages'] for r in run['results']}}
        assert not report['page_errors'], report['page_errors']
        browser.close()
    (ROOT / 'docs/research/2026-10-19-live-workflows-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Real workflow acceptance passed', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        from knowledge_service.utils.diagnostics import redact
        import traceback
        print(type(exc).__name__ + ': ' + redact(exc), file=sys.stderr)
        print(redact(traceback.format_exc()), file=sys.stderr)
        raise SystemExit(1)
