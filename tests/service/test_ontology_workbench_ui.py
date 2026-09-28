"""Real-browser contracts for the unified ontology DAG editor."""
from pathlib import Path

import pytest


EDGE = Path(r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
pytest.importorskip('playwright', reason='real browser contract requires Playwright')
if not EDGE.exists():
    pytest.skip('system Edge is required for the real browser contract', allow_module_level=True)

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'

SHELL = """<!doctype html><html><body>
<aside><nav><button data-tab="ontology">本体管理</button><button data-tab="ontology-workbench">本体工作台</button></nav></aside>
<main><h1 id="title"></h1><section id="scope"></section></main>
<select id="project"><option value="p1" selected>测试项目</option></select>
</body></html>"""

MOCKS = r"""() => {
  window.apiCalls = [];
  const draft = {id:'d1',title:'DAG 草案',status:'editing',revision:2,
    source_kind:'manual',base_ontology_id:'o1',operations:[],decisions:[]};
  window.mockDraft = draft;
  const root = iri => ({id:iri,iri,canonical_iri:iri,name:iri.split(':').pop(),
    label:iri.split(':').pop(),label_zh:'',child_count:1,other_parent_count:0,
    is_reference:false,display_path:[{iri,label:iri.split(':').pop()}]});
  const child = parent => ({id:'urn:Child',iri:'urn:Child',canonical_iri:'urn:Child',
    name:'Child',label:'Child',label_zh:'子类',child_count:0,other_parent_count:1,
    is_reference:true,parents:['urn:RootA','urn:RootB'],
    display_path:[{iri:parent,label:parent.split(':').pop()},{iri:'urn:Child',label:'Child'}]});
  window.fetch = async (url, options={}) => {
    const parsed = new URL(url, 'http://local.test');
    window.apiCalls.push({url:parsed.pathname + parsed.search, method:options.method||'GET',
      body:options.body ? JSON.parse(options.body) : null});
    let payload = {};
    if(parsed.pathname.endsWith('/ontology-discovery')) payload={candidate_count:0,entity_count:0,relation_count:0,attribute_count:0};
    else if(parsed.pathname.endsWith('/candidate-mindmap')) payload={nodes:[],edges:[],summary:{}};
    else if(parsed.pathname.endsWith('/ontology-drafts')) payload={items:[draft],total:1};
    else if(parsed.pathname.endsWith('/ontology-drafts/d1/commands')) {
      draft.revision += 1; draft.operations.push({id:'op'+draft.revision,
        fingerprint:'fp'+draft.revision,risk:'low',source:'manual',
        ...window.apiCalls.at(-1).body.command}); payload={...draft};
    }
    else if(parsed.pathname.endsWith('/ontology-drafts/d1/validate')) {
      draft.validation_report = window.mockValidation || {conforms:true,
        graph_integrity:{conforms:true,errors:[]},
        prospective_new_write_contract:{conforms:true,errors:[]},
        historical_impact:{checked_records:7,nonconforming_records:0,errors:[]},
        errors:[],warnings:[],info:[]};
      draft.validation_fingerprint = window.mockFingerprint || 'vf1'; payload={...draft};
    }
    else if(parsed.pathname.endsWith('/ontology-drafts/d1/submit')) {
      draft.status='submitted';draft.revision+=1;
      draft.validation_report = window.mockValidation || {conforms:true,
        graph_integrity:{conforms:true,errors:[]},
        prospective_new_write_contract:{conforms:true,errors:[]},
        historical_impact:{checked_records:7,nonconforming_records:0,errors:[]},
        errors:[],warnings:[],info:[]};
      draft.validation_fingerprint=window.mockFingerprint||'vf1';payload={...draft};
    }
    else if(parsed.pathname.endsWith('/ontology-drafts/d1/decisions')) {
      draft.revision+=1;draft.decisions.push(...window.apiCalls.at(-1).body.decisions);
      const decided=new Set(draft.decisions.filter(x=>['approve','reject'].includes(x.action)).map(x=>x.operation_id));
      draft.status=window.apiCalls.at(-1).body.decisions.some(x=>x.action==='request_changes')?'editing':(decided.size===draft.operations.length?'reviewed':'submitted');payload={...draft};
    }
    else if(parsed.pathname.endsWith('/ontology-drafts/d1/publish')) {
      draft.status='published';payload={id:'o2',project_id:'p1',draft_id:'d1',created_at:'2026-09-24T01:02:03Z'};
    }
    else if(parsed.pathname.endsWith('/ontology-drafts/d1')) payload={...draft};
    else if(parsed.pathname.endsWith('/ontology-hierarchy/roots')) payload={items:[root('urn:RootA'),root('urn:RootB')],next_cursor:null};
    else if(parsed.pathname.endsWith('/ontology-hierarchy/children')) payload={items:[child(parsed.searchParams.get('iri'))],next_cursor:null};
    else if(parsed.pathname.endsWith('/ontology-hierarchy/search')) payload={items:[child('urn:RootA')],next_cursor:null};
    else if(parsed.pathname.endsWith('/ontology-hierarchy/neighborhood')) payload={term:child('urn:RootA'),items:[root('urn:RootA'),root('urn:RootB')],relations:[],attributes:[],next_cursor:null};
    else if(parsed.pathname.endsWith('/ontology-matrix')) payload={items:[{iri:'urn:rel',canonical_iri:'urn:rel',kind:'relation',label:'关联',domain:['urn:RootA','urn:RootB'],range:['urn:Child']}],next_cursor:null};
    else if(parsed.pathname.endsWith('/ontology/term-impact')) payload={record_count:3,constraint_count:2,pending_review_count:1,linked_relation_count:2};
    else if(parsed.pathname.endsWith('/ontologies')) payload={versions:[{id:'o0',created_at:'2026-01-01T00:00:00Z'},{id:'o1',created_at:'2026-02-01T00:00:00Z'}]};
    return new Response(JSON.stringify(payload),{status:200,headers:{'Content-Type':'application/json'}});
  };
}"""


@pytest.fixture()
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=str(EDGE))
        context = browser.new_context(viewport={'width': 1500, 'height': 1000})
        context.add_init_script(f"({MOCKS})()")
        pg = context.new_page()
        errors = []
        pg.on('pageerror', lambda error: errors.append(str(error)))
        pg.set_content(SHELL)
        pg.add_style_tag(path=str(WEB / 'style.css'))
        pg.add_style_tag(path=str(WEB / 'ontology-workbench.css'))
        pg.add_script_tag(path=str(WEB / 'ontology-workbench.js'))
        pg._errors = errors
        yield pg
        browser.close()


