#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""E 规则 · 字号硬线（≥11px）的机器读数 —— 逐页量真实渲染出来的字号。

为什么要有一个脚本，而不是靠人看：
  · 「字号 ≥11px」是引导式界面规则里的**硬线**（《2026-10-02-完整落地规划》§7）：
    小字号是"看不清"的直接来源，且它长在 CSS 里、由布局串起来 —— 靠 grep 只能看到**声明**，
    看不到"声明在某些页面/某些交互下其实被继承或覆盖成了更小的值"。
  · 反过来，grep 也漏掉只在交互后才渲染的东西（画布节点、抽屉里的行）。所以这个脚本两头都量：
      ① 真机：12 个导航页逐个进，读 `getComputedStyle().fontSize`（只统计"自身直接承载文字"的节点）；
      ② 静态兜底：全库 CSS 里是否还有 <11px 的 `font-size` 声明（含 px / rem / em / % 四种写法）。

口径：以**声明**为准清账（grep 必须为 0），以**真机读数**为下界（交互后才出现的内容量不到）。
两者一致时报 PASS。

用法：
    python -u -m knowledge_service --port 8100 &     # 先让服务跑起来
    python scripts/check_font_floor.py --url http://127.0.0.1:8100
    python scripts/check_font_floor.py --css-only     # 只做静态兜底（不需要服务和浏览器）

退出码：0 = 达标；1 = 有 <11px；2 = 环境问题（服务/浏览器）。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

os.environ.setdefault('NO_PROXY', '127.0.0.1,localhost')

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'knowledge_service' / 'web'
EDGE = Path(r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
FLOOR = 11.0
# 1rem = 14px（:root 的 font-size）。em 会受父级影响，这里按"小于 1em 就当偏小"处理。
UNITS = {'px': 1.0, 'rem': 14.0, 'em': 14.0, '%': 14.0 / 100}

JS_MEASURE = """(tab) => {
    const section = document.getElementById('tab-' + tab);
    if (!section || getComputedStyle(section).display === 'none') return null;
    const offenders = [];
    let nodes = 0;
    for (const el of section.querySelectorAll('*')) {
        const own = [...el.childNodes].some(node => node.nodeType === 3 && node.textContent.trim());
        if (!own) continue;
        nodes += 1;
        const size = parseFloat(getComputedStyle(el).fontSize);
        if (size < 11) offenders.push(size + 'px <' + el.tagName.toLowerCase() + '> ' +
            el.textContent.trim().slice(0, 24));
    }
    return {nodes: nodes, offenders: offenders};
}"""


def static_check() -> list[str]:
    """静态兜底：全库 CSS 里还有没有 <11px 的 font-size 声明（四种单位都查）。"""
    pattern = re.compile(r'font-size:\s*([\d.]+)(px|rem|em|%)')
    bad = []
    for css in sorted(WEB.glob('*.css')):
        with open(css, encoding='utf-8', newline='') as fh:
            for number, line in enumerate(fh, 1):
                for match in pattern.finditer(line):
                    effective = float(match.group(1)) * UNITS[match.group(2)]
                    if effective < FLOOR:
                        bad.append(f'{css.name}:{number} font-size:{match.group(1)}{match.group(2)}'
                                   f'（≈{round(effective, 1)}px）')
    return bad


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8100')
    parser.add_argument('--css-only', action='store_true', help='只做静态兜底（不用服务和浏览器）')
    args = parser.parse_args()
    base = args.url.rstrip('/')

    print(f'== E 规则 · 字号硬线 ≥{FLOOR:g}px ==')
    static_bad = static_check()
    print(f'① 静态兜底（{len(list(WEB.glob("*.css")))} 个 CSS 文件）：'
          + ('没有 <11px 的 font-size 声明 ✓' if not static_bad else f'仍有 {len(static_bad)} 处 ✗'))
    for item in static_bad[:20]:
        print('   -', item)
    if args.css_only:
        return 1 if static_bad else 0

    try:
        from playwright.sync_api import sync_playwright
    except Exception as error:  # noqa: BLE001
        print(f'无法启用 Playwright（{error}）；静态兜底结果即上。')
        return 2
    if not EDGE.exists():
        print(f'找不到浏览器：{EDGE}')
        return 2

    page_offenders = 0
    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True, executable_path=str(EDGE))
            page = browser.new_context(viewport={'width': 1680, 'height': 950}).new_page()
            page.goto(f'{base}/', wait_until='load')
            page.wait_for_function("() => document.querySelectorAll('#project option').length > 1")
            options = page.evaluate(
                "() => [...document.querySelectorAll('#project option')].map(o => o.value).filter(Boolean)")
            if options:
                page.select_option('#project', options[0])   # 选一个真项目，让各页有数据可渲染
            page.wait_for_timeout(800)
            tabs = page.evaluate(
                "() => [...document.querySelectorAll('aside nav button[data-tab]')].map(b => b.dataset.tab)")
            print(f'② 真机读数（{len(tabs)} 个导航页，进入即量）：')
            for tab in tabs:
                page.evaluate("(tab) => document.querySelector(`[data-tab=\"${tab}\"]`).click()", tab)
                page.wait_for_timeout(700)
                result = page.evaluate(JS_MEASURE, tab)
                if not result:
                    print(f'   {tab:22s} 跳过（该 section 不存在或不可见）')
                    continue
                page_offenders += len(result['offenders'])
                mark = '✓' if not result['offenders'] else '✗'
                line = f"   {tab:22s} {mark} {result['nodes']} 个文字节点"
                if result['offenders']:
                    line += ' · 超标：' + '；'.join(result['offenders'][:3])
                print(line)
            browser.close()
    except Exception as error:  # noqa: BLE001
        print(f'真机读数失败（{error}）：确认服务已启动在 {base}。')
        return 2

    print(f'\n静态声明超标 {len(static_bad)} 处 · 真机可见超标 {page_offenders} 处'
          f'（真机是下界：交互后才渲染的内容量不到，因此以静态为清账口径）')
    if static_bad or page_offenders:
        print('未达标。')
        return 1
    print('达标：全站字号下限 ≥11px。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
