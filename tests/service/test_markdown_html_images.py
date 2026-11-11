"""Regression coverage for platform rules containing HTML tables and images."""
import io
import re
import unittest
import zipfile

import pytest

from knowledge_service.integrations.markdown_package import MarkdownPackageReader
from knowledge_service.integrations.markdown_parser import parse_markdown


class MarkdownHtmlImagesTests(unittest.TestCase):
    def test_html_table_preserves_cells_images_and_source_anchor(self):
        text = '# 规则\n\n<table><tr><td rowspan="2">违规场景</td><td><img src="assets/a.png" alt="案例"></td></tr><tr><td>整改要求</td></tr></table>\n'
        result = parse_markdown(text, 'version-a')
        self.assertEqual([image['path'] for image in result['images']], ['assets/a.png'])
        self.assertIn('<table', result['rendered_html'])
        self.assertIn('rowspan="2"', result['rendered_html'])
        self.assertNotIn('&lt;table', result['rendered_html'])
        image = result['images'][0]
        block = next(block for block in result['blocks'] if block['block_id'] == image['block_id'])
        self.assertIn('违规场景', block['normalized_text'])
        self.assertIn('整改要求', block['normalized_text'])
        self.assertIn(block['block_id'], result['rendered_html'])
        self.assertIn('<table>', text[block['source_start_char']:block['source_end_char']])

    def test_mixed_markdown_and_html_image_placeholders_keep_article_order(self):
        text = '![先](assets/first.png)\n\n<table><tr><td><img src="assets/shared.png" alt="表格"></td></tr></table>\n\n正文 <img src="assets/shared.png" alt="行内">\n\n![后](assets/last.png)\n'
        result = parse_markdown(text)
        self.assertEqual([image['path'] for image in result['images']],
                         ['assets/first.png', 'assets/shared.png', 'assets/shared.png', 'assets/last.png'])
        self.assertEqual(re.findall(r'data-image-index="(\d+)"', result['rendered_html']),
                         ['0', '1', '2', '3'])

    def test_html_is_inert_and_image_urls_use_existing_reference_policy(self):
        text = '<table onclick="evil()"><tr><td><img src="javascript:evil()" onerror="evil()"><a href="javascript:evil()">链接</a></td></tr></table>\n<script>evil()</script>\n'
        result = parse_markdown(text)
        html = result['rendered_html']
        self.assertNotIn('onclick=', html)
        self.assertNotIn('onerror=', html)
        self.assertNotIn('href="javascript:', html)
        self.assertNotIn('<script', html)
        self.assertNotIn('<img', html)
        self.assertEqual(result['images'][0]['kind'], 'unsafe')

    def test_images_inside_code_remain_literal_examples(self):
        result = parse_markdown('```html\n<img src="assets/example.png">\n```\n\n`<img src="assets/inline.png">`\n')
        self.assertEqual(result['images'], [])
        self.assertIn('&lt;img', result['rendered_html'])

    def test_html_code_and_quotes_exclude_dates_and_code_images(self):
        text = ('<div>本规则自2026年1月1日起生效。'
                '<code>本规则自2025年1月1日起生效。<img src="example.png"></code>'
                '<blockquote>本规则自2024年1月1日起生效。</blockquote>'
                '<pre>本规则自2023年1月1日起生效。<img src="other.png"></pre></div>')
        result = parse_markdown(text)
        self.assertEqual(result['images'], [])
        block = result['blocks'][0]
        normalized = block['normalized_text']
        ranges = block['payload']['temporal_excluded_ranges']
        for year in ('2025', '2024', '2023'):
            position = normalized.index(year)
            self.assertTrue(any(start <= position < end for start, end in ranges))
        position = normalized.index('2026')
        self.assertFalse(any(start <= position < end for start, end in ranges))
        self.assertIn('&lt;img', result['rendered_html'])

    def test_html_spans_are_bounded_and_remote_images_are_placeholders(self):
        result = parse_markdown('<table><tr><td colspan="3" rowspan="999999999">'
                                '<strong>要求</strong><img src="https://example.com/a.png">'
                                '</td><td colspan="0">说明</td></tr></table>')
        self.assertIn('colspan="3"', result['rendered_html'])
        self.assertNotIn('rowspan=', result['rendered_html'])
        self.assertNotIn('colspan="0"', result['rendered_html'])
        self.assertNotIn('https://example.com', result['rendered_html'])
        self.assertEqual(result['images'][0]['kind'], 'external')

    def test_html_table_nested_in_markdown_quote_keeps_source_anchor(self):
        text = '> <table><tr><td>本规则自2025年1月1日起生效。</td><td><img src="assets/a.png"></td></tr></table>'
        result = parse_markdown(text)
        block = result['blocks'][0]
        self.assertEqual(block['type'], 'blockquote')
        self.assertIn(block['block_id'], result['rendered_html'])
        self.assertEqual(result['images'][0]['block_id'], block['block_id'])
        position = block['normalized_text'].index('2025')
        self.assertTrue(any(start <= position < end for start, end in block['payload']['temporal_excluded_ranges']))

    def test_html_table_nested_in_markdown_list_keeps_source_anchor(self):
        result = parse_markdown('- <table><tr><td><img src="assets/a.png"></td></tr></table>')
        block = result['blocks'][0]
        self.assertEqual(block['type'], 'list')
        self.assertIn(block['block_id'], result['rendered_html'])
        self.assertEqual(result['images'][0]['block_id'], block['block_id'])

    def test_stripped_void_embed_keeps_following_article_content(self):
        result = parse_markdown('<div><embed src="unsafe"><img src="assets/a.png">正文</div>')
        self.assertEqual([image['path'] for image in result['images']], ['assets/a.png'])
        self.assertIn('正文', result['blocks'][0]['normalized_text'])
        self.assertNotIn('<embed', result['rendered_html'])

    def test_zip_resolves_html_assets_and_keeps_missing_occurrence_in_order(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('平台/规则.md', '<table><tr><td><img src="assets/missing.png"></td><td><img src="assets/存在.png"></td><td><img src="assets/存在.png"></td></tr></table>')
            archive.writestr('平台/assets/存在.png', b'PNG')
        document = MarkdownPackageReader().read(stream.getvalue())['documents'][0]
        self.assertEqual([asset['status'] for asset in document['assets']],
                         ['missing', 'available', 'available'])
        self.assertEqual(document['assets'][1]['data'], b'PNG')
        self.assertEqual(document['assets'][2]['data'], b'PNG')
        self.assertEqual(document['warnings'][0]['path'], '平台/assets/missing.png')


@pytest.mark.parametrize('packaged', [False, True])
def test_html_images_survive_documents_upload_and_authorized_asset_reads(tmp_path, packaged):
    import base64
    import json
    import time

    from fastapi.testclient import TestClient
    from source_test_images import png
    from knowledge_service.api import create_app
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.integrations.image_ocr import OCRError

    class UnavailableOCR:
        engine = 'test'
        languages = 'ch'
        configuration_fingerprint = 'html-image-reading'

        def recognize(self, data):
            raise OCRError('ocr_engine_unavailable', 'test OCR unavailable')

    image_data = png()
    image_path = 'assets/存在.png' if packaged else 'data:image/png;base64,' + base64.b64encode(image_data).decode()
    text = ('# 规则\n\n<code>本规则自2025年1月1日起生效。</code>\n\n'
            '<div>本规则自2026年1月1日起生效。</div>\n\n'
            '<table><tr><td rowspan="2">要求</td><td><img src="assets/missing.png"></td>'
            f'<td><img src="{image_path}" alt="首图"></td></tr>'
            f'<tr><td><img src="{image_path}" alt="重复图"></td></tr></table>')
    with TestClient(create_app(tmp_path / 'html-images.sqlite', encoder=HashingEncoder(),
                               upload_temp=tmp_path / 'uploads')) as client:
        client.app.state.source_documents.image_ocr.adapter = UnavailableOCR()
        project = client.post('/api/projects', json={'name': 'HTML source'}).json()['id']
        options = {'extraction_mode': 'documents', 'document_time_mode': 'auto'}
        if packaged:
            batch = client.post(f'/api/projects/{project}/ingest-batches', json={'settings': options}).json()
            base = f'/api/projects/{project}/ingest-batches/{batch["id"]}'
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr('平台/规则.md', text)
                archive.writestr('平台/assets/存在.png', image_data)
            upload = client.post(base + '/files', files={'file': ('rules.zip', stream.getvalue())})
            assert upload.status_code == 201, upload.text
            assert client.post(base + '/start').status_code == 202
            for _ in range(150):
                item = client.get(base + '/items').json()['items'][0]
                if item['status'] in {'completed', 'needs_attention'}:
                    break
                time.sleep(.05)
            assert item['status'] == 'completed', item
            document = item['result']['document']
        else:
            upload = client.post(f'/api/projects/{project}/documents/upload',
                files={'file': ('rules.md', text.encode())}, data={'options': json.dumps(options)})
            assert upload.status_code == 201, upload.text
            document = upload.json()['document']
        assert document['valid_from'] == '2025-12-31T16:00:00.000000Z'
        version = document['metadata']['source_document_version_id']
        source_base = f'/api/projects/{project}/source-documents/{version}'
        assert client.get(source_base + '/source').content == text.encode()
        rendered = client.get(source_base + '/rendered')
        assert rendered.status_code == 200, rendered.text
        assert '<table>' in rendered.text and 'rowspan="2"' in rendered.text
        assert '图片缺失' in rendered.text
        assert 'data-image-index=' not in rendered.text
        assert rendered.text.index('图片缺失') < rendered.text.index('alt="首图"') < rendered.text.index('alt="重复图"')
        urls = re.findall(r'<img[^>]+src="([^"]+)"', rendered.text)
        assert len(urls) == 2
        for url in urls:
            asset = client.get(url)
            assert asset.status_code == 200 and asset.content == image_data
            assert asset.headers['content-type'] == 'image/png'


if __name__ == '__main__':
    unittest.main()
