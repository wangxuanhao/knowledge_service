import json

from test_frontend_retrieval_flow import browser, workbench  # noqa: F401


def test_qa_formatting_state_history_and_responsive_layout(workbench, tmp_path):
    page = workbench.page
    page.click('[data-tab="qa"]')
    assert page.locator('.qa-page-heading h2').inner_text() == '知识问答'
    assert page.locator('.qa-hero').evaluate('(el)=>getComputedStyle(el).display') == 'block'
    assert not page.locator('#qa-as-of').is_visible()
    page.locator('.qa-temporal-options summary').click()
    assert page.locator('#qa-as-of').is_visible()
    page.locator('.qa-temporal-options summary').click()
    answer = '# 价格说明\n\n**团购价：** 商品销售价 [U1]\n- 外卖价\n<img src=x onerror="window.qaInjected=true">'
    generation = {'requested': True, 'called': False,
                  'skip_reason': 'no_eligible_evidence', 'material_status': 'unverified'}
    events = [('evidence', {'answer_id': 'qa-format', 'evidence': [],
               'uncertain_references': [{'citation': 'U1', 'kind': 'chunk', 'text': '价格说明',
                                         'provenance_ref': 'frozen', 'usage': 'qualified_quote'}]}),
              ('done', {'answer_id': 'qa-format', 'answer': answer, 'mode': 'evidence_only',
                        'generation': generation, 'timings_ms': {'total': 877, 'retrieval': 605}})]
    stream = ''.join('event: '+name+'\ndata: '+json.dumps(data, ensure_ascii=False)+'\n\n'
                     for name, data in events)
    page.route('**/qa/stream', lambda route: route.fulfill(body=stream, content_type='text/event-stream'))
    page.check('#generate')
    page.fill('#qa-query', '美团的价格有哪些定义？')
    page.click('#qa')
    page.wait_for_selector('.qa-turn-assistant[data-state="done"]')
    assert page.locator('.qa-answer h3').inner_text() == '价格说明'
    assert page.locator('.qa-answer strong').inner_text() == '团购价：'
    assert page.locator('.qa-answer img').count() == 0
    assert not page.evaluate('Boolean(window.qaInjected)')
    assert page.locator('.qa-answer button.provenance-citation').count() == 1
    assert '未调用模型' in page.locator('.qa-answer-status').inner_text()
    assert '877 ms' in page.locator('.qa-answer-status').inner_text()
    assert not page.locator('.qa-evidence-panel').is_visible()
    page.locator('.qa-evidence-toggle').click()
    assert page.locator('.qa-evidence-panel').is_visible()
    page.locator('.qa-evidence-toggle').click()
    for width in [1200, 768, 390]:
        page.set_viewport_size({'width': width, 'height': 850})
        assert page.locator('#qa-composer').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    page.reload(wait_until='load')
    page.click('[data-tab="qa"]')
    page.wait_for_selector('.qa-answer-status')
    assert '未调用模型' in page.locator('.qa-answer-status').inner_text()
    assert '877 ms' in page.locator('.qa-answer-status').inner_text()
    assert not workbench.errors
