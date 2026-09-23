import re
from pathlib import Path
from html.parser import HTMLParser

ROOT=Path(__file__).resolve().parents[2]/'knowledge_service'/'web'


def test_workbench_has_all_control_targets_and_no_native_dialogs():
    html=(ROOT/'index.html').read_text(encoding='utf-8')
    ids=re.findall(r'\bid="([^"]+)"',html)
    assert len(ids)==len(set(ids))
    for name in ('app.js','workbench.js'):
        js=(ROOT/name).read_text(encoding='utf-8')
        assert not re.search(r'\b(?:prompt|confirm|alert)\s*\(',js)
        refs=re.findall(r"\$\('([^']+)'\)|bind\('([^']+)'",js)
        assert {a or b for a,b in refs}.issubset(set(ids)),{a or b for a,b in refs}-set(ids)
    for tab in ('dashboard','sources','mindmap','jobs','evaluation','records','ontology','search','qa','ingest'):
        assert f'tab-{tab}' in ids
    workspace=(ROOT/'workspace.js').read_text(encoding='utf-8')
    workspace_css=(ROOT/'workspace.css').read_text(encoding='utf-8')
    assert "page.id='tab-discovery'" in workspace
    assert "mode.id='extraction-mode'" in workspace
    assert '发布本体版本' in workspace
    details=(ROOT/'ontology-details.js').read_text(encoding='utf-8')
    assert 'ontology-details.js' in html
    assert 'OntologyDetails.renderTechnicalDetails' in workspace
    assert all(label in details for label in ('审核草案并查看技术详情','版本差异','本体定义','名称与技术标识（可编辑）'))
    assert all(help_text in details for help_text in ('新增、保留或删除','Turtle 标准格式','不是乱码'))
    assert '不会重新调用 LLM' in workspace
    assert '不需要重新上传原文' in workspace
    assert '受控重解析原文' not in workspace
    assert 'button:disabled{cursor:not-allowed}' in workspace_css
    assert '.discovery-drafts [data-publish]{grid-column:1/-1;width:100%' in workspace_css
    assert 'contain:layout paint' in workspace_css and 'requestAnimationFrame(()=>{if(ui.graph===result||ui.graph===base)chart.resize();})' in workspace
    assert all(text in workspace for text in ('知识时间','当前状态','上一个已知变更节点','仅在切换节点时刷新','/timeline?limit=100'))
    assert 'tl-density' not in workspace and 'startPlayback' not in workspace
    assert 'candidate_status_counts' in workspace
    assert all(text in workspace for text in ('本体结构维护','新增本体术语','高级：本体历史版本与 Turtle 源码','可多选'))
    assert all(text in workspace for text in ('新增实体类','新增关系类型','新增实体属性','预计本体标识 IRI','最终 IRI 由后端根据名称统一生成','已保存的类型会自动勾选'))
    assert 'readableIriSegment' in workspace and 'encodeURIComponent(label)' not in workspace
    assert "kind,uri:get('term-uri').value" not in workspace
    assert 'data-term-kind-choice' in workspace and 'ontology-checklist' in workspace and 'ontology-kind-source' in workspace
    assert 'ontology-checklist-source' in workspace and '.ontology-checklist-source{display:none!important}' in workspace_css
    evidence=(ROOT/'evidence-inspector.js').read_text(encoding='utf-8')
    assert all(text in evidence for text in ('编辑这条关系','删除这条关系','删除实体及其关联关系','再次点击确认删除','新增关系','保存并添加到图谱','interactive_graph'))
    assert 'evidence-danger' in evidence and "endpoint('/records')" in evidence
    record_dialog=(ROOT/'record-dialog.js').read_text(encoding='utf-8')
    assert all(text in record_dialog for text in ('维护具体实体','维护实体关系','保存实体修改为新版本','保存关系修改为新版本','高级：编辑完整记录 JSON'))
    assert "get('draw-graph')" not in record_dialog
    assert all(label in workspace for label in ('待纳入','草案中','已批准','已物化'))
    assert '发布本体并映射候选' not in workspace
    candidate_map=(ROOT/'candidate-mindmap.js').read_text(encoding='utf-8')
    menu=(ROOT/'menu-hierarchy.js').read_text(encoding='utf-8')
    assert '/ontology-discovery/candidate-mindmap' in candidate_map
    assert '非正式知识' in candidate_map and '该聚合键不是正式实体 ID' in candidate_map
    assert all(text in candidate_map for text in ('开放解析完成后先在这里检查','审核并生成本体草案','查看正式脑图'))
    assert '正式重解析' not in candidate_map
    assert 'view-candidate-mindmap' in workspace
    assert 'knowledge-flow' not in candidate_map and 'knowledge-flow' not in workspace
    assert all(group in menu for group in ('项目','探索与展示','建模与治理','运行与质量'))
    assert "['建模与治理',['ingest','candidate-mindmap','discovery'" in menu
    assert all(asset in html for asset in ('candidate-mindmap.js','menu-hierarchy.js','candidate-mindmap.css','menu-hierarchy.css'))
    assert '/assets/workbench.css' in html and '/assets/workbench.js' in html


