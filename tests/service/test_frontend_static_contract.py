"""前端静态契约：防止"改了动态渲染、漏了静态占位"和"漏 bump 版本号"这类复发。

背景：停用影响面板曾在两处各写一份标记（workspace.js 弹窗模板 + 打开时重写 innerHTML），
只改一处会出现"新标记不生效、旧标签继续显示"的幽灵现象，且浏览器无报错。
"""
from pathlib import Path

import re

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
    """改动过的静态资源必须带版本号，否则浏览器缓存旧文件（"改了没生效"第一嫌疑）。

    这里只校验"带版本号 + 版本号不是空/占位"，不锁具体数值：锁死数值会让每次正常 bump
    都变成假红灯（本轮 9 项修复就 bump 了 8 个资源），假红灯会训练人忽略真红灯。
    本体三页与布局偏好必须都在列表里——它们是本轮新增/改动最多的资源。
    """
    html = read('index.html')
    versioned = set(re.findall(r'/assets/([\w.-]+)\?v=([\w.-]+)', html))
    for asset in ('workspace.js', 'workspace.css', 'ontology-details.js', 'projects.css',
                  'ontology-modal.css', 'style.css',
                  'provenance-drawer.js', 'provenance-drawer.css'):
        marker = dict(versioned).get(asset)
        assert marker, f'{asset} 缺少缓存版本号（?v=…）'
        assert marker not in ('', '0'), f'{asset} 版本号是占位值'


def test_provenance_assets_precede_history_hydration():
    html = read('index.html')
    # 版本值刻意不锁死（改了就要 bump，锁死会让"漏 bump"变成假红灯）：
    # 只要求这两个资源带缓存版本号，且顺序仍在 workbench.css 之后。
    assert re.search(r'/assets/provenance-drawer\.css\?v=[\w.-]+', html)
    assert re.search(r'/assets/provenance-drawer\.js\?v=[\w.-]+', html)
    # 别锁死 ?v= 的具体值：改了文件就要 bump，锁死只会制造假红灯（只要求带版本号）。
    assert re.search(r'/assets/evidence-inspector\.js\?v=[\w.-]+', html)
    assert re.search(r'/assets/workbench\.js\?v=[\w.-]+', html)
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


def test_ontology_modeling_layer_is_the_single_structure_entry():
    """P0-A2：结构层三页（审核台/编辑台/档案）合成一个「本体建模层」入口。

    侧栏不再有三个并列的本体页签；「本体建模层」是唯一控制结构的地方。
    旧三页的 JS/CSS 暂时保留（P1 把功能搬进画布后再删），但必须带缓存版本号。
    """
    html = read('index.html')
    sidebar = html.split('</nav>', 1)[0]
    workspace = read('workspace.js')
    menu = read('menu-hierarchy.js')
    # 结构层只剩「本体建模层」一个入口。
    assert html.count('>本体建模层</button>') == 1, '侧栏必须恰好一个「本体建模层」入口'
    # 旧的三个本体页签必须消失（合并被回滚的哨兵）。
    for retired in ('>本体审核台</button>', '>本体编辑台</button>', '>本体档案</button>',
                    '>本体工作台</button>'):
        assert retired not in html, retired
    assert 'data-tab="ontology"' not in sidebar
    assert '>本体管理</button>' not in sidebar
    assert "page.id='tab-discovery'" not in workspace
    assert "nav.dataset.tab='discovery'" not in workspace
    assert "'discovery'" not in menu
    assert "'ontology'" not in menu
    for name in ('records-view.js', 'task-review.js'):
        assert '[data-tab="ontology"]' not in read(name)
    # 本体建模层资源必须带缓存版本号 + 顺序正确（style/workspace 在前）。
    assert re.search(r'/assets/ontology-model\.css\?v=[\w.-]+', html)
    assert re.search(r'/assets/ontology-model\.js\?v=[\w.-]+', html)
    # 旧三页资源必须已从 index.html 卸载（文件已删除），这是"死代码清干净"的哨兵。
    for asset in ('ontology-workbench.css', 'ontology-workbench.js',
                  'ontology-design.css', 'ontology-design.js',
                  'ontology-archive.css', 'ontology-archive.js'):
        assert f'/assets/{asset}' not in html, f'{asset} 还在被加载（旧三页应已删除）'
    assert not (WEB / 'ontology-workbench.js').exists()
    assert not (WEB / 'ontology-design.js').exists()
    assert not (WEB / 'ontology-archive.js').exists()
    assert html.index('/assets/style.css') < html.index('/assets/ontology-model.css')
    assert html.index('/assets/workspace.js') < html.index('/assets/ontology-model.js')
    assert 'ontology-manager.html' not in html
    assert not __import__('re').search(r'\son(?:click|change|input|submit)=', html)
    # 侧栏分组里不再有旧本体页签；本体建模层是「本体层」组唯一入口。
    assert "'ontology-model'" in menu
    for retired in ('ontology-workbench', 'ontology-design', 'ontology-archive',
                    'candidate-mindmap', 'reviews'):
        assert f"'{retired}'" not in menu, f'{retired} 仍作为菜单入口（合并回滚了）'


def test_legacy_ontology_manager_entry_is_removed():
    """P0-4：旧的本体管理器入口必须已删除。

    它没有任何运行时引用（只在 `/assets` 挂载下仍可被直接访问），留着就是
    "第二套本体编辑入口" —— 数据入口分裂的源头。工作台的版本治理抽屉已接管其内容。
    """
    assert not (WEB / 'ontology-manager.html').exists()


def test_legacy_candidate_map_labels_embedded_evidence_as_preview():
    js = read('candidate-mindmap.js')
    html = read('index.html')
    assert 'source.evidence_preview' in js
    assert '来源预览' in js
    # 审核台已并入「本体建模层」：这里必须指向「本体建模层」，不能再出现旧名「本体工作台」
    assert '进入本体建模层' in js
    assert '本体工作台' not in js
    assert 'evidence_preview_truncated' in js
    assert "source.evidence||'未保存证据片段'" not in js
    # 别锁死 ?v= 的具体值（改了就要 bump，锁死只会制造假红灯），只要求带版本号。
    assert re.search(r'/assets/candidate-mindmap\.js\?v=', html)


def test_formal_evidence_location_labels_cover_verified_modes():
    js = read('evidence-inspector.js')
    for mode in ('exact', 'recovered_in_chunk', 'chunk', 'offset', 'passage', 'text', 'unlocated'):
        assert f'{mode}:' in js


def test_document_upload_workspace_contract():
    html = read('index.html')
    js = read('ingest-mode.js')
    css = read('workbench.css')
    source = html + js
    for marker in ('选择文件', '文件清单', '处理选项', 'doc-dropzone', 'doc-upload-summary',
                   'doc-clear-files', 'doc-retry-files', '.pdf', '.docx', '.html', '25 MB',
                   '最多 20 个', '100 MB'):
        assert marker in source, marker
    for marker in ('.document-upload-workspace', '.document-dropzone', '.document-queue-row',
                   '@media(max-width:850px)', ':focus-visible'):
        assert marker in css, marker
    assert '/assets/workbench.css?v=font-floor-11' in html
    assert '/assets/ingest-mode.js?v=document-upload-1' in html
    assert re.search(r'/assets/workbench\.js\?v=[\w.-]+', html)


