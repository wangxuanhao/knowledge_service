"""Community discovery and citation navigation in the full workspace shell."""

import re
from pathlib import Path

import pytest

from test_workspace_redesign_ui import redesign_page  # noqa: F401


@pytest.fixture
def postgres_backend():
    """These routes serve isolated API fixtures."""


@pytest.fixture
def qa_history(redesign_page):
    from knowledge_service.services.qa_conversations import sanitize_messages

    page, _ = redesign_page
    sessions = {}

    def conversations(route):
        method = route.request.method
        path = route.request.url.split('?', 1)[0]
        if path.endswith('/conversations'):
            if method == 'GET':
                return route.fulfill(json={'conversations': list(sessions.values()), 'total': len(sessions)})
            key = f'conversation-{len(sessions) + 1}'
            sessions[key] = {'id': key, 'title': '新会话', 'version': 1, 'messages': [], 'message_count': 0}
            return route.fulfill(status=201, json={'conversation': sessions[key]})
        session = sessions[path.rsplit('/', 1)[-1]]
        if method == 'PUT':
            body = route.request.post_data_json
            if body['expected_version'] != session['version']:
                return route.fulfill(status=409, json={'detail': '会话版本冲突'})
            messages = sanitize_messages('p', body['messages'])
            session.update(messages=messages, message_count=len(messages), version=session['version'] + 1,
                           title=next((item['text'] for item in messages if item['kind'] == 'user'), '新会话'))
        return route.fulfill(json={'conversation': session})

    page.route(re.compile(r'/api/projects/p/qa/conversations(?:/[^?]+)?(?:\?.*)?$'), conversations)
    return sessions


def test_community_question_clears_on_submit_and_preserves_drafts(redesign_page, qa_history):
    expect = pytest.importorskip('playwright.sync_api').expect
    page, errors = redesign_page
    status = {'state': 'ready', 'browseable': True, 'revision': 'r1', 'counts': {'reports_ready': 1}}
    page.route('**/api/projects/p/communities', lambda route: route.fulfill(json={
        'status': status, 'communities': [],
    }))
    pending = []
    page.route('**/api/projects/p/communities/ask', lambda route: pending.append(route))

    page.locator('[data-tab="qa"]').click()
    page.get_by_role('button', name='社区问答', exact=True).click()
    questions = page.locator('#community-questions')
    question = questions.get_by_role('textbox', name='社区问答问题')
    send = questions.get_by_role('button', name='发送社区问题')
    bubble = questions.locator('.qa-turn-user').last
    expect(send).to_be_enabled()

    def begin_request(text, *, use_enter=False):
        question.fill(text)
        with page.expect_request('**/api/projects/p/communities/ask') as request:
            if use_enter:
                question.press('Enter')
            else:
                send.click()
        assert request.value.post_data_json['question'] == text.strip()
        expect(bubble).to_have_text(text.strip())
        expect(send).to_be_disabled()
        expect(question).to_have_value('', timeout=1000)
        return pending.pop()

    first = begin_request('退款如何处理？', use_enter=True)
    question.fill('退款如何处理？')
    first.fulfill(json={'answer': '按退款规则执行。', 'generated': True, 'evidence': []})
    expect(questions.get_by_text('按退款规则执行。')).to_be_visible()
    expect(question).to_have_value('退款如何处理？')

    failed = begin_request('  退款需要什么材料？  ')
    failed.fulfill(status=503, json={'detail': '暂时无法查询社区报告'})
    expect(questions.locator('#community-ask-status')).to_contain_text('暂时无法查询社区报告')
    expect(question).to_have_value('  退款需要什么材料？  ')

    failed = begin_request('退款需要什么材料？')
    question.fill('下一个问题')
    failed.fulfill(status=503, json={'detail': '暂时无法查询社区报告'})
    expect(send).to_be_enabled()
    expect(question).to_have_value('下一个问题')
    assert not errors


