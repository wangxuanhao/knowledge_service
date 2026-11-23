import pytest
from test_workspace_redesign_ui import redesign_page
from test_chunk_preview_ui import browser
from pathlib import Path


@pytest.mark.parametrize('width',[390,1440])
def test_keyword_locations_and_semantic_explanation_are_distinct(redesign_page,width):
    page,errors=redesign_page
    page.set_viewport_size({'width':width,'height':950})
    def directory(route):
        semantic='retrieval_mode=semantic' in route.request.url
        match={'mode':'semantic' if semantic else 'keyword','channels':['semantic' if semantic else 'keyword'],
            'fields':[] if semantic else ['title','body'],'query':'退款',
            'text':'款项原路退回。' if semantic else '申请退款前核对订单。',
            **({'score': .86} if semantic else {})}
        route.fulfill(json={'documents':[{'id':'d','metadata':{'title':'退款办理规定','source_file':'rules.md','status':'ready'},'match':match}],
            'total':1,'counts':{'all':1,'ready':1,'failed':0,'processing':0}})
    page.route('**/api/projects/p/sources**',directory)
    page.evaluate("KnowledgeTabs.open('sources')")
    page.locator('#source-search-input').fill('退款');page.locator('#source-search-input').press('Enter')
    page.wait_for_function("document.querySelector('.source-match-labels')?.textContent.includes('正文命中')")
    assert '关键词匹配 1' in page.locator('.source-match-labels').inner_text()
    assert page.locator('.source-item__name mark').inner_text()=='退款'
    page.locator('[data-mode="semantic"]').click()
    page.wait_for_function("document.querySelector('.source-item__match')?.dataset.matchMode==='semantic'")
    assert page.locator('.source-item__match mark').count()==0
    assert '语义相关' in page.locator('.source-match-labels').inner_text()
    assert '语义相似度 0.86' in page.locator('.source-match-labels').inner_text()
    assert '款项原路退回' in page.locator('.source-item__match').inner_text()
    box=page.locator('.source-item__match').bounding_box()
    assert box['x']>=0 and box['x']+box['width']<=width+1
    page.locator('.source-relevance-settings > summary').click()
    page.select_option('#source-hybrid-strategy','fusion')
    assert page.locator('#source-hybrid-strategy option:checked').inner_text()=='扩展相近含义'
    assert '可能补充' in page.locator('#source-relevance-help').inner_text()
    assert not errors


def test_semantic_reader_uses_context_not_exact_evidence_marks(browser):
    page=browser.new_page();page.set_default_timeout(5000)
    doc={'original_name':'rules.md','version':1,'sha256':'hash','assets':[],
        'blocks':[{'block_id':'b','type':'paragraph','normalized_text':'款项原路退回。'}],'chunks':[]}
    def reply(route):
        if route.request.url.endswith('/rendered'):route.fulfill(content_type='text/html',body='<p data-block-id="b">款项原路退回。</p>')
        elif '/source-documents/' in route.request.url:route.fulfill(json=doc)
        else:route.fulfill(content_type='text/html',body='<body></body>')
    page.route('http://match.test/**',reply);page.goto('http://match.test/')
    web=Path(__file__).resolve().parents[2]/'knowledge_service'/'web'
    for file in ('source-content.js','source-reader.js'):page.add_script_tag(path=str(web/file))
    page.evaluate("SourceReader.open({projectId:'p',versionId:'v',matchKind:'semantic',location:{status:'available',precision:'evidence',tab:'reading',spans:[{block_id:'b',block_start_char:0,block_end_char:7}]}})")
    page.locator('.source-reader-body .source-context').wait_for()
    assert page.locator('.source-reader-body mark').count()==0
    assert '非逐字命中' in page.locator('.source-reader-note').inner_text()
    page.close()