# ============================================================================
# 2026-10-01 用户九项反馈的源码级契约：把"改好的地方别再退回去"钉住。
# 这些断言刻意针对"用户能看到的现象"，不是实现细节——具体变量名可以改，
# 现象不能丢（丢了就说明修复被回滚了）。
# ============================================================================


def test_provenance_guide_is_outside_the_cleared_panel():
    """证据溯源的"这一页怎么看"必须挂在常驻容器里。

    它以前是 chain 的第一个子节点，而 open()/render() 会 replaceChildren() 清空 chain，
    于是"读取中/读取失败"时说明一起被清掉，用户看到的是空抽屉（反馈：看不懂 E1/E2）。
    """
    js = read('provenance-drawer.js')
    assert 'guideHost' in js and 'ensureGuide' in js
    assert 'chain.replaceChildren(explainBlock())' not in js, '说明块又回到会被清空的容器里了'
    assert 'ensureGuide();chain.replaceChildren();' in js, 'render() 必须先保说明、再清卡片'
    for marker in ('E1 / E2', '给 LLM 的提示'):
        assert marker in js, marker
    # 标签名要能自解释，不能再叫"证据链 / API 数据"
    assert '原始接口数据' in js


def test_iri_preview_row_aligns_with_other_columns():
    """IRI 预览必须和其它字段同一行同一套 .columns 栅格（用户："这个和其他不在一行"）。"""
    workspace = read('workspace.js')
    assert '<div class="columns"><label class="ontology-term-iri">' in workspace
    assert 'ontology-term-iri-note' in workspace
    css = read('workspace.css')
    assert '.ontology-term-iri-note{align-self:center' in css, 'IRI 提示必须与输入同排对齐'


def test_type_color_comes_from_one_palette_module():
    """类型颜色只有一个出处：graph-palette.js（用户反馈"节点底下类型颜色和实体类型颜色都不统一"）。

    真因：颜色以前是"在数组里的下标"决定的（palette[index % n]），而筛选条那排圆点用按钮下标取色，
    第 0 项还是「全部类型」——每个类型的圆点整体错位一格，和图里节点颜色正好差一位。
    另一个坑：同一处传原始 type、另一处传显示名 label，哈希出来是两个颜色，必须统一用 label。
    """
    palette = read('graph-palette.js')
    assert 'function colorFor(' in palette and 'function colorMap(' in palette
    assert 'window.GraphPalette' in palette, '必须挂在 window 上供其它脚本用'
    workspace = read('workspace.js')
    assert 'catColor' not in workspace, '不能再按数组下标取色'
    # 圆点与节点/图例同 key：都用页面上显示的类型名
    assert "${dot(type?displayType(type):'')}" in workspace, '圆点必须用显示名取色'
    assert 'color:attribute?attrColor:colorFor(label)' in workspace, '节点必须用显示名取色'
    assert 'colorMap(scopeLabels)' in workspace, '同屏撞色要自动挪位'
    assert 'GraphPalette.colorFor' in workspace and 'GraphPalette.colorMap' in workspace


def test_same_type_same_color_across_pages_palette_is_deterministic():
    """颜色由类型名哈希决定，与顺序无关；同屏重色由 colorMap 兜底。"""
    palette = read('graph-palette.js')
    assert 'Math.imul' in palette, '用 FNV-1a 之类的稳定哈希，别用随环境变的算法'
    assert 'PALETTE[hash(key) % PALETTE.length]' in palette
    assert "if (used.has(color))" in palette, '缺少撞色挪位'


def test_graph_is_readonly_canvas_pan_and_click_but_no_node_drag():
    """图谱是**只读画布**：节点不可拖动（力导向布局只负责初始摆放，节点位置不承载编辑语义）。

    空白处拖动＝平移画布（ECharts 的 roam:true）；点节点/连线＝看详情；双击空白＝适应画布。
    以前那套「移动节点」zrender 拖动（bindNodeDrag + nearestNode）+ 开关按钮已整体移除：
    对一张检索浏览用的图，拖动节点是负担不是能力——布局会被拖乱，也偏离"正常画布操作"的直觉。
    """
    workspace = read('workspace.js')
    # 平移画布：ECharts graph 的 roam:true（空白处拖动）
    assert "type:'graph',layout:'force',roam:true" in workspace, '必须允许拖动空白处平移画布（roam）'
    # 点节点/连线看详情
    assert "chart.on('click'" in workspace, '点节点或连线必须能看详情'
    # 双击空白＝适应画布
    assert "getZr().on('dblclick'" in workspace, '双击空白处＝适应画布'
    assert 'bindPanAffordances' in workspace
    # 节点拖动必须整体移除：既没有 zrender 拖动实现，也没有开关按钮
    assert 'bindNodeDrag' not in workspace, '节点拖动实现应已移除'
    assert 'nearestNode' not in workspace, '节点拖动实现应已移除'
    assert 'dragNodes' not in workspace, '「移动节点」开关状态应已移除'
    html = read('index.html')
    assert 'id="graph-drag-nodes"' not in html, '「移动节点」开关按钮应已移除'
    assert 'id="graph-pan-hint"' in html, '缺少拖动/缩放操作提示'
    assert 'graph-palette.js' in html, '必须引入调色板模块'
    # 同上：按"带版本号"匹配，不锁死 fix13 这类具体值。
    assert html.index('graph-palette.js') < re.search(r'workspace\.js\?v=', html).start(), \
        '调色板要先于 workspace.js 加载'
    css = read('workspace.css')
    assert '.graph-canvas canvas{cursor:grab}' in css, '缺少可拖动的光标反馈'
    # 同类：候选图也是 roam + draggable，拖动同样会被节点吃掉
    assert 'draggable:false' in read('candidate-mindmap.js'), '候选图也必须关掉节点拖动'


def test_pan_hint_says_what_each_action_does():
    """提示行要写清各动作做什么，不能只放一个"重置视图"了事。"""
    hint = read('index.html')
    assert 'graph-pan-hint' in hint
    # 只读画布的四件事：平移、缩放、适应画布、点节点看详情（不再有"移动节点"）
    for phrase in ('平移', '缩放', '适应画布', '看详情'):
        assert phrase in hint, f'提示行缺少「{phrase}」'
    assert '移动节点' not in hint, '提示行不该再提「移动节点」（节点不可拖动）'


