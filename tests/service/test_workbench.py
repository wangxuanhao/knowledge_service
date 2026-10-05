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
    assert "page.id='tab-discovery'" not in workspace
    assert "mode.id='extraction-mode'" in workspace
    details=(ROOT/'ontology-details.js').read_text(encoding='utf-8')
    assert 'ontology-details.js' in html
    assert all(label in details for label in ('审核草案并查看技术详情','版本差异','本体定义','名称与技术标识（可编辑）'))
    assert all(help_text in details for help_text in ('新增、保留或删除','Turtle 标准格式','不是乱码'))
    assert '受控重解析原文' not in workspace
    assert 'button:disabled{cursor:not-allowed}' in workspace_css
    assert 'contain:layout paint' in workspace_css and 'requestAnimationFrame(()=>{if(ui.graph===result||ui.graph===base)chart.resize();})' in workspace
    assert all(text in workspace for text in ('知识时间','当前状态','上一个已知变更节点','仅在切换节点时刷新','/timeline?limit=100'))
    assert 'tl-density' not in workspace and 'startPlayback' not in workspace
    assert 'candidate_status_counts' in ontology_workbench
    assert all(text in workspace for text in ('本体结构维护','新增本体术语','高级：本体历史版本与 Turtle 源码','可多选'))
    # 表单文案：IRI 是"系统生成、不用手写"，勾选靠 checklist（不再是"已保存的类型会自动勾选"）。
    assert all(text in workspace for text in ('新增实体类', '新增关系类型', '新增实体属性',
                                              '系统生成的标识 IRI（自动生成，不用手写）',
                                              # 措辞可以润色，语义必须保留：IRI 由后端统一生成、前端不拼。
                                              '最终 IRI 由后端', '统一生成',
                                              '允许的起点实体类型（可多选）',
                                              '允许的终点实体类型（可多选）'))
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
    assert all(label in ontology_workbench for label in ('待纳入','草案中','已收下','已物化'))
    assert '发布本体并映射候选' not in workspace
    candidate_map=(ROOT/'candidate-mindmap.js').read_text(encoding='utf-8')
    menu=(ROOT/'menu-hierarchy.js').read_text(encoding='utf-8')
    assert '/ontology-discovery/candidate-mindmap' in candidate_map
    assert '非正式知识' in candidate_map and '该聚合键不是正式实体 ID' in candidate_map
    assert all(text in candidate_map for text in ('开放解析完成后先在这里检查','进入本体建模层','查看正式脑图'))
    assert '正式重解析' not in candidate_map
    assert 'candidate-mindmap' in ontology_workbench
    assert 'knowledge-flow' not in candidate_map and 'knowledge-flow' not in workspace
    # P0 侧栏重构：3 组 10 入口 → 4 组 7 入口；分组维度改为
    # TBox（本体结构）/ ABox（实例知识）/ 消费 / 运行 —— 本体层与实例层是两个维度，
    # 不再混在旧「建模与治理」一组里。
    assert all(group in menu for group in ('本体层 · 结构', '实例层 · 知识',
                                           '消费层 · 用起来', '运行层 · 跑起来'))
    assert "['本体层 · 结构',['ontology-model']]" in menu
    assert "['实例层 · 知识',['ingest','records']]" in menu
    assert "['消费层 · 用起来',['search','qa','mindmap']]" in menu
    # D2：四个运行页并成一个入口（项目管理 / 项目总览 / 后台任务 / 快照·评测 → 运行层）
    assert "['运行层 · 跑起来',['runtime']]" in menu
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
    assert "['消费层 · 用起来',['search','qa','mindmap']]" in menu
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
    # 台账的查询范围＝台账的四个视角；属性（attribute）以前被这里滤掉了，界面上根本看不到它
    assert "filter.kinds=['entity','relation','attribute','chunk']" in records
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
    assert "构建成功" in sources


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
    # 本体归属：必须能分辨当前版本与旧版本（C2 受控重分类的前提）
    assert '挂在哪一版本体上' in records and '（旧版）' in records and '（当前）' in records
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
    assert "if(accepted){showRuntimeView('jobs');await jobList();}" in ingest


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
