"""Source confirmations and ingestion history stay distinct in the real drawer."""
from pathlib import Path

import pytest

from test_workspace_redesign_ui import redesign_page


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
    page.click('[data-tab="sources"]')
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