def test_display_blocks_that_can_be_hidden_also_pin_the_hidden_attribute():
    """JS 里会被 .hidden=true 的块，如果作者样式又给了 display:flex/grid，必须显式补 [hidden]{display:none}。

    坑：UA 样式表的 [hidden]{display:none} 会被作者样式里的 display 盖掉，于是"隐藏"的块
    变成一条**空边框**还占着版面。真机探针 2026-10-05 抓到过一次（台账那行"还挂在旧本体上"），
    ontology-archive.css 里也早写过同样的注释。这是源码级守卫：删掉那条 [hidden] 规则即变红。
    """
    flat = ''.join(read('workspace.css').split())
    assert '.ledger-stale-notice{display:flex' in flat, '这行提示应该是横排的'
    assert '.ledger-stale-notice[hidden]{display:none}' in flat, \
        '这条提示会被 JS 隐藏，必须显式补 [hidden]{display:none}，否则剩下一个空边框条'
    assert "stale_notice.hidden=true" in read('records-view.js').replace(' ', ''), \
        '渲染函数里没有再隐藏它？那这条守卫失去意义，请顺手核对'


def test_reclassify_operation_reads_in_chinese_on_the_operations_list():
    """迁完要在「可撤销的操作」里看得懂：操作种类必须有中文名，不能漏成英文动词。"""
    js = read('records-view.js')
    assert "'reclassify'" in js
    assert '迁到新本体' in js


def test_snapshot_page_has_exactly_one_restore_entry_point():
    """快照恢复只能有一个入口：快照卡片上的「恢复」。

    旧的那套 1px 隐藏表单（select + checkbox + button）既看不见又和卡片重复；
    E 规则「一屏一个实心按钮」量到它时才发现：同一屏上出现了两个"恢复"实心按钮。
    """
    html = read('index.html')
    workbench = read('workbench.js')
    for retired in ('id="snapshot-list"', 'id="snapshot-confirm"', 'id="restore-snapshot"'):
        assert retired not in html, f'旧的隐藏快照控件 {retired} 必须已删除'
    assert "bind('restore-snapshot'" not in workbench
    assert 'data-snapshot-restore' in workbench, '卡片上的「恢复」必须还在（唯一入口）'


def test_runtime_page_merges_four_flat_pages_into_one_entry():
    """D2：项目管理 / 项目总览 / 后台任务 / 快照·评测 四个平铺菜单 → 「项目与运行」一个入口。

    为什么合并：那四页回答的其实是同一个问题——"这个项目跑得怎么样"，
    平铺成四个菜单只会让用户在页签之间来回找（用户原话："界面不要一直是平铺的"），
    而它们又都不是日常动作（都不改知识、不改本体）。

    契约钉住三件事，缺一件合并就会退回去：
      · 侧边栏只剩 7 项，旧的四项不再作为独立页签；
      · 四个旧页面成为四个分区，每个分区都写清"这里回答什么"；
      · 分区数据由 RuntimeView 在**进入时**加载，且加载函数留在各自模块里（不复制第二份）。
    """
    html = read('index.html')
    sidebar = html.split('</nav>', 1)[0].split('<nav>', 1)[1]
    entries = re.findall(r'data-tab="([\w-]+)"', sidebar)
    assert len(entries) == 7, entries
    assert entries.count('runtime') == 1
    for retired in ('projects', 'dashboard', 'jobs', 'evaluation'):
        assert f'data-tab="{retired}"' not in sidebar, f'{retired} 仍是独立页签（合并被回滚了）'
    for label in ('>项目管理</button>', '>项目总览</button>', '>后台任务</button>', '>快照 / 评测</button>'):
        assert label not in sidebar, label

    # 「做什么 / 不做什么」必须写在页面上：只给一个入口名，用户还是不知道这一页管什么。
    hero = html.split('class="runtime-hero"', 1)[1].split('</header>', 1)[0]
    assert '跑得怎么样' in hero
    for elsewhere in ('知识台账', '本体建模层'):
        assert elsewhere in hero, f'页头没写清"这件事不在这做，去{elsewhere}"'

    # 四个分区，每格都要有名称 + 一句说明（只有一个名词的分区等于没写）
    views = re.findall(r'data-runtime-view="(\w+)"', html)
    assert views == ['project', 'overview', 'jobs', 'snapshots'], views
    for view in views:
        block = html.split(f'data-runtime-view="{view}"', 1)[1].split('</button>', 1)[0]
        assert '<b>' in block and '<small>' in block, f'分区 {view} 缺少名称或说明'

    # 面板沿用旧 id（既有代码靠这些 id 判断"现在在哪一屏"），但都必须挂上 runtime-panel
    for panel in ('tab-projects', 'tab-dashboard', 'tab-jobs', 'tab-evaluation'):
        assert f'id="{panel}" class="tab hidden runtime-panel"' in html, panel

    # 加载口径只有一处：RuntimeView 的注册表；四个模块各自把加载函数交上来
    runtime = read('runtime-view.js')
    assert 'window.RuntimeView' in runtime and 'on(view,loader)' in runtime
    assert "const VIEWS=['project','overview','jobs','snapshots']" in runtime
    for name, view in (('workspace.js', 'overview'), ('task-review.js', 'jobs'),
                       ('projects-view.js', 'project'), ('workbench.js', 'snapshots')):
        assert f"RuntimeView?.on('{view}'" in read(name), f'{name} 没把 {view} 分区的加载函数交给 RuntimeView'

    # 合并之后不该再有人按旧页签名找入口（运行时那会是读一个 null 的属性）
    for name in ('workspace.js', 'task-review.js', 'workbench.js', 'projects-view.js'):
        source = read(name)
        for retired in ('projects', 'dashboard', 'jobs', 'evaluation'):
            assert f'[data-tab="{retired}"]' not in source, f'{name} 还在引用旧页签 {retired}'

    # 导航分组：旧的「项目」「运行与质量」两组并成一组
    menu = read('menu-hierarchy.js')
    assert "['运行层 · 跑起来',['runtime']]" in menu
    assert "['projects','dashboard']" not in menu and "['jobs','evaluation']" not in menu
    # 只有一个入口的分组不该默认折叠（折叠了用户就找不到它在哪）
    assert 'available.length===1' in menu

    # 新增资源要引入；改动过的资源都要带缓存版本号（否则浏览器一直用旧 JS）
    for asset in ('runtime.css', 'runtime-view.js', 'app.js', 'workspace.js', 'menu-hierarchy.js',
                  'task-review.js', 'workbench.js', 'projects-view.js', 'storage.js'):
        assert re.search(rf'/assets/{re.escape(asset)}\?v=[\w.-]+', html), asset


# ============================================================================
# 本体建模层用户反馈的源码级契约（2026-10-05）
# 用户原话："本体层界面展示的只是父类和子类是吧，关系没办法显示，然后这块发布了版本后
# 不能继续变更了不对吧……就是我点进来能直接修改内容哈，现在父类也加不了，
# 层级视图还不居中，层级改成层级树吧"
# 同上一节：断言刻意针对"用户能看到的现象"，具体变量名可以改，现象不能丢。
# ============================================================================


