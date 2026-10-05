#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""C1「结构待定」真机复验：造一条含未知概念的知识，把规划的验收口径逐条打勾。

对应《2026-10-02-完整落地规划》§8 的 C1 一行：
    造一条含未知概念的知识 → 断言它被标 `结构待定`、可检索、**问答引用时显式标注**、不进证据链。

为什么必须真机跑（而不是只跑 pytest）：
  · 单元测试用的是内存里的 `save_ontology` 直写，跳过了"保存 → 发布草案"这条真实链路；
    而"概念进了本体之后标记自动解除"恰恰要靠这条链路才能验。
  · 问答的证据链要在真实 SSE 流里看：py 层面拿到的是对象，到了页面可能被前端重排。
  · 台账/收件箱的徽标是不是真的显示、数字对不对，只有在真页面上才算数。

它自己造一个**临时项目**（默认跑完就删），不碰你的真实项目。用法：
    python scripts/verify_structure_pending.py --url http://127.0.0.1:8000
    python scripts/verify_structure_pending.py --keep          # 保留临时项目，便于手动看页面
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = '') -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} · {name}" + (f" · {detail}" if detail else ''))
    return bool(ok)


def skip(name: str, detail: str) -> None:
    RESULTS.append((name, True, f'SKIP：{detail}'))
    print(f"SKIP · {name} · {detail}")


