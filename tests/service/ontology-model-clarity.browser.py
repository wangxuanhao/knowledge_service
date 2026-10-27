"""Offline checks of the production ontology canvas and inspector; no backend writes.

Run with the existing llm_model Python environment, without pytest/PG fixtures.
"""
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'knowledge_service' / 'web'
OUTPUT = ROOT / '.benchmarks' / 'ontology-clarity'


def check_inheritance(page, width):
    inspector = page.locator('#om-detail-host')
    measurements = []
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for iri, state in [('urn:thing', 'empty'), ('urn:person', 'existing')]:
        page.evaluate('(iri)=>OntologyModel.select(iri)', iri)
        editor = inspector.locator('.om-constraint-edit')
        editor.locator(':scope > summary').click()
        picker = editor.locator('.om-type-picker')
        picker.scroll_into_view_if_needed()
        measurements.append({'state': state, 'picker': picker.bounding_box(),
            'summary': picker.locator(':scope > summary').bounding_box()})
        page.screenshot(path=str(OUTPUT / f'parents-{state}-{width}.png'), full_page=True)
    print(json.dumps({'inheritance_measurements':measurements}), flush=True)
    for item in measurements:
        assert item['picker']['height'] <= item['summary']['height'] + 4, 'a closed picker must not leave blank content below its caption'
    # Existing parent names must be visible without opening a nested selector.
    page.evaluate("OntologyModel.select('urn:person')")
    editor = inspector.locator('.om-constraint-edit')
    assert '业务对象' in editor.locator(':scope > summary').inner_text(), 'the collapsed editor must display existing parents'
    editor.locator(':scope > summary').click()
    picker = editor.locator('.om-type-picker')
    picker.locator(':scope > summary').click()
    assert picker.get_by_role('checkbox', name='业务对象', exact=True).is_checked()
    assert picker.locator('input[value="urn:person"]').count() == 0, 'a class cannot inherit itself'
    picker.get_by_role('checkbox', name='通知', exact=True).check()
    picker.get_by_role('checkbox', name='业务对象', exact=True).uncheck()
    picker.get_by_role('button', name='应用选择', exact=True).click()
    page.wait_for_function('writes.length===1')
    assert page.evaluate('writes[0]') == {'action':'batch','commands':[
        {'action':'remove_parent','target_iri':'urn:person','value':'urn:thing'},
        {'action':'add_parent','target_iri':'urn:person','value':'urn:notice'},
    ]}
    assert '通知' in editor.locator(':scope > summary').inner_text(), 'parent preview follows the successfully applied selection'
    assert inspector.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
    return {'width':width,'checks':'inheritance empty spacing, existing parents, checked values, add/remove parents'}