def test_community_stream_and_knowledge_share_a_persistent_conversation(redesign_page, qa_history):
    expect = pytest.importorskip('playwright.sync_api').expect
    page, errors = redesign_page
    status = {'state': 'ready', 'browseable': True, 'revision': 'r1', 'counts': {'reports_ready': 1},
              'question_planning': True}
    page.route('**/api/projects/p/communities', lambda route: route.fulfill(json={'status': status, 'communities': []}))
    page.route('**/api/projects/p/qa/stream', lambda route: route.fulfill(content_type='text/event-stream',
        body='event: done\ndata: {"answer":"知识依据回答","answer_id":"qa-1"}\n\n'))
    page.evaluate('''() => {
      const original = window.fetch;
      window.fetch = (url, options) => {
        if (!url.endsWith('/communities/ask')) return original(url, options);
        window.communityAccept = options.headers.Accept;
        window.communityRequest = JSON.parse(options.body);
        return Promise.resolve(new Response(new ReadableStream({start(controller) {
          window.emitCommunity = (name, data) => controller.enqueue(new TextEncoder().encode(
            'event: ' + name + '\\ndata: ' + JSON.stringify(data) + '\\n\\n'));
        }}), {headers:{'content-type':'text/event-stream'}}));
      };
    }''')
    page.locator('[data-tab="qa"]').click()
    page.locator('#qa-query').fill('知识依据问题')
    page.locator('#qa').click()
    expect(page.locator('#qa-transcript .qa-answer')).to_contain_text('知识依据回答')
    page.get_by_role('button', name='社区问答', exact=True).click()
    expect(page.get_by_role('heading', name='会话记录')).to_be_visible()
    questions = page.locator('#community-questions')
    questions.get_by_role('checkbox', name='生成模型答案').check()
    questions.get_by_role('textbox', name='社区问答问题').fill('社区问题')
    questions.get_by_role('button', name='发送社区问题').click()
    page.wait_for_function('() => Boolean(window.emitCommunity)')
    assert page.evaluate('window.communityAccept') == 'text/event-stream'
    assert page.evaluate('window.communityRequest.plan') is True
    assert page.evaluate('window.communityRequest.mode') == 'hybrid'
    answer = questions.locator('.qa-turn-assistant')
    expect(answer).to_contain_text('正在查找社区报告')
    page.evaluate("() => emitCommunity('progress',{stage:'planning'})")
    expect(answer).to_contain_text('正在分析问题并制定检索计划')
    page.evaluate('''() => emitCommunity('evidence',{subquestion:{number:1,question:'退款限制',evidence_ids:['E-aa1','E-aa2']},evidence:[
      {id:'E-aa1',kind:'document',title:'依据',quote:'不可退款条款',source_platforms:['抖音电商']},
      {id:'E-aa2',kind:'document',title:'参考材料',quote:'检索到的另一条证据'}]})''')
    expect(answer).to_contain_text('资料标注 · 抖音电商')
    page.evaluate("""() => emitCommunity('evidence',{subquestion:{number:2,question:'其他限制',
        state:'platform_unresolved',evidence_ids:[]},evidence:[]})""")
    expect(answer).to_contain_text('平台归属证据不足')
    expect(answer.get_by_text('不可退款条款', exact=True)).to_be_visible()
    expect(answer.get_by_text('检索到的另一条证据', exact=True)).to_be_visible()
    expect(answer.locator('.qa-answer')).to_be_empty()
    page.evaluate("() => emitCommunity('progress',{stage:'mapping',completed:4,total:8})")
    expect(answer).to_contain_text('已处理 4 / 8 份')
    page.evaluate("() => emitCommunity('delta',{text:'正在生成的第一段'})")
    expect(answer.locator('.qa-answer')).to_have_text('正在生成的第一段')
    expect(answer.get_by_text('不可退款条款', exact=True)).to_be_visible()
    expect(answer).to_have_attribute('data-state', 'streaming')
    saved = qa_history['conversation-1']['messages']
    assert [item['text'] for item in saved] == ['知识依据问题', '知识依据回答', '社区问题']
    with page.expect_response(lambda response: response.request.method == 'PUT' and '/qa/conversations/' in response.url):
        page.evaluate('''() => emitCommunity('done',{answer:'最终社区回答 [E-aa1]',generated:true,citations:['E-aa1'],
          subquestions:[{number:1,question:'退款限制',state:'evidence_found',channels:['keyword','original_hybrid'],evidence_ids:['E-aa1','E-aa2']},
            {number:2,question:'其他限制',state:'platform_unresolved',evidence_ids:[]}],
          evidence:[{id:'E-aa1',kind:'document',title:'依据',quote:'不可退款条款',source_platforms:['抖音电商']},
            {id:'E-aa2',kind:'document',title:'参考材料',quote:'检索到的另一条证据'}]})''')
    expect(answer).to_have_attribute('data-state', 'done')
    expect(answer.get_by_role('button', name='定位引用 1')).to_be_visible()
    cited = answer.locator('.community-question-result-body > details').filter(has_text='回答引用')
    expect(cited.get_by_text('不可退款条款', exact=True)).to_be_visible()
    expect(answer.get_by_text('检索到的另一条证据', exact=True)).to_be_hidden()
    assert qa_history['conversation-1']['messages'][-1]['community_result']['evidence'][0]['quote'] == '不可退款条款'

    page.reload()
    page.wait_for_selector('#project option[value="p"]', state='attached')
    if page.locator('#project').input_value() != 'p':
        page.select_option('#project', 'p')
    page.locator('[data-tab="qa"]').click()
    expect(page.locator('#qa-transcript .qa-answer')).to_contain_text('知识依据回答')
    page.get_by_role('button', name='社区问答', exact=True).click()
    expect(questions.get_by_text('社区问题', exact=True)).to_be_visible()
    expect(answer.locator('.qa-answer')).to_contain_text('最终社区回答')
    answer.get_by_role('button', name='定位引用 1').click()
    expect(cited.get_by_text('不可退款条款', exact=True)).to_be_visible()
    answer.get_by_text('检索覆盖 · 2 项', exact=True).click()
    expect(answer.get_by_text('社区关键词 · 原文混合', exact=True)).to_be_visible()
    answer.get_by_role('button', name='定位子问证据 2').click()
    expect(answer.get_by_text('检索到的另一条证据', exact=True)).to_be_visible()
    questions.get_by_role('button', name='新建会话').click()
    expect(answer).to_have_count(0)
    page.locator('#qa-session-list').get_by_role('button', name=re.compile('^知识依据问题')).click()
    expect(answer.locator('.qa-answer')).to_contain_text('最终社区回答')
    assert not errors


