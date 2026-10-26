"""Production review UI with offline APIs; no business writes or model calls."""
import json
import os
import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'


def review_browser_check(width, output=None):
    sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
    executable = os.environ.get('KG_BROWSER_EXECUTABLE',
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
    if not Path(executable).is_file():
        pytest.skip('Requires system Edge or KG_BROWSER_EXECUTABLE')

    html = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', '',
                  (WEB / 'index.html').read_text(encoding='utf-8'), flags=re.I)

    def inline_style(match):
        name = re.search(r'href="/assets/([^"?]+\.css)', match.group())
        return '<style>' + (WEB / name[1]).read_text(encoding='utf-8') + '</style>' if name else ''

    html = re.sub(r'<link\b[^>]*>', inline_style, html, flags=re.I)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={'width': width, 'height': 1000}, reduced_motion='reduce')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', lambda route: route.abort())
        page.set_content(html, wait_until='domcontentloaded', timeout=60000)
        page.evaluate('''() => {
          window.current='p';window.endpoint=suffix=>'/api/projects/'+current+suffix;
          document.documentElement.classList.remove('auth-pending');document.body.classList.add('is-admin');
          document.querySelectorAll('main>.tab').forEach(tab=>tab.classList.toggle('hidden',tab.id!=='tab-ontology-model'));
          window.status=()=>{};window.wb={clearVersionContext(){},setVersionContext(){}};
          window.OntologyCanvas={mount(){},render(){},resize(){},setView(){},setDisplay(){}};
          const classes=Array.from({length:34},(_,i)=>({id:'urn:entity:'+i,label_zh:'实体类型 '+(i+1)}));
          const relations=Array.from({length:26},(_,i)=>({id:'urn:relation:'+i,label_zh:'关系定义 '+(i+1),domain:[classes[i].id],range:[classes[(i+1)%34].id]}));
          window.fixtureSummary={classes,relations,attributes:[]};
          const operations=[...classes,...relations].flatMap((term,i)=>[
            {id:'op-'+i+'-create',action:'create_term',target_iri:term.id,after:{kind:i<34?'class':'relation'}},
            {id:'op-'+i+'-name',action:'add_annotation',target_iri:term.id,after:{predicate:'http://www.w3.org/2000/01/rdf-schema#label',value:term.label_zh}},
            {id:'op-'+i+'-description',action:'add_annotation',target_iri:term.id,after:{predicate:'http://www.w3.org/2000/01/rdf-schema#comment',value:'核对业务定义的含义与适用范围。'}}
          ]);
          for(let i=0;i<8;i++)operations.push({id:'parent-'+i,action:'add_parent',target_iri:classes[i].id,after:{parent:classes[(i+1)%34].id}});
          operations[0].evidence=[{quote:'资料中的定义原文'},'document-version:1'];
          window.fixtureDraft={id:'working',status:'pending',revision:5,submitted_at:'now',validation_fingerprint:'fp',base_ontology_id:'release',ontology:fixtureSummary,operations};
          window.fixturePlan={revision:5,validation_fingerprint:'fp',warnings:[],items:operations.map(op=>({operation_id:op.id,target_iri:op.target_iri,action:op.action,approvable:true,auto_approvable:true}))};
          fixturePlan.items[0].reason_codes=['medium_risk'];fixturePlan.items[0].impact={affected_records:6};
          window.fixtureCalls=[];window.delayReadiness=true;window.delayWrite=false;window.failWrite=false;window.fixtureGate={allowed:true};
          window.compactDraft=draft=>{
            const copy=structuredClone(draft),sets={};
            copy.operations=copy.operations.map(operation=>{const refs=operation.evidence||[],id=JSON.stringify(refs);sets[id]=refs;
              delete operation.evidence;return {...operation,evidence_set_id:id,evidence_count:refs.length};});
            return {...copy,evidence_sets:sets};
          };
          window.api=async(url,body,method='GET')=>{
            fixtureCalls.push({url,body,method});const reviewView=url.includes('view=review');url=url.replace('?view=review','').replace('&view=review','');
            if(url.endsWith('/ontologies'))return {versions:[]};
            if(url.includes('/stage-availability'))return {stages:{review:fixtureGate}};
            if(url.endsWith('/review-plan'))return structuredClone(fixturePlan);
            if(url.includes('/publish-readiness')){
              if(delayReadiness)await new Promise(resolve=>window.finishReadiness=resolve);
              return {ready:false,requires_full_validation:false,approval_counts:{approved:0},items:[]};
            }
            if(method==='POST'){
              if(failWrite)throw Error('模拟连接中断');
              if(delayWrite)await new Promise(resolve=>window.finishWrite=resolve);
              fixtureDraft={...fixtureDraft,revision:fixtureDraft.revision+1};
              fixturePlan={...fixturePlan,revision:fixtureDraft.revision,items:fixturePlan.items.map(item=>
                !item.decision&&(body.target_iri===item.target_iri||url.endsWith('/batch-approve')&&item.auto_approvable)
                  ?{...item,decision:{action:body.action||'approve',reason:body.reason}}:item)};
              return {draft:reviewView?compactDraft(fixtureDraft):structuredClone(fixtureDraft),batch:{approved:188,skipped:0}};
            }
            if(url.endsWith('/ontology-drafts/working'))return reviewView?compactDraft(fixtureDraft):structuredClone(fixtureDraft);
            if(url.endsWith('/ontology-drafts'))return {items:[fixtureDraft]};
            if(url.endsWith('/ontology'))return {id:'release',summary:fixtureSummary};
            if(url.endsWith('/ontology-discovery'))return {published:true};
            throw Error('Unexpected offline route: '+url);
          };
        }''')
        for name in ['ontology-draft-data.js', 'ontology-model-catalog.js', 'ontology-draft-history.js', 'ontology-model-workspace.js',
                     'ontology-model.js', 'ontology-review-workspace.js', 'ontology-model-panel.js']:
            page.add_script_tag(content=(WEB / name).read_text(encoding='utf-8'))
        page.evaluate('''() => {
          Object.assign(OntologyModel.S,{summary:fixtureSummary,draft:structuredClone(fixtureDraft),showingDraft:true,ontologyId:'release',view:'cards'});
          OntologyModel.render();OntologyModel.openWorkspace('definitions');void OntologyModel.refreshDraftState();
        }''')
        controls = page.locator('#om-workspace-controls')
        bulk = controls.get_by_role('button', name='批量批准（188 项变更）', exact=True)
        bulk.wait_for(state='visible')
        assert bulk.is_enabled(), 'review cannot wait for publication readiness'
        assert page.evaluate('OntologyModel.S.gatesLoading')
        assert controls.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
        page.evaluate('delayReadiness=false;finishReadiness()')
        controls.get_by_role('button', name='按定义审核', exact=True).click()
        dialog = page.locator('.om-definition-review')
        dialog.locator('.odr-row').first.wait_for()
        assert dialog.locator('.odr-row').count() == 60
        assert '188 项变更' in dialog.locator('.odr-summary').inner_text()
        assert not page.evaluate("fixtureCalls.some(c=>c.method==='POST')"), 'opening review must be read-only'
        assert dialog.locator('.odr-technical').get_attribute('open') is None
        assert dialog.get_by_text('资料中的定义原文', exact=True).is_visible()
        assert dialog.get_by_text('证据引用：document-version:1', exact=True).is_visible()
        assert '中风险变更' in dialog.locator('.odr-risk-reasons').inner_text()
        assert '受影响记录：6' in dialog.locator('.odr-impact').inner_text()
        for selector in ['.odr-footer', '.odr-body', '.odr-detail', '.odr-toolbar']:
            assert dialog.locator(selector).evaluate('e=>e.scrollWidth<=e.clientWidth+1'), selector
        assert dialog.locator('.odr-footer').evaluate('e=>{const b=e.getBoundingClientRect();return b.top>=0&&b.bottom<=innerHeight}')
        if output:
            output.mkdir(parents=True, exist_ok=True)
            dialog.screenshot(path=str(output / f'{width}-review.png'))
        # Free-text Enter cannot approve; approved-and-next is a single definition command.
        dialog.get_by_role('textbox', name='处理理由').fill('已核对定义')
        dialog.get_by_role('textbox', name='处理理由').press('Enter')
        assert not page.evaluate("fixtureCalls.some(c=>c.method==='POST')")
        dialog.get_by_role('button', name='批准并下一项', exact=True).click()
        page.wait_for_function("document.querySelector('.odr-detail h3')?.textContent==='实体类型 2'")
        assert page.evaluate("fixtureCalls.filter(c=>c.method==='POST').length") == 1
        assert page.evaluate("fixtureCalls.find(c=>c.method==='POST').body.target_iri") == 'urn:entity:0'
        # Completed definitions are inspectable without exposing a second approval.
        dialog.get_by_label('审核范围').select_option('completed')
        assert dialog.locator('.odr-row').count() == 1
        assert dialog.get_by_role('button', name='批准此定义', exact=True).is_disabled()
        dialog.get_by_label('审核范围').select_option('pending')
        # A genuinely uncertain result forces a reload before retrying.
        page.evaluate('failWrite=true')
        dialog.get_by_role('button', name='批准并下一项', exact=True).click()
        page.wait_for_function("document.querySelector('.odr-feedback').textContent.includes('未确认')")
        assert dialog.get_by_role('button', name='批准并下一项', exact=True).is_disabled()
        page.evaluate('failWrite=false')
        dialog.get_by_role('button', name='重新读取', exact=True).click()
        page.wait_for_function("document.querySelector('.om-definition-review').getAttribute('aria-busy')==='false'")
        assert dialog.get_by_role('button', name='批准并下一项', exact=True).is_enabled()
        # Server flags control high risk, warnings, and blocked approval.
        page.evaluate('''() => {
          fixturePlan.warnings=[{code:'scope_warning',message:'请确认此定义对关联知识的影响'}];
          fixturePlan.items=fixturePlan.items.map(item=>item.target_iri==='urn:entity:1'?{...item,risk:'high',requires_reason:true,auto_approvable:false,
            warnings:[{code:'scope_warning',message:item.operation_id.endsWith('create')?'涉及类型定义':'涉及名称或说明'}]}:item);
        }''')
        dialog.get_by_role('button', name='重新读取', exact=True).click()
        dialog.get_by_text('高风险变更：请核对影响并填写理由。').first.wait_for()
        assert '涉及类型定义' in dialog.locator('.odr-warning').inner_text()
        assert '涉及名称或说明' in dialog.locator('.odr-warning').inner_text()
        assert dialog.get_by_role('checkbox').count() == 1
        before = page.evaluate("fixtureCalls.filter(c=>c.method==='POST').length")
        dialog.get_by_role('button', name='批准并下一项', exact=True).click()
        assert '理由' in dialog.locator('.odr-feedback').inner_text()
        dialog.get_by_role('textbox', name='处理理由').fill('审核通过，已核对影响范围')
        dialog.get_by_role('button', name='批准并下一项', exact=True).click()
        assert '警告' in dialog.locator('.odr-feedback').inner_text()
        assert page.evaluate("fixtureCalls.filter(c=>c.method==='POST').length") == before
        dialog.get_by_role('checkbox').check()
        dialog.get_by_role('button', name='批准并下一项', exact=True).click()
        page.wait_for_function("document.querySelector('.odr-detail h3')?.textContent==='实体类型 3'")
        page.evaluate("fixturePlan.items=fixturePlan.items.map(i=>i.target_iri==='urn:entity:2'?{...i,approvable:false,auto_approvable:false,errors:[{code:'blocked',message:'仍有依赖未解决'}]}:i)")
        dialog.get_by_role('button', name='重新读取', exact=True).click()
        dialog.get_by_text('仍有依赖未解决', exact=True).first.wait_for()
        assert dialog.get_by_role('button', name='批准此定义', exact=True).is_disabled()
        assert dialog.get_by_role('button', name='不批准此定义', exact=True).is_enabled()
        page.evaluate("fixtureGate={allowed:false,reason:'草案基线已过期，请重新基线后再审核。'}")
        dialog.get_by_role('button', name='重新读取', exact=True).click()
        dialog.get_by_text('草案基线已过期，请重新基线后再审核。', exact=True).wait_for()
        assert dialog.get_by_role('button', name='不批准此定义', exact=True).is_disabled()
        assert not errors, errors
        browser.close()
    return {'width': width, 'definitions': 60, 'operations': 188, 'real_api_writes': 0}


@pytest.mark.parametrize('width', [1440, 900, 390])
def test_definition_review_browser(width):
    review_browser_check(width)


if __name__ == '__main__':
    output = WEB.parents[1] / '.benchmarks' / 'definition-review'
    for viewport in [1440, 900, 390]:
        print(json.dumps(review_browser_check(viewport, output)), flush=True)
