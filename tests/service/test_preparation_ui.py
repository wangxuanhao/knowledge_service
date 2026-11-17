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
        page.get_by_role('button', name='下一步：确认处理').click()
        page.get_by_role('button', name='开始建立检索索引（1 份）').click()
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
    page.get_by_role('button', name='下一步：确认处理').click()
    with page.expect_response(lambda response: response.url.endswith('/publish')) as publication:
        page.get_by_role('button', name='开始建立检索索引（1 份）').click()
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



def test_failed_preparation_cannot_advance_to_publication(browser):
    web = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'
    page = browser.new_page()
    item = dict(id='a', original_name='rule.md', status='staged', options={}, metadata_version=1)
    def route(request):
        path = request.request.url
        result = dict(id='s', items=[item])
        if path.endswith('/capabilities'): result = dict(supported_extensions=['.md'])
        elif path.endswith('/files'): result = item
        elif path.endswith('/parse'):
            item.update(status='failed', result_ready=False, error='附件识别失败，请重试')
        request.fulfill(json=result)
    page.route('http://test/api/**', route)
    page.route('http://test/', lambda r:r.fulfill(body='<div id="host"></div>', content_type='text/html'))
    page.goto('http://test/')
    page.add_script_tag(path=str(web/'document-preparation.js'))
    page.add_script_tag(path=str(web/'document-preparation-view.js'))
    page.evaluate("PreparationView.mount(document.querySelector('#host'),{project:()=> 'p'}).open()")
    page.locator('#preparation-files').set_input_files(dict(name='rule.md', mimeType='text/markdown', buffer=b'# rule'))
    page.get_by_role('button', name='下一步：配置资料').click()
    page.get_by_role('button', name='开始解析（1 份）').click()
    page.get_by_text('附件识别失败，请重试', exact=False).first.wait_for()
    assert page.get_by_role('button', name='下一步：确认处理').is_disabled()
    page.close()



@pytest.mark.parametrize('entry', ['preview', 'row', 'batch'])
def test_reparse_recovers_expired_result_without_reupload(browser, entry):
    web = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'
    page = browser.new_page()
    page.set_default_timeout(5000)
    stale = dict(id='a', original_name='md-assets-no-manifest.zip', status='completed' if entry=='preview' else 'stale',
        result_ready=entry=='preview', options={'title':'资料标题'}, metadata_version=7)
    healthy = dict(id='b', original_name='other.md', status='completed', result_ready=True, options={}, metadata_version=2)
    calls = []
    def route(request):
        path = request.request.url
        result = dict(id='s', items=[stale] if entry=='row' else [stale, healthy])
        if path.endswith('/capabilities'):
            result = dict(supported_extensions=['.zip','.md'])
        elif path.endswith('/result'):
            request.fulfill(status=409, json={'detail':'解析器或模型已变化，请明确重新开始解析'})
            return
        elif path.endswith(('/parse', '/retry')):
            calls.append(request.request.post_data_json)
            stale.update(status='queued', result_ready=False)
        request.fulfill(json=result)
    page.route('http://test/api/**', route)
    page.route('http://test/', lambda r:r.fulfill(body='<div id="host"></div>', content_type='text/html'))
    page.goto('http://test/')
    page.evaluate("localStorage.setItem('kg_preparation:local:p','s')")
    page.add_script_tag(path=str(web/'document-preparation.js'))
    page.add_script_tag(path=str(web/'document-preparation-view.js'))
    page.evaluate("PreparationView.mount(document.querySelector('#host'),{project:()=> 'p'}).open()")
    if entry=='preview':
        page.locator('.preparation-file[data-item-id="a"]').get_by_role('button', name='查看正文与切片').click()
        page.get_by_text('解析器或模型已变化', exact=False).wait_for()
        page.get_by_role('button', name='重新解析此文件', exact=True).click()
        assert not page.locator('#preparation-preview').is_visible()
    elif entry=='row':
        page.locator('.preparation-file[data-item-id="a"]').get_by_role('button', name='重新解析', exact=True).click()
    else:
        page.get_by_role('button', name='重试失败或过期项', exact=False).click()
    page.locator('.preparation-file[data-item-id="a"][data-status="queued"]').wait_for()
    assert len(calls)==1 and calls[0]['item_ids']==['a']
    assert calls[0]['versions']=={'a':7}
    if entry!='row':
        assert page.locator('.preparation-file[data-item-id="b"][data-status="completed"]').is_visible()
    assert not page.locator('#preparation-preview').is_visible()
    page.close()