def test_qa_follow_ups_send_only_their_own_conversation_history(redesign_page, qa_history):
    expect = pytest.importorskip('playwright.sync_api').expect
    page, errors = redesign_page
    status = {'state': 'ready', 'browseable': True, 'revision': 'r1', 'counts': {'reports_ready': 1}}
    page.route('**/api/projects/p/communities', lambda route: route.fulfill(json={'status': status, 'communities': []}))
    page.route('**/api/projects/p/qa/stream', lambda route: route.fulfill(content_type='text/event-stream',
        body='event: done\ndata: {"answer":"知识依据回答"}\n\n'))
    page.route('**/api/projects/p/communities/ask', lambda route: route.fulfill(
        json={'generated': True, 'answer': '社区回答', 'evidence': [], 'communities': []}))
    page.locator('[data-tab="qa"]').click()
    knowledge, knowledge_send = page.locator('#qa-query'), page.locator('#qa')
    knowledge.fill('盗播首次违规怎么处置？')
    knowledge_send.click()
    expect(page.locator('#qa-transcript .qa-answer')).to_have_text('知识依据回答')
    knowledge.fill('那第二次呢？')
    with page.expect_request('**/api/projects/p/qa/stream') as sent:
        knowledge_send.click()
    assert sent.value.post_data_json['conversation_context'] == [
        {'question': '盗播首次违规怎么处置？', 'answer': '知识依据回答'}]
    expect(page.locator('#qa-transcript .qa-answer')).to_have_count(2)
    page.get_by_role('button', name='社区问答', exact=True).click()
    questions = page.locator('#community-questions')
    entry = questions.get_by_role('textbox', name='社区问答问题')
    submit = questions.get_by_role('button', name='发送社区问题')
    entry.fill('盗播分级社区有哪些处置？')
    with page.expect_request('**/api/projects/p/communities/ask') as sent:
        submit.click()
    assert 'conversation_context' not in sent.value.post_data_json
    expect(questions.locator('.qa-answer')).to_have_text('社区回答')
    entry.fill('那第三次呢？')
    with page.expect_request('**/api/projects/p/communities/ask') as sent:
        submit.click()
    assert sent.value.post_data_json['conversation_context'] == [
        {'question': '盗播分级社区有哪些处置？', 'answer': '社区回答'}]
    expect(questions.locator('.qa-answer')).to_have_count(2)
    questions.get_by_role('button', name='新建会话').click()
    expect(questions.locator('.qa-answer')).to_have_count(0)
    entry.fill('那第四次呢？')
    with page.expect_request('**/api/projects/p/communities/ask') as sent:
        submit.click()
    assert 'conversation_context' not in sent.value.post_data_json
    assert not errors


