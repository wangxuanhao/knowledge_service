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
    for tab in ('dashboard','graph','sources','mindmap','jobs','evaluation','records','ontology','search','ingest'):
        assert f'tab-{tab}' in ids
    workspace=(ROOT/'workspace.js').read_text(encoding='utf-8')
    assert "page.id='tab-discovery'" in workspace
    assert "mode.id='extraction-mode'" in workspace
    assert '发布本体版本' in workspace
    details=(ROOT/'ontology-details.js').read_text(encoding='utf-8')
    assert 'ontology-details.js' in html
    assert 'OntologyDetails.renderTechnicalDetails' in workspace
    assert all(label in details for label in ('查看版本变化与技术详情','版本差异','本体定义','名称与技术标识'))
    assert all(help_text in details for help_text in ('新增、保留或删除','Turtle 标准格式','不是乱码'))
    assert '受控重解析' in workspace
    assert 'candidate_status_counts' in workspace
    assert all(label in workspace for label in ('待纳入','草案中','已批准','已物化'))
    assert '发布本体并映射候选' not in workspace
    candidate_map=(ROOT/'candidate-mindmap.js').read_text(encoding='utf-8')
    menu=(ROOT/'menu-hierarchy.js').read_text(encoding='utf-8')
    assert '/ontology-discovery/candidate-mindmap' in candidate_map
    assert '非正式知识' in candidate_map and '该聚合键不是正式实体 ID' in candidate_map
    assert all(group in menu for group in ('项目','探索与展示','建模与治理','运行与质量'))
    assert all(asset in html for asset in ('candidate-mindmap.js','menu-hierarchy.js','candidate-mindmap.css','menu-hierarchy.css'))
    assert '/assets/workbench.css' in html and '/assets/workbench.js' in html


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