def test_search_and_graph_share_one_workspace_with_single_mode_and_hop_controls():
    html=(ROOT/'index.html').read_text(encoding='utf-8')
    workspace=(ROOT/'workspace.js').read_text(encoding='utf-8')
    menu=(ROOT/'menu-hierarchy.js').read_text(encoding='utf-8')

    assert html.count('id="search-mode"') == 1
    assert 'id="retrieval-mode"' not in html
    assert html.count('id="graph-hops"') == 1
    assert 'data-tab="search"' in html
    assert 'data-tab="qa"' in html
    assert 'data-tab="graph"' not in html
    assert '>检索与交互图谱<' in html
    assert '>知识问答<' in html
    assert '>知识检索<' not in html
    assert '>证据问答<' not in html
    assert "['探索与展示',['search','qa','mindmap','sources'" in menu
    assert 'search-graph-workspace' in html
    assert all(region in html for region in ('search-results-pane','graph-stage','inspector-rail'))
    assert '从左侧检索结果选择实体或关系' in html
    assert all(control in html for control in ('graph-entity-filter','graph-entity-choice','graph-reset-view'))
    assert "endpoint('/search')" in workspace
    assert "scopedRead('/subgraph'" in workspace
    assert '/explore' not in workspace
    assert 'await linkedSearch()' not in workspace
    assert "get('query').oninput" not in workspace
    assert "showTab('graph')" not in workspace
    assert "delete result.nodes" in workspace and "delete result.edges" in workspace


def test_combined_workspace_has_responsive_breakpoints_and_isolated_clearing_contracts():
    workspace=(ROOT/'workspace.js').read_text(encoding='utf-8')
    styles=(ROOT/'workspace.css').read_text(encoding='utf-8')

    assert 'grid-template-columns:minmax(260px,300px) minmax(0,1fr) minmax(240px,280px)' in styles
    assert '@media(max-width:1100px)' in styles
    assert '@media(max-width:700px)' in styles
    assert 'min-height:55vh' in styles
    assert 'overflow-x:hidden' in styles
    assert 'detail-drawer-close' in workspace
    assert 'clearGraphWorkspace' in workspace
    assert 'preserveSearchResults' in workspace
    assert "get('hits').replaceChildren()" in workspace


