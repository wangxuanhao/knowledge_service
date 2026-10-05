#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本体审核台真机复验（A2：收件箱模型 · 只有两个视图 · 收下即发布）。

⚠️ 已退役（2026-10-04 · P1）—— 别按它的红色判回归：
    本脚本驱动的 `[data-tab="ontology-workbench"]` 页签在 P0 被移除、结构层三页在 P1 并成
    「本体建模层 · 单画布」。页签已不存在，所以它**现在必然失败**。
    它守的审核能力**没有消失、只是换了组织方式**：收件箱三格 → 单画布底栏「校验并审核」
    （validate→submit→batch-approve）+ 右栏候选逐条收下。新的验收口径由
    scripts/verify_ontology_model.py 接手（已进 verify_all_chains.py 的 CHAINS）。
    保留本文件只为留下"能力迁移到哪"的痕迹 —— 待旧页面代码删除时一并删除。

为什么要这个脚本（而不是只靠 pytest）：
    tests/service/test_ontology_workbench_ui.py 跑在 mock fetch 上，证明的是
    "界面按约定渲染"；这里跑在**真实服务 + 真实项目数据**上，证明的是
    "接上真数据以后，这一页说的话还是真的"：
      ① 收件箱三格的数量来自服务端（机器抽的候选 / 业务方的申请 / 你自己的改动待确认），
         不是前端猜的；
      ② 这一页只有「发现」与「审核」两个视图 —— 旧名字（设计 / 校验 / 发布）不会凭空造出视图；
      ③ 编辑中的草案只给「去本体编辑台」的指路，这里**没有**第二个编辑入口；
      ④ 已提交的草案给出逐项收下队列，并且**没有**发布按钮（发布是「收下」的结果）；
      ⑤ 真库里草案状态**只有三档**（pending / accepted / rejected）—— 旧词汇
         （editing / submitted / reviewed / published / closed / stale_base /
         stale_source）还读得到就说明 0005 迁移没跑干净。

A3（状态 7 → 3）之后「这份请求走到哪一步」不再是状态，而是服务端**当场推导**的
`pending_phase ∈ {editing, reviewing, settled}`。所以本脚本挑草案时**问服务端**
（`GET …/ontology-drafts/stage-availability?draft_id=` 的 `pending_phase`），
不再拿旧状态名去分组 —— 旧词永远不会再出现，按它分组只会让 ③④ 两条静默跳过。

安全性：默认**只读**。不会收下任何候选、不会产生新版本。
    `--open-drafts` 只是选择性地查看已有草案，仍然不写任何数据。

用法：
    python -u -m knowledge_service --port 8100 &        # 先让服务跑起来
    python scripts/verify_workbench_inbox.py --url http://127.0.0.1:8100
    python scripts/verify_workbench_inbox.py --project <uuid>   # 指定项目

退出码：0 = 全部通过；1 = 有失败项；2 = 连接 / 参数问题。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 本机系统代理会把 127.0.0.1 的请求也劫持走（502），必须显式绕过 —— 与 tests 的约定一致。
os.environ.setdefault('NO_PROXY', '127.0.0.1,localhost')