def test_long_community_content_scrolls_inside_desktop_workspace(redesign_page):
    expect = pytest.importorskip('playwright.sync_api').expect
    page, errors = redesign_page
    page.set_viewport_size({'width': 1280, 'height': 720})
    status = {'state': 'ready', 'browseable': True, 'revision': 'r1'}
    summary = {'id': 'c0', 'report_title': '跨文档主题', 'report_status': 'ready'}
    evidence = [
        {'id': f'e{i}', 'kind': 'document', 'document_id': f'd{i % 2}',
         'source_version_id': f's{i % 2}', 'chunk_version_id': f'chunk{i}',
         'title': f'资料 {i % 2}', 'quote': f'证据 {i} ' + '社区中的不同规则。' * 18}
        for i in range(24)
    ]
    detail = {**summary, 'status': status, 'evidence_count': len(evidence), 'detail_offset': 0,
              'evidence': evidence, 'report_evidence': evidence,
              'report': {'status': 'ready', 'summary': '跨文档报告',
                         'findings': [{'text': f'发现 {i} ' + '本条规则的说明。' * 16,
                                       'citations': [f'e{i}']} for i in range(len(evidence))]}}
    page.route('**/api/projects/p/communities', lambda route: route.fulfill(json={
        'status': status, 'communities': [{**summary, 'id': f'c{i}'} for i in range(24)], 'total': 24,
    }))
    page.route('**/api/projects/p/communities/detail', lambda route: route.fulfill(json=detail))
    page.get_by_role('button', name='社区', exact=True).click()
    browser = page.locator('#community-browser')
    first = browser.get_by_role('button', name='打开社区').first
    expect(first).to_be_visible()
    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight + 1')
    first.click()
    expect(browser.get_by_role('heading', name='报告摘要')).to_be_visible()
    expect(browser.get_by_text('2 份来源文档', exact=True)).to_be_visible()
    expect(browser.get_by_text('报告引用 · 24 条 · 2 份文档', exact=True)).to_be_visible()
    toolbar = browser.locator('#community-browse-path')
    before = toolbar.bounding_box()['y']
    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight + 1')
    report = browser.get_by_role('region', name='社区报告')
    rail = browser.get_by_role('region', name='成员与证据')
    assert report.evaluate('(el) => el.scrollHeight > el.clientHeight')
    browser.get_by_role('button', name='定位引用 24', exact=True).click()
    assert rail.evaluate('(el) => el.scrollTop') > 0
    assert toolbar.bounding_box()['y'] == before
    assert page.evaluate('window.scrollY') == 0
    assert not errors


