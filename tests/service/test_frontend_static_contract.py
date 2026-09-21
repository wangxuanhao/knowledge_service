"""前端静态契约：防止"改了动态渲染、漏了静态占位"和"漏 bump 版本号"这类复发。

背景：停用影响面板曾在两处各写一份标记（workspace.js 弹窗模板 + 打开时重写 innerHTML），
只改一处会出现"新标记不生效、旧标签继续显示"的幽灵现象，且浏览器无报错。
"""
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'


def read(name):
    return (WEB / name).read_text(encoding='utf-8')


def test_term_impact_panel_has_single_set_of_ids():
    """影响面板的新标记必须全套存在，旧的三个 id 必须彻底消失（两处标记已统一）。"""
    js = read('workspace.js')
    for new_id in ('impact-entities', 'impact-relations', 'impact-attributes',
                   'impact-secondary', 'impact-note'):
        assert f"'{new_id}'" in js or f'id="{new_id}"' in js, f'缺少 {new_id}'
    for old_id in ('impact-records', 'impact-constraints', 'impact-pending'):
        assert old_id not in js, f'残留旧标记 {old_id}（会造成重复/幽灵 UI）'


def test_term_impact_classifies_by_kind_not_just_total():
    """前端必须展示分类计数，而不是只显示一个总数。"""
    js = read('workspace.js')
    assert 'kind_counts' in js
    assert 'linked_relation_count' in js
    # 停用语义必须写在界面上（只摘定义、不删历史）
    assert '保留可查' in js and '不会删除任何数据' in js


def test_project_card_shows_project_id_with_copy():
    """项目管理卡片必须展示项目 ID 并提供复制，便于排查链路。"""
    js = read('projects-view.js')
    assert 'project-id' in js and 'data-copy-id' in js
    assert 'copyProjectId' in js
    # 复制失败要能退化到选中文本，不能静默失败
    assert 'selectNodeContents' in js


def test_assets_version_bumped_for_changed_files():
    """改动过的静态资源必须带版本号，否则浏览器缓存旧文件（"改了没生效"第一嫌疑）。"""
    html = read('index.html')
    for asset, marker in (('workspace.js', 'linked-review'),
                          ('ontology-details.js', 'linked-review'),
                          ('workspace.css', 'linked-review'),
                          ('projects.css', 'project-id'),
                          ('ontology-modal.css', 'term-impact')):
        assert f'{asset}?v={marker}' in html, f'{asset} 版本号未 bump（期望前缀 {marker}）'


def test_provenance_assets_precede_history_hydration():
    html = read('index.html')
    assert '/assets/provenance-drawer.css?v=2' in html
    assert '/assets/provenance-drawer.js?v=2' in html
    assert '/assets/workbench.js?v=provenance-2' in html
    assert html.index('/assets/workbench.css') < html.index('/assets/provenance-drawer.css')
    assert html.index('/assets/ingest-mode.js') < html.index('/assets/provenance-drawer.js') < html.index('/assets/workbench.js')


def test_provenance_drawer_safe_accessible_contract():
    js = read('provenance-drawer.js')
    for marker in ('ProvenanceDrawer', 'AbortController', 'aria-labelledby', 'aria-live',
                   'Escape', 'focus()', 'onProjectChange', 'createElement', 'textContent',
                   '证据链', 'API 数据', '该历史回答生成于溯源记录启用前'):
        assert marker in js, marker
    assert 'innerHTML' not in js
    assert 'insertAdjacentHTML' not in js
    css = read('provenance-drawer.css')
    for marker in ('var(--accent', 'var(--line', ':focus-visible', '@media', '[hidden]', 'position:fixed'):
        assert marker in css, marker
    assert 'font:inherit' in css


def test_provenance_reuses_read_only_history_and_frozen_source_entrypoints():
    js = read('evidence-inspector.js')
    assert 'window.openFrozenSourceEvidence' in js
    frozen = js.split('window.openFrozenSourceEvidence', 1)[1].split('function localTime', 1)[0]
    assert 'api(' not in frozen and 'fetch(' not in frozen and 'innerHTML' not in frozen
    assert 'excerpt_before' in frozen and 'excerpt_after' in frozen
    assert 'createTextNode' in js and "createElement('mark')" in js
    assert 'window.openRecordHistory' in read('record-dialog.js')


def test_narrow_provenance_panel_uses_answer_flow_instead_of_fixed_overlay():
    import re
    css = read('provenance-drawer.css')
    mobile = css.split('@media(max-width:760px)', 1)[1]
    panel = re.search(r'\.provenance-drawer\s*\{([^}]+)\}', mobile).group(1)
    assert re.search(r'position\s*:\s*(?:static|relative)\b', panel)
    assert re.search(r'inset\s*:\s*auto\b', panel)
    js = read('provenance-drawer.js')
    for marker in ('matchMedia', 'qa-transcript', 'qa-provenance-mount', 'qa-scroll'):
        assert marker in js, marker
