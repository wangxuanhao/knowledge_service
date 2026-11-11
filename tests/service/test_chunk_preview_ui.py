"""Real-browser contract for the knowledge-write chunk preview drawer."""
from ontology_seed import seed_ontology
import json
import io
import re
import socket
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.ontology import Ontology
from source_test_images import png


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
    seed_ontology(repository, project_id, turtle, Ontology(turtle).summary())
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
        page.select_option('#project', project_id)
        page.wait_for_function('(id) => current === id', arg=project_id)
        page.click('[data-tab="sources"]')
        page.locator('#tab-sources').wait_for(state='visible')
        page.click('#upload-source')
        page.locator('#tab-ingest').wait_for(state='visible')
        # 完整文件选择、追加和预览入口只在第 1 步；后续步骤保留精简摘要。
        page.locator('#tab-ingest .document-upload-panel').wait_for(state='visible')
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
    # 写入页已改为纯文件上传：切片预览读取文件队列，不再有「粘贴正文」模式。
    page.set_input_files('#doc-file', {
        'name': title + '.txt',
        'mimeType': 'text/plain',
        'buffer': text.encode('utf-8'),
    })


def _request_preview(page, path_suffix='/documents/upload/preview', button='preview-chunks'):
    with page.expect_response(lambda response: response.url.endswith(path_suffix)):
        page.click('#'+button)
    page.wait_for_function("() => document.getElementById('chunk-preview-content').getAttribute('aria-busy') === 'false'")


def test_upload_controls_are_only_in_step_one_and_returning_preserves_appendable_files(workbench):
    page = workbench.page
    assert page.locator('#doc-dropzone').is_visible()
    page.evaluate("""() => {
      const files = new DataTransfer();
      files.items.add(new File(['第一份正文'], 'first.txt', {type:'text/plain'}));
      document.getElementById('doc-dropzone').dispatchEvent(new DragEvent('drop', {bubbles:true,cancelable:true,dataTransfer:files}));
    }""")
    page.wait_for_function("window.DocumentUploadQueue.items().length===1")
    for step in (1, 2):
        page.click(f'[data-upload-step="{step}"]')
        assert page.locator('#doc-dropzone').is_hidden()
        assert page.locator('#doc-file-list').is_hidden()
        summary = page.locator('#library-upload-selection')
        assert summary.is_visible()
        assert '1 份资料' in summary.inner_text()
        assert 'first.txt' in summary.inner_text()
    assert '1 份资料' in page.locator('#library-upload-confirm').inner_text()
    page.locator('#library-upload-selection').get_by_role('button', name='返回修改文件').click()
    assert page.locator('#doc-dropzone').is_visible()
    assert page.locator('#doc-file-list .document-queue-row').count() == 1
    page.evaluate("""() => {
      const files = new DataTransfer();
      files.items.add(new File(['第二份正文'], 'second.txt', {type:'text/plain'}));
      document.getElementById('doc-dropzone').dispatchEvent(new DragEvent('drop', {bubbles:true,cancelable:true,dataTransfer:files}));
    }""")
    page.wait_for_function("window.DocumentUploadQueue.items().length===2")
    assert page.locator('#preview-source').is_visible()
    assert page.locator('#preview-chunks').is_visible()
    page.click('[data-upload-step="2"]')
    assert '2 份资料' in page.locator('#library-upload-confirm').inner_text()
    assert 'first.txt' in page.locator('#library-upload-selection').inner_text()
    assert 'second.txt' in page.locator('#library-upload-selection').inner_text()
    assert page.locator('#preview-source').is_hidden()
    assert page.locator('#preview-chunks').is_hidden()


def test_invalid_drop_has_visible_feedback_on_the_file_selection_step(workbench):
    page = workbench.page
    page.evaluate("""() => {
      const files = new DataTransfer();
      files.items.add(new File(['invalid'], 'unsupported.exe', {type:'application/octet-stream'}));
      document.getElementById('doc-dropzone').dispatchEvent(new DragEvent('drop', {bubbles:true,cancelable:true,dataTransfer:files}));
    }""")
    page.wait_for_function("document.getElementById('doc-batch-help').textContent.includes('不支持')")
    assert page.locator('#doc-batch-help').is_visible()
    assert page.evaluate('window.DocumentUploadQueue.items().length') == 0