def check_relations(page, width):
    inspector = page.locator('#om-detail-host')
    page.evaluate("OntologyModel.select('urn:works')")
    editor = inspector.locator('.om-constraint-edit')
    heading = editor.locator(':scope > summary')
    assert '人员' in heading.inner_text() and '组织' in heading.inner_text()
    assert editor.locator('.om-endpoint-summary').is_visible()
    heading.click()
    for index, iri in [(0, 'urn:person'), (1, 'urn:org')]:
        picker = editor.locator('.om-type-picker').nth(index)
        assert picker.bounding_box()['height'] <= picker.locator(':scope > summary').bounding_box()['height'] + 4
        picker.locator(':scope > summary').click()
        assert picker.locator(f'input[value="{iri}"]').is_checked()
        picker.locator(':scope > summary').click()
    print(f'{width}: relationship preview and picker spacing passed', flush=True)

    page.evaluate('''() => {
      const original=window.api;
      fixtureSummary.classes.find(c=>c.id==='urn:person').label_zh='平台规则';
      fixtureSummary.classes.find(c=>c.id==='urn:org').label_zh='法律法规';
      fixtureSummary.relations[0].domain=[];fixtureSummary.relations[0].range=[];
      window.fixtureUsage={used:4,source:'published',declared:{domain:[],range:[]},
        inferred:{domain:[{iri:'urn:person',label:'平台规则',count:4}],range:[{iri:'urn:org',label:'法律法规',count:4}]},
        missing:{domain:true,range:true},samples:[
          {subject:'美团侵权投诉须知',subject_type:'平台规则',object:'《中华人民共和国电子商务法》',object_type:'法律法规'},
          {subject:'美团侵权投诉须知',subject_type:'平台规则',object:'《中华人民共和国民法典》',object_type:'法律法规'},
          {subject:'美团侵权投诉须知',subject_type:'平台规则',object:'《信息网络传播权保护条例》',object_type:'法律法规'},
        ]};
      window.api=async url=>url.includes('/ontology/relation-usage')?structuredClone(fixtureUsage):original(url);
      OntologyModel.select('urn:works');
    }''')
    recommendation = inspector.locator('.om-endpoints')
    recommendation.locator(':scope > summary').click()
    recommendation.locator('.om-usage-head').wait_for()
    assert '4 条知识' in recommendation.locator('.om-usage-head').inner_text()
    sides = recommendation.locator('.om-usage-side')
    assert '平台规则' in sides.nth(0).inner_text() and '4 次' in sides.nth(0).inner_text()
    assert '法律法规' in sides.nth(1).inner_text() and '4 次' in sides.nth(1).inner_text()
    evidence = recommendation.locator('.om-usage-samples')
    assert not evidence.evaluate('e=>e.open')
    assert not evidence.locator('.om-usage-sample').first.is_visible()
    assert page.evaluate('writes.length') == 0
    OUTPUT.mkdir(parents=True, exist_ok=True)
    page.locator('.om-inspector-shell').screenshot(path=str(OUTPUT / f'recommendation-compact-{width}.png'))
    evidence.locator(':scope > summary').click()
    assert evidence.locator('.om-usage-sample').count() == 3
    assert '《信息网络传播权保护条例》' in evidence.inner_text()
    assert inspector.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
    page.locator('.om-inspector-shell').screenshot(path=str(OUTPUT / f'recommendation-evidence-{width}.png'))
    recommendation.get_by_role('button', name='将推荐写入草案', exact=True).click()
    page.wait_for_function('writes.length===2')
    commands = page.evaluate('writes')
    assert [(cmd['action'], cmd['target_iri'], cmd['value']) for cmd in commands] == [
        ('add_domain', 'urn:works', 'urn:person'), ('add_range', 'urn:works', 'urn:org')]
    page.evaluate("fixtureSummary.relations[0].domain=['urn:person'];fixtureSummary.relations[0].range=['urn:org'];OntologyModel.S.preview={id:'history'};OntologyModel.select('urn:works')")
    assert inspector.locator('.om-type-picker, .om-usage-apply').count() == 0
    assert '平台规则' in inspector.inner_text() and '法律法规' in inspector.inner_text(), 'historical endpoint declarations remain readable'
    return {'width':width,'checks':'current endpoints, preselection, closed spacing, recommendation counts, folded evidence, explicit draft commands, history'}


