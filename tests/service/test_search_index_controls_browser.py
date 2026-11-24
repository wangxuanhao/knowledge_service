"""Real browser wiring for relevance settings and the shared index panel."""
import pytest
from playwright.sync_api import expect
from test_workspace_redesign_ui import redesign_page


@pytest.mark.parametrize('width', [390, 1440])
def test_relevance_requests_and_shared_index_panel_fit_the_page(redesign_page, width):
    page, errors = redesign_page
    page.set_viewport_size({'width':width,'height':950})
    page.evaluate("KnowledgeTabs.open('sources')")
    page.locator('#source-search-input').fill('退款')
    with page.expect_request(lambda r:'/sources?' in r.url and r.method=='POST') as pending:
        page.locator('[data-mode="hybrid"]').click()
    assert pending.value.post_data_json['hybrid_strategy']=='keyword_rerank'
    page.locator('.source-relevance-settings > summary').click()
    with page.expect_request(lambda r:'/sources?' in r.url and r.method=='POST') as pending:
        page.select_option('#source-hybrid-strategy','fusion')
    assert pending.value.post_data_json['hybrid_strategy']=='fusion'
    with page.expect_request(lambda r:'/sources?' in r.url and r.method=='POST') as pending:
        page.locator('#source-min-semantic-score').fill('.65')
        page.locator('#source-min-semantic-score').press('Tab')
    assert pending.value.post_data_json['min_semantic_score']==.65
    page.route('**/indexes/status',lambda route:route.fulfill(json={'state':'ready','automatic':True,
        'applied_seq':5,'watermark':5,
        'record_counts':{'chunk':3,'entity':2},'indexed_record_counts':{'chunk':3,'entity':2}}))
    trigger=page.locator('#tab-sources [data-search-index]')
    trigger.click()
    panel=page.locator('.search-index-dialog')
    panel.get_by_text('3 条已索引 / 3 条当前记录',exact=True).wait_for()
    box=panel.bounding_box()
    assert box['x']>=0 and box['x']+box['width']<=width+1
    page.keyboard.press('Escape')
    assert not panel.count()
    assert trigger.evaluate('(node)=>document.activeElement===node')
    page.evaluate("KnowledgeTabs.open('qa')")
    page.locator('#qa-settings > summary').click()
    assert page.locator('#qa-hybrid-strategy').is_visible()
    assert page.locator('#qa-min-semantic-score').is_visible()
    assert not errors


def test_ontology_and_search_show_when_the_project_vectors_are_ready(redesign_page):
    page, errors = redesign_page
    index = {'state':'ready','automatic':True,'applied_seq':7,'watermark':7}
    page.route('**/indexes/status',lambda route:route.fulfill(json=index))
    page.evaluate("KnowledgeTabs.open('ontology-model')")
    indicator = page.locator('#om-vector-index-state [data-search-index]')
    expect(indicator).to_have_text('向量检索可用')
    index.update(state='building',watermark=10,job={'id':'job','status':'running','stage':'正在计算向量','progress':40})
    page.evaluate("document.dispatchEvent(new CustomEvent('ontology-model:published',{detail:{projectId:'p'}}))")
    expect(indicator).to_have_text('向量索引同步中')
    indicator.click()
    panel = page.locator('.search-index-dialog')
    panel.get_by_text('正在计算向量',exact=True).wait_for()
    assert panel.locator('progress').evaluate('(node)=>node.value') == 40
    page.keyboard.press('Escape')
    index.update(state='ready',applied_seq=10,job=None)
    expect(indicator).to_have_text('向量检索可用',timeout=12000)
    page.evaluate("KnowledgeTabs.open('search')")
    expect(page.locator('#tab-search [data-search-index]')).to_have_text('向量检索可用')
    assert not errors
