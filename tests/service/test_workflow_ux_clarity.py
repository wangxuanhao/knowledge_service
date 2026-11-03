"""Offline UX regressions: primary work is visible and choices look interactive."""
from pathlib import Path

import pytest

from test_workspace_redesign_ui import redesign_page


OUTPUT = Path(__file__).resolve().parents[2] / '.benchmarks' / 'ux-clarity'
OUTPUT.mkdir(parents=True, exist_ok=True)


@pytest.mark.parametrize('width', [1440, 900, 390])
def test_pending_work_and_draft_choices_are_visible_without_hierarchy(redesign_page, width):
    page, errors = redesign_page
    page.set_viewport_size({'width': width, 'height': 950})
    page.locator('[data-tab="ontology-model"]').click()
    page.wait_for_selector('#om-side', state='attached')
    page.evaluate('''()=>{
      Object.assign(OntologyModel.S,{loading:false,summary:null,ontologyId:null,draft:null,preview:null,
        discovery:{published:false,candidate_count:9,pending_document_count:3,
          candidate_status_counts:{pending:9,included_in_draft:0}}});
      OntologyModel.refresh=async()=>{};OntologyModel.render();window.scrollTo(0,0);
    }''')
    work = page.locator('#om-candidate-tasks')
    assert work.count() == 1, 'Pending work needs its own always-visible region.'
    assert work.is_visible()
    assert work.evaluate('(node)=>!node.closest("details")')
    assert not page.locator('.om-hierarchy-directory').evaluate('(node)=>node.open')
    assert '来自 3 份资料，共 9 条候选内容待核对' in work.inner_text()
    review = work.get_by_role('button', name='按资料核对候选', exact=True)
    assert review.is_visible()
    box = review.bounding_box()
    pane = page.locator('#tab-ontology-model .om-side').bounding_box()
    assert box['y'] >= 0 and box['y'] + box['height'] <= 950, (width, box)
    assert box['y'] + box['height'] <= pane['y'] + pane['height'] + 1, (width, box, pane)
    project_style = page.locator('#project').evaluate('''node=>{const s=getComputedStyle(node);
      return {image:s.backgroundImage,padding:parseFloat(s.paddingRight),background:s.backgroundColor,cursor:s.cursor};}''')
    assert project_style['image'] != 'none' and project_style['padding'] >= 34, project_style
    assert project_style['background'] == 'rgb(12, 46, 45)' and project_style['cursor'] == 'pointer', project_style
    page.screenshot(path=str(OUTPUT / f'{width}-model-tasks.png'))

    facts = [{'id': f'doc-{number}-candidate-{index}', 'kind': 'entity', 'text': f'商户 {number}-{index}',
              'proposed_type': '商户', 'document_id': f'doc-{number}',
              'document_title': f'资料{number}.md', 'evidence': '商户需要遵守平台规范。'}
             for number in range(1, 4) for index in range(3)]
    documents = [{'document_id': f'doc-{number}', 'document_title': f'资料{number}.md',
                  'total': 3, 'pending_count': 3, 'draft_count': 0, 'dependency_count': 0}
                 for number in range(1, 4)]
    page.route('**/ontology-drafts', lambda route: route.fulfill(json={'items': [
        {'id': 'existing', 'title': '退款规则调整', 'revision': 2, 'status': 'pending'}]}))
    page.route('**/ontology-discovery/candidates*', lambda route: route.fulfill(json={
        'items': facts, 'total': 9, 'documents': documents, 'published_terms': [],
        'expected_ontology_id': None, 'summary': {'document_count': 3, 'total': 9,
            'pending_count': 9, 'draft_count': 0, 'dependency_count': 0}}))
    writes = []
    page.on('request', lambda request: writes.append(request.url)
            if request.method not in {'GET', 'HEAD', 'OPTIONS'} and '/api/' in request.url else None)
    review.click()
    drawer = page.locator('#ontology-intake-drawer')
    drawer.locator('.oi-document-toggle').first.click()
    drawer.locator('.oi-document-body input[type=checkbox]').first.check()
    next_step = drawer.get_by_role('button', name='下一步：选择处理方式', exact=True)
    if next_step.count():
        next_step.click()
    create = drawer.get_by_role('radio', name='新建一份草案', exact=False)
    existing = drawer.get_by_role('radio', name='加入已有草案', exact=False)
    assert create.is_visible() and existing.is_visible(), 'Draft destinations must be direct choices.'
    scroll_trace = []

    def trace_scroll(step):
        scroll_trace.append({'step': step, **drawer.evaluate('''dialog=>({
          dialog:dialog.scrollTop,body:dialog.querySelector('.workspace-drawer-body').scrollTop,
          active:document.activeElement.id,goal:document.getElementById('oi-processing-goal').getBoundingClientRect().top})''')})

    trace_scroll('before-new')
    create.check()
    trace_scroll('after-new')
    assert page.evaluate('OntologyIntake.state.target') == 'new'
    existing.check()
    trace_scroll('after-existing')
    select = drawer.get_by_label('选择已有草案', exact=True)
    existing_box = select.bounding_box()
    actions_box = drawer.locator('.oi-actions').bounding_box()
    assert existing_box['y'] >= 0 and existing_box['y'] + existing_box['height'] <= actions_box['y'] - 4, (width, existing_box, actions_box, scroll_trace)
    select.focus()
    select.select_option('existing')
    trace_scroll('after-select')
    assert page.evaluate('OntologyIntake.state.target') == 'existing'
    selected_box = select.bounding_box()
    actions_box = drawer.locator('.oi-actions').bounding_box()
    assert selected_box['y'] >= 0 and selected_box['y'] + selected_box['height'] <= actions_box['y'] - 4, (width, selected_box, actions_box, scroll_trace)
    style = select.evaluate('''node=>{const s=getComputedStyle(node);return {
      image:s.backgroundImage,padding:parseFloat(s.paddingRight),border:s.borderTopWidth,cursor:s.cursor};}''')
    assert style['image'] != 'none' and style['padding'] >= 34, style
    assert style['border'] != '0px' and style['cursor'] == 'pointer', style
    assert not writes, writes
    page.screenshot(path=str(OUTPUT / f'{width}-draft-choice.png'))
    close_box = drawer.locator('.workspace-drawer-close').bounding_box()
    assert close_box['y'] >= 0 and close_box['y'] + close_box['height'] <= 950
    assert drawer.evaluate('(node)=>node.scrollWidth<=node.clientWidth+1')
    assert not errors, errors


