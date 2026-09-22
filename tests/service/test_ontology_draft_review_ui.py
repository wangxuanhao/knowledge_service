"""三段式本体草案审核 UI 的真浏览器契约测试（.venv + 系统 Edge）。

依赖方向对齐后端 _materialize_candidates：
  本体类型 → 实体 → 关系/属性。
断言"手动排除"与"派生跳过预览"严格分离。
"""
import json
from pathlib import Path

import pytest

EDGE = Path(r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')

# 本文件是真浏览器契约测试：须用 .venv 解释器跑（llm_model 没装 playwright）。
# 两个前置条件任一不满足就整体跳过，避免在 llm_model 全量跑时产生 ERROR。
pytest.importorskip('playwright', reason='真浏览器契约测试须用 .venv（含 playwright）')
if not EDGE.exists():
    pytest.skip('需要系统 Edge 做真浏览器验证', allow_module_level=True)

WEB = (Path(__file__).resolve().parents[2] / 'knowledge_service/web')


def _candidate(id_, kind, **kw):
    base = {'id': id_, 'kind': kind, 'confidence': 0.9, 'proposed_type': '类型',
            'document_id': 'doc1', 'document_version_id': 'dv1',
            'document_title': '测试文档.pdf'}
    base.update(kw)
    return base


def _draft(status='draft'):
    entities = [
        _candidate('e1', 'entity', text='张三', proposed_type='主播'),
        _candidate('e2', 'entity', text='快驴', proposed_type='MCN机构'),
    ]
    relations = [_candidate('r1', 'relation', proposed_type='签约',
                            subject_id='e1', object_id='e2',
                            subject='张三', object='快驴')]
    attrs = [_candidate('a1', 'attribute', proposed_type='粉丝量',
                        entity_id='e1', value='12万')]
    return {'id': 'draftxyz', 'project_id': 'p1', 'name': '测试本体',
            'status': status, 'revision': 1,
            'created_at': '2026-09-20T00:00:00',
            'candidate_ids': ['e1', 'e2', 'r1', 'a1'],
            'candidate_snapshot': [*entities, *relations, *attrs],
            'candidate_count': 4,
            'mappings': {'entity_types': {'主播': 'urn:ns:主播',
                                          'MCN机构': 'urn:ns:MCN'},
                         'relation_types': {'签约': 'urn:ns:签约'},
                         'attributes': {'粉丝量': 'urn:ns:粉丝量'}},
            'turtle': '@prefix : <urn:ns:> .',
            'summary': {'classes': [], 'relations': [], 'attributes': []},
            'diff': {}, 'ontology_id': None}


HARNESS = """<!doctype html><html><head><meta charset="utf-8"></head>
<body><div id="host"></div>
<script src="ontology-details.js"></script></body></html>"""


@pytest.fixture()
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=str(EDGE))
        pg = browser.new_page(viewport={'width': 1400, 'height': 1000})
        errors = []
        pg.on('pageerror', lambda e: errors.append(str(e)))
        pg._errors = errors
        pg.goto((WEB / '_harness.html').as_uri())
        yield pg
        browser.close()


@pytest.fixture(autouse=True)
def _harness_file():
    (WEB / '_harness.html').write_text(HARNESS, encoding='utf-8')
    yield
    (WEB / '_harness.html').unlink(missing_ok=True)


RENDER = """([draft]) => {
  const host = document.getElementById('host');
  host.innerHTML = `<article data-draft="${draft.id}">` +
    OntologyDetails.reviewPanelHtml(draft) +
    OntologyDetails.renderTechnicalDetails(draft) + '</article>';
  window.saved = false;
  OntologyDetails.bindReviewPanel(host.querySelector('article'), draft,
    async (payload) => { window.savedPayload = payload; });
}"""