def test_relation_endpoints_section_explains_why_and_backfills_from_real_usage():
    """关系详情的「两端」区：说清为什么画不出线，并能按**实际用法**一键回填。

    用户看到的现象是：左栏写「关系类型 9」、统计栏写「关系 0」，同一屏两个数字打架，
    画布上一条关系线都没有。真因是抽出来的关系类型没有 rdfs:domain/range，
    而画布只在两端都在图里时才画得出线。
    """
    panel = read('ontology-model-panel.js')
    assert '/ontology/relation-usage' in panel
    # 回填走草案命令通道，且两端各一条（只有 domain 也画不出线）
    assert "'add_domain'" in panel and "'add_range'" in panel
    assert 'action: t.action, target_iri: rel.id, value: t.value' in panel
    # 画布画的是草案时必须带 draft_id 问，否则回填完还显示"未声明"（又是一个"改了没反应"）
    assert '&draft_id=' in panel
    # 统计口径只在服务端：前端不许自己数一遍
    assert 'current_records' not in panel
    # 没有依据时不猜
    assert '读不到实际用法' in panel
    assert '宁可不补，也不猜' in panel


def test_hierarchy_view_is_renamed_and_fitted_to_the_band_frame():
    """「分层视图」改名「层级树」，且居中按**层带外框**算（不是节点外接框）。

    真机读数：3 条层带 x=117.7 宽 848.5，容器只有 902 → 右边缘 966 已被切掉 64px，
    左边却空出 118px。根因是 Cytoscape 预设布局的 fit 拟合的是**节点**，
    而层带是后加的、更宽的一圈 SVG 覆盖层 —— fit 根本不知道它存在。
    """
    model = read('ontology-model.js')
    canvas = read('ontology-model-canvas.js')
    assert '层级树' in model, '视图页签必须叫「层级树」'
    assert '分层视图' not in model, '旧名「分层视图」必须彻底消失'
    # 摆位与缩放分开：preset 不 fit，改成按层带外框自己算 zoom/pan
    assert 'fit: false' in canvas
    assert 'function fitBox(box, pad)' in canvas
    assert 'function drawBands(bands)' in canvas
    # drawBands 必须把层带外框交回去当居中判据：左边界含 NODE_W/2 + BAND_PAD、
    # 宽度含两侧 BAND_PAD（曾经只从 -BAND_PAD 起，最左列的左半边露在带外，
    # 右边缘又被容器切掉，"居中"永远差一截）。
    assert 'return { x1: -NODE_W / 2 - BAND_PAD' in canvas, \
        'drawBands 必须把层带外框交回去当居中判据'
    # 层号标签画在带内侧：以前在带外左侧会把外框撑偏，"居中"永远差一截
    assert 'lx: mx + 4' in canvas, '层号标签必须画在层带内侧左上角（随带左/顶缘走）'


def test_canvas_has_a_create_entry_because_without_it_parent_cannot_be_added():
    """画布必须有「新建类」入口 —— 否则就是"父类加不了"。

    整页原先只有「改父类（连线）」，而它要求先选中一个**已存在**的类；空本体上没有
    任何地方能新建，于是想加父类无从下手。
    """
    canvas = read('ontology-model-canvas.js')
    panel = read('ontology-model-panel.js')
    assert '＋ 新建类' in canvas
    assert "'父类（选填：不选就是顶层类）'" in panel, '新建类必须能直接带父类'
    assert "'/ontology/terms'" in panel, '新建要走与审核台同一条路（POST /ontology/terms），不自己拼 IRI'
    assert 'focusCreate' in canvas and 'focusCreate' in panel


def test_canvas_draws_the_draft_so_edits_are_visible_and_editable_after_publish():
    """画布画的是**草案**：改完立刻看得见；发布之后也能接着改（自动开新草案）。

    以前只读已发布本体 → 编辑写进草案、画布却按已发布版本画，改了半天屏幕没变化；
    而且发布成功后 `S.draft` 仍是那份终态草案，下一笔编辑被服务端回一句"已经收下"
    —— 用户看到的就是"发布了就不能再改了"，刷新一下才好。
    """
    model = read('ontology-model.js')
    assert 'showingDraft' in model, '必须能分辨画布上画的是草案还是已发布本体'
    assert '/ontology-drafts/${draft.id}' in model, '展示草案要读草案自己的结构'
    assert '草案 · 未发布' in model and '已发布本体' in model
    # 终态草案不能再改：必须看状态，不能只看"有没有"
    assert 'OPEN_STATES.includes(S.draft.status)' in model


def test_canvas_stats_separate_relation_types_from_drawn_edges():
    """统计栏把「关系类型 N」与「画得出线 M」分开说 —— 两个数字打架是这轮反馈的起因。"""
    assert '关系类型 ${relTotal}（画得出线 ${rel}）' in read('ontology-model-canvas.js')


def test_ontology_model_shows_discovery_guide_when_not_yet_published():
    """没发布本体、但有开放发现候选时，本体建模层不能空白/「读取失败」。

    用户原话：「开放知识写入后，本体建模没内容」。真因是 refresh() 只读已发布本体
    （没发布 = 404）和治理草案，从不读 /ontology-discovery，也没有归纳入口 ——
    discovery 模式抽出一堆候选后，点进本体建模层就是一片空白。
    """
    model = read('ontology-model.js')
    # 读开放发现概览，拿到「是否已发布本体 + 待纳入候选数」
    assert "'/ontology-discovery'" in model
    # 没发布本体时走引导态，不再误报「读取失败」
    assert 'S.discovery.published === false' in model
    # 有候选时给生成本体入口；这一步默认秒级、不调模型
    assert "'/ontology-discovery/drafts'" in model
    assert '生成本体（约 1 秒）' in model
    # 「模型推断父子层级」是可选的慢入口（默认不阻塞拿到本体）
    assert 'om-infer-hierarchy' in model
    # 这一步做什么 / 不做什么都要写清（用户核心要求：新功能一句话说明它做什么、不做什么）
    assert '这一步做什么' in model and '不做什么' in model


def test_ontology_model_pending_candidates_chip_reads_real_discovery_count():
    """底栏「待收下」计数必须读开放发现概览的真实计数，不能永远显示 0。

    旧实现拿画布虚线节点的 candidates.length 当计数，而那一整路从没接过数据源、
    恒为 0 —— 用户会看到「待收下 0」和左栏「还有 N 个待纳入」互相打脸。
    """
    panel = read('ontology-model-panel.js')
    assert 'unpublished_candidate_count' in panel


def test_ontology_model_empty_actionbar_focuses_on_induction_not_disabled_controls():
    """没本体时，底栏不摆「校验并审核 / 发布 / 发布说明输入框」这一堆 disabled 控件。

    用户反馈「底下的校验和发布是两行」：根因是没本体时仍渲染那三个控件，
    发布说明输入框最小 180px，flex-wrap 放不下就把按钮挤成两行。
    没本体时底栏应聚焦一件事——把候选归纳成本体。
    """
    panel = read('ontology-model-panel.js')
    assert 'renderEmptyActions' in panel
    assert 'if (!summary) { renderEmptyActions(st); return; }' in panel


def test_ontology_model_version_badge_does_not_show_v_placeholder():
    """没本体时顶栏不能停在占位「当前本体 v?」，要说清「尚未发布本体」。

    用户反馈「上面还有个 v?」。renderSidebar 在没 summary 时提前 return，
    没更新 #om-version，它就停在 buildSkeleton 的初始占位上。
    """
    model = read('ontology-model.js')
    assert '尚未发布本体' in model