@pytest.mark.parametrize('width', [390, 1440])
def test_real_single_document_zip_preview_keeps_media_and_switches_uploaded_files_without_importing(workbench, width):
    page = workbench.page
    page.set_viewport_size({'width': width, 'height': 960})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('manifest.json', json.dumps({'schema_version': 2, 'document': {
            'entry': 'platform/a.md', 'title': '第一份规则'}}, ensure_ascii=False))
        archive.writestr('platform/a.md', '# 第一份规则\n\n生效日期：2026年9月2日\n\n'
                         '<table><tr><td>图片案例</td><td><img src="assets/a.png"></td></tr></table>\n\n'
                         '第一份独立正文\n\n'
                         '![missing](assets/missing.png)\n\n![remote](https://example.invalid/image.png)\n\n'
                         '<p onclick="window.zipPreviewPwned=true">安全正文</p>')
        archive.writestr('platform/assets/a.png', png())
    asset_requests = []
    page.on('request', lambda request: asset_requests.append(request.url)
            if '/source-documents/upload-preview/' in request.url or 'example.invalid' in request.url else None)
    page.evaluate("""() => {
      window.previewUrls = new Set(); window.revokedPreviewUrls = new Set();
      const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
      URL.createObjectURL = value => { const url=create(value); window.previewUrls.add(url); return url; };
      URL.revokeObjectURL = url => { window.revokedPreviewUrls.add(url); revoke(url); };
    }""")
    page.set_input_files('#doc-file', [
        {'name': 'rules.zip', 'mimeType': 'application/zip', 'buffer': buffer.getvalue()},
        {'name': 'second.txt', 'mimeType': 'text/plain', 'buffer': '第二份独立正文'.encode('utf-8')},
    ])
    assert 'ZIP' in page.locator('#doc-upload-formats').inner_text()
    assert '.zip' in page.locator('#doc-file').get_attribute('accept')
    assert page.locator('#preview-chunks').inner_text() == '预览切片'
    assert page.locator('#preview-source').inner_text() == '预览原文'

    _request_preview(page, button='preview-source')

    drawer = page.locator('#chunk-preview')
    assert drawer.locator('#chunk-preview-title').inner_text() == '原文预览'
    assert drawer.locator('#chunk-preview-list').is_hidden()
    assert drawer.locator('#chunk-preview-document').count() == 0
    assert '一包一份资料' in drawer.locator('#chunk-preview-package-summary').inner_text()
    assert '正文及 3 附件' in drawer.locator('#chunk-preview-package-summary').inner_text()
    picker = drawer.get_by_role('combobox', name='预览文件')
    assert picker.locator('option').all_text_contents() == ['rules.zip', 'second.txt']
    assert '第一份独立正文' in drawer.locator('#chunk-preview-content').inner_text()
    assert '图片缺失或不可读' in drawer.locator('#chunk-preview-content').inner_text()
    assert '外链图片' in drawer.locator('#chunk-preview-content').inner_text()
    assert page.evaluate('window.zipPreviewPwned') is None
    image = drawer.locator('#chunk-preview-content img')
    image.scroll_into_view_if_needed()
    page.wait_for_function("() => document.querySelector('#chunk-preview-content img')?.naturalWidth > 0")
    assert image.get_attribute('src').startswith('blob:')
    drawer.locator('.source-image-open').click()
    zoom = page.get_by_role('dialog', name='图片全屏查看')
    assert zoom.is_visible()
    page.keyboard.press('Escape')
    assert not zoom.is_visible()
    assert drawer.is_visible()
    drawer.locator('.source-image-open').click()
    zoom.get_by_role('button', name='关闭图片 ×').click()

    with page.expect_response(lambda response: response.url.endswith('/documents/upload/preview')):
        picker.select_option('1')
    page.wait_for_function("() => document.getElementById('chunk-preview-content').textContent.includes('第二份独立正文')")
    assert drawer.locator('#chunk-preview-package').is_hidden()
    assert not asset_requests
    picker.select_option('0')
    page.wait_for_function("() => document.getElementById('chunk-preview-content').textContent.includes('第一份独立正文')")
    assert drawer.locator('#chunk-preview-package').is_visible()
    assert picker.evaluate('(element) => element.scrollWidth <= element.clientWidth + 2')
    screenshots = Path(__file__).resolve().parents[2] / '.benchmarks' / 'zip-preview-20261111'
    screenshots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshots / f'zip-preview-{width}.png'))
    drawer.locator('#chunk-preview-close').click()
    assert page.evaluate('window.previewUrls.size === window.revokedPreviewUrls.size')
    assert page.locator('#doc-file-list .document-queue-row').count() == 2
    _request_preview(page)
    assert drawer.locator('#chunk-preview-title').inner_text() == '切片预览'
    assert drawer.locator('#chunk-preview-list').is_visible()
    assert drawer.locator('#chunk-preview-content pre').count() == 1
    assert drawer.locator('#chunk-preview-content img').count() == 0
    assert drawer.get_by_role('button', name='图文正文').count() == 0


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