def test_sections_render_published_hidden(page):
    page.evaluate(RENDER, [_draft()])
    assert page.locator('[data-odr-terms]').count() == 1
    assert page.locator('[data-odr-entities]').count() == 1
    assert page.locator('[data-odr-relations]').count() == 1
    assert page.locator('details.ontology-technical-details').count() == 1
    # 折叠态：候选行不进 DOM（零行渲染）
    assert page.locator('.odr-crow').count() == 0
    # 类型行渲染：2 实体类 + 1 关系 + 1 属性
    assert page.locator('.odr-term').count() == 4
    # 已发布草案没有审核面板
    page.evaluate(RENDER, [_draft(status='published')])
    assert page.locator('[data-odr]').count() == 0


def test_term_off_cascades_and_restore(page):
    page.evaluate(RENDER, [_draft()])
    # 关掉 MCN机构
    page.click('input[data-term-toggle="MCN机构"]')
    # 实体组标题显示整组跳过；组内行不渲染
    text = page.locator('[data-odr-entities]').inner_text()
    assert '整组跳过' in text
    assert page.locator('[data-ent="e2"]').count() == 0
    # 统计条：1 实体将跳过
    stats = page.locator('[data-odr-stats]').inner_text()
    assert '1 实体' in stats
    # 关系 r1 端点缺失，连带跳过（展开关系组才渲染行）
    page.click('details[data-group="rel:签约"] > summary')
    page.wait_for_selector('[data-rel="r1"]', timeout=10000)
    assert page.locator('[data-rel="r1"]').is_disabled()
    assert '端点' in page.locator('[data-rel="r1"]').locator('..').inner_text()
    # 重开类型 → 派生项复活
    page.click('input[data-term-toggle="MCN机构"]')
    page.click('details[data-group="rel:签约"] > summary')
    page.wait_for_function(
        "() => {const x=document.querySelector('[data-rel=\"r1\"]'); return x && !x.disabled;}",
        timeout=10000)
    assert page.locator('[data-rel="r1"]').is_enabled()


def test_manual_off_kept_on_derived_restore(page):
    page.evaluate(RENDER, [_draft()])
    # 手动取消实体 e1（先展开主播组）
    page.click('details[data-group="ent:主播"] > summary')
    page.uncheck('[data-ent="e1"]')
    # 属性 a1 连带跳过
    page.click('details[data-group="attr:粉丝量"] > summary')
    page.wait_for_selector('[data-attr="a1"]', timeout=10000)
    assert page.locator('[data-attr="a1"]').is_disabled()
    # 保存：手动排除进 payload；派生跳过不进
    page.click('[data-odr-save]')
    page.wait_for_function("() => window.savedPayload !== undefined")
    payload = page.evaluate("() => window.savedPayload")
    assert set(payload['excluded_candidate_ids']) == {'e1'}
    assert payload['excluded_terms'] == []


def test_large_dataset_collapsed_dom_small(page):
    # 构造 2500 候选：3 实体类，其余按比例
    n = 2500
    per = n // 3
    candidates = []
    types = ['类A', '类B', '类C']
    for ti, t in enumerate(types):
        for i in range(per):
            candidates.append(_candidate(f'{ti}_{i}', 'entity',
                                         text=f'{t}实体{i}', proposed_type=t))
    mappings = {'entity_types': {t: f'urn:ns:{t}' for t in types},
                'relation_types': {}, 'attributes': {}}
    draft = _draft()
    draft['candidate_snapshot'] = candidates
    draft['mappings'] = mappings
    page.evaluate(RENDER, [draft])
    # 折叠态：只有 3 个组标题 + 3 个类型行，零候选行
    assert page.locator('.odr-crow').count() == 0
    assert page.locator('.odr-group').count() == 3
    # 展开一个组：只渲染前 100（等条件成立，避免 toggle 时序抖动）
    page.click('details[data-group="ent:类A"] > summary')
    page.wait_for_function(
        "() => document.querySelectorAll('[data-ent]').length === 100", timeout=10000)
    assert page.locator('[data-more]').count() == 1
    # 统计条仍然全量精确
    stats = page.locator('[data-odr-stats]').inner_text()
    assert f'{per * 3} 实体' in stats
    assert page._errors == []
