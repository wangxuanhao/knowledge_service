import re
from pathlib import Path
from html.parser import HTMLParser

ROOT=Path(__file__).resolve().parents[2]/'knowledge_service'/'web'


def test_workbench_has_all_control_targets_and_no_native_dialogs():
    html=(ROOT/'index.html').read_text(encoding='utf-8')
    ids=re.findall(r'\bid="([^"]+)"',html)
    assert len(ids)==len(set(ids))
    workspace=(ROOT/'workspace.js').read_text(encoding='utf-8')
    declared_ids=set(ids)|set(re.findall(r"\.id=['\"]([^'\"]+)['\"]",workspace))
    for name in ('app.js','workbench.js'):
        js=(ROOT/name).read_text(encoding='utf-8')
        assert not re.search(r'\b(?:prompt|confirm|alert)\s*\(',js)
        refs=re.findall(r"\$\('([^']+)'\)|bind\('([^']+)'",js)
        assert {a or b for a,b in refs}.issubset(declared_ids),{a or b for a,b in refs}-declared_ids
    for tab in ('dashboard','sources','mindmap','jobs','evaluation','records','ontology-model','search','qa','ingest'):
        assert f'tab-{tab}' in ids
    workspace=(ROOT/'workspace.js').read_text(encoding='utf-8')
    workspace_css=(ROOT/'workspace.css').read_text(encoding='utf-8')
    assert "page.id='tab-discovery'" not in workspace
    assert "mode.id='extraction-mode'" in workspace
    assert 'ontology-details.js' not in html and 'ontology-modal.css' not in html
    assert 'ontology-model-panel.js' in html and 'ontology-intake.js' in html
    assert '受控重解析原文' not in workspace
    assert 'button:disabled{cursor:not-allowed}' in workspace_css
    assert 'contain:layout paint' in workspace_css and 'requestAnimationFrame(()=>{if(ui.graph===result||ui.graph===base)chart.resize();})' in workspace
    assert all(text in workspace for text in ('知识时间','当前状态','上一个已知变更节点','仅在切换节点时刷新','/timeline?limit=100'))
    assert 'tl-density' not in workspace and 'startPlayback' not in workspace
    assert 'ontology-term-create' not in workspace and "endpoint('/ontology/terms')" not in workspace
    model=(ROOT/'ontology-model.js').read_text(encoding='utf-8')
    panel=(ROOT/'ontology-model-panel.js').read_text(encoding='utf-8')
    assert 'async function createTerm' in model and "action: 'batch'" in model
    assert 'await M.createTerm' in panel and '新建并写入草案' in panel
    evidence=(ROOT/'evidence-inspector.js').read_text(encoding='utf-8')
    creator=(ROOT/'record-create.js').read_text(encoding='utf-8')
    assert all(text in evidence for text in ('编辑这条关系','删除这条关系','删除实体及其关联关系','再次点击确认删除','新增关系'))
    assert 'evidence-danger' in evidence
    # The inspector delegates creation with the selected entity as its subject;
    # the shared form owns the published types, endpoints and formal write.
    assert "window.RecordCreate?.open('relation',{subject:row})" in evidence
    assert "window.RecordCreate?.open('attribute',{subject:row})" in evidence
    assert html.index('/assets/record-create.js') < html.index('/assets/evidence-inspector.js')
    assert all(text in creator for text in ('创建正式记录','起点实体','终点实体','只有管理员可以创建知识记录'))
    assert 'selected:subject' in creator and 'catalog(publication)[kind]' in creator
    assert 'await session.submit(record)' in creator
    assert "return read('/records',{records:[record]})" in creator
    assert "new CustomEvent('records:created'" in creator
    for js in (evidence,creator):
        assert not re.search(r'\b(?:prompt|confirm|alert)\s*\(',js)
    record_dialog=(ROOT/'record-dialog.js').read_text(encoding='utf-8')
    assert all(text in record_dialog for text in ('维护具体实体','维护实体关系','保存实体修改为新修订','保存关系修改为新修订','高级：编辑完整记录 JSON'))
    assert "get('draw-graph')" not in record_dialog
    assert '发布本体并映射候选' not in workspace
    candidate_map=(ROOT/'candidate-mindmap.js').read_text(encoding='utf-8')
    menu=(ROOT/'menu-hierarchy.js').read_text(encoding='utf-8')
    assert '/ontology-discovery/candidate-mindmap' in candidate_map
    assert '非正式知识' in candidate_map and '该聚合键不是正式实体 ID' in candidate_map
    assert all(text in candidate_map for text in ('开放解析完成后先在这里检查','进入本体建模层','查看正式脑图'))
    assert '正式重解析' not in candidate_map
    assert 'knowledge-flow' not in candidate_map and 'knowledge-flow' not in workspace
    assert all(group in menu for group in ('使用知识', '管理资料', '运行与质量'))
    assert "['使用知识',['qa','search','mindmap']]" in menu
    assert "['管理资料',['sources','records','ontology-model']]" in menu
    assert "['运行与质量',['runtime','quality']]" in menu
    assert "['projects','useradmin']" in menu
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
    assert '>检索与图谱<' in html
    assert '>知识问答<' in html
    assert '>知识检索<' not in html
    assert '>证据问答<' not in html
    assert "['使用知识',['qa','search','mindmap']]" in menu
    assert 'search-graph-workspace' in html
    assert all(region in html for region in ('search-results-pane','graph-stage','inspector-rail'))
    assert '从左侧检索结果选择实体或关系' in html
    assert all(control in html for control in ('graph-entity-filter','graph-entity-choice','graph-reset-view'))
    assert "endpoint('/search')" in workspace
    local_graph=workspace[workspace.index('window.drawGraph=async('):workspace.index('window.clearGraphWorkspace=')]
    assert re.search(r'if\(!id\)\{[^}]*return null;\}',local_graph)
    assert local_graph.index('if(!id)') < local_graph.index("'/graph-neighborhood'")
    assert 'seeds:[id]' in local_graph and 'hops:Number(hops)' in local_graph
    assert 'Number(hops)>10' in local_graph and 'Number.isInteger(Number(hops))' in local_graph
    assert all(control in local_graph for control in ('graph-direction','graph-max-nodes','graph-max-edges'))
    assert 'max_nodes:' in local_graph and 'max_edges:' in local_graph
    assert "'/graph-neighborhood',{...appliedScope,...neighborhood}" in local_graph
    assert 'truncated:Boolean(result.truncated)' in local_graph
    # Complete enumeration has its own explicit mode and session cursor loop.
    full_view=(ROOT/'full-graph-view.js').read_text(encoding='utf-8')
    full_graph=(ROOT/'full-graph.js').read_text(encoding='utf-8')
    assert html.index('/assets/full-graph.js') < html.index('/assets/full-graph-view.js')
    assert 'data-fg-mode="full"' in full_view and "mode='local'" in full_view
    assert 'new window.FullGraph.SessionLoader' in full_view and 'loader.begin(selected,fixed)' in full_view
    assert "'/graph-sessions'" in full_graph and "kind==='connected'" in full_graph
    assert 'this.cursor=page.next_cursor' in full_graph and 'this.resume()' in full_graph
    assert all(action in full_view for action in ('data-fg="next"','data-fg="auto"','data-fg="pause"','data-fg="restart"'))
    assert '/graph-neighborhood' not in full_graph and '/graph-sessions' not in local_graph
    assert '/explore' not in workspace
    assert 'await linkedSearch()' not in workspace
    assert "get('query').oninput" not in workspace
    assert "showTab('graph')" not in workspace
    assert "delete result.nodes" in workspace and "delete result.edges" in workspace


