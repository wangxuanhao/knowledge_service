#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""D3「停用/恢复 + 影响面」真机探针（只读：绝不提交停用）。

验证四件事：
  ① 选中一个类后，右栏出现「停用这个术语」区；
  ② 影响面数字与服务端 term-impact 直读一致（不是前端编的）；
  ③ 未读到时按钮禁用；读到影响面但原因为空时**仍然禁用**（不许盲停）；
  ④ 填入原因后按钮才可用 —— 到这一步就停，不点它。

前提：项目本体里要有类。没有就先建一个可丢弃项目（自带种子本体），跑完删掉。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

os.environ.setdefault('NO_PROXY', '127.0.0.1,localhost')
EDGE = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
CHECKS: list[tuple[bool, str]] = []


def ok(cond: bool, label: str, detail: str = '') -> bool:
    CHECKS.append((bool(cond), label))
    print(('  ✔ ' if cond else '  ✘ ') + label + (f'  [{detail}]' if detail else ''))
    return bool(cond)


def req(base: str, path: str, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data,
                               method=method or ('POST' if data else 'GET'),
                               headers={'Content-Type': 'application/json'} if data else {})
    with urllib.request.urlopen(r, timeout=30) as resp:
        raw = resp.read().decode() or 'null'
        return json.loads(raw)


def main() -> int:
    # 必须用 argparse 收 `--url`：汇总入口（verify_all_chains.py）按约定传的是
    # `--url <地址>`，早先用 sys.argv[1] 取位置参数，拿到的就是字符串 '--url' 本身，
    # 拼出来是 `--url/api/projects` → `unknown url type`。所有 verify_*.py 都遵守这条约定。
    parser = argparse.ArgumentParser(description='D3 停用/影响面真机只读探针')
    parser.add_argument('--url', default='http://127.0.0.1:8100', help='服务地址')
    args = parser.parse_args()
    base = args.url.rstrip('/')

    # 用现有项目（只读探测，不动它的数据）；没有就现建一个可丢弃的
    projects = (req(base, '/api/projects') or {}).get('projects') or []
    disposable = None
    if projects:
        pid = projects[0]['id']
        print(f'用现有项目：{projects[0]["name"]}')
    else:
        created = req(base, '/api/projects', {'name': 'D3探针·临时', 'use_default_ontology': True})
        pid, disposable = created['id'], created['id']
        print(f'现建可丢弃项目：{pid[:8]}…')

    try:
        onto = req(base, f'/api/projects/{pid}/ontology') or {}
        classes = ((onto.get('summary') or {}).get('classes') or [])
        if not classes:
            print('该项目本体里没有类，无法验证 D3'); return 2
        target = classes[0]
        iri = target['id']
        print(f'目标类：{target.get("label_zh") or target.get("label") or iri}')

        # 服务端直读的影响面（作为对照真值）
        truth = req(base, f'/api/projects/{pid}/ontology/term-impact?uri={urllib.parse.quote(iri)}')
        print(f'服务端影响面真值：{json.dumps(truth, ensure_ascii=False)}')

        from playwright.sync_api import sync_playwright

        def pending_count() -> int:
            d = req(base, f'/api/projects/{pid}/ontology-drafts') or {}
            return len([x for x in (d.get('items') or []) if x.get('status') == 'pending'])

        # 现有项目可能本来就有历史 pending 草案，所以只能比"前后不变"，不能比"等于 0"
        pending_before = pending_count()

        with sync_playwright() as pw:
            with tempfile.TemporaryDirectory(prefix='d3-', ignore_cleanup_errors=True) as profile:
                ctx = pw.chromium.launch_persistent_context(
                    profile, executable_path=str(EDGE), headless=True,
                    viewport={'width': 1680, 'height': 950})
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                errors: list[str] = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(base + '/', wait_until='domcontentloaded')
                page.wait_for_function("() => document.querySelectorAll('#project option').length > 1",
                                       timeout=20000)
                page.select_option('#project', pid)
                page.wait_for_timeout(800)
                page.evaluate("""() => {
                  const b = document.querySelector('[data-tab="ontology-model"]');
                  if (b) { const g = b.closest('details'); if (g) g.open = true; b.click(); }
                }""")
                page.wait_for_timeout(2500)

                # 选中目标类（走主控 select，等价于点左栏项）
                page.evaluate("""(iri) => {
                  const M = window.OntologyModel;
                  if (M && typeof M.select === 'function') M.select(iri);
                }""", iri)
                page.wait_for_timeout(1500)

                host = 'section.om-detail, .om-detail'
                html = page.evaluate(f"() => (document.querySelector('{host}')||{{}}).innerHTML || ''")
                ok('停用这个术语' in html or '恢复这个术语' in html,
                   '① 右栏出现「停用/恢复」动作区')

                # 影响面文字是否与真值同数（前端显示必须来自服务端）
                impact_text = page.evaluate(
                    "() => { const el = document.querySelector('.om-retire-impact'); return el ? el.textContent : ''; }")
                want = f"{truth.get('record_count', 0)} 条正式知识"
                ok(want in impact_text, '② 影响面数字与服务端真值一致', impact_text[:60] or '未取到')

                # 原因为空时按钮必须禁用
                disabled_before = page.evaluate(
                    "() => { const el = document.querySelector('.om-retire-actions button'); return el ? el.disabled : null; }")
                ok(disabled_before is True, '③ 原因为空时「确认」按钮是禁用的')

                # 填入原因后应变可用（到这一步就停，不点提交）
                page.evaluate("""() => {
                  const t = document.querySelector('.om-retire-reason');
                  if (t) { t.value = '探针：只验证按钮可用性，不提交'; t.dispatchEvent(new Event('input', {bubbles:true})); }
                }""")
                page.wait_for_timeout(400)
                disabled_after = page.evaluate(
                    "() => { const el = document.querySelector('.om-retire-actions button'); return el ? el.disabled : null; }")
                ok(disabled_after is False, '④ 填了原因后按钮变为可用')

                # 留一张截图给人工核对（停在"按钮可用"这一刻，没有提交）
                shot = Path(r'C:\Users\admin\AppData\Local\hermes\profiles\workspace\cache\scratch\ui-7-retire.png')
                page.screenshot(path=str(shot))
                print(f'  截图 -> {shot}')

                # 确认没有真写：草案数应与探针开始前一致（本探针只读，不该产生新草案）
                pending_after = pending_count()
                ok(pending_after == pending_before, '⑤ 探针没有产生新的待审草案（确认只读）',
                   f'{pending_before} → {pending_after}')

                if errors:
                    print('  JS 报错：' + ' | '.join(errors[:3]))
                ok(not errors, '⑥ 页面无 JS 报错')
                ctx.close()
    finally:
        if disposable:
            try:
                req(base, f'/api/projects/{disposable}', method='DELETE')
                print(f'已删除临时项目 {disposable[:8]}…')
            except urllib.error.HTTPError as e:
                print(f'删临时项目失败（{e.code}）')

    passed = sum(1 for c, _ in CHECKS if c)
    print(f'\n结果：{passed}/{len(CHECKS)} 通过' + (' · 全部通过 ✔' if passed == len(CHECKS) else ' · 有失败'))
    return 0 if passed == len(CHECKS) else 1


if __name__ == '__main__':
    sys.exit(main())
