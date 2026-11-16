"""Standalone manual rendering regressions, independent of application storage."""
import runpy
import base64
import tempfile
import unittest
from pathlib import Path


class ManualTableTests(unittest.TestCase):
    def setUp(self):
        self.builder = runpy.run_path(str(Path(__file__).resolve().parents[2] /
                                        'scripts' / 'build_standalone_manual.py'))

    def test_escaped_pipe_stays_inside_table_cell_and_inline_command(self):
        builder = runpy.run_path(str(Path(__file__).resolve().parents[2] /
                                    'scripts' / 'build_standalone_manual.py'))
        rendered, _ = builder['render'](builder['tokenize']([
            '| 状态 | 命令 |', '| --- | --- |',
            r'| 查端口 | `netstat -ano \| findstr 8100` |']))
        self.assertEqual(rendered.count('<td>'), 2)
        self.assertIn('<code>netstat -ano | findstr 8100</code>', rendered)

    def test_offline_images_are_embedded_and_source_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            picture = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
            (root / 'figure.svg').write_bytes(picture)
            source = '# 手册\n\n说明\n\n## 流程\n\n![步骤](figure.svg)\n'
            markdown = root / 'manual.md'
            markdown.write_text(source, encoding='utf-8')
            output = self.builder['build'](markdown, embed_images=True).read_text(encoding='utf-8')
            self.assertIn('src="data:image/svg+xml;base64,' + base64.b64encode(picture).decode(), output)
            self.assertNotIn('src="figure.svg"', output)
            self.assertIn('aria-label="放大附图：步骤"', output)
            self.assertEqual(markdown.read_text(encoding='utf-8'), source)

    def test_inline_code_examples_are_not_interpreted_as_document_links(self):
        with tempfile.TemporaryDirectory() as folder:
            markdown=Path(folder)/'manual.md'
            markdown.write_text('# ZIP 手册\n\n## 引用\n\n写作 `![示例](assets/a.png)`，代码 `**原样**`；普通 **强调** 保留。**包含 `键名` 的强调**。\n',encoding='utf-8')
            output=self.builder['build'](markdown,embed_images=True).read_text(encoding='utf-8')
            self.assertIn('<code>![示例](assets/a.png)</code>',output)
            self.assertIn('<code>**原样**</code>',output)
            self.assertIn('<strong>强调</strong>',output)
            self.assertIn('<strong>包含 <code>键名</code> 的强调</strong>',output)
            self.assertNotIn('href="assets/a.png"',output)

    def test_html_code_examples_are_not_treated_as_embedded_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            markdown=Path(folder)/'manual.md'
            markdown.write_text('# 附件手册\n\n## 示例\n\n`<img src="assets/missing.png">`\n\n```html\n<video src="assets/missing.mp4"></video>\n```\n',encoding='utf-8')
            output=self.builder['build'](markdown,embed_images=True).read_text(encoding='utf-8')
            self.assertIn('&lt;img src="assets/missing.png"&gt;',output)
            self.assertIn('&lt;video src=&quot;assets/missing.mp4&quot;&gt;',output)

    def test_missing_image_prevents_overwriting_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            markdown = Path(folder) / 'manual.md'
            markdown.write_text('# 手册\n\n## 流程\n\n![缺图](missing.png)\n', encoding='utf-8')
            output = markdown.with_suffix('.html')
            output.write_text('last good build', encoding='utf-8')
            with self.assertRaises(SystemExit):
                self.builder['build'](markdown, embed_images=True)
            self.assertEqual(output.read_text(encoding='utf-8'), 'last good build')

    def test_embedded_mode_rejects_remote_images(self):
        with tempfile.TemporaryDirectory() as folder:
            markdown = Path(folder) / 'manual.md'
            markdown.write_text('# 手册\n\n## 流程\n\n![联网图](https://example.org/pic.png)\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Offline manuals require local images'):
                self.builder['build'](markdown, embed_images=True)


if __name__ == '__main__':
    unittest.main()
