#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识台账真机复验（B1：实例层的家 · 查 / 改 / 并重复项 / 撤销误操作）。

为什么要这个脚本（而不是只靠 pytest）：
    tests/service/test_records_ledger_ui.py 跑在 mock fetch 上，证明的是"界面按约定渲染"；
    这里跑在**真实服务 + 真实项目数据**上，证明的是"接上真数据以后，台账上的数字与列还是真的"：
      ① 顶部四个数字（实体 / 关系 / 原文片段 / 疑似重复）逐个与服务端独立算出的数字对照；
      ② 每一行的「挂在哪一版本体上」与本体的真实版本序列对照（当前 / 旧版不能标错）；
      ③ 每一行的「原文断言」与断言表独立算出的支撑句数对照（两条血缘去重后）；
      ④ 「可撤销的操作」条数 = 服务端审计操作数（不是写死的 0，也不是把不可逆的东西算进去）；
      ⑤ 「编辑 / 版本历史」点得开，而且打开的是**记录弹窗**（不是旧的内联面板）；
      ⑥ 这一页没有 JS 报错、没有静默失败的接口调用（>=400）。

安全性：默认**只读** —— 不改任何记录、不合并、不撤销、不建版本。
    `--write` 才会做一次真实的「建临时实体 → 软删除 → 点撤销恢复」，并在结尾如实报告遗留了什么。

用法：
    python -u -m knowledge_service --port 8100 &          # 先让服务跑起来
    python scripts/verify_knowledge_ledger.py --url http://127.0.0.1:8100
    python scripts/verify_knowledge_ledger.py --project <uuid>
    python scripts/verify_knowledge_ledger.py --write      # 额外做一次可撤销性实测

退出码：0 = 全部通过；1 = 有失败项；2 = 连接 / 参数问题。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# 本机系统代理会把 127.0.0.1 的请求也劫持走（502），必须显式绕过 —— 与 tests 的约定一致。
os.environ.setdefault('NO_PROXY', '127.0.0.1,localhost')

EDGE = Path(r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')

VIEWS = {'entity': ('实体', ['名称', '本体类型', '挂在哪一版本体上', '版本', '原文断言', '操作']),
         'relation': ('关系', ['关系', '关系类型', '挂在哪一版本体上', '版本', '原文断言', '操作']),
         'chunk': ('原文片段', ['原文片段', '来源文档', '版本', '原文断言', '操作'])}


class Failure(Exception):
    """一条检查失败。用异常而不是 assert，保证 python -O 下也照样检查。"""


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = '') -> None:
        self.rows.append((name, bool(ok), detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" —— {detail}" if detail else ''))

    def note(self, name: str, detail: str) -> None:
        self.rows.append((name, True, detail))
        print(f"NOTE  {name} —— {detail}")

    def failures(self) -> list[tuple[str, bool, str]]:
        return [row for row in self.rows if not row[1]]