def test_filtered_ocr_notice_is_not_rendered_as_preparation_error(browser):
    page=browser.new_page();page.set_default_timeout(5000)
    web=Path(__file__).resolve().parents[2]/'knowledge_service'/'web'
    item=dict(id='a',original_name='rule.zip',status='completed',result_ready=True,options={},metadata_version=1)
    warning=dict(code='ocr_text_filtered',severity='warning',message='已保留 2 块可靠文字，1 块模糊文字未采用。')
    def route(request):
        if request.request.url.endswith('/result'):result=dict(text='可靠文字',chunks=[{'text':'可靠文字'}],warnings=[warning])
        elif request.request.url.endswith('/capabilities'):result={}
        else:result=dict(id='s',items=[item])
        request.fulfill(json=result)
    page.route('http://test/api/**',route)
    page.route('http://test/',lambda r:r.fulfill(content_type='text/html',body='<div id="host"></div>'))
    page.goto('http://test/');page.evaluate("localStorage.setItem('kg_preparation:local:p','s')")
    page.add_script_tag(path=str(web/'document-preparation.js'));page.add_script_tag(path=str(web/'document-preparation-view.js'))
    page.evaluate("PreparationView.mount(document.querySelector('#host'),{project:()=> 'p'}).open()")
    page.get_by_role('button',name='查看正文与切片').click()
    notice=page.get_by_text(warning['message'],exact=True)
    notice.wait_for();assert 'preparation-error' not in (notice.get_attribute('class') or '')
    assert page.get_by_role('button',name='重新解析此文件',exact=True).count()==0
    page.close()



@pytest.mark.parametrize('mode,button',[('documents','开始建立检索索引'),('ontology','开始知识抽取'),('discovery','开始构建本体')])
def test_publication_uses_processing_mode_and_clears_submitted_files(browser,mode,button):
    page=browser.new_page();page.set_default_timeout(5000)
    web=Path(__file__).resolve().parents[2]/'knowledge_service'/'web'
    items=[dict(id='a',original_name='pending.md',status='completed',result_ready=True,metadata_version=1,options={}),
        dict(id='b',original_name='submitted.md',status='published',metadata_version=1,options={})]
    calls=[]
    def route(request):
        path=request.request.url;result=dict(id='s',items=items,publication_settings={'extraction_mode':mode})
        if path.endswith('/capabilities'):result={}
        elif path.endswith('/publish'):
            calls.append(request.request.post_data_json);items[0]['status']='published'
            result={'batch_id':'batch','session':result}
        request.fulfill(json=result)
    page.route('http://test/api/**',route)
    page.route('http://test/',lambda r:r.fulfill(content_type='text/html',body='<div id="host"></div>'))
    page.goto('http://test/');page.evaluate("localStorage.setItem('kg_preparation:local:p','s')")
    page.add_script_tag(path=str(web/'document-preparation.js'));page.add_script_tag(path=str(web/'document-preparation-view.js'))
    page.evaluate("window.view=PreparationView.mount(document.querySelector('#host'),{project:()=> 'p'});view.open()")
    assert page.locator('.preparation-file[data-item-id="b"]').count()==0
    page.get_by_role('button',name='下一步：确认处理',exact=True).click()
    page.get_by_role('button',name=button+'（1 份）',exact=True).click()
    page.wait_for_function('view.snapshot().step===0')
    assert page.locator('.preparation-file').count()==0
    assert page.evaluate("localStorage.getItem('kg_preparation:local:p')") is None
    assert calls[0]['item_ids']==['a']
    page.close()