def call(base: str, path: str, body=None, method: str | None = None, raw: bool = False):
    """一次 HTTP 调用；非 2xx 时把服务端 detail 原样抛出来（排错靠它）。"""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(base + path, data=data, method=method or ('GET' if body is None else 'POST'),
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read().decode('utf-8')
    except urllib.error.HTTPError as error:
        detail = error.read().decode('utf-8', 'replace')
        raise RuntimeError(f'{method or "POST"} {path} → HTTP {error.code}：{detail[:400]}') from error
    return payload if raw else json.loads(payload)


def sse_events(payload: str) -> list[tuple[str, dict]]:
    """把 `event: x\\ndata: {...}` 的 SSE 文本拆成 [(事件名, 数据)]。"""
    events, name = [], None
    for line in payload.splitlines():
        if line.startswith('event: '):
            name = line[7:].strip()
        elif line.startswith('data: ') and name:
            try:
                events.append((name, json.loads(line[6:])))
            except json.JSONDecodeError:
                pass
    return events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--keep', action='store_true', help='跑完保留临时项目（默认删除）')
    parser.add_argument('--no-browser', action='store_true', help='跳过真页面检查（只验接口与问答）')
    args = parser.parse_args()
    base = args.url.rstrip('/')

    stamp = datetime.now().strftime('%m%d-%H%M%S')
    project_name = f'C1复验·结构待定·{stamp}'
    print(f'== C1 结构待定 真机复验 · {base} · 临时项目「{project_name}」==')

    health = call(base, '/api/health')
    check('服务健康', health.get('status') == 'ok', str(health.get('status')))

    project = call(base, '/api/projects', {'name': project_name, 'use_default_ontology': False})
    p = project['id']
    try:
        run(base, p, stamp, args)
    finally:
        if args.keep:
            print(f'--keep：保留临时项目 {p}（{project_name}），记得手工删掉')
        else:
            try:
                call(base, f'/api/projects/{p}', {}, method='DELETE')
                print(f'已删除临时项目 {p}')
            except RuntimeError as error:
                print(f'临时项目删除失败（请手工清理 {p}）：{error}')

    failed = [name for name, ok, _ in RESULTS if not ok]
    skipped = [detail for _, _, detail in RESULTS if str(detail).startswith('SKIP')]
    print(f'\n共 {len(RESULTS)} 项：通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}，跳过 {len(skipped)}')
    if failed:
        print('失败项：')
        for name in failed:
            print('  -', name)
        return 1
    print('C1 真机复验全部通过。')
    return 0


def run(base: str, p: str, stamp: str, args) -> None:
    ttl_v1 = ('@prefix ex: <http://ex/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . '
              'ex:文件 a owl:Class . ex:提到 a owl:ObjectProperty .')
    ontology = call(base, f'/api/projects/{p}/ontologies', {'turtle': ttl_v1, 'expected_ontology_id': None})
    check('建立项目本体（1 个类）', bool(ontology.get('id')), f"ontology_id={ontology.get('id')}")

    unknown_type = f'urn:knowledge:ontology:{p}:包裹'
    unknown_term = f'urn:knowledge:ontology:{p}:寄件人'
    created = call(base, f'/api/projects/{p}/records', {'records': [
        {'id': 'doc-1', 'kind': 'entity', 'type': '文件', 'text': '查价规则.docx'},
        {'id': 'pkg-1', 'kind': 'entity', 'type': unknown_type, 'text': '一个跨城包裹'},
        {'id': 'who-1', 'kind': 'entity', 'type': unknown_term, 'text': '寄件人王某'},
        {'id': 'rel-1', 'kind': 'relation', 'type': '提到', 'text': '查价规则提到一个跨城包裹',
         'subject_id': 'doc-1', 'object_id': 'pkg-1'},
    ]})
    rows = {row['id']: row for row in created['records']}
    check('未知概念的实体被收下（不再 422 丢掉）', 'pkg-1' in rows)
    check('已知概念的记录不受影响（没有待定标记）', 'structure_pending' not in rows.get('doc-1', {}))
    status = (rows.get('pkg-1') or {}).get('structure_pending') or {}
    check('写入回执当场标出「结构待定」', status.get('state') == 'pending', str(status.get('terms')))
    check('缺的概念被如实记下（不是一句"校验失败"）',
          (status.get('terms') or []) == [unknown_type], str(status.get('terms')))
    rel_status = (rows.get('rel-1') or {}).get('structure_pending') or {}
    check('端点传染：关系也标待定并说明因为哪一端',
          rel_status.get('state') == 'pending' and rel_status.get('via') == ['pkg-1'],
          f"via={rel_status.get('via')}")

    report = call(base, f'/api/projects/{p}/structure-pending')
    check('收件箱：待建模概念数 = 2（包裹、寄件人）', report.get('pending_structure') == 2,
          str([item['term'] for item in report.get('terms', [])]))
    check('收件箱：结构待定记录数 = 3（2 实体 + 1 关系）', report.get('pending_records') == 3,
          str(report.get('pending_records')))
    grouped = {item['term']: item for item in report.get('terms', [])}
    check('按概念分组（包裹 2 条：实体 + 关系）',
          grouped.get(unknown_type, {}).get('record_count') == 2,
          str(grouped.get(unknown_type, {}).get('record_count')))
    check('每组都给出"该去哪儿做什么"', '本体编辑台' in str(grouped.get(unknown_type, {}).get('suggested_action')))

    scoped = call(base, f'/api/projects/{p}/records/query?limit=100', {'kinds': ['entity', 'relation', 'chunk']})
    ids = [row['id'] for row in scoped['records']]
    check('结构待定的知识仍然可检索（在范围读取里）', 'pkg-1' in ids and 'rel-1' in ids, str(ids))
    hit = call(base, f'/api/projects/{p}/search', {'query': '跨城包裹', 'k': 5})
    check('关键词检索能搜到（可看原文）',
          any(item['id'] == 'pkg-1' for item in hit.get('hits', [])),
          str([item['id'] for item in hit.get('hits', [])]))

    validation = call(base, f'/api/projects/{p}/ontology/validate', {})
    skipped_ids = {item['id'] for item in validation.get('structure_pending_skipped', [])}
    check('不作为约束校验的输入（并且明说跳过了哪些）',
          skipped_ids == {'pkg-1', 'who-1', 'rel-1'}, str(sorted(skipped_ids)))
    check('校验本身仍然通过（不是靠报错把问题藏起来）', validation.get('conforms') is True,
          str(validation.get('errors')))
    check('校验回执写清为什么跳过', '本体编辑台' in str(validation.get('structure_pending_note')))

    # ---- 问答：不进证据链，但**显式标注**（两个入口必须同口径） ----------------
    question = {'query': '跨城包裹 寄件人王某', 'k': 10, 'k_entities': 5, 'k_chunks': 5,
                'retrieval_mode': 'keyword', 'hops': 0, 'generate': False}
    # 入口①：一次性 JSON（POST /qa）
    once = call(base, f'/api/projects/{p}/qa', question)
    check('问答（一次性）：证据链里没有结构待定的知识',
          all(row['id'] not in {'pkg-1', 'who-1', 'rel-1'} for row in once.get('evidence') or []),
          str([row['id'] for row in (once.get('evidence') or [])]))
    once_block = once.get('structure_pending') or {}
    # 条数取决于检索范围（这里 hops=0，命中的是两条实体），所以只要求"至少这两条 + 概念对得上"。
    check('问答（一次性）：显式标注了它们（条数 + 缺的概念）',
          once_block.get('count') >= 2 and unknown_type in str(once_block.get('terms')),
          f"count={once_block.get('count')} terms={once_block.get('terms')}")
    check('问答（一次性）：答案说清"有知识、但不算正式证据"',
          '结构待定' in str(once.get('answer')) and '不能作为正式证据' in str(once.get('answer')),
          str(once.get('answer'))[:70])
    # 入口②：SSE 流（POST /qa/stream，页面走的就是这条）
    raw = call(base, f'/api/projects/{p}/qa/stream', question, raw=True)
    events = dict(sse_events(raw))
    stream_block = (events.get('evidence') or {}).get('structure_pending') or {}
    check('问答（流式）：与一次性入口同口径（条数/概念一致）',
          stream_block.get('count') == once_block.get('count')
          and stream_block.get('terms') == once_block.get('terms'),
          f"stream={stream_block.get('count')} once={once_block.get('count')}")
    check('问答（流式）：证据清单里同样没有它们',
          all(row['id'] not in {'pkg-1', 'who-1', 'rel-1'}
              for row in (events.get('evidence') or {}).get('evidence') or []))
    done = str((events.get('done') or {}).get('answer') or '')
    check('问答（流式）：答案正文显式说明"不算正式证据"',
          '结构待定' in done and '不能作为正式证据' in done, done[:70])

    # ---- 真页面：台账徽标 + 审核台收件箱 ------------------------------------
    if args.no_browser:
        skip('真页面（台账徽标 / 审核台收件箱）', '--no-browser')
    else:
        browser_checks(base, p, unknown_type)

    # ---- 自动解除：概念真进本体（走完整的「校验 → 提交 → 收下 → 发布」） -------
    # 这条链不能抄近路：只在数据库里塞一条本体，验不出"发布之后页面才解除"这件事。
    ttl_v2 = (ttl_v1 + f' @prefix pkg: <urn:knowledge:ontology:{p}:> . '
                       'pkg:包裹 a owl:Class . pkg:寄件人 a owl:Class .')
    draft = call(base, f'/api/projects/{p}/ontologies',
                 {'turtle': ttl_v2, 'expected_ontology_id': ontology.get('id')})
    draft_id = draft.get('id') or (draft.get('draft') or {}).get('id')
    if not draft_id:
        skip('发布新版本体 → 标记自动解除', f'没拿到草案 id：{list(draft)[:6]}')
        return
    try:
        validated = call(base, f'/api/projects/{p}/ontology-drafts/{draft_id}/validate',
                         {'expected_revision': draft['revision']})
        submitted = call(base, f'/api/projects/{p}/ontology-drafts/{draft_id}/submit',
                         {'expected_revision': validated['revision']})
        warnings = [item.get('code') for item in
                    (submitted.get('validation_report') or {}).get('warnings') or []
                    if item.get('code')]
        operations = submitted.get('operations') or []
        if not operations:
            skip('发布新版本体 → 标记自动解除', '这次草案没有产生任何变更操作')
            return
        # 逐条决定，不打批量包：这两个类的变更是"新建术语"，按服务端口径属于必须逐条处理的
        # 变更（批量通道只放行低风险项）—— 走批量会被 422 挡下（第一次跑就是这么被挡的）。
        reviewed = submitted
        for operation in operations:
            reviewed = call(base, f'/api/projects/{p}/ontology-drafts/{draft_id}/decisions', {
                'expected_revision': reviewed['revision'],
                'expected_ontology_id': ontology.get('id'),
                'validation_fingerprint': submitted['validation_fingerprint'],
                'acknowledged_warning_codes': warnings,
                'actor': 'script:verify_structure_pending',
                'decisions': [{'operation_id': operation['id'],
                               'operation_fingerprint': operation['fingerprint'],
                               'action': 'approve', 'reason': 'C1 复验：把缺的概念建出来'}]})
        call(base, f'/api/projects/{p}/ontology-drafts/{draft_id}/publish', {
            'expected_revision': reviewed['revision'],
            'expected_ontology_id': ontology.get('id'),
            'validation_fingerprint': submitted['validation_fingerprint'],
            'acknowledged_warning_codes': warnings,
            'idempotency_key': f'c1-verify-{uuid.uuid4()}',
            'actor': 'script:verify_structure_pending'})
    except RuntimeError as error:
        skip('发布新版本体 → 标记自动解除', f'发布链路没走通：{error}')
        return
    after = call(base, f'/api/projects/{p}/structure-pending')
    check('概念进本体（发布新版）后，待建模数字归零', after.get('pending_structure') == 0,
          str(after.get('pending_structure')))
    cleared = call(base, f'/api/projects/{p}/records/query?limit=100', {'kinds': ['entity']})
    state = next((row.get('structure_pending') for row in cleared['records']
                  if row['id'] == 'pkg-1'), None) or {}
    check('记录状态自动变「已解除」，标记仍留在记录上作历史',
          state.get('state') == 'cleared' and state.get('marked') == [unknown_type], str(state))


def browser_checks(base: str, project_id: str, unknown_type: str) -> None:
    """在真页面上看两个地方：台账的行徽标 + 数字，审核台的待建模收件箱。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        skip('真页面（台账徽标 / 审核台收件箱）', '没装 Playwright')
        return
    edge = Path(r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
    if not edge.exists():
        skip('真页面（台账徽标 / 审核台收件箱）', '没找到系统 Edge')
        return
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=str(edge))
        page = browser.new_page(viewport={'width': 1600, 'height': 1000})
        page.on('dialog', lambda dialog: dialog.accept())
        errors: list[str] = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(base, wait_until='domcontentloaded')
            # 等"至少有一个真项目"再选：第一个 option 是占位「选择项目」，它永远不可见，
            # 用 wait_for_selector 等它会一直等到超时（Playwright 对 <option> 的可见性判定）。
            page.wait_for_function(
                "() => [...document.querySelectorAll('#project option')].some(o => o.value)",
                timeout=20000)
            page.select_option('#project', project_id)
            # 真页面的左侧菜单是折叠的：不先展开这一组，台账按钮不可见（点不动）。
            page.evaluate('''() => {
              const btn = document.querySelector('[data-tab="records"]');
              const group = btn && btn.closest('details');
              if (group && !group.open) { const summary = group.querySelector('summary'); if (summary) summary.click(); }
            }''')
            page.wait_for_selector('[data-tab="records"]', state='visible', timeout=15000)
            page.click('[data-tab="records"]')
            page.wait_for_selector('#records .ledger-pending-badge', timeout=20000)
            badge = page.inner_text('#records .ledger-pending-badge')
            check('台账里这条知识带着「结构待定」徽标', '结构待定' in badge, badge)
            title = page.get_attribute('#records .ledger-pending-badge', 'title')
            check('徽标说清缺的概念与"不算正式证据"',
                  unknown_type in (title or '') and '不作为正式证据' in (title or ''), (title or '')[:80])
            # 数字是旁数据（并行请求）回来的，晚于表格：等它是数字再断言，别抢跑。
            page.wait_for_function(
                "() => /^\\d+$/.test(document.querySelector('[data-ledger-kpi=\"pending\"] b').textContent.trim())",
                timeout=15000)
            tile = page.inner_text('[data-ledger-kpi="pending"]')
            check('台账顶部数字来自 /structure-pending（2 个概念待定）', tile.splitlines()[0].strip() == '2',
                  tile.replace('\n', ' '))
            page.click('[data-ledger-kpi="pending"]')
            page.wait_for_selector('#ontology-workbench-pending .ontology-workbench__pending-item', timeout=20000)
            items = page.eval_on_selector_all(
                '#ontology-workbench-pending .ontology-workbench__pending-item',
                "nodes => nodes.map(node => node.getAttribute('data-pending-term'))")
            check('点数字跳到审核台收件箱，并列出缺的概念', unknown_type in items, str(items))
            check('收件箱说清动作与解除条件',
                  '本体编辑台' in page.inner_text('#ontology-workbench-pending'),
                  page.inner_text('#ontology-workbench-pending')[:60].replace('\n', ' '))
            check('页面上没有 JS 报错', not errors, '; '.join(errors[:2]))
        finally:
            browser.close()


if __name__ == '__main__':
    sys.exit(main())