def fetch_json(base: str, path: str):
    """只读 GET。用 urllib 免得为脚本再引依赖。"""
    import urllib.error
    import urllib.request
    request = urllib.request.Request(base + path, headers={'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode('utf-8')
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raise Failure(f'{path} 返回 HTTP {error.code}') from error
    except urllib.error.URLError as error:
        raise Failure(f'连不上 {base}：{error.reason}') from error


def pick_project(base: str, wanted: str | None) -> tuple[str, dict]:
    """挑项目：优先"记录多、且能验出更多面"的项目（有疑似重复 > 有审计操作 > 记录多）。"""
    projects = fetch_json(base, '/api/projects') or {}
    items = (projects.get('items') or projects.get('projects')) if isinstance(projects, dict) else projects
    if not items:
        raise Failure('服务里没有任何项目 —— 先建一个项目、写入知识，才有得验')
    if wanted:
        for project in items:
            if project.get('id') == wanted:
                return project['id'], project
        raise Failure(f'指定的项目不存在：{wanted}')

    best, best_key = None, None
    for project in items:
        pid = project['id']
        records = record_query(base, pid)
        entities = sum(1 for row in records if row.get('kind') == 'entity')
        groups = len((fetch_json(base, f'/api/projects/{pid}/duplicate-groups') or {}).get('groups') or [])
        operations = len((fetch_json(base, f'/api/projects/{pid}/operations') or {}).get('operations') or [])
        key = (1 if groups else 0, 1 if operations else 0, entities)
        if best_key is None or key > best_key:
            best, best_key = project, key
    if best is None:  # 项目列表非空时不会走到这里，留着是为了不静默返回 None
        raise Failure('挑不出项目')
    return best['id'], best


def record_query(base: str, project_id: str) -> list[dict]:
    """一次拿全（脚本只读，不走分页）：台账的四个数字与每一行都从这份数据独立重算。

    注意：记录列表是 **POST /records/query**（body 是 Scope，limit/offset 在查询串上），
    不是 GET /records —— 用 GET 会收到 405。
    """
    payload = post_json(base, f'/api/projects/{project_id}/records/query?limit=1000',
                        {'kinds': ['entity', 'relation', 'chunk']}) or {}
    rows = payload.get('records') or payload.get('items') or []
    return [row for row in rows if row.get('kind') in ('entity', 'relation', 'chunk')]


def ontology_ownership(base: str, project_id: str, rows: list[dict]) -> dict[str, str]:
    """独立重算每一行的「挂在哪一版本体上」——版本序号按发布时间排（与服务端不给序号的事实一致）。"""
    versions = (fetch_json(base, f'/api/projects/{project_id}/ontologies') or {}).get('versions') or []
    ordered = sorted(versions, key=lambda v: str(v.get('created_at') or ''))
    index = {row['id']: number + 1 for number, row in enumerate(ordered)}
    latest = ordered[-1]['id'] if ordered else None

    def describe(row: dict) -> tuple | str:
        """返回 (版本号, 创建日期, 是不是当前版本)；无法判定时返回一句人话。"""
        ontology_id = row.get('ontology_id')
        if not ontology_id:
            return '未知（这条记录没有本体归属）'
        number = index.get(ontology_id)
        if number is None:
            return '未知版本（该版本已不在项目里）'
        matches = [v for v in ordered if v['id'] == ontology_id]
        stamp = str(matches[0].get('created_at') or '')[:10]
        return (number, stamp, ontology_id == latest)

    return {row['id']: describe(row) for row in rows}


def parse_ownership(text: str) -> tuple | None:
    """把页面上那句「本体 v1 · 2026/10/1（旧版）」解析成 (版本号, 日期, 是不是当前)。

    刻意不比对字符串：页面按中文习惯显示不补零的月日（2026/10/1），
    脚本从接口拿到的是 ISO（2026-10-01）。比的是"同一版本、同一天、同样的当前/旧版判定"。
    """
    match = re.match(r'^本体 v(\d+) · (\d{4})/(\d{1,2})/(\d{1,2})(（当前）|（旧版）)?$', (text or '').strip())
    if not match:
        return None
    number = int(match.group(1))
    year, month, day = (int(value) for value in match.groups()[1:4])
    return (number, f'{year:04d}-{month:02d}-{day:02d}', match.group(5) == '（当前）')


def assertion_support(base: str, project_id: str, rows: list[dict]) -> dict[str, int]:
    """独立重算每一行的「原文断言」：候选血缘 ∪ 已归位血缘，同一句只算一次。

    口径必须与 records-view.js 的 supportCount() 完全一致，否则这个脚本就是自欺欺人：
      ① 发现候选：记录的 metadata.discovery_candidate_id(s) === 断言的 id；
      ② 已归位：断言的 canonical_record_id === 这条记录的 id。
    """
    assertions = (fetch_json(base, f'/api/projects/{project_id}/assertions') or {}).get('assertions') or []
    by_id = {item['id'] for item in assertions}
    by_canonical: dict[str, set] = defaultdict(set)
    for item in assertions:
        if item.get('canonical_record_id'):
            by_canonical[item['canonical_record_id']].add(item['id'])
    counts = {}
    for row in rows:
        metadata = row.get('metadata') or {}
        lineage = set()
        if metadata.get('discovery_candidate_id'):
            lineage.add(metadata['discovery_candidate_id'])
        lineage.update(metadata.get('discovery_candidate_ids') or [])
        counts[row['id']] = len((lineage & by_id) | by_canonical.get(row['id'], set()))
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description='知识台账真机复验（B1）')
    parser.add_argument('--url', default='http://127.0.0.1:8100', help='服务地址')
    parser.add_argument('--project', default=None, help='指定项目 uuid（默认自动挑）')
    parser.add_argument('--headful', action='store_true', help='显示浏览器窗口（排查用）')
    parser.add_argument('--write', action='store_true',
                        help='额外做一次真实的「建临时实体 → 软删除 → 撤销恢复」'
                             '（会在项目里留下审计记录，详见结尾说明）')
    args = parser.parse_args()

    base = args.url.rstrip('/')
    report = Report()
    try:
        project_id, project = pick_project(base, args.project)
    except Failure as error:
        print(f'无法开始：{error}')
        return 2
    report.note('项目', f"{project.get('name') or project_id} ({project_id})")

    from playwright.sync_api import sync_playwright

    rows = record_query(base, project_id)
    expected_ownership = ontology_ownership(base, project_id, rows)
    expected_support = assertion_support(base, project_id, rows)
    operations = (fetch_json(base, f'/api/projects/{project_id}/operations') or {}).get('operations') or []
    groups = (fetch_json(base, f'/api/projects/{project_id}/duplicate-groups') or {}).get('groups') or []
    counts = {kind: sum(1 for row in rows if row.get('kind') == kind) for kind in ('entity', 'relation', 'chunk')}
    report.note('服务端独立算出的数字',
                f"实体 {counts['entity']} · 关系 {counts['relation']} · 原文片段 {counts['chunk']} · "
                f"疑似重复 {len(groups)} 组 · 可撤销操作 {len(operations)} 条")

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=not args.headful, executable_path=str(EDGE))
        context = browser.new_context(viewport={'width': 1560, 'height': 1000})
        page = context.new_page()
        errors: list[str] = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        # 撤销是不可逆动作，页面点下去会先弹一次 confirm —— 脚本按「确认」处理，
        # 否则 Playwright 默认**拒绝**所有对话框，我们会误判成"撤销没生效"。
        page.on('dialog', lambda dialog: dialog.accept())
        bad_responses: list[str] = []

        def _watch(response):
            if response.status >= 400 and '/api/' in response.url:
                bad_responses.append(f'{response.status} {response.request.method} {response.url.split("/api/")[-1]}')

        page.on('response', _watch)
        page.goto(base + '/', wait_until='domcontentloaded')
        page.wait_for_function("() => document.querySelectorAll('#project option').length > 1", timeout=20000)
        page.select_option('#project', project_id)
        page.wait_for_function("() => document.querySelector('#title') && document.querySelector('#title').textContent.trim() !== ''",
                               timeout=20000)
        # 侧边栏按组折叠：目标页签没展开时虽在 DOM 里但被 overflow 裁掉、点不到（真机等价于用户点一下分组标题）。
        page.evaluate("""() => {
          const btn = document.querySelector('[data-tab="records"]');
          const group = btn && (btn.closest('details.nav-group') || btn.closest('details'));
          if (group && !group.open) { const summary = group.querySelector('summary'); if (summary) summary.click(); }
        }""")
        page.wait_for_selector('[data-tab="records"]', state='visible', timeout=15000)
        page.click('[data-tab="records"]')
        page.wait_for_selector('.ledger-kpi', timeout=20000)
        page.wait_for_function("() => [...document.querySelectorAll('.ledger-kpi b')]"
                               ".every(node => node.textContent.trim() !== '—')", timeout=20000)

        # ① 这一页叫什么、边界是不是写清楚了（用户反复要的就是"这块是干什么的"）
        title = page.locator('#title').inner_text().strip()
        intro = page.inner_text('.ledger-intro')
        report.check('页签名与标题是「知识台账」', '知识台账' in title, title)
        report.check('一句话边界：这里管具体的东西，分类不在这',
                     '具体的东西' in intro and '本体编辑台' in intro and '本体审核台' in intro,
                     intro.replace('\n', ' ')[:90])

        # ② 顶部四个数字 = 服务端独立算出的数字
        kpi = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('.ledger-kpi')]
            .map(node => [node.dataset.ledgerKpi, node.innerText.trim().split('\\n')[0].trim()]))""")
        expected_kpi = {'entity': counts['entity'], 'relation': counts['relation'],
                        'chunk': counts['chunk'], 'duplicates': len(groups)}
        for key, wanted in expected_kpi.items():
            shown = kpi.get(key, '（没有这一格）')
            report.check(f"顶部数字 {key} 与服务端一致", shown == str(wanted), f'页面 {shown} / 服务端 {wanted}')

        # ③ 三个视角：表头逐列核对（列名就是"这一页承诺给你看什么"）
        tabs = page.evaluate("() => [...document.querySelectorAll('.ledger-tab')].map(n => n.textContent.trim())")
        report.check('三个视角齐全（实体 / 关系 / 原文片段）',
                     tabs[:3] == [VIEWS['entity'][0], VIEWS['relation'][0], VIEWS['chunk'][0]], f'{tabs}')
        headers = page.evaluate("() => [...document.querySelectorAll('.record-library thead th')].map(n => n.textContent.trim())")
        report.check('默认视角（实体）的列头与约定一致', headers == VIEWS['entity'][1], f'{headers}')
        for index, key in ((1, 'relation'), (2, 'chunk')):
            page.click(f'.ledger-tab:nth-child({index + 1})')
            page.wait_for_timeout(400)
            headers = page.evaluate("() => [...document.querySelectorAll('.record-library thead th')].map(n => n.textContent.trim())")
            expected_rows = sum(1 for row in rows if row.get('kind') == key)
            shown_rows = page.locator('.record-library tbody tr').count()
            pager = page.locator('.record-pages').inner_text().replace('\n', ' ')
            expected_pages = max(1, -(-expected_rows // 25))
            report.check(f"切到「{VIEWS[key][0]}」：列头正确", headers == VIEWS[key][1], f'{headers}')
            # 台账每页 25 条（沿用旧表格的分页），所以第一页行数要对，页数也要对得上总数
            report.check(f"切到「{VIEWS[key][0]}」：行数与分页数都对得上服务端",
                         shown_rows == min(25, expected_rows) and f'1 / {expected_pages} 页' in pager,
                         f'页面 {shown_rows} 行（{pager}）/ 服务端 {expected_rows} 条 ≈ {expected_pages} 页')
        page.click('.ledger-tab:nth-child(1)')
        page.wait_for_selector('.record-library tbody tr', timeout=15000)

        # ④ 逐行核对「挂在哪一版本体上」与「原文断言」——这两列是台账最有信息量的地方
        table = page.evaluate("""() => [...document.querySelectorAll('.record-library tbody tr')].map(row => {
            const cells = [...row.querySelectorAll('td')];
            return {name: cells[0].innerText.trim().split('\\n')[0].trim(),
                    ownership: cells[2].innerText.trim(), support: cells[4].innerText.trim()};})""")
        by_name = {(row['text'], row.get('ontology_id')): row for row in rows if row.get('kind') == 'entity'}
        ownership_bad, support_bad = [], []
        for item in table:
            match = [row for row in rows if row.get('kind') == 'entity'
                     and (row.get('text') or '').strip() == item['name']]
            if not match:
                continue
            row = match[0]
            if parse_ownership(item['ownership']) != expected_ownership.get(row['id']):
                ownership_bad.append(f"{item['name']}：页面「{item['ownership']}」≠ 服务端 «{expected_ownership.get(row['id'])}»")
            if not item['support'].startswith(str(expected_support.get(row['id'], 0))):
                support_bad.append(f"{item['name']}：页面「{item['support'].replace(chr(10), ' ')}」≠ 服务端 {expected_support.get(row['id'], 0)}")
        report.check(f"逐行「挂在哪一版本体上」都标对（{len(table)} 行）", not ownership_bad, '；'.join(ownership_bad[:3]))
        report.check(f"逐行「原文断言」都对得上（两条血缘去重后）", not support_bad, '；'.join(support_bad[:3]))
        sample = ', '.join(f"{item['name']}={item['ownership']}·{item['support'].splitlines()[0]}句" for item in table[:3])
        report.note('  抽样', sample or '（这个项目没有实体）')

        # ⑤ 可撤销的操作：条数与服务端一致；有的话逐条报出可点性
        section = page.inner_text('#ledger-operations')
        buttons = page.locator('#ledger-operations .ledger-operation button').count()
        report.check('「可撤销的操作」条数 = 服务端审计操作数',
                     buttons == len(operations), f'页面 {buttons} 个撤销按钮 / 服务端 {len(operations)} 条操作')
        if operations:
            report.note('  可撤销的操作', '；'.join(f"{(row.get('metadata') or {}).get('operation')}"
                                                 f"({len((row.get('metadata') or {}).get('before') or [])} 条记录)"
                                                 for row in operations[:4]))
        else:
            report.note('  可撤销的操作', '这个项目还没做过可撤销的治理动作（页面如实写"还没有"，不是写死 0）')
        report.check('无可撤销操作时，这一节说明了为什么是空的',
                     bool(operations) or ('还没有' in section or '没有可撤销' in section), section.replace('\n', ' ')[:90])

        # ⑥ 「编辑 / 版本历史」点得开，而且是**记录弹窗**（不是把内容塞进页面底部的旧面板）
        if table:
            page.click('.record-library tbody tr:first-child [data-edit-row]')
            page.wait_for_selector('#record-dialog[open]', timeout=15000)
            edit_title = page.locator('#record-dialog-title').inner_text()
            report.check('点「编辑」打开的是记录弹窗（编辑模式）',
                         edit_title.startswith('编辑记录'), edit_title)
            report.check('弹窗里给的是结构化编辑 + 高级 JSON',
                         page.locator('#record-structured-edit').is_visible()
                         and page.locator('#record-advanced-edit').count() == 1)
            page.click('#close-record-dialog')
            page.wait_for_timeout(300)
            page.click('.record-library tbody tr:first-child [data-history-row]')
            page.wait_for_selector('#record-dialog[open]', timeout=15000)
            history_title = page.locator('#record-dialog-title').inner_text()
            report.check('点「版本历史」打开的是同一个弹窗（历史模式）',
                         history_title.startswith('版本历史'), history_title)
            page.wait_for_selector('#record-history-view article', timeout=15000)
            report.note('  版本历史', page.locator('#record-history-view').inner_text().replace('\n', ' ')[:80])
            page.click('#close-record-dialog')
            page.wait_for_timeout(300)

        # ⑦ 疑似重复：有分组就必须"点得动、卡片齐全"；没有就如实报"这一项在这个项目里验不出来"
        if groups:
            page.click('.ledger-kpi[data-ledger-kpi="duplicates"]')
            page.wait_for_selector('#resolve-result .candidate', timeout=15000)
            cards = page.locator('#resolve-result .candidate').count()
            members = sum(len(group['members']) for group in groups)
            report.check('点「疑似重复」摆出可直接选角色的卡片',
                         cards == members, f'卡片 {cards} / 成员 {members}')
            first = groups[0]
            page.locator('#resolve-result .candidate').first.locator('[data-keep]').click()
            keep = page.input_value('#keep-id')
            report.check('卡片上的「设为保留实体」真的填进了合并向导',
                         bool(keep), f'keep-id={keep}')
        else:
            report.note('疑似重复', '这个项目里没有同名同类型的实体 —— 卡片交互由 pytest 契约覆盖；'
                                    '数字为 0 时那一格是禁用的（点不出不存在的分组）')
            disabled = page.evaluate("""() => {
                const node = [...document.querySelectorAll('.ledger-kpi')]
                  .find(item => item.dataset.ledgerKpi === 'duplicates');
                return node ? node.disabled : null; }""")
            report.check('数字为 0 时那一格禁用（不摆空分组）', disabled is True, f'disabled={disabled}')

        # ⑧ 可选：真实做一次「建临时实体 → 软删除 → 点撤销恢复 → 清理」，证明撤销不是摆设
        if args.write:
            sample = next((row for row in rows if row.get('kind') == 'entity'), None)
            if not sample:
                report.note('--write', '这个项目里没有实体，跳过真实撤销实测（写实体需要本项目本体里已有的类型）')
            else:
                record_id = f'verify-ledger-{datetime.now().strftime("%H%M%S")}'
                # 写实体必须带**本项目本体认识的类型**：空类型会被本体校验挡回 422
                # （"未知或存在歧义的本体术语：空"）——这就是为什么临时实体要照抄现有实体的 type/ontology_id。
                post_json(base, f'/api/projects/{project_id}/records', {'records': [
                    {'id': record_id, 'kind': 'entity', 'text': '台账复验临时实体',
                     'type': sample.get('type'), 'ontology_id': sample.get('ontology_id')}]})
                post_json(base, f'/api/projects/{project_id}/delete',
                          {'record_id': record_id, 'expected_version': 1})
                operations = (fetch_json(base, f'/api/projects/{project_id}/operations')
                              or {}).get('operations') or []
                # 不能用 operations[-1] 猜"最新那条"：服务端的审计记录是按 id/写入顺序回来的，
                # 顺序不等于时间顺序（上一版脚本就因此点到了别人的操作）。用"哪条审计动过这条记录"来认。
                newest = next((item for item in reversed(operations)
                               if any(row.get('id') == record_id
                                      for row in (item.get('metadata') or {}).get('before') or [])), None)
                newest = (newest or {}).get('metadata') or {}
                operation_id = newest.get('operation_id')
                report.check('软删除后在「可撤销的操作」里出现',
                             newest.get('operation') == 'delete' and bool(operation_id),
                             f"最新操作：{newest.get('operation')}（影响 {len(newest.get('before') or [])} 条记录）")
                page.click('#load-records')
                page.wait_for_selector(f'[data-undo-operation="{operation_id}"]', timeout=15000)
                report.check('页面上出现了那条「撤销这次操作」',
                             page.locator(f'[data-undo-operation="{operation_id}"]').count() == 1,
                             f'operation_id={operation_id}')
                page.evaluate("""id => {const node = [...document.querySelectorAll('[data-undo-operation]')]
                    .find(item => item.dataset.undoOperation === id); if (node) node.click(); }""", operation_id)
                page.wait_for_timeout(1500)
                alive = [row for row in record_query(base, project_id)
                         if row['id'] == record_id and not (row.get('metadata') or {}).get('_deleted')]
                report.check('点「撤销」真的把记录恢复了（不是只改了页面文字）', bool(alive),
                             f'记录 {record_id} {"已恢复" if alive else "仍然是删除状态"}')
                if alive:
                    # 收尾：临时实体不能留在项目里（审计记录留着 —— 那是撤销功能的记账）
                    version = alive[0].get('version')
                    post_json(base, f'/api/projects/{project_id}/delete',
                              {'record_id': record_id, 'expected_version': version})
                    report.note('--write 收尾',
                                f'临时实体 {record_id} 已删除（版本 {version} → 软删）；'
                                f'项目里留下这次删除+撤销+再删除的审计记录，不清理（撤销功能靠它记账）')
        report.check('页面没有 JS 报错', not errors, '；'.join(errors[:3]))
        report.check('台账没有静默失败的接口调用（>=400）', not bad_responses,
                     '；'.join(dict.fromkeys(bad_responses))[:300])
        context.close()
        browser.close()

    failures = report.failures()
    print()
    if failures:
        print(f'结果：{len(failures)} 项失败')
        for name, _, detail in failures:
            print(f'  · {name} —— {detail}')
        return 1
    print('结果：全部通过')
    print('说明：默认只读 —— 不改记录、不合并、不撤销、不建版本；本节验的是"页面说的 = 服务端的事实"。')
    return 0


def post_json(base: str, path: str, payload: dict):
    """只在 --write 时使用：写一个临时实体、软删它、再点撤销恢复。"""
    import urllib.request
    request = urllib.request.Request(base + path, data=json.dumps(payload).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode('utf-8') or '{}')


if __name__ == '__main__':
    if not EDGE.exists():
        print(f'需要系统 Edge：{EDGE}')
        sys.exit(2)
    sys.exit(main())
