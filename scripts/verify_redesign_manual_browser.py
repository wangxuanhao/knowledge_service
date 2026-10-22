"""Render the offline manual and verify navigation, images and small screens."""
import json
import importlib.util
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('manual_browser_contract', ROOT/'scripts/verify_redesign_manual.py')
MANUAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MANUAL)


def main():
    report = {'errors': [], 'source': MANUAL.MARKDOWN.name, 'edition': MANUAL.EDITION}
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda error:report['errors'].append(str(error)))
        page.goto(MANUAL.OUTPUT.as_uri(),wait_until='load')
        report['chapters'] = page.locator('section.chapter').count()
        report['images'] = page.locator('img').count()
        inventory = MANUAL.HtmlInventory()
        inventory.feed(MANUAL.OUTPUT.read_text(encoding='utf-8'))
        expected_images = len(inventory.assets)
        assert expected_images >= 10, 'The illustrated guide unexpectedly lost its current UI captures'
        for picture in page.locator('img').all():
            picture.scroll_into_view_if_needed()
            page.wait_for_function('(src)=>[...document.images].some(i=>i.getAttribute("src")===src&&i.complete&&i.naturalWidth>0)',arg=picture.get_attribute('src'))
        page.locator('#manual-search').fill('Milvus')
        report['search_matches'] = page.locator('.toc a:not([hidden])').count()
        assert 0 < report['search_matches'] < report['chapters']
        page.locator('#manual-search').fill('')
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(ROOT/'docs/research/2026-10-20-unified-manual-reader-desktop.png'))
        for width in (850,390):
            page.set_viewport_size({'width':width,'height':900})
            report[f'overflow_{width}'] = page.evaluate('document.documentElement.scrollWidth>innerWidth')
        page.locator('#menu-toggle').click()
        report['mobile_menu_opens'] = page.locator('#menu-toggle').get_attribute('aria-expanded') == 'true'
        report['title'] = page.title()
        browser.close()
    (ROOT/'docs/research/2026-10-20-unified-manual-reader-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    assert report['chapters'] == len(MANUAL.COVERAGE) and report['images'] == expected_images and not report['errors']
    assert report['mobile_menu_opens'] and not report['overflow_850'] and not report['overflow_390']


if __name__ == '__main__':
    main()
