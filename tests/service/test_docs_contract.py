"""Current workflow manual delivery.

These checks read docs and route source; business and capacity acceptance is separate.
"""
from pathlib import Path
import importlib.util
import re

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / 'README.md'
HTML_MANUAL = ROOT / 'docs/知识平台完整操作手册.html'
CURRENT_MANUAL = ROOT / 'docs/知识平台操作手册.md'


def test_readme_local_links_point_to_existing_files():
    text = README.read_text(encoding='utf-8')
    targets = re.findall(r'\]\((?!https?://|mailto:|#)([^)\s]+)\)', text)
    assert targets
    missing = [target for target in targets if not (ROOT / target.split('#', 1)[0]).is_file()]
    assert not missing, f'README 指向了不存在的文件：{missing}'


def test_current_manual_is_the_indexed_source_and_generated_output():
    readme = README.read_text(encoding='utf-8')
    assert 'docs/知识平台完整操作手册.html' in readme
    for obsolete in ('2026-10-19-知识平台完整操作手册.md', '2026-10-01-本体工作台操作手册.md',
                     '2026-10-07-用户手册（登录·账号·权限）.md', '知识图谱操作手册.html'):
        assert obsolete not in readme, f'活动索引仍指向已替代的手册：{obsolete}'
    assert 'docs/知识平台操作手册.md' in readme
    alias = ROOT / 'docs/知识平台操作手册.html'
    assert HTML_MANUAL.read_bytes() == alias.read_bytes(), '两个当前入口必须保持相同内容'


def test_manual_builder_renders_the_current_canonical_manual():
    path = ROOT / 'scripts/build_knowledge_service_manual.py'
    spec = importlib.util.spec_from_file_location('manual_build_entrypoint', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.OUTPUT == HTML_MANUAL
    assert module.MARKDOWN == CURRENT_MANUAL
    assert module.render_manual() == HTML_MANUAL.read_text(encoding='utf-8')


def test_current_manual_embeds_all_source_figures_without_changing_their_bytes():
    import base64
    from html.parser import HTMLParser

    class Figures(HTMLParser):
        def __init__(self):
            super().__init__()
            self.images = []

        def handle_starttag(self, tag, attrs):
            data = dict(attrs)
            if tag == 'img' and data.get('src'):
                self.images.append((data['src'], data.get('alt', '')))

    expected = re.findall(r'^!\[([^\]]+)\]\(([^)]+)\)$',
                          CURRENT_MANUAL.read_text(encoding='utf-8'), re.M)
    inventory = Figures()
    inventory.feed(HTML_MANUAL.read_text(encoding='utf-8'))
    assert expected and len(inventory.images) == len(expected)
    for (alt, path), (embedded, rendered_alt) in zip(expected, inventory.images):
        assert rendered_alt == alt
        assert embedded.startswith('data:image/') and ';base64,' in embedded
        payload = base64.b64decode(embedded.split(',', 1)[1], validate=True)
        assert payload == (CURRENT_MANUAL.parent / path).read_bytes(), path