def test_finished_drafts_are_not_restored_and_recovery_only_lists_pending_files(browser):
    page=browser.new_page();page.set_default_timeout(5000)
    web=Path(__file__).resolve().parents[2]/'knowledge_service'/'web'
    published=dict(id='done',original_name='done.md',status='published',options={})
    pending=dict(id='pending',original_name='continue.md',status='failed',options={})
    def route(request):
        path=request.request.url
        if path.endswith('/capabilities'):result={}
        elif path.endswith('/preparations'):result={'items':[{'id':'finished','items':[published]},{'id':'pending','items':[published,pending]}]}
        else:result={'id':'s','items':[published]}
        request.fulfill(json=result)
    page.route('http://test/api/**',route);page.route('http://test/',lambda r:r.fulfill(content_type='text/html',body='<div id="host"></div>'))
    page.goto('http://test/');page.evaluate("localStorage.setItem('kg_preparation:local:p','s')")
    page.add_script_tag(path=str(web/'document-preparation.js'));page.add_script_tag(path=str(web/'document-preparation-view.js'))
    page.evaluate("window.view=PreparationView.mount(document.querySelector('#host'),{project:()=> 'p'});view.open()")
    assert page.locator('.preparation-file').count()==0
    page.get_by_text('继续未完成上传',exact=True).click()
    page.get_by_role('button',name='继续上传 · 1 份 · continue.md',exact=True).wait_for()
    assert page.get_by_role('button',name='finished',exact=False).count()==0
    page.close()



def test_submitting_good_file_keeps_failed_upload_available_for_retry(browser):
    page=browser.new_page();page.set_default_timeout(5000)
    web=Path(__file__).resolve().parents[2]/'knowledge_service'/'web'
    items=[];attempts=0
    def route(request):
        nonlocal attempts
        path=request.request.url;result={'id':'s','items':items,'publication_settings':{'extraction_mode':'documents'}}
        if path.endswith('/capabilities'):result={'supported_extensions':['.md'],'max_file_bytes':1000000}
        elif path.endswith('/files'):
            failed=b'retry.md' in request.request.post_data_buffer
            if failed:
                attempts+=1
                if attempts==1:request.fulfill(status=422,json={'detail':'模拟上传失败'});return
            item=dict(id='b' if failed else 'a',original_name='retry.md' if failed else 'ok.md',status='staged',options={},metadata_version=1)
            items.append(item);result=item
        elif path.endswith('/parse'):items[0].update(status='completed',result_ready=True)
        elif path.endswith('/publish'):
            items[0]['status']='published';result={'batch_id':'batch','session':result}
        request.fulfill(json=result)
    page.route('http://test/api/**',route);page.route('http://test/',lambda r:r.fulfill(content_type='text/html',body='<div id="host"></div>'))
    page.goto('http://test/');page.add_script_tag(path=str(web/'document-preparation.js'));page.add_script_tag(path=str(web/'document-preparation-view.js'))
    page.evaluate("window.view=PreparationView.mount(document.querySelector('#host'),{project:()=> 'p',settings:()=>({extraction_mode:'documents'})});view.open()")
    page.locator('#preparation-files').set_input_files([{'name':name,'mimeType':'text/markdown','buffer':b'# rules'} for name in ['ok.md','retry.md']])
    page.get_by_role('button',name='重试上传',exact=True).wait_for()
    page.get_by_role('button',name='下一步：配置资料',exact=True).click()
    page.get_by_role('button',name='开始解析（1 份）',exact=True).click()
    page.get_by_role('button',name='下一步：确认处理',exact=True).click()
    page.get_by_role('button',name='开始建立检索索引（1 份）',exact=True).click()
    page.wait_for_function('view.snapshot().step===0')
    page.get_by_role('button',name='重试上传',exact=True).click()
    page.locator('.preparation-file[data-item-id="b"]').wait_for()
    assert attempts==2 and page.locator('.preparation-file[data-item-id="a"]').count()==0
    page.close()