def open_design(page):
    page.click('[data-tab="ontology-workbench"]')
    page.evaluate("() => OntologyWorkbench.setStage('design')")
    page.wait_for_selector('[data-hierarchy-row="urn:RootA"]')


def test_hierarchy_is_lazy_paginated_and_multi_parent_selection_is_shared(page):
    open_design(page)
    calls = page.evaluate("() => apiCalls.map(x => x.url)")
    assert any('/ontology-hierarchy/roots?' in call for call in calls)
    assert not any('/ontology-hierarchy/children?' in call for call in calls)

    page.click('[data-expand-iri="urn:RootA"]')
    page.click('[data-expand-iri="urn:RootB"]')
    assert page.locator('[data-hierarchy-row="urn:Child"]').count() == 2
    page.locator('[data-hierarchy-row="urn:Child"]').first.click()
    assert page.locator('[data-hierarchy-row="urn:Child"][aria-current="true"]').count() == 2
    inspector = page.locator('#ontology-workbench-inspector').inner_text()
    assert 'RootA' in inspector and 'Child' in inspector
    assert '另有 1 个父级' in inspector
    assert page._errors == []


def test_search_and_matrix_use_paged_endpoints_not_full_summary(page):
    open_design(page)
    page.fill('#ontology-workbench-search', 'Child')
    page.click('#ontology-workbench-search-button')
    page.wait_for_selector('[data-hierarchy-row="urn:Child"]')
    page.click('[data-workbench-mode="matrix"]')
    page.wait_for_selector('[data-matrix-row="urn:rel"]')
    calls = page.evaluate("() => apiCalls.map(x => x.url)")
    assert any('/ontology-hierarchy/search?' in call and 'limit=50' in call for call in calls)
    assert any('/ontology-matrix?' in call and 'limit=50' in call for call in calls)
    assert not any('/ontology?' in call for call in calls)


def test_object_editor_sends_revisioned_multi_parent_and_constraint_commands(page):
    open_design(page)
    page.click('[data-new-object]')
    page.select_option('#ontology-object-kind', 'relation')
    page.fill('#ontology-object-iri', 'urn:worksFor')
    page.fill('#ontology-object-label-zh', '任职于')
    page.fill('#ontology-object-label-en', 'works for')
    page.fill('#ontology-object-domain', 'urn:RootA\nurn:RootB')
    page.fill('#ontology-object-range', 'urn:Child')
    page.click('#ontology-object-save')
    page.wait_for_function("() => apiCalls.filter(x => x.url.endsWith('/commands')).length >= 6")
    commands = page.evaluate("() => apiCalls.filter(x => x.url.endsWith('/commands')).map(x => x.body)")
    actions = [item['command']['action'] for item in commands]
    assert actions[:6] == ['create_term','add_domain','add_domain','add_range','add_annotation','add_annotation']
    assert [item['expected_revision'] for item in commands[:6]] == [2,3,4,5,6,7]


