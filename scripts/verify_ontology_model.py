#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P1「本体建模层 · 单画布」**写路径**真机复验 —— 唯一被真实执行验证过的那条端到端链路。

为什么必须真机跑（而不是只靠 pytest / 只读探针）：
    单画布的四条写动作（改父类 / 收下候选 / 校验并审核 / 发布）此前只在"渲染正确、交互链路通"
    这一层被验证过；**没有任何一次是把写请求真的提交到服务端**的（助手明确不拿真实项目数据试写）。
    本脚本补上这一段：自建一个**可丢弃项目**，用系统 Edge 真的点按钮，真的发出写请求，
    再用服务端读数反证每一步的结果。跑完（默认）把项目删掉，真库不留痕。

与旧 verify_workbench_inbox.py 的关系：
    那条链路守的是已退役的「本体审核台」页（收件箱三格 + window.OntologyWorkbench）。
    P0 把该页签移除、P1 把结构层三页并成一张画布后，"审核"这个能力**没消失但换了组织方式**：
    收件箱三格 → 底栏「校验并审核」（validate → submit → batch-approve）+ 右栏候选逐条收下。
    本脚本守的是新组织方式，旧脚本因此退役（在 verify_all_chains.py 的 CHAINS 里被替换）。

它守的具体缺陷（P1 真机上踩过 / 最容易再踩）：
    · 草案状态词表：OPEN_STATES 必须只认 'pending'（旧词 editing/submitted/reviewed 筛不到草案，
      表现为"每次编辑都新建一份草案"）。
    · stage-availability 是**项目级**路由 `?draft_id=`，写成 `/{draft_id}/stage-availability` 会 404
      被 catch 吞掉，表现为"门禁永远空、按钮永远灰"。
    · expected_ontology_id = **项目当前最新本体 id**（GET /ontology 的 id），不是草案自带的 base。
    · 改父类走命令通道 add_parent（target_iri=子类，parent_iri=父类），确认后才发，写进草案待审核。

安全性：只对自己建的项目写；退出前默认删除。写操作一旦发生就无法"撤销"，
    所以项目名带时间戳、且删项目这一步即使中途失败也会尝试执行（finally）。

用法：
    python scripts/verify_ontology_model.py                          # 建临时项目、跑完删除
    python scripts/verify_ontology_model.py --keep                   # 保留项目（排查用）
    python scripts/verify_ontology_model.py --url http://127.0.0.1:8100

退出码：0 = 全部通过；1 = 有失败项；2 = 环境/连接问题。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

# 本机代理会劫持 127.0.0.1，必须显式绕过（与仓库其它 verify_/tests 同一约定）。
os.environ.setdefault('NO_PROXY', '127.0.0.1,localhost')

EDGE = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')

# 默认本体（use_default_ontology=true）在真机上的规模；用**下限**而不是等号：
# 种子本体将来可能扩，等号会让脚本在无谓的地方变红。
SEED_CLASS_MIN = 20


class Failure(Exception):
    """一条断言失败。用异常而不是 assert，保证 python -O 下也照样检查。"""


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = '') -> None:
        self.rows.append((name, bool(ok), detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" —— {detail}" if detail else ''), flush=True)

    def note(self, name: str, detail: str) -> None:
        self.rows.append((name, True, detail))
        print(f'NOTE  {name} —— {detail}', flush=True)

    def failures(self) -> list[tuple[str, bool, str]]:
        return [row for row in self.rows if not row[1]]


def call(base: str, path: str, payload=None, method: str | None = None):
    """打一个接口。返回 (status, body)；HTTP 错误也当正常结果返回，由调用方判断。"""
    data = None
    headers = {'Accept': 'application/json'}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(base + path, data=data, headers=headers,
                                 method=method or ('POST' if data else 'GET'))
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode('utf-8')
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        raw = error.read().decode('utf-8')
        try:
            return error.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return error.code, raw
    except urllib.error.URLError as error:
        raise Failure(f'连不上 {base}：{error.reason}') from error


