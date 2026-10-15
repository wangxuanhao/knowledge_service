import pytest

from test_records_ledger_ui import WEB, ledger_browser, page  # shared real Edge harness


@pytest.fixture()
def edit_page(page):
    page.evaluate(r'''() => {
      window.editSaves=[]; window.ontologyRequests=[]; window.graphRefreshes=0;
      const draw=document.createElement('button');draw.id='draw-graph';
      draw.onclick=()=>{window.graphRefreshes++;};document.body.append(draw);
      const original=window.fetch;
      window.fetch=async(input,options={})=>{
        const url=new URL(typeof input==='string'?input:input.url,'http://localhost');
        const payload=body=>new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json'}});
        if(url.pathname.endsWith('/ontology')){
          window.ontologyRequests.push(url.searchParams.get('ontology_id'));
          if(window.failOntology)return new Response('{"error":"ontology unavailable"}',{status:500});
          const old=url.searchParams.get('ontology_id')==='o1';
          return payload({id:old?'o1':'o2-note',summary:{classes:[
            {id:'urn:ontology:文档标题',label_zh:'文档标题'},
            {id:old?'urn:ontology:旧类':'urn:ontology:新类',label_zh:old?'旧类':'新类'}],relations:[]}});
        }
        if(url.pathname.endsWith('/records/e1')&&options.method==='PUT'){
          const request=JSON.parse(options.body);window.editSaves.push(request);
          window.savedEdit={...request.record,version:request.expected_version+1,
            version_id:'saved-'+(request.expected_version+1),recorded_at:'2026-10-14T01:00:00Z'};
          return payload(window.savedEdit);
        }
        const response=await original(input,options);
        if(url.pathname.endsWith('/records/query')&&window.savedEdit){
          const result=await response.json();
          result.records=result.records.map(row=>row.id==='e1'?window.savedEdit:row);
          return payload(result);
        }
        return response;
      };
    }''')
    page.add_style_tag(path=str(WEB / 'record-dialog.css'))
    page.add_script_tag(path=str(WEB / 'record-dialog.js'))
    return page


def open_editor(page, *, ontology_id='o1'):
    page.evaluate('''ontologyId => window.editRecord({id:'e1',kind:'entity',text:'价格说明',
      type:'urn:ontology:文档标题',ontology_id:ontologyId,version:4,metadata:{},properties:{}})''', ontology_id)
    page.wait_for_selector('#record-dialog[open]')


def test_editor_uses_bound_release_and_shows_both_versions(edit_page):
    open_editor(edit_page)
    edit_page.wait_for_selector('#structured-record-type')
    options=edit_page.locator('#structured-record-type option').all_text_contents()
    assert '旧类' in options and '新类' not in options
    assert edit_page.evaluate('() => window.ontologyRequests') == ['o1']
    assert '修订 r4' in edit_page.inner_text('#record-dialog-help')
    assert '依据本体 v1（历史）' in edit_page.inner_text('#record-dialog-help')


def test_graph_edit_save_refreshes_ledger_and_repeated_save_uses_new_revision(edit_page):
    open_editor(edit_page)
    edit_page.wait_for_selector('#structured-record-text')
    edit_page.fill('#structured-record-text', '已修改名称')
    edit_page.click('#save-structured-record')
    edit_page.wait_for_function("() => window.graphRefreshes===1")
    edit_page.wait_for_function("() => document.querySelector('[data-record-id=\"e1\"]').textContent.includes('修订 r5')")
    assert '依据本体 v1（历史）' in edit_page.inner_text('[data-record-id="e1"]')
    assert '依据本体 v1（历史）' in edit_page.inner_text('#record-dialog-message')
    assert '修订 r5' in edit_page.inner_text('#record-dialog-help')
    edit_page.click('#save-structured-record')
    edit_page.wait_for_function('() => window.editSaves.length===2')
    requests=edit_page.evaluate('() => window.editSaves')
    assert [request['expected_version'] for request in requests] == [4, 5]
    assert [request['record']['ontology_id'] for request in requests] == ['o1', 'o1']


@pytest.mark.parametrize('ontology_id', [None, 'missing-release'])
def test_unknown_binding_blocks_structured_editor_without_latest_fallback(edit_page, ontology_id):
    open_editor(edit_page, ontology_id=ontology_id)
    edit_page.wait_for_function("() => document.getElementById('record-structured-edit').textContent.includes('未知本体')")
    assert edit_page.query_selector('#save-structured-record') is None
    assert edit_page.evaluate('() => window.ontologyRequests') == []