def test_ontology_model_offers_incremental_induction_after_publish():
    """已发布本体之后又抽出新候选，左栏要给「增量归纳」入口。

    用户问「后续再添加新的知识的时候如何显示」——否则新知识只会静默躺在
    discovery 里，用户不知道还能再次归纳进本体。
    """
    model = read('ontology-model.js')
    assert 'om-generate-draft-inline' in model
    assert '新候选' in model


def test_ontology_model_allows_inline_constraint_editing():
    """右栏必须能编辑 domain/range/数据类型/父类——不能只给只读清单。

    用户原话：\"编辑 domain range 这些、限制啥的都不能操作，那怎么算是本体建模层\"。
    后端命令通道一直支持，缺的是前端控件。
    """
    panel = read('ontology-model-panel.js')
    assert 'constraintEditSection' in panel
    assert "'add_domain'" in panel and "'add_range'" in panel
    assert "'set_datatype'" in panel and "'add_parent'" in panel
    # 父类下拉必须排除当前类自身：类不能是自己的父类（真机探针抓到自环 target==value）
    assert "kind === 'class'" in panel
    assert "o.id !== term.id" in panel


def test_ontology_model_assets_are_versioned_after_this_round():
    """改动过的本体建模层资源必须带缓存版本号（漏 bump = 浏览器继续用旧 JS）。"""
    html = read('index.html')
    for asset in ('ontology-model.js', 'ontology-model-canvas.js', 'ontology-model-panel.js',
                  'ontology-model-panel.css'):
        assert re.search(rf'/assets/{re.escape(asset)}\?v=[\w.-]+', html), asset


# ══════════════════════════════════════════════════════════════════════════════
# 版本管理（发布说明必填 / 版本管理抽屉 / 查看某一版 / 回到某一版）
#
# 这一组钉住"界面上的入口与文案确实存在"：动态渲染的东西漏了静态占位，
# 浏览器不会报错，用户只会看到少了按钮 —— 靠服务端契约测试查不出来。
# ══════════════════════════════════════════════════════════════════════════════

def test_release_button_is_now_version_management():
    """顶栏入口从「版本历史」升级为「版本管理」，且带上预览横幅与退出按钮。"""
    js = read('ontology-model.js')
    # 按钮上的**可见文案**必须是「版本管理」：叫「版本历史」用户会以为还是只读列表
    assert '版本管理</button>' in js, '顶栏入口文案不是「版本管理」'
    assert '版本历史' not in js, '旧文案「版本历史」残留（含注释，会误导下一个改这里的人）'
    assert 'om-preview-banner' in js and 'om-preview-exit' in js


def test_version_management_offers_view_and_go_back():
    """版本管理必须同时提供「查看这一版（只读）」与「回到这一版」——
    只有查看没有回退等于没回答用户的问题；只有回退没有查看则没人敢按。"""
    panel = read('ontology-model-panel.js')
    assert '查看这一版' in panel
    assert '回到这一版' in panel
    # 回退不能悄悄改写历史：文案要自己说清它是"再发一版"
    assert '不改写历史' in panel


def test_revert_is_an_explicit_source_kind_with_acknowledgement():
    """回到某一版必须用显式的 revert 来源，并且要人勾过回退警告。"""
    model = read('ontology-model.js')
    panel = read('ontology-model-panel.js')
    assert "source_kind: 'revert'" in model, '回退必须用 revert 来源（服务端只对它放行历史基线）'
    assert 'om-ab-warning' in panel, '缺少发布警告逐条勾选（回退那条警告必须真的被人勾过）'
    assert 'ackWarnings' in panel
    # 历史版本只读：预览态要锁住编辑入口，并说明去哪解锁
    assert 'om-ctx.is-preview' in read('ontology-model.js') or 'is-preview' in model


def test_ontology_model_offers_rebase_when_draft_is_stale():
    """草案过期时（服务端推导 needs_rebase 为 stale_base 或 stale_source），底栏必须有出口。

    操作手册第 9 章「实体类型关闭」如实标注的已知卡点：停用变更进草案后，发布被
    publish-readiness 的 base_outdated 硬阻断，但整个单画布找不到「重新基线」按钮 ——
    用户只能"回到最新重开草案"（等于丢掉已做的停用等变更）。服务端 rebase 接口
    （POST /ontology-drafts/{id}/rebase）一直都在，缺的只是单画布这一处入口。
    两类过期（stale_base 基线落后 / stale_source 来源快照变了）都走同一个接口，文案区分。
    """
    panel = read('ontology-model-panel.js')
    assert 'rebaseDraft' in panel, '单画布必须实现「重新基线/刷新来源」处理器'
    assert "'/rebase'" in panel, '两类过期都打 POST /rebase 接口，不是前端自己拼结构'
    assert "rebaseKind === 'stale_base'" in panel and "rebaseKind === 'stale_source'" in panel, \
        '按钮在服务端判定 stale_base 或 stale_source 时出现，前端不自算'
    assert '重新基线' in panel, 'stale_base 的按钮可见文案必须是「重新基线」'
    assert '刷新来源' in panel, 'stale_source 的按钮可见文案必须是「刷新来源」'


def _rule_body(css: str, selector: str) -> str:
    """取某条选择器的声明块内容（找不到返回空串）。选择器按文本精确匹配，避免正则转义地狱。"""
    start = css.find(selector + '{')
    if start < 0:
        start = css.find(selector + ' {')
    if start < 0:
        return ''
    open_brace = css.index('{', start)
    return css[open_brace + 1:css.index('}', open_brace)]


# 选择器里出现这些就是"状态/排除/伪元素"变体，宽度继承自基础规则，不该单独要求定宽
_STATE_SUFFIX = (':checked', ':hover', ':focus', ':disabled', ':focus-visible', ':active')


def _positive_checkbox_selectors(css: str) -> list[str]:
    """挑出"**正向**给勾选框/单选框定样式"的选择器组。

    排除三类误报（都真实存在于本仓库）：
    - `input:not([type=checkbox])` 这种把勾选框**排除在外**的规则（theme.css 的全局表单皮肤）；
    - `:checked{...}` / `::before{...}` 这类状态与伪元素变体（宽度继承自基础规则）；
    - 选择器组里混着别的目标（如 `body main .check input, body main input[type=checkbox]`）。
    """
    found = []
    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
        group, body = m.group(1).strip(), m.group(2)
        parts = [p.strip() for p in group.split(',')]
        positive = [p for p in parts
                    if ('checkbox' in p or 'radio' in p)
                    and ':not([type=' not in p.replace(' ', '')
                    and not p.endswith(_STATE_SUFFIX)
                    and '::' not in p]
        # 组里只要有一个不是"点名的勾选框"，就说明这条规则不只服务勾选框 → 跳过
        if positive and len(positive) == len(parts):
            found.append((group, body))
    return found


