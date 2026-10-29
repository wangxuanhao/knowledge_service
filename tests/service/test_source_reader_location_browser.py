"""Real DOM source navigation with immutable, character-level evidence mappings."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'


def test_reader_locates_entity_without_painting_whole_blocks(tmp_path):
    prefix = '开头😀同样提到《电子商务法》，但不是本条证据。'
    evidence = '根据《电子商务法》处理投诉。'
    paragraph = prefix + ' ' + evidence + ' 后续无关说明。'
    start = paragraph.index(evidence)
    end = start + len(evidence)
    chunk_text = '# 平台规则\n\n' + paragraph
    chunk_start = chunk_text.index(paragraph)
    mapped = dict(block_id='evidence', chunk_version_id='chunk-v',
                  block_start_char=start, block_end_char=end,
                  chunk_start_char=chunk_start + start, chunk_end_char=chunk_start + end)
    doc = dict(original_name='投诉须知.md', version=1, sha256='fixed-hash',
               blocks=[dict(block_id='evidence', type='paragraph', normalized_text=paragraph,
                            source_start_line=3, source_end_line=3)], assets=[],
               chunks=[dict(id='model-chunk', version_id='chunk-v', text=chunk_text,
                            metadata={}, spans=[mapped])])
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            args=['--disable-gpu', '--disable-gpu-compositing'])
        page = browser.new_page(viewport={'width': 1280, 'height': 800})

        def respond(route):
            path = route.request.url.split('reader.test', 1)[-1]
            if path.endswith('/rendered'):
                route.fulfill(content_type='text/html', body=
                    '<p>无关开头</p>' * 30 + '<p data-block-id="evidence">' + prefix +
                    ' 根据<strong>《电子商务法》</strong>处理投诉。 后续无关说明。</p>' + '<p>无关末尾</p>' * 30)
            elif path.endswith('/source'):
                route.fulfill(content_type='text/plain; charset=utf-8', body='# 投诉须知\n\n' + paragraph)
            elif '/source-documents/' in path:
                route.fulfill(json=doc)
            else:
                route.fulfill(content_type='text/html', body='<button>打开</button>')

        page.route('http://reader.test/**', respond)
        page.goto('http://reader.test/')
        page.add_style_tag(path=str(WEB / 'source-reader.css'))
        page.add_script_tag(path=str(WEB / 'source-content.js'))
        page.add_script_tag(path=str(WEB / 'source-reader.js'))
        page.evaluate('(options)=>SourceReader.open(options)', dict(projectId='p', versionId='v',
                      focusText='《电子商务法》', location=dict(document_version_id='v', status='available',
                      precision='evidence', spans=[mapped])))
        marks = page.locator('.source-reader-body mark')
        assert marks.count() == 1, 'only the selected entity occurrence should be marked'
        assert marks.inner_text() == '《电子商务法》'
        assert page.locator('p.source-highlight,section.source-highlight').count() == 0, 'no whole paragraph yellow'
        assert marks.evaluate('(n)=>{const r=n.getBoundingClientRect(),b=n.closest("article").getBoundingClientRect();return r.top>=b.top&&r.bottom<=b.bottom;}')
        page.get_by_role('button', name='解析视图').click()
        page.locator('.source-parsed-block[open]').wait_for()
        chunk = page.locator('details[data-chunk-version-id="chunk-v"]')
        assert chunk.get_attribute('open') is not None
        assert chunk.locator('mark').inner_text() == '《电子商务法》'
        assert page.locator('section[data-block-id="evidence"] mark').inner_text() == '《电子商务法》'
        page.screenshot(path=str(tmp_path / 'source-reader-exact-parsed.png'))
        page.get_by_role('button', name='原始视图').click()
        page.get_by_role('link', name='下载固定版本原文件').wait_for()
        assert '投诉须知' in page.locator('.source-reader-body').inner_text()
        browser.close()


if __name__ == '__main__':
    import sys
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.benchmarks/source-reader-location')
    output.mkdir(parents=True, exist_ok=True)
    test_reader_locates_entity_without_painting_whole_blocks(output)
    print(json.dumps({'source_reader_entity_location': 'passed'}))
