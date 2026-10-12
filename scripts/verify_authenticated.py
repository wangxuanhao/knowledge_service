"""Run a business verification script against an authenticated local service."""
import json
import os
from pathlib import Path
import runpy
import sys
from uuid import uuid4
import urllib.parse
import urllib.request


def main():
    script = Path(sys.argv[1]).resolve()
    args = sys.argv[2:]
    base = args[args.index('--url') + 1].rstrip('/')
    origin = urllib.parse.urlsplit(base)
    original_open = urllib.request.urlopen
    login = urllib.request.Request(base + '/api/auth/login',
        data=json.dumps({'username': os.environ.get('KG_SEED_ADMIN_USERNAME', 'admin'),
                         'password': os.environ.get('KG_SEED_ADMIN_PASSWORD', 'admin123')}).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    with original_open(login, timeout=15) as response:
        token = json.load(response)['access_token']

    def authenticated_open(request, *positional, **keywords):
        if isinstance(request, str):
            request = urllib.request.Request(request)
        target = urllib.parse.urlsplit(request.full_url)
        if ((target.scheme, target.netloc) == (origin.scheme, origin.netloc)
                and target.path.startswith('/api/')
                and not request.has_header('Authorization')):
            request.add_header('Authorization', 'Bearer ' + token)
        return original_open(request, *positional, **keywords)

    urllib.request.urlopen = authenticated_open
    from playwright.sync_api import Browser, BrowserType
    init_script = ('if (location.origin === ' + json.dumps(base) + ') '
                   '{ localStorage.setItem("kg_token", ' + json.dumps(token) + '); }')
    original_context = Browser.new_context
    original_page = Browser.new_page
    original_persistent = BrowserType.launch_persistent_context

    def new_context(browser, *positional, **keywords):
        context = original_context(browser, *positional, **keywords)
        context.add_init_script(init_script)
        return context

    def new_page(browser, *positional, **keywords):
        page = original_page(browser, *positional, **keywords)
        page.add_init_script(init_script)
        return page

    Browser.new_context = new_context
    Browser.new_page = new_page

    def persistent(browser_type, *positional, **keywords):
        context = original_persistent(browser_type, *positional, **keywords)
        context.add_init_script(init_script)
        return context

    BrowserType.launch_persistent_context = persistent
    fixture_id = None

    def call(path, body=None, method=None):
        request = urllib.request.Request(base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'}, method=method)
        with authenticated_open(request, timeout=180) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    try:
        if script.name in {'verify_knowledge_ledger.py','verify_ontology_retire.py','verify_ontology_gate.py'} and '--project' not in args:
            project = call('/api/projects', {'name': '验证临时项目-' + uuid4().hex[:8]})
            fixture_id = project['id']
            root = '/api/projects/' + fixture_id
            ontology = call(root + '/ontology')
            doc = call(root + '/documents', {'title': '验证原文',
                'text': '张三与李四使用测试系统。这是验证用的原文。', 'extract': False})
            term = ontology['summary']['classes'][0]['id']
            call(root + '/records', {'records': [
                {'id': 'fixture-a', 'kind': 'entity', 'type': term, 'text': '张三',
                 'source_id': doc['document']['id']},
                {'id': 'fixture-b', 'kind': 'entity', 'type': term, 'text': '李四',
                 'source_id': doc['document']['id']}]})
            if script.name == 'verify_ontology_gate.py':
                closed = call(root + '/ontology-drafts', {
                    'base_ontology_id': ontology['id'], 'title': '终态验证草案'})
                path = root + '/ontology-drafts/' + closed['id']
                closed = call(path + '/commands', {'expected_revision': closed['revision'],
                    'command': {'action': 'create_term', 'kind': 'class',
                                'target_iri': 'urn:verification:Gate'}})
                call(path + '/close', {'expected_revision': closed['revision'], 'reason': '验证终态守卫'})
                call(root + '/ontology-drafts', {
                    'base_ontology_id': ontology['id'], 'title': '待处理验证草案'})
            args.extend(['--project', fixture_id])
        sys.argv = [str(script), *args]
        sys.path.insert(0, str(script.parent))
        runpy.run_path(str(script), run_name='__main__')
    finally:
        if fixture_id:
            call('/api/projects/' + fixture_id, method='DELETE')


if __name__ == '__main__':
    main()
