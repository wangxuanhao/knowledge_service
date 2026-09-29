"""Real-browser contract for the knowledge-write chunk preview drawer."""
import json
import re
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.ontology import Ontology


def _browser_path():
    candidates = [
        Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),
        Path(r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'),
        Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
        Path('/usr/bin/google-chrome'),
        Path('/usr/bin/chromium'),
    ]
    return next((str(path) for path in candidates if path.exists()), None)


def _missing_browser_runtime(error):
    message = str(error)
    return any(marker in message for marker in (
        "Executable doesn't exist",
        'Host system is missing dependencies',
        'error while loading shared libraries',
    ))


def _project(repository, name):
    project_id = repository.create_project(name, {'ontology_mode': 'ontology'})['id']
    turtle = (Path(__file__).resolve().parents[2] / 'knowledge_service' /
              'resources' / 'default_ontology.ttl').read_text(encoding='utf-8')
    repository.save_ontology(project_id, turtle, Ontology(turtle).summary())
    return project_id


@pytest.fixture(scope='module')
def browser():
    playwright = pytest.importorskip('playwright.sync_api')
    runtime = playwright.sync_playwright().start()
    instance = None
    try:
        try:
            instance = runtime.chromium.launch(headless=True)
        except playwright.Error as managed_error:
            if not _missing_browser_runtime(managed_error):
                raise
            executable = _browser_path()
            if not executable:
                pytest.skip(
                    'Playwright Chromium is not installed and no Edge or Chrome fallback was found')
            try:
                instance = runtime.chromium.launch(
                    headless=True, executable_path=executable)
            except playwright.Error as fallback_error:
                if _missing_browser_runtime(fallback_error):
                    pytest.skip(f'Chromium runtime dependency is unavailable: {fallback_error}')
                raise
        yield instance
    finally:
        try:
            if instance is not None:
                instance.close()
        finally:
            runtime.stop()


@pytest.fixture
def workbench(browser, tmp_path):
    app = create_app(tmp_path / 'preview-drawer.sqlite', HashingEncoder())
    repository = app.state.service.repository
    project_id = _project(repository, '切片预览抽屉')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(
        app, host='127.0.0.1', port=port, log_level='critical'))
    thread = threading.Thread(target=server.run, daemon=True)
    context = page = None
    thread.start()
    try:
        for _ in range(100):
            if server.started or not thread.is_alive():
                break
            time.sleep(.05)
        if not server.started:
            pytest.fail('The ephemeral preview-drawer server did not start')

        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(5000)
        page.goto(f'http://127.0.0.1:{port}/', wait_until='load')
        page.wait_for_function("() => document.querySelectorAll('#project option').length > 1")
        with page.expect_response(lambda response: response.url.endswith('/entity-options')):
            page.select_option('#project', project_id)
        page.locator('.nav-group:has([data-tab="ingest"]) summary').click()
        page.click('[data-tab="ingest"]')
        page.locator('#tab-ingest').wait_for(state='visible')
        yield SimpleNamespace(page=page, project=project_id)
    finally:
        try:
            if page is not None:
                page.close()
        finally:
            try:
                if context is not None:
                    context.close()
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                assert not thread.is_alive(), 'The ephemeral preview-drawer server did not stop'


def _preview_result(*texts):
    offset = 0
    chunks = []
    for text in texts:
        chunks.append({'text': text, 'start_char': offset, 'end_char': offset + len(text)})
        offset += len(text)
    return {
        'total': len(chunks),
        'chunks': chunks,
        'preview_limit': 20,
        'strategy': 'fixed',
        'chunk_size': 1800,
        'chunk_overlap': 200,
    }


def _route_json(page, pattern, payloads, status=200):
    calls = []
    responses = list(payloads) if isinstance(payloads, (list, tuple)) else [payloads]

    def handle(route):
        calls.append(route.request)
        payload = responses[min(len(calls) - 1, len(responses) - 1)]
        route.fulfill(status=status, content_type='application/json',
                      body=json.dumps(payload, ensure_ascii=False))

    page.route(pattern, handle)
    return calls


def _choose_text(page, title='预览规则', text='待切片的正文'):
    page.locator(
        'label.document-mode-option:has(input[name="document-input-mode"][value="text"])'
    ).click()
    page.fill('#doc-title', title)
    page.fill('#doc-text', text)