def test_combined_workspace_has_responsive_breakpoints_and_isolated_clearing_contracts():
    workspace=(ROOT/'workspace.js').read_text(encoding='utf-8')
    styles=(ROOT/'workspace.css').read_text(encoding='utf-8')

    assert 'grid-template-columns:minmax(240px,280px) minmax(0,1fr)' in styles
    assert '.inspector-rail' in styles
    assert '@media(max-width:1100px)' in styles
    assert '@media(max-width:700px)' in styles
    assert 'height:clamp(520px,calc(100dvh - 228px),1000px)' in styles
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
    composer=re.search(r'<form\b[^>]*\bid="qa-composer"[^>]*>(.*?)</form>',html,re.S)
    assert composer, '提问和发送按钮必须由同一提交表单承载'
    assert re.search(r'<textarea\b[^>]*\bid="qa-query"',composer[1])
    assert re.search(r'<button\b(?=[^>]*\bid="qa")(?=[^>]*\btype="submit")[^>]*>',composer[1])
    assert re.search(r'<input\b(?=[^>]*\bid="generate")(?=[^>]*\btype="checkbox")[^>]*>',composer[1])
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
    # All four views remain reachable, including attributes, while each request
    # reads only the selected kind through the server cursor pager.
    views=re.search(r'const VIEW_ORDER=\[([^]]+)\]',records)
    assert views and re.findall(r"'([^']+)'",views.group(1)) == ['entity','relation','attribute','chunk']
    assert 'if(!VIEW_ORDER.includes(key))return;' in records
    assert 'view=key;syncKindSelect();renderTabs();return loadPage();' in records
    criteria=records[records.index('const readCriteria='):records.index('function guard(')]
    assert '...scope(),...versionFields(),kinds:[view]' in criteria
    assert "query:$('record-search').value.trim()" in criteria
    assert "window.RecordCreate.createPager(body=>api('/api/projects/'+encodeURIComponent(current)+'/records/page',body))" in records
    assert "direction==='next'?pager.next():direction==='previous'?pager.previous():pager.load(criteria)" in records
    assert 'rows=result.records;total=result.total;page=result.page' in records
    creator=(ROOT/'record-create.js').read_text(encoding='utf-8')
    assert 'cursor:cursors[index],limit:50' in creator
    assert 'criteria=clone(next);cursors=[null]' in creator
    assert "ontology_scope:'all',ontology_ids:null" in records
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
    assert "知识构建失败" in sources
    assert "处理完成 / 待核验" in sources


