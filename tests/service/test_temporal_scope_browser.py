"""Offline browser contract for the shared search/QA temporal controls."""
import json
from datetime import datetime, timezone
from pathlib import Path

from test_workspace_redesign_ui import redesign_page  # noqa: F401


def test_temporal_filters_apply_to_search_and_frozen_qa_turns(redesign_page):
    page, errors = redesign_page
    # This fixture tests submission scope independently of the conversation API.
    page.route('**/assets/qa-conversations.js*', lambda route: route.fulfill(
        body='', content_type='application/javascript'))
    page.reload(wait_until='domcontentloaded')
    page.wait_for_function("() => document.querySelectorAll('#project option').length>1")
    page.select_option('#project', 'p')
    page.wait_for_function("() => current === 'p'")
    page.clock.set_fixed_time(datetime(2026, 10, 28, 9, tzinfo=timezone.utc))
    searches, questions = [], []

    def search(route):
        searches.append(route.request.post_data_json)
        route.fulfill(json={'hits': [], 'source_chunks': [], 'context_chunks': [],
            'candidate_count': 0, 'active_mode': searches[-1]['retrieval_mode'], 'complete': True})

    def question(route):
        questions.append(route.request.post_data_json)
        payload = {'answer_id': 'temporal-' + str(len(questions)), 'answer': '范围已核对',
                   'evidence': [], 'mode': 'evidence_only', 'generation': {'called': False}}
        route.fulfill(body='event: done\ndata: ' + json.dumps(payload) + '\n\n',
                      content_type='text/event-stream')

    page.route('**/projects/p/search', search)
    page.route('**/projects/p/qa/stream', question)
    page.click('#scope-toggle')
    assert page.locator('#scope-time-mode').input_value() == 'all'
    assert page.locator('#scope-include-unknown').is_disabled()
    page.select_option('#scope-time-mode', 'current')
    page.uncheck('#scope-include-unknown')
    page.click('#apply-scope')
    applied = page.evaluate("WorkspaceContext.read('p','search')")
    assert applied['valid_at'] == '2026-10-28T09:00:00.000Z'
    assert applied['include_unknown'] is False
    page.fill('#query', '退款')
    page.click('#search')
    page.wait_for_function("() => !document.getElementById('search').disabled")
    assert searches[-1]['valid_at'] == applied['valid_at']
    assert searches[-1]['include_unknown'] is False

    page.click('#scope-to-qa')
    assert page.locator('#qa-as-of').count() == 0
    page.fill('#qa-query', '退款是什么')
    page.click('#qa')
    page.wait_for_selector('.qa-turn-assistant[data-state="done"]')
    assert questions[-1]['valid_at'] == applied['valid_at']
    assert questions[-1]['include_unknown'] is False
    first_note = page.locator('.qa-submitted-scope').first.inner_text()

    page.click('#scope-toggle')
    page.select_option('#scope-time-mode', 'at')
    page.fill('#scope-valid-at', '')
    before = page.evaluate("WorkspaceContext.read('p','qa')")
    page.click('#apply-scope')
    assert page.evaluate("WorkspaceContext.read('p','qa')") == before
    assert page.locator('#cb-details').get_attribute('open') is not None
    page.locator('#status .ks-notice__close').click()
    if page.locator('#cb-details').get_attribute('open') is None:
        page.click('#scope-toggle')
    page.fill('#scope-valid-at', '2025-01-01T08:00')
    page.locator('.cb-advanced>summary').click()
    page.fill('#scope-known-at', '2026-10-27T08:00')
    page.click('#apply-scope')
    historical = page.evaluate("WorkspaceContext.read('p','qa')")
    page.fill('#qa-query', '退款的历史规则')
    page.click('#qa')
    assert len(questions) == 1
    assert '历史时点仅支持关键词' in page.locator('#qa-summary').inner_text()
    assert page.evaluate("WorkspaceContext.read('p','qa')") == historical
    page.locator('#qa-settings>summary').click()
    page.select_option('#qa-mode', 'keyword')
    page.locator('#qa-settings>summary').click()
    page.click('#qa')
    page.wait_for_function("() => document.querySelectorAll('.qa-turn-assistant[data-state=done]').length === 2")
    assert questions[-1]['valid_at'] == historical['valid_at']
    assert questions[-1]['known_at'] == historical['known_at']
    assert questions[-1]['include_unknown'] is False
    assert page.locator('.qa-submitted-scope').first.inner_text() == first_note

    page.click('#scope-toggle')
    page.select_option('#scope-time-mode', 'all')
    page.click('#apply-scope')
    assert page.evaluate("WorkspaceContext.read('p','qa').valid_at") is None
    assert page.evaluate("WorkspaceContext.read('p','qa').include_unknown") is True
    assert page.locator('.qa-submitted-scope').first.inner_text() == first_note
    assert page.evaluate("WorkspaceContext.read('p','search')") == applied
    page.set_viewport_size({'width': 390, 'height': 850})
    page.click('#scope-toggle')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    bounds = page.locator('#cb-details').bounding_box()
    assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 391, bounds
    page.locator('#scope-time-mode').scroll_into_view_if_needed()
    output = Path('.benchmarks/temporal-scope')
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / 'mobile-filters.png'))
    assert errors == []


if __name__ == '__main__':
    # Avoid a second pytest/PG session while another workspace task runs tests.
    fixture = redesign_page.__wrapped__()
    try:
        test_temporal_filters_apply_to_search_and_frozen_qa_turns(next(fixture))
        print('PASS: real browser shared time filters, strict unknown flag, QA history and mobile layout')
    finally:
        fixture.close()