def test_preview_host_is_inside_upload_dialog_and_record_import_stays_separate(workbench):
    page = workbench.page
    drawer = page.locator('#library-workspace-drawer > #chunk-preview')

    assert drawer.count() == 1
    assert drawer.locator('.chunk-preview-drawer').count() == 1
    accessibility = _dialog_accessibility(page)
    assert accessibility['role'] == 'dialog'
    assert accessibility['modal'] == 'true'
    assert accessibility['name']
    assert accessibility['description']
    structured_import = page.locator('details:has(#batch):has(#write-batch)')
    assert structured_import.count() == 1
    assert structured_import.evaluate('(details) => details.open') is True
    assert '结构化记录导入（高级）' in structured_import.locator('summary').inner_text()
    assert '不是批量上传文档' in structured_import.text_content()


def test_text_preview_opens_safe_switchable_drawer_without_refetching(workbench):
    page = workbench.page
    first = '<img src=x onerror="window.previewPwned=true">第一段原文'
    second = '第二段原文'
    calls = _route_json(page, '**/documents/upload/preview', _preview_result(first, second))
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
    calls = _route_json(page, '**/documents/upload/preview', [
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
    _route_json(page, '**/documents/upload/preview', _preview_result())
    _choose_text(page)

    _request_preview(page)
    drawer = page.locator('#chunk-preview')
    drawer.wait_for(state='visible')
    assert drawer.get_by_role('button', name=re.compile(r'^片段\s*\d+')).count() == 0
    assert re.search(r'没有可预览|暂无切片|未生成切片', drawer.inner_text())
    page.keyboard.press('Escape')
    drawer.wait_for(state='hidden')

    page.unroute('**/documents/upload/preview')
    _route_json(page, '**/documents/upload/preview', {'detail': '预览暂时不可用'}, status=503)
    _request_preview(page)

    drawer.wait_for(state='visible')
    page.wait_for_function("() => !document.getElementById('preview-chunks').disabled")
    assert '预览暂时不可用' in page.locator('#status').inner_text()
    assert '预览暂时不可用' in drawer.locator('#chunk-preview-content').inner_text()
    assert drawer.get_by_role('button', name='重试此文件').count() == 1
    assert page.locator('#preview-chunks').is_enabled()
    _assert_background_is_inert(page, True)


@pytest.mark.parametrize('width', [850, 520])
def test_preview_traps_focus_and_stays_inside_narrow_viewports(workbench, width):
    page = workbench.page
    page.set_viewport_size({'width': width, 'height': 700})
    _route_json(page, '**/documents/upload/preview', _preview_result('第一段' * 800, '第二段'))
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


@pytest.mark.parametrize('width', [390, 1440])
def test_document_date_evidence_scrolls_without_hiding_preview_controls(workbench, width):
    page = workbench.page
    page.set_viewport_size({'width': width, 'height': 844})
    result = _preview_result('第一片正文', '第二片正文')
    result['document_time'] = {
        'published_at': '2026-09-24T16:00:00Z', 'valid_from': None, 'valid_until': None,
        'document_temporal': {'fields': {'published_at': {
            'status': 'extracted', 'candidates': [
                {'raw': '<img src=x onerror="window.previewTemporalPwned=true">'
                        + '发布时间：2026年9月25日' + '文档日期依据' * 20,
                 'start_char': index * 20, 'end_char': index * 20 + 19, 'kind': 'header'}
                for index in range(40)
            ],
        }}, 'warnings': []},
    }
    _route_json(page, '**/documents/upload/preview', result)
    _choose_text(page)
    _request_preview(page)
    drawer = page.locator('#chunk-preview')
    drawer.wait_for(state='visible')
    dates = drawer.locator('#chunk-preview-summary')
    assert '原文发布时间 · 2026-09-25' in dates.inner_text()
    assert '业务生效 · 未知' in dates.inner_text()
    assert '未设结束时间' in dates.inner_text()
    assert '持续有效' not in dates.inner_text()
    evidence = dates.locator('details')
    assert evidence.evaluate('(element) => element.open') is False
    evidence.locator('summary').click()
    assert evidence.evaluate('(element) => element.open') is True
    assert '解析正文位置 0–19' in evidence.inner_text()
    assert evidence.locator('img').count() == 0
    assert page.evaluate('window.previewTemporalPwned') is None

    overview = drawer.locator('.chunk-preview-overview')
    assert overview.evaluate('(element) => element.scrollHeight > element.clientHeight')
    assert overview.evaluate('(element) => element.clientHeight <= innerHeight * .45 + 1')
    overview.evaluate('(element) => { element.scrollTop = element.scrollHeight; }')
    assert overview.evaluate('(element) => element.scrollTop > 0')
    overview.evaluate('(element) => { element.scrollTop = 0; }')
    for region in [overview, drawer.locator('.chunk-preview-content')]:
        assert region.evaluate('(element) => element.scrollWidth <= element.clientWidth + 1')
    content_box = drawer.locator('.chunk-preview-content').bounding_box()
    assert content_box and content_box['height'] > 40
    assert content_box['y'] + content_box['height'] <= 845
    drawer.get_by_role('button', name=re.compile(r'^片段\s*2')).click()
    assert '第二片正文' in drawer.locator('#chunk-preview-content').inner_text()
    drawer.get_by_role('button', name=re.compile('关闭')).click()
    drawer.wait_for(state='hidden')


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
    assert drawer.evaluate("(element) => element.parentElement.id === 'library-workspace-drawer'")
    assert drawer.locator('.chunk-preview-drawer').get_attribute('role') == 'dialog'
    assert len(upload_calls) == 1
    assert len(text_calls) == 0
    assert '文件首段预览' in drawer.inner_text()
    assert drawer.locator('button[aria-current="true"]').count() == 1


def test_multiple_files_can_be_selected_and_previewed_independently(workbench):
    page = workbench.page
    calls = _route_json(page, '**/documents/upload/preview', [
        _preview_result('第一份文件的切片'),
        _preview_result('第二份文件的切片'),
    ])
    page.set_input_files('#doc-file', [
        {'name': 'first.txt', 'mimeType': 'text/plain', 'buffer': b'first source'},
        {'name': 'second.txt', 'mimeType': 'text/plain', 'buffer': b'second source'},
    ])
    _request_preview(page)
    drawer = page.locator('#chunk-preview')
    drawer.wait_for(state='visible')
    picker = drawer.get_by_role('combobox', name='预览文件')
    assert picker.locator('option').all_text_contents() == ['first.txt', 'second.txt']
    assert '第一份文件的切片' in drawer.locator('#chunk-preview-content').inner_text()

    with page.expect_response(lambda response: response.url.endswith('/documents/upload/preview')):
        picker.select_option('1')
    page.wait_for_function("() => document.getElementById('chunk-preview-content').textContent.includes('第二份文件的切片')")
    assert '第二份文件的切片' in drawer.locator('#chunk-preview-content').inner_text()
    assert '第一份文件的切片' not in drawer.locator('#chunk-preview-content').inner_text()
    assert 'second.txt' in drawer.locator('#chunk-preview-summary').inner_text()
    assert 'filename="second.txt"' in calls[1].post_data_buffer.decode('utf-8')

    picker.select_option('0')
    assert '第一份文件的切片' in drawer.locator('#chunk-preview-content').inner_text()
    assert len(calls) == 2