def test_knowledge_ledger_is_the_instance_layer_home():
    """知识台账（B1）：实例层的家 —— 三视角 + 本体归属 + 原文断言 + 撤销入口 + 疑似重复。

    静态契约钉住的是"这几样东西存在且口径没被改回旧说法"，行为由
    tests/service/test_records_ledger_ui.py 用真浏览器验。
    """
    records=(ROOT/'records-view.js').read_text(encoding='utf-8')
    styles=(ROOT/'workspace.css').read_text(encoding='utf-8')
    menu=(ROOT/'menu-hierarchy.js').read_text(encoding='utf-8')
    index=(ROOT/'index.html').read_text(encoding='utf-8')
    # 一句话边界：这里管具体的东西，分类不在台账
    assert '具体的东西' in records and '分类不在这' in records
    assert '本体建模层' in records and '本体建模层' in records
    # 四个视角（页签）与旧的下拉不能并存成两套入口；属性这一维是用户点名要的，别再漏
    assert all(view in records for view in ('实体','关系','属性','原文片段'))
    assert 'dataset.ledgerView' in records and 'ledger-tabs' in records
    # 本体归属：必须能分辨当前发布与历史发布（C2 受控重分类的前提）
    assert '本体归属' in records and 'RecordVersionContext' in records
    # 原文断言：两条血缘都要认（候选血缘 discovery_candidate_id + 已归位血缘 canonical_record_id）
    assert 'discovery_candidate_id' in records and 'byCanonical' in records
    # 撤销：以前接口有、UI 点不到；现在行内与"可撤销的操作"里都要能点到
    assert '可撤销的操作' in records and '/undo' in records
    # 疑似重复是一个可点的数字，点了要摆出可用的卡片（不是一个死数）
    assert 'dataset.ledgerKpi' in records and 'duplicate-groups' in records
    # 台账的样式
    assert '.ledger-kpi' in styles and '.ledger-tabs' in styles and '.ledger-operation' in styles
    # 页面文案统一：知识与历史 → 知识台账（页签名、标题、菜单、指引同步）
    assert '知识台账' in index and '知识与历史' not in index
    # 页签名在 app.js 的 titles 映射里（menu-hierarchy.js 只存 id）
    app=(ROOT/'app.js').read_text(encoding='utf-8')
    assert "records:'知识台账'" in app and '知识与历史' not in app
    assert '知识与版本' not in index
    assert 'records' in menu
    # 撤销入口只能有一处：旧的内联「操作历史」列表不许再作为入口出现
    assert '操作历史' not in index
    assert '.entity-governance-panel #operations{display:none}' in styles
    assert "可在「知识台账 → 可撤销的操作」撤销" in (ROOT/'workbench.js').read_text(encoding='utf-8')
    assert "「知识台账 → 可撤销的操作」" in (ROOT/'evidence-inspector.js').read_text(encoding='utf-8')
    assert 'record-library-section' in records


def test_successful_document_submission_opens_task_logs_immediately():
    """提交成功要立刻把用户送到任务日志上（不让用户自己去找日志在哪）。

    D2 之后「后台任务」不再是独立页签（并进了「项目与运行」），所以跳转写成
    "进这一页 + 切到任务分区"——行为不变，路径变了。
    """
    workbench=(ROOT/'workbench.js').read_text(encoding='utf-8')
    ingest=workbench[workbench.index("bind('ingest'"):workbench.index('async function snapshots')]
    # 提交成功后显示「想看实时日志可点」的链接（点击才跳任务日志，不打断用户继续上传）。
    assert "想看实时日志可点" in ingest and "showRuntimeView('jobs')" in ingest


def test_knowledge_write_has_one_preview_host_and_an_advanced_record_import():
    class WorkbenchMarkup(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids=[]
            self.assets=[]
            self.copy=[]

        def handle_starttag(self, tag, attrs):
            attributes=dict(attrs)
            if 'id' in attributes:
                self.ids.append(attributes['id'])
            if tag == 'link' and 'href' in attributes:
                self.assets.append(attributes['href'])
            if tag == 'script' and 'src' in attributes:
                self.assets.append(attributes['src'])

        def handle_data(self, data):
            self.copy.append(data)

    markup=WorkbenchMarkup()
    markup.feed((ROOT/'index.html').read_text(encoding='utf-8'))
    page_copy=''.join(markup.copy)

    assert markup.ids.count('chunk-preview') == 1
    assert markup.ids.count('batch') == 1
    assert markup.ids.count('write-batch') == 1
    assert '结构化记录导入（高级）' in page_copy
    assert '不是批量上传文档' in page_copy
    # 只要求"带缓存版本号"，不锁死具体值：锁死会让每次改样式都得回来改测试，
    # 漏改时测试就变成"假红灯"而不是真契约（前端静态契约里也是这个约定）。
    for asset in ('workbench.css', 'workbench.js', 'workspace.js'):
        assert any(re.fullmatch(rf'/assets/{re.escape(asset)}\?v=[\w.-]+', item) for item in markup.assets), asset
