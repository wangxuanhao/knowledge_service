"""Community discovery and citation navigation in the full workspace shell."""

import re
from pathlib import Path

import pytest

from test_workspace_redesign_ui import redesign_page  # noqa: F401


@pytest.fixture
def postgres_backend():
    """These routes serve isolated API fixtures."""


@pytest.mark.parametrize('width', [1800, 390])
def test_community_discovery_fills_workspace_and_connects_report_to_evidence(redesign_page, width):
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
            return route.fulfill(json={**communities[0], 'status': status, 'children': [communities[1]],
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
    browser.get_by_role('button', name='查看原文', exact=True).first.click()
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
    expect(questions.get_by_label('添加社区')).to_be_hidden()
    question.fill('预约未履约怎么处理？')
    question.press('Enter')
    expect(questions.get_by_text(first['quote'], exact=True)).to_be_visible()
    coverage = questions.locator('.community-coverage-line')
    expect(coverage).to_contain_text('展示社区 1/2')
    shown_evidence = coverage.get_by_text('展示证据 2/73', exact=True)
    expect(shown_evidence).to_have_attribute('title', '接口返回 4 条，页面选取并去重后展示 2 条，本次候选 73 条。')
    expect(questions.get_by_text('证据摘录 · 2 条 · 1 个来源', exact=True)).to_be_visible()
    expect(questions.locator('.community-source-excerpt')).to_have_count(2)
    badges = coverage.locator('span').all()
    assert abs(badges[0].bounding_box()['y'] - badges[1].bounding_box()['y']) <= 1
    matches = questions.locator('details').filter(has=page.get_by_text('社区列表 · 1 个', exact=True))
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
    expect(questions.locator('#community-answer')).not_to_contain_text(first['id'])
    generate.check()
    question.fill('预约未履约怎么处理？')
    send.click()
    expect(questions.get_by_role('heading', name='社区回答')).to_be_visible()
    expect(questions.get_by_role('button', name='定位引用 1')).to_be_visible()
    assert requests[-1]['generate'] is True

    questions.get_by_role('button', name='预约履约社区 ×').click()
    generate.uncheck()
    question.fill('不履行预约有什么后果？')
    send.click()
    expect(questions.get_by_text(first['quote'], exact=True)).to_be_visible()
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
    expect(questions.get_by_text(first['quote'], exact=True)).to_be_visible()
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
    expect(questions.get_by_text(first['quote'], exact=True)).to_have_count(0)
    assert requests[-2]['match_only'] is True and requests[-2]['generate'] is True
    assert 'match_only' not in requests[-1] and requests[-1]['generate'] is False
    question.fill('预约规则怎样处理？')
    send.click()
    expect(questions.get_by_role('heading', name='社区回答')).to_be_visible()
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
    expect(questions.get_by_text('全局归纳', exact=True)).to_be_visible()
    expect(questions.get_by_text('归纳 1/3', exact=True)).to_be_visible()
    assert requests[-1]['community_ids'] == []
    assert requests[-1]['mode'] == 'global' and requests[-1]['level'] == 0
    filters.click()
    questions.get_by_label('问答方式').select_option('semantic')
    expect(level).to_be_hidden()
    generate.uncheck()
    question.fill('如何处理履约问题？')
    send.click()
    expect(questions.get_by_text(first['quote'], exact=True)).to_be_visible()
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
