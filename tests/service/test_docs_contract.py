"""文档契约：README 里指向的文档必须真实存在，且操作手册必须在索引里。

防止的情况和前端静态契约一样：改了文档/挪了文件，索引还指向旧路径，
读者点进去 404 —— 这种"看起来没问题"的坏链最费时间。
"""
from pathlib import Path
from html.parser import HTMLParser

import base64
import importlib.util
import re
import struct

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / 'README.md'
MANUAL = ROOT / 'docs' / '2026-10-01-本体工作台操作手册.md'
HTML_MANUAL = ROOT / 'docs' / '知识图谱操作手册.html'
BUILDER = ROOT / 'scripts' / 'build_knowledge_service_manual.py'

SHOT_IDS = (
    'S01a', 'S01b', 'S01c',
    'S02a', 'S02b', 'S02c',
    'S03a', 'S03b', 'S03c', 'S03d', 'S03e',
    'S04a', 'S04b', 'S04d', 'S04c',
    'S05a', 'S05b', 'S05c', 'S05d',
    'S06a', 'S06b',
    'S07a', 'S07b',
    'S08a', 'S08b', 'S08c', 'S08d',
    'S09a', 'S09b', 'S09c',
    'S10a', 'S10b', 'S10c',
)


def test_manual_covers_resumable_batches_and_fixed_sources():
    for path in (MANUAL, HTML_MANUAL):
        text = path.read_text(encoding='utf-8')
        for marker in ('申请并发', '实际并发', '从断点继续', '只重试失败切片',
                       '整份重新处理', 'Markdown', '图片 OCR', '低置信度',
                       '忽略并继续', '阅读视图', '解析视图', '原始视图', '在原文中打开'):
            assert marker in text, (path.name, marker)


def test_ingest_flow_is_ordered_inside_knowledge_ingest_chapter():
    html_manual = HTML_MANUAL.read_text(encoding='utf-8')
    html_ingest = html_manual.split('<section id="chapter-4"', 1)[1].split('<section id="chapter-5"', 1)[0]
    markdown_manual = MANUAL.read_text(encoding='utf-8')
    markdown_ingest = markdown_manual.split('## 当前界面入口：知识写入', 1)[1].split('## 问答中的', 1)[0]
    for section in (html_ingest, markdown_ingest):
        markers = ('选模式', '选文件', '预览切片', '上传并处理', '查看批次进度',
                   '失败恢复', '打开结果工作区', '核对固定版本原文')
        positions = [section.index(marker) for marker in markers]
        assert positions == sorted(positions), '知识写入应按准备、提交、恢复、结果和来源核对的顺序阅读'
        for marker in ('申请并发', '实际并发', '只重试失败切片', '低置信度', '图片区域框'):
            assert marker in section
    assert '<section id="chapter-13"' not in html_manual
    assert '\n# 批量知识写入与断点续跑' not in markdown_manual
    toc = re.search(r'<nav class="toc">(.*?)</nav>', html_manual, re.S).group(1)
    expected_links = [f'#chapter-{i}' for i in range(1, 6)]
    expected_links += ['#graph-canvas-workflow', '#graph-path-workflow']
    expected_links += [f'#chapter-{i}' for i in range(6, 13)] + ['#troubleshooting']
    assert re.findall(r'href="([^"]+)"', toc) == expected_links
    for anchor in expected_links:
        assert f'id="{anchor[1:]}"' in html_manual
    assert [int(n) for n in re.findall(r'<header class="chapter-head"><span>(\d+)</span>', html_manual)] == list(range(1, 14))