def test_rechunk_applies_current_chunk_settings_on_completed_file(browser):
    """解析完成的文件必须能按当前切片设置重新拆分（正文复用），不必重新识别。"""
    page=browser.new_page();page.set_default_timeout(5000)
    web=Path(__file__).resolve().parents[2]/'knowledge_service'/'web'
    item=dict(id='a',original_name='rules.md',status='completed',result_ready=True,
        options={'chunk_strategy':'fixed','chunk_size':1800,'chunk_overlap':200},metadata_version=3)
    calls=[]
    def route(request):
        path=request.request.url
        result=dict(id='s',items=[item],publication_settings={'extraction_mode':'documents'},settings_version=0)
        if path.endswith('/capabilities'):result=dict(supported_extensions=['.md'],max_file_bytes=1000000)
        elif path.endswith('/settings'):
            calls.append(('settings',request.request.post_data_json))
            result=dict(id='s',items=[item],settings_version=1,
                publication_settings=request.request.post_data_json.get('settings'),defaults={})
        elif path.endswith('/parse'):
            calls.append(('parse',request.request.post_data_json))
            item['status']='reused'
        request.fulfill(json=result)
    page.route('http://test/api/**',route);page.route('http://test/',lambda r:r.fulfill(content_type='text/html',body='<div id="host"></div>'))
    page.goto('http://test/');page.evaluate("localStorage.setItem('kg_preparation:local:p','s')")
    page.add_script_tag(path=str(web/'document-preparation.js'));page.add_script_tag(path=str(web/'document-preparation-view.js'))
    page.evaluate("window.view=PreparationView.mount(document.querySelector('#host'),{project:()=> 'p',"
                  "settings:()=>({extraction_mode:'documents',chunk_strategy:'fixed',chunk_size:300,chunk_overlap:50})});view.open()")
    page.get_by_role('button',name='重新拆分',exact=True).click()
    page.get_by_text('已按当前切片设置重新拆分',exact=False).first.wait_for()
    assert [name for name,_ in calls]==['settings','parse'],calls
    assert calls[0][1]['settings']['chunk_size']==300 and calls[0][1]['settings']['chunk_overlap']==50
    assert calls[1][1]['item_ids']==['a']
    assert page.locator('.preparation-file[data-item-id="a"][data-status="reused"]').is_visible()
    page.close()


def test_project_switch_resets_pending_upload_list(browser):
    """未提交的上传草稿不能被带到另一个项目（避免跨项目误入库）。"""
    page=browser.new_page();page.set_default_timeout(5000)
    web=Path(__file__).resolve().parents[2]/'knowledge_service'/'web'
    project={'id':'p'}
    items=[dict(id='a',original_name='draft.md',status='staged',options={},metadata_version=1)]
    def route(request):
        path=request.request.url
        result=dict(id='s',items=items,publication_settings={'extraction_mode':'documents'})
        if path.endswith('/capabilities'):result=dict(supported_extensions=['.md'],max_file_bytes=1000000)
        elif path.endswith('/files'):
            new=dict(id=items[-1]['id']+items[-1]['id'],original_name='draft.md',status='staged',options={},metadata_version=1)
            items.append(new);result=new
        request.fulfill(json=result)
    page.route('http://test/api/**',route);page.route('http://test/',lambda r:r.fulfill(content_type='text/html',body='<div id="host"></div>'))
    page.goto('http://test/')
    page.add_script_tag(path=str(web/'document-preparation.js'));page.add_script_tag(path=str(web/'document-preparation-view.js'))
    page.evaluate("window.__project={id:'p'};window.view=PreparationView.mount(document.querySelector('#host'),{project:()=> window.__project.id});view.open()")
    page.locator('#preparation-files').set_input_files(dict(name='draft.md',mimeType='text/markdown',buffer=b'# draft'))
    page.locator('.preparation-file[data-item-id="aa"]').wait_for()
    # 切换项目后清单必须清空，未提交内容不进入新项目。
    page.evaluate("()=>{window.__project.id='q';view.projectChanged('q');}")
    assert page.locator('#preparation-workflow .preparation-file').count()==0
    page.close()
