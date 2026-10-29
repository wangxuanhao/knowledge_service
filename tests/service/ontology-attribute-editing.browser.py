"""Offline production UI with deterministic draft commands; never writes business data."""
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'knowledge_service' / 'web'
OUTPUT = ROOT / '.benchmarks' / 'ontology-attributes'


def check(width):
    html = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', '',
                  (WEB / 'index.html').read_text(encoding='utf-8'), flags=re.I)

    def style(match):
        name = re.search(r'href="/assets/([^"?]+\.css)', match.group())
        return '<style>' + (WEB / name[1]).read_text(encoding='utf-8') + '</style>' if name else ''

    html = re.sub(r'<link\b[^>]*>', style, html, flags=re.I)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True,
            executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            args=['--disable-gpu'], timeout=20000)
        page = browser.new_page(viewport={'width': width, 'height': 1000}, reduced_motion='reduce')
        page.set_default_timeout(8000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', lambda route: route.abort())
        page.set_content(html, wait_until='domcontentloaded')
        page.evaluate('''() => {
          window.current='p';window.endpoint=suffix=>'/api/projects/p'+suffix;
          document.documentElement.classList.remove('auth-pending');document.body.classList.add('is-admin');
          document.querySelectorAll('main>.tab').forEach(tab=>tab.classList.toggle('hidden',tab.id!=='tab-ontology-model'));
          window.notices=[];window.status=(...args)=>notices.push(args);window.wb={clearVersionContext(){},setVersionContext(){}};
          const rdfs='http://www.w3.org/2000/01/rdf-schema#',xsd='http://www.w3.org/2001/XMLSchema#';
          window.fixtureSummary={classes:[{id:'urn:test:base',label_zh:'基础对象'},
            {id:'urn:test:product',label_zh:'商品',parents:['urn:test:base']},{id:'urn:test:order',label_zh:'订单'}],relations:[],
            attributes:[{id:'urn:test:name',label_zh:'名称',domain:['urn:test:base'],range:[xsd+'string'],annotations:[]},
              {id:'urn:test:stock',label_zh:'库存',description:'当前库存',domain:['urn:test:product','urn:test:order'],range:[xsd+'integer'],
                annotations:[{predicate:rdfs+'label',value:'库存',language:'zh'}, {predicate:rdfs+'comment',value:'当前库存'}]},
              {id:'urn:test:code',label_zh:'编码',domain:[],range:[xsd+'string'],annotations:[]}]};
          window.writes=[];window.failWrite=false;
          window.api=async url=>url.endsWith('/ontologies')?{versions:[]}:{};
        }''')
        for name in ['vendor/cytoscape.min.js', 'ontology-model-catalog.js', 'ontology-model-attributes.js',
                     'ontology-model.js', 'ontology-model-canvas.js', 'ontology-model-panel.js']:
            page.add_script_tag(content=(WEB / name).read_text(encoding='utf-8'))
        page.evaluate('''() => {
          const M=OntologyModel,rdfs='http://www.w3.org/2000/01/rdf-schema#';
          Object.assign(M.S,{summary:fixtureSummary,ontologyId:'published',view:'types',draft:{id:'draft',status:'pending',revision:1}});
          window.fixtureRevision=1;
          const draft=()=>({id:'draft',status:'pending',revision:fixtureRevision,ontology:structuredClone(fixtureSummary)});
          const apply=command=>{
            if(failWrite)throw Error('模拟保存失败');writes.push(structuredClone(command));
            for(const c of command.commands||[command]){
              let term=fixtureSummary.attributes.find(a=>a.id===c.target_iri);
              if(c.action==='create_term'){term={id:c.target_iri,domain:[],range:[],annotations:[]};fixtureSummary.attributes.push(term);}
              if(c.action==='add_domain')term.domain=[...new Set([...term.domain,c.value])];
              if(c.action==='remove_domain')term.domain=term.domain.filter(id=>id!==c.value);
              if(c.action==='set_datatype')term.range=c.datatype?[c.datatype]:[];
              if(c.action==='add_annotation')term.annotations.push({predicate:c.predicate,value:c.value,...(c.language?{language:c.language}:{})});
              if(c.action==='remove_annotation')term.annotations=term.annotations.filter(a=>!(a.predicate===c.predicate&&a.value===c.value&&a.language===c.language));
              term.label_zh=term.annotations.find(a=>a.predicate===rdfs+'label'&&a.language==='zh')?.value||term.label_zh;
              term.description=term.annotations.find(a=>a.predicate===rdfs+'comment')?.value||'';
            }
            fixtureSummary=structuredClone(fixtureSummary);fixtureRevision++;
          };
          window.api=async(url,body)=>{
            if(url.endsWith('/commands')){apply(body.command);return draft();}
            if(url.endsWith('/stage-availability')||url.endsWith('/publish-readiness'))return {};
            if(url.endsWith('/ontology-drafts'))return {items:[draft()]};
            if(url.includes('/ontology-drafts/draft'))return draft();
            if(url.endsWith('/ontology'))return {id:'published',summary:structuredClone(fixtureSummary)};
            if(url.endsWith('/ontologies'))return {versions:[]};
            return {};
          };
          M.select('urn:test:product');
        }''')
        inspector = page.locator('#om-detail-host')
        assert '基础对象' in inspector.locator('.om-inherited-attributes').inner_text()
        assert '库存' in inspector.locator('.om-attributes').inner_text()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        page.locator('.om-inspector-shell').screenshot(path=str(OUTPUT / f'type-properties-{width}.png'))

        inspector.get_by_role('button', name='添加属性', exact=True).click()
        assert inspector.locator('select').nth(1).input_value() == 'urn:test:product'
        inspector.get_by_role('textbox', name='中文名', exact=True).fill('发布日期')
        inspector.get_by_label('属性数据类型', exact=True).select_option('http://www.w3.org/2001/XMLSchema#date')
        inspector.get_by_label('新属性说明', exact=True).fill('商品公布日期')
        inspector.get_by_role('button', name='新建并写入草案', exact=True).click()
        try:
            inspector.locator('.om-attribute-list').get_by_role('button', name='发布日期', exact=True).wait_for()
        except Exception:
            print(json.dumps(page.evaluate('({notices,writes,errors:OntologyModel.S.error,detail:document.getElementById("om-detail-host").innerText})'), ensure_ascii=False), flush=True)
            raise
        creation = page.evaluate('writes[0].commands')
        assert any(c['action'] == 'set_datatype' and c['datatype'].endswith('#date') for c in creation)
        assert any(c['action'] == 'add_annotation' and c['value'] == '商品公布日期' for c in creation)

        picker = inspector.locator('.om-attribute-picker')
        picker.locator(':scope > summary').click()
        picker.get_by_role('checkbox', name='库存', exact=False).uncheck()
        picker.get_by_role('checkbox', name='编码', exact=False).check()
        picker.get_by_role('button', name='应用关联', exact=True).click()
        page.wait_for_function('writes.length===2')
        assert page.evaluate("fixtureSummary.attributes.find(a=>a.id==='urn:test:stock').domain") == ['urn:test:order']
        assert page.evaluate('writes[1].commands') == [
            {'action':'remove_domain','target_iri':'urn:test:stock','value':'urn:test:product'},
            {'action':'add_domain','target_iri':'urn:test:code','value':'urn:test:product'}]

        page.evaluate("OntologyModel.select('urn:test:stock')")
        editor = inspector.locator('.om-attribute-editor')
        editor.locator(':scope > summary').click()
        editor.get_by_label('属性名称', exact=True).fill('可用库存')
        editor.get_by_label('属性说明', exact=True).fill('扣除锁定后的库存')
        page.evaluate('failWrite=true')
        editor.get_by_role('button', name='保存名称与说明', exact=True).click()
        assert '模拟保存失败' in editor.locator('[role=status]').inner_text()
        assert editor.get_by_label('属性名称', exact=True).input_value() == '可用库存'
        page.locator('.om-inspector-shell').screenshot(path=str(OUTPUT / f'edit-property-{width}.png'))
        page.evaluate('failWrite=false')
        editor.get_by_role('button', name='保存名称与说明', exact=True).click()
        page.wait_for_function("fixtureSummary.attributes.find(a=>a.id==='urn:test:stock').label_zh==='可用库存'")

        page.evaluate("OntologyModel.S.preview={id:'history'};OntologyModel.select('urn:test:product')")
        assert not inspector.get_by_role('button', name='添加属性', exact=True).count()
        assert not inspector.locator('.om-attribute-picker').count()
        assert '名称' in inspector.locator('.om-inherited-attributes').inner_text()
        page.evaluate("OntologyModel.select('urn:test:stock')")
        assert not inspector.locator('.om-attribute-editor').count()
        assert inspector.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors, errors
        browser.close()
        return {'width': width, 'checks': 'create, datatype, description, ownership, inheritance, metadata retry, read-only, containment'}


if __name__ == '__main__':
    for width in [1280, 390]:
        print(json.dumps(check(width), ensure_ascii=False), flush=True)
