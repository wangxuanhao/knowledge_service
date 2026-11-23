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

    def respond(route):
        body = route.request.post_data_json
        requests.append(body)
        if route.request.url.endswith('/detail'):
            return route.fulfill(json={**communities[0], 'status': status, 'children': [communities[1]],
                                       'evidence': [first, duplicate, second], 'report_evidence': [first, duplicate],
                                       'report': {'status': 'ready', 'summary': '预约规则按商户履约与用户通知归纳。',
                                                  'findings': [{'text': '商户需遵守预约约定。', 'citations': [first['id']]},
                                                               {'text': '异常情况需要核实责任。', 'citations': [duplicate['id']]}]},
                                       'members': [{'id': 'm1', 'text': '商户', 'version_id': 'm1v'}],
                                       'relationships': [{'subject_text': '商户', 'type': '遵循', 'object_text': '预约规则'}]})
        if route.request.url.endswith('/ask'):
            answer = f"商户需遵守预约约定。 [{first['id']}]" if body['generate'] else f"• {first['quote']} [{first['id']}]"
            if body.get('mode') == 'global':
                return route.fulfill(json={'status': status, 'generated': True, 'answer': '全局归纳',
                   'answer_purpose': 'document_explanation', 'evidence': [first], 'citations': [first['id']],
                   'coverage': {'mode': 'global', 'level': body['level'], 'reports_ready': 2, 'target_communities': 3,
                                'reports_with_eligible_evidence': 1, 'reports_mapped': 1}})
            return route.fulfill(json={'status': status, 'generated': body['generate'], 'answer': answer,
                                       'communities': communities[:1], 'evidence': [first], 'citations': [first['id']]})
        rows = communities
        if body.get('keyword') == '履约':
            hidden_match = {'field': 'evidence', 'before': '平台应核查商户是否', 'term': '履约',
                            'after': '。', 'source_title': '平台商户管理规则.md'}
            rows = [communities[0], {**communities[1], 'keyword_match': hidden_match}]
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
    query.fill('履约')
    header.locator('#search').click()
    expect(browser.get_by_role('heading', name='预约履约社区', exact=True)).to_be_visible()
    assert requests[-1]['keyword'] == '履约'
    indirect = browser.get_by_role('article').filter(has_text='退款处理社区')
    expect(indirect.locator('mark')).to_have_text('履约')
    expect(indirect.get_by_text('命中原文引句', exact=True)).to_be_visible()
    expect(indirect.get_by_text('平台商户管理规则.md', exact=True)).to_be_visible()
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
    expect(browser.get_by_text('商户 · 遵循 · 预约规则', exact=True)).to_be_hidden()
    browser.get_by_text('关系 · 13', exact=True).click()
    expect(browser.get_by_text('商户 · 遵循 · 预约规则', exact=True)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.screenshot(path=str(output / f'detail-{width}.png'), full_page=True,
                    style='.ks-notice { visibility: hidden; }')
    browser.get_by_role('button', name='就此社区提问').click()
    questions = page.locator('#community-questions')
    expect(questions).to_be_visible()
    expect(page.get_by_role('button', name='预约履约社区 ×')).to_be_visible()
    questions.get_by_label('问题', exact=True).fill('预约未履约怎么处理？')
    questions.get_by_role('button', name='查看相关报告').click()
    expect(questions.get_by_text(first['quote'], exact=True)).to_be_visible()
    assert requests[-1]['community_ids'] == ['c0']
    assert requests[-1]['generate'] is False
    expect(questions.locator('#community-answer')).not_to_contain_text(first['id'])
    questions.get_by_role('button', name='生成答案').click()
    expect(questions.get_by_role('heading', name='社区回答')).to_be_visible()
    expect(questions.get_by_role('button', name='定位引用 1')).to_be_visible()
    assert requests[-1]['generate'] is True
    questions.get_by_label('问答方式').select_option('global')
    level = questions.get_by_label('社区层级')
    expect(level).to_be_visible()
    expect(questions.get_by_label('添加社区')).to_be_hidden()
    level.select_option('0')
    questions.get_by_role('button', name='生成答案').click()
    expect(questions.get_by_text('全局归纳', exact=True)).to_be_visible()
    expect(questions.get_by_text('本次归纳 1 / 3 份', exact=False)).to_be_visible()
    assert requests[-1]['community_ids'] == []
    assert requests[-1]['mode'] == 'global' and requests[-1]['level'] == 0
    questions.get_by_label('问答方式').select_option('semantic')
    expect(level).to_be_hidden()
    questions.get_by_role('button', name='查看相关报告').click()
    expect(questions.get_by_text(first['quote'], exact=True)).to_be_visible()
    assert requests[-1]['mode'] == 'semantic' and 'level' not in requests[-1]
    assert not errors
