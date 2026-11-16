"""Browser workflow against deterministic API responses, without OCR/model work."""
import json
from pathlib import Path

import pytest
from test_chunk_preview_ui import browser, native_workbench


@pytest.mark.parametrize('width', [390, 1280])
def test_upload_configure_parse_and_publish_are_separate(tmp_path, width):
    sync = pytest.importorskip('playwright.sync_api')
    web = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'
    with sync.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
        page = browser.new_page(viewport={'width': width, 'height': 844})
        calls = []
        publication_settings = {}
        item = {'id': 'a', 'original_name': 'rules.md', 'status': 'staged', 'options': {}, 'metadata_version': 1, 'chunk_version': 1}

        def route(request):
            nonlocal publication_settings
            path = request.request.url.split('http://test')[1]
            method = request.request.method
            calls.append((method, path))
            result = {'id': 's', 'items': [item], 'publication_settings': publication_settings}
            if path == '/api/ingest/capabilities':
                result = {'supported_extensions': ['.md'], 'max_file_bytes': 25000000}
            elif path.endswith('/preparations') and method == 'POST':
                result = {'id': 's', 'items': []}
            elif path.endswith('/preparations') and method == 'GET':
                result = {'items': []}
            elif path.endswith('/files'):
                result = item
            elif path.endswith('/settings'):
                publication_settings = request.request.post_data_json['settings']
                result['publication_settings'] = publication_settings
            elif '/items/a' in path and method == 'PATCH':
                item['options'] = request.request.post_data_json['options']
                item['metadata_version'] += 1
                result = item
            elif path.endswith('/parse'):
                item['status'] = 'completed'
            elif path.endswith('/result'):
                result = {'text': '这是规则正文', 'chunks': [{'text': '这是规则正文'}], 'warnings': []}
            elif path.endswith('/publish'):
                item['status'] = 'published'
                result = {'batch_id': 'b', 'session': {'id': 's', 'items': [item]}}
            request.fulfill(json=result)

        page.route('http://test/api/**', route)
        page.route('http://test/', lambda r: r.fulfill(body='<html><body><div id="host"></div></body></html>', content_type='text/html'))
        page.goto('http://test/')
        page.add_style_tag(path=str(web / 'document-preparation.css'))
        page.add_script_tag(path=str(web / 'document-preparation.js'))
        page.add_script_tag(path=str(web / 'document-preparation-view.js'))
        page.evaluate("window.view=PreparationView.mount(document.querySelector('#host'),{project:()=> 'p',settings:()=>({extraction_mode:'documents'})}); view.open()")
        page.locator('#preparation-files').set_input_files({'name': 'rules.md', 'mimeType': 'text/markdown', 'buffer': b'# Rules'})
        page.get_by_text('待解析', exact=False).first.wait_for()
        assert not any(path.endswith('/parse') for _, path in calls)
        page.get_by_role('button', name='下一步：配置资料').click()
        page.get_by_role('button', name='设置', exact=True).click()
        assert page.locator('.preparation-editor').evaluate("e => e.closest('.preparation-file')?.dataset.itemId") == 'a'
        page.locator('#preparation-edit-title').fill('美团规则')
        page.get_by_role('button', name='设置', exact=True).click()
        assert page.locator('.preparation-editor').is_hidden()
        page.get_by_role('button', name='设置', exact=True).click()
        assert page.locator('#preparation-edit-title').input_value() == '美团规则'
        page.get_by_role('button', name='保存设置').click()
        assert not any(path.endswith('/parse') for _, path in calls)
        page.get_by_role('button', name='开始解析（1 份）').click()
        page.get_by_role('button', name='查看正文与切片').click()
        page.get_by_text('这是规则正文', exact=True).first.wait_for()
        page.get_by_role('button', name='关闭预览').click()
        page.get_by_role('button', name='下一步：确认入库').click()
        page.get_by_role('button', name='确认入库（1 份）').click()
        assert sum(path.endswith('/parse') for _, path in calls) == 1
        assert sum(path.endswith('/publish') for _, path in calls) == 1
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        browser.close()