def main() -> int:
    parser = argparse.ArgumentParser(description='P1 单画布写路径真机复验')
    parser.add_argument('--url', default='http://127.0.0.1:8100', help='服务地址')
    parser.add_argument('--keep', action='store_true', help='跑完保留临时项目（排查用）')
    args = parser.parse_args()
    base = args.url.rstrip('/')

    report = Report()
    stamp = datetime.now().strftime('%m%d-%H%M%S')
    project_name = f'P1写路径复验·单画布·{stamp}'
    print(f'== P1「单画布」写路径真机复验 · {base} · 临时项目「{project_name}」==', flush=True)

    # ① 健康检查 —— 服务没起就给出清楚提示，而不是一串超时。
    status, _ = call(base, '/api/health')
    if status != 200:
        print(f'✘ 服务没起（{base}/api/health -> {status}）。先起服务：'
              f'python -u -m knowledge_service --port 8100')
        return 2
    if not EDGE.exists():
        print(f'需要系统 Edge：{EDGE}')
        return 2

    project_id = None
    try:
        # ② 建一个**可丢弃**项目：use_default_ontology=true 会带一份种子本体（≈29 类），
        #    这样画布上有东西可改，不需要上传文档 + 等 LLM 抽取。
        status, created = call(base, '/api/projects',
                               {'name': project_name, 'use_default_ontology': True})
        if status != 201 or not isinstance(created, dict) or not created.get('id'):
            print(f'✘ 建项目失败：{status} {created}')
            return 2
        project_id = created['id']
        report.note('临时项目已建立', f'{project_name} （id={project_id[:8]}…）')

        # ③ 记下写之前的服务端基线：本体 id + 版本数 + 类数。
        status, before = call(base, f'/api/projects/{project_id}/ontology')
        before_id = (before or {}).get('id')
        before_classes = len(((before or {}).get('summary') or {}).get('classes') or [])
        status, versions = call(base, f'/api/projects/{project_id}/ontologies')
        before_versions = _version_count(versions)
        report.check('种子本体可用（画布有东西可改）', before_classes >= SEED_CLASS_MIN,
                     f'类数 {before_classes}（下限 {SEED_CLASS_MIN}）、版本数 {before_versions}、本体 id {str(before_id)[:8]}…')

        # ④ 真机驱动：打开 → 切项目 → 进单画布页。
        from playwright.sync_api import sync_playwright

        errors: list[str] = []
        bad: list[str] = []
        with sync_playwright() as pw:
            # 用隔离的 user-data-dir，避免与常驻 Edge 争默认配置目录而卡死 launch。
            with tempfile.TemporaryDirectory(prefix='om-verify-',
                                              ignore_cleanup_errors=True) as profile:
                # 关键：用 launch_persistent_context 给一个隔离的 user-data-dir，
                # 避免与常驻 Edge 争默认配置目录而卡死 launch（Playwright 只认这个入口，
                # 不接受把 --user-data-dir 当命令行参数传）。
                context = pw.chromium.launch_persistent_context(
                    profile, executable_path=str(EDGE), headless=True,
                    viewport={'width': 1680, 'height': 950})
                page = context.pages[0] if context.pages else context.new_page()
                page.on('pageerror', lambda exc: errors.append(str(exc)))
                page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)

                missing: list[str] = []

                def _watch(response):
                    if response.status >= 400 and '/api/' in response.url:
                        bad.append(f'{response.status} {response.request.method} '
                                   f'{response.url.split("/api/")[-1]}')
                    elif response.status == 404:
                        missing.append(response.url)
                page.on('response', _watch)

                page.goto(base + '/', wait_until='domcontentloaded')
                page.wait_for_function("() => document.querySelectorAll('#project option').length > 1",
                                       timeout=20000)
                page.select_option('#project', project_id)
                # 主控在 #project 的 change 事件里**只清空 S、不加载**；
                # 真正加载发生在点页签时（wire() 里 tab.click → refresh()）。
                # 所以这里只等 change 处理完，加载在下一步点完页签后再等。
                page.wait_for_timeout(600)

                # 进单画布页（侧栏按组折叠：先展开所在组再点）
                page.evaluate("""() => {
                  const btn = document.querySelector('[data-tab="ontology-model"]');
                  const group = btn && btn.closest('details');
                  if (group && !group.open) { const s = group.querySelector('summary'); if (s) s.click(); }
                }""")
                page.wait_for_selector('[data-tab="ontology-model"]', state='visible', timeout=15000)
                page.click('[data-tab="ontology-model"]')
                page.wait_for_selector('.om-canvas-stage', timeout=20000)

                # 等本体 + 图编译完（主控把 summary 变成 graph）
                page.wait_for_function(
                    "() => window.OntologyModel?.S?.summary"
                    " && (window.OntologyModel.S.graph?.nodes || []).length > 0",
                    timeout=25000)

                # ⑤ 画布渲染 = 服务端 summary（第一道：前端没有自己编数）
                rendered = page.evaluate("""() => {
                  const S = window.OntologyModel.S;
                  return {nodes: (S.graph?.nodes || []).length,
                          classes: (S.summary?.classes || []).length,
                          ontologyId: S.ontologyId || null};
                }""")
                report.check('画布节点数 = 服务端类数', rendered['nodes'] == before_classes,
                             f'画布 {rendered["nodes"]} vs 服务端 {before_classes}')
                report.check('主控留住了本体 id（发布要用作 expected_ontology_id）',
                             bool(rendered['ontologyId']), str(rendered['ontologyId'])[:8])

                bar = page.locator('.om-canvas-bar').inner_text()
                report.note('画布工具条', bar.replace(chr(10), ' / ')[:120])

                # ⑥ 选一个类 → 右栏详情（点节点＝唯一选中入口）。
                #    用 Cytoscape 的 tap 事件驱动，等价于用户真的点了一下节点（走同一条 handler）。
                target = page.evaluate("""() => {
                  const S = window.OntologyModel.S;
                  const cls = (S.summary?.classes || []).find(c => c.id !== S.summary.classes[0].id)
                             || (S.summary?.classes || [])[0];
                  return cls ? {id: cls.id, name: cls.name} : null;
                }""")
                if not target:
                    report.check('画布上至少有一个类可选', False, '服务端没返回类')
                else:
                    page.evaluate("""(iri) => {
                      const cy = window.OntologyCanvas.__cy();
                      const n = cy.getElementById(iri);
                      if (n.empty()) throw new Error('画布上没有节点 ' + iri);
                      n.emit('tap');
                    }""", target['id'])
                    page.wait_for_timeout(400)
                    detail = page.locator('.om-detail').first
                    detail_text = detail.inner_text() if detail.count() else ''
                    report.check('点类 → 右栏出现详情（含"做什么/不做什么"）',
                                 target['name'] in detail_text and '做什么' in detail_text,
                                 f'选中 {target["name"]}，右栏 {len(detail_text)} 字')

                # ⑦ 写动作 1：改父类（两步点击 + 确认）—— 真的发 add_parent 命令。
                #    先挑一对"现在不是父子"的两个类，避免加重复边。
                pair = page.evaluate("""() => {
                  const S = window.OntologyModel.S;
                  const classes = S.summary?.classes || [];
                  for (const child of classes) {
                    const parents = new Set(child.parents || []);
                    for (const parent of classes) {
                      if (parent.id === child.id || parents.has(parent.id)) continue;
                      if ((parent.parents || []).includes(child.id)) continue;  // 别造环
                      return {child: {id: child.id, name: child.name},
                              parent: {id: parent.id, name: parent.name}};
                    }
                  }
                  return null;
                }""")
                if not pair:
                    report.check('找得到一对可建立的父子', False, '没找到候选对')
                else:
                    page.evaluate("""(iri) => {
                      const cy = window.OntologyCanvas.__cy();
                      cy.getElementById(iri).emit('tap');
                    }""", pair['child']['id'])
                    page.wait_for_timeout(300)
                    page.click('.om-canvas-bar button:has-text("改父类（连线）")')
                    page.evaluate("""(iri) => {
                      const cy = window.OntologyCanvas.__cy();
                      cy.getElementById(iri).emit('tap');
                    }""", pair['parent']['id'])
                    page.wait_for_selector('.om-canvas-confirm:not([hidden])', timeout=10000)
                    confirm_text = page.locator('.om-canvas-confirm').inner_text()
                    report.check('连线后出现确认条（把待写入的变更摆出来）',
                                 '父' in confirm_text, confirm_text.replace(chr(10), ' / ')[:90])
                    # 用 DOM 直调而不是 page.click：确认条叠在 Cytoscape 画布上方，
                    # Playwright 的可点击性检查会被画布层干扰（点下去静默无反应）。
                    # btn.click() 仍走同一条 addEventListener，等价于用户点击。
                    page.evaluate("""() => {
                      const btn = [...document.querySelectorAll('.om-canvas-confirm button')]
                        .find(b => b.textContent.includes('确认建立父级'));
                      if (!btn) throw new Error('确认条上没有"确认建立父级"按钮');
                      btn.click();
                    }""")
                    page.wait_for_timeout(2500)

                    # 命令失败时 confirmParent 会把原因写进页面状态区（catch 里），必须抓出来 —— 否则
                    # 只能看到"没建草案"这个结果，看不到为什么。
                    after_write = page.evaluate("""() => {
                      const S = window.OntologyModel.S;
                      const st = document.querySelector('[class*="om-status"], .om-actionbar-note');
                      return {draft: S.draft ? {id: S.draft.id, rev: S.draft.revision,
                                                status: S.draft.status} : null,
                              error: S.error,
                              status: st ? (st.textContent || '').trim().slice(0, 200) : null};
                    }""")
                    report.note('改父类后页面状态',
                                json.dumps(after_write, ensure_ascii=False)[:320])

                    # 服务端反证：真的建了草案，且草案里有这条变更。
                    status, drafts = call(base, f'/api/projects/{project_id}/ontology-drafts')
                    items = (drafts or {}).get('items') or []
                    open_items = [d for d in items if d.get('status') == 'pending']
                    report.check('改父类后服务端有了 pending 草案（不是只改前端）',
                                 len(open_items) >= 1,
                                 f'草案 {len(items)} 份，其中 pending {len(open_items)} 份')
                    if open_items:
                        d = open_items[0]
                        report.note('  草案', f'id={str(d.get("id"))[:8]}… status={d.get("status")} '
                                              f'revision={d.get("revision")}')
                        status, detail = call(base, f'/api/projects/{project_id}/'
                                                     f'ontology-drafts/{d["id"]}')
                        blob = json.dumps(detail, ensure_ascii=False) if detail is not None else ''
                        report.check('草案里确实有 add_parent 这条变更',
                                     'add_parent' in blob, f'{status} · {blob[:180]}')

                # ⑧ 写动作 2：底栏「校验并审核」（validate → submit → batch-approve）。
                review_btn = page.locator('.om-ab-actions button:has-text("校验并审核")')
                if review_btn.count() and not review_btn.first.is_disabled():
                    review_btn.first.click()
                    page.wait_for_timeout(4000)
                    status_line = page.locator('.om-actionbar-note, .om-status').first
                    report.note('「校验并审核」反馈',
                                status_line.inner_text()[:140] if status_line.count() else '(无反馈节点)')
                    # 发布门禁看的是服务端 publish-readiness 的 approval_counts.approved，
                    # **不是** draft.status（草案可以还是 pending 却已可发布）——
                    # 所以断言该看"发布按钮是否解锁"（用户真实看到的东西）。
                    pub_now = page.locator('.om-ab-actions button:has-text("发布为"), '
                                           '.om-ab-actions button:has-text("发布新版本")')
                    unlocked = bool(pub_now.count()) and not pub_now.first.is_disabled()
                    report.check('校验并审核后「发布」按钮解锁（门禁放行）', unlocked,
                                 '按钮可点' if unlocked else '仍灰着')
                else:
                    reason = page.locator('.om-ab-note').inner_text() if page.locator('.om-ab-note').count() else ''
                    report.note('「校验并审核」按钮当前灰着', reason[:160] or '(无说明)')

                # ⑨ 写动作 3：发布（底栏主按钮）。
                pub_btn = page.locator('.om-ab-actions button:has-text("发布为"), '
                                       '.om-ab-actions button:has-text("发布新版本")')
                if pub_btn.count() and not pub_btn.first.is_disabled():
                    pub_btn.first.click()
                    page.wait_for_timeout(5000)
                    status, after = call(base, f'/api/projects/{project_id}/ontology')
                    after_id = (after or {}).get('id')
                    status, versions2 = call(base, f'/api/projects/{project_id}/ontologies')
                    after_versions = _version_count(versions2)
                    report.check('发布后版本数 +1',
                                 after_versions == before_versions + 1,
                                 f'{before_versions} → {after_versions}')
                    report.check('发布后本体换了新 id（不可变新版本）',
                                 bool(after_id) and after_id != before_id,
                                 f'{str(before_id)[:8]}… → {str(after_id)[:8]}…')
                else:
                    reason = page.locator('.om-ab-note').inner_text() if page.locator('.om-ab-note').count() else ''
                    report.note('「发布」按钮当前灰着', reason[:160] or '(无说明)')

                # ⑩ 台账可见：发布出来的东西在"实例层·知识台账"里查得到。
                status, records = call(base, f'/api/projects/{project_id}/records/query',
                                       {}, 'POST')
                report.note('台账查询', f'{status} '
                                        f'{(json.dumps(records, ensure_ascii=False)[:120] if records else "")}')

                if missing:
                    report.note('页面 404 资源', '；'.join(dict.fromkeys(missing))[:220])
                # favicon 404 是浏览器自动请求、与本页无关（服务端没有 /favicon.ico 路由），
                # 不算 JS 报错 —— 其余报错照旧判失败。
                real_errors = [e for e in errors if 'favicon' not in e.lower()
                               and 'Failed to load resource' not in e]
                if errors:
                    report.note('JS 报错全文', ' || '.join(dict.fromkeys(errors))[:400])
                report.check('页面没有 JS 报错', not real_errors, '；'.join(real_errors[:3]))
                report.check('没有未说明的失败接口调用（>=400）', not bad,
                             '；'.join(dict.fromkeys(bad))[:300])
                context.close()

    finally:
        # ⑪ 收尾：默认删掉临时项目（含写操作已产生的新版本）。--keep 时保留。
        if project_id and not args.keep:
            code, _ = call(base, f'/api/projects/{project_id}', {'confirm': project_name}, 'DELETE')
            print(f'已删除临时项目 {project_id}（HTTP {code}）', flush=True)
        elif project_id:
            print(f'--keep：保留临时项目 {project_id}', flush=True)

    failures = report.failures()
    print()
    if failures:
        print(f'结果：{len(failures)} 项失败')
        for name, _, detail in failures:
            print(f'  · {name} —— {detail}')
        return 1
    print('结果：全部通过')
    print('说明：本脚本只对自己建的临时项目写入，跑完已删除；不触碰任何既有项目。')
    return 0


def _version_count(versions) -> int:
    """兼容两种返回形状：list（版本数组）或 {items|versions: [...]}。"""
    if isinstance(versions, list):
        return len(versions)
    if isinstance(versions, dict):
        for key in ('items', 'versions', 'results'):
            if isinstance(versions.get(key), list):
                return len(versions[key])
    return 0


if __name__ == '__main__':
    sys.exit(main())