def test_knowledge_chat_shell_is_a_focused_one_shot_conversation():
    html=(ROOT/'index.html').read_text(encoding='utf-8')
    workbench=(ROOT/'workbench.js').read_text(encoding='utf-8')
    styles=(ROOT/'workspace.css').read_text(encoding='utf-8')
    sources=(ROOT/'sources-view.js').read_text(encoding='utf-8')

    assert 'qa-shell' not in html
    assert 'id="answer"' not in html and 'id="qa-evidence"' not in html
    assert all(part in html for part in ('id="qa-page"','id="qa-hero"','id="qa-scroll"',
                                         'id="qa-transcript"','id="qa-composer"',
                                         'qa-composer-toolbar','id="qa-progress"','id="qa-live"',
                                         'id="qa-summary"','id="graph-heading"'))
    assert html.count('class="qa-example"') == 3
    assert html.index('id="generate"') < html.index('id="qa"')
    # The chat must never borrow the search workspace controls or the legacy /explore wrapper.
    chat=workbench[workbench.index('const chat=window.KnowledgeChat'):]
    assert 'search-mode' not in chat and 'graph-hops' not in chat
    assert '/explore' not in chat and 'prompt(' not in chat and 'confirm(' not in chat
    assert 'window.clearKnowledgeChat' in workbench
    assert 'window.selectSource' in sources
    assert '\\r?\\n\\r?\\n' in workbench and 'malformed' in workbench
    assert 'aria-expanded' in workbench and 'aria-controls' in workbench
    assert 'compositionstart' in workbench and 'isComposing' in workbench
    assert 'AbortController' in workbench
    assert "historyFor({id:b.dataset.evidenceSource})" not in workbench
    assert all(rule in styles for rule in ('.qa-page{','.qa-transcript{','.qa-evidence-panel{',
                                           '.qa-composer{position:relative','--qa-keyboard-inset',
                                           '.qa-composer-input textarea{'))


def test_timeline_change_clears_graph_without_reloading_and_preserves_search_results():
    workspace=(ROOT/'workspace.js').read_text(encoding='utf-8')
    timeline=workspace[workspace.index('function applyTimelinePoint'):workspace.index('function moveTimeline')]

    assert "window.clearGraphWorkspace({preserveSearchResults:true})" in timeline
    assert 'drawGraph(' not in timeline


def test_graph_reset_has_one_workspace_owner_and_preserves_request_invalidation():
    workbench=(ROOT/'workbench.js').read_text(encoding='utf-8')
    workspace=(ROOT/'workspace.js').read_text(encoding='utf-8')
    reset=workspace[workspace.index('window.clearGraphWorkspace='):
                    workspace.index("act('search'")]
    project_change=workspace[workspace.index("const priorChange=get('project').onchange"):
                             workspace.index('function autoLoadGraph')]

    assert 'clearOwnedGraphState' not in workbench
    assert "graph-summary').textContent='从左侧检索结果选择实体或关系'" not in workbench
    assert workspace.count('window.clearGraphWorkspace=') == 1
    assert 'graphRequests.invalidate()' in reset
    assert 'ui.graph=null' in reset and 'wb.nodes.clear()' in reset
    assert "get('graph-summary').textContent='从左侧检索结果选择实体或关系'" in reset
    assert project_change.index('window.clearGraphWorkspace()') < project_change.index('autoLoadGraph')


def test_knowledge_history_excludes_document_receipts_and_sources_show_build_state():
    records=(ROOT/'records-view.js').read_text(encoding='utf-8')
    workspace=(ROOT/'workspace.js').read_text(encoding='utf-8')
    styles=(ROOT/'workspace.css').read_text(encoding='utf-8')
    sources=(ROOT/'sources-view.js').read_text(encoding='utf-8')
    assert "filter.kinds=['entity','relation','chunk']" in records
    assert "document:'文档'" not in records
    assert "record-library-section" in records and "record-governance" in records
    assert all(step in records for step in ('按名称查找','选择保留的实体','选择另一个重复实体'))
    assert all(action in records for action in ('合并后保留','作为重复项合并','确认合并这两个实体'))
    assert '已完成查重' in records and '技术 ID：' in records
    assert '来源' in records
    assert 'governance-technical-id' in records
    assert not re.search(r'(?<!\.)\bget\([\'\"]',records)
    assert 'aliasButton.disabled=' in records
    assert '#tab-records>.record-section' in styles and '.governance-step-risk' in styles
    assert '.governance-technical-id{display:none}' in styles
    assert '.governance-merge-preview' in styles and '.governance-selection' in styles
    assert '兼容估算' in workspace and '这些状态如何变化？' in workspace
    assert "知识构建失败" in sources
    assert "构建成功" in sources


def test_successful_document_submission_opens_task_logs_immediately():
    workbench=(ROOT/'workbench.js').read_text(encoding='utf-8')
    ingest=workbench[workbench.index("bind('ingest'"):workbench.index('async function snapshots')]
    assert "if(accepted){showTab('jobs');await jobList();}" in ingest