def check(width=1600, inheritance_only=False, relations_only=False):
    html = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', '',
                  (WEB / 'index.html').read_text(encoding='utf-8'), flags=re.I)

    def style(match):
        name = re.search(r'href="/assets/([^"?]+\.css)', match.group())
        return '<style>' + (WEB / name[1]).read_text(encoding='utf-8') + '</style>' if name else ''

    html = re.sub(r'<link\b[^>]*>', style, html, flags=re.I)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True,
            executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            args=['--disable-gpu', '--disable-accelerated-2d-canvas'], timeout=20000)
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
          window.status=()=>{};window.wb={clearVersionContext(){},setVersionContext(){}};
          window.fixtureSummary={classes:[
            {id:'urn:thing',label_zh:'业务对象',description:'业务模型中的基础对象。'},
            {id:'urn:person',label_zh:'人员',parents:['urn:thing'],description:'参与业务活动的人员。'},
            {id:'urn:staff',label_zh:'员工',parents:['urn:person']},
            {id:'urn:org',label_zh:'组织',parents:['urn:thing']},
            {id:'urn:notice',label_zh:'通知',parents:[]},
          ],relations:[{id:'urn:works',label_zh:'任职于',domain:['urn:person'],range:['urn:org']}],
          attributes:[{id:'urn:name',label_zh:'姓名',domain:['urn:person'],range:['http://www.w3.org/2001/XMLSchema#string']}]};
          window.writes=[];window.failWrite=false;
          window.api=async url=>url.includes('relation-usage')?{declared:{domain:[],range:[]},inferred:{domain:[],range:[]},missing:{}}:
            url.endsWith('/ontologies')?{versions:[]}:{};
        }''')
        for name in ['vendor/cytoscape.min.js', 'ontology-model-catalog.js', 'ontology-model.js', 'ontology-model-canvas.js', 'ontology-model-panel.js']:
            page.add_script_tag(content=(WEB / name).read_text(encoding='utf-8'))
        page.evaluate('''() => {
          Object.assign(OntologyModel.S,{summary:fixtureSummary,ontologyId:'published',view:'types'});
          OntologyModel.select('urn:thing');OntologyModel.select(null);
          OntologyModel.sendCommand=async command=>{if(failWrite)throw Error('模拟提交失败');writes.push(command);};
        }''')
        if inheritance_only:
            result = check_inheritance(page, width)
            assert not errors, errors
            browser.close()
            return result
        if relations_only:
            result = check_relations(page, width)
            assert not errors, errors
            browser.close()
            return result
        # Help is absent in the old UI: this also makes the visual simplification red-capable.
        page.evaluate("OntologyModel.select('urn:person')")
        help_button = page.locator('#om-detail-host .om-help-button').first
        assert help_button.count(), 'field explanations need an adjacent question-mark control'
        help_button.hover()
        tooltip = page.locator('#om-detail-host [role=tooltip]:visible').first
        tooltip.wait_for(state='visible')
        assert tooltip.inner_text().strip()
        tip_box = tooltip.bounding_box()
        assert tip_box['x'] >= 0 and tip_box['x'] + tip_box['width'] <= width
        help_button.focus()
        help_button.press('Escape')
        assert not tooltip.is_visible()
        help_button.blur()
        help_button.focus()
        assert page.locator('#om-detail-host [role=tooltip]:visible').count() == 1
        help_button.press('Escape')
        notes = page.locator('#om-detail-host > .om-detail-card > .om-detail-sec > .om-detail-note').all_text_contents()
        assert not any('做什么' in note for note in notes), 'routine field explanations are in help popovers'
        print(f'{width}: help controls passed', flush=True)

        # Actual node click must travel through the canonical controller and move the camera.
        page.evaluate("OntologyModel.select(null);OntologyModel.setInspector(false);OntologyCanvas.__cy().$id('urn:person').position({x:750,y:500});OntologyCanvas.__cy().zoom(.55);OntologyCanvas.__cy().pan({x:-90,y:0})")
        canvas = page.locator('.om-canvas-stage')
        canvas.scroll_into_view_if_needed()
        point = page.evaluate("OntologyCanvas.__cy().$id('urn:person').renderedPosition()")
        rect = canvas.bounding_box()
        page.mouse.click(rect['x'] + point['x'], rect['y'] + point['y'])
        page.wait_for_function("OntologyModel.S.selected==='urn:person'")
        assert page.locator('#tab-ontology-model').evaluate("e=>e.classList.contains('om-inspector-open')")
        assert page.evaluate("OntologyCanvas.__cy().$id('urn:person').hasClass('is-focus')")
        assert page.evaluate("OntologyCanvas.__cy().$id('urn:notice').hasClass('is-muted')")
        focused = page.evaluate('''() => {const cy=OntologyCanvas.__cy(),node=cy.$id('urn:person');
          return {point:node.renderedPosition(),width:cy.width(),height:cy.height(),zoom:cy.zoom(),
            card:node.boundingBox(),inspector:document.getElementById('om-inspector-shell').getBoundingClientRect().toJSON()};}''')
        assert focused['zoom'] > .55, focused
        if width > 1280:
            assert abs(focused['point']['x'] - focused['width'] / 2) < 2
        print(f'{width}: node click and camera passed', flush=True)

        page.evaluate("OntologyModel.select('urn:works')")
        inspector = page.locator('#om-detail-host')
        editor = inspector.locator('.om-constraint-edit')
        editor.locator('summary').first.click()
        picker = editor.locator('.om-type-picker').first
        picker.locator('summary').click()
        picker.get_by_placeholder('搜索实体类型').fill('员工')
        picker.get_by_role('checkbox', name='员工', exact=True).check()
        picker.get_by_placeholder('搜索实体类型').fill('')
        picker.get_by_role('checkbox', name='人员', exact=True).uncheck()
        assert page.evaluate('writes.length') == 0, 'selection changes stay local until Apply'
        page.evaluate('failWrite=true')
        picker.get_by_role('button', name='应用选择', exact=True).click()
        picker.get_by_text('模拟提交失败', exact=True).wait_for()
        assert page.evaluate('writes.length') == 0
        assert picker.get_by_role('checkbox', name='员工', exact=True).is_checked()
        page.evaluate('failWrite=false')
        picker.get_by_role('button', name='应用选择', exact=True).click()
        page.wait_for_function('writes.length===1')
        assert page.evaluate('writes[0]') == {'action':'batch','commands':[
            {'action':'remove_domain','target_iri':'urn:works','value':'urn:person'},
            {'action':'add_domain','target_iri':'urn:works','value':'urn:staff'},
        ]}
        assert picker.get_by_role('button', name='应用选择', exact=True).is_disabled()
        picker.get_by_role('button', name='应用选择', exact=True).dispatch_event('click')
        assert page.evaluate('writes.length') == 1, 'unchanged selection cannot create another command'
        print(f'{width}: type selection and retry passed', flush=True)
        OUTPUT.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(OUTPUT / f'type-picker-{width}.png'), full_page=True)

        # Persisted unknown/inactive endpoint declarations must survive selecting another type.
        page.evaluate("fixtureSummary.relations[0].range=['urn:legacy'];OntologyModel.select('urn:works')")
        inspector.locator('.om-constraint-edit > summary').click()
        target = inspector.locator('.om-type-picker').nth(1)
        target.locator('summary').click()
        assert target.locator('input[type=checkbox][value="urn:legacy"]').is_checked()
        target.get_by_role('checkbox', name='组织', exact=True).check()
        target.get_by_role('button', name='应用选择', exact=True).click()
        page.wait_for_function('writes.length===2')
        assert page.evaluate('writes[1].commands') == [
            {'action':'add_range','target_iri':'urn:works','value':'urn:org'}]
        page.evaluate("OntologyModel.S.preview={id:'history'};OntologyModel.select('urn:works')")
        assert inspector.locator('.om-type-picker').count() == 0, 'historical definitions stay read-only'
        page.evaluate("OntologyModel.S.preview=null;fixtureSummary.relations[0].range=['urn:org'];OntologyModel.select(null);OntologyModel.setInspector(false)")
        page.get_by_role('button', name='重新布局', exact=True).click()
        page.evaluate("OntologyModel.select('urn:person')")
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert inspector.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
        if width <= 1280:
            assert inspector.evaluate('e=>e.clientHeight>e.parentElement.clientHeight-100'), 'drawer details use the available height'
        assert not inspector.locator('.om-retire').evaluate('e=>e.open'), 'lifecycle actions start collapsed'
        page.screenshot(path=str(OUTPUT / f'ontology-{width}.png'), full_page=True)
        assert not errors, errors
        browser.close()
        return {'width':width,'console_errors':errors,'checks':'focus, help, type selection, retry, legacy values, history, containment'}


if __name__ == '__main__':
    import sys
    parent_mode = '--parents' in sys.argv
    relation_mode = '--relations' in sys.argv
    sizes = [int(arg) for arg in sys.argv[1:] if arg not in ('--parents', '--relations')] or [1600, 1280, 390]
    for viewport in sizes:
        print(json.dumps(check(viewport, inheritance_only=parent_mode, relations_only=relation_mode), ensure_ascii=False), flush=True)