def test_retire_preview_and_restore_require_explicit_source_version(page):
    open_design(page)
    page.click('[data-expand-iri="urn:RootA"]')
    page.locator('[data-hierarchy-row="urn:Child"]').first.click()
    page.click('[data-preview-retire]')
    page.wait_for_selector('[data-retire-confirm]')
    text = page.locator('#ontology-workbench-inspector').inner_text()
    assert '3 条正式知识' in text and '2 条约束' in text
    page.click('[data-open-restore]')
    page.wait_for_selector('#ontology-restore-version')
    assert page.locator('#ontology-restore-version option').count() == 2
    assert page.locator('#ontology-restore-fields input[type="checkbox"]:checked').count() == 0


def prepare_review(page, operations, warnings=None):
    open_design(page)
    page.evaluate("([operations,warnings]) => { mockDraft.operations=operations; mockDraft.decisions=[]; mockDraft.status='editing'; mockValidation={conforms:true,graph_integrity:{conforms:true,errors:[]},prospective_new_write_contract:{conforms:true,errors:[]},historical_impact:{checked_records:7,nonconforming_records:0,errors:[]},errors:[],warnings:warnings||[],info:[]}; }", [operations, warnings or []])
    page.evaluate("() => OntologyWorkbench.selectDraft('d1')")
    page.wait_for_selector('[data-submit-review]:not([disabled])')
    page.click('[data-submit-review]')
    page.wait_for_selector('[data-review-operation]')


def operation(id_, risk='low', action='add_annotation'):
    return {'id': id_, 'fingerprint': 'fp-'+id_, 'action': action,
            'target_iri': 'urn:'+id_, 'risk': risk, 'source': 'manual',
            'before': {'value': '旧值'}, 'after': {'value': '新值'},
            'impact': {'formal_records': 0},
            'validation': {'warnings': []},
            'evidence': [{'document_title': '依据.pdf', 'evidence': '原文证据'}]}


def test_review_shortcuts_autosave_and_reasons_are_enforced(page):
    prepare_review(page, [operation('low'), operation('high','high','retire_term')])
    page.locator('[data-review-operation]').first.click()
    page.keyboard.press('a')
    page.wait_for_function("() => apiCalls.some(x => x.url.endsWith('/decisions'))")
    low = page.evaluate("() => apiCalls.filter(x => x.url.endsWith('/decisions')).at(-1).body.decisions[0]")
    assert low['action'] == 'approve' and not low.get('reason')

    page.keyboard.press('j')
    assert page.locator('[data-review-operation][aria-current="true"]').get_attribute('data-operation-id') == 'high'
    page.keyboard.press('a')
    page.wait_for_selector('#ontology-decision-reason')
    assert page.locator('[data-save-decision]').is_disabled()
    page.fill('#ontology-decision-reason', '已核对历史影响')
    assert page.locator('[data-save-decision]').is_enabled()
    page.click('[data-save-decision]')
    page.wait_for_function("() => apiCalls.filter(x => x.url.endsWith('/decisions')).length === 2")


def test_batch_approve_only_visible_no_warning_low_risk(page):
    prepare_review(page, [operation('a'), operation('b'), operation('c','medium')])
    assert page.locator('[data-batch-approve]').is_visible()
    assert page.locator('[data-review-operation][data-batch-eligible="true"]').count() == 2
    page.click('[data-batch-approve]')
    page.wait_for_function("() => apiCalls.some(x => x.url.endsWith('/decisions'))")
    decisions = page.evaluate("() => apiCalls.filter(x => x.url.endsWith('/decisions')).at(-1).body.decisions")
    assert len(decisions) == 2 and all(item['action'] == 'approve' for item in decisions)


def test_validation_sections_publish_acknowledgement_and_provenance_link(page):
    warning = {'code':'range_widened','severity':'warning','message':'Range 扩大','operation_ids':['low']}
    prepare_review(page, [operation('low')], [warning])
    page.locator('[data-review-operation]').first.click()
    page.keyboard.press('a')
    page.fill('#ontology-decision-reason', '已核对警告')
    page.check('[data-ack-warning="range_widened"]')
    page.click('[data-save-decision]')
    page.wait_for_function("() => mockDraft.status === 'reviewed'")
    page.click('[data-workbench-stage="validate"]')
    page.wait_for_selector('[data-validation-section="graph"]')
    assert page.locator('[data-validation-section]').count() == 3
    assert 'vf1' in page.locator('#ontology-workbench-canvas-content').inner_text()
    page.click('[data-workbench-stage="publish"]')
    page.check('[data-publish-warning="range_widened"]')
    page.fill('#ontology-publisher', 'owner@example.test')
    page.click('[data-publish-draft]')
    page.wait_for_selector('[data-published-version="o2"]')
    assert '草案 d1' in page.locator('#ontology-workbench-canvas-content').inner_text()