EDGE = Path(r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')

# 本页**不允许**出现的东西：旧阶段条、发布表单、编辑器入口。
FORBIDDEN_SELECTORS = {
    '[data-workbench-stage]': '旧的四站阶段条按钮',
    '[data-publish-draft]': '发布按钮（发布是「收下」的结果，不是一个要点的按钮）',
    '#ontology-publisher': '发布人输入框（发布表单已删）',
    '[data-canvas-add-child]': '画布上的「＋子类」（编辑归「本体编辑台」）',
    '[data-canvas-link]': '画布上的「改父类」（编辑归「本体编辑台」）',
}
# 这一页不该再出现的旧叙事词。
FORBIDDEN_COPY = ('四阶段', '流水线四站', '阶段条')


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


def draft_phase(base: str, project_id: str, draft_id: str):
    """问服务端：这份请求走到哪一步（A3 之后它是**推导**字段，不是状态）。

    为什么不自己算：`submitted_at` + 决定行 + 变更条数三条一起才能算出 pending_phase，
    脚本重算一遍就等于把口径复制两份（改一处忘一处时，脚本会拿旧口径挑草案，
    然后静默跳过本该验的那条）。挑样本**不是断言**，用服务端的答案最诚实：
    断言部分仍然全部落在真实 DOM 上。
    """
    payload = fetch_json(
        base, f'/api/projects/{project_id}/ontology-drafts/stage-availability'
              f'?draft_id={draft_id}') or {}
    return payload.get('pending_phase'), payload.get('needs_rebase')


def pick_project(base: str, wanted: str | None) -> tuple[str, dict]:
    """挑项目：优先"有待处理草案、且其中至少一份已经提交过"的项目，其次有待处理草案的。

    收件箱三格里「你自己的改动待确认」这一格只有存在草案时才验得出来，
    「逐项收下队列」那一格只有**已提交**的待处理草案才验得出来，
    所以挑项目时先看草案分布，而不是随便取第一个。
    """
    projects = fetch_json(base, '/api/projects') or {}
    items = (projects.get('items') or projects.get('projects')) if isinstance(projects, dict) else projects
    if not items:
        raise Failure('服务里没有任何项目 —— 先建一个项目、写入知识，才有得验')
    if wanted:
        for project in items:
            if project.get('id') == wanted:
                return project['id'], project
        raise Failure(f'指定的项目不存在：{wanted}')

    best, best_rank, best_count = None, -1, -1
    for project in items:
        drafts = fetch_json(base, f"/api/projects/{project['id']}/ontology-drafts") or {}
        rows = drafts.get('items') or []
        # 排序键：① 有"已提交过的待处理草案"（能验逐项收下队列）> ② 只有待处理草案
        # > ③ 只有终态草案 > ④ 没有草案；再看草案条数。
        pending = [row for row in rows if row.get('status') == 'pending']
        submitted = [row for row in pending if row.get('submitted_at')]
        rank = 3 if submitted else 2 if pending else 1 if rows else 0
        if (rank, len(rows)) > (best_rank, best_count):
            best, best_rank, best_count = project, rank, len(rows)
    if best is None:  # 项目列表非空时不会走到这里，留着是为了不静默返回 None
        raise Failure('挑不出项目')
    return best['id'], best


def main() -> int:
    parser = argparse.ArgumentParser(description='本体审核台真机复验（A2 收件箱模型）')
    parser.add_argument('--url', default='http://127.0.0.1:8100', help='服务地址')
    parser.add_argument('--project', default=None, help='指定项目 uuid（默认自动挑）')
    parser.add_argument('--headful', action='store_true', help='显示浏览器窗口（排查用）')
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

    drafts = (fetch_json(base, f'/api/projects/{project_id}/ontology-drafts') or {}).get('items') or []
    by_status: dict[str, list] = {}
    for row in drafts:
        by_status.setdefault(row.get('status') or '?', []).append(row)
    report.note('草案分布', '、'.join(f'{status} {len(rows)} 条' for status, rows in sorted(by_status.items())) or '（没有草案）')

    # 旧词汇（editing / submitted / reviewed / published / closed / …）在 A3 之后
    # 一个都不该再出现；读得到就说明 0005 迁移没跑干净 —— 这是本脚本能提供、
    # mock 契约提供不了的证据（它读的是迁移后的真库）。
    legacy = sorted(status for status in by_status if status not in {'pending', 'accepted', 'rejected'})
    report.check('真库草案状态只有三档（pending / accepted / rejected）',
                 not legacy, f'仍在：{legacy}' if legacy else '无旧词汇残留')

    # 「走到哪一步」问服务端（pending_phase），不按状态名分组 —— 三档状态分不出
    # 「还没提交」与「已提交待逐条处理」。这两条筛选只用来**挑样本**，断言仍在 DOM 上。
    phases = {}
    for row in drafts:
        phases[row['id']] = draft_phase(base, project_id, row['id'])
    in_review = [row for row in drafts
                 if row.get('status') == 'pending' and phases[row['id']][0] in {'reviewing', 'settled'}]
    editing = [row for row in drafts
               if row.get('status') == 'pending' and phases[row['id']][0] == 'editing']
    report.note('推导阶段分布', '、'.join(
        f'{draft_id[:8]}={phase or "—"}{f"（需重新基线：{rebase}）" if rebase else ""}'
        for draft_id, (phase, rebase) in list(phases.items())[:8]) or '（没有草案）')

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=not args.headful, executable_path=str(EDGE))
        context = browser.new_context(viewport={'width': 1500, 'height': 1000})
        page = context.new_page()
        errors: list[str] = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        # 记下所有 >=400 的接口响应：审核台出问题时往往表现为"操作条莫名变成『刷新并恢复』"，
        # 那背后一定有一个 409/422 在响 —— 只看页面文字会误判成"文案写错了"。
        bad_responses: list[str] = []

        def _watch(response):
            if response.status >= 400 and '/api/' in response.url:
                bad_responses.append(f'{response.status} {response.request.method} {response.url.split("/api/")[-1]}')

        page.on('response', _watch)
        page.goto(base + '/', wait_until='domcontentloaded')
        page.wait_for_function("() => document.querySelectorAll('#project option').length > 1", timeout=20000)
        page.select_option('#project', project_id)
        # 项目列表加载完 ≠ 工作台已经切到这个项目：等它自己把 projectId 落到 state 上再继续。
        page.wait_for_function("id => window.OntologyWorkbench?.state?.projectId === id",
                               arg=project_id, timeout=20000)
        # 侧边栏是按组折叠的：目标页签所在的那一组没展开时，它虽然在 DOM 里但被 overflow 裁掉、点不到。
        # 先展开它所在的分组，再等它可见（真机上这一步等价于用户点一下分组标题）。
        page.evaluate("""() => {
          const btn = document.querySelector('[data-tab="ontology-workbench"]');
          const group = btn && (btn.closest('details.nav-group') || btn.closest('details'));
          if (group && !group.open) { const summary = group.querySelector('summary'); if (summary) summary.click(); }
        }""")
        page.wait_for_selector('[data-tab="ontology-workbench"]', state='visible', timeout=15000)
        page.click('[data-tab="ontology-workbench"]')
        page.wait_for_selector('[data-inbox-bucket]', timeout=15000)

        # ① 收件箱三格 + 真实计数
        buckets = page.evaluate("""() => Object.fromEntries(
            [...document.querySelectorAll('[data-inbox-bucket]')].map(button => [button.dataset.inboxBucket, {
              label: button.querySelector('strong')?.textContent?.trim() || '',
              count: button.querySelector('[data-inbox-count]')?.textContent?.trim() || '',
              disabled: button.disabled,
              copy: button.querySelector('small')?.textContent?.trim() || ''}]))""")
        expected = {'machine', 'request', 'mine'}
        report.check('收件箱三格齐全（机器抽的候选 / 业务方的申请 / 你自己的改动待确认）',
                     set(buckets) == expected, f'实际：{sorted(buckets)}')
        for bucket, row in sorted(buckets.items()):
            report.note(f'  格子 {bucket}', f"{row['label']} · {row['count']} · "
                                          f"{'禁用' if row['disabled'] else '可点'} · {row['copy']}")
        settled = page.evaluate("""() => [...document.querySelectorAll('[data-inbox-count]')]
            .every(node => node.textContent.trim() !== '—' && node.textContent.trim() !== '')""")
        report.check('三格计数来自服务端（不是还没读到的占位「—」）', settled)

        # ② 那些被删掉的东西真的不在页面上
        for selector, why in FORBIDDEN_SELECTORS.items():
            count = page.locator(selector).count()
            report.check(f'页面上没有{why}', count == 0, f'{selector} × {count}')

        body_text = page.locator('#tab-ontology-workbench').inner_text()
        stale = [word for word in FORBIDDEN_COPY if word in body_text]
        report.check('旧叙事词（四阶段 / 阶段条）没有残留', not stale, f'仍在：{stale}')

        # ③ 旧阶段名不会凭空造出视图（测的是**前端名字映射**，不是门禁）
        # 注意：门禁由服务端说了算，草案还没到「能进收件箱」的状态时 setStage('review') 本来就该被拒。
        # 所以这条要先挑一份能进审核视图的草案，否则测到的是门禁（它拦得对），会把结论带偏。
        reviewable = in_review
        if reviewable:
            page.evaluate("id => OntologyWorkbench.selectDraft(id)", reviewable[0]['id'])
            page.wait_for_timeout(600)
            landed = page.evaluate("""() => {
                OntologyWorkbench.setStage('review');
                const out = {review: OntologyWorkbench.state.stage};
                for (const name of ['design', 'validate', 'publish']) {
                  OntologyWorkbench.setStage(name);
                  out[name] = OntologyWorkbench.state.stage;
                }
                return out; }""")
            report.check('旧阶段名（design / validate / publish）一律落到 review',
                         all(value == 'review' for value in landed.values()), f'{landed}')
        elif drafts:
            report.note('旧阶段名回落', '这份项目里没有已提交（pending_phase=reviewing/settled）的草案，名字映射由 pytest 契约覆盖')
        else:
            report.note('旧阶段名回落', '该项目没有草案，跳过（回落逻辑见 pytest 契约）')

        # ④ 编辑中的草案：只有「去本体编辑台」的指路，没有第二个编辑入口
        if editing:
            draft_id = editing[0]['id']
            page.evaluate("id => OntologyWorkbench.selectDraft(id).then(() =>"
                          " OntologyWorkbench.setStage('review'))", draft_id)
            page.wait_for_selector('[data-go-to-designer]', timeout=15000)
            hint = page.locator('#ontology-workbench-canvas-content').inner_text()
            report.check('编辑中的草案：给出去「本体编辑台」的指路',
                         '本体编辑台' in hint and '保存并生效' in hint,
                         f'草案 {draft_id}')
            report.check('编辑中的草案：这一页没有画布编辑入口',
                         page.locator('[data-canvas-add-child]').count() == 0
                         and page.locator('[data-canvas-link]').count() == 0)
            page.click('[data-go-to-designer]')
            page.wait_for_timeout(800)
            editor_tab = page.locator('#tab-ontology-design')
            report.check('点「去本体编辑台」真的切到本体编辑台',
                         editor_tab.is_visible() and '本体编辑台' in page.locator('#title').inner_text(),
                         page.locator('#title').inner_text())
            page.click('[data-tab="ontology-workbench"]')
            page.wait_for_selector('[data-inbox-bucket]')
        else:
            report.note('编辑中的草案', '该项目没有编辑中的草案，跳过这条')

        # ⑤ 已提交的草案（pending_phase=reviewing/settled）：逐项收下队列 + 没有发布按钮
        reviewing = in_review
        if reviewing:
            draft_id = reviewing[0]['id']
            page.evaluate("id => OntologyWorkbench.selectDraft(id).then(() =>"
                          " OntologyWorkbench.setStage('review'))", draft_id)
            page.wait_for_selector('[data-review-operation]', timeout=20000)
            bar = page.locator('#ontology-workbench-actionbar').inner_text()
            page_text = page.locator('#tab-ontology-workbench').inner_text()
            report.note('  操作条', bar.replace(chr(10), ' / ')[:110])
            report.check('已提交的草案：能逐项收下，且写清"收完自动发布"',
                         '收下' in page_text and '自动发布' in page_text,
                         f'草案 {draft_id}｜操作条：{bar[:120]}')
            report.check('没有发布按钮 / 发布表单',
                         page.locator('[data-publish-draft]').count() == 0
                         and page.locator('#ontology-publisher').count() == 0)
            # 三条决策路都在**右侧检查器**的决策区（不是在操作条上）：逐项收下 / 退回让对方改 / 不收。
            page.click('[data-review-operation]')
            page.wait_for_selector('#ontology-workbench-inspector [data-decision-action]', timeout=15000)
            decisions = page.evaluate("""() => Object.fromEntries(
                [...document.querySelectorAll('#ontology-workbench-inspector [data-decision-action]')]
                  .map(button => [button.dataset.decisionAction, button.textContent.trim()]))""")
            report.check('逐条决策三路都在（收下 / 退回让对方改 / 不收）',
                         set(decisions) == {'approve', 'request_changes', 'reject'}
                         and '收下' in decisions.get('approve', '')
                         and '不收' in decisions.get('reject', ''),
                         f'{decisions}')
        else:
            report.note('已提交的草案', '该项目没有已提交（pending_phase=reviewing/settled）的草案，跳过这条')

        report.check('页面没有 JS 报错', not errors, '；'.join(errors[:3]))
        report.check('审核台没有静默失败的接口调用（>=400）', not bad_responses,
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
    print('说明：脚本自己不写任何业务数据；但进入「逐项收下」视图时，页面会按设计自动跑一次')
    print('      POST /validate（校验快照要落库），这会把该草案的修订号 +1 —— 真机复验时留意这一点。')
    return 0


if __name__ == '__main__':
    if not EDGE.exists():
        print(f'需要系统 Edge：{EDGE}')
        sys.exit(2)
    sys.exit(main())