def _request_preview(page, path_suffix='/documents/preview'):
    with page.expect_response(lambda response: response.url.endswith(path_suffix)):
        page.click('#preview-chunks')


def _dialog_accessibility(page):
    return page.locator('#chunk-preview .chunk-preview-drawer').evaluate(r"""dialog => {
      const referencedText = attribute => (dialog.getAttribute(attribute) || '')
        .split(/\s+/).filter(Boolean)
        .map(id => document.getElementById(id)?.textContent?.trim() || '').join(' ').trim();
      return {
        role: dialog.getAttribute('role'),
        modal: dialog.getAttribute('aria-modal'),
        name: dialog.getAttribute('aria-label')?.trim() || referencedText('aria-labelledby'),
        description: dialog.getAttribute('aria-description')?.trim()
          || referencedText('aria-describedby'),
      };
    }""")


def _assert_background_is_inert(page, expected):
    assert page.locator('body > aside').evaluate('(element) => element.inert') is expected
    assert page.locator('body > main').evaluate('(element) => element.inert') is expected


def test_preview_host_is_a_body_level_dialog_and_record_import_is_collapsed(workbench):
    page = workbench.page
    drawer = page.locator('body > #chunk-preview')

    assert drawer.count() == 1
    assert drawer.locator('.chunk-preview-drawer').count() == 1
    accessibility = _dialog_accessibility(page)
    assert accessibility['role'] == 'dialog'
    assert accessibility['modal'] == 'true'
    assert accessibility['name']
    assert accessibility['description']
    structured_import = page.locator('details:has(#batch):has(#write-batch)')
    assert structured_import.count() == 1
    assert structured_import.evaluate('(details) => details.open') is False
    assert '结构化记录导入（高级）' in structured_import.locator('summary').inner_text()
    assert '不是批量上传文档' in structured_import.text_content()


def test_text_preview_opens_safe_switchable_drawer_without_refetching(workbench):
    page = workbench.page
    first = '<img src=x onerror="window.previewPwned=true">第一段原文'
    second = '第二段原文'
    calls = _route_json(page, '**/documents/preview', _preview_result(first, second))
    _choose_text(page)

    _request_preview(page)

    drawer = page.locator('#chunk-preview')
    drawer.wait_for(state='visible')
    _assert_background_is_inert(page, True)
    close = drawer.get_by_role('button', name=re.compile('关闭'))
    assert close.count() == 1
    assert close.evaluate('(button) => button === document.activeElement')

    chunk_buttons = drawer.get_by_role('button', name=re.compile(r'^片段\s*[12]'))
    assert chunk_buttons.count() == 2
    assert chunk_buttons.evaluate_all(
        "buttons => buttons.every(button => button.tagName === 'BUTTON')")
    assert drawer.locator('button[aria-current="true"]').count() == 1
    assert first in drawer.inner_text()
    assert drawer.locator('img').count() == 0
    assert page.evaluate('window.previewPwned') is None

    chunk_buttons.nth(1).click()
    assert second in drawer.inner_text()
    assert first not in drawer.inner_text()
    assert len(calls) == 1


def test_repeated_preview_replaces_content_and_both_dismiss_paths_restore_page(workbench):
    page = workbench.page
    calls = _route_json(page, '**/documents/preview', [
        _preview_result('旧片段一', '旧片段二'),
        _preview_result('唯一的新片段'),
    ])
    _choose_text(page)

    _request_preview(page)
    drawer = page.locator('#chunk-preview')
    drawer.wait_for(state='visible')
    assert page.evaluate("document.documentElement.classList.contains('chunk-preview-open')")
    page.keyboard.press('Escape')
    drawer.wait_for(state='hidden')
    _assert_background_is_inert(page, False)
    assert not page.evaluate("document.documentElement.classList.contains('chunk-preview-open')")
    assert page.evaluate("document.activeElement?.id") == 'preview-chunks'

    _request_preview(page)
    drawer.wait_for(state='visible')
    assert len(calls) == 2
    assert drawer.get_by_role('button', name=re.compile(r'^片段\s*1')).count() == 1
    assert drawer.get_by_role('button', name=re.compile(r'^片段\s*2')).count() == 0
    assert '唯一的新片段' in drawer.inner_text()
    assert '旧片段' not in drawer.inner_text()

    drawer.click(position={'x': 2, 'y': 2})
    drawer.wait_for(state='hidden')
    _assert_background_is_inert(page, False)
    assert not page.evaluate("document.documentElement.classList.contains('chunk-preview-open')")
    assert page.evaluate("document.activeElement?.id") == 'preview-chunks'


