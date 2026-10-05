#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统一深色设计层的真机截图探针（只读：不改任何数据）。

目的：`theme.css` 是"最后加载、覆盖旧样式"的换肤层。光看 CSS 源码证明不了它真的盖住了
旧的浅色规则（特异性、加载顺序、!important 都可能让覆盖失效）——必须在真浏览器里
逐页截图看实际像素。本脚本打开应用、切到每个入口、各截一张图，供人工核对：

  ① 侧栏菜单是不是深色 + 四组分组可见；
  ② 旧的浅色页（检索 / 问答 / 台账 / 运行层）是不是变成了深色；
  ③ 原本就是深色的页（本体建模层 / 知识写入）有没有被换肤层改坏。

不写任何业务数据；不改项目；不点写按钮。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

os.environ.setdefault('NO_PROXY', '127.0.0.1,localhost')

EDGE = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
OUT = Path(r'C:\Users\admin\AppData\Local\hermes\profiles\workspace\cache\scratch')

# (data-tab, 截图文件名, 说明)
SHOTS = [
    ('search',   'ui-1-search.png',   '检索（旧浅色 → 应变深色）'),
    ('qa',       'ui-2-qa.png',       '知识问答（旧浅色）'),
    ('records',  'ui-3-records.png',  '知识台账（旧浅色）'),
    ('runtime',  'ui-4-runtime.png',  '运行层（旧浅色）'),
    ('ingest',   'ui-5-ingest.png',   '知识写入（P1 深色，别改坏）'),
    ('ontology-model', 'ui-6-canvas.png', '本体建模层（P1 深色，别改坏）'),
]


def get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=20) as resp:
        return json.loads(resp.read().decode('utf-8') or 'null')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://127.0.0.1:8100')
    ap.add_argument('--project', default=None, help='项目 id（默认挑第一个）')
    args = ap.parse_args()
    base = args.url.rstrip('/')

    projects = (get(base, '/api/projects') or {}).get('projects') or []
    if not projects:
        print('没有项目可看，先建一个'); return 2
    pid = args.project or projects[0]['id']
    print(f'项目：{projects[0]["name"]} ({pid[:8]}…)')

    if not EDGE.exists():
        print(f'需要系统 Edge：{EDGE}'); return 2
    OUT.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright
    errors: list[str] = []
    with sync_playwright() as pw:
        with tempfile.TemporaryDirectory(prefix='ui-shot-', ignore_cleanup_errors=True) as profile:
            ctx = pw.chromium.launch_persistent_context(
                profile, executable_path=str(EDGE), headless=True,
                viewport={'width': 1680, 'height': 950})
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(base + '/', wait_until='domcontentloaded')
            page.wait_for_function("() => document.querySelectorAll('#project option').length > 1",
                                   timeout=20000)
            page.select_option('#project', pid)
            page.wait_for_timeout(1200)

            # 菜单是否真的分了四组（这是"菜单统一"的第一道证据）
            groups = page.evaluate(
                "() => [...document.querySelectorAll('aside .nav-group > summary')].map(s => s.textContent.trim())")
            print(f'侧栏分组：{groups}')

            # 换肤层是否生效：取侧栏与主区背景的实际计算值
            colors = page.evaluate("""() => {
              const a = getComputedStyle(document.querySelector('aside'));
              const m = getComputedStyle(document.querySelector('main'));
              const b = getComputedStyle(document.body);
              return {aside: a.backgroundColor, asideImage: a.backgroundImage.slice(0, 60),
                      main: m.backgroundColor, body: b.backgroundColor,
                      bodyColor: b.color};
            }""")
            print('实际计算色：' + json.dumps(colors, ensure_ascii=False))

            for tab, name, desc in SHOTS:
                page.evaluate("""(t) => {
                  const btn = document.querySelector(`[data-tab="${t}"]`);
                  if (btn) { const g = btn.closest('details'); if (g) g.open = true; btn.click(); }
                }""", tab)
                page.wait_for_timeout(1400)
                path = OUT / name
                page.screenshot(path=str(path))
                print(f'  ✔ {desc} -> {path}')

            ctx.close()

    if errors:
        print('JS 报错：' + ' | '.join(errors[:3]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