def test_checkbox_rows_pin_their_width_against_the_global_input_rule():
    """放进 flex 行的勾选框必须**显式定宽**，否则会长成整行。

    `style.css` 第 1 行有一条全局 `input,select,textarea{width:100%}`。勾选框写
    `flex:0 0 auto` 时 flex-basis 取 auto → 解析成 `width:100%`，于是它吃掉整行、
    把相邻文字挤成**一行一个汉字**。真机实测（本体建模台回退警告）：
    勾选框 981px、说明文字只剩 35px、动作条被撑到 729px 高；
    修好后同一处是 15px 勾选框 + 1341px 文字（一行放下），动作条 216px。
    DOM 契约测试全绿也发现不了（它只断言文字/类名，不量像素），所以要单独钉住。

    这条是**源码级**证伪（便宜、跑在全量里）；像素级证伪在真机探针里
    （scratch/probe_ab_layout.py，1680/1280/1100 三档量 span 宽与行数）。
    """
    # ① 正向点名的勾选框/单选框规则，必须自己定宽
    offenders = []
    for path in sorted(WEB.glob('*.css')):
        css = re.sub(r'/\*.*?\*/', '', path.read_text(encoding='utf-8'), flags=re.S)
        for group, body in _positive_checkbox_selectors(css):
            if 'width' not in body:
                offenders.append(f'{path.name} :: {" ".join(group.split())}')
    assert not offenders, (
        'These checkbox/radio rules do not set a width — the global input{width:100%} '
        'will stretch them to a full row: ' + ' | '.join(offenders))

    # ② 用**裸 input** 选择器给"勾选框所在行"定样式的地方，选择器上看不出它是勾选框，只能点名。
    #    新增同类行（flex 行里的裸 input 勾选框）时把它加进这张表。
    for name, selector in (('ontology-model-panel.css', '.om-ab-warning input'),):
        body = _rule_body(read(name), selector)
        assert body, f'{name} 里找不到 `{selector}` 的声明块 —— 选择器改名了？'
        assert 'width' in body, (
            f'{name} 的 `{selector}` 没定宽：全局 input{{width:100%}} 会把勾选框撑满整行，'
            '相邻文字会被挤成一行一个汉字')


# ============================================================================
# 知识写入 = 纯文件上传（2026-10-05）
# ============================================================================

def test_ingest_page_is_plain_file_upload_without_chat_or_textbox():
    """写入页只有「文件上传」：没有对话流、没有正文文本框、没有\"粘贴正文\"入口。

    用户拍板：「这个输入框没必要了……不行就是一个文件上传就行了。」
    钉住三件事：① 文件拖放区与文件清单在；② 对话流三件套与正文框**彻底不存在**；
    ③ 旧对话流脚本/样式不再被 index.html 加载（否则它会自动挂回对话层）。
    """
    html = read('index.html')
    ingest = html.split('id="tab-ingest"', 1)[1].split('id="tab-records"', 1)[0]
    # 文件上传主路径仍在
    for keep in ('id="doc-file"', 'id="doc-dropzone"', 'id="doc-file-list"', 'id="ingest"'):
        assert keep in ingest, f'文件上传通道被误删了：{keep}'
    # 对话流 / 正文框 / 粘贴模式入口都不在了
    for gone in ('ingest-flow', 'id="doc-text"', 'document-input-mode', '粘贴正文'):
        assert gone not in ingest, f'已废弃的 {gone} 又回到写入页'
    # 旧对话流资源不再加载，新的纯文件上传资源要带版本号加载
    assert 'ingest-flow.js' not in html and 'ingest-flow.css' not in html, '旧对话流脚本/样式还在加载'
    assert 'ingest-upload.js?v=' in html and 'ingest-upload.css?v=' in html, '纯文件上传资源未带版本号引入'


def test_ingest_mode_selector_is_promoted_to_top_and_explained():
    """「解析模式」是\"文件会被怎样处理\"的唯一总开关：必须置顶可见、三档、带说明。

    用户上一轮的困惑就是\"不知道文件会被正式入图还是只存候选\"。钉住：
      ① 面板顶部有占位槽 #ingest-mode-slot；② workspace.js 把模式选择器注入该槽；
      ③ 三档齐全（正式入图 / 候选暂存 / 仅文档）；④ 每档有中文说明。
    """
    html = read('index.html')
    ingest = html.split('id="tab-ingest"', 1)[1].split('id="tab-records"', 1)[0]
    assert 'id="ingest-mode-slot"' in ingest, '面板顶部缺少解析模式占位槽'
    js = read('workspace.js')
    assert "getElementById('ingest-mode-slot')" in js, '没有把解析模式注入顶部占位槽'
    for option in ('使用项目本体', '开放本体发现', '仅文档检索'):
        assert option in js, f'解析模式缺少档位：{option}'
    assert 'extraction-mode-help' in js, '切换模式时没有给一句人话说明'


def test_ingest_submit_stays_on_the_page_instead_of_jumping_to_jobs():
    """点「上传并处理」后留在写入页：逐文件结果就地显示，后台任务只作可选入口。

    旧逻辑提交成功就 showRuntimeView('jobs')，等于把人赶走。钉住：
      workbench.js 提交成功分支不再强制跳转，改为在本页 #doc-submit-results 里给结果，
      并提供一个\"查看后台任务进度\"的可选按钮。
    """
    js = read('workbench.js')
    # 提交成功分支不再无条件 showRuntimeView('jobs')：改为本页给结果 + 一个可选入口。
    assert '查看后台任务进度' in js, '没有给可选的后台任务入口'
    # 不允许「提交成功 → 直接强制跳转」这种形态（showRuntimeView 只能出现在可选按钮里）
    assert not re.search(r"if\(accepted\)\s*\{\s*showRuntimeView", js), \
        '提交成功后仍在强制跳转后台任务页'
    assert 'results.append(view)' in js, '提交结果没有留在本页'


def test_ingest_keeps_advanced_options_and_structured_import():
    """高级解析设置与结构化 JSON 导入仍然可达：删的是对话流，不是这些能力。"""
    ingest = read('index.html').split('id="tab-ingest"', 1)[1].split('id="tab-records"', 1)[0]
    for keep in ('id="doc-title"', 'id="write-batch"', 'parse-settings'):
        assert keep in ingest, f'被误删的能力：{keep}'
    assert '结构化记录导入（高级）' in ingest, '结构化记录导入被误删了'


