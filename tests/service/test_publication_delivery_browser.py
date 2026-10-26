"""Offline production-asset acceptance: saved version, independent sync and lazy history."""
import json
import os
from pathlib import Path
import re

import pytest

WEB=Path(__file__).resolve().parents[2]/'knowledge_service'/'web'


def check_publication_delivery(width, output=None):
    runtime=pytest.importorskip('playwright.sync_api')
    executable=os.environ.get('KG_BROWSER_EXECUTABLE',r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
    if not Path(executable).is_file():
        pytest.skip('Requires system Edge or KG_BROWSER_EXECUTABLE')
    html=re.sub(r'<script\b[^>]*>[\s\S]*?</script>','',(WEB/'index.html').read_text(encoding='utf-8'),flags=re.I)
    def style(match):
        name=re.search(r'href="/assets/([^"?]+\.css)',match.group())
        return '<style>'+(WEB/name[1]).read_text(encoding='utf-8')+'</style>' if name else ''
    html=re.sub(r'<link\b[^>]*>',style,html,flags=re.I)
    with runtime.sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=executable)
        page=browser.new_page(viewport={'width':width,'height':1000},reduced_motion='reduce')
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('**/*',lambda route:route.abort())
        page.set_content(html,wait_until='domcontentloaded')
        page.evaluate('''() => {
          window.current='p';window.endpoint=suffix=>'/api/projects/'+current+suffix;window.status=()=>{};
          window.wb={clearVersionContext(){},setVersionContext(){}};
          document.documentElement.classList.remove('auth-pending');document.body.classList.add('is-admin');
          document.querySelectorAll('main>.tab').forEach(tab=>tab.classList.toggle('hidden',tab.id!=='tab-ontology-model'));
          window.OntologyCanvas={mount(){},render(){},resize(){},setView(){},setDisplay(){}};
          window.fixtureSummary={classes:Array.from({length:34},(_,i)=>({id:'urn:Type:'+i,label_zh:'业务类型 '+(i+1)})),relations:Array.from({length:26},(_,i)=>({id:'urn:Relation:'+i,label_zh:'业务关系 '+(i+1)})),attributes:[]};
          window.fixtureDraft={id:'draft',revision:6,status:'pending',submitted_at:'now',validation_fingerprint:'fp',base_ontology_id:null,ontology:fixtureSummary,
            operations:Array.from({length:188},(_,i)=>({id:'op-'+i,action:'add_annotation',target_iri:'urn:Type:'+i%34,after:{value:'定义说明'}})),decisions:[],associated_knowledge:{selected:118}};
          window.fixtureVersion={id:'v1',version:1,created_at:'2026-10-26T04:00:00Z',summary:fixtureSummary,note:'确认本轮定义与知识',metadata:{version:1,publication:{draft_id:'draft',note:'确认本轮定义与知识',actor:'审核人',write_path:'governed_publish',receipt:{facts:{selected:118,added:118,reused:0,skipped:0,per_batch:[{run_id:'frozen-source-run',selected:118,added:118,reused:0,skipped:0,candidate_outcomes:Array.from({length:118},(_,i)=>({candidate_id:'candidate-'+i,status:'materialized',reason_code:'materialized'}))}]},index:{status:'pending',task_id:'background-index'}}}}};
          window.fixtureCalls=[];window.saved=false;window.waitSync=true;window.failSync=false;window.syncResolvers=[];
          window.finishSync=()=>{waitSync=false;syncResolvers.splice(0).forEach(resolve=>resolve());};
          window.api=async(url,body,method='GET')=>{
            fixtureCalls.push({url,body,method});url=url.replace('?view=review','').replace('&view=review','');
            if(url.endsWith('/ontologies'))return {versions:saved?[fixtureVersion]:[]};
            if(url.endsWith('/publish-readiness'))return {ready:true,revision:6,validation_fingerprint:'fp',approval_counts:{approved:188},items:[]};
            if(url.endsWith('/publish'))return new Promise(resolve=>window.finishPublish=()=>{saved=true;fixtureDraft={...fixtureDraft,status:'accepted',revision:7,published_ontology_id:'v1'};resolve(structuredClone(fixtureVersion));});
            if(saved&&(url.endsWith('/ontology')||url.endsWith('/ontology-drafts'))){if(waitSync)await new Promise(resolve=>syncResolvers.push(resolve));if(failSync)throw Error('模拟页面同步读取失败');}
            if(url.endsWith('/ontology'))return fixtureVersion;
            if(url.endsWith('/ontology-drafts'))return {items:saved?[]:[fixtureDraft]};
            if(url.includes('/ontology-drafts/draft'))return fixtureDraft;
            if(url.includes('/knowledge-context'))return {ontology:{is_current:true},current_bound:{total:118,by_kind:{entity:65,relation:53,attribute:0}},migration:{pending:0,blocked:0},history:{revision_count:0,record_count:0,migrated_away:0}};
            if(url.includes('/ontology-discovery'))return {published:true};
            if(url.includes('/stage-availability'))return {stages:{review:{allowed:true}}};
            throw Error('Unexpected offline route '+url);
          };
        }''')
        for name in ['ontology-draft-data.js','ontology-model-catalog.js','ontology-draft-history.js','ontology-model-workspace.js','ontology-model.js','ontology-model-panel.js']:
            page.add_script_tag(content=(WEB/name).read_text(encoding='utf-8'))
        page.evaluate('''() => {
          Object.assign(OntologyModel.S,{summary:fixtureSummary,draft:structuredClone(fixtureDraft),showingDraft:true,view:'cards',stageAvailability:{stages:{review:{allowed:true}}},readiness:{ready:true,revision:6,validation_fingerprint:'fp',items:[],approval_counts:{approved:188}}});
          OntologyModel.render();OntologyModel.openWorkspace('definitions');
        }''')
        controls=page.locator('#om-workspace-controls')
        controls.locator('.om-pub-note').fill('确认本轮定义与知识')
        controls.get_by_role('button',name=re.compile('^发布')).click()
        page.wait_for_function('typeof window.finishPublish===\'function\'')
        assert page.locator('#om-actions-host').get_attribute('aria-busy')=='true'
        page.evaluate('finishPublish()')
        banner=page.locator('#om-publication-feedback')
        banner.get_by_text('已发布为 v1 · 正式版本已生效',exact=True).wait_for()
        assert '页面正在同步' in banner.inner_text()
        assert '2026' in banner.inner_text() and '118' in banner.inner_text()
        assert page.evaluate('OntologyModel.S.draft===null && OntologyModel.S.ontologyId===\'v1\'')
        assert page.locator('#om-actions-host').get_attribute('aria-busy')=='false'
        assert page.evaluate("fixtureCalls.filter(c=>c.method==='POST').length")==1
        assert not page.evaluate("fixtureCalls.some(c=>c.method==='GET'&&/ontology-drafts\\/draft(?:\\?|$)/.test(c.url)&&!c.url.includes('readiness'))")
        if output:
            output.mkdir(parents=True,exist_ok=True)
            page.locator('#tab-ontology-model').screenshot(path=str(output/f'{width}-saved.png'))
        banner.get_by_role('button',name='查看发布记录',exact=True).click()
        history=page.locator('[aria-label="版本管理"]')
        history.get_by_text('本次发布实际处理的关联知识',exact=True).wait_for()
        assert history.locator('.om-publication-outcome').count()==0
        assert not page.evaluate("fixtureCalls.some(c=>c.url.includes('knowledge-context'))")
        assert not history.locator('.om-history-technical').evaluate('e=>e.open')
        assert history.locator('.om-history-actions').evaluate('e=>{const b=e.getBoundingClientRect();return b.bottom<innerHeight&&b.top>=0}')
        assert history.locator('.om-history-panel').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
        if output:
            history.screenshot(path=str(output/f'{width}-history.png'))
        history.locator('.om-publication-batch>summary').click()
        assert history.locator('.om-publication-outcome').count()==118
        assert history.locator('.om-publication-outcomes').evaluate('e=>e.clientHeight<=281&&e.scrollHeight>e.clientHeight')
        history.locator('.om-history-knowledge>summary').click()
        history.get_by_text('当前关联',exact=True).wait_for()
        history.get_by_role('button',name='关闭版本管理',exact=True).click()
        page.evaluate('failSync=true;finishSync()')
        banner.get_by_role('button',name='重试页面同步',exact=True).wait_for()
        assert '已发布为 v1' in banner.inner_text()
        page.evaluate('failSync=false')
        banner.get_by_role('button',name='重试页面同步',exact=True).click()
        page.wait_for_function("OntologyModel.S.publication.refreshState==='complete'")
        assert '页面已同步' in banner.inner_text()
        assert page.evaluate("fixtureCalls.filter(c=>c.method==='POST').length")==1
        assert not errors,errors
        browser.close()
    return {'width':width,'candidate_details_default':0,'candidate_details_on_demand':118,'real_business_writes':0}


@pytest.mark.parametrize('width',[1440,900,390])
def test_publication_delivery_browser(width):
    check_publication_delivery(width)


if __name__=='__main__':
    for width in [1440,900,390]:
        print(json.dumps(check_publication_delivery(width,WEB.parents[1]/'.benchmarks/publication-delivery')),flush=True)