def test_answer_generation_mode_is_visible_before_sending(redesign_page):
    page, errors = redesign_page
    page.locator('[data-tab="qa"]').click()
    generation = page.locator('#generate')
    assert generation.count() == 1
    assert generation.is_visible(), 'Answer generation is a primary sending choice.'
    assert generation.evaluate('(node)=>!node.closest("details")')
    assert not generation.is_checked(), 'Visibility must not silently enable model generation.'
    page.screenshot(path=str(OUTPUT / 'qa-answer-mode.png'))
    assert not errors, errors


def test_ledger_kind_select_only_offers_supported_views(redesign_page):
    page, errors = redesign_page
    page.locator('[data-tab="records"]').click()
    kind = page.locator('#record-kind')
    kind.wait_for(state='attached')
    values = kind.locator('option').evaluate_all('(nodes)=>nodes.map(node=>node.value)')
    assert '' not in values, 'An unsupported all-kind option must not misrepresent the query.'
    with page.expect_request(lambda request: '/records/page' in request.url
            and request.method == 'POST' and request.post_data_json.get('limit', 0) > 1
            and request.post_data_json.get('kinds') == ['relation']) as request:
        page.get_by_role('tab', name='关系', exact=True).click()
    assert request.value.post_data_json['kinds'] == ['relation']
    assert kind.input_value() == 'relation'
    assert not errors, errors


def test_project_form_errors_are_inside_the_open_dialog(redesign_page):
    page, errors = redesign_page
    page.locator('[data-tab="projects"]').click()
    page.locator('#new-project').click()
    modal = page.get_by_role('dialog', name='创建项目', exact=True)
    modal.get_by_role('button', name='创建', exact=True).click()
    assert '请输入项目名称' in modal.inner_text()
    assert modal.locator('#create-name').evaluate('(node)=>node===document.activeElement')
    modal.locator('#create-name').fill('保留的项目名称')
    page.route('**/api/projects', lambda route: route.fulfill(status=503, json={
        'detail': '服务暂不可用，请重试'}) if route.request.method == 'POST' else route.fallback())
    modal.get_by_role('button', name='创建', exact=True).click()
    modal.get_by_text('服务暂不可用，请重试', exact=True).wait_for()
    assert modal.locator('#create-name').input_value() == '保留的项目名称'
    assert modal.get_by_role('button', name='创建', exact=True).is_enabled()
    page.screenshot(path=str(OUTPUT / 'project-inline-error.png'))
    assert not errors, errors