def test_ingest_results_drawer_uses_sse_and_assertions_with_real_data():
    """结果抽屉：进度走 SSE、来源走正式 assertions 表，数据全部真实。

    用户诉求（2026-10-05）：① 别再靠 ID 前缀猜来源；② 轮询改成 SSE；
    ③ 实体要显示本体类型。
    钉住：① 入口按钮存在；② ingest-results.js/css 带版本号；③ 脚本挂 .ingest-drawer、
    订阅 /api/jobs/{id}/stream（SSE）并读 endpoint('/assertions?document_id=...')；
    ④ 有实体／关系／属性三类胶囊渲染分支；⑤ 有对应样式。
    """
    html = read('index.html')
    ingest = html.split('id="tab-ingest"', 1)[1].split('id="tab-records"', 1)[0]
    assert 'id="ingest-results-slot"' not in ingest, '旧页面堆叠槽未移除'
    assert 'id="open-ingest-results"' in ingest, '缺少结果工作区入口按钮'
    assert 'ingest-results.js?v=' in html and 'ingest-results.css?v=' in html, \
        '脚本/样式未带版本号引入'

    js, css = read('ingest-results.js'), read('ingest-results.css')
    wb = read('workbench.js')
    # 属性抽取开关：页面有 checkbox，且 readParseSettings 把它传后端
    # （根因：extract_attributes 默认 False、UI 无开关，导致属性永远抽不到）
    # id 与关系约束选择器按活文档 task_review_ui.test.cjs 对齐
    assert 'id="parse-attributes"' in ingest, '高级设置缺少「抽取实体属性」开关'
    assert "extract_attributes:$('parse-attributes').checked" in wb, \
        'readParseSettings 没有把属性开关传给后端'
    assert 'id="parse-relation-constraints"' in ingest, '缺少关系约束模式选择器'
    assert "relation_constraint_mode:$('parse-relation-constraints').value" in wb, \
        'readParseSettings 没有传关系约束模式'
    # 抽屉 + SSE + assertions（真实口径，非写死）
    assert "'section', 'ingest-drawer'" in js, '没有构建右侧抽屉'
    assert "'/stream'" in js and 'splitSseBuffer' in js, '没有消费 SSE 进度流'
    assert "endpoint('/assertions?document_id=" in js, '没有按文档读正式断言'
    # 三类知识渲染分支 + 进度条 + 超量折叠
    for fn in ('assertChip', 'renderAsserts', 'loadAssertions', 'streamJob',
               'CHIP_PREVIEW_LIMIT'):
        assert fn in js, f'工作区缺少渲染分支：{fn}'
    for klass in ('.ingest-drawer', '.ingest-job__bar', '.ingest-result__chip',
                  '.ingest-result__group', '.ingest-result__more'):
        assert klass in css, f'样式缺少 {klass}'


def test_ingest_results_drawer_shows_entity_type_and_status_honestly():
    """实体要带类型、区分入图/待确认；无知识与失败都如实说，并能关闭。

    钉住：① 实体胶囊拼「名字 · 类型短名」；② accepted/pending 用 is-pending 区分；
    ③ 空知识说"没抽出"；④ 失败保留原文收据；⑤ 提交后自动滑出、Esc 可关。
    """
    js = read('ingest-results.js')
    # 实体类型：已入图用短 IRI；待确认候选字段不同（proposed_type），不许渲染成 —
    assert "shortIri(p.type)" in js, '已入图实体没有显示本体类型'
    assert 'proposed_type' in js, '待确认实体没用提议类型（会渲染成 —）'
    assert 'p.subject' in js and 'p.predicate' in js and 'p.object' in js, \
        '待确认关系没用 主语/谓词/宾语 名字（会渲染成 —）'
    # 待确认属性字段是 proposed_type（不是关系用的 predicate），否则属性也会显示空
    assert "safe(p.proposed_type) + ' = '" in js, '待确认属性没用提议属性名（会渲染成 —）'
    assert "is-pending" in js, '没有区分待确认状态'
    # 进度不许回退：snapshot 只填空、progress 单调递增
    assert "d.status === 'queued'" in js, 'snapshot 会覆盖实时状态'
    assert 'next >= (d.progress || 0)' in js, '进度条没有防回退（会从 97% 跳回 15%）'
    # 开关（watch 由 workbench 提交后调用，不是 setTimeout 轮询那套）
    assert 'function watch' in js, '缺少订阅入口'
    assert "e.key === 'Escape'" in js, '不支持 Esc 关闭'
    # 诚实文案
    assert '没有抽出实体' in js, '没有如实说明"本次没抽到知识"'
    assert '原文收据已保留' in js, '失败时没有说明原文不丢'


def test_attribute_is_kept_when_value_is_a_literal_not_an_object_property():
    """标量属性不许被「属性值命中实体」启发式错误改判成关系（2026-10-05 根因）。

    真机复验抓到：NER 误把字面量值（50000元/已完成）建成 DataEntity 实体；旧逻辑只凭
    属性值文本命中该实体就把属性改判关系，导致标量属性全跑去关系组、属性组为空。
    钉住改判关系的三条必要条件：① 值唯一命中实体；② 谓词在本体里是**对象属性**；
    ③ 命中实体不是 DataEntity 字面量兜底类。
    """
    adapter_path = (Path(__file__).resolve().parents[2]
                    / 'knowledge_service' / 'integrations' / 'semantica_adapter.py')
    adapter = adapter_path.read_text(encoding='utf-8')
    # 谓词必须是本体里声明的对象属性
    assert 'declared_object_property' in adapter, '改判关系前没有校验谓词是对象属性'
    assert 'ontology.resolve(predicate, ontology.relations)' in adapter, \
        '没有确认谓词属于本体关系集'
    # 命中实体不许是字面量兜底类
    assert 'LITERAL_FALLBACK_CLASSES' in adapter and 'DataEntity' in adapter, \
        '没有排除被误建成实体的字面量值'
    assert 'can_be_relation' in adapter, '缺少三条全满足才改判关系的总判据'
    # 保留为属性的分支仍要落进 review_candidates（不丢失、可在审核台看到）
    assert "kind='attribute'" in adapter or "kind': 'attribute'" in adapter, \
        '保留为属性的候选没有进入审核（会丢失）'




# ============================================================================
# 知识台账：实体 / 关系 / 属性 / 原文片段 四个视角（2026-10-05）
# ============================================================================

def test_ledger_covers_attributes_as_a_first_class_view():
    """台账要维护「实体、关系、属性」—— 属性以前在台账里**没有视角**。

    属性是一条独立记录（subject_id + 属性名 + 值 + 数据类型）；后端 current_records 早就收
    'attribute'，是前端查询时把它滤掉了（''.join(['entity','relation','chunk'])）。这条钉住：
    查询带上它、视角/列/单元格都有，且属性行能回到它所属实体（在图谱里定位）。
    """
    js = read('records-view.js')
    assert "attribute:'属性'" in js, '缺少「属性」这个知识类别'
    assert "['entity','relation','attribute','chunk']" in js, '查询没有把属性带上'
    assert 'attribute:[' in js, '缺少属性视角的列定义'
    assert "view==='attribute'" in js, '缺少属性行的渲染分支'
    assert 'subject_id' in js and 'datatype' in js, '属性行没有显示所属实体/数据类型'


def test_ingest_page_states_its_purpose_in_plain_chinese():
    """写入页要「简介明确如何使用」：一句话说清本页做什么、不做什么。

    用户原话：「一定要简介明确如何使用才行」。纯文件上传后，\"现在这一步\"状态条随之移除，
    改为 .ingest-upload-purpose 一句说明：做\"把文件解析成知识\"；不做\"改/删/合并\"（去台账）。
    钉住：① ingest-upload.js 里有这句中文说明；② 样式里有对应声明；③ 明确指向「知识台账」。
    """
    js, css = read('ingest-upload.js'), read('ingest-upload.css')
    assert 'ingest-upload-purpose' in js, '缺少一句话使用说明'
    assert '.ingest-upload-purpose' in css, '样式里缺少 .ingest-upload-purpose'
    assert '把文件解析成知识' in js and '知识台账' in js, \
        '没有说清本页做什么、不做什么（修改去台账）'


