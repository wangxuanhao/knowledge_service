"""Exercise the ledger, real governance routes and PostgreSQL with a seven-member group."""
from contextlib import ExitStack
import socket
from threading import Thread
from time import monotonic, sleep

import pytest
import uvicorn
from ontology_seed import seed_ontology
from test_attribute_conflict_sets import TTL
from test_workspace_redesign_ui import EDGE

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.ontology import Ontology


def test_ledger_partition_seven_into_two_survives_reload(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    if not EDGE.exists():
        pytest.skip('System Edge is required for the ledger browser test')
    app = create_app(tmp_path / 'partition-browser', HashingEncoder())
    with ExitStack() as stack:
        listener = stack.enter_context(socket.socket())
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False))
        thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()

        def shutdown():
            server.should_exit = True
            thread.join(timeout=30)

        stack.callback(shutdown)
        deadline = monotonic() + 60
        while not server.started:
            assert thread.is_alive() and monotonic() < deadline, 'Test service startup failed'
            sleep(.1)
        service = app.state.service
        repo = service.repository
        project = repo.create_project('分组复核验收')['id']
        seed_ontology(repo, project, TTL, Ontology(TTL).summary())
        destinations = [1, 2, 1, 2, 1, 2, 2]
        source_titles = ['商品信息管理规范', '内容发布规范', '商品审核细则', '发布实施细则',
                         '商家入驻指南', '直播行为规范', '内容审核指引']
        service.write(project, [
            dict(id=f'doc-{number}', kind='document', text=title, metadata={'title': title})
            for number, title in enumerate(source_titles, start=1)])
        service.write(project, [
            dict(id=f'person-{number}', kind='entity', type='Person', text='未成年人',
                 source_id=f'doc-{number}', valid_from='2025-01-01' if target == 1 else '2026-01-01',
                 metadata={'applicability': {'platforms': {'mode':'specific',
                           'values':['京东' if target == 1 else '抖音电商']}}})
            for number, target in enumerate(destinations, start=1)])

        runtime = stack.enter_context(playwright.sync_playwright())
        browser = runtime.chromium.launch(headless=True, executable_path=str(EDGE))
        stack.callback(browser.close)
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.set_default_timeout(30_000)
        page.set_default_navigation_timeout(90_000)
        page.goto(f'http://127.0.0.1:{port}/', wait_until='domcontentloaded')
        page.wait_for_function('id => document.getElementById("project")?.value === id', arg=project)

        def scan_group():
            page.locator('[data-tab="records"]').click()
            page.locator('#ledger-open-governance').click()
            drawer = page.locator('#ledger-governance-drawer')
            drawer.get_by_role('button', name='扫描已有重复实体', exact=True).click()
            return drawer.locator('.duplicate-candidate').filter(has_text='未成年人')

        card = scan_group()
        card.get_by_role('button', name='复核组内成员').click()
        review = page.get_by_role('dialog', name='复核组内成员')
        matrix = review.get_by_role('table', name='成员差异对比')
        matrix.get_by_role('columnheader', name='平台', exact=True).wait_for()
        assert matrix.get_by_text('京东', exact=True).count() == 3
        assert matrix.get_by_text('抖音电商', exact=True).count() == 4
        for number in (1, 2):
            review.get_by_role('checkbox', name=f'保留实体 {number:02}', exact=True).check()
        for number, target in enumerate(destinations[2:], start=3):
            review.get_by_role('button', name=f'{number:02} 归入 {target:02}', exact=True).click()
        page.screenshot(path=tmp_path / 'partition-comparison.png')
        review.get_by_role('button', name='预览分组方案').click()
        review.get_by_role('checkbox', name='已核对归并影响').check()
        page.screenshot(path=tmp_path / 'partition-preview.png')
        with page.expect_response(lambda res: res.url.endswith('/duplicates/partition/confirm')) as saved:
            review.get_by_role('button', name='确认分组方案').click()
        assert saved.value.ok, saved.value.text()
        review.get_by_text('分组复核已完成', exact=True).first.wait_for()
        page.screenshot(path=tmp_path / 'partition-completed.png')
        for number, target in enumerate(destinations, start=1):
            row = repo.get_record(project, f'person-{number}')
            if number == target:
                assert not row['metadata'].get('_deleted')
            else:
                assert row['metadata']['merged_into'] == f'person-{target}'
        page.reload()
        scan_group().get_by_text('分组复核已完成', exact=False).first.wait_for()