def test_bound_release_read_failure_does_not_offer_latest_types(edit_page):
    edit_page.evaluate('() => {window.failOntology=true;}')
    open_editor(edit_page)
    edit_page.wait_for_function("() => document.getElementById('record-structured-edit').textContent.includes('加载失败')")
    assert edit_page.query_selector('#save-structured-record') is None
    assert edit_page.evaluate('() => window.ontologyRequests') == ['o1']


def test_save_success_with_refresh_failure_is_not_reported_as_save_failure(edit_page):
    open_editor(edit_page)
    edit_page.wait_for_selector('#structured-record-text')
    edit_page.evaluate("() => {window.drawGraph=async()=>{throw Error('graph unavailable');};}")
    edit_page.click('#save-structured-record')
    edit_page.wait_for_function("() => document.getElementById('record-dialog-message').textContent.includes('部分视图刷新失败')")
    assert '已保存修订 r5' in edit_page.inner_text('#record-dialog-message')
    assert edit_page.evaluate('() => window.editSaves.length') == 1
    assert edit_page.input_value('#revision-version') == '5'


def test_revision_timeline_distinguishes_unreadable_ontology_from_unknown(edit_page):
    edit_page.evaluate(r'''() => {
      const previous=window.fetch;
      window.fetch=async(input,options)=>String(input).endsWith('/ontologies')
        ?new Response('{"error":"unavailable"}',{status:500}):previous(input,options);
      window.historyFor(window.timelineRecord);
    }''')
    edit_page.wait_for_selector('.record-revision-item')
    assert '本体归属读取失败' in edit_page.inner_text('.record-revision-item[data-revision="3"]')
    assert '未知本体' not in edit_page.inner_text('.record-revision-item[data-revision="3"]')


def test_save_updates_open_graph_inspector_with_the_saved_revision(edit_page):
    edit_page.evaluate('''() => {
      const detail=document.createElement('section');detail.id='graph-detail';
      detail.dataset.recordId='e1';detail.innerHTML='<section class="evidence-identity"></section>';
      document.body.append(detail);
      window.renderEvidenceInspector=async row=>{window.inspectedRevision=row.version;};
    }''')
    open_editor(edit_page)
    edit_page.wait_for_selector('#structured-record-text')
    edit_page.click('#save-structured-record')
    edit_page.wait_for_function('() => window.graphRefreshes===1')
    assert edit_page.evaluate('() => window.inspectedRevision||null') == 5


def test_older_refresh_cannot_replace_a_newer_saved_revision(edit_page):
    edit_page.evaluate('''() => {
      const detail=document.createElement('section');detail.id='graph-detail';
      detail.dataset.recordId='e1';detail.innerHTML='<section class="evidence-identity"></section>';
      document.body.append(detail);window.graphWaiters=[];
      window.drawGraph=()=>new Promise(resolve=>window.graphWaiters.push(resolve));
      window.renderEvidenceInspector=async row=>{window.inspectedRevision=row.version;};
    }''')
    open_editor(edit_page)
    edit_page.wait_for_selector('#structured-record-text')
    edit_page.click('#save-structured-record')
    edit_page.wait_for_function('() => window.graphWaiters.length===1')
    edit_page.evaluate("() => {document.getElementById('record-advanced-edit').open=true;}")
    edit_page.click('#revise')
    edit_page.wait_for_function('() => window.graphWaiters.length===2')
    edit_page.evaluate('() => window.graphWaiters[1]()')
    edit_page.wait_for_function('() => window.inspectedRevision===6')
    edit_page.evaluate('() => window.graphWaiters[0]()')
    edit_page.wait_for_function("() => !document.getElementById('save-structured-record').disabled")
    assert edit_page.evaluate('() => window.inspectedRevision') == 6
    assert '修订 r6' in edit_page.inner_text('#record-dialog-message')


def test_old_ontology_load_failure_cannot_replace_new_revision_editor(edit_page):
    edit_page.evaluate('''() => {
      const previous=window.fetch;let held=false;
      window.fetch=async(input,options)=>{
        if(!held&&String(input).includes('/ontology?')){
          held=true;return new Promise(resolve=>{
            window.releaseOldOntology=()=>resolve(new Response('{"error":"old load failed"}',{status:500}));
          });
        }
        return previous(input,options);
      };
    }''')
    open_editor(edit_page)
    edit_page.wait_for_function('() => Boolean(window.releaseOldOntology)')
    edit_page.evaluate("() => {document.getElementById('record-advanced-edit').open=true;}")
    edit_page.click('#revise')
    edit_page.wait_for_selector('#structured-record-text')
    assert edit_page.input_value('#revision-version') == '5'
    edit_page.evaluate('() => window.releaseOldOntology()')
    # Both fetch continuations settle before the next animation frame.
    edit_page.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    assert edit_page.query_selector('#structured-record-text') is not None
    assert 'old load failed' not in edit_page.inner_text('#record-structured-edit')