@pytest.mark.parametrize('width', [390, 1280])
def test_native_file_entry_uses_staged_api_and_documents_mode(native_workbench, width):
    page = native_workbench.page
    page.set_viewport_size({'width': width, 'height': 900})
    calls = []
    page.on('request', lambda request: calls.append(request.url))
    page.set_input_files('#preparation-files', {
        'name': 'standalone.txt', 'mimeType': 'text/plain',
        'buffer': '可以直接核对的独立资料正文。'.encode('utf-8'),
    })
    page.locator('[data-preparation-step="0"]').get_by_text('待解析', exact=False).wait_for()
    assert not any(url.endswith('/parse') for url in calls)
    page.get_by_role('button', name='原件预览', exact=True).click()
    page.locator('#preparation-preview pre').wait_for()
    assert '独立资料正文' in page.locator('#preparation-preview').inner_text()
    page.get_by_role('button', name='关闭预览').click()
    assert not any(url.endswith('/parse') for url in calls)
    page.get_by_role('button', name='下一步：配置资料').click()
    page.select_option('#extraction-mode', 'documents')
    artifacts = Path('.benchmarks/preparation-ui')
    artifacts.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(artifacts / f'configuration-{width}.png'), full_page=True)
    page.get_by_role('button', name='开始解析（1 份）').click()
    page.get_by_role('button', name='查看正文与切片').wait_for(timeout=15000)
    page.get_by_role('button', name='查看正文与切片').click()
    page.locator('#preparation-preview pre').first.wait_for()
    assert '独立资料正文' in page.locator('#preparation-preview').inner_text()
    page.get_by_role('button', name='关闭预览').click()
    assert not any(url.endswith('/publish') for url in calls)
    page.reload(wait_until='load')
    page.wait_for_function("document.querySelectorAll('#project option').length > 1")
    page.select_option('#project', native_workbench.project)
    if not page.locator('[data-tab="sources"]').is_visible():
        page.get_by_role('button', name='工作区菜单', exact=True).click()
    page.click('[data-tab="sources"]')
    page.click('#upload-source')
    page.get_by_role('button', name='查看正文与切片').wait_for(timeout=10000)
    assert page.locator('#extraction-mode').input_value() == 'documents'
    page.get_by_role('button', name='下一步：确认入库').click()
    with page.expect_response(lambda response: response.url.endswith('/publish')) as publication:
        page.get_by_role('button', name='确认入库（1 份）').click()
    assert publication.value.status == 202
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_native_same_name_files_and_mixed_platform_bulk_configuration(native_workbench):
    page = native_workbench.page
    calls = []
    page.on('request', lambda request: calls.append(request.url))
    page.evaluate("""() => {
      const input=document.querySelector('#preparation-files'), files=new DataTransfer();
      files.items.add(new File(['第一份资料正文'], 'rules.txt', {type:'text/plain',lastModified:123}));
      files.items.add(new File(['第二份资料正文'], 'rules.txt', {type:'text/plain',lastModified:123}));
      input.files=files.files;input.dispatchEvent(new Event('change'));
    }""")
    page.wait_for_function("document.querySelectorAll('[data-preparation-step=\"0\"] .preparation-file[data-item-id]').length===2")
    page.get_by_role('button', name='下一步：配置资料').click()
    page.get_by_role('button', name='批量设置所选文件').click()
    editor = page.locator('.preparation-editor')
    assert editor.evaluate("e => !e.closest('.preparation-file') && Boolean(e.compareDocumentPosition(document.querySelector('[data-preparation-step=\"1\"] .preparation-files')) & Node.DOCUMENT_POSITION_FOLLOWING)")
    editor.get_by_role('checkbox', name='修改平台', exact=True).check()
    editor.get_by_label('平台范围', exact=True).select_option('specific')
    editor.get_by_label('平台值（逗号分隔）', exact=True).fill('美团')
    editor.get_by_role('checkbox', name='修改业务', exact=True).check()
    editor.get_by_label('业务范围', exact=True).select_option('specific')
    editor.get_by_label('业务值（逗号分隔）', exact=True).fill('到店')
    editor.get_by_role('button', name='保存设置').click()
    editor.wait_for(state='hidden')
    page.get_by_role('button', name='设置', exact=True).nth(1).click()
    assert editor.evaluate("e => e.closest('.preparation-file') === document.querySelectorAll('[data-preparation-step=\"1\"] .preparation-file')[1]")
    editor.get_by_label('平台值（逗号分隔）', exact=True).fill('抖音')
    editor.get_by_role('button', name='保存设置').click()
    editor.wait_for(state='hidden')
    scopes = page.locator('[data-preparation-step="1"] .preparation-scope').all_text_contents()
    assert '平台：美团' in scopes[0] and '业务：到店' in scopes[0]
    assert '平台：抖音' in scopes[1] and '业务：到店' in scopes[1]
    assert not any(url.endswith('/parse') for url in calls)