def test_empty_and_failed_previews_have_distinct_recoverable_states(workbench):
    page = workbench.page
    _route_json(page, '**/documents/preview', _preview_result())
    _choose_text(page)

    _request_preview(page)
    drawer = page.locator('#chunk-preview')
    drawer.wait_for(state='visible')
    assert drawer.get_by_role('button', name=re.compile(r'^片段\s*\d+')).count() == 0
    assert re.search(r'没有可预览|暂无切片|未生成切片', drawer.inner_text())
    page.keyboard.press('Escape')
    drawer.wait_for(state='hidden')

    page.unroute('**/documents/preview')
    _route_json(page, '**/documents/preview', {'detail': '预览暂时不可用'}, status=503)
    _request_preview(page)

    drawer.wait_for(state='hidden')
    page.wait_for_function("() => !document.getElementById('preview-chunks').disabled")
    assert '预览暂时不可用' in page.locator('#status').inner_text()
    assert page.locator('#preview-chunks').is_enabled()
    _assert_background_is_inert(page, False)


@pytest.mark.parametrize('width', [850, 520])
def test_preview_traps_focus_and_stays_inside_narrow_viewports(workbench, width):
    page = workbench.page
    page.set_viewport_size({'width': width, 'height': 700})
    _route_json(page, '**/documents/preview', _preview_result('第一段' * 800, '第二段'))
    _choose_text(page)
    _request_preview(page)

    drawer = page.locator('#chunk-preview')
    drawer.wait_for(state='visible')
    page.wait_for_function("""() => getComputedStyle(
        document.querySelector('#chunk-preview .chunk-preview-drawer')).transform === 'none'""")
    close = drawer.get_by_role('button', name=re.compile('关闭'))
    for key in ('Shift+Tab', 'Tab', 'Tab', 'Tab', 'Tab', 'Tab'):
        page.keyboard.press(key)
        assert page.evaluate(
            "document.activeElement?.closest('#chunk-preview') !== null")

    responsive_regions = [
        drawer.locator('.chunk-preview-drawer'),
        drawer.locator('.chunk-preview-list'),
        drawer.locator('.chunk-preview-content'),
    ]
    for region in responsive_regions:
        assert region.count() == 1
        assert region.evaluate('(element) => element.scrollWidth <= element.clientWidth + 1')
    for box in [*(region.bounding_box() for region in responsive_regions), close.bounding_box()]:
        assert box is not None
        assert box['x'] >= 0
        assert box['x'] + box['width'] <= width + 1
    close_box = close.bounding_box()
    assert close_box['width'] >= 44
    assert close_box['height'] >= 44
    preview_text = drawer.locator('.chunk-preview-content pre')
    assert preview_text.evaluate('(element) => element.scrollHeight <= element.clientHeight + 1')
    assert page.evaluate('document.documentElement.clientWidth === window.innerWidth')
    assert page.evaluate(
        'document.documentElement.scrollWidth <= window.innerWidth')


def test_file_preview_uses_upload_endpoint_and_opens_the_same_drawer(workbench):
    page = workbench.page
    upload_calls = _route_json(
        page, '**/documents/upload/preview',
        {**_preview_result('文件首段预览'), 'parsed': {'source_format': 'txt'}})
    text_calls = _route_json(
        page, '**/documents/preview', {'detail': 'text endpoint must not be used'}, status=500)
    page.set_input_files('#doc-file', {
        'name': 'memory-rules.txt',
        'mimeType': 'text/plain',
        'buffer': b'file preview source',
    })

    _request_preview(page, '/documents/upload/preview')

    drawer = page.locator('#chunk-preview')
    drawer.wait_for(state='visible')
    assert drawer.evaluate('(element) => element.parentElement === document.body')
    assert drawer.locator('.chunk-preview-drawer').get_attribute('role') == 'dialog'
    assert len(upload_calls) == 1
    assert len(text_calls) == 0
    assert '文件首段预览' in drawer.inner_text()
    assert drawer.locator('button[aria-current="true"]').count() == 1