@pytest.mark.parametrize('width', [1800, 390])
def test_community_discovery_fills_workspace_and_connects_report_to_evidence(redesign_page, width, qa_history):
    expect = pytest.importorskip('playwright.sync_api').expect
    page, errors = redesign_page
    page.set_viewport_size({'width': width, 'height': 1050})
    requests = []
    legacy_runtime = False
    status = {'state': 'ready', 'browseable': True, 'revision': 'r1',
              'counts': {'communities': 4, 'entities': 20, 'covered_entities': 18, 'reports_ready': 4},
              'levels': [{'level': 0, 'communities': 3, 'reports_ready': 2}, {'level': 1, 'communities': 1, 'reports_ready': 1}],
              'vector_index': {'state': 'ready', 'indexed': 4}}
    communities = [{'id': f'c{i}', 'report_title': f'{topic}社区', 'member_count': 8,
                    'relationship_count': 13, 'report_status': 'ready',
                    'summary': '汇总规则中涉及商户的要求和处理流程。' * (i + 2)}
                   for i, topic in enumerate(['预约履约', '退款处理', '榜单下架', '未成年人保护'])]
    first = {'id': 'E-aa1', 'title': '平台商户管理规则.md', 'kind': 'document', 'source_version_id': 's1',
             'chunk_version_id': 'chunk-1', 'start_char': 4, 'end_char': 20,
             'quote': '预约未履约时，平台会核实商户责任。'}
    duplicate = {**first, 'id': 'E-aa2'}
    second = {**first, 'id': 'e3', 'chunk_version_id': 'chunk-2', 'start_char': 25, 'end_char': 39,
              'quote': '已确认的订单变更需要及时通知用户。'}
    historical = {'id': 'e-old', 'kind': 'document', 'title': '历史履约规则',
                  'document_version_id': 'old-revision', 'quote': '历史资料保留的履约引句。'}
    keyword_match = {'field': 'evidence', 'before': '平台应核查商户是否', 'term': '履约',
                     'after': '。', 'source_title': '平台商户管理规则.md'}

    def respond(route):
        body = route.request.post_data_json
        requests.append(body)
        if route.request.url.endswith('/detail'):
            if body['community_id'] == 'c1':
                return route.fulfill(json={**communities[1], 'status': status, 'level': 1, 'parent_id': 'c0'})
            return route.fulfill(json={**communities[0], 'status': status, 'children': [communities[1]],
                                       'level': 0, 'source_document_count': 2, 'cited_document_count': 2,
                                       'evidence': [first, duplicate, second, historical],
                                       'report_evidence': [first, duplicate, historical],
                                       'report': {'status': 'ready', 'summary': '预约规则按商户履约与用户通知归纳。',
                                                  'findings': [{'text': '商户需遵守预约约定。', 'citations': [first['id']]},
                                                               {'text': '异常情况需要核实责任。', 'citations': [duplicate['id']]},
                                                               {'text': '历史条文可供复核。', 'citations': [historical['id']]}]},
                                       'members': [{'id': 'm1', 'text': '商户', 'version_id': 'm1v'}],
                                       'relationships': [{'subject_text': '商户', 'type': '遵循', 'object_text': '预约规则'}]})
        if route.request.url.endswith('/ask'):
            if legacy_runtime and body.get('match_only'):
                return route.fulfill(status=422, json={
                    'detail': [{'type': 'extra_forbidden', 'loc': ['body', 'match_only']}],
                })
            if legacy_runtime and body['question'] == '没有对应主题的问题':
                return route.fulfill(json={'status': status, 'generated': False,
                    'communities': communities[:1], 'evidence': [first],
                    'coverage': {'retrieval': 'representative'}})
            answer = f"商户需遵守预约约定。 [{first['id']}]" if body['generate'] else f"• {first['quote']} [{first['id']}]"
            if body.get('mode') == 'global':
                return route.fulfill(json={'status': status, 'generated': True, 'answer': '全局归纳',
                   'answer_purpose': 'document_explanation', 'evidence': [first], 'citations': [first['id']],
                   'coverage': {'mode': 'global', 'level': body['level'], 'reports_ready': 2, 'target_communities': 3,
                                'reports_with_eligible_evidence': 1, 'reports_mapped': 1}})
            returned = [first] if body['generate'] else [first, duplicate, second, historical]
            cited = [first['id']] if body['generate'] else [first['id'], duplicate['id'], second['id']]
            retrieval = 'selected' if body['community_ids'] else body['mode']
            match = {'semantic_score': .91751} if retrieval == 'semantic' else {'keyword_match': keyword_match}
            return route.fulfill(json={'status': status, 'generated': body['generate'], 'answer': answer,
                                       'communities': [{**communities[0], **match}], 'evidence': returned,
                                       'citations': cited,
                                       'coverage': {'retrieval': retrieval, 'complete': False, 'matched_communities': 2,
                                                    'included_communities': 1, 'reports_included': 1,
                                                    'evidence_returned': len(returned), 'evidence_available': 73}})
        rows = communities
        if body.get('mode') == 'semantic':
            rows = [{**communities[0], 'semantic_score': .91}]
        if body.get('keyword') == '履约':
            rows = [communities[0], {**communities[1], 'keyword_match': keyword_match}]
        return route.fulfill(json={'status': status, 'communities': rows, 'total': len(rows)})

    page.route(re.compile(r'/api/projects/p/communities(?:/(?:detail|ask))?$'), respond)
    header = page.locator('#graph-workspace-header')
    kinds = header.get_by_role('group', name='检索对象')
    expect(kinds.get_by_role('button')).to_have_text(['知识', '邻域', '社区'])
    expect(header.locator('.retrieval-page-heading #graph-workspace-state')).to_have_count(1)
    page.route('**/records/page', lambda route: route.fulfill(json={
        'records': [{'id': 'seed', 'kind': 'entity', 'text': '预约规则'}], 'next_cursor': None,
    }))
    pending = []
    page.route('**/api/projects/p/search', lambda route: pending.append(route))
    query = header.locator('#query')
    query.fill('预约')
    submit = header.locator('#search')
    submit.click()
    expect(submit).to_be_disabled()
    kinds.get_by_role('button', name='邻域').click()
    expect(submit).to_be_enabled()
    query.press('Enter')
    candidates = page.locator('#graph-panel-neighborhood')
    expect(candidates.get_by_role('button', name='预约规则')).to_be_visible()
    expect(candidates.get_by_label('定位起点实体')).to_be_hidden()
    pending.pop().fulfill(json={'hits': [], 'active_mode': 'hybrid', 'candidate_count': 0})
    expect(page.locator('#search-summary')).to_contain_text('0 命中', use_inner_text=False)
    expect(candidates).to_be_visible()
    expect(header.locator('#search-mode')).to_be_hidden()
    kinds.get_by_role('button', name='社区').click()
    expect(page.locator('#graph-panel-neighborhood')).to_be_hidden()
    browser = page.locator('#community-browser')
    expect(browser.get_by_role('heading', name='预约履约社区', exact=True)).to_be_visible()
    box = browser.bounding_box()
    tab_box = page.locator('#tab-search').bounding_box()
    assert abs(box['width'] - tab_box['width']) <= 2
    assert query.bounding_box()['height'] == header.locator('#search').bounding_box()['height']
    expect(query).to_have_value('预约')
    assert requests[-1]['keyword'] == '预约'
    mode = header.get_by_label('社区检索方式')
    mode.select_option('semantic')
    query.fill('服务预约没有按约定交付')
    submit.click()
    expect(browser.get_by_text('语义相似度 0.91')).to_be_visible()
    expect(browser.get_by_text('匹配社区报告向量', exact=True)).to_be_visible()
    assert requests[-1]['mode'] == 'semantic'
    assert requests[-1]['keyword'] == '服务预约没有按约定交付'
    mode.select_option('keyword')
    query.fill('履约')
    header.locator('#search').click()
    expect(browser.get_by_role('heading', name='预约履约社区', exact=True)).to_be_visible()
    assert requests[-1]['keyword'] == '履约'
    indirect = browser.get_by_role('article').filter(has_text='退款处理社区')
    expect(indirect.locator('mark')).to_have_text('履约')
    expect(indirect.get_by_text('命中原文引句', exact=True)).to_be_visible()
    expect(indirect.get_by_text('关键词匹配 1', exact=True)).to_be_visible()
    expect(indirect.get_by_text('平台商户管理规则.md', exact=True)).to_be_visible()
    expect(browser.get_by_text('关键词匹配 1', exact=True)).to_have_count(1)
    kinds.get_by_role('button', name='知识').click()
    expect(header.locator('#search-mode')).to_be_visible()
    expect(browser).to_be_hidden()
    expect(query).to_have_value('履约')
    kinds.get_by_role('button', name='社区').click()
    output = Path('.benchmarks/community-workspace')
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / f'list-{width}.png'), full_page=True,
                    style='.ks-notice { visibility: hidden; }')

    browser.get_by_role('article').filter(has_text='预约履约社区').get_by_role('button', name='打开社区').click()
    expect(browser.get_by_role('heading', name='报告摘要')).to_be_visible()
    expect(browser.get_by_role('heading', name='主要发现')).to_be_visible()
    expect(browser.get_by_text('2 份来源文档', exact=True)).to_be_visible()
    expect(browser.get_by_text('报告引用 · 2 条 · 2 份文档', exact=True)).to_be_visible()
    children = browser.get_by_text('子社区 · 1', exact=True)
    children.click()
    expect(browser.get_by_text('由本社区内关系更紧密的实体组成。', exact=True)).to_be_visible()
    children.click()
    toolbar = browser.locator('#community-browse-path')
    back = toolbar.get_by_role('button', name='返回社区列表')
    ask = toolbar.get_by_role('button', name='就此社区提问')
    expect(back).to_be_visible()
    expect(ask).to_be_visible()
    assert abs(back.bounding_box()['y'] - ask.bounding_box()['y']) <= 1
    assert abs(back.bounding_box()['height'] - ask.bounding_box()['height']) <= 1
    expect(browser.get_by_role('searchbox')).to_be_hidden()
    assert requests[-1]['expected_revision'] == 'r1'
    expect(browser.get_by_text(first['quote'], exact=True)).to_have_count(1)
    refs = browser.get_by_role('button', name='定位引用 1')
    expect(refs).to_have_count(2)
    expect(browser.get_by_text(first['quote'], exact=True)).to_be_hidden()
    refs.first.click()
    expect(browser.get_by_text(first['quote'], exact=True)).to_be_visible()
    page.evaluate('window.SourceReader.open = options => { window.communityOpened = options; }')
    open_source = browser.get_by_role('button', name='查看原文', exact=True).first
    assert 24 <= open_source.bounding_box()['height'] <= 32
    assert open_source.bounding_box()['width'] <= 75
    open_source.click()
    assert page.evaluate('window.communityOpened.versionId') == 's1'
    browser.get_by_role('button', name='定位引用 2').click()
    browser.get_by_role('button', name='查看引用片段').click()
    excerpt = page.get_by_role('dialog', name='历史履约规则')
    expect(excerpt.get_by_text(historical['quote'], exact=True)).to_be_visible()
    expect(excerpt).to_contain_text('社区证据保存的原文片段')
    page.keyboard.press('Escape')
    expect(excerpt).to_be_hidden()
    expect(browser.get_by_text('商户 · 遵循 · 预约规则', exact=True)).to_be_hidden()
    browser.get_by_text('关系 · 13', exact=True).click()
    expect(browser.get_by_text('商户 · 遵循 · 预约规则', exact=True)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.screenshot(path=str(output / f'detail-{width}.png'), full_page=True,
                    style='.ks-notice { visibility: hidden; }')
    children.click()
    browser.get_by_role('button', name='退款处理社区', exact=True).click()
    expect(browser.get_by_role('heading', name='退款处理社区', exact=True)).to_be_visible()
    expect(browser.get_by_text('第 2 层', exact=True)).to_be_visible()
    toolbar.get_by_role('button', name='返回上一级').click()
    expect(browser.get_by_role('heading', name='预约履约社区', exact=True)).to_be_visible()
    browser.get_by_role('button', name='就此社区提问').click()
    questions = page.locator('#community-questions')
    expect(questions).to_be_visible()
    expect(questions.locator('#scope-toggle')).to_be_visible()
    expect(page.get_by_role('button', name='预约履约社区 ×')).to_be_visible()
    question = questions.get_by_role('textbox', name='社区问答问题')
    send = questions.get_by_role('button', name='发送社区问题')
    generate = questions.get_by_role('checkbox', name='生成模型答案')
    filters = questions.get_by_text('筛选社区', exact=True)
    strategy = questions.get_by_text('匹配方式', exact=True)
    reply = questions.locator('.qa-turn-assistant').last
    expect(send).to_be_enabled()
    expect(questions.get_by_label('添加社区')).to_be_hidden()
    question.fill('预约未履约怎么处理？')
    question.press('Enter')
    expect(reply.get_by_text(first['quote'], exact=True)).to_be_visible()
    coverage = reply.locator('.community-coverage-line')
    expect(coverage).to_contain_text('展示社区 1/2')
    shown_evidence = coverage.get_by_text('展示证据 2/73', exact=True)
    expect(shown_evidence).to_have_attribute('title', '接口返回 4 条，页面选取并去重后展示 2 条，本次候选 73 条。')
    expect(reply.get_by_text('证据摘录 · 2 条 · 1 个来源', exact=True)).to_be_visible()
    expect(reply.locator('.community-source-excerpt')).to_have_count(2)
    badges = coverage.locator('span').all()
    assert abs(badges[0].bounding_box()['y'] - badges[1].bounding_box()['y']) <= 1
    matches = reply.locator('details').filter(has=page.get_by_text('社区列表 · 1 个', exact=True))
    expect(matches).not_to_have_attribute('open', '')
    matches.locator('summary').click()
    report = matches.get_by_role('article')
    expect(report.get_by_role('heading', name='预约履约社区')).to_be_visible()
    expect(report.get_by_text('关键词匹配 1', exact=True)).to_have_count(0)
    expect(report.locator('mark')).to_have_count(0)
    assert report.bounding_box()['x'] - matches.bounding_box()['x'] >= 10
    expect(report.get_by_text('报告可用')).to_have_count(0)
    matches.locator('summary').click()
    assert requests[-1]['community_ids'] == ['c0']
    assert requests[-1]['generate'] is False
    expect(reply).not_to_contain_text(first['id'])
    generate.check()
    question.fill('预约未履约怎么处理？')
    send.click()
    expect(reply.get_by_role('heading', name='社区回答')).to_be_visible()
    expect(reply.get_by_role('button', name='定位引用 1')).to_be_visible()
    assert requests[-1]['generate'] is True

    questions.get_by_role('button', name='预约履约社区 ×').click()
    generate.uncheck()
    question.fill('不履行预约有什么后果？')
    send.click()
    expect(reply.get_by_text(first['quote'], exact=True)).to_be_visible()
    assert requests[-1]['community_ids'] == []
    assert requests[-1]['mode'] == 'semantic'
    assert requests[-1]['question'] == '不履行预约有什么后果？'
    matches.locator('summary').click()
    expect(matches.get_by_text('语义相似度 0.91751', exact=True)).to_be_visible()
    expect(matches.get_by_text('匹配社区报告向量', exact=True)).to_be_visible()
    matches.locator('summary').click()
    form = questions.locator('#community-question-form').bounding_box()
    assert form['y'] + form['height'] <= 1051
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.screenshot(path=str(output / f'questions-{width}.png'),
                    style='.ks-notice { visibility: hidden; }')

    status['vector_index']['state'] = 'disabled'
    page.locator('[data-community-view="knowledge"]').click()
    expect(page.locator('#qa-page #scope-toggle')).to_be_visible()
    page.locator('[data-community-view="questions"]').click()
    question.fill('如何处理履约问题？')
    send.click()
    expect(reply.get_by_text(first['quote'], exact=True)).to_be_visible()
    assert requests[-1]['mode'] == 'keyword'
    assert requests[-1]['community_ids'] == [] and requests[-1]['match_only'] is True
    matches.locator('summary').click()
    expect(matches.get_by_text('关键词匹配 1', exact=True)).to_be_visible()
    expect(matches.get_by_text('命中原文引句', exact=True)).to_be_visible()
    expect(matches.locator('mark')).to_have_text('履约')
    legacy_runtime = True
    generate.check()
    question.fill('没有对应主题的问题')
    send.click()
    expect(questions.get_by_text('当前范围没有与问题匹配的社区报告。', exact=True)).to_be_visible()
    expect(reply.get_by_text(first['quote'], exact=True)).to_have_count(0)
    assert requests[-2]['match_only'] is True and requests[-2]['generate'] is True
    assert 'match_only' not in requests[-1] and requests[-1]['generate'] is False
    question.fill('预约规则怎样处理？')
    send.click()
    expect(reply.get_by_role('heading', name='社区回答')).to_be_visible()
    assert requests[-2]['generate'] is False and requests[-2]['community_ids'] == []
    assert requests[-1]['generate'] is True and requests[-1]['community_ids'] == ['c0']
    status['vector_index']['state'] = 'ready'

    filters.click()
    strategy.click()
    questions.get_by_label('问答方式').select_option('global')
    level = questions.get_by_label('社区层级')
    expect(level).to_be_visible()
    expect(questions.get_by_label('添加社区')).to_be_hidden()
    level.select_option('0')
    generate.check()
    question.fill('全局有什么共性？')
    send.click()
    expect(reply.get_by_text('全局归纳', exact=True)).to_be_visible()
    expect(reply.get_by_text('归纳 1/3', exact=True)).to_be_visible()
    assert requests[-1]['community_ids'] == []
    assert requests[-1]['mode'] == 'global' and requests[-1]['level'] == 0
    filters.click()
    questions.get_by_label('问答方式').select_option('semantic')
    expect(level).to_be_hidden()
    generate.uncheck()
    question.fill('如何处理履约问题？')
    send.click()
    expect(reply.get_by_text(first['quote'], exact=True)).to_be_visible()
    assert requests[-1]['mode'] == 'semantic' and 'level' not in requests[-1]
    assert not errors


@pytest.mark.parametrize('width', [1440, 390])
def test_missing_community_index_uses_one_compact_action(redesign_page, width):
    expect = pytest.importorskip('playwright.sync_api').expect
    page, errors = redesign_page
    page.set_viewport_size({'width': width, 'height': 850})
    status = {'state': 'disabled', 'browseable': False}

    def community_list(route):
        route.fulfill(json={'status': status, 'communities': [], 'total': 0})

    page.route('**/api/projects/p/communities', community_list)
    page.route('**/api/projects/p/communities/status', lambda route: route.fulfill(json=status))
    search = page.locator('#graph-workspace-header')
    community_tab = search.get_by_role('button', name='社区', exact=True)
    search.locator('#query').fill('商品')
    community_tab.click()
    community = page.locator('#community-browser')
    state = community.locator('.community-browse-state')
    build = state.get_by_role('button', name='前往社区索引', exact=True)
    ask = community.get_by_role('button', name='社区问答', exact=True)
    expect(state.get_by_role('status')).to_have_text('当前范围尚未构建社区索引')
    expect(state.locator('.workspace-icon')).to_be_visible()
    expect(build).to_be_visible()
    expect(ask).to_be_hidden()
    expect(community.locator('.community-browse-controls')).to_be_hidden()
    expect(community.get_by_role('button')).to_have_count(1)
    assert state.bounding_box()['height'] < 65
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')

    output = Path('.benchmarks/community-workspace')
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / f'empty-compact-{width}.png'),
                    style='.ks-notice { visibility: hidden; }')
    build.click()
    expect(page.locator('#tab-community-index')).to_be_visible()
    expect(page.locator('#community-build-kind')).to_have_value('project')
    expect(page.locator('#community-build')).to_be_visible()

    page.evaluate("() => { Auth.isAdmin = () => false; KnowledgeTabs.open('search'); }")
    expect(state.get_by_role('status')).to_have_text('当前范围尚未构建社区索引')
    expect(community.get_by_role('button')).to_have_count(0)

    status.update(state='ready', browseable=True, counts={'communities': 1, 'reports_ready': 1})
    search.get_by_role('button', name='检索', exact=True).click()
    expect(build).to_be_hidden()
    expect(ask).to_be_visible()
    ask.click()
    expect(page.locator('#community-questions')).to_be_visible()
    assert not errors