def _jpeg_size(payload: bytes) -> tuple[int, int]:
    """Read dimensions from a JPEG SOF marker without adding an image dependency."""
    offset = 2
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while offset + 4 <= len(payload):
        if payload[offset] != 0xFF:
            offset += 1
            continue
        marker = payload[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        segment_length = struct.unpack('>H', payload[offset:offset + 2])[0]
        if marker in sof_markers:
            height, width = struct.unpack('>HH', payload[offset + 3:offset + 7])
            return width, height
        offset += segment_length
    raise AssertionError('JPEG 中没有找到尺寸标记')


class _AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.external = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {'img', 'script'} and values.get('src', '').startswith(('http://', 'https://')):
            self.external.append((tag, values['src']))
        if tag == 'link' and values.get('href', '').startswith(('http://', 'https://')):
            self.external.append((tag, values['href']))


def test_readme_links_point_to_existing_files():
    text = README.read_text(encoding='utf-8')
    # 只校验仓库内的相对链接，外链（http/https、mailto、锚点）交给读者。
    targets = re.findall(r'\]\((?!https?://|mailto:|#)([^)\s]+)\)', text)
    assert targets, 'README 里一个仓库内链接都没有，检查是不是链接格式变了'
    missing = [target for target in targets if not (ROOT / target).exists()]
    assert not missing, f'README 指向了不存在的文件：{missing}'


def test_workbench_manual_is_indexed_and_keeps_key_sections():
    """操作手册必须被 README 索引，并保留用户真正会查的那几节。"""
    readme = README.read_text(encoding='utf-8')
    assert 'docs/2026-10-01-本体工作台操作手册.md' in readme

    manual = MANUAL.read_text(encoding='utf-8')
    # A2-c：审核台不再是"四站流水线"，章节名随之改（设计归编辑台、发布是收下的结果）。
    for section in ('## 0. 三个页面各做什么、不做什么', '## 1. 审核台的两个视图：收件箱 + 逐项收下',
                    '## 2. 改结构：去「本体编辑台」', '## 3. 逐项收下（审核台的主场）',
                    '## 4. 校验与发布（发布是"收下"的结果）',
                    '## 5. 本体编辑台', '## 6. 本体档案',
                    '## 7. 常见问题', '## 8. 自测与验收命令', '## 9. 本轮修复对照'):
        assert section in manual, f'操作手册缺少章节：{section}'
    # 三页各自的"不做什么"必须写在手册里——用户反复追问的就是边界。
    for page in ('本体审核台', '本体编辑台', '本体档案'):
        assert page in manual, f'操作手册没有提到：{page}'
    # "提交并自审"跳过了他人复核，这个边界必须写明，不能只当一个按钮。
    assert '提交并自审' in manual and '跳过他人复核' in manual
    # 一键批准的边界必须写清楚"覆盖什么、不覆盖什么"——这是用户反复追问的点。
    assert '覆盖' in manual and '不覆盖' in manual


def test_v110_html_manual_explains_the_real_end_to_end_lifecycle():
    manual = HTML_MANUAL.read_text(encoding='utf-8')
    for phrase in (
            '本体建模层', '属性不会单独画成节点', '实体脑图当前不展示属性分支',
            '知识台账可以切换到“属性”视角', '发布本体不会自动修改已有知识',
            '服务 v1.1.0', '本体 v1', '本体 v2', '本体 v3', '记录修订 rN',
            '受控重分类不会自动把“员工”改成“产品经理”',
            '前三条依据默认展开', '第一份冻结原文默认可见',
            'Milvus collection_ready', 'Semantica 质量检查',
            '本体切片收据'):
        assert phrase in manual, f'HTML 手册缺少关键说明：{phrase}'
    assert '本体建设台' not in manual

    parser = _AssetParser()
    parser.feed(manual)
    assert not parser.external, f'HTML 手册包含外部资源依赖：{parser.external}'
    styles = ''.join(re.findall(r'<style[^>]*>(.*?)</style>', manual, re.S | re.I))
    external_css = re.findall(r'url\(\s*["\']?https?://', styles, re.I)
    assert not external_css, 'HTML 手册的 CSS 不得引用外部资源'


def test_manual_explains_ingest_modes_next_steps_and_owl_boundary():
    """知识写入手册必须让操作人能选对模式、跟着六步做完，并按结果去对的页面。"""
    html_manual = HTML_MANUAL.read_text(encoding='utf-8')
    markdown_manual = MANUAL.read_text(encoding='utf-8')

    required_phrases = (
            '使用项目本体 · 正式入图', '开放本体发现 · 候选暂存',
            '仅文档检索 · 不抽取图谱', '是否入图', '结果去向',
            '选模式', '选文件', '预览切片', '上传并处理', '打开结果工作区', '按结果动作继续',
            '已入图', '待审核', '待建模', '仅文档', '失败',
            '图谱关系路径不是 OWL 推理', '本阶段暂不实施 OWL 推理',
            '仅文档模式没有实体', '抽取结果为空', '待确认去哪里处理',
            '发现结果全部作为待建模候选暂存，不直接进入正式图谱', 'llm_model')
    for label, manual in (('HTML', html_manual), ('Markdown', markdown_manual)):
        for phrase in required_phrases:
            assert phrase in manual, f'{label} 手册缺少知识写入说明：{phrase}'
        assert 'model_agent' not in manual
        assert '已入图去知识台账；待建模去本体画布' not in manual

    # HTML 是面向操作人的完整离线手册，六步和分支必须在这份成品中直接可见。
    for phrase in (
            '选模式', '选文件', '预览切片', '上传并处理', '打开结果工作区', '按结果动作继续',
            '已入图', '待审核', '待建模', '仅文档', '失败'):
        assert phrase in html_manual, f'HTML 手册缺少可操作流程：{phrase}'

    for phrase in (
            '使用项目本体 · 正式入图', '开放本体发现 · 候选暂存',
            '仅文档检索 · 不抽取图谱', '打开结果工作区',
            '本体建模层仍然可直接编辑', '图谱关系路径不是 OWL 推理'):
        assert phrase in markdown_manual, f'Markdown 手册缺少当前操作说明：{phrase}'



def test_generated_html_manual_matches_builder_output():
    spec = importlib.util.spec_from_file_location('manual_builder_consistency', BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert HTML_MANUAL.read_text(encoding='utf-8') == module.render_manual()


def test_v110_manual_has_every_verified_screenshot_once():
    manual = HTML_MANUAL.read_text(encoding='utf-8')
    figures = re.findall(
        r'<figure\b[^>]*data-shot-id="([^"]+)"[^>]*>(.*?)</figure>', manual, re.S | re.I)
    found = [shot_id for shot_id, _ in figures]
    assert found == list(SHOT_IDS), f'截图顺序或数量不对：{found}'

    for shot_id, body in figures:
        images = re.findall(
            r'<img\b[^>]*src="data:image/(png|jpeg);base64,([^"]+)"[^>]*alt="([^"]+)"[^>]*>',
            body, re.S | re.I)
        assert len(images) == 1, f'{shot_id} 必须且只能有一张内联截图'
        mime, payload, alt = images[0]
        assert alt.strip(), f'{shot_id} 缺少 alt'
        decoded = base64.b64decode(payload)
        if shot_id == 'S04d':
            assert mime == 'jpeg' and decoded.startswith(b'\xff\xd8\xff'), 'S04d 应保留原始 JPEG 资源'
            width, height = _jpeg_size(decoded)
            assert (width, height) == (789, 884), f'S04d 尺寸为 {width}x{height}'
        else:
            assert mime == 'png' and decoded.startswith(b'\x89PNG\r\n\x1a\n'), f'{shot_id} 不是 PNG'
            width, height = struct.unpack('>II', decoded[16:24])
            assert (width, height) == (1440, 900), f'{shot_id} 尺寸为 {width}x{height}'
        assert 'class="completion-sign"' in body, f'{shot_id} 缺少完成标志'
        markers = re.findall(
            r'<span\b[^>]*class="shot-marker"[^>]*data-n="(\d+)"[^>]*'
            r'data-x="([\d.]+)"[^>]*data-y="([\d.]+)"[^>]*'
            r'data-w="([\d.]+)"[^>]*data-h="([\d.]+)"[^>]*>', body)
        assert 1 <= len(markers) <= 3, f'{shot_id} 应有 1～3 个编号框'
        assert [int(marker[0]) for marker in markers] == list(range(1, len(markers) + 1))
        for _, x, y, width, height in markers:
            x, y, width, height = map(float, (x, y, width, height))
            assert min(x, y, width, height) >= 0
            assert x + width <= 100 and y + height <= 100


def test_fixture_screenshots_are_distinct_from_verified_service_case():
    manual = HTML_MANUAL.read_text(encoding='utf-8')
    figures = re.findall(
        r'<figure\b[^>]*data-fixture-shot-id="([^"]+)"[^>]*>(.*?)</figure>',
        manual, re.S)
    assert [key for key, _ in figures] == ['P15a', 'P15b']
    for _, body in figures:
        payload = re.search(r'data:image/png;base64,([^"]+)', body)[1]
        assert struct.unpack('>II', base64.b64decode(payload)[16:24]) == (1500, 1000)
        assert '隔离' in body and 'completion-sign' in body
    assert 'data-shot-id="P15' not in manual


def test_v110_manual_chapters_and_troubleshooting_are_structured():
    manual = HTML_MANUAL.read_text(encoding='utf-8')
    chapters = re.findall(
        r'<section\b[^>]*class="[^"]*core-chapter[^"]*"[^>]*>(.*?)</section>',
        manual, re.S | re.I)
    assert len(chapters) >= 8
    for chapter in chapters:
        assert 'class="completion-sign"' in chapter
        assert 'class="common-misunderstanding"' in chapter

    items = re.findall(
        r'<article\b[^>]*class="troubleshooting-item"[^>]*>(.*?)</article>', manual, re.S | re.I)
    assert len(items) >= 8
    for item in items:
        for field in ('看到什么', '原因', '下一步'):
            assert f'data-field="{field}"' in item


def test_manual_builder_reports_a_missing_screenshot_from_a_temporary_manifest(tmp_path):
    spec = importlib.util.spec_from_file_location('manual_builder', BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = {key: dict(value) for key, value in module.SHOTS.items()}
    manifest['S03b']['path'] = tmp_path / 'does-not-exist.png'
    try:
        module.render_manual(shots=manifest)
    except FileNotFoundError as exc:
        message = str(exc)
        assert 'S03b' in message
        assert 'does-not-exist.png' in message
    else:
        raise AssertionError('构建器缺图时必须失败')
