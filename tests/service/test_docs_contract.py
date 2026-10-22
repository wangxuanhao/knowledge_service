"""Contracts for the current illustrated offline operation manual.

These checks read docs and route source; business and capacity acceptance is separate.
"""
from pathlib import Path
import importlib.util
import re
import struct

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / 'README.md'
MANUAL = ROOT / 'docs/2026-10-20-知识平台完整操作手册.md'
HTML_MANUAL = ROOT / 'docs/知识平台完整操作手册.html'
BUILDER = ROOT / 'scripts/verify_redesign_manual.py'


def _builder():
    spec = importlib.util.spec_from_file_location('current_manual_contract', BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _chapter(number):
    _, chapters = _builder().split_chapters(MANUAL.read_text(encoding='utf-8'))
    return next(body for found, _, body in chapters if found == number)


def test_readme_local_links_point_to_existing_files():
    text = README.read_text(encoding='utf-8')
    targets = re.findall(r'\]\((?!https?://|mailto:|#)([^)\s]+)\)', text)
    assert targets
    missing = [target for target in targets if not (ROOT / target.split('#', 1)[0]).is_file()]
    assert not missing, f'README 指向了不存在的文件：{missing}'


def test_current_manual_is_the_indexed_source_and_generated_output():
    readme = README.read_text(encoding='utf-8')
    assert 'docs/2026-10-20-知识平台完整操作手册.md' in readme
    assert 'docs/知识平台完整操作手册.html' in readme
    for obsolete in ('2026-10-19-知识平台完整操作手册.md', '2026-10-01-本体工作台操作手册.md',
                     '2026-10-07-用户手册（登录·账号·权限）.md', '知识图谱操作手册.html'):
        assert obsolete not in readme, f'活动索引仍指向已替代的手册：{obsolete}'
    module = _builder()
    source = MANUAL.read_text(encoding='utf-8')
    html = HTML_MANUAL.read_text(encoding='utf-8')
    assert module.MARKDOWN == MANUAL and module.OUTPUT == HTML_MANUAL
    assert html == module.build_html(source), 'Markdown 已改变，须重建离线 HTML'
    assert not module.verify(source, html)


def test_all_29_chapters_explain_actions_results_and_recovery():
    module = _builder()
    _, chapters = module.split_chapters(MANUAL.read_text(encoding='utf-8'))
    assert [number for number, _, _ in chapters] == list(range(1, 30))
    assert set(module.COVERAGE) == set(range(1, 30))
    for number, title, body in chapters:
        for field in module.FIELDS:
            assert len(re.findall(r'^### ' + re.escape(field) + '$', body, re.M)) == 1, (number, field)
        for topic in module.COVERAGE[number]:
            assert topic in title + body, (number, topic)


def test_onboarding_and_three_intake_paths_keep_the_governance_boundaries():
    intro = _chapter(4)
    for marker in ('具体知识', '结构', 'documents', 'ontology', 'discovery', '预览首份切片'):
        assert marker in intro
    controlled, discovery, manual = (_chapter(number) for number in (5, 6, 7))
    assert '不应因普通上传增加' in controlled
    assert '草案中启用的术语仍未发布' in controlled
    for marker in ('正式 IRI', '不创建空草案', '只读预演', '未加入本轮修改', '多批候选',
                   '最终获批结构', '未选候选', '来源撤回', '同一请求身份'):
        assert marker in discovery, marker
    for marker in ('新增定义', '空基线', '多份', '第一次保存', '关联知识 0 条',
                   '历史积压候选', '已保存到草案', '不必等待完整发布校验'):
        assert marker in manual, marker
    assert discovery.index('点击预览') < discovery.index('确认“加入本轮修改”')
    assert '确认写入所选知识' in discovery
    assert '预演不会自动创建草案' in MANUAL.read_text(encoding='utf-8')


def test_resumable_batches_fixed_sources_and_evidence_remain_operational():
    batches, sources = _chapter(8), _chapter(9)
    for marker in ('ZIP', '申请并发', '实际并发', '从断点继续', '只重试失败切片',
                   '整份重新处理', '忽略并继续', '低置信度'):
        assert marker in batches, marker
    for marker in ('阅读视图', '解析视图', '原始视图', 'OCR', '固定版本',
                   'source_id', '断言', '修订记录'):
        assert marker in sources, marker


def test_release_receipts_distinguish_structure_facts_and_index_projection():
    body = _chapter(13)
    for marker in ('发布 ID', '逐来源', '新增', '复用', '跳过', '索引', '0 条',
                   '操作指纹', '重新编辑', '只读历史', '来源'):
        assert marker in body, marker
    assert '静态手册检查不执行这些业务故事' in _chapter(29)
    appendix = _chapter(29)
    for marker in ('selection-preview', 'add-selection', 'confirm-reuse', 'source_fingerprint',
                   'preview_fingerprint', 'idempotency_key', 'facts.selected/pending',
                   'not_requested', 'metadata.publication.receipt'):
        assert marker in appendix, marker


def test_supported_features_and_capacity_limits_are_not_removed_or_overclaimed():
    required = {
        12: ('SHACL', 'RDF', 'Turtle', 'SPARQL'),
        14: ('预演', '迁移', '撤销', 'source_ontology_id'),
        15: ('独立属性', '软删除', '恢复', '预期修订'),
        16: ('别名', '查重', 'attribute_winners', '消歧', '撤销'),
        19: ('Local/OAG', '冻结原文', '最终依据校验'),
        20: ('脑图', '路径', '截断'),
        21: ('渐进', 'WebGL', '不完整'),
        23: ('basic', 'document_only', 'no_graph', 'no_ontology', 'combined', '评分题数'),
        24: ('Milvus', 'Neo4j', 'ontology-index'),
        25: ('快照', '恢复前自动备份'),
        27: ('150,000', '30 s', '未通过', 'P95', '独立测量', '单次', '项目成员 ACL'),
    }
    for number, markers in required.items():
        body = _chapter(number)
        for marker in markers:
            assert marker in body, (number, marker)


def test_current_ui_images_are_local_real_capture_files_with_descriptions():
    module = _builder()
    inventory = module.HtmlInventory()
    inventory.feed(HTML_MANUAL.read_text(encoding='utf-8'))
    assert len(inventory.assets) >= 10 and not inventory.remote
    previous_captures = {
        '2026-10-19-people.png', '2026-10-20-projects.png', '2026-10-20-upload-drawer.png',
        '2026-10-20-sources.png', '2026-10-19-records.png', '2026-10-20-create-entity.png',
        '2026-10-20-search.png', '2026-10-20-graph-settings.png', '2026-10-20-progressive-graph.png',
        '2026-10-19-evaluation.png', '2026-10-19-runtime.png',
    }
    assert previous_captures <= {Path(source).name for source, _ in inventory.assets}
    for source, alt in inventory.assets:
        path = HTML_MANUAL.parent / source
        assert source.startswith('manual-redesign/') and path.is_file(), source
        assert alt.strip() and len(alt) >= 10, source
        payload = path.read_bytes()
        dimensions = module.image_dimensions(payload)
        assert dimensions is not None, source
        width, height = dimensions
        assert width >= 700 and height >= 450, (source, width, height)
    assert '交互稿不作为生产截图' in MANUAL.read_text(encoding='utf-8')


def test_image_dimensions_read_png_and_progressive_jpeg_capture_headers():
    module = _builder()
    assert callable(getattr(module, 'image_dimensions', None)), 'Screenshot header reader is missing'
    png_header = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + struct.pack('>II', 1440, 1000)
    jpeg_header = (b'\xff\xd8\xff\xe0\x00\x04AB\xff\xc2\x00\x11\x08'
                   + struct.pack('>HH', 1000, 1440) + b'\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00')
    assert module.image_dimensions(png_header) == (1440, 1000)
    assert module.image_dimensions(jpeg_header) == (1440, 1000)
    for invalid in (b'', b'not an image', b'\xff\xd8\xff\xe0\x00', jpeg_header[:-3]):
        assert module.image_dimensions(invalid) is None


def test_static_validator_detects_missing_images_and_broken_anchors():
    module = _builder()
    source = MANUAL.read_text(encoding='utf-8')
    with_missing_image = source + '\n![不存在的真实页面截图](manual-redesign/missing-current-ui.png)\n'
    errors = module.verify(with_missing_image, module.build_html(with_missing_image))
    assert any('Missing screenshot' in error for error in errors)
    with_bad_anchor = source + '\n[坏锚点](#chapter-99)\n'
    errors = module.verify(with_bad_anchor, module.build_html(with_bad_anchor))
    assert 'Broken anchor: #chapter-99' in errors


def test_static_validator_detects_lost_illustrated_chapter_coverage():
    module = _builder()
    source = MANUAL.read_text(encoding='utf-8')
    first = source.index('<a id="chapter-06"></a>')
    following = source.index('<a id="chapter-07"></a>')
    stripped = re.sub(r'^!\[[^\]]+\]\([^)]+\)\n', '', source[first:following], flags=re.M)
    damaged = source[:first] + stripped + source[following:]
    errors = module.verify(damaged, module.build_html(damaged))
    assert 'Chapter 06: missing current UI screenshot coverage' in errors


def test_manual_builder_renders_the_current_canonical_manual():
    path = ROOT / 'scripts/build_knowledge_service_manual.py'
    spec = importlib.util.spec_from_file_location('manual_build_entrypoint', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.OUTPUT == HTML_MANUAL
    assert module.render_manual() == HTML_MANUAL.read_text(encoding='utf-8')
