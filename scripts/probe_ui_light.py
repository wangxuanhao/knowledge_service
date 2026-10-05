#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""找出"换肤没盖住"的浅底元素（只读诊断）。

theme.css 是堆叠在最后的一层覆盖，但旧 CSS 里有些选择器特异性更高、或有内联样式，
覆盖会失效——表现为深色页面上残留白块。本脚本在每个入口里扫描所有可见元素，
按"背景亮度"筛出浅色的，并报出它的选择器，便于精准补规则，而不是靠截图猜。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('NO_PROXY', '127.0.0.1,localhost')
EDGE = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')

# 找出可见、且背景明显偏亮（>0.55 亮度）或接近纯白/浅绿的元素
FIND_LIGHT = r"""
() => {
  const lum = (c) => {
    const m = c.match(/\d+(\.\d+)?/g);
    if (!m || m.length < 3) return null;
    if (m.length > 3 && parseFloat(m[3]) === 0) return null;   // 全透明
    const [r, g, b] = m.map(Number);
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  };
  const sel = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    if (el.className && typeof el.className === 'string')
      s += '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.');
    return s;
  };
  const out = [];
  for (const el of document.querySelectorAll('#tab-search *, #tab-qa *, #tab-records *, #tab-runtime *, #tab-ontology-model *')) {
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 20) continue;               // 忽略小装饰
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') continue;
    // 不用正则解析 alpha：本脚本的选择器字符串是 Python raw string，反斜杠层数容易出错。
    // `rgba(r, g, b, a)` 直接按逗号切最后一段；`rgb(...)` 视为不透明。
    const bgStr = st.backgroundColor || '';
    const alpha = bgStr.indexOf('rgba(') === 0
      ? parseFloat(bgStr.slice(bgStr.lastIndexOf(',') + 1).replace(')', '')) : 1;
    const L = lum(st.backgroundColor);
    if (L === null || L <= 0.55) continue;
    // 亮度高不等于"残留"。深色主题里有两种**设计意图内**的高亮，必须分开报，
    // 否则读数天天是噪音，"统一了没有"这个指标就废了：
    //   ① 半透明叠加（alpha<1）：深底上的微亮，如激活态页签 rgba(56,189,248,.14)；
    //   ② 不透明的**主按钮**：主色实底（如 rgb(43,166,224)），代表"这一屏要做的事"。
    // 真正该清零的是 ③：**非按钮**元素上的不透明浅底 —— 在深色皮肤里它只可能是漏网的白/浅表面。
    const isButton = el.tagName === 'BUTTON' || el.getAttribute('role') === 'button';
    const kind = alpha < 1 ? 'intent-overlay' : (isButton ? 'intent-primary' : 'residue');
    out.push({sel: sel(el), bg: st.backgroundColor, lum: +L.toFixed(2), kind,
              w: Math.round(r.width), h: Math.round(r.height),
              inline: el.getAttribute('style') || ''});
  }
  return out.slice(0, 40);
}
"""


def main() -> int:
    import argparse
    import urllib.request
    # 必须用 argparse 收 `--url`：本仓库所有真机脚本（含 verify_all_chains.py 的汇总入口）
    # 都按 `--url <地址>` 传参。早先这里用 sys.argv[1] 取位置参数，拿到的是字符串 '--url' 本身，
    # 报 `unknown url type: '--url/api/projects'`，表现为"单独跑好好的、一进汇总就红"的假失败。
    parser = argparse.ArgumentParser(description='浅底元素探针：扫出深色主题下残留的浅色背景')
    parser.add_argument('--url', default='http://127.0.0.1:8100', help='服务地址')
    args = parser.parse_args()
    base = args.url.rstrip('/')
    with urllib.request.urlopen(base + '/api/projects', timeout=20) as resp:
        projects = (json.loads(resp.read().decode()) or {}).get('projects') or []
    pid = projects[0]['id']

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        with tempfile.TemporaryDirectory(prefix='probe-', ignore_cleanup_errors=True) as profile:
            ctx = pw.chromium.launch_persistent_context(
                profile, executable_path=str(EDGE), headless=True,
                viewport={'width': 1680, 'height': 950})
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(base + '/', wait_until='domcontentloaded')
            page.wait_for_function("() => document.querySelectorAll('#project option').length > 1",
                                   timeout=20000)
            page.select_option('#project', pid)
            page.wait_for_timeout(1000)

            for tab in ('search', 'qa', 'records', 'runtime', 'ontology-model'):
                page.evaluate("""(t) => {
                  const b = document.querySelector(`[data-tab="${t}"]`);
                  if (b) { const g = b.closest('details'); if (g) g.open = true; b.click(); }
                }""", tab)
                page.wait_for_timeout(1200)
                lights = page.evaluate(FIND_LIGHT)
                # 只把 residue（非按钮上的不透明浅底）当"残留"。主按钮实底与半透明叠加是设计意图，
                # 混在一起数会让这个指标失去意义（见 FIND_LIGHT 里的三条注释）。
                residue = [i for i in lights if i['kind'] == 'residue']
                intents = [i for i in lights if i['kind'] != 'residue']
                print(f'\n=== {tab}：疑似浅色残留 {len(residue)} 个 · 设计意图内的高亮 {len(intents)} 个 ===')
                for it in lights:
                    print(f'  {it["lum"]:.2f} {it["bg"]:>22} {it["w"]}x{it["h"]:<5} {it["sel"]}'
                          f'  [{it["kind"]}]'
                          + (f'  [inline: {it["inline"][:50]}]' if it['inline'] else ''))
            ctx.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
