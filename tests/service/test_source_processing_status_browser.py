"""Source confirmations and ingestion history stay distinct in the real drawer."""
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from test_workspace_redesign_ui import redesign_page


def test_source_load_more_appends_only_new_directory_rows(redesign_page):
    page, errors = redesign_page

    def sources(route):
        query = parse_qs(urlparse(route.request.url).query)
        start = int(query.get('cursor', ['0'])[0])
        end = min(1000, start + 50)
        route.fulfill(json={'documents': [
            {'id': f'd-{index}', 'metadata': {'title': f'资料 {index}', 'status': 'completed'}}
            for index in range(start, end)
        ], 'total': 1000, 'next_cursor': str(end) if end < 1000 else None,
            'counts': {'all': 1000, 'ready': 1000, 'processing': 0, 'failed': 0}})

    page.route('**/api/projects/p/sources?*', sources)
    page.evaluate("() => window.KnowledgeTabs.open('sources')")
    for count in range(50, 950, 50):
        page.wait_for_function("count => document.querySelectorAll('#source-list .source-item').length === count", arg=count)
        page.locator('#source-load-more').evaluate('button => button.click()')
    page.wait_for_function("document.querySelectorAll('#source-list .source-item').length === 950")
    page.evaluate("""() => {
      const list = document.getElementById('source-list');
      const originalRows = [...list.children];
      let added = 0, removed = 0;
      const start = performance.now();
      const observer = new MutationObserver(entries => {
        for (const entry of entries) {
          added += entry.addedNodes.length;
          removed += entry.removedNodes.length;
        }
        if (list.children.length !== 1000) return;
        observer.disconnect();
        window.sourceAppendMetrics = {added, removed, retained: originalRows.filter(row => row.isConnected).length,
          elapsed_ms: performance.now() - start};
      });
      observer.observe(list, {childList: true});
      document.getElementById('source-load-more').click();
    }""")
    page.wait_for_function('window.sourceAppendMetrics')
    metrics = page.evaluate('window.sourceAppendMetrics')
    print('Sources append:', metrics)
    assert (metrics['added'], metrics['removed'], metrics['retained']) == (50, 0, 950)
    assert page.locator('#source-list .source-item').last.locator('b').inner_text() == '资料 999'
    assert errors == []


@pytest.mark.parametrize('width,height', [(1440, 950), (390, 844)])
def test_published_source_facts_are_visible_beside_discovery_history(redesign_page, width, height):
    page, errors = redesign_page
    page.set_viewport_size({'width': width, 'height': height})
    document = {'id': 'd', 'kind': 'document', 'text': '平台基础规则原文',
                'metadata': {'title': '平台基础规则_未成年人模式使用须知.md', 'status': 'completed'}}
    page.route('**/api/projects/p/records/d', lambda route: route.fulfill(json=document))
    page.route('**/api/projects/p/ingest-runs**', lambda route: route.fulfill(json={'runs': [{
        'id': 'discovery-run', 'status': 'completed', 'active_stage': 'completed',
        'created_at': '2026-10-22T05:51:13Z', 'attempt': 1,
        'counts': {'chunks_total': 1, 'chunks_failed': 0, 'records_accepted': 1, 'assertions_pending': 0},
        'readiness': {'document_ready': True, 'keyword_ready': True, 'semantic_ready': True,
                      'candidate_ready': True, 'graph_ready': False},
    }]}))
    page.route('**/api/projects/p/assertions**', lambda route: route.fulfill(json={'assertions': [{
        'document_id': 'd', 'kind': 'entity', 'status': 'accepted', 'canonical_record_id': 'formal-fact',
    }]}))
    page.evaluate("() => window.KnowledgeTabs.open('sources')")
    page.locator('#source-list .source-item').first.click()
    page.locator('.source-detail-tabs button').filter(has_text='处理记录').click()
    page.wait_for_function("document.querySelector('.source-graph-status')?.textContent.includes('已确认入图事实：1 条')")
    drawer = page.locator('#source-detail-drawer')
    assert '当次入图：未直接入图' in drawer.inner_text()
    assert '正式图谱：尚未就绪' not in drawer.inner_text()
    assert drawer.locator('.source-readiness').get_attribute('aria-label') == '当次处理结果'
    assert drawer.evaluate('(node) => node.scrollWidth <= node.clientWidth + 1')
    screenshots = Path(__file__).resolve().parents[2] / '.benchmarks' / 'source-graph-status'
    screenshots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshots / f'drawer-{width}.png'), full_page=True)
    assert errors == []