# ── 登录 / 角色 / 用户管理（这一轮补的认证面）────────────────────────────────

def test_auth_assets_are_loaded_and_versioned():
    """auth.js / user-admin.js / auth.css 必须都在 index.html 里带 ?v= 引入。

    漏了 user-admin.js 的连带表现很隐蔽：登录能用、菜单也在，只有点
    「用户与权限 / 改口令」时静默没反应（auth.js 里降级成一句提示）。
    """
    index = read('index.html')
    for asset in ('auth.js', 'user-admin.js', 'auth.css'):
        assert re.search(r'%s\?v=[^"\']+' % re.escape(asset), index), f'{asset} 没有带版本号引入'


def test_admin_only_show_rule_must_beat_the_hide_rule():
    """`.admin-only` 的"显示"那条必须也带 !important。

    隐藏那条是 `display:none !important`；不带 !important 的显示规则**永远赢不了它**，
    于是管理员登录后也看不到任何写入口 —— 界面上表现为"功能没做"，
    只有真浏览器量可见性才发现（本轮实测踩到并修掉）。
    """
    css = read('auth.css')
    assert '.admin-only { display: none !important; }' in css, '缺少 .admin-only 的隐藏规则'
    assert 'body.is-admin .admin-only { display: revert !important; }' in css, \
        '管理员的显示规则没有 !important —— 会被隐藏规则压住，写入口对管理员也不可见'


def test_session_buttons_do_not_reuse_login_form_ids():
    """会话区的按钮 id 不能和登录浮层输入框重名。

    重名时 `getElementById('auth-password')` 取到的是按钮（DOM 顺序在前），
    登录时读 `.value` 得到 undefined → 登录直接崩；只读提示也拿不到输入框。
    本节用契约钉住：登录用 id 只允许出现在浮层模板里，会话区必须用另一套名字。
    """
    js = read('auth.js')
    css = read('auth.css')
    assert 'id="auth-open-password"' in js, '会话区按钮没有用 auth-open-* 这套独立 id'
    # 「用户与权限」已从会话区搬到侧栏「系统层 · 管理」（页面式 tab），
    # 入口由 menu-hierarchy.js 生成并带 admin-only —— 这里钉住它的新位置，
    # 免得有人又把它塞回会话区（那里只该有改口令 / 退出登录）。
    hierarchy = read('menu-hierarchy.js')
    assert "'用户与权限'" in hierarchy and 'admin-only' in hierarchy, \
        '「用户与权限」入口必须在侧栏系统层里且对只读用户隐藏'
    assert 'id="auth-open-users"' not in js, '会话区不该再出现用户管理入口'
    assert "'auth-password'" not in js.split('function renderSession')[1].split('function notice')[0], \
        '会话区又用回了与登录输入框重名的 id'
    assert '.session-box .session-who' in css, '会话区按钮缺样式'
    assert 'body.is-viewer .session-box .session-hint' in css, \
        '只读账号的说明没有"仅对只读用户显示"的样式（管理员会看到多余提示）'
    assert '写入入口已隐藏' in read('auth.js'), '只读账号没有一句"我不能做什么"'


def test_readonly_sidebar_hides_groups_that_become_empty():
    """只读用户的侧栏不能留下"有标题、里面空的"分组。

    `.admin-only` 只藏按钮，不藏分组标题 → 只读账号会看到「本体层 / 实例层 / 运行层」
    三块空壳，像是坏了。规则：确知是只读用户（body.is-viewer）时把整组成员都是
    写入口的分组收起；登录态未知时不动菜单（那时浮层盖着，改了反而闪）。
    """
    js = read('menu-hierarchy.js')
    assert "classList.contains('is-viewer')" in js, '缺少"确知只读用户"的判断'
    assert "every(button=>button.classList.contains('admin-only'))" in js, \
        '没有按"组里全是写入口"来收组'
    assert "attributeFilter:['class']" in js, '没有监听 body 角色变化 —— 登录后不会重算'
    assert '.nav-group[hidden]{display:none}' in read('menu-hierarchy.css'), \
        'CSS 没有显式收起规则（只靠 UA 的 [hidden] 会被将来的 display 声明压住）'


def test_user_admin_says_what_it_manages_and_what_it_does_not():
    """「用户与权限」必须一句话说清管什么、不管什么，以及动作的后果。

    用户口径：「新功能不能只给一个开关或数字，必须一句话说明它做什么、不做什么」。
    """
    js = read('user-admin.js')
    assert '不管项目里的知识与本体' in js, '没说清这一页不做什么'
    assert '立刻失效' in js, '没说清改口令/停用会让已有登录失效'
    assert '不能改自己' in js, '没说清为什么自己那一行不给改角色'
    # 三档角色各能做什么（只读用户连按钮都看不到）：
    assert '管理所有账号' in js, '没写清超级管理员能管理一切账号'
    assert '管理普通账号' in js, '没写清管理员能管理普通账号'
    assert '不能管理超级管理员' in js, '没写清管理员的越权边界'
    assert '只能检索、问答、看脑图' in js, '没写清只读用户能做什么'

def test_auth_401_only_means_session_lost_when_a_token_was_sent():
    """未登录首屏的杂散 401 不能被当成"登录状态已失效"。

    真机踩过：workspace.js / projects-view.js 在未登录时也发 /api/projects → 401，
    登录框于是红字写着"登录状态已失效，请重新登录。"（用户根本没登录过），
    而且顺手把会话标记成"挂起"，登录后还会多一次整页重载。
    """
    js = read('auth.js')
    assert 'response.status === 401 && isApi && getToken()' in js, \
        '401 处理没有先判断"本来有没有带令牌"'


def test_project_loaders_wait_for_auth_when_the_server_enforces_it():
    """拉项目列表必须先等认证就绪（只在服务端确知启用鉴权时才等）。

    两条一起钉：① 调用点不能裸调 projects()/renderProjects()；
    ② whenReady 只在 auth_enforced 为真时等 —— 否则测试桩（没这个字段）里
    整页会永远拉不到数据（没人登录）。
    """
    for name, call in (('workspace.js', 'projects()'), ('projects-view.js', 'renderProjects')):
        js = read(name)
        assert 'whenReady' in js, f'{name} 没有等认证就绪'
    js = read('auth.js')
    assert 'authEnforced !== true' in js, 'whenReady 没有区分"服务端是否启用鉴权"'
    assert "loadAuthPolicy();" in js, 'whenReady 没有自己触发策略探针（会早于 bootstrap 被调用）'
    assert 'function releaseWithoutAuth()' in js, '缺少"服务端没启用鉴权就直接放行"的分支'

